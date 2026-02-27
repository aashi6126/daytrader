import logging
import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import get_db
from app.models import Alert, Trade, TradeStatus
from app.schemas import DailyStatsResponse, PnLChartResponse, PnLDataPoint, PnLSummaryDay, PnLSummaryResponse

logger = logging.getLogger(__name__)

router = APIRouter()
settings = Settings()

ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


@router.get("/dashboard/stats", response_model=DailyStatsResponse)
def get_daily_stats(
    trade_date: Optional[date] = None,
    db: Session = Depends(get_db),
):
    today = trade_date or date.today()
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


class MarketOrderOverrideResponse(BaseModel):
    use_market_orders: bool


class MarketOrderOverrideRequest(BaseModel):
    use_market_orders: bool


@router.get("/dashboard/market-order-override", response_model=MarketOrderOverrideResponse)
def get_market_order_override(request: Request):
    return MarketOrderOverrideResponse(
        use_market_orders=getattr(request.app.state, "use_market_orders", False)
    )


@router.put("/dashboard/market-order-override", response_model=MarketOrderOverrideResponse)
def set_market_order_override(body: MarketOrderOverrideRequest, request: Request):
    """Toggle market order entry at runtime (no restart needed)."""
    request.app.state.use_market_orders = body.use_market_orders
    logger.info(f"use_market_orders set to {body.use_market_orders}")
    return MarketOrderOverrideResponse(use_market_orders=body.use_market_orders)


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


class TickerQuote(BaseModel):
    price: Optional[float] = None
    change: Optional[float] = None
    change_percent: Optional[float] = None


class MarketOverviewResponse(BaseModel):
    vix: Optional[TickerQuote] = None
    spy: Optional[TickerQuote] = None
    qqq: Optional[TickerQuote] = None
    error: Optional[str] = None


def _fetch_market_quotes() -> dict[str, Optional[TickerQuote]]:
    """Batch-fetch SPY, QQQ, VIX with pre/post-market data via yfinance download."""
    import yfinance as yf

    tickers = ["SPY", "QQQ", "^VIX"]
    result: dict[str, Optional[TickerQuote]] = {}

    # 1-minute bars include pre/post market prices
    df = yf.download(tickers, period="1d", interval="1m", prepost=True, progress=False)
    # Daily bars for previous close
    daily = yf.download(tickers, period="5d", progress=False)

    for t in tickers:
        close_col = ("Close", t)
        if close_col not in df.columns:
            result[t] = None
            continue
        vals = df[close_col].dropna()
        if len(vals) == 0:
            result[t] = None
            continue
        last = float(vals.iloc[-1])
        prev = None
        if close_col in daily.columns:
            daily_vals = daily[close_col].dropna()
            if len(daily_vals) > 0:
                prev = float(daily_vals.iloc[-1])
        change = round(last - prev, 2) if prev else None
        pct = round((change / prev) * 100, 2) if prev and change is not None else None
        result[t] = TickerQuote(price=round(last, 2), change=change, change_percent=pct)

    return result


@router.get("/dashboard/vix", response_model=MarketOverviewResponse)
def get_market_overview():
    """Fetch VIX, SPY, QQQ quotes via yfinance."""
    try:
        quotes = _fetch_market_quotes()
        return MarketOverviewResponse(
            spy=quotes.get("SPY"),
            qqq=quotes.get("QQQ"),
            vix=quotes.get("^VIX"),
        )
    except Exception as e:
        logger.warning(f"Failed to fetch market overview: {e}")
        return MarketOverviewResponse(error=str(e))


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


# --- Candle data for chart widget ---

_ET = ZoneInfo("America/New_York")
_MARKET_OPEN = time(9, 30)
_MARKET_CLOSE = time(16, 0)


class CandleResponse(BaseModel):
    time: int  # Unix seconds
    open: float
    high: float
    low: float
    close: float
    volume: int


@router.get("/dashboard/candles", response_model=List[CandleResponse])
def get_candles(request: Request, ticker: str = "SPY", frequency: int = 5, trade_date: Optional[date] = None):
    """Fetch intraday candles from Schwab for a given date (defaults to today)."""
    now_et = datetime.now(_ET)
    target_date = trade_date or now_et.date()
    start = datetime.combine(target_date, _MARKET_OPEN, tzinfo=_ET)
    end = datetime.combine(target_date, _MARKET_CLOSE, tzinfo=_ET) if trade_date else now_et + timedelta(minutes=1)

    try:
        resp = request.app.state.schwab_client.price_history(
            ticker,
            periodType="day",
            period="1",
            frequencyType="minute",
            frequency=frequency,
            startDate=start,
            endDate=end,
            needExtendedHoursData=False,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"Failed to fetch candles for {ticker}: {e}")
        return []

    candles = []
    for c in data.get("candles", []):
        ts = datetime.fromtimestamp(c["datetime"] / 1000, tz=_ET)
        if ts.time() < _MARKET_OPEN or ts.time() >= _MARKET_CLOSE:
            continue
        # Send ET wall-clock time as a fake UTC timestamp so lightweight-charts
        # displays the correct Eastern Time labels (it has no timezone support).
        # Shift: real_utc + utc_offset = ET wall-clock pretending to be UTC
        utc_offset_seconds = int(ts.utcoffset().total_seconds())
        fake_utc = int(ts.timestamp()) + utc_offset_seconds
        candles.append(CandleResponse(
            time=fake_utc,
            open=round(c["open"], 2),
            high=round(c["high"], 2),
            low=round(c["low"], 2),
            close=round(c["close"], 2),
            volume=int(c["volume"]),
        ))

    candles.sort(key=lambda x: x.time)
    return candles


# --- Pivot points ---


class PivotLevelsResponse(BaseModel):
    pivot: float
    r1: float
    s1: float
    r2: float
    s2: float


@router.get("/dashboard/pivots")
def get_pivot_levels(
    request: Request,
    ticker: str = "SPY",
    trade_date: Optional[date] = None,
):
    """Compute classic pivot points from prior trading day OHLC."""
    now_et = datetime.now(_ET)
    target_date = trade_date or now_et.date()

    # Fetch daily bars for the week preceding target_date
    start = datetime.combine(target_date - timedelta(days=7), time(0, 0), tzinfo=_ET)
    end = datetime.combine(target_date, time(0, 0), tzinfo=_ET)

    try:
        resp = request.app.state.schwab_client.price_history(
            ticker,
            periodType="month",
            period="1",
            frequencyType="daily",
            frequency=1,
            startDate=start,
            endDate=end,
            needExtendedHoursData=False,
        )
        resp.raise_for_status()
        candles = resp.json().get("candles", [])
    except Exception as e:
        logger.warning(f"Failed to fetch daily bars for pivots: {e}")
        return None

    if not candles:
        return None

    last = candles[-1]
    h, l, c = float(last["high"]), float(last["low"]), float(last["close"])
    p = (h + l + c) / 3.0

    return PivotLevelsResponse(
        pivot=round(p, 2),
        r1=round(2 * p - l, 2),
        s1=round(2 * p - h, 2),
        r2=round(p + (h - l), 2),
        s2=round(p - (h - l), 2),
    )


# --- Post-trade analytics ---


class HourBucket(BaseModel):
    hour: int
    label: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    avg_pnl: float


class StrategyBucket(BaseModel):
    strategy: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    avg_pnl: float
    profit_factor: float


class DayOfWeekBucket(BaseModel):
    day: int
    label: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float


class HoldTimeBucket(BaseModel):
    label: str
    total_trades: int
    winning_trades: int
    win_rate: float
    avg_pnl: float


class StreakInfo(BaseModel):
    current_type: str  # "win" or "loss"
    current_count: int
    longest_win: int
    longest_loss: int


class AnalyticsResponse(BaseModel):
    period_label: str
    total_trades: int
    by_hour: List[HourBucket]
    by_strategy: List[StrategyBucket]
    by_day_of_week: List[DayOfWeekBucket]
    by_hold_time: List[HoldTimeBucket]
    streak: StreakInfo


@router.get("/dashboard/analytics", response_model=AnalyticsResponse)
def get_analytics(
    days: int = Query(30, ge=1, le=365, description="Lookback period in days"),
    db: Session = Depends(get_db),
):
    """Post-trade analytics: PnL by hour, strategy, day-of-week, hold time, streaks."""
    cutoff = date.today() - timedelta(days=days)
    closed_trades = (
        db.query(Trade)
        .filter(Trade.trade_date >= cutoff)
        .filter(Trade.status == TradeStatus.CLOSED)
        .order_by(Trade.exit_filled_at.asc())
        .all()
    )

    # Link trades to their alerts to get source/ticker info
    trade_ids = [t.id for t in closed_trades]
    alerts = db.query(Alert).filter(Alert.trade_id.in_(trade_ids)).all() if trade_ids else []
    alert_by_trade: dict[int, Alert] = {a.trade_id: a for a in alerts}

    # ── By hour of day (ET) ──
    hour_data: dict[int, list] = defaultdict(list)
    for t in closed_trades:
        if t.entry_filled_at:
            et_time = t.entry_filled_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(_ET)
            hour_data[et_time.hour].append(t)

    hour_labels = {
        9: "9 AM", 10: "10 AM", 11: "11 AM", 12: "12 PM",
        13: "1 PM", 14: "2 PM", 15: "3 PM",
    }
    by_hour = []
    for h in range(9, 16):
        trades = hour_data.get(h, [])
        wins = [t for t in trades if (t.pnl_dollars or 0) > 0]
        total_pnl = sum(t.pnl_dollars or 0 for t in trades)
        by_hour.append(HourBucket(
            hour=h,
            label=hour_labels.get(h, f"{h}:00"),
            total_trades=len(trades),
            winning_trades=len(wins),
            losing_trades=len(trades) - len(wins),
            win_rate=round(len(wins) / len(trades) * 100, 1) if trades else 0,
            total_pnl=round(total_pnl, 2),
            avg_pnl=round(total_pnl / len(trades), 2) if trades else 0,
        ))

    # ── By strategy (source) ──
    strat_data: dict[str, list] = defaultdict(list)
    for t in closed_trades:
        source = t.source or "unknown"
        strat_data[source].append(t)

    by_strategy = []
    for strat, trades in sorted(strat_data.items()):
        wins = [t for t in trades if (t.pnl_dollars or 0) > 0]
        losses = [t for t in trades if (t.pnl_dollars or 0) <= 0]
        total_pnl = sum(t.pnl_dollars or 0 for t in trades)
        gross_wins = sum(t.pnl_dollars or 0 for t in wins)
        gross_losses = abs(sum(t.pnl_dollars or 0 for t in losses))
        by_strategy.append(StrategyBucket(
            strategy=strat,
            total_trades=len(trades),
            winning_trades=len(wins),
            losing_trades=len(losses),
            win_rate=round(len(wins) / len(trades) * 100, 1) if trades else 0,
            total_pnl=round(total_pnl, 2),
            avg_pnl=round(total_pnl / len(trades), 2) if trades else 0,
            profit_factor=round(gross_wins / gross_losses, 2) if gross_losses > 0 else 0,
        ))

    # ── By day of week ──
    dow_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    dow_data: dict[int, list] = defaultdict(list)
    for t in closed_trades:
        dow_data[t.trade_date.weekday()].append(t)

    by_day_of_week = []
    for d in range(5):  # Mon-Fri
        trades = dow_data.get(d, [])
        wins = [t for t in trades if (t.pnl_dollars or 0) > 0]
        total_pnl = sum(t.pnl_dollars or 0 for t in trades)
        by_day_of_week.append(DayOfWeekBucket(
            day=d,
            label=dow_labels[d],
            total_trades=len(trades),
            winning_trades=len(wins),
            losing_trades=len(trades) - len(wins),
            win_rate=round(len(wins) / len(trades) * 100, 1) if trades else 0,
            total_pnl=round(total_pnl, 2),
        ))

    # ── By hold time ──
    hold_buckets_def = [
        ("< 5 min", 0, 5),
        ("5-15 min", 5, 15),
        ("15-30 min", 15, 30),
        ("30-60 min", 30, 60),
        ("60-90 min", 60, 90),
        ("> 90 min", 90, 9999),
    ]
    by_hold_time = []
    for label, lo, hi in hold_buckets_def:
        trades = [
            t for t in closed_trades
            if t.exit_filled_at and t.entry_filled_at
            and lo <= (t.exit_filled_at - t.entry_filled_at).total_seconds() / 60 < hi
        ]
        wins = [t for t in trades if (t.pnl_dollars or 0) > 0]
        total_pnl = sum(t.pnl_dollars or 0 for t in trades)
        by_hold_time.append(HoldTimeBucket(
            label=label,
            total_trades=len(trades),
            winning_trades=len(wins),
            win_rate=round(len(wins) / len(trades) * 100, 1) if trades else 0,
            avg_pnl=round(total_pnl / len(trades), 2) if trades else 0,
        ))

    # ── Streak analysis ──
    current_type = "none"
    current_count = 0
    longest_win = 0
    longest_loss = 0
    streak_win = 0
    streak_loss = 0
    for t in closed_trades:
        if (t.pnl_dollars or 0) > 0:
            streak_win += 1
            streak_loss = 0
            longest_win = max(longest_win, streak_win)
        else:
            streak_loss += 1
            streak_win = 0
            longest_loss = max(longest_loss, streak_loss)

    if streak_win > 0:
        current_type = "win"
        current_count = streak_win
    elif streak_loss > 0:
        current_type = "loss"
        current_count = streak_loss

    return AnalyticsResponse(
        period_label=f"Last {days} days",
        total_trades=len(closed_trades),
        by_hour=by_hour,
        by_strategy=by_strategy,
        by_day_of_week=by_day_of_week,
        by_hold_time=by_hold_time,
        streak=StreakInfo(
            current_type=current_type,
            current_count=current_count,
            longest_win=longest_win,
            longest_loss=longest_loss,
        ),
    )


# --- Chart markers (signals + trades) ---


class ChartMarker(BaseModel):
    time: int       # Fake-UTC unix seconds (ET wall-clock)
    type: str       # "signal", "entry", "exit"
    direction: str  # "CALL" or "PUT"
    label: str
    price: Optional[float] = None


def _to_fake_utc(dt: datetime) -> int:
    """Convert a UTC datetime to the same ET-faked timestamp used by candles."""
    ts_et = dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(_ET)
    utc_offset = int(ts_et.utcoffset().total_seconds())
    return int(dt.timestamp()) + utc_offset


@router.get("/dashboard/chart-markers", response_model=List[ChartMarker])
def get_chart_markers(ticker: str, trade_date: Optional[date] = None, db: Session = Depends(get_db)):
    """Return signals and trades for a ticker on a given date (defaults to today)."""
    today = trade_date or date.today()
    markers: list[ChartMarker] = []

    # Signals (alerts) for this ticker today
    alerts = (
        db.query(Alert)
        .filter(Alert.ticker == ticker)
        .filter(Alert.received_at >= datetime.combine(today, time(0, 0)))
        .filter(Alert.source == "strategy_signal")
        .order_by(Alert.received_at)
        .all()
    )
    for a in alerts:
        direction = a.direction.value if a.direction else "CALL"
        markers.append(ChartMarker(
            time=_to_fake_utc(a.received_at),
            type="signal",
            direction=direction,
            label=f"Signal {direction}",
            price=a.signal_price,
        ))

    # Trades for this ticker today
    trades = (
        db.query(Trade)
        .filter(Trade.ticker == ticker)
        .filter(Trade.trade_date == today)
        .filter(Trade.status != TradeStatus.CANCELLED)
        .order_by(Trade.created_at)
        .all()
    )
    for t in trades:
        direction = t.direction.value if t.direction else "CALL"
        # Entry marker
        if t.entry_filled_at and t.entry_price:
            markers.append(ChartMarker(
                time=_to_fake_utc(t.entry_filled_at),
                type="entry",
                direction=direction,
                label=f"{'Buy' if direction == 'CALL' else 'Sell'} ${t.entry_price:.2f}",
                price=t.entry_price,
            ))
        # Exit marker
        if t.exit_filled_at and t.exit_price:
            pnl = t.pnl_dollars or 0
            pnl_str = f"+${pnl:.0f}" if pnl >= 0 else f"-${abs(pnl):.0f}"
            markers.append(ChartMarker(
                time=_to_fake_utc(t.exit_filled_at),
                type="exit",
                direction=direction,
                label=f"Exit {pnl_str}",
                price=t.exit_price,
            ))

    markers.sort(key=lambda m: m.time)
    return markers
