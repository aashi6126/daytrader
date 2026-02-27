"""Parameter optimizer for the backtest engine.

Generates random parameter combinations, runs backtests with cached market data,
ranks results by a configurable target metric.
"""

import logging
import math
import os
import random
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, time as dtime
from typing import Literal, Optional

from app.services.backtest.engine import (
    BacktestParams,
    BacktestResult,
    MarketDataCache,
    run_backtest,
)
from app.services.backtest.market_data import fetch_spy_bars, fetch_vix_daily, load_csv_bars

logger = logging.getLogger(__name__)


# ── Parameter space ───────────────────────────────────────────────

PARAM_SPACE: dict[str, list] = {
    "signal_type": ["ema_cross", "vwap_cross", "ema_vwap", "orb", "vwap_rsi", "bb_squeeze", "rsi_reversal", "confluence", "orb_direction", "vwap_reclaim"],
    "ema_fast": [5, 8, 13, 21],
    "ema_slow": [13, 21, 34, 55],
    "stop_loss_percent": [12, 16, 20, 25],
    "profit_target_percent": [25, 35, 45, 60, 80],
    "trailing_stop_percent": [10, 15, 20, 25],
    "trailing_stop_after_scale_out_percent": [5, 10, 15],
    "delta_target": [0.3, 0.4, 0.5],
    "max_hold_minutes": [15, 30, 45, 60],
    "rsi_period": [0, 9, 14],
    "atr_period": [0, 14],
    "atr_stop_mult": [1.5, 2.0, 2.5],
    "orb_minutes": [5, 10, 15, 30],
    "min_confluence": [4, 5, 6],
    "vol_threshold": [1.0, 1.5, 2.0],
    # ORB direction filter params
    "orb_body_min_pct": [0.0, 0.3, 0.5, 0.6, 0.7],
    "orb_vwap_filter": [True, False],
    "orb_gap_fade_filter": [True, False],
    "orb_stop_mult": [0.5, 0.75, 1.0, 1.25],
    "orb_target_mult": [0.75, 1.0, 1.5, 2.0],
    # VIX regime filter
    "vix_min": [0, 12, 14, 16, 18, 20],
    "vix_max": [22, 25, 30, 35, 50, 100],
    # Bollinger Bands & MACD
    "bb_period": [10, 15, 20, 25],
    "bb_std_mult": [1.5, 2.0, 2.5, 3.0],
    "macd_fast": [8, 12, 16],
    "macd_slow": [21, 26, 30],
    "macd_signal_period": [7, 9, 12],
    # Entry confirmation (minutes of 1m bars, 0=immediate)
    "entry_confirm_minutes": [0, 1, 2, 3],
    # Spread model
    "spread_model_enabled": [True],
    # Trade limits
    "max_daily_trades": [3, 5, 10],
    "max_daily_loss": [300, 500, 1000, 1500],
    "max_consecutive_losses": [2, 3, 5],
    # Trading windows (minutes after 9:30)
    "morning_start_min": [15, 20, 30, 45],       # 9:45, 9:50, 10:00, 10:15
    "morning_end_min": [90, 105, 120, 150],       # 11:00, 11:05, 11:30, 12:00
    "afternoon_start_min": [180, 195, 210],       # 12:30, 12:45, 13:00
    "afternoon_end_min": [300, 320, 330],         # 14:30, 14:50, 15:00
    # Pivot point S/R
    "pivot_enabled": [True, False],
    "pivot_proximity_pct": [0.2, 0.3, 0.4, 0.5],
    "pivot_filter_enabled": [True, False],
}

# Minimum thresholds for a strategy to be considered viable
MIN_TRADES = 30
MIN_WIN_RATE = 35.0
MIN_PROFIT_FACTOR = 0.8
MAX_HOLD_EXIT_PCT = 40.0  # max % of exits that can be MAX_HOLD_TIME

TargetMetric = Literal[
    "total_pnl",
    "profit_factor",
    "win_rate",
    "composite",
    "risk_adjusted",
    "sharpe",
    "pro",
]


# ── Data classes ──────────────────────────────────────────────────


@dataclass
class OptimizationConfig:
    start_date: date
    end_date: date
    bar_interval: str = "5m"
    num_iterations: int = 200
    target_metric: str = "composite"
    top_n: int = 10
    afternoon_enabled: bool = True
    scale_out_enabled: bool = True
    quantity: int = 2
    data_source: str = "yfinance"  # "csv" or "yfinance"
    walk_forward: bool = True       # enable train/test split validation
    train_pct: float = 0.7          # fraction of days for training (0.7 = 70%)


@dataclass
class OptimizationResultEntry:
    rank: int
    params: dict
    total_pnl: float
    total_trades: int
    win_rate: float
    profit_factor: float
    max_drawdown: float
    avg_hold_minutes: float
    score: float
    exit_reasons: dict = field(default_factory=dict)
    # Out-of-sample (walk-forward) metrics
    oos_total_pnl: Optional[float] = None
    oos_total_trades: Optional[int] = None
    oos_win_rate: Optional[float] = None
    oos_profit_factor: Optional[float] = None
    oos_score: Optional[float] = None


@dataclass
class OptimizationResult:
    total_combinations_tested: int
    elapsed_seconds: float
    results: list[OptimizationResultEntry]
    train_start: Optional[date] = None
    train_end: Optional[date] = None
    test_start: Optional[date] = None
    test_end: Optional[date] = None


# ── Combination generation ────────────────────────────────────────


def _generate_combinations(num_iterations: int) -> list[dict]:
    combos: list[dict] = []
    max_attempts = num_iterations * 15

    # Strategies that don't use EMA crossovers (but confluence uses EMA as trend indicator)
    non_ema_strategies = {"orb", "bb_squeeze", "rsi_reversal", "orb_direction", "vwap_reclaim"}

    for _ in range(max_attempts):
        if len(combos) >= num_iterations:
            break

        combo = {k: random.choice(v) for k, v in PARAM_SPACE.items()}

        # ema_fast must be < ema_slow (for EMA-based strategies and confluence)
        if combo["signal_type"] not in non_ema_strategies:
            if combo["ema_fast"] >= combo["ema_slow"]:
                continue

        # profit target should exceed stop loss (for non-ORB strategies)
        if combo["signal_type"] != "orb_direction":
            if combo["profit_target_percent"] <= combo["stop_loss_percent"]:
                continue

        # RSI strategies need rsi_period > 0
        if combo["signal_type"] in ("vwap_rsi", "rsi_reversal") and combo["rsi_period"] == 0:
            combo["rsi_period"] = random.choice([9, 14])

        # Confluence always uses RSI internally (default 9 if not set)
        if combo["signal_type"] == "confluence" and combo["rsi_period"] == 0:
            combo["rsi_period"] = random.choice([9, 14])

        # ATR stops: if atr_period is 0, atr_stop_mult doesn't matter
        if combo["atr_period"] == 0:
            combo["atr_stop_mult"] = 2.0  # doesn't matter, normalize

        # Non-confluence strategies don't use confluence params
        if combo["signal_type"] != "confluence":
            combo["min_confluence"] = 5
            combo["vol_threshold"] = 1.5

        # Non-ORB-direction strategies: normalize ORB direction params
        if combo["signal_type"] != "orb_direction":
            combo["orb_body_min_pct"] = 0.0
            combo["orb_vwap_filter"] = False
            combo["orb_gap_fade_filter"] = False
            combo["orb_stop_mult"] = 1.0
            combo["orb_target_mult"] = 1.5

        # MACD: fast must be < slow
        if combo["macd_fast"] >= combo["macd_slow"]:
            continue

        # Normalize BB/MACD params for strategies that don't use them
        if combo["signal_type"] not in ("bb_squeeze", "confluence"):
            combo["bb_period"] = 20
            combo["bb_std_mult"] = 2.0
        if combo["signal_type"] != "confluence":
            combo["macd_fast"] = 12
            combo["macd_slow"] = 26
            combo["macd_signal_period"] = 9

        # VIX range: vix_min must be < vix_max
        if combo["vix_min"] >= combo["vix_max"]:
            continue

        # Trading windows: start must be < end
        if combo["morning_start_min"] >= combo["morning_end_min"]:
            continue
        if combo["afternoon_start_min"] >= combo["afternoon_end_min"]:
            continue

        # Normalize pivot params when disabled
        if not combo.get("pivot_enabled", False):
            combo["pivot_proximity_pct"] = 0.3
            combo["pivot_filter_enabled"] = False

        combos.append(combo)

    return combos


# ── Helpers ───────────────────────────────────────────────────────


def _minutes_to_time(m: int) -> dtime:
    """Convert minutes-after-9:30 to a time object."""
    h = 9 + (30 + m) // 60
    mn = (30 + m) % 60
    return dtime(h, mn)


# ── Scoring ───────────────────────────────────────────────────────


def _compute_score(result: BacktestResult, metric: str) -> float:
    if result.total_trades == 0:
        return float("-inf")

    # ── Pre-score filters: reject unviable strategies early ──
    if result.total_trades < MIN_TRADES:
        return float("-inf")
    if result.win_rate < MIN_WIN_RATE:
        return float("-inf")
    if result.profit_factor < MIN_PROFIT_FACTOR:
        return float("-inf")

    # Reject strategies where too many trades hit max hold time (untested exits)
    exit_reasons = getattr(result, "exit_reasons", {}) or {}
    max_hold_exits = exit_reasons.get("MAX_HOLD_TIME", 0)
    if result.total_trades > 0 and max_hold_exits / result.total_trades * 100 > MAX_HOLD_EXIT_PCT:
        return float("-inf")

    if metric == "total_pnl":
        return result.total_pnl
    elif metric == "profit_factor":
        return result.profit_factor
    elif metric == "win_rate":
        return result.win_rate
    elif metric == "composite":
        if result.profit_factor <= 0:
            return float("-inf")
        return result.profit_factor * math.sqrt(result.total_trades)
    elif metric == "risk_adjusted":
        if result.max_drawdown <= 0:
            return result.total_pnl
        return result.total_pnl / result.max_drawdown
    elif metric == "sharpe":
        if result.max_drawdown <= 0:
            return result.total_pnl * math.sqrt(result.total_trades)
        return (result.total_pnl / result.max_drawdown) * math.sqrt(result.total_trades)
    elif metric == "pro":
        # Pro scoring: profit factor, trade count, exit quality, drawdown recovery
        if result.profit_factor <= 0 or result.total_pnl <= 0:
            return float("-inf")

        # Profit factor component (sqrt to dampen outliers)
        pf_component = math.sqrt(result.profit_factor)

        # Trade count component (more trades = more statistical confidence)
        trade_component = math.sqrt(result.total_trades)

        # Exit quality: penalize MAX_HOLD_TIME exits (strategy didn't reach a real exit)
        max_hold_pct = max_hold_exits / result.total_trades if result.total_trades > 0 else 0
        exit_quality = 1.0 - max_hold_pct * 0.5

        # Recovery factor: how well PnL compensates for drawdown
        if result.max_drawdown > 0:
            recovery = min(2.0, result.total_pnl / result.max_drawdown)
        else:
            recovery = 2.0

        return pf_component * trade_component * exit_quality * recovery

    return float("-inf")


# ── Parallel worker ───────────────────────────────────────────────

_worker_market_data: Optional[MarketDataCache] = None
_worker_config_dict: Optional[dict] = None


def _init_worker(bars_by_day, vix_by_day, config_dict):
    global _worker_market_data, _worker_config_dict
    _worker_market_data = MarketDataCache(bars_by_day=bars_by_day, vix_by_day=vix_by_day)
    _worker_config_dict = config_dict


def _run_single_combo(combo: dict) -> tuple:
    """Worker: run one backtest, return (score, combo, summary_dict)."""
    cfg = _worker_config_dict
    params = BacktestParams(
        start_date=cfg["start_date"],
        end_date=cfg["end_date"],
        bar_interval=cfg["bar_interval"],
        data_source=cfg["data_source"],
        signal_type=combo["signal_type"],
        ema_fast=combo["ema_fast"],
        ema_slow=combo["ema_slow"],
        stop_loss_percent=combo["stop_loss_percent"],
        profit_target_percent=combo["profit_target_percent"],
        trailing_stop_percent=combo["trailing_stop_percent"],
        trailing_stop_after_scale_out_percent=combo.get("trailing_stop_after_scale_out_percent", 10.0),
        delta_target=combo["delta_target"],
        max_hold_minutes=combo["max_hold_minutes"],
        rsi_period=combo.get("rsi_period", 0),
        atr_period=combo.get("atr_period", 0),
        atr_stop_mult=combo.get("atr_stop_mult", 2.0),
        orb_minutes=combo.get("orb_minutes", 15),
        min_confluence=combo.get("min_confluence", 5),
        vol_threshold=combo.get("vol_threshold", 1.5),
        bb_period=combo.get("bb_period", 20),
        bb_std_mult=combo.get("bb_std_mult", 2.0),
        macd_fast=combo.get("macd_fast", 12),
        macd_slow=combo.get("macd_slow", 26),
        macd_signal_period=combo.get("macd_signal_period", 9),
        orb_body_min_pct=combo.get("orb_body_min_pct", 0.0),
        orb_vwap_filter=combo.get("orb_vwap_filter", False),
        orb_gap_fade_filter=combo.get("orb_gap_fade_filter", False),
        orb_stop_mult=combo.get("orb_stop_mult", 1.0),
        orb_target_mult=combo.get("orb_target_mult", 1.5),
        vix_min=combo.get("vix_min", 0.0),
        vix_max=combo.get("vix_max", 100.0),
        spread_model_enabled=combo.get("spread_model_enabled", True),
        entry_confirm_minutes=combo.get("entry_confirm_minutes", 0),
        max_daily_trades=combo.get("max_daily_trades", 10),
        max_daily_loss=combo.get("max_daily_loss", 2000.0),
        max_consecutive_losses=combo.get("max_consecutive_losses", 3),
        morning_window_start=_minutes_to_time(combo.get("morning_start_min", 15)),
        morning_window_end=_minutes_to_time(combo.get("morning_end_min", 105)),
        afternoon_window_start=_minutes_to_time(combo.get("afternoon_start_min", 195)),
        afternoon_window_end=_minutes_to_time(combo.get("afternoon_end_min", 320)),
        afternoon_enabled=cfg["afternoon_enabled"],
        scale_out_enabled=cfg["scale_out_enabled"],
        quantity=cfg["quantity"],
        pivot_enabled=combo.get("pivot_enabled", False),
        pivot_proximity_pct=combo.get("pivot_proximity_pct", 0.3),
        pivot_filter_enabled=combo.get("pivot_filter_enabled", False),
    )

    result = run_backtest(params, market_data=_worker_market_data)
    score = _compute_score(result, cfg["target_metric"])
    summary = {
        "total_pnl": result.total_pnl,
        "total_trades": result.total_trades,
        "win_rate": result.win_rate,
        "profit_factor": result.profit_factor,
        "max_drawdown": result.max_drawdown,
        "avg_hold_minutes": result.avg_hold_minutes,
        "exit_reasons": result.exit_reasons,
    }
    return (score, combo, summary)


# ── Main optimizer ────────────────────────────────────────────────


def _split_data(bars_by_day: dict, vix_by_day: dict, train_pct: float):
    """Split market data into train and test sets by date."""
    sorted_dates = sorted(bars_by_day.keys())
    if not sorted_dates:
        return bars_by_day, vix_by_day, {}, {}, None, None, None, None

    split_idx = max(1, int(len(sorted_dates) * train_pct))
    train_dates = sorted_dates[:split_idx]
    test_dates = sorted_dates[split_idx:]

    train_bars = {d: bars_by_day[d] for d in train_dates}
    test_bars = {d: bars_by_day[d] for d in test_dates}
    train_vix = {d: vix_by_day[d] for d in train_dates if d in vix_by_day}
    test_vix = {d: vix_by_day[d] for d in test_dates if d in vix_by_day}

    return (
        train_bars, train_vix, test_bars, test_vix,
        train_dates[0], train_dates[-1],
        test_dates[0] if test_dates else None,
        test_dates[-1] if test_dates else None,
    )


def _run_oos_backtest(combo: dict, config_dict: dict, test_bars: dict, test_vix: dict) -> dict:
    """Run a single backtest on out-of-sample data for walk-forward validation."""
    test_dates = sorted(test_bars.keys())
    if not test_dates:
        return {"total_pnl": 0, "total_trades": 0, "win_rate": 0, "profit_factor": 0, "score": 0}

    params = BacktestParams(
        start_date=test_dates[0],
        end_date=test_dates[-1],
        bar_interval=config_dict["bar_interval"],
        data_source=config_dict["data_source"],
        signal_type=combo["signal_type"],
        ema_fast=combo["ema_fast"],
        ema_slow=combo["ema_slow"],
        stop_loss_percent=combo["stop_loss_percent"],
        profit_target_percent=combo["profit_target_percent"],
        trailing_stop_percent=combo["trailing_stop_percent"],
        trailing_stop_after_scale_out_percent=combo.get("trailing_stop_after_scale_out_percent", 10.0),
        delta_target=combo["delta_target"],
        max_hold_minutes=combo["max_hold_minutes"],
        rsi_period=combo.get("rsi_period", 0),
        atr_period=combo.get("atr_period", 0),
        atr_stop_mult=combo.get("atr_stop_mult", 2.0),
        orb_minutes=combo.get("orb_minutes", 15),
        min_confluence=combo.get("min_confluence", 5),
        vol_threshold=combo.get("vol_threshold", 1.5),
        bb_period=combo.get("bb_period", 20),
        bb_std_mult=combo.get("bb_std_mult", 2.0),
        macd_fast=combo.get("macd_fast", 12),
        macd_slow=combo.get("macd_slow", 26),
        macd_signal_period=combo.get("macd_signal_period", 9),
        orb_body_min_pct=combo.get("orb_body_min_pct", 0.0),
        orb_vwap_filter=combo.get("orb_vwap_filter", False),
        orb_gap_fade_filter=combo.get("orb_gap_fade_filter", False),
        orb_stop_mult=combo.get("orb_stop_mult", 1.0),
        orb_target_mult=combo.get("orb_target_mult", 1.5),
        vix_min=combo.get("vix_min", 0.0),
        vix_max=combo.get("vix_max", 100.0),
        spread_model_enabled=combo.get("spread_model_enabled", True),
        entry_confirm_minutes=combo.get("entry_confirm_minutes", 0),
        max_daily_trades=combo.get("max_daily_trades", 10),
        max_daily_loss=combo.get("max_daily_loss", 2000.0),
        max_consecutive_losses=combo.get("max_consecutive_losses", 3),
        morning_window_start=_minutes_to_time(combo.get("morning_start_min", 15)),
        morning_window_end=_minutes_to_time(combo.get("morning_end_min", 105)),
        afternoon_window_start=_minutes_to_time(combo.get("afternoon_start_min", 195)),
        afternoon_window_end=_minutes_to_time(combo.get("afternoon_end_min", 320)),
        afternoon_enabled=config_dict["afternoon_enabled"],
        scale_out_enabled=config_dict["scale_out_enabled"],
        quantity=config_dict["quantity"],
        pivot_enabled=combo.get("pivot_enabled", False),
        pivot_proximity_pct=combo.get("pivot_proximity_pct", 0.3),
        pivot_filter_enabled=combo.get("pivot_filter_enabled", False),
    )

    cache = MarketDataCache(bars_by_day=test_bars, vix_by_day=test_vix)
    result = run_backtest(params, market_data=cache)
    score = _compute_score(result, config_dict["target_metric"])

    return {
        "total_pnl": result.total_pnl,
        "total_trades": result.total_trades,
        "win_rate": result.win_rate,
        "profit_factor": result.profit_factor,
        "score": round(score, 4) if score != float("-inf") else 0,
    }


def run_optimization(config: OptimizationConfig) -> OptimizationResult:
    t0 = time.time()

    # Fetch all data
    logger.info(f"Optimizer: fetching data {config.start_date} to {config.end_date} ({config.bar_interval}) source={config.data_source}")
    if config.data_source == "csv":
        all_bars = load_csv_bars(config.start_date, config.end_date, config.bar_interval)
    else:
        all_bars = fetch_spy_bars(config.start_date, config.end_date, config.bar_interval)
    all_vix = fetch_vix_daily(config.start_date, config.end_date)

    # Walk-forward: split into train/test
    train_start = train_end = test_start = test_end = None
    test_bars: dict = {}
    test_vix: dict = {}

    if config.walk_forward and len(all_bars) >= 10:
        (
            train_bars, train_vix, test_bars, test_vix,
            train_start, train_end, test_start, test_end,
        ) = _split_data(all_bars, all_vix, config.train_pct)
        logger.info(
            f"Optimizer: walk-forward split — train {len(train_bars)} days "
            f"({train_start} to {train_end}), test {len(test_bars)} days "
            f"({test_start} to {test_end})"
        )
    else:
        train_bars = all_bars
        train_vix = all_vix

    logger.info(f"Optimizer: {len(train_bars)} train days, {sum(len(v) for v in train_bars.values())} bars loaded")

    # Generate combos
    combos = _generate_combinations(config.num_iterations)
    logger.info(f"Optimizer: testing {len(combos)} parameter combinations")

    config_dict = {
        "start_date": config.start_date,
        "end_date": config.end_date,
        "bar_interval": config.bar_interval,
        "data_source": config.data_source,
        "afternoon_enabled": config.afternoon_enabled,
        "scale_out_enabled": config.scale_out_enabled,
        "quantity": config.quantity,
        "target_metric": config.target_metric,
    }

    # Run backtests in parallel on TRAIN data
    workers = min(os.cpu_count() or 4, len(combos))
    scored: list[tuple[float, dict, dict]] = []

    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_worker,
        initargs=(train_bars, train_vix, config_dict),
    ) as pool:
        futures = {pool.submit(_run_single_combo, combo): combo for combo in combos}
        for i, future in enumerate(as_completed(futures)):
            result = future.result()
            scored.append(result)
            if (i + 1) % 50 == 0:
                logger.info(f"Optimizer: completed {i + 1}/{len(combos)}")

    # Rank by in-sample score and take top N
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[: config.top_n]

    entries = []
    for rank, (score, combo, summary) in enumerate(top, start=1):
        entry = OptimizationResultEntry(
            rank=rank,
            params=combo,
            total_pnl=summary["total_pnl"],
            total_trades=summary["total_trades"],
            win_rate=summary["win_rate"],
            profit_factor=summary["profit_factor"],
            max_drawdown=summary["max_drawdown"],
            avg_hold_minutes=summary["avg_hold_minutes"],
            score=round(score, 4) if score != float("-inf") else 0,
            exit_reasons=summary["exit_reasons"],
        )

        # Walk-forward: validate top results on TEST data
        if config.walk_forward and test_bars:
            oos = _run_oos_backtest(combo, config_dict, test_bars, test_vix)
            entry.oos_total_pnl = oos["total_pnl"]
            entry.oos_total_trades = oos["total_trades"]
            entry.oos_win_rate = oos["win_rate"]
            entry.oos_profit_factor = oos["profit_factor"]
            entry.oos_score = oos["score"]

        entries.append(entry)

    elapsed = round(time.time() - t0, 1)
    logger.info(f"Optimizer: {len(combos)} combos in {elapsed}s ({workers} workers). Best score={entries[0].score if entries else 0}")
    if config.walk_forward and entries and entries[0].oos_score is not None:
        logger.info(f"Optimizer: best OOS score={entries[0].oos_score}, OOS PnL=${entries[0].oos_total_pnl}")

    return OptimizationResult(
        total_combinations_tested=len(combos),
        elapsed_seconds=elapsed,
        results=entries,
        train_start=train_start,
        train_end=train_end,
        test_start=test_start,
        test_end=test_end,
    )
