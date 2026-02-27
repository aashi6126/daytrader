import logging
from datetime import date
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import get_db
from app.dependencies import get_trade_manager

settings = Settings()
from app.models import Alert, Trade, TradeEvent, TradePriceSnapshot, TradeStatus
from app.schemas import PriceSnapshotListResponse, PriceSnapshotResponse, TradeEventListResponse, TradeEventResponse, TradeListResponse, TradeResponse, WebhookResponse
from app.services.schwab_client import SchwabService
from app.services.trade_manager import TradeManager

router = APIRouter()
logger = logging.getLogger(__name__)


def _enrich_with_best_entry(
    trade_responses: list[TradeResponse],
    trade_ids: list[int],
    db: Session,
) -> None:
    """Attach best_entry_price and best_entry_minutes to each TradeResponse.

    Best entry = lowest price observed before the option first exceeded entry price.
    """
    if not trade_ids:
        return

    # Fetch all snapshots for these trades, ordered by time
    snapshots = (
        db.query(TradePriceSnapshot)
        .filter(TradePriceSnapshot.trade_id.in_(trade_ids))
        .order_by(TradePriceSnapshot.trade_id, TradePriceSnapshot.timestamp.asc())
        .all()
    )

    # Group by trade
    snaps_by_trade: dict[int, list[TradePriceSnapshot]] = {}
    for s in snapshots:
        snaps_by_trade.setdefault(s.trade_id, []).append(s)

    # Build entry price lookup
    entry_by_id = {tr.id: tr.entry_price for tr in trade_responses}

    # Alert received_at per trade
    alerts = db.query(Alert).filter(Alert.trade_id.in_(trade_ids)).all()
    alert_by_trade = {a.trade_id: a for a in alerts}

    for tr in trade_responses:
        snaps = snaps_by_trade.get(tr.id)
        entry_price = entry_by_id.get(tr.id)
        if not snaps or entry_price is None:
            continue

        # Walk snapshots: find lowest price before price first exceeds entry
        best_snap = None
        for s in snaps:
            if s.price > entry_price:
                break
            if best_snap is None or s.price < best_snap.price:
                best_snap = s

        if not best_snap:
            continue

        tr.best_entry_price = round(best_snap.price, 2)
        alert = alert_by_trade.get(tr.id)
        if alert and alert.received_at and best_snap.timestamp:
            delta = (best_snap.timestamp - alert.received_at).total_seconds() / 60
            tr.best_entry_minutes = round(delta, 1)


@router.get("/trades/tickers")
def list_trade_tickers(db: Session = Depends(get_db)):
    """Return distinct tickers that have associated trades."""
    rows = (
        db.query(Alert.ticker)
        .filter(Alert.trade_id.isnot(None))
        .distinct()
        .order_by(Alert.ticker)
        .all()
    )
    return {"tickers": [r[0] for r in rows]}


@router.get("/trades", response_model=TradeListResponse)
def list_trades(
    trade_date: Optional[date] = None,
    status: Optional[TradeStatus] = None,
    ticker: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(Trade)
    if trade_date:
        query = query.filter(Trade.trade_date == trade_date)
    if status:
        query = query.filter(Trade.status == status)
    if ticker:
        query = query.filter(Trade.alert.has(Alert.ticker == ticker))

    total = query.count()
    trades = (
        query.order_by(Trade.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    trade_responses = [TradeResponse.model_validate(t) for t in trades]
    _enrich_with_best_entry(trade_responses, [t.id for t in trades], db)

    # Populate ticker from related alert
    alert_map = {a.trade_id: a.ticker for a in db.query(Alert).filter(Alert.trade_id.in_([t.id for t in trades])).all()}
    for tr in trade_responses:
        tr.ticker = alert_map.get(tr.id)

    return TradeListResponse(
        trades=trade_responses,
        total=total,
        page=page,
        per_page=per_page,
    )


class QuoteItem(BaseModel):
    trade_id: int
    option_symbol: str
    last_price: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None


class QuotesResponse(BaseModel):
    quotes: List[QuoteItem]


@router.get("/trades/open/quotes", response_model=QuotesResponse)
def get_open_quotes(request: Request, db: Session = Depends(get_db)):
    """Fetch live quotes for all open positions."""
    active_statuses = [TradeStatus.PENDING, TradeStatus.FILLED, TradeStatus.STOP_LOSS_PLACED, TradeStatus.EXITING]
    open_trades = (
        db.query(Trade)
        .filter(Trade.trade_date == date.today())
        .filter(Trade.status.in_(active_statuses))
        .all()
    )

    if not open_trades:
        return QuotesResponse(quotes=[])

    schwab = SchwabService(request.app.state.schwab_client)
    quotes: List[QuoteItem] = []

    for trade in open_trades:
        item = QuoteItem(trade_id=trade.id, option_symbol=trade.option_symbol)
        try:
            quote_data = schwab.get_quote(trade.option_symbol)
            q = quote_data.get(trade.option_symbol, {}).get("quote", {})
            item.last_price = q.get("lastPrice")
            item.bid = q.get("bidPrice")
            item.ask = q.get("askPrice")
        except Exception as e:
            logger.warning(f"Failed to get quote for {trade.option_symbol}: {e}")
        quotes.append(item)

    return QuotesResponse(quotes=quotes)


@router.get("/trades/{trade_id}", response_model=TradeResponse)
def get_trade(trade_id: int, db: Session = Depends(get_db)):
    trade = db.query(Trade).filter(Trade.id == trade_id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    tr = TradeResponse.model_validate(trade)
    _enrich_with_best_entry([tr], [trade.id], db)
    if trade.alert:
        tr.ticker = trade.alert.ticker
    return tr


@router.get("/trades/{trade_id}/events", response_model=TradeEventListResponse)
def get_trade_events(trade_id: int, db: Session = Depends(get_db)):
    trade = db.query(Trade).filter(Trade.id == trade_id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    events = (
        db.query(TradeEvent)
        .filter(TradeEvent.trade_id == trade_id)
        .order_by(TradeEvent.timestamp.asc())
        .all()
    )

    return TradeEventListResponse(
        events=[TradeEventResponse.model_validate(e) for e in events],
        trade_id=trade_id,
    )


@router.get("/trades/{trade_id}/prices", response_model=PriceSnapshotListResponse)
def get_trade_prices(trade_id: int, db: Session = Depends(get_db)):
    trade = db.query(Trade).filter(Trade.id == trade_id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    snapshots = (
        db.query(TradePriceSnapshot)
        .filter(TradePriceSnapshot.trade_id == trade_id)
        .order_by(TradePriceSnapshot.timestamp.asc())
        .all()
    )

    return PriceSnapshotListResponse(
        snapshots=[PriceSnapshotResponse.model_validate(s) for s in snapshots],
        trade_id=trade_id,
        entry_price=trade.entry_price,
        stop_loss_price=trade.stop_loss_price,
    )


@router.post("/trades/{trade_id}/close")
async def close_trade_now(
    trade_id: int,
    request: Request,
    db: Session = Depends(get_db),
    trade_manager: TradeManager = Depends(get_trade_manager),
):
    """Force-close an open trade via market sell order on Schwab."""
    from app.models import ExitReason

    trade = db.query(Trade).filter(Trade.id == trade_id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    active = {TradeStatus.FILLED, TradeStatus.STOP_LOSS_PLACED}
    if trade.status not in active:
        raise HTTPException(
            status_code=400,
            detail=f"Trade #{trade.id} is not active (status: {trade.status.value})",
        )

    await trade_manager._close_trade(
        db, trade, ExitReason.MANUAL, "Manual close requested via UI"
    )
    db.commit()

    return {"status": "closing", "message": f"Trade #{trade.id} market sell placed"}


@router.post("/trades/{trade_id}/cancel")
async def cancel_pending_trade(
    trade_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Cancel a PENDING trade by cancelling the entry order on Schwab."""
    from app.dependencies import get_schwab_service, get_ws_manager
    from app.models import TradeEventType
    from app.services.trade_events import log_trade_event

    trade = db.query(Trade).filter(Trade.id == trade_id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    if trade.status != TradeStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Trade #{trade.id} is not pending (status: {trade.status.value})",
        )

    schwab = get_schwab_service(request)
    if trade.entry_order_id:
        try:
            schwab.cancel_order(trade.entry_order_id)
        except Exception as e:
            logger.warning(f"Trade #{trade.id}: cancel order failed: {e}")

    trade.status = TradeStatus.CANCELLED
    log_trade_event(
        db, trade.id, TradeEventType.ENTRY_CANCELLED,
        "Entry order cancelled manually via UI",
        details={"order_id": trade.entry_order_id},
    )
    db.commit()

    ws = get_ws_manager()
    await ws.broadcast({
        "event": "trade_cancelled",
        "data": {"trade_id": trade.id, "reason": "MANUAL_CANCEL"},
    })

    return {"status": "cancelled", "message": f"Trade #{trade.id} entry order cancelled"}


@router.post("/trades/{trade_id}/cancel-stop-loss")
async def cancel_stop_loss(
    trade_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Cancel the stop-loss order on an active trade."""
    from app.dependencies import get_schwab_service, get_ws_manager
    from app.models import TradeEventType
    from app.services.trade_events import log_trade_event

    trade = db.query(Trade).filter(Trade.id == trade_id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    if trade.status not in (TradeStatus.FILLED, TradeStatus.STOP_LOSS_PLACED):
        raise HTTPException(
            status_code=400,
            detail=f"Trade #{trade.id} is not active (status: {trade.status.value})",
        )

    if not trade.stop_loss_order_id:
        raise HTTPException(status_code=400, detail=f"Trade #{trade.id} has no stop-loss order")

    schwab = get_schwab_service(request)
    try:
        schwab.cancel_order(trade.stop_loss_order_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to cancel stop-loss: {e}")

    old_order_id = trade.stop_loss_order_id
    trade.stop_loss_order_id = None
    trade.stop_loss_price = None
    trade.status = TradeStatus.FILLED
    log_trade_event(
        db, trade.id, TradeEventType.STOP_LOSS_CANCELLED,
        f"Stop-loss order {old_order_id} cancelled manually via UI",
        details={"order_id": old_order_id},
    )
    db.commit()

    ws = get_ws_manager()
    await ws.broadcast({
        "event": "trade_stop_loss_cancelled",
        "data": {"trade_id": trade.id},
    })

    return {"status": "ok", "message": f"Trade #{trade.id} stop-loss cancelled"}


@router.post("/trades/{trade_id}/replace-stop-loss")
async def replace_stop_loss(
    trade_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Cancel existing stop-loss and re-place at current STOP_LOSS_PERCENT."""
    from app.dependencies import get_schwab_service, get_ws_manager
    from app.models import TradeEventType
    from app.services.trade_events import log_trade_event

    trade = db.query(Trade).filter(Trade.id == trade_id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    if trade.status not in (TradeStatus.FILLED, TradeStatus.STOP_LOSS_PLACED):
        raise HTTPException(status_code=400, detail=f"Trade #{trade.id} is not active (status: {trade.status.value})")

    if trade.entry_price is None:
        raise HTTPException(status_code=400, detail=f"Trade #{trade.id} has no entry price")

    schwab = get_schwab_service(request)

    # Cancel existing stop-loss if present
    if trade.stop_loss_order_id:
        try:
            schwab.cancel_order(trade.stop_loss_order_id)
        except Exception:
            pass
        trade.stop_loss_order_id = None

    # Place new stop-loss at per-trade or global config percent
    sl_pct = trade.param_stop_loss_percent or settings.STOP_LOSS_PERCENT
    stop_price = round(trade.entry_price * (1 - sl_pct / 100), 2)
    remaining_qty = trade.entry_quantity - (trade.scaled_out_quantity or 0)
    order = SchwabService.build_stop_loss_order(
        option_symbol=trade.option_symbol,
        quantity=remaining_qty,
        stop_price=stop_price,
    )
    order_id = schwab.place_order(order)
    trade.stop_loss_order_id = order_id
    trade.stop_loss_price = stop_price
    trade.status = TradeStatus.STOP_LOSS_PLACED
    log_trade_event(
        db, trade.id, TradeEventType.STOP_LOSS_PLACED,
        f"Stop-loss re-placed at ${stop_price:.2f} ({sl_pct}% SL), order={order_id}",
        details={"stop_price": stop_price, "order_id": order_id, "stop_loss_percent": sl_pct},
    )
    db.commit()

    ws = get_ws_manager()
    await ws.broadcast({
        "event": "trade_stop_loss_replaced",
        "data": {"trade_id": trade.id, "stop_loss_price": stop_price},
    })

    return {"status": "ok", "message": f"Trade #{trade.id} stop-loss set to ${stop_price:.2f} ({sl_pct}%)"}


@router.post("/trades/{trade_id}/retake", response_model=WebhookResponse)
async def retake_trade(
    trade_id: int,
    db: Session = Depends(get_db),
    trade_manager: TradeManager = Depends(get_trade_manager),
):
    """Re-enter the same direction as a previous trade with a fresh 0DTE contract."""
    return await trade_manager.retake_trade(db, trade_id)
