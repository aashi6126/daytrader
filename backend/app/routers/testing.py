import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_ws_manager
from app.models import ExitReason, Trade, TradeEventType, TradeStatus
from app.schemas import TradeResponse
from app.services.schwab_client import SchwabService
from app.services.trade_events import log_trade_event
from app.services.ws_manager import WebSocketManager

router = APIRouter()
logger = logging.getLogger(__name__)

ACTIVE_STATUSES = {
    TradeStatus.FILLED,
    TradeStatus.STOP_LOSS_PLACED,
    TradeStatus.EXITING,
}


class TestCloseRequest(BaseModel):
    trade_id: int
    pnl_percent: float = Field(..., ge=-99.99, description="Target P&L percentage, e.g. 10.0 or -20.0")


@router.post("/testing/close-trade")
async def test_close_trade(
    body: TestCloseRequest,
    request: Request,
    db: Session = Depends(get_db),
    ws_manager: WebSocketManager = Depends(get_ws_manager),
):
    trade = db.query(Trade).filter(Trade.id == body.trade_id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    if trade.status not in ACTIVE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Trade #{trade.id} is not active (status: {trade.status.value})",
        )

    if trade.entry_price is None:
        raise HTTPException(
            status_code=400,
            detail=f"Trade #{trade.id} has no entry price yet",
        )

    exit_price = round(trade.entry_price * (1 + body.pnl_percent / 100), 2)
    remaining_qty = trade.entry_quantity - (trade.scaled_out_quantity or 0)
    remaining_pnl = (exit_price - trade.entry_price) * remaining_qty * 100
    scale_out_pnl = 0.0
    if trade.scaled_out and trade.scaled_out_price:
        scale_out_pnl = (trade.scaled_out_price - trade.entry_price) * (trade.scaled_out_quantity or 0) * 100
    pnl_dollars = round(remaining_pnl + scale_out_pnl, 2)

    # Cancel stop-loss order if present
    if trade.stop_loss_order_id:
        try:
            schwab = SchwabService(request.app.state.schwab_client)
            schwab.cancel_order(trade.stop_loss_order_id)
            log_trade_event(
                db, trade.id, TradeEventType.STOP_LOSS_CANCELLED,
                f"Stop-loss order {trade.stop_loss_order_id} cancelled for manual close",
                details={"order_id": trade.stop_loss_order_id},
            )
        except Exception as e:
            logger.warning(f"Test close: could not cancel stop-loss: {e}")

    trade.exit_price = exit_price
    trade.exit_filled_at = datetime.utcnow()
    trade.exit_reason = ExitReason.MANUAL
    trade.pnl_dollars = pnl_dollars
    trade.pnl_percent = body.pnl_percent
    trade.status = TradeStatus.CLOSED
    log_trade_event(
        db, trade.id, TradeEventType.MANUAL_CLOSE,
        f"Manual close at ${exit_price:.2f} — PnL ${pnl_dollars:.2f} ({body.pnl_percent:+.1f}%)",
        details={"exit_price": exit_price, "pnl_dollars": pnl_dollars, "pnl_percent": body.pnl_percent},
    )
    db.commit()

    logger.info(
        f"Test close: Trade #{trade.id} closed at {exit_price:.2f}, "
        f"PnL=${pnl_dollars:.2f} ({body.pnl_percent:+.1f}%)"
    )

    await ws_manager.broadcast(
        {
            "event": "trade_closed",
            "data": {
                "trade_id": trade.id,
                "exit_price": exit_price,
                "pnl_dollars": pnl_dollars,
                "pnl_percent": body.pnl_percent,
                "exit_reason": ExitReason.MANUAL.value,
            },
        }
    )

    return {
        "status": "closed",
        "message": f"Trade #{trade.id} closed at {exit_price:.2f} ({body.pnl_percent:+.1f}%)",
    }
