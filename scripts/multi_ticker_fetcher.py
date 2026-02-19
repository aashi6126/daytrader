"""
Multi-Ticker Historical Data Fetcher (Schwab API)
==================================================
Fetches ~6 months of OHLCV candle data for multiple tickers at all minute
frequencies using the Schwab API's price_history endpoint.

SETUP:
  1. Ensure Schwab OAuth tokens are valid:
       cd backend && python -m scripts.auth_setup
  2. Run this script:
       python scripts/multi_ticker_fetcher.py

OUTPUT (in data/ directory):
  - {TICKER}_1min_6months.csv
  - {TICKER}_5min_6months.csv
  - {TICKER}_10min_6months.csv
  - {TICKER}_15min_6months.csv
  - {TICKER}_30min_6months.csv
"""

import os
import sys
import time
from datetime import datetime, timedelta, time as dtime

import pandas as pd
import pytz

# Load .env from backend/ directory
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", ".env"))

TICKERS = ["NVDA", "TSLA", "AMZN", "AMD", "AAPL", "PLTR", "MSFT", "GOOGL", "QQQ", "GLD", "ASTS", "NBIS", "CRWV", "IREN"]
FREQUENCIES = [1, 5, 10, 15, 30]
LOOKBACK_MONTHS = 6
CHUNK_DAYS = 14
ET = pytz.timezone("US/Eastern")
MARKET_OPEN = dtime(9, 30)
MARKET_CLOSE = dtime(16, 0)
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")


def get_client():
    """Create authenticated schwabdev client using existing tokens."""
    from app.config import Settings

    settings = Settings()

    if settings.SCHWAB_APP_KEY == "change-me":
        print("ERROR: SCHWAB_APP_KEY not configured.")
        print("Run: cd backend && python -m scripts.auth_setup")
        sys.exit(1)

    try:
        import schwabdev
    except ImportError:
        print("ERROR: schwabdev not installed. Run: pip install schwabdev")
        sys.exit(1)

    tokens_db = os.path.expanduser(settings.SCHWAB_TOKENS_DB)
    if not os.path.exists(tokens_db):
        print(f"ERROR: Tokens not found at {tokens_db}")
        print("Run: cd backend && python -m scripts.auth_setup")
        sys.exit(1)

    client = schwabdev.Client(
        settings.SCHWAB_APP_KEY,
        settings.SCHWAB_APP_SECRET,
        settings.SCHWAB_CALLBACK_URL,
        tokens_db=tokens_db,
    )
    print(f"Schwab client authenticated (tokens: {tokens_db})")
    return client


def fetch_candles(client, symbol: str, frequency: int) -> pd.DataFrame:
    """Fetch minute candles for a given symbol and frequency, chunked over ~6 months."""
    now = datetime.now()
    start = now - timedelta(days=LOOKBACK_MONTHS * 30)
    all_candles = []

    chunk_start = start
    chunk_num = 0
    total_chunks = (now - start).days // CHUNK_DAYS + 1

    while chunk_start < now:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS), now)
        chunk_num += 1
        print(f"    Chunk {chunk_num}/{total_chunks}: {chunk_start.strftime('%Y-%m-%d')} to {chunk_end.strftime('%Y-%m-%d')} ... ", end="", flush=True)

        try:
            resp = client.price_history(
                symbol=symbol,
                frequencyType="minute",
                frequency=frequency,
                startDate=chunk_start,
                endDate=chunk_end,
                needExtendedHoursData=False,
            )
            resp.raise_for_status()
            data = resp.json()

            candles = data.get("candles", [])
            print(f"{len(candles)} candles")
            all_candles.extend(candles)
        except Exception as e:
            print(f"ERROR: {e}")

        chunk_start = chunk_end
        time.sleep(0.5)

    if not all_candles:
        print(f"  WARNING: No candles returned for {symbol} {frequency}-min")
        return pd.DataFrame()

    df = pd.DataFrame(all_candles)

    # Convert epoch ms to ET datetime
    df["Timestamp"] = pd.to_datetime(df["datetime"], unit="ms", utc=True).dt.tz_convert(ET).dt.tz_localize(None)

    # Deduplicate (overlapping chunk boundaries)
    df = df.drop_duplicates(subset=["Timestamp"], keep="first")
    df = df.sort_values("Timestamp").reset_index(drop=True)

    # Filter to regular market hours (9:30 AM - 4:00 PM ET)
    df["_time"] = df["Timestamp"].dt.time
    df = df[(df["_time"] >= MARKET_OPEN) & (df["_time"] < MARKET_CLOSE)]
    df = df.drop(columns=["_time"])

    # Format columns to match existing CSV pattern
    df["Date"] = df["Timestamp"].dt.strftime("%Y-%m-%d")
    df["Time"] = df["Timestamp"].dt.strftime("%H:%M:%S")
    df["Open"] = df["open"].round(2)
    df["High"] = df["high"].round(2)
    df["Low"] = df["low"].round(2)
    df["Close"] = df["close"].round(2)
    df["Volume"] = df["volume"].astype(int)

    df = df[["Date", "Time", "Timestamp", "Open", "High", "Low", "Close", "Volume"]]
    return df


def main():
    print("=" * 60)
    print("  Multi-Ticker Historical Data Fetcher (Schwab API)")
    print("=" * 60)
    print(f"\n  Tickers: {', '.join(TICKERS)}")
    print(f"  Frequencies: {', '.join(str(f) + 'min' for f in FREQUENCIES)}")
    print(f"  Lookback: ~{LOOKBACK_MONTHS} months")
    print()

    client = get_client()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    summary = []

    for ticker in TICKERS:
        print(f"\n{'='*50}")
        print(f"  {ticker}")
        print(f"{'='*50}")

        for freq in FREQUENCIES:
            print(f"\n  Fetching {ticker} {freq}-min candles (~{LOOKBACK_MONTHS} months)...")
            df = fetch_candles(client, ticker, freq)

            if df.empty:
                summary.append((ticker, freq, 0, 0, "NO DATA"))
                continue

            trading_days = df["Date"].nunique()
            date_range = f"{df['Date'].iloc[0]} to {df['Date'].iloc[-1]}"
            csv_name = os.path.join(OUTPUT_DIR, f"{ticker}_{freq}min_6months.csv")

            df.to_csv(csv_name, index=False)

            print(f"  Rows: {len(df):,}  |  Days: {trading_days}  |  Range: {date_range}")
            print(f"  Saved: {csv_name}")
            summary.append((ticker, freq, len(df), trading_days, date_range))

            # Brief pause between frequencies to avoid rate limiting
            time.sleep(0.3)

        # Pause between tickers
        time.sleep(1.0)

    # Print summary table
    print(f"\n\n{'='*70}")
    print("  DOWNLOAD SUMMARY")
    print(f"{'='*70}")
    print(f"{'Ticker':<8} {'Freq':<6} {'Rows':>8} {'Days':>6}  {'Date Range'}")
    print(f"{'-'*70}")
    for ticker, freq, rows, days, rng in summary:
        print(f"{ticker:<8} {freq}min{'':<3} {rows:>8,} {days:>6}  {rng}")

    total_files = sum(1 for _, _, rows, _, _ in summary if rows > 0)
    total_rows = sum(rows for _, _, rows, _, _ in summary)
    print(f"\nTotal: {total_files} files, {total_rows:,} rows")
    print("Done!")


if __name__ == "__main__":
    main()
