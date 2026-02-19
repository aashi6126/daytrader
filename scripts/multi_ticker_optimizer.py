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
from dataclasses import asdict
from datetime import date, time as dtime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stock_backtest_engine import (
    StockBacktestParams,
    StockBacktestResult,
    load_ticker_csv_bars,
    load_vix_data,
    run_stock_backtest,
)
from app.services.backtest.market_data import BarData

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

ALL_TICKERS = ["NVDA", "TSLA", "AMZN", "AMD", "AAPL", "PLTR", "MSFT", "GOOGL", "QQQ", "GLD", "ASTS", "NBIS", "CRWV", "IREN"]
ALL_TIMEFRAMES = ["1m", "5m", "10m", "15m", "30m"]

# ── Parameter space (options-level) ──────────────────────────────

OPTION_PARAM_SPACE: dict[str, list] = {
    "signal_type": [
        "ema_cross", "vwap_cross", "ema_vwap", "orb", "vwap_rsi",
        "bb_squeeze", "rsi_reversal", "confluence", "orb_direction", "vwap_reclaim",
    ],
    "ema_fast": [5, 8, 13, 21],
    "ema_slow": [13, 21, 34, 55],
    "stop_loss_percent": [10.0, 16.0, 20.0, 25.0, 30.0],
    "profit_target_percent": [20.0, 30.0, 40.0, 50.0, 60.0],
    "trailing_stop_percent": [10.0, 15.0, 20.0, 25.0, 30.0],
    "max_hold_minutes": [30, 60, 90, 120],
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
}

NON_EMA_STRATEGIES = {"orb", "bb_squeeze", "rsi_reversal", "orb_direction", "vwap_reclaim"}


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

        combos.append(combo)

    return combos


# ── Scoring ───────────────────────────────────────────────────────


def compute_score(result: StockBacktestResult, metric: str) -> float:
    if result.total_trades < 5:
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


# ── Optimization loop ─────────────────────────────────────────────


def optimize_ticker_timeframe(
    ticker: str,
    timeframe: str,
    bars_by_day: dict[date, list[BarData]],
    iterations: int,
    metric: str,
    quantity: int,
    top_n: int = 10,
    vix_by_day: dict[date, float] | None = None,
) -> list[dict]:
    """Run optimization for a single ticker/timeframe combo. Returns top N results."""

    if not bars_by_day:
        return []

    dates = sorted(bars_by_day.keys())
    start_date = dates[0]
    end_date = dates[-1]

    # Load VIX if not provided
    if vix_by_day is None:
        vix_by_day = load_vix_data(start_date, end_date)

    combos = generate_combinations(iterations)
    scored: list[tuple[float, dict, StockBacktestResult]] = []

    for combo in combos:
        params = StockBacktestParams(
            start_date=start_date,
            end_date=end_date,
            ticker=ticker,
            bar_interval=timeframe,
            quantity=quantity,
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
            orb_body_min_pct=combo.get("orb_body_min_pct", 0.0),
            orb_vwap_filter=combo.get("orb_vwap_filter", False),
            orb_gap_fade_filter=combo.get("orb_gap_fade_filter", False),
            orb_stop_mult=combo.get("orb_stop_mult", 1.0),
            orb_target_mult=combo.get("orb_target_mult", 1.5),
        )

        result = run_stock_backtest(params, bars_by_day=bars_by_day, vix_by_day=vix_by_day)
        score = compute_score(result, metric)
        scored.append((score, combo, result))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_n]

    entries = []
    for rank, (score, combo, result) in enumerate(top, start=1):
        if score == float("-inf"):
            continue
        entries.append({
            "rank": rank,
            "ticker": ticker,
            "timeframe": timeframe,
            "params": combo,
            "total_pnl": result.total_pnl,
            "total_trades": result.total_trades,
            "win_rate": result.win_rate,
            "profit_factor": result.profit_factor,
            "max_drawdown": result.max_drawdown,
            "avg_hold_minutes": result.avg_hold_minutes,
            "avg_win": result.avg_win,
            "avg_loss": result.avg_loss,
            "largest_win": result.largest_win,
            "largest_loss": result.largest_loss,
            "score": round(score, 4),
            "exit_reasons": result.exit_reasons,
            "days_traded": len(result.days),
        })

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
    parser.add_argument("--metric", default="risk_adjusted",
                        choices=["total_pnl", "profit_factor", "win_rate", "composite", "risk_adjusted"],
                        help="Scoring metric (default: risk_adjusted)")
    parser.add_argument("--top-n", type=int, default=20,
                        help="Number of top setups in final report (default: 20)")
    parser.add_argument("--quantity", type=int, default=2,
                        help="Option contracts per trade (default: 2)")
    parser.add_argument("--output", default=None,
                        help="Path to save JSON results (optional)")
    return parser.parse_args()


def main():
    args = parse_args()
    tickers = ALL_TICKERS if args.tickers == "all" else [t.strip().upper() for t in args.tickers.split(",")]
    timeframes = ALL_TIMEFRAMES if args.timeframes == "all" else [t.strip() for t in args.timeframes.split(",")]

    print("=" * 60)
    print("  Multi-Ticker Options Optimizer (Black-Scholes)")
    print("=" * 60)
    print(f"\n  Tickers ({len(tickers)}): {', '.join(tickers)}")
    print(f"  Timeframes ({len(timeframes)}): {', '.join(timeframes)}")
    print(f"  Iterations per combo: {args.iterations}")
    print(f"  Scoring metric: {args.metric}")
    print(f"  Contracts per trade: {args.quantity} @ 0.40 delta")
    print(f"  Total optimizations: {len(tickers) * len(timeframes)}")
    print()

    # Pre-load VIX data once
    print("  Loading VIX data...", end=" ", flush=True)
    vix_by_day = load_vix_data(date(2000, 1, 1), date(2099, 12, 31))
    print(f"({len(vix_by_day)} days)")

    t0 = time.time()
    all_results: list[dict] = []
    completed = 0
    total = len(tickers) * len(timeframes)

    for ticker in tickers:
        for tf in timeframes:
            completed += 1
            print(f"[{completed}/{total}] Optimizing {ticker} @ {tf} ({args.iterations} combos)...", end=" ", flush=True)

            # Load data
            bars_by_day = load_ticker_csv_bars(ticker, date(2000, 1, 1), date(2099, 12, 31), tf)

            if not bars_by_day:
                print("SKIP (no data)")
                continue

            total_bars = sum(len(v) for v in bars_by_day.values())
            print(f"({len(bars_by_day)} days, {total_bars:,} bars) ", end="", flush=True)

            t1 = time.time()
            top = optimize_ticker_timeframe(
                ticker=ticker,
                timeframe=tf,
                bars_by_day=bars_by_day,
                iterations=args.iterations,
                metric=args.metric,
                quantity=args.quantity,
                top_n=3,  # keep top 3 per ticker/timeframe
                vix_by_day=vix_by_day,
            )
            elapsed = time.time() - t1

            if top:
                best = top[0]
                print(f"-> Best: {best['params']['signal_type']} "
                      f"PnL=${best['total_pnl']:,.2f} WR={best['win_rate']:.1f}% "
                      f"PF={best['profit_factor']:.2f} ({elapsed:.1f}s)")
                all_results.extend(top)
            else:
                print(f"-> No profitable setups ({elapsed:.1f}s)")

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
