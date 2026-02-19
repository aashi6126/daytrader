import logging
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import get_db
from app.models import Trade, TradeStatus
from app.schemas import DailyStatsResponse, PnLChartResponse, PnLDataPoint, PnLSummaryDay, PnLSummaryResponse

logger = logging.getLogger(__name__)

router = APIRouter()
settings = Settings()

ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


@router.get("/dashboard/stats", response_model=DailyStatsResponse)
def get_daily_stats(db: Session = Depends(get_db)):
    today = date.today()
    trades = db.query(Trade).filter(Trade.trade_date == today).all()

    closed = [t for t in trades if t.status == TradeStatus.CLOSED]
    open_positions = [
        t
        for t in trades
        if t.status
        in (TradeStatus.FILLED, TradeStatus.STOP_LOSS_PLACED, TradeStatus.EXITING)
    ]
    winners = [t for t in closed if (t.pnl_dollars or 0) > 0]
    total_pnl = sum(t.pnl_dollars or 0 for t in closed)
    trade_count = len(
        [t for t in trades if t.status != TradeStatus.CANCELLED]
    )

    return DailyStatsResponse(
        trade_date=today,
        total_trades=trade_count,
        trades_remaining=max(0, settings.MAX_DAILY_TRADES - trade_count),
        winning_trades=len(winners),
        losing_trades=len(closed) - len(winners),
        total_pnl=total_pnl,
        win_rate=(len(winners) / len(closed) * 100) if closed else 0,
        open_positions=len(open_positions),
    )


@router.get("/dashboard/pnl", response_model=PnLChartResponse)
def get_pnl_chart(
    trade_date: Optional[date] = None,
    db: Session = Depends(get_db),
):
    target_date = trade_date or date.today()
    closed_trades = (
        db.query(Trade)
        .filter(Trade.trade_date == target_date)
        .filter(Trade.status == TradeStatus.CLOSED)
        .order_by(Trade.exit_filled_at.asc())
        .all()
    )

    data_points = []
    cumulative = 0.0
    for trade in closed_trades:
        cumulative += trade.pnl_dollars or 0
        data_points.append(
            PnLDataPoint(
                timestamp=trade.exit_filled_at,
                cumulative_pnl=cumulative,
                trade_id=trade.id,
            )
        )

    return PnLChartResponse(data_points=data_points, total_pnl=cumulative)


@router.get("/dashboard/pnl-summary", response_model=PnLSummaryResponse)
def get_pnl_summary(
    period: str = "weekly",
    db: Session = Depends(get_db),
):
    """Return per-day PnL for the current week or month."""
    today = date.today()
    if period == "monthly":
        start_date = today.replace(day=1)
    else:
        # weekly: Monday of current week
        start_date = today - timedelta(days=today.weekday())

    closed_trades = (
        db.query(Trade)
        .filter(Trade.trade_date >= start_date, Trade.trade_date <= today)
        .filter(Trade.status == TradeStatus.CLOSED)
        .all()
    )

    # Group by date
    by_date: dict[date, list] = {}
    for t in closed_trades:
        by_date.setdefault(t.trade_date, []).append(t)

    days = []
    current = start_date
    while current <= today:
        trades_on_day = by_date.get(current, [])
        winners = [t for t in trades_on_day if (t.pnl_dollars or 0) > 0]
        days.append(
            PnLSummaryDay(
                trade_date=current,
                pnl=sum(t.pnl_dollars or 0 for t in trades_on_day),
                total_trades=len(trades_on_day),
                winning_trades=len(winners),
                losing_trades=len(trades_on_day) - len(winners),
            )
        )
        current += timedelta(days=1)

    total_pnl = sum(d.pnl for d in days)
    total_trades = sum(d.total_trades for d in days)
    total_wins = sum(d.winning_trades for d in days)

    return PnLSummaryResponse(
        period=period,
        days=days,
        total_pnl=total_pnl,
        total_trades=total_trades,
        winning_trades=total_wins,
        losing_trades=total_trades - total_wins,
        win_rate=(total_wins / total_trades * 100) if total_trades else 0,
    )


class StrategyResponse(BaseModel):
    strategy: str
    description: str


@router.get("/dashboard/strategy", response_model=StrategyResponse)
def get_active_strategy():
    descriptions = {
        "orb_auto": "ORB Auto (9:50 ET entry)",
        "tradingview": "TradingView Signals",
        "disabled": "Disabled",
    }
    return StrategyResponse(
        strategy=settings.ACTIVE_STRATEGY,
        description=descriptions.get(settings.ACTIVE_STRATEGY, settings.ACTIVE_STRATEGY),
    )


class ModeResponse(BaseModel):
    paper_trade: bool


class ModeRequest(BaseModel):
    paper_trade: bool


@router.get("/dashboard/mode", response_model=ModeResponse)
def get_mode():
    return ModeResponse(paper_trade=settings.PAPER_TRADE)


@router.put("/dashboard/mode", response_model=ModeResponse)
def set_mode(body: ModeRequest):
    """Update PAPER_TRADE in .env. Requires server restart to take effect."""
    new_value = "true" if body.paper_trade else "false"
    if ENV_FILE.exists():
        content = ENV_FILE.read_text()
        if re.search(r"^PAPER_TRADE=", content, re.MULTILINE):
            content = re.sub(
                r"^PAPER_TRADE=.*$", f"PAPER_TRADE={new_value}", content, flags=re.MULTILINE
            )
        else:
            content += f"\nPAPER_TRADE={new_value}\n"
        ENV_FILE.write_text(content)
    return ModeResponse(paper_trade=body.paper_trade)


class WindowOverrideResponse(BaseModel):
    ignore_trading_windows: bool


class WindowOverrideRequest(BaseModel):
    ignore_trading_windows: bool


@router.get("/dashboard/window-override", response_model=WindowOverrideResponse)
def get_window_override(request: Request):
    return WindowOverrideResponse(
        ignore_trading_windows=getattr(request.app.state, "ignore_trading_windows", False)
    )


@router.put("/dashboard/window-override", response_model=WindowOverrideResponse)
def set_window_override(body: WindowOverrideRequest, request: Request):
    """Toggle ignore_trading_windows at runtime (no restart needed)."""
    request.app.state.ignore_trading_windows = body.ignore_trading_windows
    logger.info(f"ignore_trading_windows set to {body.ignore_trading_windows}")
    return WindowOverrideResponse(ignore_trading_windows=body.ignore_trading_windows)


class SpyPriceResponse(BaseModel):
    price: Optional[float] = None
    change: Optional[float] = None
    change_percent: Optional[float] = None
    error: Optional[str] = None


@router.get("/dashboard/spy-price", response_model=SpyPriceResponse)
def get_spy_price(request: Request):
    """Fetch current SPY price from Schwab."""
    try:
        from app.services.schwab_client import SchwabService

        schwab = SchwabService(request.app.state.schwab_client)
        data = schwab.get_quote("SPY")
        q = data.get("SPY", {}).get("quote", {})
        last = q.get("lastPrice")
        change = q.get("netChange")
        pct = q.get("netPercentChangeInDouble")
        if pct is None and last and change:
            pct = round((change / (last - change)) * 100, 2)
        return SpyPriceResponse(
            price=last,
            change=change,
            change_percent=pct,
        )
    except Exception as e:
        return SpyPriceResponse(error=str(e))


class NgrokStatus(BaseModel):
    online: bool
    url: Optional[str] = None
    error: Optional[str] = None


@router.get("/dashboard/ngrok", response_model=NgrokStatus)
async def get_ngrok_status():
    """Check ngrok tunnel status via its local API on port 4040."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get("http://127.0.0.1:4040/api/tunnels")
            data = resp.json()
            tunnels = data.get("tunnels", [])
            https_tunnel = next(
                (t for t in tunnels if t.get("proto") == "https"), None
            )
            if https_tunnel:
                return NgrokStatus(online=True, url=https_tunnel["public_url"])
            elif tunnels:
                return NgrokStatus(online=True, url=tunnels[0]["public_url"])
            else:
                return NgrokStatus(online=False, error="No active tunnels")
    except Exception as e:
        return NgrokStatus(online=False, error="ngrok not running")


class TokenStatus(BaseModel):
    valid: bool
    refresh_token_issued: Optional[str] = None
    refresh_token_expires: Optional[str] = None
    days_remaining: Optional[float] = None
    error: Optional[str] = None


@router.get("/dashboard/token-status", response_model=TokenStatus)
def get_token_status():
    """Check Schwab refresh token expiry from tokens.db."""
    tokens_path = Path(settings.SCHWAB_TOKENS_DB).expanduser()
    if not tokens_path.exists():
        return TokenStatus(valid=False, error="tokens.db not found")
    try:
        conn = sqlite3.connect(str(tokens_path))
        row = conn.execute(
            "SELECT refresh_token_issued FROM schwabdev LIMIT 1"
        ).fetchone()
        conn.close()
        if not row or not row[0]:
            return TokenStatus(valid=False, error="No refresh token found")
        issued_str = row[0]
        issued = datetime.fromisoformat(issued_str)
        expires = issued + timedelta(days=7)
        now = datetime.now(issued.tzinfo)
        remaining = (expires - now).total_seconds() / 86400
        return TokenStatus(
            valid=remaining > 0,
            refresh_token_issued=issued.isoformat(),
            refresh_token_expires=expires.isoformat(),
            days_remaining=round(remaining, 1),
        )
    except Exception as e:
        return TokenStatus(valid=False, error=str(e))
