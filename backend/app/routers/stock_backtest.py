"""Multi-ticker options backtest & optimization API endpoints."""

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time as _time
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import FavoriteStrategy

logger = logging.getLogger(__name__)
router = APIRouter()

# Add scripts/ to path so we can import stock_backtest_engine
_SCRIPTS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts"))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

_DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "data"))

ALL_TIMEFRAMES = ["1m", "5m", "10m", "15m", "30m"]

# Common symbols for search (beyond what's downloaded)
_COMMON_SYMBOLS = [
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "NVDA", "META", "TSLA", "BRK.B",
    "UNH", "JNJ", "V", "XOM", "WMT", "JPM", "MA", "PG", "HD", "CVX", "MRK",
    "ABBV", "LLY", "PEP", "KO", "COST", "AVGO", "TMO", "MCD", "CSCO", "ACN",
    "ABT", "DHR", "NKE", "TXN", "NEE", "PM", "HON", "UPS", "UNP", "ORCL",
    "AMD", "PLTR", "QQQ", "SPY", "IWM", "DIA", "GLD", "SLV", "TLT", "XLF",
    "ASTS", "NBIS", "CRWV", "IREN", "MARA", "COIN", "SQ", "SHOP", "SNOW",
    "NET", "DDOG", "CRM", "NOW", "PANW", "ZS", "CRWD", "MNDY", "TEAM",
    "ROKU", "TTD", "PINS", "SNAP", "RBLX", "U", "ABNB", "DASH", "UBER",
    "LYFT", "RIVN", "LCID", "NIO", "XPEV", "LI", "BABA", "JD", "PDD",
    "ARM", "SMCI", "MSTR", "SOFI", "AFRM", "HOOD", "UPST",
]


# ── Market Cap Tiers ─────────────────────────────────────────────
# Approximate market cap categories (as of early 2026).
# Mega:  >$200B   Large: $50-200B   Mid: $10-50B   Small: $2-10B
# Everything else defaults to "small" if not listed.

_MEGA_CAP = {
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "NVDA", "META", "TSLA", "AVGO",
    "LLY", "WMT", "JPM", "V", "UNH", "XOM", "MA", "COST", "ORCL", "HD",
    "PG", "JNJ", "ABBV", "NFLX", "CRM", "BAC", "CVX", "MRK", "KO", "PEP",
    "TMO", "AMD", "LIN", "ACN", "MCD", "CSCO", "ABT", "WFC", "GE", "NOW",
    "ADBE", "ISRG", "PM", "GS", "TXN", "INTU", "BKNG", "QCOM", "AXP", "CAT",
    "BX", "MS", "PFE", "AMGN", "RTX", "NEE", "T", "UNP", "DHR", "HON",
    "IBM", "UBER", "BLK", "LOW", "PGR", "SPGI", "COP", "ANET", "PLTR", "TJX",
    "SYK", "GEV", "DE", "FI", "C", "BSX", "VRTX", "ADP", "SCHW", "MMC",
    "ELV", "BMY", "SBUX", "MDLZ", "ADI", "LRCX", "GILD", "CB", "APP",
    "ETN", "PANW", "CME", "KLAC", "CEG", "SPY", "QQQ",
}

_LARGE_CAP = {
    "CRWD", "ABNB", "TT", "PH", "MO", "SO", "MCO", "CI", "USB", "SNPS",
    "CDNS", "SHW", "ICE", "DUK", "MCK", "CL", "PYPL", "CMG", "CTAS", "WELL",
    "MSI", "TDG", "APH", "COIN", "EOG", "EMR", "ORLY", "PNC", "FDX", "AON",
    "NOC", "MAR", "AJG", "GD", "REGN", "RCL", "TGT", "HCA", "OKE", "ECL",
    "DASH", "AFL", "APD", "BK", "SRE", "PSA", "KMI", "SLB", "TFC", "SPG",
    "DLR", "AMP", "FANG", "PCAR", "ALL", "NSC", "ROP", "FICO", "CPAY",
    "FTNT", "MET", "KDP", "HLT", "O", "AZO", "MSCI", "GM", "AEP", "URI",
    "HWM", "D", "MCHP", "LHX", "AIG", "FIS", "PRU", "VST", "GWW", "PSX",
    "ROST", "NEM", "CCI", "F", "PCG", "FAST", "VLO", "KVUE", "LEN", "PWR",
    "DAL", "IT", "CRH", "CBRE", "BKR", "RSG", "EXC", "GEHC", "CMI", "DHI",
    "TPL", "YUM", "NDAQ", "MNST", "VRSK", "EA", "XEL", "DG", "SYY", "ACGL",
    "DD", "PEG", "TMUS", "WMB", "STZ", "GPN", "AXON", "IR", "ODFL",
    "LULU", "CTSH", "CHTR", "A", "EW", "PPG", "GLW", "WEC", "DDOG", "EXE",
    "FITB", "WAB", "IQV", "MLM", "NXPI", "TTWO", "AVB", "EBAY", "XYL",
    "LII", "TTD", "HPE", "KR", "BR", "DOW", "VMC", "EQR", "HOOD", "GIS",
    "VLTO", "TROW", "DECK", "CVNA", "HBAN", "WTW", "EFX", "IDXX", "IRM",
    "ROK", "SBAC", "VICI", "ETR", "ED", "TSCO", "DELL", "HPQ", "DPZ",
    "BLDR", "WAT", "ON", "ANSS", "TRV", "STT", "SMCI", "GDDY", "EIX",
    "NET", "WDAY", "ZS",
}

_MID_CAP = {
    "NVR", "BRO", "COO", "FE", "TER", "WRB", "CINF", "HOLX", "DTE", "PPL",
    "AWK", "WY", "GEN", "MKC", "ARE", "DRI", "STLD", "HIG", "SNA", "NTAP",
    "CDW", "KEYS", "TRGP", "PFG", "PHM", "CLX", "CPRT", "DOV", "LUV",
    "SWK", "BBY", "ADM", "SW", "CSGP", "ERIE", "K", "FTV", "NTRS", "INCY",
    "TYL", "AEE", "BEN", "BALL", "LNT", "PKG", "NI", "STE", "IEX",
    "ESS", "WDC", "HAL", "CF", "PODD", "CMS", "L", "WST", "UDR", "J",
    "CIEN", "JKHY", "RL", "EPAM", "LH", "BXP", "FOXA", "KIM", "PNW",
    "MKTX", "REG", "CNP", "EME", "IP", "SWKS", "MOH", "FSLR", "TECH",
    "ALGN", "PTC", "FIX", "HUBB", "NRG", "ALLE", "EXPD", "ROL", "RVTY",
    "VTR", "LW", "GPC", "KMB", "HST", "GRMN", "AMAT", "DAL", "MPC",
    "APO", "CAH", "ARW", "POOL", "TPR", "BAX", "LDOS", "JBHT", "RJF",
    "EXPE", "CFG", "MTD", "IFF", "CARR", "PAYC", "PAYX", "HSY", "CTVA",
    "TKO", "EG", "ATO", "DOC", "LYV", "AMCR", "CCL", "BG", "RF", "DGX",
    "COR", "WBD", "UAL", "NCLH", "MAA", "STX", "MOS", "ZBRA", "TDY",
    "MPWR", "EVRG", "DVN", "AKAM", "SJM", "AIZ", "CRL", "CAG", "WSM",
    "TEL", "PNR", "INVH", "PLD", "SOLV", "DVA", "HII", "GL", "FOX",
    "CHRW", "CPT", "RMD", "HRL", "EXR", "CPB", "BIIB", "HAS", "FRT",
    "AES", "FFIV", "JBL", "VTRS", "DXC", "WM", "ULTA",
}

_SMALL_CAP = {
    "ASTS", "NBIS", "CRWV", "IREN", "PSKY",
}

# ETFs get their own tier
_ETF = {
    "SPY", "QQQ", "GLD", "IWM", "DIA", "SLV", "TLT", "XLF",
}

MARKET_CAP_TIERS: dict[str, str] = {}
for _sym in _MEGA_CAP:
    MARKET_CAP_TIERS[_sym] = "mega"
for _sym in _LARGE_CAP:
    MARKET_CAP_TIERS[_sym] = "large"
for _sym in _MID_CAP:
    MARKET_CAP_TIERS[_sym] = "mid"
for _sym in _SMALL_CAP:
    MARKET_CAP_TIERS[_sym] = "small"
for _sym in _ETF:
    MARKET_CAP_TIERS[_sym] = "etf"

# Anything not listed defaults to "mid"
_DEFAULT_TIER = "mid"

ALL_TIER_LABELS = {
    "all": "All",
    "mega": "Mega Cap (>$200B)",
    "large": "Large Cap ($50-200B)",
    "mid": "Mid Cap ($10-50B)",
    "small": "Small Cap (<$10B)",
    "etf": "ETFs",
}


def _get_ticker_tier(ticker: str) -> str:
    return MARKET_CAP_TIERS.get(ticker, _DEFAULT_TIER)


def _filter_tickers_by_tier(tickers: list[str], tier: str) -> list[str]:
    if tier == "all":
        return tickers
    return [t for t in tickers if _get_ticker_tier(t) == tier]


def _scan_available_tickers() -> list[str]:
    """Scan data/ subdirectories for tickers that have CSV data."""
    if not os.path.isdir(_DATA_DIR):
        return []
    tickers = []
    for name in sorted(os.listdir(_DATA_DIR)):
        subdir = os.path.join(_DATA_DIR, name)
        if os.path.isdir(subdir) and any(
            f.endswith("_6months.csv") for f in os.listdir(subdir)
        ):
            tickers.append(name)
    return tickers


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Schemas ───────────────────────────────────────────────────────


class StockBacktestRequest(BaseModel):
    ticker: str = Field("NVDA", description="Ticker symbol")
    start_date: date
    end_date: date
    signal_type: str = Field("ema_cross")
    ema_fast: int = Field(8, ge=2, le=50)
    ema_slow: int = Field(21, ge=5, le=200)
    bar_interval: str = Field("5m", description="1m | 5m | 10m | 15m | 30m")
    rsi_period: int = Field(0, ge=0, le=50)
    rsi_ob: float = Field(70.0)
    rsi_os: float = Field(30.0)
    orb_minutes: int = Field(15, ge=5, le=60)
    atr_period: int = Field(0, ge=0, le=50)
    atr_stop_mult: float = Field(2.0)
    afternoon_enabled: bool = True
    quantity: int = Field(2, ge=1, le=100)
    stop_loss_percent: float = Field(16.0, ge=1, le=100)
    profit_target_percent: float = Field(40.0, ge=1, le=500)
    trailing_stop_percent: float = Field(20.0, ge=1, le=100)
    max_hold_minutes: int = Field(90, ge=1, le=300)
    min_confluence: int = Field(5, ge=3, le=6)
    vol_threshold: float = Field(1.5, ge=1.0, le=3.0)
    orb_body_min_pct: float = Field(0.0, ge=0.0, le=1.0)
    orb_vwap_filter: bool = False
    orb_gap_fade_filter: bool = False
    orb_stop_mult: float = Field(1.0, ge=0.25, le=3.0)
    orb_target_mult: float = Field(1.5, ge=0.5, le=5.0)
    # Bollinger Bands & MACD
    bb_period: int = Field(20, ge=5, le=50)
    bb_std_mult: float = Field(2.0, ge=1.0, le=4.0)
    macd_fast: int = Field(12, ge=5, le=30)
    macd_slow: int = Field(26, ge=10, le=50)
    macd_signal_period: int = Field(9, ge=3, le=20)

    max_daily_trades: int = Field(10, ge=1, le=50)
    max_daily_loss: float = Field(2000.0, ge=50, le=5000)
    max_consecutive_losses: int = Field(3, ge=1, le=10)

    vix_min: float = Field(0.0, ge=0, le=100, description="Min VIX to trade (0=disabled)")
    vix_max: float = Field(100.0, ge=0, le=100, description="Max VIX to trade (100=disabled)")
    exit_slippage_percent: float = Field(0.0, ge=0, le=5.0, description="Exit slippage percent (flat fallback)")
    spread_model_enabled: bool = Field(True, description="Dynamic bid-ask spread model (overrides flat slippage)")
    entry_confirm_minutes: int = Field(0, ge=0, le=15, description="Minutes of 1m bars to confirm entry (0=immediate)")

    # Pivot point S/R
    pivot_enabled: bool = Field(False, description="Enable pivot point S/R levels")
    pivot_proximity_pct: float = Field(0.3, ge=0.1, le=1.0, description="% proximity to pivot level")
    pivot_filter_enabled: bool = Field(False, description="Block signals that fight S/R")


class StockTradeResponse(BaseModel):
    trade_date: str
    ticker: str
    direction: str
    strike: float
    entry_time: str
    entry_price: float
    exit_time: Optional[str]
    exit_price: Optional[float]
    exit_reason: Optional[str]
    highest_price_seen: float
    pnl_dollars: Optional[float]
    pnl_percent: Optional[float]
    hold_minutes: Optional[float]
    quantity: int
    underlying_price: Optional[float] = None
    expiry_date: Optional[str] = None
    dte: int = 0
    delta: Optional[float] = None
    entry_reason: Optional[str] = None
    exit_detail: Optional[str] = None


class StockDayResponse(BaseModel):
    trade_date: str
    pnl: float
    total_trades: int
    winning_trades: int
    losing_trades: int


class StockSummaryResponse(BaseModel):
    total_pnl: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    largest_win: float
    largest_loss: float
    max_drawdown: float
    profit_factor: float
    avg_hold_minutes: float
    exit_reasons: dict[str, int]
    avg_entry_price: float = 0.0
    max_entry_price: float = 0.0


class StockBacktestResponse(BaseModel):
    summary: StockSummaryResponse
    days: list[StockDayResponse]
    trades: list[StockTradeResponse]


class StockOptimizeRequest(BaseModel):
    ticker: str = Field("NVDA")
    bar_interval: str = Field("5m")
    num_iterations: int = Field(200, ge=10, le=2000)
    target_metric: str = Field("risk_adjusted")
    top_n: int = Field(10, ge=1, le=50)
    quantity: int = Field(2, ge=1, le=100)


class StockOptimizeResultEntry(BaseModel):
    rank: int
    ticker: str
    timeframe: str
    params: dict
    total_pnl: float
    total_trades: int
    win_rate: float
    profit_factor: float
    max_drawdown: float
    avg_hold_minutes: float
    avg_win: float
    avg_loss: float
    largest_win: float
    largest_loss: float
    score: float
    exit_reasons: dict[str, int]
    days_traded: int
    avg_entry_price: float = 0.0
    max_entry_price: float = 0.0
    # Out-of-sample (walk-forward) metrics
    oos_total_pnl: Optional[float] = None
    oos_total_trades: Optional[int] = None
    oos_win_rate: Optional[float] = None
    oos_profit_factor: Optional[float] = None
    oos_max_drawdown: Optional[float] = None
    oos_score: Optional[float] = None
    # Monte Carlo bootstrap confidence
    mc_win_pct: Optional[float] = None
    mc_median_pnl: Optional[float] = None
    mc_p5_pnl: Optional[float] = None
    mc_p95_pnl: Optional[float] = None
    # Market cap tier
    market_cap_tier: Optional[str] = None


class StockOptimizeResponse(BaseModel):
    total_combinations_tested: int
    elapsed_seconds: float
    target_metric: str
    results: list[StockOptimizeResultEntry]


class TickerInfo(BaseModel):
    ticker: str
    timeframes: list[str]


class SearchResult(BaseModel):
    symbol: str
    has_data: bool


class DownloadResponse(BaseModel):
    ok: bool
    symbol: str = ""
    message: str = ""
    files: int = 0
    total_rows: int = 0


class FavoriteStrategyRequest(BaseModel):
    ticker: str
    strategy_name: str
    direction: Optional[str] = None
    params: dict
    summary: Optional[dict] = None
    notes: Optional[str] = None


class FavoriteStrategyResponse(BaseModel):
    id: int
    ticker: str
    strategy_name: str
    direction: Optional[str] = None
    params: dict
    summary: Optional[dict] = None
    notes: Optional[str] = None
    created_at: str


class BatchOptimizeRequest(BaseModel):
    iterations: int = Field(200, ge=10, le=2000)
    metric: str = Field("risk_adjusted")
    min_trades: int = Field(40, ge=1, le=200)
    market_cap_tier: str = Field("all", description="Filter by market cap tier: all, mega, large, mid, small, etf")
    tickers: Optional[list[str]] = Field(None, description="Specific tickers to optimize. If None, scan all available.")


class BatchOptimizeStatusResponse(BaseModel):
    status: str = "idle"  # "idle" | "running" | "completed" | "failed"
    progress: str = ""
    elapsed_seconds: float = 0
    results_count: int = 0
    error: str = ""


# ── Batch optimize job state ─────────────────────────────────────

_batch_job: dict = {
    "status": "idle",
    "progress": "",
    "elapsed_seconds": 0.0,
    "results_count": 0,
    "error": "",
    "lock": threading.Lock(),
}


def _run_batch_optimize(iterations: int, metric: str, min_trades: int, market_cap_tier: str = "all", tickers_filter: Optional[list[str]] = None):
    """Worker function for batch optimization. Runs in a background thread."""
    from multi_ticker_optimizer import optimize_ticker_timeframe
    from stock_backtest_engine import load_ticker_csv_bars, load_vix_data

    t0 = _time.time()
    try:
        # Use explicit ticker list if provided, otherwise scan and filter by tier
        if tickers_filter:
            available = set(_scan_available_tickers())
            tickers = [t for t in tickers_filter if t in available]
        else:
            tickers = _scan_available_tickers()
            tickers = _filter_tickers_by_tier(tickers, market_cap_tier)
        if not tickers:
            with _batch_job["lock"]:
                _batch_job["status"] = "failed"
                _batch_job["error"] = f"No tickers with CSV data found" + (f" for {tickers_filter}" if tickers_filter else f" for tier '{market_cap_tier}'")
            return

        # Build task list: each ticker × each available timeframe
        tasks = []
        for ticker in tickers:
            ticker_dir = os.path.join(_DATA_DIR, ticker)
            for tf in ALL_TIMEFRAMES:
                label = tf.replace("m", "min")
                csv_path = os.path.join(ticker_dir, f"{ticker}_{label}_6months.csv")
                if os.path.exists(csv_path):
                    tasks.append((ticker, tf))

        total = len(tasks)
        all_results: list[dict] = []
        json_path = os.path.join(_DATA_DIR, "optimization_results.json")
        os.makedirs(_DATA_DIR, exist_ok=True)

        def _flush_results():
            """Write current results to disk so the UI can show partial progress."""
            sorted_results = sorted(
                all_results, key=lambda x: x.get("score", 0), reverse=True
            )
            with open(json_path, "w") as fp:
                json.dump({
                    "generated": date.today().isoformat(),
                    "total_results": len(sorted_results),
                    "status": "running",
                    "results": sorted_results,
                }, fp, indent=2, default=str)

        for i, (ticker, tf) in enumerate(tasks):
            with _batch_job["lock"]:
                _batch_job["progress"] = f"{i}/{total} ({ticker} @ {tf})"
                _batch_job["elapsed_seconds"] = round(_time.time() - t0, 1)

            try:
                bars_by_day = load_ticker_csv_bars(
                    ticker, date(2000, 1, 1), date(2099, 12, 31), tf
                )
                if not bars_by_day:
                    continue

                dates = sorted(bars_by_day.keys())
                vix_by_day = load_vix_data(dates[0], dates[-1])

                top = optimize_ticker_timeframe(
                    ticker=ticker,
                    timeframe=tf,
                    bars_by_day=bars_by_day,
                    iterations=iterations,
                    metric=metric,
                    quantity=2,
                    top_n=3,
                    vix_by_day=vix_by_day,
                )

                # Filter by min_trades, tag with market cap tier
                added = 0
                for entry in top:
                    if entry["total_trades"] >= min_trades:
                        entry["market_cap_tier"] = _get_ticker_tier(ticker)
                        all_results.append(entry)
                        added += 1

                # Flush to disk after each ticker/tf so UI shows incremental results
                if added > 0:
                    with _batch_job["lock"]:
                        _batch_job["results_count"] = len(all_results)
                    _flush_results()

            except Exception as e:
                logger.warning(f"Batch optimize {ticker}@{tf} failed: {e}")
                continue

        # Final write with completed status
        sorted_results = sorted(
            all_results, key=lambda x: x.get("score", 0), reverse=True
        )
        with open(json_path, "w") as f:
            json.dump({
                "generated": date.today().isoformat(),
                "total_results": len(sorted_results),
                "status": "completed",
                "results": sorted_results,
            }, f, indent=2, default=str)

        with _batch_job["lock"]:
            _batch_job["status"] = "completed"
            _batch_job["progress"] = f"{total}/{total}"
            _batch_job["elapsed_seconds"] = round(_time.time() - t0, 1)
            _batch_job["results_count"] = len(all_results)

        logger.info(
            f"Batch optimize completed: {len(all_results)} results from "
            f"{total} ticker/timeframe combos in {_time.time() - t0:.1f}s"
        )

    except Exception as e:
        logger.exception("Batch optimize failed")
        with _batch_job["lock"]:
            _batch_job["status"] = "failed"
            _batch_job["error"] = str(e)
            _batch_job["elapsed_seconds"] = round(_time.time() - t0, 1)


# ── Endpoints ─────────────────────────────────────────────────────


@router.get("/stock-backtest/tiers")
def get_market_cap_tiers():
    """Return available market cap tiers with ticker counts."""
    tickers = _scan_available_tickers()
    counts: dict[str, int] = {"all": len(tickers)}
    for t in tickers:
        tier = _get_ticker_tier(t)
        counts[tier] = counts.get(tier, 0) + 1
    return [
        {"value": k, "label": v, "count": counts.get(k, 0)}
        for k, v in ALL_TIER_LABELS.items()
    ]


@router.get("/stock-backtest/tickers", response_model=list[TickerInfo])
def get_available_tickers():
    """Return list of tickers with available CSV data (scanned dynamically)."""
    downloaded = _scan_available_tickers()
    result = []
    for ticker in downloaded:
        ticker_dir = os.path.join(_DATA_DIR, ticker)
        available_tf = []
        for tf in ALL_TIMEFRAMES:
            label = tf.replace("m", "min")
            csv_path = os.path.join(ticker_dir, f"{ticker}_{label}_6months.csv")
            if os.path.exists(csv_path):
                available_tf.append(tf)
        if available_tf:
            result.append(TickerInfo(ticker=ticker, timeframes=available_tf))
    return result


@router.post("/stock-backtest/run", response_model=StockBacktestResponse)
def run_stock_backtest_endpoint(body: StockBacktestRequest):
    """Run a single options-level backtest for a ticker."""
    from stock_backtest_engine import StockBacktestParams, run_stock_backtest

    if body.end_date < body.start_date:
        raise HTTPException(400, "end_date must be >= start_date")

    downloaded = _scan_available_tickers()
    if body.ticker.upper() not in downloaded:
        raise HTTPException(400, f"No data for {body.ticker}. Download it first.")

    params = StockBacktestParams(
        start_date=body.start_date,
        end_date=body.end_date,
        ticker=body.ticker.upper(),
        signal_type=body.signal_type,
        ema_fast=body.ema_fast,
        ema_slow=body.ema_slow,
        bar_interval=body.bar_interval,
        rsi_period=body.rsi_period,
        rsi_ob=body.rsi_ob,
        rsi_os=body.rsi_os,
        orb_minutes=body.orb_minutes,
        atr_period=body.atr_period,
        atr_stop_mult=body.atr_stop_mult,
        afternoon_enabled=body.afternoon_enabled,
        quantity=body.quantity,
        stop_loss_percent=body.stop_loss_percent,
        profit_target_percent=body.profit_target_percent,
        trailing_stop_percent=body.trailing_stop_percent,
        max_hold_minutes=body.max_hold_minutes,
        min_confluence=body.min_confluence,
        vol_threshold=body.vol_threshold,
        bb_period=body.bb_period,
        bb_std_mult=body.bb_std_mult,
        macd_fast=body.macd_fast,
        macd_slow=body.macd_slow,
        macd_signal_period=body.macd_signal_period,
        orb_body_min_pct=body.orb_body_min_pct,
        orb_vwap_filter=body.orb_vwap_filter,
        orb_gap_fade_filter=body.orb_gap_fade_filter,
        orb_stop_mult=body.orb_stop_mult,
        orb_target_mult=body.orb_target_mult,
        max_daily_trades=body.max_daily_trades,
        max_daily_loss=body.max_daily_loss,
        max_consecutive_losses=body.max_consecutive_losses,
        vix_min=body.vix_min,
        vix_max=body.vix_max,
        exit_slippage_percent=body.exit_slippage_percent,
        spread_model_enabled=body.spread_model_enabled,
        entry_confirm_minutes=body.entry_confirm_minutes,
        pivot_enabled=body.pivot_enabled,
        pivot_proximity_pct=body.pivot_proximity_pct,
        pivot_filter_enabled=body.pivot_filter_enabled,
    )

    try:
        result = run_stock_backtest(params)
    except Exception as e:
        logger.exception("Stock backtest failed")
        raise HTTPException(500, f"Backtest failed: {str(e)}")

    return StockBacktestResponse(
        summary=StockSummaryResponse(
            total_pnl=result.total_pnl,
            total_trades=result.total_trades,
            winning_trades=result.winning_trades,
            losing_trades=result.losing_trades,
            win_rate=result.win_rate,
            avg_win=result.avg_win,
            avg_loss=result.avg_loss,
            largest_win=result.largest_win,
            largest_loss=result.largest_loss,
            max_drawdown=result.max_drawdown,
            profit_factor=result.profit_factor,
            avg_hold_minutes=result.avg_hold_minutes,
            exit_reasons=result.exit_reasons,
            avg_entry_price=result.avg_entry_price,
            max_entry_price=result.max_entry_price,
        ),
        days=[
            StockDayResponse(
                trade_date=d.trade_date.isoformat(),
                pnl=d.pnl,
                total_trades=len(d.trades),
                winning_trades=d.winning_trades,
                losing_trades=d.losing_trades,
            )
            for d in result.days
        ],
        trades=[
            StockTradeResponse(
                trade_date=t.trade_date.isoformat(),
                ticker=t.ticker,
                direction=t.direction,
                strike=t.strike,
                entry_time=t.entry_time.isoformat(),
                entry_price=t.entry_price,
                exit_time=t.exit_time.isoformat() if t.exit_time else None,
                exit_price=t.exit_price,
                exit_reason=t.exit_reason,
                highest_price_seen=t.highest_price_seen,
                pnl_dollars=t.pnl_dollars,
                pnl_percent=t.pnl_percent,
                hold_minutes=t.hold_minutes,
                quantity=t.quantity,
                underlying_price=t.underlying_price,
                expiry_date=t.expiry_date.isoformat() if t.expiry_date else None,
                dte=t.dte,
                delta=t.delta,
                entry_reason=t.entry_reason,
                exit_detail=t.exit_detail,
            )
            for t in result.trades
        ],
    )


@router.post("/stock-backtest/optimize", response_model=StockOptimizeResponse)
def run_stock_optimize_endpoint(body: StockOptimizeRequest):
    """Run options-level parameter optimization for a single ticker/timeframe."""
    import time as _time

    from stock_backtest_engine import load_ticker_csv_bars

    downloaded = _scan_available_tickers()
    if body.ticker.upper() not in downloaded:
        raise HTTPException(400, f"No data for {body.ticker}. Download it first.")

    # Lazy import to avoid circular issues
    sys.path.insert(0, _SCRIPTS_DIR)
    from multi_ticker_optimizer import optimize_ticker_timeframe

    bars_by_day = load_ticker_csv_bars(
        body.ticker.upper(), date(2000, 1, 1), date(2099, 12, 31), body.bar_interval
    )

    if not bars_by_day:
        raise HTTPException(404, f"No data for {body.ticker} @ {body.bar_interval}")

    t0 = _time.time()
    try:
        top = optimize_ticker_timeframe(
            ticker=body.ticker.upper(),
            timeframe=body.bar_interval,
            bars_by_day=bars_by_day,
            iterations=body.num_iterations,
            metric=body.target_metric,
            quantity=body.quantity,
            top_n=body.top_n,
        )
    except Exception as e:
        logger.exception("Stock optimization failed")
        raise HTTPException(500, f"Optimization failed: {str(e)}")

    elapsed = round(_time.time() - t0, 1)

    return StockOptimizeResponse(
        total_combinations_tested=body.num_iterations,
        elapsed_seconds=elapsed,
        target_metric=body.target_metric,
        results=[StockOptimizeResultEntry(**r) for r in top],
    )


@router.get("/stock-backtest/results", response_model=list[StockOptimizeResultEntry])
def get_saved_results(
    min_trades: int = Query(0, ge=0, description="Filter results with fewer trades"),
    limit: int = Query(0, ge=0, description="Max results to return (0=all)"),
):
    """Return the saved multi-ticker optimization results from JSON."""
    json_path = os.path.join(_DATA_DIR, "optimization_results.json")
    if not os.path.exists(json_path):
        raise HTTPException(404, "No saved results found. Run the optimizer first.")

    with open(json_path) as f:
        data = json.load(f)

    results = data.get("results", [])
    if min_trades > 0:
        results = [r for r in results if r.get("total_trades", 0) >= min_trades]
    if limit > 0:
        results = results[:limit]

    return [StockOptimizeResultEntry(**r) for r in results]


@router.delete("/stock-backtest/results")
def clear_saved_results():
    """Clear all saved optimization results."""
    json_path = os.path.join(_DATA_DIR, "optimization_results.json")
    with open(json_path, "w") as f:
        json.dump({"generated": "", "total_results": 0, "results": []}, f)
    return {"ok": True}


@router.post("/stock-backtest/batch-optimize", response_model=BatchOptimizeStatusResponse)
def start_batch_optimize(body: BatchOptimizeRequest):
    """Start batch optimization across all available tickers in background."""
    with _batch_job["lock"]:
        if _batch_job["status"] == "running":
            raise HTTPException(409, "Batch optimization already running")

        _batch_job["status"] = "running"
        _batch_job["progress"] = "0/?"
        _batch_job["elapsed_seconds"] = 0
        _batch_job["results_count"] = 0
        _batch_job["error"] = ""

    thread = threading.Thread(
        target=_run_batch_optimize,
        args=(body.iterations, body.metric, body.min_trades, body.market_cap_tier, body.tickers),
        daemon=True,
    )
    thread.start()

    return BatchOptimizeStatusResponse(status="running", progress="Starting...")


@router.get("/stock-backtest/batch-optimize/status", response_model=BatchOptimizeStatusResponse)
def get_batch_optimize_status():
    """Poll batch optimization progress."""
    with _batch_job["lock"]:
        return BatchOptimizeStatusResponse(
            status=_batch_job["status"],
            progress=_batch_job["progress"],
            elapsed_seconds=_batch_job["elapsed_seconds"],
            results_count=_batch_job["results_count"],
            error=_batch_job["error"],
        )


# ── Search & Download ────────────────────────────────────────────


@router.post("/stock-backtest/search", response_model=list[SearchResult])
def search_symbols(query: str = Query(..., min_length=1, max_length=10)):
    """Search for symbols across downloaded tickers and common symbols list."""
    q = query.upper().strip()
    downloaded = set(_scan_available_tickers())

    # Combine downloaded + common symbols, dedup
    all_symbols = sorted(set(list(downloaded) + _COMMON_SYMBOLS))

    # Filter: starts with query or exact match
    matches = [s for s in all_symbols if s.startswith(q)]

    # Limit results
    return [
        SearchResult(symbol=s, has_data=(s in downloaded))
        for s in matches[:20]
    ]


@router.post("/stock-backtest/download/{symbol}", response_model=DownloadResponse)
def download_symbol_data(symbol: str):
    """Download ~6 months of historical data for a symbol via Schwab API."""
    sym = symbol.upper().strip()
    if not re.match(r"^[A-Z.]{1,10}$", sym):
        raise HTTPException(400, "Invalid symbol")

    fetcher_path = os.path.join(_SCRIPTS_DIR, "schwab_fetcher.py")
    if not os.path.exists(fetcher_path):
        raise HTTPException(500, "Fetcher script not found")

    try:
        result = subprocess.run(
            [sys.executable, fetcher_path, sym],
            capture_output=True,
            text=True,
            timeout=600,
            cwd=os.path.join(_SCRIPTS_DIR, ".."),
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Download timed out (10 minutes)")
    except Exception as e:
        raise HTTPException(500, f"Download failed: {e}")

    # Parse JSON output from fetcher
    stdout = result.stdout.strip()
    if not stdout:
        raise HTTPException(500, f"Fetcher produced no output. stderr: {result.stderr[:500]}")

    try:
        data = json.loads(stdout.split("\n")[-1])
    except json.JSONDecodeError:
        raise HTTPException(500, f"Invalid fetcher output: {stdout[:200]}")

    if not data.get("ok"):
        return DownloadResponse(ok=False, symbol=sym, message=data.get("error", "Unknown error"))

    return DownloadResponse(
        ok=True,
        symbol=sym,
        message=f"Downloaded {data['files']} files ({data['total_rows']:,} rows)",
        files=data["files"],
        total_rows=data["total_rows"],
    )


# ── Favorites CRUD ───────────────────────────────────────────────


@router.get("/stock-backtest/favorites", response_model=list[FavoriteStrategyResponse])
def get_favorites(db: Session = Depends(_get_db)):
    """Return all saved favorite strategies."""
    rows = db.query(FavoriteStrategy).order_by(FavoriteStrategy.created_at.desc()).all()
    return [
        FavoriteStrategyResponse(
            id=r.id,
            ticker=r.ticker,
            strategy_name=r.strategy_name,
            direction=r.direction,
            params=json.loads(r.params) if r.params else {},
            summary=json.loads(r.summary) if r.summary else None,
            notes=r.notes,
            created_at=r.created_at.isoformat() if r.created_at else "",
        )
        for r in rows
    ]


@router.post("/stock-backtest/favorites", response_model=FavoriteStrategyResponse)
def save_favorite(body: FavoriteStrategyRequest, db: Session = Depends(_get_db)):
    """Save a strategy as a favorite."""
    fav = FavoriteStrategy(
        ticker=body.ticker.upper(),
        strategy_name=body.strategy_name,
        direction=body.direction,
        params=json.dumps(body.params),
        summary=json.dumps(body.summary) if body.summary else None,
        notes=body.notes,
    )
    db.add(fav)
    db.commit()
    db.refresh(fav)

    return FavoriteStrategyResponse(
        id=fav.id,
        ticker=fav.ticker,
        strategy_name=fav.strategy_name,
        direction=fav.direction,
        params=json.loads(fav.params),
        summary=json.loads(fav.summary) if fav.summary else None,
        notes=fav.notes,
        created_at=fav.created_at.isoformat() if fav.created_at else "",
    )


@router.delete("/stock-backtest/favorites/{fav_id}")
def delete_favorite(fav_id: int, db: Session = Depends(_get_db)):
    """Delete a favorite strategy by ID."""
    fav = db.query(FavoriteStrategy).filter(FavoriteStrategy.id == fav_id).first()
    if not fav:
        raise HTTPException(404, "Favorite not found")
    db.delete(fav)
    db.commit()
    return {"ok": True}
