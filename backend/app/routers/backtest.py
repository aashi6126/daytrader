"""Backtest API endpoint. Runs simulation and returns results (no DB storage)."""

import json
import logging
import os
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter()

_DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "data"))


# ── Request / Response schemas ────────────────────────────────────


class BacktestRequest(BaseModel):
    start_date: date
    end_date: date
    data_source: str = Field("csv", description="csv | yfinance")

    signal_type: str = Field("ema_cross", description="ema_cross | vwap_cross | ema_vwap | orb | orb_direction | vwap_rsi | vwap_reclaim | bb_squeeze | rsi_reversal | confluence")
    ema_fast: int = Field(8, ge=2, le=50)
    ema_slow: int = Field(21, ge=5, le=200)
    bar_interval: str = Field("5m", description="1m | 5m | 10m | 15m | 30m")

    rsi_period: int = Field(0, ge=0, le=50)
    rsi_ob: float = Field(70.0, ge=50, le=95)
    rsi_os: float = Field(30.0, ge=5, le=50)
    orb_minutes: int = Field(15, ge=5, le=60)
    atr_period: int = Field(0, ge=0, le=50)
    atr_stop_mult: float = Field(2.0, ge=0.5, le=5.0)

    # ORB direction filter params
    orb_body_min_pct: float = Field(0.0, ge=0.0, le=1.0, description="Min ORB body/range ratio")
    orb_vwap_filter: bool = False
    orb_gap_fade_filter: bool = False
    orb_stop_mult: float = Field(1.0, ge=0.25, le=3.0, description="Stop = N * ORB range")
    orb_target_mult: float = Field(1.5, ge=0.5, le=5.0, description="Target = N * ORB range")

    afternoon_enabled: bool = True

    entry_limit_below_percent: float = Field(5.0, ge=0, le=20)
    quantity: int = Field(2, ge=1, le=10)
    delta_target: float = Field(0.4, ge=0.1, le=0.9)

    stop_loss_percent: float = Field(16.0, ge=1, le=50)
    profit_target_percent: float = Field(40.0, ge=5, le=200)
    trailing_stop_percent: float = Field(20.0, ge=5, le=50)
    trailing_stop_after_scale_out_percent: float = Field(10.0, ge=2, le=30)
    max_hold_minutes: int = Field(90, ge=10, le=300)

    scale_out_enabled: bool = True
    breakeven_trigger_percent: float = Field(10.0, ge=0, le=50)

    # Confluence strategy
    min_confluence: int = Field(5, ge=3, le=6)
    vol_threshold: float = Field(1.5, ge=1.0, le=3.0)

    max_daily_trades: int = Field(10, ge=1, le=50)
    max_daily_loss: float = Field(500.0, ge=50, le=5000)
    max_consecutive_losses: int = Field(3, ge=1, le=10)


class BacktestTradeResponse(BaseModel):
    trade_date: str
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
    scaled_out: bool
    scaled_out_price: Optional[float]
    underlying_price: Optional[float] = None
    expiry_date: Optional[str] = None
    dte: int = 0
    delta: Optional[float] = None


class BacktestDayResponse(BaseModel):
    trade_date: str
    pnl: float
    total_trades: int
    winning_trades: int
    losing_trades: int


class BacktestSummaryResponse(BaseModel):
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


class BacktestResponse(BaseModel):
    summary: BacktestSummaryResponse
    days: list[BacktestDayResponse]
    trades: list[BacktestTradeResponse]


# ── Endpoint ──────────────────────────────────────────────────────


@router.post("/backtest/run", response_model=BacktestResponse)
def run_backtest_endpoint(body: BacktestRequest):
    """Run a backtest. Downloads SPY data, simulates trades, returns results.

    Typical execution: 5-30 seconds depending on date range.
    """
    from app.services.backtest.engine import BacktestParams, run_backtest

    if body.end_date < body.start_date:
        raise HTTPException(400, "end_date must be >= start_date")

    days_span = (body.end_date - body.start_date).days
    if body.data_source == "yfinance":
        if days_span > 90:
            raise HTTPException(400, "Date range limited to 90 days (yfinance)")
        if body.bar_interval == "1m" and days_span > 7:
            raise HTTPException(400, "1-minute bars limited to 7 days (yfinance constraint)")
    valid_signals = ("ema_cross", "vwap_cross", "ema_vwap", "orb", "orb_direction", "vwap_rsi", "vwap_reclaim", "bb_squeeze", "rsi_reversal", "confluence")
    if body.signal_type not in valid_signals:
        raise HTTPException(400, f"signal_type must be one of {valid_signals}")

    params = BacktestParams(
        start_date=body.start_date,
        end_date=body.end_date,
        data_source=body.data_source,
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
        orb_body_min_pct=body.orb_body_min_pct,
        orb_vwap_filter=body.orb_vwap_filter,
        orb_gap_fade_filter=body.orb_gap_fade_filter,
        orb_stop_mult=body.orb_stop_mult,
        orb_target_mult=body.orb_target_mult,
        afternoon_enabled=body.afternoon_enabled,
        entry_limit_below_percent=body.entry_limit_below_percent,
        quantity=body.quantity,
        delta_target=body.delta_target,
        stop_loss_percent=body.stop_loss_percent,
        profit_target_percent=body.profit_target_percent,
        trailing_stop_percent=body.trailing_stop_percent,
        trailing_stop_after_scale_out_percent=body.trailing_stop_after_scale_out_percent,
        max_hold_minutes=body.max_hold_minutes,
        scale_out_enabled=body.scale_out_enabled,
        breakeven_trigger_percent=body.breakeven_trigger_percent,
        min_confluence=body.min_confluence,
        vol_threshold=body.vol_threshold,
        max_daily_trades=body.max_daily_trades,
        max_daily_loss=body.max_daily_loss,
        max_consecutive_losses=body.max_consecutive_losses,
    )

    try:
        result = run_backtest(params)
    except Exception as e:
        logger.exception("Backtest failed")
        raise HTTPException(500, f"Backtest failed: {str(e)}")

    trade_responses = [
        BacktestTradeResponse(
            trade_date=t.trade_date.isoformat(),
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
            scaled_out=t.scaled_out,
            scaled_out_price=t.scaled_out_price,
            underlying_price=t.underlying_price,
            expiry_date=t.expiry_date.isoformat() if t.expiry_date else None,
            dte=t.dte,
            delta=t.delta,
        )
        for t in result.trades
    ]

    day_responses = [
        BacktestDayResponse(
            trade_date=d.trade_date.isoformat(),
            pnl=d.pnl,
            total_trades=len(d.trades),
            winning_trades=d.winning_trades,
            losing_trades=d.losing_trades,
        )
        for d in result.days
    ]

    return BacktestResponse(
        summary=BacktestSummaryResponse(
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
        days=day_responses,
        trades=trade_responses,
    )


# ── Optimizer schemas ─────────────────────────────────────────────


class OptimizeRequest(BaseModel):
    start_date: date
    end_date: date
    data_source: str = Field("csv", description="csv | yfinance")
    bar_interval: str = Field("5m", description="1m | 5m | 10m | 15m | 30m")
    num_iterations: int = Field(200, ge=10, le=5000)
    target_metric: str = Field("composite", description="total_pnl | profit_factor | win_rate | composite | risk_adjusted")
    top_n: int = Field(10, ge=1, le=50)
    afternoon_enabled: bool = True
    scale_out_enabled: bool = True
    quantity: int = Field(2, ge=1, le=10)


class OptimizeResultEntry(BaseModel):
    rank: int
    params: dict
    total_pnl: float
    total_trades: int
    win_rate: float
    profit_factor: float
    max_drawdown: float
    avg_hold_minutes: float
    score: float
    exit_reasons: dict[str, int]


class OptimizeResponse(BaseModel):
    total_combinations_tested: int
    elapsed_seconds: float
    target_metric: str
    results: list[OptimizeResultEntry]


# ── Helpers ───────────────────────────────────────────────────────


def _save_spy_result_to_json(entry: "OptimizeResultEntry", bar_interval: str) -> None:
    """Upsert the top SPY optimizer result into data/optimization_results.json."""
    json_path = os.path.join(_DATA_DIR, "optimization_results.json")
    data: dict = {"generated": "", "total_results": 0, "results": []}

    if os.path.exists(json_path):
        try:
            with open(json_path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    # Remove existing SPY entries for this timeframe
    results = [
        r for r in data.get("results", [])
        if not (r.get("ticker") == "SPY" and r.get("timeframe") == bar_interval)
    ]

    # Build entry in StockOptimizeResultEntry format
    spy_entry = {
        "rank": 1,
        "ticker": "SPY",
        "timeframe": bar_interval,
        "params": entry.params,
        "total_pnl": entry.total_pnl,
        "total_trades": entry.total_trades,
        "win_rate": entry.win_rate,
        "profit_factor": entry.profit_factor,
        "max_drawdown": entry.max_drawdown,
        "avg_hold_minutes": entry.avg_hold_minutes,
        "avg_win": 0,
        "avg_loss": 0,
        "largest_win": 0,
        "largest_loss": 0,
        "score": entry.score,
        "exit_reasons": entry.exit_reasons,
        "days_traded": 0,
    }
    results.append(spy_entry)

    from datetime import date as _date
    data["generated"] = _date.today().isoformat()
    data["total_results"] = len(results)
    data["results"] = results

    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    with open(json_path, "w") as f:
        json.dump(data, f, indent=2)

    logger.info(f"Saved SPY optimizer result to {json_path} (timeframe={bar_interval})")


# ── Optimizer endpoint ────────────────────────────────────────────


@router.post("/backtest/optimize", response_model=OptimizeResponse)
def run_optimize_endpoint(body: OptimizeRequest):
    """Run parameter optimization. Fetches data once, tests N random
    parameter combinations, returns top results ranked by target metric.
    """
    from app.services.backtest.optimizer import OptimizationConfig, run_optimization

    if body.end_date < body.start_date:
        raise HTTPException(400, "end_date must be >= start_date")

    days_span = (body.end_date - body.start_date).days
    if body.data_source == "yfinance":
        if days_span > 90:
            raise HTTPException(400, "Date range limited to 90 days (yfinance)")
        if body.bar_interval == "1m" and days_span > 7:
            raise HTTPException(400, "1-minute bars limited to 7 days (yfinance constraint)")

    valid_metrics = ("total_pnl", "profit_factor", "win_rate", "composite", "risk_adjusted")
    if body.target_metric not in valid_metrics:
        raise HTTPException(400, f"target_metric must be one of {valid_metrics}")

    config = OptimizationConfig(
        start_date=body.start_date,
        end_date=body.end_date,
        data_source=body.data_source,
        bar_interval=body.bar_interval,
        num_iterations=body.num_iterations,
        target_metric=body.target_metric,
        top_n=body.top_n,
        afternoon_enabled=body.afternoon_enabled,
        scale_out_enabled=body.scale_out_enabled,
        quantity=body.quantity,
    )

    try:
        result = run_optimization(config)
    except Exception as e:
        logger.exception("Optimization failed")
        raise HTTPException(500, f"Optimization failed: {str(e)}")

    response_entries = [
        OptimizeResultEntry(
            rank=r.rank,
            params=r.params,
            total_pnl=r.total_pnl,
            total_trades=r.total_trades,
            win_rate=r.win_rate,
            profit_factor=r.profit_factor,
            max_drawdown=r.max_drawdown,
            avg_hold_minutes=r.avg_hold_minutes,
            score=r.score,
            exit_reasons=r.exit_reasons,
        )
        for r in result.results
    ]

    # Auto-save #1 result for Top Setups display
    if response_entries:
        try:
            _save_spy_result_to_json(response_entries[0], body.bar_interval)
        except Exception:
            logger.exception("Failed to save SPY optimizer result to JSON")

    return OptimizeResponse(
        total_combinations_tested=result.total_combinations_tested,
        elapsed_seconds=result.elapsed_seconds,
        target_metric=body.target_metric,
        results=response_entries,
    )
