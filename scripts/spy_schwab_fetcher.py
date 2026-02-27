"""
SPY Historical Minute Data Fetcher (Schwab API)
================================================
Fetches ~6 months of SPY OHLCV candle data at all minute frequencies
using the Schwab API's price_history endpoint.

SETUP:
  1. Ensure Schwab OAuth tokens are valid:
       cd backend && python -m scripts.auth_setup
  2. Run this script:
       python scripts/spy_schwab_fetcher.py

OUTPUT (in data/ directory):
  - SPY_1min_6months.csv
  - SPY_5min_6months.csv
  - SPY_10min_6months.csv
  - SPY_15min_6months.csv
  - SPY_30min_6months.csv
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

SYMBOL = "SPY"
FREQUENCIES = [1, 5, 10, 15, 30]
LOOKBACK_MONTHS = 6
CHUNK_DAYS = 14  # fetch in 2-week windows
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


def fetch_candles(client, frequency: int) -> pd.DataFrame:
    """Fetch SPY minute candles for a given frequency, chunked over ~6 months."""
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

        resp = client.price_history(
            symbol=SYMBOL,
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

        chunk_start = chunk_end
        # Small delay to avoid rate limiting
        time.sleep(0.5)

    if not all_candles:
        print(f"  WARNING: No candles returned for {frequency}-min frequency")
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
    print("  SPY Historical Minute Data Fetcher (Schwab API)")
    print("=" * 60)
    print()

    client = get_client()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    for freq in FREQUENCIES:
        print(f"\nFetching {SYMBOL} {freq}-min candles (~{LOOKBACK_MONTHS} months)...")
        df = fetch_candles(client, freq)

        if df.empty:
            continue

        trading_days = df["Date"].nunique()
        date_range = f"{df['Date'].iloc[0]} to {df['Date'].iloc[-1]}"
        spy_dir = os.path.join(OUTPUT_DIR, "SPY")
        os.makedirs(spy_dir, exist_ok=True)
        csv_name = os.path.join(spy_dir, f"SPY_{freq}min_6months.csv")

        df.to_csv(csv_name, index=False)

        print(f"  Rows: {len(df):,}  |  Trading days: {trading_days}  |  Range: {date_range}")
        print(f"  Saved: {csv_name}")

    print("\nDone!")


if __name__ == "__main__":
    main()
