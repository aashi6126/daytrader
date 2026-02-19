"""
SPY OHLCV Data Fetcher (30 Trading Days)
=========================================
Uses yfinance to pull intraday candle data for SPY.

SETUP:
  1. Install dependencies:  pip install yfinance pandas openpyxl
  2. Run:                    python spy_data_fetcher.py

NOTE:
  yfinance limits: 1-min bars → ~7 days, 5-min bars → ~60 days.
  Default is 5-min for 30 trading days. Set INTERVAL = "1m" for
  1-min bars (will only get ~5-7 trading days).

OUTPUT:
  - SPY_{interval}_30days.csv
  - SPY_{interval}_30days.xlsx
"""

import yfinance as yf
from datetime import time as dtime

SYMBOL = "SPY"
INTERVAL = "5m"  # "1m" for 1-minute (max ~7 days), "5m" for 5-minute (max ~60 days)
TRADING_DAYS = 30

# yfinance max lookback depends on interval
lookback_days = {
    "1m": 7,
    "2m": 60,
    "5m": 60,
}
max_period = {
    "1m": "5d",
    "2m": "60d",
    "5m": "60d",
}
period = max_period.get(INTERVAL, "60d")

print(f"Fetching {SYMBOL} {INTERVAL} bars (period={period})...")

ticker = yf.Ticker(SYMBOL)
df = ticker.history(period=period, interval=INTERVAL)

if df.empty:
    print("\nNo data retrieved. Check your internet connection and try again.")
    exit(1)

print(f"  Raw records: {len(df)}")

# Reset index to get Datetime as a column
df = df.reset_index()

# The column name varies: "Datetime" for intraday, "Date" for daily
ts_col = "Datetime" if "Datetime" in df.columns else "Date"
df = df.rename(columns={ts_col: "Timestamp"})

# Ensure timezone-naive (yfinance returns tz-aware in ET)
if df["Timestamp"].dt.tz is not None:
    df["Timestamp"] = df["Timestamp"].dt.tz_localize(None)

# Filter to market hours: 9:30 AM - 4:00 PM ET
df["_time"] = df["Timestamp"].dt.time
market_open = dtime(9, 30)
market_close = dtime(16, 0)
df = df[(df["_time"] >= market_open) & (df["_time"] <= market_close)]
df = df.drop(columns=["_time"])

# Keep standard OHLCV columns
df = df[["Timestamp", "Open", "High", "Low", "Close", "Volume"]]
df["Volume"] = df["Volume"].astype(int)

# Keep only last N trading days
trading_days = sorted(df["Timestamp"].dt.date.unique())
print(f"\nTotal trading days available: {len(trading_days)}")

last_n_days = trading_days[-TRADING_DAYS:]
df = df[df["Timestamp"].dt.date.isin(last_n_days)]

# Add separate Date and Time columns
df["Date"] = df["Timestamp"].dt.strftime("%Y-%m-%d")
df["Time"] = df["Timestamp"].dt.strftime("%H:%M:%S")
df = df[["Date", "Time", "Timestamp", "Open", "High", "Low", "Close", "Volume"]]

print(f"Final dataset: {len(df)} rows across {len(last_n_days)} trading days")
print(f"Date range: {last_n_days[0]} to {last_n_days[-1]}")
print(f"\nSample data:")
print(df.head(10).to_string(index=False))

# Save outputs
interval_label = INTERVAL.replace("m", "min")
csv_name = f"SPY_{interval_label}_{len(last_n_days)}days.csv"
xlsx_name = f"SPY_{interval_label}_{len(last_n_days)}days.xlsx"

df.to_csv(csv_name, index=False)
df.to_excel(xlsx_name, index=False, sheet_name=f"SPY {interval_label} Data")

print(f"\nFiles saved:")
print(f"  {csv_name}")
print(f"  {xlsx_name}")
print(f"\nDone! You can now upload the CSV/XLSX back to Claude or import into Google Sheets.")
