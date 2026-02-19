"""Multi-ticker options backtest & optimization API endpoints."""

import json
import logging
import os
import sys
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter()

# Add scripts/ to path so we can import stock_backtest_engine
_SCRIPTS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts"))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

_DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "data"))

ALL_TICKERS = ["NVDA", "TSLA", "AMZN", "AMD", "AAPL", "PLTR", "MSFT", "GOOGL", "QQQ", "GLD", "ASTS", "NBIS", "CRWV", "IREN"]
ALL_TIMEFRAMES = ["1m", "5m", "10m", "15m", "30m"]


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
    stop_loss_percent: float = Field(16.0, ge=1, le=80)
    profit_target_percent: float = Field(40.0, ge=1, le=200)
    trailing_stop_percent: float = Field(20.0, ge=1, le=80)
    max_hold_minutes: int = Field(90, ge=10, le=300)
    min_confluence: int = Field(5, ge=3, le=6)
    vol_threshold: float = Field(1.5, ge=1.0, le=3.0)
    orb_body_min_pct: float = Field(0.0, ge=0.0, le=1.0)
    orb_vwap_filter: bool = False
    orb_gap_fade_filter: bool = False
    orb_stop_mult: float = Field(1.0, ge=0.25, le=3.0)
    orb_target_mult: float = Field(1.5, ge=0.5, le=5.0)
    max_daily_trades: int = Field(10, ge=1, le=50)
    max_daily_loss: float = Field(500.0, ge=50, le=5000)
    max_consecutive_losses: int = Field(3, ge=1, le=10)


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


class StockOptimizeResponse(BaseModel):
    total_combinations_tested: int
    elapsed_seconds: float
    target_metric: str
    results: list[StockOptimizeResultEntry]


class TickerInfo(BaseModel):
    ticker: str
    timeframes: list[str]


# ── Endpoints ─────────────────────────────────────────────────────


@router.get("/stock-backtest/tickers", response_model=list[TickerInfo])
def get_available_tickers():
    """Return list of tickers with available CSV data."""
    result = []
    for ticker in ALL_TICKERS:
        available_tf = []
        for tf in ALL_TIMEFRAMES:
            label = tf.replace("m", "min")
            csv_path = os.path.join(_DATA_DIR, f"{ticker}_{label}_6months.csv")
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
    if body.ticker.upper() not in ALL_TICKERS:
        raise HTTPException(400, f"ticker must be one of {ALL_TICKERS}")

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
        orb_body_min_pct=body.orb_body_min_pct,
        orb_vwap_filter=body.orb_vwap_filter,
        orb_gap_fade_filter=body.orb_gap_fade_filter,
        orb_stop_mult=body.orb_stop_mult,
        orb_target_mult=body.orb_target_mult,
        max_daily_trades=body.max_daily_trades,
        max_daily_loss=body.max_daily_loss,
        max_consecutive_losses=body.max_consecutive_losses,
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
            )
            for t in result.trades
        ],
    )


@router.post("/stock-backtest/optimize", response_model=StockOptimizeResponse)
def run_stock_optimize_endpoint(body: StockOptimizeRequest):
    """Run options-level parameter optimization for a single ticker/timeframe."""
    import time as _time

    from stock_backtest_engine import load_ticker_csv_bars

    if body.ticker.upper() not in ALL_TICKERS:
        raise HTTPException(400, f"ticker must be one of {ALL_TICKERS}")

    # Lazy import to avoid circular issues
    sys.path.insert(0, _SCRIPTS_DIR)
    from multi_ticker_optimizer import (
        compute_score,
        generate_combinations,
        optimize_ticker_timeframe,
    )

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
def get_saved_results():
    """Return the saved multi-ticker optimization results from JSON."""
    json_path = os.path.join(_DATA_DIR, "optimization_results.json")
    if not os.path.exists(json_path):
        raise HTTPException(404, "No saved results found. Run the optimizer first.")

    with open(json_path) as f:
        data = json.load(f)

    return [StockOptimizeResultEntry(**r) for r in data.get("results", [])]
