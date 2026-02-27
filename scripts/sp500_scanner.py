"""
S&P 500 Day Trading Scanner
============================
Downloads OHLCV data for all S&P 500 stocks and runs the optimizer to find
the best day trading setups. Skips 1-minute timeframes.

USAGE:
  # Full run (download + optimize all S&P 500 stocks)
  python scripts/sp500_scanner.py

  # Download only
  python scripts/sp500_scanner.py --download-only

  # Optimize only (assumes data is already downloaded)
  python scripts/sp500_scanner.py --optimize-only

  # Custom ticker list
  python scripts/sp500_scanner.py --tickers-file data/my_tickers.txt

  # Quick test with fewer iterations
  python scripts/sp500_scanner.py --iterations 50 --top-n 20
"""

import argparse
import json
import os
import re
import sys
import time as time_mod
import threading
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from datetime import date, timedelta

# Path setup — same pattern as other scripts
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.join(SCRIPT_DIR, "..")
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(ROOT_DIR, "backend"))

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT_DIR, "backend", ".env"))

# Reuse existing fetcher & optimizer functions
from multi_ticker_fetcher import (
    get_client,
    fetch_candles,
    fetch_vix_daily,
    safe_print,
    OUTPUT_DIR,
    LOOKBACK_MONTHS,
)
from multi_ticker_optimizer import (
    generate_combinations,
    optimize_ticker_timeframe,
    compute_score,
    print_report,
    save_json_report,
    _worker_task,
)
from stock_backtest_engine import load_ticker_csv_bars, load_vix_data

# Skip 1-minute — user requested
FREQUENCIES = [5, 10, 15, 30]
TIMEFRAMES = ["5m", "10m", "15m", "30m"]

SP500_CACHE = os.path.join(OUTPUT_DIR, "sp500_tickers.txt")
SP500_RESULTS = os.path.join(OUTPUT_DIR, "sp500_optimization_results.json")

_print_lock = threading.Lock()


# ── S&P 500 Ticker List ─────────────────────────────────────────


def fetch_sp500_tickers() -> list[str]:
    """Fetch current S&P 500 constituent tickers from Wikipedia."""
    import httpx

    print("Fetching S&P 500 ticker list from Wikipedia...")
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers = {"User-Agent": "DayTrader/1.0 (stock scanner script)"}
    resp = httpx.get(url, timeout=15.0, follow_redirects=True, headers=headers)
    resp.raise_for_status()

    # Parse the constituents table (id="constituents")
    match = re.search(
        r'<table[^>]*id="constituents"[^>]*>(.*?)</table>', resp.text, re.DOTALL
    )
    if not match:
        raise RuntimeError("Could not find S&P 500 constituents table on Wikipedia")

    table_html = match.group(1)
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.DOTALL)

    tickers = []
    for row in rows[1:]:  # skip header row
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
        if not tds:
            continue
        # First column is the ticker symbol
        text = re.sub(r"<[^>]+>", "", tds[0]).strip()
        if text and 1 <= len(text) <= 5:
            tickers.append(text.replace(".", "-"))

    tickers = sorted(set(tickers))
    print(f"  Found {len(tickers)} S&P 500 tickers")
    return tickers


def get_tickers(tickers_file: str | None = None, use_cache: bool = True) -> list[str]:
    """Get ticker list from file, cache, or Wikipedia."""
    # Custom file takes priority
    if tickers_file and os.path.exists(tickers_file):
        with open(tickers_file) as f:
            tickers = [line.strip().upper() for line in f if line.strip()]
        print(f"Loaded {len(tickers)} tickers from {tickers_file}")
        return tickers

    # Check cache
    if use_cache and os.path.exists(SP500_CACHE):
        with open(SP500_CACHE) as f:
            tickers = [line.strip() for line in f if line.strip()]
        if tickers:
            print(f"Loaded {len(tickers)} tickers from cache ({SP500_CACHE})")
            return tickers

    # Fetch from Wikipedia
    tickers = fetch_sp500_tickers()

    # Cache for future runs
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(SP500_CACHE, "w") as f:
        f.write("\n".join(tickers) + "\n")
    print(f"  Cached to {SP500_CACHE}")

    return tickers


# ── Download ─────────────────────────────────────────────────────


def ticker_has_data(ticker: str) -> bool:
    """Check if a ticker already has all required CSV files."""
    ticker_dir = os.path.join(OUTPUT_DIR, ticker)
    if not os.path.isdir(ticker_dir):
        return False
    for freq in FREQUENCIES:
        csv_path = os.path.join(ticker_dir, f"{ticker}_{freq}min_6months.csv")
        if not os.path.exists(csv_path):
            return False
    return True


def download_ticker(client, ticker: str) -> list[tuple]:
    """Download all frequencies for a single ticker (skip 1m)."""
    results = []
    safe_print(f"\n  [{ticker}] Starting download...")

    for freq in FREQUENCIES:
        safe_print(f"  [{ticker}] Fetching {freq}min candles...")
        df = fetch_candles(client, ticker, freq)

        if df.empty:
            results.append((ticker, freq, 0, 0, "NO DATA"))
            continue

        trading_days = df["Date"].nunique()
        date_range = f"{df['Date'].iloc[0]} to {df['Date'].iloc[-1]}"
        ticker_dir = os.path.join(OUTPUT_DIR, ticker)
        os.makedirs(ticker_dir, exist_ok=True)
        csv_path = os.path.join(ticker_dir, f"{ticker}_{freq}min_6months.csv")

        df.to_csv(csv_path, index=False)
        safe_print(f"  [{ticker} {freq}min] {len(df):,} rows, {trading_days} days — saved")
        results.append((ticker, freq, len(df), trading_days, date_range))

        time_mod.sleep(0.3)

    safe_print(f"  [{ticker}] Done")
    return results


def run_downloads(tickers: list[str], workers: int, skip_existing: bool):
    """Download data for all tickers in parallel."""
    if skip_existing:
        to_download = [t for t in tickers if not ticker_has_data(t)]
        skipped = len(tickers) - len(to_download)
        if skipped:
            print(f"\n  Skipping {skipped} tickers with existing data")
    else:
        to_download = tickers

    if not to_download:
        print("\n  All tickers already have data. Use --no-skip-existing to force re-download.")
        return

    print(f"\n  Downloading {len(to_download)} tickers ({', '.join(TIMEFRAMES)}) with {workers} workers...")

    client = get_client()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    summary = []
    t0 = time_mod.time()
    completed = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(download_ticker, client, ticker): ticker
            for ticker in to_download
        }

        for future in as_completed(futures):
            ticker = futures[future]
            completed += 1
            try:
                results = future.result()
                summary.extend(results)
                with _print_lock:
                    print(f"  [{completed}/{len(to_download)}] {ticker} complete")
            except Exception as e:
                with _print_lock:
                    print(f"  [{completed}/{len(to_download)}] {ticker} ERROR: {e}")

    # Download VIX daily data
    fetch_vix_daily()

    elapsed = time_mod.time() - t0
    total_files = sum(1 for _, _, rows, _, _ in summary if rows > 0)
    total_rows = sum(rows for _, _, rows, _, _ in summary)
    print(f"\n  Download complete: {total_files} files, {total_rows:,} rows in {elapsed:.1f}s ({elapsed/60:.1f} min)")


# ── Optimize ─────────────────────────────────────────────────────


def discover_tickers_with_data() -> list[str]:
    """Scan data/ directory for tickers that have CSV files."""
    tickers = []
    if not os.path.isdir(OUTPUT_DIR):
        return tickers
    for entry in sorted(os.listdir(OUTPUT_DIR)):
        ticker_dir = os.path.join(OUTPUT_DIR, entry)
        if not os.path.isdir(ticker_dir):
            continue
        # Check for at least one non-1m CSV
        for freq in FREQUENCIES:
            csv_path = os.path.join(ticker_dir, f"{entry}_{freq}min_6months.csv")
            if os.path.exists(csv_path):
                tickers.append(entry)
                break
    return tickers


def run_optimization(
    tickers: list[str],
    iterations: int,
    metric: str,
    quantity: int,
    top_n: int,
    opt_workers: int,
    days_back: int,
):
    """Run optimizer across all tickers and timeframes (skip 1m)."""
    # Filter to tickers that actually have data
    available = discover_tickers_with_data()
    tickers_to_optimize = [t for t in tickers if t in available]

    if not tickers_to_optimize:
        print("\n  No tickers with data found. Run download first.")
        return

    end_date = date.today()
    start_date = end_date - timedelta(days=days_back)

    # Build task list — all ticker/timeframe combos (skip 1m)
    tasks = [
        (ticker, tf, iterations, metric, quantity, 3, start_date, end_date)
        for ticker in tickers_to_optimize
        for tf in TIMEFRAMES
    ]

    total = len(tasks)
    print(f"\n  Optimizing {len(tickers_to_optimize)} tickers x {len(TIMEFRAMES)} timeframes = {total} combos")
    print(f"  Iterations: {iterations} | Metric: {metric} | Workers: {opt_workers}")
    print(f"  Date range: {start_date} to {end_date}")
    print()

    t0 = time_mod.time()
    all_results: list[dict] = []
    completed = 0

    with ProcessPoolExecutor(max_workers=opt_workers) as pool:
        futures = {pool.submit(_worker_task, task): task for task in tasks}

        for future in as_completed(futures):
            task = futures[future]
            ticker, tf = task[0], task[1]
            completed += 1

            try:
                top = future.result()
            except Exception as e:
                print(f"  [{completed}/{total}] {ticker} @ {tf} -> ERROR: {e}")
                continue

            if top:
                best = top[0]
                print(
                    f"  [{completed}/{total}] {ticker} @ {tf} -> "
                    f"{best['params']['signal_type']} "
                    f"PnL=${best['total_pnl']:,.2f} WR={best['win_rate']:.1f}% "
                    f"PF={best['profit_factor']:.2f}"
                )
                all_results.extend(top)
            else:
                print(f"  [{completed}/{total}] {ticker} @ {tf} -> No profitable setups")

    elapsed = time_mod.time() - t0

    print_report(all_results, top_n, metric, elapsed)
    save_json_report(all_results, SP500_RESULTS)

    return all_results


# ── CLI ──────────────────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(
        description="S&P 500 Day Trading Scanner — Download data & find best setups"
    )

    # Mode
    parser.add_argument("--download-only", action="store_true",
                        help="Only download data, skip optimization")
    parser.add_argument("--optimize-only", action="store_true",
                        help="Only optimize (assumes data exists)")

    # Ticker source
    parser.add_argument("--tickers-file", default=None,
                        help="Path to file with one ticker per line (overrides S&P 500 list)")

    # Download options
    parser.add_argument("--workers", type=int, default=4,
                        help="Concurrent download workers (default: 4)")
    parser.add_argument("--no-skip-existing", action="store_true",
                        help="Re-download even if CSV files exist")

    # Optimization options
    parser.add_argument("--iterations", type=int, default=100,
                        help="Param combos per ticker/timeframe (default: 100)")
    parser.add_argument("--metric", default="risk_adjusted",
                        choices=["total_pnl", "profit_factor", "win_rate",
                                 "composite", "risk_adjusted", "sharpe"],
                        help="Scoring metric (default: risk_adjusted)")
    parser.add_argument("--quantity", type=int, default=2,
                        help="Option contracts per trade (default: 2)")
    parser.add_argument("--top-n", type=int, default=50,
                        help="Top results in report (default: 50)")
    parser.add_argument("--opt-workers", type=int, default=0,
                        help="Optimization workers (default: CPU count)")
    parser.add_argument("--days-back", type=int, default=180,
                        help="Historical data lookback in days (default: 180)")

    return parser.parse_args()


def main():
    args = parse_args()

    print("=" * 60)
    print("  S&P 500 Day Trading Scanner")
    print("=" * 60)

    tickers = get_tickers(args.tickers_file)

    print(f"\n  Tickers: {len(tickers)}")
    print(f"  Timeframes: {', '.join(TIMEFRAMES)} (1m excluded)")

    # ── Download phase ──
    if not args.optimize_only:
        print(f"\n{'─'*60}")
        print("  PHASE 1: DOWNLOAD DATA")
        print(f"{'─'*60}")
        run_downloads(
            tickers=tickers,
            workers=args.workers,
            skip_existing=not args.no_skip_existing,
        )

    # ── Optimization phase ──
    if not args.download_only:
        print(f"\n{'─'*60}")
        print("  PHASE 2: OPTIMIZE STRATEGIES")
        print(f"{'─'*60}")
        opt_workers = args.opt_workers if args.opt_workers > 0 else os.cpu_count() or 4
        run_optimization(
            tickers=tickers,
            iterations=args.iterations,
            metric=args.metric,
            quantity=args.quantity,
            top_n=args.top_n,
            opt_workers=opt_workers,
            days_back=args.days_back,
        )

    print("\nDone!")


if __name__ == "__main__":
    main()
