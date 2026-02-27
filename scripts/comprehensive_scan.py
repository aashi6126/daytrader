"""
Comprehensive Backtest Scanner — All Tickers, All Setups (Except 1m)
=====================================================================
Scans all downloaded CSV tickers across 5m/10m/15m/30m timeframes
using curated strategy configurations to find the highest profit-potential setups.

USAGE:
  python scripts/comprehensive_scan.py
  python scripts/comprehensive_scan.py --workers 8 --top-n 30
"""

import argparse
import json
import math
import os
import sys
import time as time_mod
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from datetime import date, time, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stock_backtest_engine import (
    StockBacktestParams,
    StockBacktestResult,
    load_ticker_csv_bars,
    load_vix_data,
    run_stock_backtest,
)

DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"))
TIMEFRAMES = ["5m", "10m", "15m", "30m"]

# ── Curated Strategy Configurations ──────────────────────────────
# 20 well-tuned configs covering all signal types and key parameter combos.
# Much more efficient than random sampling for a broad scan.

STRATEGIES: list[dict] = [
    # EMA Cross variants
    {
        "name": "ema_cross_tight",
        "signal_type": "ema_cross", "ema_fast": 8, "ema_slow": 21,
        "stop_loss_percent": 16.0, "profit_target_percent": 40.0,
        "trailing_stop_percent": 15.0, "max_hold_minutes": 60,
    },
    {
        "name": "ema_cross_wide",
        "signal_type": "ema_cross", "ema_fast": 5, "ema_slow": 21,
        "stop_loss_percent": 25.0, "profit_target_percent": 50.0,
        "trailing_stop_percent": 20.0, "max_hold_minutes": 90,
    },
    {
        "name": "ema_cross_slow",
        "signal_type": "ema_cross", "ema_fast": 13, "ema_slow": 34,
        "stop_loss_percent": 20.0, "profit_target_percent": 40.0,
        "trailing_stop_percent": 20.0, "max_hold_minutes": 120,
    },
    # VWAP Cross
    {
        "name": "vwap_cross_default",
        "signal_type": "vwap_cross", "ema_fast": 8, "ema_slow": 21,
        "stop_loss_percent": 16.0, "profit_target_percent": 40.0,
        "trailing_stop_percent": 15.0, "max_hold_minutes": 60,
    },
    {
        "name": "vwap_cross_wide",
        "signal_type": "vwap_cross", "ema_fast": 8, "ema_slow": 21,
        "stop_loss_percent": 25.0, "profit_target_percent": 60.0,
        "trailing_stop_percent": 25.0, "max_hold_minutes": 90,
    },
    # EMA + VWAP
    {
        "name": "ema_vwap_default",
        "signal_type": "ema_vwap", "ema_fast": 8, "ema_slow": 21,
        "stop_loss_percent": 20.0, "profit_target_percent": 40.0,
        "trailing_stop_percent": 15.0, "max_hold_minutes": 60,
    },
    {
        "name": "ema_vwap_wide",
        "signal_type": "ema_vwap", "ema_fast": 5, "ema_slow": 13,
        "stop_loss_percent": 25.0, "profit_target_percent": 50.0,
        "trailing_stop_percent": 20.0, "max_hold_minutes": 90,
    },
    # VWAP Reclaim
    {
        "name": "vwap_reclaim_default",
        "signal_type": "vwap_reclaim", "ema_fast": 8, "ema_slow": 21,
        "stop_loss_percent": 16.0, "profit_target_percent": 40.0,
        "trailing_stop_percent": 15.0, "max_hold_minutes": 60,
    },
    # VWAP RSI
    {
        "name": "vwap_rsi_default",
        "signal_type": "vwap_rsi", "ema_fast": 8, "ema_slow": 21,
        "rsi_period": 14,
        "stop_loss_percent": 16.0, "profit_target_percent": 40.0,
        "trailing_stop_percent": 15.0, "max_hold_minutes": 60,
    },
    {
        "name": "vwap_rsi_fast",
        "signal_type": "vwap_rsi", "ema_fast": 8, "ema_slow": 21,
        "rsi_period": 9,
        "stop_loss_percent": 20.0, "profit_target_percent": 30.0,
        "trailing_stop_percent": 10.0, "max_hold_minutes": 45,
    },
    # RSI Reversal
    {
        "name": "rsi_reversal_default",
        "signal_type": "rsi_reversal", "ema_fast": 8, "ema_slow": 21,
        "rsi_period": 14,
        "stop_loss_percent": 20.0, "profit_target_percent": 30.0,
        "trailing_stop_percent": 15.0, "max_hold_minutes": 60,
    },
    {
        "name": "rsi_reversal_fast",
        "signal_type": "rsi_reversal", "ema_fast": 8, "ema_slow": 21,
        "rsi_period": 9,
        "stop_loss_percent": 16.0, "profit_target_percent": 40.0,
        "trailing_stop_percent": 20.0, "max_hold_minutes": 90,
    },
    # BB Squeeze
    {
        "name": "bb_squeeze_default",
        "signal_type": "bb_squeeze", "ema_fast": 8, "ema_slow": 21,
        "bb_period": 20, "bb_std_mult": 2.0,
        "stop_loss_percent": 20.0, "profit_target_percent": 50.0,
        "trailing_stop_percent": 20.0, "max_hold_minutes": 90,
    },
    {
        "name": "bb_squeeze_tight",
        "signal_type": "bb_squeeze", "ema_fast": 8, "ema_slow": 21,
        "bb_period": 15, "bb_std_mult": 1.5,
        "stop_loss_percent": 16.0, "profit_target_percent": 40.0,
        "trailing_stop_percent": 15.0, "max_hold_minutes": 60,
    },
    # ORB
    {
        "name": "orb_15min",
        "signal_type": "orb", "ema_fast": 8, "ema_slow": 21,
        "orb_minutes": 15,
        "stop_loss_percent": 20.0, "profit_target_percent": 40.0,
        "trailing_stop_percent": 15.0, "max_hold_minutes": 60,
    },
    {
        "name": "orb_30min",
        "signal_type": "orb", "ema_fast": 8, "ema_slow": 21,
        "orb_minutes": 30,
        "stop_loss_percent": 25.0, "profit_target_percent": 50.0,
        "trailing_stop_percent": 20.0, "max_hold_minutes": 90,
    },
    # ORB Direction (with filters)
    {
        "name": "orb_dir_filtered",
        "signal_type": "orb_direction", "ema_fast": 8, "ema_slow": 21,
        "orb_minutes": 15, "orb_body_min_pct": 0.5,
        "orb_vwap_filter": True, "orb_gap_fade_filter": True,
        "orb_stop_mult": 1.0, "orb_target_mult": 1.5,
        "stop_loss_percent": 20.0, "profit_target_percent": 40.0,
        "trailing_stop_percent": 15.0, "max_hold_minutes": 60,
    },
    {
        "name": "orb_dir_loose",
        "signal_type": "orb_direction", "ema_fast": 8, "ema_slow": 21,
        "orb_minutes": 10, "orb_body_min_pct": 0.3,
        "orb_vwap_filter": False, "orb_gap_fade_filter": False,
        "orb_stop_mult": 0.75, "orb_target_mult": 2.0,
        "stop_loss_percent": 25.0, "profit_target_percent": 50.0,
        "trailing_stop_percent": 20.0, "max_hold_minutes": 90,
    },
    # Confluence
    {
        "name": "confluence_strict",
        "signal_type": "confluence", "ema_fast": 8, "ema_slow": 21,
        "rsi_period": 14, "min_confluence": 5, "vol_threshold": 1.5,
        "bb_period": 20, "bb_std_mult": 2.0,
        "macd_fast": 12, "macd_slow": 26, "macd_signal_period": 9,
        "stop_loss_percent": 20.0, "profit_target_percent": 40.0,
        "trailing_stop_percent": 15.0, "max_hold_minutes": 60,
    },
    {
        "name": "confluence_relaxed",
        "signal_type": "confluence", "ema_fast": 5, "ema_slow": 13,
        "rsi_period": 9, "min_confluence": 4, "vol_threshold": 1.0,
        "bb_period": 15, "bb_std_mult": 2.0,
        "macd_fast": 8, "macd_slow": 21, "macd_signal_period": 7,
        "stop_loss_percent": 25.0, "profit_target_percent": 50.0,
        "trailing_stop_percent": 20.0, "max_hold_minutes": 90,
    },
]


def discover_tickers() -> list[str]:
    """Find all tickers with at least one non-1m CSV file."""
    tickers = []
    for entry in sorted(os.listdir(DATA_DIR)):
        path = os.path.join(DATA_DIR, entry)
        if not os.path.isdir(path):
            continue
        # Check for at least one non-1m CSV
        for tf in ["5min", "10min", "15min", "30min"]:
            if os.path.exists(os.path.join(path, f"{entry}_{tf}_6months.csv")):
                tickers.append(entry)
                break
    return tickers


def _build_params(ticker: str, timeframe: str, strat: dict, start_date: date, end_date: date) -> StockBacktestParams:
    """Build StockBacktestParams from a strategy config dict."""
    return StockBacktestParams(
        start_date=start_date,
        end_date=end_date,
        ticker=ticker,
        bar_interval=timeframe,
        quantity=2,
        signal_type=strat["signal_type"],
        ema_fast=strat.get("ema_fast", 8),
        ema_slow=strat.get("ema_slow", 21),
        stop_loss_percent=strat["stop_loss_percent"],
        profit_target_percent=strat["profit_target_percent"],
        trailing_stop_percent=strat["trailing_stop_percent"],
        max_hold_minutes=strat["max_hold_minutes"],
        rsi_period=strat.get("rsi_period", 0),
        orb_minutes=strat.get("orb_minutes", 15),
        bb_period=strat.get("bb_period", 20),
        bb_std_mult=strat.get("bb_std_mult", 2.0),
        macd_fast=strat.get("macd_fast", 12),
        macd_slow=strat.get("macd_slow", 26),
        macd_signal_period=strat.get("macd_signal_period", 9),
        min_confluence=strat.get("min_confluence", 5),
        vol_threshold=strat.get("vol_threshold", 1.5),
        orb_body_min_pct=strat.get("orb_body_min_pct", 0.0),
        orb_vwap_filter=strat.get("orb_vwap_filter", False),
        orb_gap_fade_filter=strat.get("orb_gap_fade_filter", False),
        orb_stop_mult=strat.get("orb_stop_mult", 1.0),
        orb_target_mult=strat.get("orb_target_mult", 1.5),
        spread_model_enabled=True,
        scale_out_enabled=True,
    )


def _worker(args: tuple) -> list[dict]:
    """Worker: backtest one ticker across all timeframes and strategies."""
    ticker, start_date, end_date, strategies = args
    results = []

    for tf in TIMEFRAMES:
        bars_by_day = load_ticker_csv_bars(ticker, start_date, end_date, tf)
        if not bars_by_day or len(bars_by_day) < 10:
            continue

        vix_by_day = load_vix_data(start_date, end_date)

        for strat in strategies:
            try:
                params = _build_params(ticker, tf, strat, start_date, end_date)
                result = run_stock_backtest(params, bars_by_day=bars_by_day, vix_by_day=vix_by_day)

                if result.total_trades < 3:
                    continue

                results.append({
                    "ticker": ticker,
                    "timeframe": tf,
                    "strategy": strat["name"],
                    "signal_type": strat["signal_type"],
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
                    "exit_reasons": result.exit_reasons,
                    "days_traded": len(result.days),
                    "winning_trades": result.winning_trades,
                    "losing_trades": result.losing_trades,
                    "avg_entry_price": result.avg_entry_price,
                    "max_entry_price": result.max_entry_price,
                    "params": {k: v for k, v in strat.items() if k != "name"},
                })
            except Exception:
                continue

    return results


def print_report(all_results: list[dict], top_n: int, elapsed: float):
    # Sort by total_pnl (most profit potential)
    sorted_by_pnl = sorted(all_results, key=lambda x: x["total_pnl"], reverse=True)

    print(f"\n\n{'='*100}")
    print("  COMPREHENSIVE BACKTEST SCAN — ALL TICKERS (excl. 1m)")
    print(f"{'='*100}")
    print(f"  Date: {date.today()}")
    print(f"  Elapsed: {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"  Total setups evaluated: {len(all_results)}")
    tickers_seen = set(r["ticker"] for r in all_results)
    print(f"  Tickers with results: {len(tickers_seen)}")

    # ── TOP N BY RAW PNL ──
    print(f"\n\n{'─'*100}")
    print(f"  TOP {top_n} SETUPS BY TOTAL PNL (Most Profit Potential)")
    print(f"{'─'*100}")
    print(f"  {'#':<4} {'Ticker':<8} {'TF':<6} {'Strategy':<22} {'Signal':<16} "
          f"{'PnL':>10} {'WR%':>6} {'PF':>6} {'MaxDD':>8} {'Trades':>7} {'AvgHold':>8}")
    print(f"  {'-'*107}")

    for i, r in enumerate(sorted_by_pnl[:top_n], 1):
        print(f"  {i:<4} {r['ticker']:<8} {r['timeframe']:<6} {r['strategy']:<22} "
              f"{r['signal_type']:<16} ${r['total_pnl']:>9,.2f} {r['win_rate']:>5.1f}% "
              f"{r['profit_factor']:>5.2f} ${r['max_drawdown']:>7,.2f} {r['total_trades']:>7} "
              f"{r['avg_hold_minutes']:>6.0f}m")

    # ── TOP N BY RISK-ADJUSTED (PnL / MaxDD) ──
    sorted_by_risk = sorted(
        [r for r in all_results if r["max_drawdown"] > 0],
        key=lambda x: x["total_pnl"] / x["max_drawdown"],
        reverse=True,
    )

    print(f"\n\n{'─'*100}")
    print(f"  TOP {top_n} SETUPS BY RISK-ADJUSTED RETURN (PnL / MaxDD)")
    print(f"{'─'*100}")
    print(f"  {'#':<4} {'Ticker':<8} {'TF':<6} {'Strategy':<22} {'Signal':<16} "
          f"{'PnL':>10} {'PnL/DD':>7} {'WR%':>6} {'PF':>6} {'Trades':>7}")
    print(f"  {'-'*100}")

    for i, r in enumerate(sorted_by_risk[:top_n], 1):
        ratio = r["total_pnl"] / r["max_drawdown"]
        print(f"  {i:<4} {r['ticker']:<8} {r['timeframe']:<6} {r['strategy']:<22} "
              f"{r['signal_type']:<16} ${r['total_pnl']:>9,.2f} {ratio:>6.2f}x "
              f"{r['win_rate']:>5.1f}% {r['profit_factor']:>5.2f} {r['total_trades']:>7}")

    # ── BEST BY SIGNAL TYPE ──
    print(f"\n\n{'─'*100}")
    print("  BEST SETUP PER SIGNAL TYPE (by PnL)")
    print(f"{'─'*100}")
    print(f"  {'Signal':<16} {'Ticker':<8} {'TF':<6} {'Strategy':<22} "
          f"{'PnL':>10} {'WR%':>6} {'PF':>6} {'Trades':>7}")
    print(f"  {'-'*87}")

    seen_signals = set()
    for r in sorted_by_pnl:
        sig = r["signal_type"]
        if sig not in seen_signals:
            seen_signals.add(sig)
            print(f"  {sig:<16} {r['ticker']:<8} {r['timeframe']:<6} {r['strategy']:<22} "
                  f"${r['total_pnl']:>9,.2f} {r['win_rate']:>5.1f}% {r['profit_factor']:>5.2f} "
                  f"{r['total_trades']:>7}")

    # ── BEST BY TIMEFRAME ──
    print(f"\n\n{'─'*100}")
    print("  BEST SETUP PER TIMEFRAME (by PnL)")
    print(f"{'─'*100}")
    print(f"  {'TF':<6} {'Ticker':<8} {'Strategy':<22} {'Signal':<16} "
          f"{'PnL':>10} {'WR%':>6} {'PF':>6} {'Trades':>7}")
    print(f"  {'-'*87}")

    seen_tf = set()
    for r in sorted_by_pnl:
        if r["timeframe"] not in seen_tf:
            seen_tf.add(r["timeframe"])
            print(f"  {r['timeframe']:<6} {r['ticker']:<8} {r['strategy']:<22} "
                  f"{r['signal_type']:<16} ${r['total_pnl']:>9,.2f} {r['win_rate']:>5.1f}% "
                  f"{r['profit_factor']:>5.2f} {r['total_trades']:>7}")

    # ── BEST TICKER (aggregate across all strategies) ──
    print(f"\n\n{'─'*100}")
    print("  TOP 20 TICKERS BY BEST SINGLE-SETUP PNL")
    print(f"{'─'*100}")
    print(f"  {'Ticker':<8} {'TF':<6} {'Strategy':<22} {'Signal':<16} "
          f"{'PnL':>10} {'WR%':>6} {'PF':>6} {'Trades':>7}")
    print(f"  {'-'*87}")

    seen_tickers = set()
    for r in sorted_by_pnl:
        if r["ticker"] not in seen_tickers:
            seen_tickers.add(r["ticker"])
            print(f"  {r['ticker']:<8} {r['timeframe']:<6} {r['strategy']:<22} "
                  f"{r['signal_type']:<16} ${r['total_pnl']:>9,.2f} {r['win_rate']:>5.1f}% "
                  f"{r['profit_factor']:>5.2f} {r['total_trades']:>7}")
            if len(seen_tickers) >= 20:
                break

    # ── DETAILED PARAMS FOR TOP 5 ──
    print(f"\n\n{'─'*100}")
    print("  DETAILED PARAMETERS FOR TOP 5 SETUPS (by PnL)")
    print(f"{'─'*100}")

    for i, r in enumerate(sorted_by_pnl[:5], 1):
        p = r["params"]
        print(f"\n  #{i} {r['ticker']} @ {r['timeframe']} — {r['strategy']} ({r['signal_type']})")
        print(f"     PnL: ${r['total_pnl']:,.2f} | Win Rate: {r['win_rate']:.1f}% | "
              f"Profit Factor: {r['profit_factor']:.2f} | Trades: {r['total_trades']}")
        print(f"     Max Drawdown: ${r['max_drawdown']:,.2f} | Avg Hold: {r['avg_hold_minutes']:.0f}m")
        print(f"     Avg Win: ${r['avg_win']:,.2f} | Avg Loss: ${r['avg_loss']:,.2f} | "
              f"Best: ${r['largest_win']:,.2f} | Worst: ${r['largest_loss']:,.2f}")
        print(f"     Avg Entry Price: ${r['avg_entry_price']:.2f} | Max Entry Price: ${r['max_entry_price']:.2f}")
        print(f"     Params: stop={p['stop_loss_percent']}% target={p['profit_target_percent']}% "
              f"trail={p['trailing_stop_percent']}% hold={p['max_hold_minutes']}m")
        if p.get("rsi_period", 0) > 0:
            print(f"     RSI: period={p['rsi_period']}")
        if p.get("signal_type") in ("orb", "orb_direction"):
            print(f"     ORB: minutes={p.get('orb_minutes', 15)}")
        if p.get("signal_type") == "orb_direction":
            print(f"     ORB Filters: body_min={p.get('orb_body_min_pct')} "
                  f"vwap={p.get('orb_vwap_filter')} gap_fade={p.get('orb_gap_fade_filter')}")
        if p.get("signal_type") == "confluence":
            print(f"     Confluence: min_score={p.get('min_confluence')} "
                  f"vol_threshold={p.get('vol_threshold')}")
        print(f"     Exit Reasons: {r['exit_reasons']}")

    print(f"\n{'='*100}\n")


def main():
    parser = argparse.ArgumentParser(description="Comprehensive Backtest Scanner")
    parser.add_argument("--workers", type=int, default=0, help="Parallel workers (default: CPU count)")
    parser.add_argument("--top-n", type=int, default=25, help="Top N results per section (default: 25)")
    parser.add_argument("--output", default=None, help="JSON output path")
    parser.add_argument("--days-back", type=int, default=180, help="Days of history (default: 180)")
    args = parser.parse_args()

    tickers = discover_tickers()
    end_date = date.today()
    start_date = end_date - timedelta(days=args.days_back)
    workers = args.workers if args.workers > 0 else os.cpu_count() or 4

    total_combos = len(tickers) * len(TIMEFRAMES) * len(STRATEGIES)

    print("=" * 80)
    print("  Comprehensive Backtest Scanner")
    print("=" * 80)
    print(f"\n  Tickers: {len(tickers)}")
    print(f"  Timeframes: {', '.join(TIMEFRAMES)}")
    print(f"  Strategies: {len(STRATEGIES)}")
    print(f"  Total backtests: {total_combos:,}")
    print(f"  Date range: {start_date} to {end_date}")
    print(f"  Workers: {workers}")
    print()

    t0 = time_mod.time()
    all_results: list[dict] = []

    # Build one task per ticker (each worker handles all TF/strategies for that ticker)
    tasks = [
        (ticker, start_date, end_date, STRATEGIES)
        for ticker in tickers
    ]

    completed = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_worker, task): task[0] for task in tasks}

        for future in as_completed(futures):
            ticker = futures[future]
            completed += 1

            try:
                results = future.result()
            except Exception as e:
                print(f"  [{completed}/{len(tickers)}] {ticker} -> ERROR: {e}")
                continue

            if results:
                best = max(results, key=lambda x: x["total_pnl"])
                print(f"  [{completed}/{len(tickers)}] {ticker} -> "
                      f"{len(results)} setups, best: {best['strategy']}@{best['timeframe']} "
                      f"PnL=${best['total_pnl']:,.2f} WR={best['win_rate']:.1f}%")
                all_results.extend(results)
            else:
                print(f"  [{completed}/{len(tickers)}] {ticker} -> No profitable setups")

    elapsed = time_mod.time() - t0

    print_report(all_results, args.top_n, elapsed)

    # Save JSON
    output_path = args.output or os.path.join(DATA_DIR, "comprehensive_scan_results.json")
    sorted_results = sorted(all_results, key=lambda x: x["total_pnl"], reverse=True)
    with open(output_path, "w") as f:
        json.dump({
            "generated": date.today().isoformat(),
            "elapsed_seconds": round(elapsed, 1),
            "total_tickers": len(tickers),
            "total_setups": len(all_results),
            "timeframes": TIMEFRAMES,
            "strategies_count": len(STRATEGIES),
            "results": sorted_results,
        }, f, indent=2, default=str)
    print(f"JSON saved to: {output_path}")


if __name__ == "__main__":
    main()
