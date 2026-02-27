"""Backtesting engine for 0DTE SPY options strategy.

Walks through historical bars, generates signals, simulates trade entry/exit
using the same exit priority order as the live system (exit_engine.py).
"""

import bisect
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Literal, Optional

from app.services.backtest.black_scholes import (
    estimate_option_price_at,
    estimate_option_price_and_delta,
    select_strike_for_delta,
)
from app.services.backtest.spread_model import (
    estimate_spread_pct,
)
from app.services.backtest.market_data import BarData, fetch_spy_bars, fetch_vix_daily, load_csv_bars

logger = logging.getLogger(__name__)


# ── Data classes ──────────────────────────────────────────────────


@dataclass
class BacktestParams:
    start_date: date
    end_date: date

    # Signal
    signal_type: str = "ema_cross"
    ema_fast: int = 8
    ema_slow: int = 21
    bar_interval: str = "5m"

    # RSI filter (0 = disabled)
    rsi_period: int = 0
    rsi_ob: float = 70.0   # overbought threshold (PUT signals)
    rsi_os: float = 30.0   # oversold threshold (CALL signals)

    # ORB params
    orb_minutes: int = 15  # opening range window (minutes after 9:30)

    # ATR-based stops (0 = disabled, use fixed % instead)
    atr_period: int = 0
    atr_stop_mult: float = 2.0  # stop = entry - atr * mult

    # Trading windows (ET)
    morning_window_start: time = field(default_factory=lambda: time(9, 45))
    morning_window_end: time = field(default_factory=lambda: time(11, 15))
    afternoon_window_start: time = field(default_factory=lambda: time(12, 45))
    afternoon_window_end: time = field(default_factory=lambda: time(14, 50))
    afternoon_enabled: bool = True

    # Entry
    entry_limit_below_percent: float = 0.0  # removed: 5% free edge doesn't exist in live trading
    quantity: int = 2
    delta_target: float = 0.4
    dynamic_delta: bool = False  # Use RegimeClassifier + VIX + time-of-day per signal

    # Exit
    stop_loss_percent: float = 16.0
    profit_target_percent: float = 40.0
    trailing_stop_percent: float = 20.0
    trailing_stop_after_scale_out_percent: float = 10.0
    max_hold_minutes: int = 90
    force_exit_time: time = field(default_factory=lambda: time(15, 30))

    # Scale-out / breakeven
    scale_out_enabled: bool = True
    breakeven_trigger_percent: float = 10.0

    # Confluence strategy params
    min_confluence: int = 5       # minimum score (out of 6, or 7 with pivots) to trigger signal
    vol_sma_period: int = 20      # volume moving average lookback
    vol_threshold: float = 1.5    # volume must be this multiple of average

    # Pivot point S/R (7th confluence factor + optional entry filter)
    pivot_enabled: bool = False           # enable pivot point calculations
    pivot_proximity_pct: float = 0.3      # % threshold for "near a pivot level"
    pivot_filter_enabled: bool = False    # block signals that fight S/R

    # Bollinger Bands
    bb_period: int = 20
    bb_std_mult: float = 2.0

    # MACD
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal_period: int = 9

    # Data source
    data_source: str = "yfinance"  # "csv" or "yfinance"

    # ORB direction filter params
    orb_body_min_pct: float = 0.4        # min ORB body/range ratio (0-1), 0=disabled
    orb_vwap_filter: bool = True         # require ORB close on VWAP side
    orb_gap_fade_filter: bool = True     # require gap to oppose ORB direction
    orb_time_stop: time = field(default_factory=lambda: time(14, 0))
    orb_stop_mult: float = 1.0          # stop = N * ORB range below breakout
    orb_target_mult: float = 1.5        # target = N * ORB range above breakout

    # Limits
    max_daily_trades: int = 10
    max_daily_loss: float = 2000.0
    max_consecutive_losses: int = 3

    # VIX filter (skip trades when VIX outside range; 0/100 = disabled)
    vix_min: float = 0.0
    vix_max: float = 100.0

    # Slippage: model bid-ask spread costs on entry and exit
    entry_slippage_percent: float = 1.0   # flat fallback when spread model disabled
    exit_slippage_percent: float = 1.0    # flat fallback when spread model disabled
    spread_model_enabled: bool = True     # dynamic spread from delta/time/VIX (overrides flat %)

    # Entry confirmation: require N 1-minute bars to confirm direction (0 = immediate)
    entry_confirm_minutes: int = 0


@dataclass
class MarketDataCache:
    """Pre-fetched market data to avoid redundant yfinance downloads."""
    bars_by_day: dict  # dict[date, list[BarData]]
    vix_by_day: dict   # dict[date, float]


@dataclass
class SimulatedTrade:
    trade_date: date
    direction: Literal["CALL", "PUT"]
    strike: float
    entry_time: datetime
    entry_price: float
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

    entry_reason: Optional[str] = None
    exit_detail: Optional[str] = None


@dataclass
class DailyResult:
    trade_date: date
    trades: list[SimulatedTrade] = field(default_factory=list)
    pnl: float = 0.0
    winning_trades: int = 0
    losing_trades: int = 0


@dataclass
class BacktestResult:
    params: BacktestParams
    days: list[DailyResult] = field(default_factory=list)
    trades: list[SimulatedTrade] = field(default_factory=list)

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


# ── Signal generation ─────────────────────────────────────────────


def _compute_ema(values: list[float], period: int) -> list[Optional[float]]:
    if len(values) < period:
        return [None] * len(values)

    result: list[Optional[float]] = [None] * (period - 1)
    sma = sum(values[:period]) / period
    result.append(sma)

    k = 2.0 / (period + 1)
    for i in range(period, len(values)):
        val = values[i] * k + result[-1] * (1 - k)
        result.append(val)

    return result


def _compute_rsi(closes: list[float], period: int) -> list[Optional[float]]:
    """Wilder's RSI."""
    if len(closes) < period + 1:
        return [None] * len(closes)

    result: list[Optional[float]] = [None] * period

    gains = []
    losses = []
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    rs = avg_gain / avg_loss if avg_loss > 0 else 100
    result.append(100 - 100 / (1 + rs))

    for i in range(period + 1, len(closes)):
        delta = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(delta, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-delta, 0)) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else 100
        result.append(100 - 100 / (1 + rs))

    return result


def _compute_atr(bars: list[BarData], period: int) -> list[Optional[float]]:
    """Average True Range (Wilder smoothing)."""
    if len(bars) < period + 1:
        return [None] * len(bars)

    trs: list[float] = [0.0]  # first bar has no previous close
    for i in range(1, len(bars)):
        h, l, pc = bars[i].high, bars[i].low, bars[i - 1].close
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))

    result: list[Optional[float]] = [None] * period
    atr = sum(trs[1 : period + 1]) / period
    result.append(atr)

    for i in range(period + 1, len(bars)):
        atr = (atr * (period - 1) + trs[i]) / period
        result.append(atr)

    return result


def _compute_vwap(bars: list[BarData]) -> list[Optional[float]]:
    vwap: list[Optional[float]] = []
    cum_tp_vol = 0.0
    cum_vol = 0
    for bar in bars:
        tp = (bar.high + bar.low + bar.close) / 3.0
        cum_tp_vol += tp * bar.volume
        cum_vol += bar.volume
        vwap.append(cum_tp_vol / cum_vol if cum_vol > 0 else None)
    return vwap


@dataclass
class PivotLevels:
    """Classic floor trader pivot points from prior day OHLC."""
    pivot: float    # P = (H + L + C) / 3
    r1: float       # R1 = 2*P - L
    s1: float       # S1 = 2*P - H
    r2: float       # R2 = P + (H - L)
    s2: float       # S2 = P - (H - L)


def compute_pivot_levels(
    prev_high: float,
    prev_low: float,
    prev_close: float,
) -> PivotLevels:
    """Compute classic pivot points from prior day OHLC."""
    p = (prev_high + prev_low + prev_close) / 3.0
    return PivotLevels(
        pivot=round(p, 2),
        r1=round(2.0 * p - prev_low, 2),
        s1=round(2.0 * p - prev_high, 2),
        r2=round(p + (prev_high - prev_low), 2),
        s2=round(p - (prev_high - prev_low), 2),
    )


@dataclass
class Signal:
    timestamp: datetime
    direction: Literal["CALL", "PUT"]
    ticker_price: float
    reason: str
    orb_range: Optional[float] = None
    orb_entry_level: Optional[float] = None
    confluence_score: Optional[int] = None
    confluence_max_score: Optional[int] = None
    rel_vol: Optional[float] = None


def _compute_bollinger(closes: list[float], period: int = 20, std_mult: float = 2.0):
    """Returns (upper, lower, mid) band lists."""
    n = len(closes)
    upper: list[Optional[float]] = [None] * n
    lower: list[Optional[float]] = [None] * n
    mid: list[Optional[float]] = [None] * n

    for i in range(period - 1, n):
        window = closes[i - period + 1 : i + 1]
        m = sum(window) / period
        var = sum((x - m) ** 2 for x in window) / period
        std = var ** 0.5
        mid[i] = m
        upper[i] = m + std_mult * std
        lower[i] = m - std_mult * std

    return upper, lower, mid


def _compute_macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[list[Optional[float]], list[Optional[float]], list[Optional[float]]]:
    """MACD line, signal line, histogram."""
    ema_fast = _compute_ema(closes, fast)
    ema_slow = _compute_ema(closes, slow)
    n = len(closes)

    macd_line: list[Optional[float]] = [None] * n
    for i in range(n):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            macd_line[i] = ema_fast[i] - ema_slow[i]

    # Signal line = EMA of MACD line (skip Nones)
    macd_vals = [v for v in macd_line if v is not None]
    if len(macd_vals) < signal:
        return macd_line, [None] * n, [None] * n

    sig_ema = _compute_ema(macd_vals, signal)
    signal_line: list[Optional[float]] = [None] * n
    j = 0
    for i in range(n):
        if macd_line[i] is not None:
            signal_line[i] = sig_ema[j] if j < len(sig_ema) else None
            j += 1

    histogram: list[Optional[float]] = [None] * n
    for i in range(n):
        if macd_line[i] is not None and signal_line[i] is not None:
            histogram[i] = macd_line[i] - signal_line[i]

    return macd_line, signal_line, histogram


def _compute_volume_sma(bars: list[BarData], period: int) -> list[Optional[float]]:
    """Simple moving average of volume."""
    n = len(bars)
    if n < period:
        return [None] * n

    result: list[Optional[float]] = [None] * (period - 1)
    window_sum = sum(b.volume for b in bars[:period])
    result.append(window_sum / period)

    for i in range(period, n):
        window_sum += bars[i].volume - bars[i - period].volume
        result.append(window_sum / period)

    return result


def _generate_signals(
    bars: list[BarData],
    params: BacktestParams,
    prev_close: Optional[float] = None,
    prev_high: Optional[float] = None,
    prev_low: Optional[float] = None,
    confirm_bars: Optional[list[BarData]] = None,
) -> list[Signal]:
    if len(bars) < max(params.ema_slow + 1, 26):
        return []

    closes = [b.close for b in bars]
    ema_f = _compute_ema(closes, params.ema_fast)
    ema_s = _compute_ema(closes, params.ema_slow)
    vwap = _compute_vwap(bars)

    # RSI (computed if needed for rsi strategies OR confluence)
    rsi: list[Optional[float]] = [None] * len(bars)
    rsi_period = params.rsi_period if params.rsi_period > 0 else 9
    if params.rsi_period > 0 or params.signal_type == "confluence":
        rsi = _compute_rsi(closes, rsi_period)

    # Bollinger Bands (for bb_squeeze strategy)
    bb_upper, bb_lower, bb_mid = _compute_bollinger(closes, params.bb_period, params.bb_std_mult)

    # MACD and Volume SMA (for confluence strategy)
    macd_line: list[Optional[float]] = [None] * len(bars)
    macd_sig_line: list[Optional[float]] = [None] * len(bars)
    macd_hist: list[Optional[float]] = [None] * len(bars)
    vol_sma: list[Optional[float]] = [None] * len(bars)
    if params.signal_type == "confluence":
        macd_line, macd_sig_line, macd_hist = _compute_macd(closes, params.macd_fast, params.macd_slow, params.macd_signal_period)
        vol_sma = _compute_volume_sma(bars, params.vol_sma_period)

    # Pivot points (from prior day OHLC)
    pivots: Optional[PivotLevels] = None
    if params.pivot_enabled and prev_high is not None and prev_low is not None and prev_close is not None:
        pivots = compute_pivot_levels(prev_high, prev_low, prev_close)

    # ORB: compute opening range from first N minutes
    orb_high: Optional[float] = None
    orb_low: Optional[float] = None
    orb_open: Optional[float] = None
    orb_close: Optional[float] = None
    orb_ready = False
    if params.signal_type in ("orb", "orb_direction"):
        open_time = bars[0].timestamp.replace(hour=9, minute=30, second=0) if bars else None
        if open_time:
            from datetime import timedelta
            orb_end_time = open_time + timedelta(minutes=params.orb_minutes)
            orb_bars = [b for b in bars if b.timestamp < orb_end_time]
            if orb_bars:
                orb_high = max(b.high for b in orb_bars)
                orb_low = min(b.low for b in orb_bars)
                orb_open = orb_bars[0].open
                orb_close = orb_bars[-1].close
                orb_ready = True

    windows = [(params.morning_window_start, params.morning_window_end)]
    if params.afternoon_enabled:
        windows.append((params.afternoon_window_start, params.afternoon_window_end))

    signals: list[Signal] = []

    for i in range(1, len(bars)):
        bar = bars[i]
        bt = bar.timestamp.time()

        # Strategy-specific window checks
        if params.signal_type == "vwap_reclaim":
            if not (time(10, 30) <= bt <= time(12, 0)):
                continue
        elif params.signal_type == "orb_direction":
            orb_end_minutes = 30 + params.orb_minutes
            orb_end_t = time(9 + orb_end_minutes // 60, orb_end_minutes % 60)
            if bt < orb_end_t or bt > params.orb_time_stop:
                continue
        else:
            if not any(s <= bt <= e for s, e in windows):
                continue

        direction: Optional[str] = None
        reason = ""
        sig_orb_range: Optional[float] = None
        sig_orb_entry: Optional[float] = None
        sig_confluence_score: Optional[int] = None
        sig_confluence_max: Optional[int] = None
        sig_rel_vol: Optional[float] = None

        if params.signal_type == "confluence":
            # ── Multi-indicator confluence scoring ──
            # 6 factors scored independently for CALL and PUT
            call_score = 0
            put_score = 0
            call_factors: list[str] = []
            put_factors: list[str] = []

            # 1. VWAP bias: close above/below VWAP
            if vwap[i] is not None:
                if bar.close > vwap[i]:
                    call_score += 1
                    call_factors.append("VWAP")
                elif bar.close < vwap[i]:
                    put_score += 1
                    put_factors.append("VWAP")

            # 2. EMA trend: fast above/below slow
            if ema_f[i] is not None and ema_s[i] is not None:
                if ema_f[i] > ema_s[i]:
                    call_score += 1
                    call_factors.append("EMA")
                elif ema_f[i] < ema_s[i]:
                    put_score += 1
                    put_factors.append("EMA")

            # 3. RSI favorable zone (not at extremes)
            if rsi[i] is not None:
                if rsi[i] < params.rsi_ob:
                    call_score += 1
                    call_factors.append(f"RSI:{rsi[i]:.0f}")
                if rsi[i] > params.rsi_os:
                    put_score += 1
                    put_factors.append(f"RSI:{rsi[i]:.0f}")

            # 4. MACD histogram direction
            if macd_hist[i] is not None:
                if macd_hist[i] > 0:
                    call_score += 1
                    call_factors.append("MACD")
                elif macd_hist[i] < 0:
                    put_score += 1
                    put_factors.append("MACD")

            # 5. Relative volume above threshold (confirms EMA trend direction)
            if vol_sma[i] is not None and vol_sma[i] > 0:
                rel_vol = bar.volume / vol_sma[i]
                if rel_vol >= params.vol_threshold:
                    if ema_f[i] is not None and ema_s[i] is not None:
                        if ema_f[i] > ema_s[i]:
                            call_score += 1
                            call_factors.append(f"Vol:{rel_vol:.1f}x")
                        elif ema_f[i] < ema_s[i]:
                            put_score += 1
                            put_factors.append(f"Vol:{rel_vol:.1f}x")

            # 6. Price action: candle direction
            if bar.close > bar.open:
                call_score += 1
                call_factors.append("Candle")
            elif bar.close < bar.open:
                put_score += 1
                put_factors.append("Candle")

            # 7. Pivot point S/R proximity
            if pivots is not None:
                proximity = params.pivot_proximity_pct / 100.0
                price = bar.close
                near_s1 = abs(price - pivots.s1) / pivots.s1 < proximity if pivots.s1 != 0 else False
                near_s2 = abs(price - pivots.s2) / pivots.s2 < proximity if pivots.s2 != 0 else False
                near_r1 = abs(price - pivots.r1) / pivots.r1 < proximity if pivots.r1 != 0 else False
                near_r2 = abs(price - pivots.r2) / pivots.r2 < proximity if pivots.r2 != 0 else False
                if near_s1 or near_s2:
                    call_score += 1
                    nearest = "S1" if abs(price - pivots.s1) < abs(price - pivots.s2) else "S2"
                    call_factors.append(f"Pivot:{nearest}")
                elif near_r1 or near_r2:
                    put_score += 1
                    nearest = "R1" if abs(price - pivots.r1) < abs(price - pivots.r2) else "R2"
                    put_factors.append(f"Pivot:{nearest}")
                elif price < pivots.pivot:
                    call_score += 1
                    call_factors.append("Pivot:<P")
                elif price > pivots.pivot:
                    put_score += 1
                    put_factors.append("Pivot:>P")

            # Fire signal if score meets minimum confluence threshold
            max_score = 7 if pivots is not None else 6

            # Compute rel_vol for this bar (used for confidence sizing)
            bar_rel_vol = None
            if vol_sma[i] is not None and vol_sma[i] > 0:
                bar_rel_vol = round(bar.volume / vol_sma[i], 2)

            if call_score >= params.min_confluence and call_score > put_score:
                direction = "CALL"
                reason = f"Confluence {call_score}/{max_score}: {', '.join(call_factors)}"
                sig_confluence_score = call_score
                sig_confluence_max = max_score
                sig_rel_vol = bar_rel_vol
            elif put_score >= params.min_confluence and put_score > call_score:
                direction = "PUT"
                reason = f"Confluence {put_score}/{max_score}: {', '.join(put_factors)}"
                sig_confluence_score = put_score
                sig_confluence_max = max_score
                sig_rel_vol = bar_rel_vol

        elif params.signal_type == "orb":
            # ORB: trade breakouts of opening range
            if orb_ready and orb_high is not None and orb_low is not None:
                prev = bars[i - 1]
                if prev.close <= orb_high and bar.close > orb_high:
                    direction, reason = "CALL", f"ORB breakout above {orb_high:.2f}"
                elif prev.close >= orb_low and bar.close < orb_low:
                    direction, reason = "PUT", f"ORB breakdown below {orb_low:.2f}"

        elif params.signal_type == "orb_direction":
            # ORB with direction filter: only trade in ORB candle direction
            if orb_ready and orb_high is not None and orb_low is not None:
                orb_rng = orb_high - orb_low
                if orb_rng > 0:
                    prev = bars[i - 1]
                    orb_body = abs((orb_close or 0) - (orb_open or 0))
                    body_pct = orb_body / orb_rng

                    if body_pct >= params.orb_body_min_pct:
                        orb_bullish = (orb_close or 0) > (orb_open or 0)
                        orb_bearish = (orb_close or 0) < (orb_open or 0)

                        vwap_ok = True
                        if params.orb_vwap_filter and vwap[i] is not None:
                            if orb_bullish and (orb_close or 0) < vwap[i]:
                                vwap_ok = False
                            elif orb_bearish and (orb_close or 0) > vwap[i]:
                                vwap_ok = False

                        gap_ok = True
                        if params.orb_gap_fade_filter and prev_close is not None and orb_open is not None:
                            gap = orb_open - prev_close
                            if orb_bullish and gap > 0:
                                gap_ok = False  # want gap to oppose direction
                            elif orb_bearish and gap < 0:
                                gap_ok = False

                        if vwap_ok and gap_ok:
                            if orb_bullish and prev.close <= orb_high and bar.close > orb_high:
                                direction = "CALL"
                                reason = f"ORB-{params.orb_minutes} bullish breakout (body {body_pct:.0%})"
                                sig_orb_range = orb_rng
                                sig_orb_entry = orb_high
                            elif orb_bearish and prev.close >= orb_low and bar.close < orb_low:
                                direction = "PUT"
                                reason = f"ORB-{params.orb_minutes} bearish breakdown (body {body_pct:.0%})"
                                sig_orb_range = orb_rng
                                sig_orb_entry = orb_low

        elif params.signal_type == "vwap_reclaim":
            # VWAP reclaim: price crosses VWAP with strong bar
            if vwap[i] is not None and vwap[i - 1] is not None:
                prev = bars[i - 1]
                bar_body = abs(bar.close - bar.open)
                if bar_body >= 0.30:
                    if prev.close < vwap[i - 1] and bar.close > vwap[i]:
                        direction = "CALL"
                        reason = f"VWAP reclaim bullish (body ${bar_body:.2f})"
                    elif prev.close > vwap[i - 1] and bar.close < vwap[i]:
                        direction = "PUT"
                        reason = f"VWAP reclaim bearish (body ${bar_body:.2f})"

        elif params.signal_type == "vwap_rsi":
            # VWAP for direction + RSI for timing
            if vwap[i] is not None and rsi[i] is not None:
                if bar.close > vwap[i] and rsi[i] <= params.rsi_os:
                    direction, reason = "CALL", f"Above VWAP + RSI oversold ({rsi[i]:.0f})"
                elif bar.close < vwap[i] and rsi[i] >= params.rsi_ob:
                    direction, reason = "PUT", f"Below VWAP + RSI overbought ({rsi[i]:.0f})"

        elif params.signal_type == "bb_squeeze":
            # Bollinger Band squeeze breakout
            if bb_upper[i] is not None and bb_lower[i] is not None and bb_upper[i - 1] is not None:
                width = bb_upper[i] - bb_lower[i]
                prev_width = (bb_upper[i - 1] or 0) - (bb_lower[i - 1] or 0)
                expanding = width > prev_width  # bands expanding = squeeze release
                if expanding:
                    if bar.close > bb_upper[i]:
                        direction, reason = "CALL", "BB squeeze breakout above"
                    elif bar.close < bb_lower[i]:
                        direction, reason = "PUT", "BB squeeze breakdown below"

        elif params.signal_type == "rsi_reversal":
            # Pure RSI reversal signals
            if rsi[i] is not None and rsi[i - 1] is not None:
                if rsi[i - 1] < params.rsi_os and rsi[i] >= params.rsi_os:
                    direction, reason = "CALL", f"RSI crossed above {params.rsi_os:.0f}"
                elif rsi[i - 1] > params.rsi_ob and rsi[i] <= params.rsi_ob:
                    direction, reason = "PUT", f"RSI crossed below {params.rsi_ob:.0f}"

        else:
            # Original strategies: ema_cross, vwap_cross, ema_vwap
            if any(v is None for v in [ema_f[i], ema_f[i - 1], ema_s[i], ema_s[i - 1]]):
                continue

            ema_bull = ema_f[i - 1] <= ema_s[i - 1] and ema_f[i] > ema_s[i]
            ema_bear = ema_f[i - 1] >= ema_s[i - 1] and ema_f[i] < ema_s[i]

            vwap_bull = vwap[i] is not None and bar.close > vwap[i]
            vwap_bear = vwap[i] is not None and bar.close < vwap[i]

            if params.signal_type == "ema_cross":
                if ema_bull:
                    direction, reason = "CALL", f"EMA {params.ema_fast}/{params.ema_slow} bullish cross"
                elif ema_bear:
                    direction, reason = "PUT", f"EMA {params.ema_fast}/{params.ema_slow} bearish cross"

            elif params.signal_type == "vwap_cross":
                if vwap[i] is not None and vwap[i - 1] is not None:
                    if bars[i - 1].close <= vwap[i - 1] and bar.close > vwap[i]:
                        direction, reason = "CALL", "Price crossed above VWAP"
                    elif bars[i - 1].close >= vwap[i - 1] and bar.close < vwap[i]:
                        direction, reason = "PUT", "Price crossed below VWAP"

            elif params.signal_type == "ema_vwap":
                if ema_bull and vwap_bull:
                    direction, reason = "CALL", "EMA cross + above VWAP"
                elif ema_bear and vwap_bear:
                    direction, reason = "PUT", "EMA cross + below VWAP"

        # RSI filter: if enabled, block signals that disagree with RSI
        # (confluence handles RSI internally, skip filter for it)
        if direction and params.rsi_period > 0 and rsi[i] is not None:
            if params.signal_type not in ("vwap_rsi", "rsi_reversal", "confluence"):
                if direction == "CALL" and rsi[i] > params.rsi_ob:
                    continue  # don't buy calls when overbought
                if direction == "PUT" and rsi[i] < params.rsi_os:
                    continue  # don't buy puts when oversold

        # Pivot S/R filter: block signals that fight key levels
        if direction and params.pivot_filter_enabled and pivots is not None:
            proximity = params.pivot_proximity_pct / 100.0
            price = bar.close
            if direction == "CALL":
                # Block CALL if price is near resistance (buying into ceiling)
                near_r1 = abs(price - pivots.r1) / pivots.r1 < proximity if pivots.r1 != 0 else False
                near_r2 = abs(price - pivots.r2) / pivots.r2 < proximity if pivots.r2 != 0 else False
                if near_r1 or near_r2:
                    continue
            elif direction == "PUT":
                # Block PUT if price is near support (selling into floor)
                near_s1 = abs(price - pivots.s1) / pivots.s1 < proximity if pivots.s1 != 0 else False
                near_s2 = abs(price - pivots.s2) / pivots.s2 < proximity if pivots.s2 != 0 else False
                if near_s1 or near_s2:
                    continue

        if direction:
            signals.append(Signal(
                timestamp=bar.timestamp,
                direction=direction,
                ticker_price=bar.close,
                reason=reason,
                orb_range=sig_orb_range,
                orb_entry_level=sig_orb_entry,
                confluence_score=sig_confluence_score,
                confluence_max_score=sig_confluence_max,
                rel_vol=sig_rel_vol,
            ))

    # Entry confirmation: require N 1-minute bars to confirm direction
    if params.entry_confirm_minutes > 0 and confirm_bars:
        confirmed: list[Signal] = []
        for sig in signals:
            future = [b for b in confirm_bars if b.timestamp > sig.timestamp]
            if len(future) < params.entry_confirm_minutes:
                continue
            confirm_bar = future[params.entry_confirm_minutes - 1]
            if sig.direction == "CALL" and confirm_bar.close > confirm_bar.open:
                sig.timestamp = confirm_bar.timestamp
                sig.ticker_price = confirm_bar.close
                confirmed.append(sig)
            elif sig.direction == "PUT" and confirm_bar.close < confirm_bar.open:
                sig.timestamp = confirm_bar.timestamp
                sig.ticker_price = confirm_bar.close
                confirmed.append(sig)
        signals = confirmed

    return signals


# ── Trade simulation ──────────────────────────────────────────────


def _minutes_to_close(ts: datetime) -> float:
    close_dt = ts.replace(hour=16, minute=0, second=0, microsecond=0)
    return max((close_dt - ts).total_seconds() / 60.0, 0.0)


def _close_trade(
    trade: SimulatedTrade,
    exit_time: datetime,
    exit_price: float,
    exit_reason: str,
    exit_slippage_percent: float = 0.0,
    exit_detail: str = "",
) -> None:
    if exit_slippage_percent > 0:
        exit_price = max(exit_price * (1 - exit_slippage_percent / 100), 0.01)
    trade.exit_time = exit_time
    trade.exit_price = exit_price
    trade.exit_reason = exit_reason
    trade.exit_detail = exit_detail or None
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


def _simulate_trade(
    trade: SimulatedTrade,
    bars_after: list[BarData],
    vix: float,
    params: BacktestParams,
    atr_at_entry: Optional[float] = None,
) -> None:
    """Walk bars and apply exit rules (same priority as exit_engine.py)."""
    trade.highest_price_seen = trade.entry_price

    # ORB range-based stops (SPY-level)
    use_orb_stops = (
        trade.orb_range is not None
        and trade.orb_entry_level is not None
        and trade.orb_range > 0
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
            approx_opt_atr = atr_stop_offset * params.delta_target
            stop_price = max(trade.entry_price - approx_opt_atr, 0.01)
        else:
            stop_price = trade.entry_price * (1 - params.stop_loss_percent / 100)

    for bar in bars_after:
        mtc = _minutes_to_close(bar.timestamp)
        # Combined B-S call: get both price and delta in one shot
        opt_result = estimate_option_price_and_delta(bar.close, trade.strike, mtc, vix, trade.direction)
        opt_price = max(opt_result.price, 0.01)

        # Intrabar stop check: estimate option price at worst underlying level
        if not use_orb_stops:
            worst_underlying = bar.low if trade.direction == "CALL" else bar.high
            opt_price_worst = max(
                estimate_option_price_at(worst_underlying, trade.strike, mtc, vix, trade.direction),
                0.01,
            )
        else:
            opt_price_worst = opt_price

        # Compute per-bar exit slippage (dynamic spread or flat)
        if params.spread_model_enabled:
            exit_spread = estimate_spread_pct(opt_result.delta, mtc, vix, opt_price, is_0dte=True)
            bar_slippage = exit_spread / 2 * 100  # _close_trade expects a percentage
        else:
            bar_slippage = params.exit_slippage_percent

        if opt_price > trade.highest_price_seen:
            trade.highest_price_seen = opt_price

        elapsed = (bar.timestamp - trade.entry_time).total_seconds() / 60
        gain_pct = (opt_price - trade.entry_price) / trade.entry_price * 100 if trade.entry_price > 0 else 0

        # P1: Force exit
        if bar.timestamp.time() >= params.force_exit_time:
            _close_trade(trade, bar.timestamp, opt_price, "TIME_BASED", bar_slippage,
                         exit_detail=f"Force exit at {bar.timestamp.strftime('%H:%M')} (opt ${opt_price:.2f})")
            return

        # ORB time stop
        if use_orb_stops and bar.timestamp.time() >= params.orb_time_stop:
            _close_trade(trade, bar.timestamp, opt_price, "ORB_TIME_STOP", bar_slippage,
                         exit_detail=f"ORB time stop at {bar.timestamp.strftime('%H:%M')} (opt ${opt_price:.2f})")
            return

        # P2: Max hold
        if elapsed >= params.max_hold_minutes:
            _close_trade(trade, bar.timestamp, opt_price, "MAX_HOLD_TIME", bar_slippage,
                         exit_detail=f"Held {elapsed:.0f}min (max {params.max_hold_minutes}min)")
            return

        # P3: Stop loss (intrabar: check worst-case price within bar)
        if use_orb_stops:
            if trade.direction == "CALL" and bar.low <= spy_stop:
                _close_trade(trade, bar.timestamp, opt_price, "STOP_LOSS", bar_slippage,
                             exit_detail=f"Underlying ${bar.low:.2f} hit ORB stop ${spy_stop:.2f}")
                return
            elif trade.direction == "PUT" and bar.high >= spy_stop:
                _close_trade(trade, bar.timestamp, opt_price, "STOP_LOSS", bar_slippage,
                             exit_detail=f"Underlying ${bar.high:.2f} hit ORB stop ${spy_stop:.2f}")
                return
        else:
            if opt_price_worst <= stop_price:
                fill_price = opt_price_worst  # realistic fill at actual worst price, not ideal stop level
                _close_trade(trade, bar.timestamp, fill_price, "STOP_LOSS", bar_slippage,
                             exit_detail=f"Opt ${opt_price_worst:.2f} <= stop ${stop_price:.2f} (-{params.stop_loss_percent:.0f}%)")
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
                _close_trade(trade, bar.timestamp, opt_price, "PROFIT_TARGET", bar_slippage,
                             exit_detail=f"Underlying ${bar.high:.2f} hit ORB target ${spy_target:.2f}")
                return
            elif trade.direction == "PUT" and bar.low <= spy_target:
                _close_trade(trade, bar.timestamp, opt_price, "PROFIT_TARGET", bar_slippage,
                             exit_detail=f"Underlying ${bar.low:.2f} hit ORB target ${spy_target:.2f}")
                return
        else:
            if gain_pct >= params.profit_target_percent:
                if params.scale_out_enabled and trade.quantity >= 2 and not trade.scaled_out:
                    trade.scaled_out = True
                    trade.scaled_out_quantity = trade.quantity // 2
                    trade.scaled_out_price = opt_price
                elif not trade.scaled_out:
                    _close_trade(trade, bar.timestamp, opt_price, "PROFIT_TARGET", bar_slippage,
                                 exit_detail=f"Gain {gain_pct:.1f}% >= {params.profit_target_percent:.0f}% (opt ${opt_price:.2f})")
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
                _close_trade(trade, bar.timestamp, opt_price, "TRAILING_STOP", bar_slippage,
                             exit_detail=f"Opt ${opt_price:.2f} <= trail ${trail_price:.2f} (peak ${trade.highest_price_seen:.2f}, {trail_pct:.0f}%)")
                return

    # End of day — force close at last bar
    if trade.exit_time is None and bars_after:
        last = bars_after[-1]
        mtc = _minutes_to_close(last.timestamp)
        eod_result = estimate_option_price_and_delta(last.close, trade.strike, mtc, vix, trade.direction)
        last_price = max(eod_result.price, 0.01)
        if params.spread_model_enabled:
            exit_spread = estimate_spread_pct(eod_result.delta, mtc, vix, last_price, is_0dte=True)
            eod_slippage = exit_spread / 2 * 100
        else:
            eod_slippage = params.exit_slippage_percent
        _close_trade(trade, last.timestamp, last_price, "TIME_BASED", eod_slippage,
                     exit_detail=f"EOD close (opt ${last_price:.2f})")


# ── Main entry point ──────────────────────────────────────────────


def run_backtest(
    params: BacktestParams,
    market_data: Optional[MarketDataCache] = None,
) -> BacktestResult:
    logger.info(f"Starting backtest: {params.start_date} to {params.end_date}")

    if market_data is not None:
        bars_by_day = market_data.bars_by_day
        vix_by_day = market_data.vix_by_day
    else:
        if params.data_source == "csv":
            bars_by_day = load_csv_bars(params.start_date, params.end_date, params.bar_interval)
        else:
            bars_by_day = fetch_spy_bars(params.start_date, params.end_date, params.bar_interval)
        vix_by_day = fetch_vix_daily(params.start_date, params.end_date)

    # Fetch 1-minute bars for entry confirmation if needed
    confirm_bars_by_day: Optional[dict] = None
    if params.entry_confirm_minutes > 0:
        if params.data_source == "csv":
            confirm_bars_by_day = load_csv_bars(params.start_date, params.end_date, "1m")
        else:
            confirm_bars_by_day = fetch_spy_bars(params.start_date, params.end_date, "1m")

    result = BacktestResult(params=params)
    default_vix = 20.0
    prev_close: Optional[float] = None
    prev_high: Optional[float] = None
    prev_low: Optional[float] = None

    for trade_date in sorted(bars_by_day.keys()):
        day_bars = bars_by_day[trade_date]
        vix = vix_by_day.get(trade_date, default_vix)

        # VIX regime filter: skip entire day if VIX outside [vix_min, vix_max]
        if vix < params.vix_min or vix > params.vix_max:
            if day_bars:
                prev_close = day_bars[-1].close
                prev_high = max(b.high for b in day_bars)
                prev_low = min(b.low for b in day_bars)
            result.days.append(DailyResult(trade_date=trade_date))
            continue

        day_result = DailyResult(trade_date=trade_date)

        confirm_day = confirm_bars_by_day.get(trade_date) if confirm_bars_by_day else None
        signals = _generate_signals(
            day_bars, params, prev_close=prev_close,
            prev_high=prev_high, prev_low=prev_low,
            confirm_bars=confirm_day,
        )

        # Precompute ATR for the day if enabled
        day_atr: list[Optional[float]] = [None] * len(day_bars)
        if params.atr_period > 0:
            day_atr = _compute_atr(day_bars, params.atr_period)

        daily_trades = 0
        daily_pnl = 0.0
        consecutive_losses = 0
        last_exit_time: Optional[datetime] = None

        # Precompute sorted timestamps for bisect-based entry bar lookup
        bar_timestamps = [b.timestamp for b in day_bars]

        for signal in signals:
            # Limits
            if daily_trades >= params.max_daily_trades:
                continue
            if daily_pnl <= -params.max_daily_loss:
                continue
            if consecutive_losses >= params.max_consecutive_losses:
                continue

            # Cooldown
            if last_exit_time:
                if (signal.timestamp - last_exit_time).total_seconds() / 60 < 5:
                    continue

            mtc = _minutes_to_close(signal.timestamp)
            if mtc < 30:
                continue

            # Resolve delta target (dynamic or static)
            effective_delta = params.delta_target
            if params.dynamic_delta:
                try:
                    from app.services.delta_resolver import DeltaResolver

                    signal_bars = [b for b in day_bars if b.timestamp <= signal.timestamp]
                    if len(signal_bars) >= 21:
                        import pandas as pd

                        df = pd.DataFrame([
                            {"open": b.open, "high": b.high, "low": b.low,
                             "close": b.close, "volume": b.volume}
                            for b in signal_bars
                        ])
                        resolver = DeltaResolver()
                        effective_delta = resolver.resolve_for_backtest(
                            signal_type=params.signal_type,
                            df=df,
                            vix=vix,
                            signal_time=signal.timestamp.time(),
                            atr=day_atr,
                            hold_minutes=params.max_hold_minutes,
                            underlying_price=signal.ticker_price,
                        )
                except Exception:
                    pass  # fall back to params.delta_target

            strike, opt_data = select_strike_for_delta(
                ticker_price=signal.ticker_price,
                target_delta=effective_delta,
                minutes_to_expiry=mtc,
                vix=vix,
                option_type=signal.direction,
            )

            # Entry price: apply limit discount, then add spread friction
            limit_price = opt_data.price * (1 - params.entry_limit_below_percent / 100)
            if params.spread_model_enabled:
                entry_spread = estimate_spread_pct(
                    opt_data.delta, mtc, vix, limit_price, is_0dte=True,
                )
                entry_price = round(max(limit_price * (1 + entry_spread / 2), 0.01), 2)
            else:
                entry_price = round(
                    max(limit_price * (1 + params.entry_slippage_percent / 100), 0.01), 2,
                )

            entry_idx = bisect.bisect_left(bar_timestamps, signal.timestamp)
            if entry_idx >= len(day_bars):
                continue
            bars_after = day_bars[entry_idx + 1:]
            if not bars_after:
                continue

            trade = SimulatedTrade(
                trade_date=trade_date,
                direction=signal.direction,
                strike=strike,
                entry_time=signal.timestamp,
                entry_price=entry_price,
                quantity=params.quantity,
                orb_range=signal.orb_range,
                orb_entry_level=signal.orb_entry_level,
                underlying_price=round(signal.ticker_price, 2),
                expiry_date=trade_date,
                dte=0,
                delta=round(opt_data.delta, 4),
                entry_reason=signal.reason,
            )

            # Get ATR at entry point
            atr_val = day_atr[entry_idx] if entry_idx < len(day_atr) else None
            _simulate_trade(trade, bars_after, vix, params, atr_at_entry=atr_val)

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
            prev_high = max(b.high for b in day_bars)
            prev_low = min(b.low for b in day_bars)
        result.days.append(day_result)

    _compute_summary(result)
    logger.info(
        f"Backtest complete: {result.total_trades} trades, "
        f"PnL=${result.total_pnl:.2f}, WR={result.win_rate:.1f}%"
    )
    return result


def _compute_summary(result: BacktestResult) -> None:
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
