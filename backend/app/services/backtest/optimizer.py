"""Parameter optimizer for the backtest engine.

Generates random parameter combinations, runs backtests with cached market data,
ranks results by a configurable target metric.
"""

import logging
import math
import random
import time
from dataclasses import dataclass, field
from datetime import date
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
    "stop_loss_percent": [10, 16, 20, 25, 30],
    "profit_target_percent": [20, 30, 40, 50, 60],
    "trailing_stop_percent": [10, 15, 20, 25],
    "trailing_stop_after_scale_out_percent": [5, 10, 15, 20],
    "delta_target": [0.3, 0.4, 0.5],
    "max_hold_minutes": [30, 60, 90, 120],
    "rsi_period": [0, 9, 14],             # 0 = disabled (pure signal), 9/14 = filter or driver
    "atr_period": [0, 14],                # 0 = fixed % stops, 14 = ATR-based stops
    "atr_stop_mult": [1.5, 2.0, 2.5],
    "orb_minutes": [5, 10, 15, 30],
    "min_confluence": [4, 5, 6],           # confluence strategy: min indicators that must agree
    "vol_threshold": [1.0, 1.5, 2.0],     # confluence strategy: relative volume threshold
    # ORB direction filter params
    "orb_body_min_pct": [0.0, 0.3, 0.5, 0.6, 0.7],
    "orb_vwap_filter": [True, False],
    "orb_gap_fade_filter": [True, False],
    "orb_stop_mult": [0.5, 0.75, 1.0, 1.25],
    "orb_target_mult": [0.75, 1.0, 1.5, 2.0],
}

TargetMetric = Literal[
    "total_pnl",
    "profit_factor",
    "win_rate",
    "composite",
    "risk_adjusted",
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


@dataclass
class OptimizationResult:
    total_combinations_tested: int
    elapsed_seconds: float
    results: list[OptimizationResultEntry]


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

        combos.append(combo)

    return combos


# ── Scoring ───────────────────────────────────────────────────────


def _compute_score(result: BacktestResult, metric: str) -> float:
    if result.total_trades == 0:
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

    return float("-inf")


# ── Main optimizer ────────────────────────────────────────────────


def run_optimization(config: OptimizationConfig) -> OptimizationResult:
    t0 = time.time()

    # Fetch data once
    logger.info(f"Optimizer: fetching data {config.start_date} to {config.end_date} ({config.bar_interval}) source={config.data_source}")
    if config.data_source == "csv":
        bars_by_day = load_csv_bars(config.start_date, config.end_date, config.bar_interval)
    else:
        bars_by_day = fetch_spy_bars(config.start_date, config.end_date, config.bar_interval)
    vix_by_day = fetch_vix_daily(config.start_date, config.end_date)
    market_data = MarketDataCache(bars_by_day=bars_by_day, vix_by_day=vix_by_day)

    logger.info(f"Optimizer: {len(bars_by_day)} days, {sum(len(v) for v in bars_by_day.values())} bars loaded")

    # Generate combos
    combos = _generate_combinations(config.num_iterations)
    logger.info(f"Optimizer: testing {len(combos)} parameter combinations")

    # Run backtests
    scored: list[tuple[float, dict, BacktestResult]] = []

    for i, combo in enumerate(combos):
        params = BacktestParams(
            start_date=config.start_date,
            end_date=config.end_date,
            bar_interval=config.bar_interval,
            data_source=config.data_source,
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
            orb_body_min_pct=combo.get("orb_body_min_pct", 0.0),
            orb_vwap_filter=combo.get("orb_vwap_filter", False),
            orb_gap_fade_filter=combo.get("orb_gap_fade_filter", False),
            orb_stop_mult=combo.get("orb_stop_mult", 1.0),
            orb_target_mult=combo.get("orb_target_mult", 1.5),
            afternoon_enabled=config.afternoon_enabled,
            scale_out_enabled=config.scale_out_enabled,
            quantity=config.quantity,
        )

        result = run_backtest(params, market_data=market_data)
        score = _compute_score(result, config.target_metric)
        scored.append((score, combo, result))

        if (i + 1) % 50 == 0:
            logger.info(f"Optimizer: completed {i + 1}/{len(combos)}")

    # Rank and return top N
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[: config.top_n]

    entries = []
    for rank, (score, combo, result) in enumerate(top, start=1):
        entries.append(
            OptimizationResultEntry(
                rank=rank,
                params=combo,
                total_pnl=result.total_pnl,
                total_trades=result.total_trades,
                win_rate=result.win_rate,
                profit_factor=result.profit_factor,
                max_drawdown=result.max_drawdown,
                avg_hold_minutes=result.avg_hold_minutes,
                score=round(score, 4) if score != float("-inf") else 0,
                exit_reasons=result.exit_reasons,
            )
        )

    elapsed = round(time.time() - t0, 1)
    logger.info(f"Optimizer: {len(combos)} combos in {elapsed}s. Best score={entries[0].score if entries else 0}")

    return OptimizationResult(
        total_combinations_tested=len(combos),
        elapsed_seconds=elapsed,
        results=entries,
    )
