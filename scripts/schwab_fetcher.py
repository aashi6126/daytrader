"""
Single-Symbol Historical Data Fetcher (Schwab API)
====================================================
Downloads ~6 months of OHLCV candle data for ONE ticker at all minute
frequencies. Designed to be called as a subprocess from the backend API.

USAGE:
  python scripts/schwab_fetcher.py AAPL
  python scripts/schwab_fetcher.py TSLA
"""

import json
import os
import sys

# Reuse multi_ticker_fetcher logic
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", ".env"))

from multi_ticker_fetcher import FREQUENCIES, OUTPUT_DIR, fetch_candles, get_client


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "Usage: schwab_fetcher.py <SYMBOL>"}))
        sys.exit(1)

    symbol = sys.argv[1].upper()
    results = []

    try:
        client = get_client()
    except SystemExit:
        print(json.dumps({"ok": False, "error": "Schwab client not authenticated"}))
        sys.exit(1)

    ticker_dir = os.path.join(OUTPUT_DIR, symbol)
    os.makedirs(ticker_dir, exist_ok=True)

    for freq in FREQUENCIES:
        print(f"Fetching {symbol} {freq}-min candles...", file=sys.stderr)
        df = fetch_candles(client, symbol, freq)

        if df.empty:
            results.append({"freq": freq, "rows": 0, "days": 0})
            continue

        csv_name = os.path.join(ticker_dir, f"{symbol}_{freq}min_6months.csv")
        df.to_csv(csv_name, index=False)
        results.append({
            "freq": freq,
            "rows": len(df),
            "days": df["Date"].nunique(),
        })

    total_rows = sum(r["rows"] for r in results)
    total_files = sum(1 for r in results if r["rows"] > 0)

    output = {
        "ok": True,
        "symbol": symbol,
        "files": total_files,
        "total_rows": total_rows,
        "frequencies": results,
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
