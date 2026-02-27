"""
Multi-Ticker Day Trading Options Optimizer
=============================================
Runs options-level backtest optimization across all tickers and timeframes.
Uses Black-Scholes pricing with 2 contracts at 0.40 delta target.

USAGE:
  # Full run (all 14 tickers, all 5 timeframes, 200 iterations each)
  python scripts/multi_ticker_optimizer.py

  # Quick test
  python scripts/multi_ticker_optimizer.py --tickers NVDA,TSLA --timeframes 5m --iterations 50

  # Custom settings
  python scripts/multi_ticker_optimizer.py --tickers all --timeframes all --iterations 300 \
      --metric risk_adjusted --top-n 30 --output results.json
"""

import argparse
import json
import logging
import math
import os
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from datetime import date, time as dtime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stock_backtest_engine import (
    StockBacktestParams,
    StockBacktestResult,
    _compute_rolling_vol,
    _0DTE_TICKERS,
    load_ticker_csv_bars,
    load_vix_data,
    run_stock_backtest,
)
from app.services.backtest.market_data import BarData

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

ALL_TICKERS = ["SPY", "NVDA", "TSLA", "AMZN", "AMD", "AAPL", "PLTR", "MSFT", "GOOGL", "QQQ", "GLD"]
ALL_TIMEFRAMES = ["1m", "5m", "10m", "15m", "30m"]

# ── Parameter space (options-level) ──────────────────────────────

OPTION_PARAM_SPACE: dict[str, list] = {
    "signal_type": [
        "ema_cross", "vwap_cross", "ema_vwap", "orb", "vwap_rsi",
        "bb_squeeze", "rsi_reversal", "confluence", "orb_direction", "vwap_reclaim",
    ],
    "delta_target": [0.30, 0.35, 0.40, 0.45, 0.50],
    "ema_fast": [5, 8, 13, 21],
    "ema_slow": [13, 21, 34, 55],
    "stop_loss_percent": [12.0, 16.0, 20.0, 25.0],
    "profit_target_percent": [25.0, 35.0, 45.0, 60.0, 80.0],
    "trailing_stop_percent": [10.0, 15.0, 20.0, 25.0],
    "max_hold_minutes": [15, 30, 45, 60],
    "rsi_period": [0, 9, 14],
    "atr_period": [0, 14],
    "atr_stop_mult": [1.5, 2.0, 2.5],
    "orb_minutes": [5, 10, 15, 30],
    "min_confluence": [4, 5, 6],
    "vol_threshold": [1.0, 1.5, 2.0],
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
    # Spread model (replaces flat slippage with dynamic delta/time/VIX-based spread)
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

NON_EMA_STRATEGIES = {"orb", "bb_squeeze", "rsi_reversal", "orb_direction", "vwap_reclaim"}


# ── Helpers ───────────────────────────────────────────────────────


def _minutes_to_time(m: int) -> dtime:
    """Convert minutes-after-9:30 to a time object."""
    h = 9 + (30 + m) // 60
    mn = (30 + m) % 60
    return dtime(h, mn)


# ── Combination generation ────────────────────────────────────────


def generate_combinations(num_iterations: int) -> list[dict]:
    combos: list[dict] = []
    max_attempts = num_iterations * 15

    for _ in range(max_attempts):
        if len(combos) >= num_iterations:
            break

        combo = {k: random.choice(v) for k, v in OPTION_PARAM_SPACE.items()}

        # ema_fast < ema_slow for EMA-based strategies
        if combo["signal_type"] not in NON_EMA_STRATEGIES:
            if combo["ema_fast"] >= combo["ema_slow"]:
                continue

        # profit target > stop loss for non-ORB
        if combo["signal_type"] != "orb_direction":
            if combo["profit_target_percent"] <= combo["stop_loss_percent"]:
                continue

        # RSI strategies need rsi_period > 0
        if combo["signal_type"] in ("vwap_rsi", "rsi_reversal") and combo["rsi_period"] == 0:
            combo["rsi_period"] = random.choice([9, 14])

        # Confluence uses RSI internally
        if combo["signal_type"] == "confluence" and combo["rsi_period"] == 0:
            combo["rsi_period"] = random.choice([9, 14])

        # Normalize irrelevant params
        if combo["atr_period"] == 0:
            combo["atr_stop_mult"] = 2.0

        if combo["signal_type"] != "confluence":
            combo["min_confluence"] = 5
            combo["vol_threshold"] = 1.5

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


# ── Scoring ───────────────────────────────────────────────────────


def compute_score(result: StockBacktestResult, metric: str) -> float:
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


# ── Optimization loop ─────────────────────────────────────────────


def _build_params(
    combo: dict,
    ticker: str,
    timeframe: str,
    start_date: date,
    end_date: date,
    quantity: int,
) -> StockBacktestParams:
    """Build StockBacktestParams from an optimizer combo dict."""
    return StockBacktestParams(
        start_date=start_date,
        end_date=end_date,
        ticker=ticker,
        bar_interval=timeframe,
        quantity=quantity,
        delta_target=combo.get("delta_target", 0.35),
        signal_type=combo["signal_type"],
        ema_fast=combo["ema_fast"],
        ema_slow=combo["ema_slow"],
        stop_loss_percent=combo["stop_loss_percent"],
        profit_target_percent=combo["profit_target_percent"],
        trailing_stop_percent=combo["trailing_stop_percent"],
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
        pivot_enabled=combo.get("pivot_enabled", False),
        pivot_proximity_pct=combo.get("pivot_proximity_pct", 0.3),
        pivot_filter_enabled=combo.get("pivot_filter_enabled", False),
    )


def monte_carlo_confidence(
    trade_pnls: list[float],
    n_simulations: int = 1000,
) -> dict:
    """Bootstrap confidence analysis on trade PnLs.

    Samples len(trade_pnls) trades with replacement n_simulations times.
    Returns:
      - win_pct: % of simulations where total PnL > 0
      - median_pnl: median total PnL across simulations
      - p5_pnl / p95_pnl: 5th/95th percentile bounds
    """
    if not trade_pnls or len(trade_pnls) < 5:
        return {"win_pct": 0.0, "median_pnl": 0.0, "p5_pnl": 0.0, "p95_pnl": 0.0}

    n = len(trade_pnls)
    sim_pnls: list[float] = []

    for _ in range(n_simulations):
        sample = random.choices(trade_pnls, k=n)
        sim_pnls.append(sum(sample))

    sim_pnls.sort()
    wins = sum(1 for p in sim_pnls if p > 0)
    p5_idx = max(0, int(n_simulations * 0.05) - 1)
    p50_idx = n_simulations // 2
    p95_idx = min(n_simulations - 1, int(n_simulations * 0.95))

    return {
        "win_pct": round(wins / n_simulations * 100, 1),
        "median_pnl": round(sim_pnls[p50_idx], 2),
        "p5_pnl": round(sim_pnls[p5_idx], 2),
        "p95_pnl": round(sim_pnls[p95_idx], 2),
    }


def optimize_ticker_timeframe(
    ticker: str,
    timeframe: str,
    bars_by_day: dict[date, list[BarData]],
    iterations: int,
    metric: str,
    quantity: int,
    top_n: int = 10,
    vix_by_day: dict[date, float] | None = None,
    walk_forward: bool = True,
    train_pct: float = 0.7,
) -> list[dict]:
    """Run optimization for a single ticker/timeframe combo.

    With walk_forward=True (default), splits data into 70/30 train/test:
    - Optimizes params on train set
    - Validates top results on test set (out-of-sample)
    - Runs Monte Carlo bootstrap on OOS trades
    - Sorts final results by OOS score (not in-sample)

    Returns top N results with both IS and OOS metrics.
    """

    if not bars_by_day:
        return []

    dates = sorted(bars_by_day.keys())
    start_date = dates[0]
    end_date = dates[-1]

    # Load VIX if not provided
    if vix_by_day is None:
        vix_by_day = load_vix_data(start_date, end_date)

    # ── Walk-forward: split into train/test ──────────────────────
    train_bars = bars_by_day
    test_bars: dict[date, list[BarData]] = {}
    has_oos = False

    if walk_forward and len(dates) >= 30:
        split_idx = max(20, int(len(dates) * train_pct))
        if len(dates) - split_idx >= 10:
            train_dates = dates[:split_idx]
            test_dates = dates[split_idx:]
            train_bars = {d: bars_by_day[d] for d in train_dates}
            test_bars = {d: bars_by_day[d] for d in test_dates}
            has_oos = True

    train_dates_sorted = sorted(train_bars.keys())
    train_start = train_dates_sorted[0]
    train_end = train_dates_sorted[-1]

    # ── Precompute rolling vol once (avoids redundant CSV loads) ──
    is_0dte = ticker in _0DTE_TICKERS
    precomputed_vol = None
    if not is_0dte:
        precomputed_vol = _compute_rolling_vol(train_bars)

    # ── Optimize on train set ────────────────────────────────────
    combos = generate_combinations(iterations)
    scored: list[tuple[float, dict, StockBacktestResult]] = []

    for combo in combos:
        params = _build_params(combo, ticker, timeframe, train_start, train_end, quantity)
        result = run_stock_backtest(params, bars_by_day=train_bars, vix_by_day=vix_by_day, rolling_vol=precomputed_vol)
        score = compute_score(result, metric)
        scored.append((score, combo, result))

    scored.sort(key=lambda x: x[0], reverse=True)
    # Take more candidates for OOS filtering (2x top_n)
    candidates = scored[:top_n * 2] if has_oos else scored[:top_n]

    # Precompute OOS rolling vol once (outside candidate loop)
    oos_vol = None
    if has_oos and test_bars and not is_0dte:
        oos_vol = _compute_rolling_vol(test_bars)

    entries = []
    for rank, (is_score, combo, is_result) in enumerate(candidates, start=1):
        if is_score == float("-inf"):
            continue

        entry: dict = {
            "rank": rank,
            "ticker": ticker,
            "timeframe": timeframe,
            "params": combo,
            # Full-period metrics (updated below if OOS exists)
            "total_pnl": is_result.total_pnl,
            "total_trades": is_result.total_trades,
            "win_rate": is_result.win_rate,
            "profit_factor": is_result.profit_factor,
            "max_drawdown": is_result.max_drawdown,
            "avg_hold_minutes": is_result.avg_hold_minutes,
            "avg_win": is_result.avg_win,
            "avg_loss": is_result.avg_loss,
            "largest_win": is_result.largest_win,
            "largest_loss": is_result.largest_loss,
            "score": round(is_score, 4),
            "exit_reasons": is_result.exit_reasons,
            "days_traded": len(is_result.days),
            "avg_entry_price": is_result.avg_entry_price,
            "max_entry_price": is_result.max_entry_price,
        }

        # ── Out-of-sample validation ─────────────────────────────
        if has_oos and test_bars:
            test_dates_sorted = sorted(test_bars.keys())
            test_params = _build_params(
                combo, ticker, timeframe,
                test_dates_sorted[0], test_dates_sorted[-1], quantity,
            )
            oos_result = run_stock_backtest(
                test_params, bars_by_day=test_bars, vix_by_day=vix_by_day, rolling_vol=oos_vol,
            )
            oos_score = compute_score(oos_result, metric)

            entry["oos_total_pnl"] = oos_result.total_pnl
            entry["oos_total_trades"] = oos_result.total_trades
            entry["oos_win_rate"] = oos_result.win_rate
            entry["oos_profit_factor"] = oos_result.profit_factor
            entry["oos_max_drawdown"] = oos_result.max_drawdown
            entry["oos_score"] = round(oos_score, 4) if oos_score != float("-inf") else 0

            # Combine IS + OOS for full-period summary so rows match drilldown
            all_trades = (is_result.trades or []) + (oos_result.trades or [])
            entry["total_pnl"] = is_result.total_pnl + oos_result.total_pnl
            entry["total_trades"] = is_result.total_trades + oos_result.total_trades
            entry["days_traded"] = len(is_result.days) + len(oos_result.days)
            entry["max_drawdown"] = max(is_result.max_drawdown, oos_result.max_drawdown)
            entry["largest_win"] = max(is_result.largest_win, oos_result.largest_win)
            entry["largest_loss"] = min(is_result.largest_loss, oos_result.largest_loss)
            if all_trades:
                wins = [t for t in all_trades if (t.pnl_dollars or 0) > 0]
                losses = [t for t in all_trades if (t.pnl_dollars or 0) <= 0]
                entry["win_rate"] = round(len(wins) / len(all_trades) * 100, 1) if all_trades else 0
                entry["avg_win"] = round(sum(t.pnl_dollars or 0 for t in wins) / len(wins), 2) if wins else 0
                entry["avg_loss"] = round(sum(t.pnl_dollars or 0 for t in losses) / len(losses), 2) if losses else 0
                gross_wins = sum(t.pnl_dollars or 0 for t in wins)
                gross_losses = abs(sum(t.pnl_dollars or 0 for t in losses))
                entry["profit_factor"] = round(gross_wins / gross_losses, 2) if gross_losses > 0 else 99.0
                hold_mins = [t.hold_minutes for t in all_trades if t.hold_minutes is not None]
                entry["avg_hold_minutes"] = round(sum(hold_mins) / len(hold_mins), 1) if hold_mins else 0
                # Merge exit reasons
                merged_exits = dict(is_result.exit_reasons)
                for k, v in oos_result.exit_reasons.items():
                    merged_exits[k] = merged_exits.get(k, 0) + v
                entry["exit_reasons"] = merged_exits

            # Monte Carlo on OOS trades
            if oos_result.trades:
                pnls = [t.pnl_dollars or 0 for t in oos_result.trades]
                mc = monte_carlo_confidence(pnls)
                entry["mc_win_pct"] = mc["win_pct"]
                entry["mc_median_pnl"] = mc["median_pnl"]
                entry["mc_p5_pnl"] = mc["p5_pnl"]
                entry["mc_p95_pnl"] = mc["p95_pnl"]

        entries.append(entry)

    # ── Sort by OOS score if available, otherwise IS score ────────
    if has_oos:
        entries.sort(key=lambda x: x.get("oos_score", float("-inf")), reverse=True)
    else:
        entries.sort(key=lambda x: x.get("score", float("-inf")), reverse=True)

    # Re-rank and trim to top_n
    entries = entries[:top_n]
    for i, e in enumerate(entries, 1):
        e["rank"] = i

    return entries


# ── Report generation ─────────────────────────────────────────────


def print_report(all_results: list[dict], top_n: int, metric: str, elapsed: float):
    print(f"\n\n{'='*80}")
    print("  MULTI-TICKER OPTIONS OPTIMIZATION REPORT (2 contracts @ 0.40 delta)")
    print(f"{'='*80}")
    print(f"  Date: {date.today()}")
    print(f"  Metric: {metric}")
    print(f"  Elapsed: {elapsed:.1f}s")
    print(f"  Total setups evaluated: {len(all_results)} (top results per ticker/timeframe)")

    if not all_results:
        print("\n  No profitable setups found.")
        return

    # ── BEST STRATEGY PER TICKER ──
    print(f"\n\n{'─'*80}")
    print("  BEST STRATEGY PER TICKER")
    print(f"{'─'*80}")
    print(f"  {'Ticker':<8} {'TF':<6} {'Signal':<16} {'PnL':>10} {'WR%':>6} {'PF':>6} {'Trades':>7} {'Score':>8}")
    print(f"  {'-'*73}")

    seen_tickers = set()
    sorted_results = sorted(all_results, key=lambda x: x["score"], reverse=True)
    for r in sorted_results:
        if r["ticker"] not in seen_tickers:
            seen_tickers.add(r["ticker"])
            print(f"  {r['ticker']:<8} {r['timeframe']:<6} {r['params']['signal_type']:<16} "
                  f"${r['total_pnl']:>9,.2f} {r['win_rate']:>5.1f}% {r['profit_factor']:>5.2f} "
                  f"{r['total_trades']:>7} {r['score']:>8.2f}")

    # ── BEST STRATEGY PER TIMEFRAME ──
    print(f"\n\n{'─'*80}")
    print("  BEST STRATEGY PER TIMEFRAME")
    print(f"{'─'*80}")
    print(f"  {'TF':<6} {'Ticker':<8} {'Signal':<16} {'PnL':>10} {'WR%':>6} {'PF':>6} {'Trades':>7} {'Score':>8}")
    print(f"  {'-'*73}")

    seen_tf = set()
    for r in sorted_results:
        if r["timeframe"] not in seen_tf:
            seen_tf.add(r["timeframe"])
            print(f"  {r['timeframe']:<6} {r['ticker']:<8} {r['params']['signal_type']:<16} "
                  f"${r['total_pnl']:>9,.2f} {r['win_rate']:>5.1f}% {r['profit_factor']:>5.2f} "
                  f"{r['total_trades']:>7} {r['score']:>8.2f}")

    # ── BEST STRATEGY PER SIGNAL TYPE ──
    print(f"\n\n{'─'*80}")
    print("  BEST RESULT PER SIGNAL TYPE")
    print(f"{'─'*80}")
    print(f"  {'Signal':<16} {'Ticker':<8} {'TF':<6} {'PnL':>10} {'WR%':>6} {'PF':>6} {'Trades':>7} {'Score':>8}")
    print(f"  {'-'*73}")

    seen_signals = set()
    for r in sorted_results:
        sig = r["params"]["signal_type"]
        if sig not in seen_signals:
            seen_signals.add(sig)
            print(f"  {sig:<16} {r['ticker']:<8} {r['timeframe']:<6} "
                  f"${r['total_pnl']:>9,.2f} {r['win_rate']:>5.1f}% {r['profit_factor']:>5.2f} "
                  f"{r['total_trades']:>7} {r['score']:>8.2f}")

    # ── TOP N OVERALL SETUPS ──
    print(f"\n\n{'─'*80}")
    print(f"  TOP {top_n} SETUPS (Ranked by {metric})")
    print(f"{'─'*80}")
    print(f"  {'#':<4} {'Ticker':<8} {'TF':<6} {'Signal':<16} {'PnL':>10} {'WR%':>6} {'PF':>6} "
          f"{'MaxDD':>8} {'Trades':>7} {'AvgHold':>8} {'Score':>8}")
    print(f"  {'-'*93}")

    for i, r in enumerate(sorted_results[:top_n], 1):
        print(f"  {i:<4} {r['ticker']:<8} {r['timeframe']:<6} {r['params']['signal_type']:<16} "
              f"${r['total_pnl']:>9,.2f} {r['win_rate']:>5.1f}% {r['profit_factor']:>5.2f} "
              f"${r['max_drawdown']:>7,.2f} {r['total_trades']:>7} {r['avg_hold_minutes']:>6.0f}m "
              f"{r['score']:>8.2f}")

    # ── DETAILED PARAMS FOR TOP 5 ──
    print(f"\n\n{'─'*80}")
    print("  DETAILED PARAMETERS FOR TOP 5 SETUPS")
    print(f"{'─'*80}")

    for i, r in enumerate(sorted_results[:5], 1):
        p = r["params"]
        print(f"\n  #{i} {r['ticker']} @ {r['timeframe']} — {p['signal_type']}")
        print(f"     PnL: ${r['total_pnl']:,.2f} | Win Rate: {r['win_rate']:.1f}% | "
              f"Profit Factor: {r['profit_factor']:.2f} | Trades: {r['total_trades']}")
        print(f"     Max Drawdown: ${r['max_drawdown']:,.2f} | Avg Hold: {r['avg_hold_minutes']:.0f}m")
        print(f"     Avg Win: ${r['avg_win']:,.2f} | Avg Loss: ${r['avg_loss']:,.2f} | "
              f"Best: ${r['largest_win']:,.2f} | Worst: ${r['largest_loss']:,.2f}")
        print(f"     Params: ema={p['ema_fast']}/{p['ema_slow']} stop={p['stop_loss_percent']}% "
              f"target={p['profit_target_percent']}% trail={p['trailing_stop_percent']}% "
              f"hold={p['max_hold_minutes']}m")
        if p["rsi_period"] > 0:
            print(f"     RSI: period={p['rsi_period']}")
        if p["atr_period"] > 0:
            print(f"     ATR: period={p['atr_period']} mult={p['atr_stop_mult']}")
        if p["signal_type"] in ("orb", "orb_direction"):
            print(f"     ORB: minutes={p['orb_minutes']} stop_mult={p['orb_stop_mult']} "
                  f"target_mult={p['orb_target_mult']}")
        if p["signal_type"] == "orb_direction":
            print(f"     ORB Filters: body_min={p['orb_body_min_pct']} "
                  f"vwap={p['orb_vwap_filter']} gap_fade={p['orb_gap_fade_filter']}")
        if p["signal_type"] == "confluence":
            print(f"     Confluence: min_score={p['min_confluence']} vol_threshold={p['vol_threshold']}")
        if p.get("vix_min", 0) > 0 or p.get("vix_max", 100) < 100:
            print(f"     VIX Filter: min={p.get('vix_min', 0)} max={p.get('vix_max', 100)}")
        print(f"     Exit Reasons: {r['exit_reasons']}")

    print(f"\n{'='*80}\n")


def save_json_report(all_results: list[dict], filepath: str):
    """Save full results to JSON for further analysis."""
    with open(filepath, "w") as f:
        json.dump({
            "generated": date.today().isoformat(),
            "total_results": len(all_results),
            "results": sorted(all_results, key=lambda x: x["score"], reverse=True),
        }, f, indent=2, default=str)
    print(f"JSON report saved to: {filepath}")


# ── Main ──────────────────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(description="Multi-Ticker Options Optimizer")
    parser.add_argument("--tickers", default="all",
                        help="Comma-separated tickers or 'all' (default: all)")
    parser.add_argument("--timeframes", default="all",
                        help="Comma-separated timeframes (1m,5m,10m,15m,30m) or 'all' (default: all)")
    parser.add_argument("--iterations", type=int, default=200,
                        help="Parameter combinations per ticker/timeframe (default: 200)")
    parser.add_argument("--metric", default="pro",
                        choices=["total_pnl", "profit_factor", "win_rate", "composite", "risk_adjusted", "sharpe", "pro"],
                        help="Scoring metric (default: pro)")
    parser.add_argument("--top-n", type=int, default=20,
                        help="Number of top setups in final report (default: 20)")
    parser.add_argument("--quantity", type=int, default=2,
                        help="Option contracts per trade (default: 2)")
    parser.add_argument("--output", default=None,
                        help="Path to save JSON results (optional)")
    parser.add_argument("--workers", type=int, default=0,
                        help="Parallel workers (default: CPU count)")
    parser.add_argument("--days-back", type=int, default=180,
                        help="Number of days of historical data to use (default: 180)")
    return parser.parse_args()


def _worker_task(args: tuple) -> list[dict]:
    """Worker function for parallel optimization of a single ticker/timeframe."""
    ticker, tf, iterations, metric, quantity, top_n, start_date, end_date = args

    # Each worker loads its own data (can't share across processes)
    bars_by_day = load_ticker_csv_bars(ticker, start_date, end_date, tf)
    if not bars_by_day:
        return []

    vix_by_day = load_vix_data(start_date, end_date)

    return optimize_ticker_timeframe(
        ticker=ticker,
        timeframe=tf,
        bars_by_day=bars_by_day,
        iterations=iterations,
        metric=metric,
        quantity=quantity,
        top_n=top_n,
        vix_by_day=vix_by_day,
    )


def main():
    args = parse_args()
    tickers = ALL_TICKERS if args.tickers == "all" else [t.strip().upper() for t in args.tickers.split(",")]
    timeframes = ALL_TIMEFRAMES if args.timeframes == "all" else [t.strip() for t in args.timeframes.split(",")]

    from datetime import timedelta
    end_date = date.today()
    start_date = end_date - timedelta(days=args.days_back)

    print("=" * 60)
    print("  Multi-Ticker Options Optimizer (Black-Scholes)")
    print("=" * 60)
    workers = args.workers if args.workers > 0 else os.cpu_count() or 4
    total = len(tickers) * len(timeframes)

    print(f"\n  Tickers ({len(tickers)}): {', '.join(tickers)}")
    print(f"  Timeframes ({len(timeframes)}): {', '.join(timeframes)}")
    print(f"  Date range: {start_date} to {end_date} ({args.days_back} days)")
    print(f"  Iterations per combo: {args.iterations}")
    print(f"  Scoring metric: {args.metric}")
    print(f"  Contracts per trade: {args.quantity} @ 0.35 delta")
    print(f"  Total optimizations: {total}")
    print(f"  Workers: {workers}")
    print()

    t0 = time.time()
    all_results: list[dict] = []

    # Build task list
    tasks = [
        (ticker, tf, args.iterations, args.metric, args.quantity, 3, start_date, end_date)
        for ticker in tickers
        for tf in timeframes
    ]

    completed = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_worker_task, task): task for task in tasks}

        for future in as_completed(futures):
            task = futures[future]
            ticker, tf = task[0], task[1]
            completed += 1

            try:
                top = future.result()
            except Exception as e:
                print(f"[{completed}/{total}] {ticker} @ {tf} -> ERROR: {e}")
                continue

            if top:
                best = top[0]
                print(f"[{completed}/{total}] {ticker} @ {tf} -> "
                      f"Best: {best['params']['signal_type']} "
                      f"PnL=${best['total_pnl']:,.2f} WR={best['win_rate']:.1f}% "
                      f"PF={best['profit_factor']:.2f}")
                all_results.extend(top)
            else:
                print(f"[{completed}/{total}] {ticker} @ {tf} -> No profitable setups")

    total_elapsed = time.time() - t0

    # Print report
    print_report(all_results, args.top_n, args.metric, total_elapsed)

    # Save JSON if requested
    output_path = args.output
    if output_path is None:
        output_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "data", "optimization_results.json"
        )
    save_json_report(all_results, output_path)


if __name__ == "__main__":
    main()
