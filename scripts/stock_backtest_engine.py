"""
Multi-Ticker Options Backtest Engine
======================================
Reuses the signal generation logic (10 strategies) from the existing 0DTE
options backtest engine. Simulates option trades (CALL/PUT) using
Black-Scholes pricing — same approach as the SPY engine but for any ticker.

Each trade: select strike at target delta, price entry/exit via B-S,
P&L = (exit_price - entry_price) * quantity * 100.

This module is imported by multi_ticker_optimizer.py.
"""

import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Literal, Optional

import pandas as pd
import pytz

# Add backend to path so we can import the existing engine
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from app.services.backtest.black_scholes import (
    estimate_option_price_at,
    select_strike_for_delta,
)
from app.services.backtest.engine import (
    BacktestParams,
    Signal,
    _compute_atr,
    _generate_signals,
)
from app.services.backtest.market_data import BarData, fetch_vix_daily

logger = logging.getLogger(__name__)

ET = pytz.timezone("US/Eastern")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")

# Tickers with daily (0DTE) expirations; all others use weekly (Friday) expiry
_0DTE_TICKERS = {"SPY", "QQQ"}


def _strike_interval(ticker_price: float) -> float:
    """Standard option strike intervals based on underlying price."""
    if ticker_price < 20:
        return 0.5
    elif ticker_price < 100:
        return 1.0
    else:
        return 5.0


def _compute_historical_vol(bars_by_day: dict[date, list[BarData]]) -> float:
    """Compute annualized historical volatility from daily close prices.

    Uses close-to-close daily returns, annualized by sqrt(252).
    Returns volatility as a percentage (e.g. 41.0 for 41%).
    Falls back to 30.0 if insufficient data.
    """
    import math

    dates = sorted(bars_by_day.keys())
    if len(dates) < 10:
        return 30.0

    # Get daily closing prices
    daily_closes = []
    for d in dates:
        day_bars = bars_by_day[d]
        if day_bars:
            daily_closes.append(day_bars[-1].close)

    if len(daily_closes) < 10:
        return 30.0

    # Compute log returns
    log_returns = [
        math.log(daily_closes[i] / daily_closes[i - 1])
        for i in range(1, len(daily_closes))
        if daily_closes[i - 1] > 0
    ]

    if len(log_returns) < 5:
        return 30.0

    mean = sum(log_returns) / len(log_returns)
    variance = sum((r - mean) ** 2 for r in log_returns) / (len(log_returns) - 1)
    daily_vol = math.sqrt(variance)
    annualized_vol = daily_vol * math.sqrt(252) * 100  # as percentage

    return round(max(annualized_vol, 10.0), 1)


# ── Data classes ──────────────────────────────────────────────────


@dataclass
class StockBacktestParams:
    start_date: date
    end_date: date
    ticker: str = "NVDA"

    # Signal (same types as options engine)
    signal_type: str = "ema_cross"
    ema_fast: int = 8
    ema_slow: int = 21
    bar_interval: str = "5m"

    # RSI filter
    rsi_period: int = 0
    rsi_ob: float = 70.0
    rsi_os: float = 30.0

    # ORB params
    orb_minutes: int = 15

    # ATR-based stops
    atr_period: int = 0
    atr_stop_mult: float = 2.0

    # Trading windows (ET)
    morning_window_start: time = field(default_factory=lambda: time(9, 45))
    morning_window_end: time = field(default_factory=lambda: time(11, 15))
    afternoon_window_start: time = field(default_factory=lambda: time(12, 45))
    afternoon_window_end: time = field(default_factory=lambda: time(14, 50))
    afternoon_enabled: bool = True

    # Entry — options
    quantity: int = 2
    delta_target: float = 0.40
    entry_limit_below_percent: float = 5.0

    # Exit (options-level percentages)
    stop_loss_percent: float = 16.0
    profit_target_percent: float = 40.0
    trailing_stop_percent: float = 20.0
    trailing_stop_after_scale_out_percent: float = 10.0
    max_hold_minutes: int = 90
    force_exit_time: time = field(default_factory=lambda: time(15, 30))

    # Scale-out / breakeven
    scale_out_enabled: bool = True
    breakeven_trigger_percent: float = 10.0

    # ORB direction filter params
    orb_body_min_pct: float = 0.0
    orb_vwap_filter: bool = False
    orb_gap_fade_filter: bool = False
    orb_time_stop: time = field(default_factory=lambda: time(14, 0))
    orb_stop_mult: float = 1.0
    orb_target_mult: float = 1.5

    # Confluence params
    min_confluence: int = 5
    vol_sma_period: int = 20
    vol_threshold: float = 1.5

    # Limits
    max_daily_trades: int = 10
    max_daily_loss: float = 500.0
    max_consecutive_losses: int = 3


@dataclass
class StockTrade:
    trade_date: date
    ticker: str
    direction: Literal["CALL", "PUT"]
    strike: float
    entry_time: datetime
    entry_price: float  # option entry price
    quantity: int

    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    highest_price_seen: float = 0.0

    scaled_out: bool = False
    scaled_out_price: Optional[float] = None
    scaled_out_quantity: int = 0
    breakeven_stop_applied: bool = False

    orb_range: Optional[float] = None
    orb_entry_level: Optional[float] = None

    pnl_dollars: Optional[float] = None
    pnl_percent: Optional[float] = None
    hold_minutes: Optional[float] = None

    underlying_price: Optional[float] = None
    expiry_date: Optional[date] = None
    dte: int = 0
    delta: Optional[float] = None


@dataclass
class StockDailyResult:
    trade_date: date
    trades: list[StockTrade] = field(default_factory=list)
    pnl: float = 0.0
    winning_trades: int = 0
    losing_trades: int = 0


@dataclass
class StockBacktestResult:
    params: StockBacktestParams
    days: list[StockDailyResult] = field(default_factory=list)
    trades: list[StockTrade] = field(default_factory=list)

    total_pnl: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    max_drawdown: float = 0.0
    profit_factor: float = 0.0
    avg_hold_minutes: float = 0.0
    exit_reasons: dict[str, int] = field(default_factory=dict)


# ── Data loading ──────────────────────────────────────────────────


def load_ticker_csv_bars(
    ticker: str,
    start_date: date,
    end_date: date,
    interval: str = "5m",
) -> dict[date, list[BarData]]:
    """Load bars from local CSV files in the data/ directory."""
    interval_map = {"1m": "1min", "5m": "5min", "10m": "10min", "15m": "15min", "30m": "30min"}
    csv_label = interval_map.get(interval, interval.replace("m", "min"))
    csv_path = os.path.normpath(os.path.join(_DATA_DIR, f"{ticker}_{csv_label}_6months.csv"))

    if not os.path.exists(csv_path):
        logger.warning(f"CSV not found: {csv_path}")
        return {}

    df = pd.read_csv(csv_path, parse_dates=["Timestamp"])
    logger.info(f"Loaded {len(df)} rows from {csv_path}")

    bars_by_day: dict[date, list[BarData]] = {}

    for _, row in df.iterrows():
        ts_naive = row["Timestamp"].to_pydatetime()
        trade_date = ts_naive.date()

        if trade_date < start_date or trade_date > end_date:
            continue

        ts_et = ET.localize(ts_naive)

        bar = BarData(
            timestamp=ts_et,
            open=float(row["Open"]),
            high=float(row["High"]),
            low=float(row["Low"]),
            close=float(row["Close"]),
            volume=int(row["Volume"]),
        )
        bars_by_day.setdefault(trade_date, []).append(bar)

    for day in bars_by_day:
        bars_by_day[day].sort(key=lambda b: b.timestamp)

    logger.info(
        f"CSV {ticker} {interval}: {len(bars_by_day)} days, "
        f"{sum(len(v) for v in bars_by_day.values())} bars "
        f"({start_date} to {end_date})"
    )
    return bars_by_day


# ── Trade simulation (options-level, matches SPY engine) ─────────


def _minutes_to_close(ts: datetime) -> float:
    close_dt = ts.replace(hour=16, minute=0, second=0, microsecond=0)
    return max((close_dt - ts).total_seconds() / 60.0, 0.0)


def _next_weekly_expiry(trade_date: date) -> date:
    """Find the next Friday expiry (weekly options)."""
    days_until_friday = (4 - trade_date.weekday()) % 7
    if days_until_friday == 0:
        return trade_date  # Already Friday
    return trade_date + timedelta(days=days_until_friday)


def _trading_days_to_expiry(ts: datetime, expiry_date: date) -> float:
    """Count trading days from ts to expiry date close (weekdays only).

    Returns fractional trading days: partial first day + full intermediate days.
    """
    current_date = ts.date()
    if current_date >= expiry_date:
        # Same day or past expiry — intraday fraction
        close_ts = ts.replace(hour=16, minute=0, second=0, microsecond=0)
        return max((close_ts - ts).total_seconds() / 60.0 / 390.0, 0.0)

    # Partial first day: remaining market minutes / 390
    close_ts = ts.replace(hour=16, minute=0, second=0, microsecond=0)
    partial = max((close_ts - ts).total_seconds() / 60.0 / 390.0, 0.0)

    # Count weekdays from day after today through expiry_date (inclusive)
    full_days = 0
    d = current_date + timedelta(days=1)
    while d <= expiry_date:
        if d.weekday() < 5:  # Mon–Fri
            full_days += 1
        d += timedelta(days=1)

    return partial + full_days


def _minutes_to_expiry(ts: datetime, expiry_date: date) -> float:
    """B-S compatible minutes to expiry for trading-day-annualized vol.

    Counts actual trading days (weekdays only) and converts to equivalent
    minutes that, when divided by 525600 in the B-S formula, produce the
    correct T = trading_days / 252.  This ensures consistency with vol
    annualized by sqrt(252).
    """
    td = _trading_days_to_expiry(ts, expiry_date)
    # T_correct = td / 252
    # B-S does T = minutes / 525600, so minutes = td * 525600 / 252
    return td * (525600.0 / 252.0)


def _close_trade(
    trade: StockTrade,
    exit_time: datetime,
    exit_price: float,
    exit_reason: str,
) -> None:
    trade.exit_time = exit_time
    trade.exit_price = exit_price
    trade.exit_reason = exit_reason
    trade.hold_minutes = (exit_time - trade.entry_time).total_seconds() / 60

    remaining_qty = trade.quantity - trade.scaled_out_quantity
    remaining_pnl = (exit_price - trade.entry_price) * remaining_qty * 100

    scale_pnl = 0.0
    if trade.scaled_out and trade.scaled_out_price:
        scale_pnl = (trade.scaled_out_price - trade.entry_price) * trade.scaled_out_quantity * 100

    trade.pnl_dollars = round(remaining_pnl + scale_pnl, 2)
    trade.pnl_percent = round(
        (exit_price - trade.entry_price) / trade.entry_price * 100
        if trade.entry_price > 0 else 0,
        2,
    )


def _simulate_option_trade(
    trade: StockTrade,
    bars_after: list[BarData],
    vix: float,
    params: StockBacktestParams,
    atr_at_entry: Optional[float] = None,
    expiry_date: Optional[date] = None,
) -> None:
    """Walk bars and apply exit rules using option pricing."""
    trade.highest_price_seen = trade.entry_price

    # ORB range-based stops (underlying price level)
    use_orb_stops = (
        trade.orb_range is not None
        and trade.orb_entry_level is not None
        and (trade.orb_range or 0) > 0
    )
    spy_stop: Optional[float] = None
    spy_target: Optional[float] = None
    if use_orb_stops:
        if trade.direction == "CALL":
            spy_stop = trade.orb_entry_level - trade.orb_range * params.orb_stop_mult
            spy_target = trade.orb_entry_level + trade.orb_range * params.orb_target_mult
        else:
            spy_stop = trade.orb_entry_level + trade.orb_range * params.orb_stop_mult
            spy_target = trade.orb_entry_level - trade.orb_range * params.orb_target_mult

    # ATR-based stop or fixed % stop (for non-ORB trades)
    stop_price = 0.01
    if not use_orb_stops:
        if params.atr_period > 0 and atr_at_entry is not None and atr_at_entry > 0:
            atr_stop_offset = atr_at_entry * params.atr_stop_mult
            approx_opt_atr = atr_stop_offset * 0.4  # rough delta
            stop_price = max(trade.entry_price - approx_opt_atr, 0.01)
        else:
            stop_price = trade.entry_price * (1 - params.stop_loss_percent / 100)

    for bar in bars_after:
        mtc = _minutes_to_expiry(bar.timestamp, expiry_date) if expiry_date else _minutes_to_close(bar.timestamp)
        opt_price = max(
            estimate_option_price_at(bar.close, trade.strike, mtc, vix, trade.direction),
            0.01,
        )

        if opt_price > trade.highest_price_seen:
            trade.highest_price_seen = opt_price

        elapsed = (bar.timestamp - trade.entry_time).total_seconds() / 60
        gain_pct = (opt_price - trade.entry_price) / trade.entry_price * 100 if trade.entry_price > 0 else 0

        # P1: Force exit at day end
        if bar.timestamp.time() >= params.force_exit_time:
            _close_trade(trade, bar.timestamp, opt_price, "TIME_BASED")
            return

        # ORB time stop
        if use_orb_stops and bar.timestamp.time() >= params.orb_time_stop:
            _close_trade(trade, bar.timestamp, opt_price, "ORB_TIME_STOP")
            return

        # P2: Max hold
        if elapsed >= params.max_hold_minutes:
            _close_trade(trade, bar.timestamp, opt_price, "MAX_HOLD_TIME")
            return

        # P3: Stop loss
        if use_orb_stops:
            if trade.direction == "CALL" and bar.low <= spy_stop:
                _close_trade(trade, bar.timestamp, opt_price, "STOP_LOSS")
                return
            elif trade.direction == "PUT" and bar.high >= spy_stop:
                _close_trade(trade, bar.timestamp, opt_price, "STOP_LOSS")
                return
        else:
            if opt_price <= stop_price:
                _close_trade(trade, bar.timestamp, opt_price, "STOP_LOSS")
                return

        # Breakeven stop adjustment (non-ORB only)
        if not use_orb_stops:
            if (
                not trade.breakeven_stop_applied
                and params.breakeven_trigger_percent > 0
                and trade.highest_price_seen >= trade.entry_price * (1 + params.breakeven_trigger_percent / 100)
            ):
                stop_price = trade.entry_price
                trade.breakeven_stop_applied = True

        # P4: Profit target
        if use_orb_stops:
            if trade.direction == "CALL" and bar.high >= spy_target:
                _close_trade(trade, bar.timestamp, opt_price, "PROFIT_TARGET")
                return
            elif trade.direction == "PUT" and bar.low <= spy_target:
                _close_trade(trade, bar.timestamp, opt_price, "PROFIT_TARGET")
                return
        else:
            if gain_pct >= params.profit_target_percent:
                if params.scale_out_enabled and trade.quantity >= 2 and not trade.scaled_out:
                    trade.scaled_out = True
                    trade.scaled_out_quantity = trade.quantity // 2
                    trade.scaled_out_price = opt_price
                elif not trade.scaled_out:
                    _close_trade(trade, bar.timestamp, opt_price, "PROFIT_TARGET")
                    return

        # P5: Trailing stop
        if trade.highest_price_seen > trade.entry_price:
            trail_pct = (
                params.trailing_stop_after_scale_out_percent
                if trade.scaled_out
                else params.trailing_stop_percent
            )
            trail_price = trade.highest_price_seen * (1 - trail_pct / 100)
            if opt_price <= trail_price:
                _close_trade(trade, bar.timestamp, opt_price, "TRAILING_STOP")
                return

    # End of day — force close at last bar
    if trade.exit_time is None and bars_after:
        last = bars_after[-1]
        mtc = _minutes_to_expiry(last.timestamp, expiry_date) if expiry_date else _minutes_to_close(last.timestamp)
        last_price = max(
            estimate_option_price_at(last.close, trade.strike, mtc, vix, trade.direction),
            0.01,
        )
        _close_trade(trade, last.timestamp, last_price, "TIME_BASED")


# ── Summary computation ───────────────────────────────────────────


def _compute_summary(result: StockBacktestResult) -> None:
    trades = result.trades
    result.total_trades = len(trades)
    if not trades:
        return

    wins = [t for t in trades if (t.pnl_dollars or 0) > 0]
    losses = [t for t in trades if (t.pnl_dollars or 0) <= 0]

    result.winning_trades = len(wins)
    result.losing_trades = len(losses)
    result.win_rate = round(len(wins) / len(trades) * 100, 1)
    result.total_pnl = round(sum(t.pnl_dollars or 0 for t in trades), 2)

    win_pnls = [t.pnl_dollars for t in wins if t.pnl_dollars]
    loss_pnls = [t.pnl_dollars for t in losses if t.pnl_dollars]

    result.avg_win = round(sum(win_pnls) / len(win_pnls), 2) if win_pnls else 0
    result.avg_loss = round(sum(loss_pnls) / len(loss_pnls), 2) if loss_pnls else 0
    result.largest_win = round(max(win_pnls), 2) if win_pnls else 0
    result.largest_loss = round(min(loss_pnls), 2) if loss_pnls else 0

    gross_wins = sum(win_pnls) if win_pnls else 0
    gross_losses = abs(sum(loss_pnls)) if loss_pnls else 0
    result.profit_factor = round(gross_wins / gross_losses, 2) if gross_losses > 0 else 0

    # Max drawdown
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        cum += t.pnl_dollars or 0
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    result.max_drawdown = round(max_dd, 2)

    hold_times = [t.hold_minutes for t in trades if t.hold_minutes is not None]
    result.avg_hold_minutes = round(sum(hold_times) / len(hold_times), 1) if hold_times else 0

    reasons: dict[str, int] = {}
    for t in trades:
        r = t.exit_reason or "UNKNOWN"
        reasons[r] = reasons.get(r, 0) + 1
    result.exit_reasons = reasons


# ── VIX data loading ─────────────────────────────────────────────


def load_vix_data(start_date: date, end_date: date) -> dict[date, float]:
    """Load VIX daily data. First try local CSV, then fall back to yfinance."""
    vix_csv = os.path.normpath(os.path.join(_DATA_DIR, "VIX_daily.csv"))
    if os.path.exists(vix_csv):
        df = pd.read_csv(vix_csv, parse_dates=["Date"])
        vix_by_day: dict[date, float] = {}
        for _, row in df.iterrows():
            d = row["Date"].to_pydatetime().date()
            if start_date <= d <= end_date:
                vix_by_day[d] = float(row["Close"])
        if vix_by_day:
            logger.info(f"Loaded {len(vix_by_day)} VIX days from CSV")
            return vix_by_day

    # Fall back to yfinance
    return fetch_vix_daily(start_date, end_date)


# ── Main entry point ──────────────────────────────────────────────


def run_stock_backtest(
    params: StockBacktestParams,
    bars_by_day: Optional[dict[date, list[BarData]]] = None,
    vix_by_day: Optional[dict[date, float]] = None,
) -> StockBacktestResult:
    """Run an options-level backtest using Black-Scholes pricing for any ticker."""

    if bars_by_day is None:
        bars_by_day = load_ticker_csv_bars(
            params.ticker, params.start_date, params.end_date, params.bar_interval
        )

    if vix_by_day is None:
        dates = sorted(bars_by_day.keys()) if bars_by_day else []
        if dates:
            vix_by_day = load_vix_data(dates[0], dates[-1])
        else:
            vix_by_day = {}

    # Build a BacktestParams to pass to _generate_signals (it expects this type)
    engine_params = BacktestParams(
        start_date=params.start_date,
        end_date=params.end_date,
        signal_type=params.signal_type,
        ema_fast=params.ema_fast,
        ema_slow=params.ema_slow,
        bar_interval=params.bar_interval,
        rsi_period=params.rsi_period,
        rsi_ob=params.rsi_ob,
        rsi_os=params.rsi_os,
        orb_minutes=params.orb_minutes,
        atr_period=params.atr_period,
        atr_stop_mult=params.atr_stop_mult,
        morning_window_start=params.morning_window_start,
        morning_window_end=params.morning_window_end,
        afternoon_window_start=params.afternoon_window_start,
        afternoon_window_end=params.afternoon_window_end,
        afternoon_enabled=params.afternoon_enabled,
        orb_body_min_pct=params.orb_body_min_pct,
        orb_vwap_filter=params.orb_vwap_filter,
        orb_gap_fade_filter=params.orb_gap_fade_filter,
        orb_time_stop=params.orb_time_stop,
        orb_stop_mult=params.orb_stop_mult,
        orb_target_mult=params.orb_target_mult,
        min_confluence=params.min_confluence,
        vol_sma_period=params.vol_sma_period,
        vol_threshold=params.vol_threshold,
    )

    result = StockBacktestResult(params=params)
    default_vix = 20.0
    prev_close: Optional[float] = None
    is_0dte = params.ticker in _0DTE_TICKERS

    # For non-SPY/QQQ, compute per-ticker historical vol from full CSV
    ticker_vol: Optional[float] = None
    if not is_0dte:
        all_bars = load_ticker_csv_bars(
            params.ticker, date(2000, 1, 1), date(2099, 12, 31), params.bar_interval
        )
        ticker_vol = _compute_historical_vol(all_bars)
        logger.info(f"{params.ticker} historical vol: {ticker_vol}%")

    for trade_date in sorted(bars_by_day.keys()):
        day_bars = bars_by_day[trade_date]
        # Use per-ticker vol for stocks, VIX for SPY/QQQ
        vix = vix_by_day.get(trade_date, default_vix) if is_0dte else (ticker_vol or default_vix)
        day_result = StockDailyResult(trade_date=trade_date)

        signals = _generate_signals(day_bars, engine_params, prev_close=prev_close)

        # Precompute ATR if enabled
        day_atr: list[Optional[float]] = [None] * len(day_bars)
        if params.atr_period > 0:
            day_atr = _compute_atr(day_bars, params.atr_period)

        daily_trades = 0
        daily_pnl = 0.0
        consecutive_losses = 0
        last_exit_time: Optional[datetime] = None

        for signal in signals:
            # Limits
            if daily_trades >= params.max_daily_trades:
                continue
            if daily_pnl <= -params.max_daily_loss:
                continue
            if consecutive_losses >= params.max_consecutive_losses:
                continue

            # Cooldown: 5 min between trades
            if last_exit_time:
                if (signal.timestamp - last_exit_time).total_seconds() / 60 < 5:
                    continue

            # Don't enter too close to close
            mtc_close = _minutes_to_close(signal.timestamp)
            if mtc_close < 30:
                continue

            # Determine expiry and time-to-expiry for option pricing
            if is_0dte:
                expiry = trade_date
                dte = 0
                mtc = mtc_close
            else:
                expiry = _next_weekly_expiry(trade_date)
                dte = (expiry - trade_date).days
                mtc = _minutes_to_expiry(signal.timestamp, expiry)

            # Select strike at target delta using Black-Scholes
            interval = 1.0 if is_0dte else _strike_interval(signal.ticker_price)
            strike, opt_data = select_strike_for_delta(
                ticker_price=signal.ticker_price,
                target_delta=params.delta_target,
                minutes_to_expiry=mtc,
                vix=vix,
                option_type=signal.direction,
                strike_interval=interval,
            )

            entry_price = round(
                max(opt_data.price * (1 - params.entry_limit_below_percent / 100), 0.01),
                2,
            )

            entry_idx = next(
                (i for i, b in enumerate(day_bars) if b.timestamp >= signal.timestamp),
                None,
            )
            if entry_idx is None:
                continue
            bars_after = day_bars[entry_idx + 1:]
            if not bars_after:
                continue

            trade = StockTrade(
                trade_date=trade_date,
                ticker=params.ticker,
                direction=signal.direction,
                strike=strike,
                entry_time=signal.timestamp,
                entry_price=entry_price,
                quantity=params.quantity,
                orb_range=signal.orb_range,
                orb_entry_level=signal.orb_entry_level,
                underlying_price=round(signal.ticker_price, 2),
                expiry_date=expiry,
                dte=dte,
                delta=round(opt_data.delta, 4),
            )

            atr_val = day_atr[entry_idx] if entry_idx < len(day_atr) else None
            _simulate_option_trade(trade, bars_after, vix, params, atr_at_entry=atr_val,
                                   expiry_date=expiry if not is_0dte else None)

            if trade.exit_time is not None:
                daily_trades += 1
                daily_pnl += trade.pnl_dollars or 0
                last_exit_time = trade.exit_time

                day_result.trades.append(trade)
                result.trades.append(trade)

                if (trade.pnl_dollars or 0) > 0:
                    day_result.winning_trades += 1
                    consecutive_losses = 0
                else:
                    day_result.losing_trades += 1
                    consecutive_losses += 1

        day_result.pnl = round(daily_pnl, 2)
        if day_bars:
            prev_close = day_bars[-1].close
        result.days.append(day_result)

    _compute_summary(result)
    return result
