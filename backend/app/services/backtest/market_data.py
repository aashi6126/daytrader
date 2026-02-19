"""Fetch historical SPY and VIX data via yfinance or local CSV for backtesting."""

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import pandas as pd
import pytz
import yfinance as yf

logger = logging.getLogger(__name__)
ET = pytz.timezone("US/Eastern")

MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)

# Resolve data directory relative to project root
_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "data")


@dataclass
class BarData:
    timestamp: datetime  # ET-aware
    open: float
    high: float
    low: float
    close: float
    volume: int


def fetch_spy_bars(
    start_date: date,
    end_date: date,
    interval: str = "5m",
) -> dict[date, list[BarData]]:
    """Download SPY intraday bars grouped by trading day.

    yfinance limits: 1m ~7 days, 5m ~60 days.
    Only bars within market hours (9:30-16:00 ET) are included.
    """
    ticker = yf.Ticker("SPY")
    df = ticker.history(
        start=start_date.isoformat(),
        end=(end_date + timedelta(days=1)).isoformat(),
        interval=interval,
    )

    if df.empty:
        logger.warning(f"No SPY data for {start_date} to {end_date}")
        return {}

    if df.index.tz is None:
        df.index = df.index.tz_localize("US/Eastern")
    else:
        df.index = df.index.tz_convert("US/Eastern")

    bars_by_day: dict[date, list[BarData]] = {}

    for ts, row in df.iterrows():
        ts_et = ts.to_pydatetime()
        if not (MARKET_OPEN <= ts_et.time() < MARKET_CLOSE):
            continue

        trade_date = ts_et.date()
        bar = BarData(
            timestamp=ts_et,
            open=float(row["Open"]),
            high=float(row["High"]),
            low=float(row["Low"]),
            close=float(row["Close"]),
            volume=int(row["Volume"]),
        )
        bars_by_day.setdefault(trade_date, []).append(bar)

    for day in bars_by_day:
        bars_by_day[day].sort(key=lambda b: b.timestamp)

    logger.info(
        f"Fetched SPY {interval}: {len(bars_by_day)} days, "
        f"{sum(len(v) for v in bars_by_day.values())} bars"
    )
    return bars_by_day


def load_csv_bars(
    start_date: date,
    end_date: date,
    interval: str = "5m",
) -> dict[date, list[BarData]]:
    """Load SPY bars from local CSV files (Schwab data, up to 6 months).

    CSV format: Date,Time,Timestamp,Open,High,Low,Close,Volume
    Files live in the project data/ directory.
    """
    interval_map = {"1m": "1min", "5m": "5min", "10m": "10min", "15m": "15min", "30m": "30min"}
    csv_label = interval_map.get(interval, interval.replace("m", "min"))
    csv_path = os.path.normpath(os.path.join(_DATA_DIR, f"SPY_{csv_label}_6months.csv"))

    if not os.path.exists(csv_path):
        logger.warning(f"CSV not found: {csv_path}, falling back to yfinance")
        return fetch_spy_bars(start_date, end_date, interval)

    df = pd.read_csv(csv_path, parse_dates=["Timestamp"])
    logger.info(f"Loaded {len(df)} rows from {csv_path}")

    bars_by_day: dict[date, list[BarData]] = {}

    for _, row in df.iterrows():
        ts_naive = row["Timestamp"].to_pydatetime()
        trade_date = ts_naive.date()

        if trade_date < start_date or trade_date > end_date:
            continue

        ts_et = ET.localize(ts_naive)

        bar = BarData(
            timestamp=ts_et,
            open=float(row["Open"]),
            high=float(row["High"]),
            low=float(row["Low"]),
            close=float(row["Close"]),
            volume=int(row["Volume"]),
        )
        bars_by_day.setdefault(trade_date, []).append(bar)

    for day in bars_by_day:
        bars_by_day[day].sort(key=lambda b: b.timestamp)

    logger.info(
        f"CSV SPY {interval}: {len(bars_by_day)} days, "
        f"{sum(len(v) for v in bars_by_day.values())} bars "
        f"({start_date} to {end_date})"
    )
    return bars_by_day


def fetch_vix_daily(start_date: date, end_date: date) -> dict[date, float]:
    """Download VIX daily close (annualized IV as percentage, e.g. 18.5)."""
    try:
        ticker = yf.Ticker("^VIX")
        df = ticker.history(
            start=start_date.isoformat(),
            end=(end_date + timedelta(days=1)).isoformat(),
            interval="1d",
        )

        if df.empty:
            logger.warning("No VIX data from yfinance, using default")
            return {}

        vix_by_day: dict[date, float] = {}
        for ts, row in df.iterrows():
            vix_by_day[ts.to_pydatetime().date()] = float(row["Close"])

        return vix_by_day
    except Exception as e:
        logger.warning(f"VIX fetch failed ({e}), using default VIX=18.0")
        return {}
