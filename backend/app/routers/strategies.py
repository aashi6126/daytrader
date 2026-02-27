"""API endpoints for enabling/disabling live trading strategies (multi-strategy)."""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Request

from app.schemas import (
    DisableStrategyRequest,
    EnabledStrategiesResponse,
    EnabledStrategyEntry,
    EnableStrategyRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "data"))
_STRATEGIES_FILE = os.path.join(_DATA_DIR, "enabled_strategies.json")

# Also check legacy single-strategy file for migration
_LEGACY_FILE = os.path.join(_DATA_DIR, "enabled_strategy.json")


def _strategy_key(ticker: str, signal_type: str, timeframe: str) -> str:
    return f"{ticker}_{signal_type}_{timeframe}"


def _read_strategies() -> list[dict]:
    """Read enabled strategies from file. Handles migration from legacy format."""
    if os.path.exists(_STRATEGIES_FILE):
        with open(_STRATEGIES_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []

    # Migrate from legacy single-strategy file
    if os.path.exists(_LEGACY_FILE):
        with open(_LEGACY_FILE) as f:
            old = json.load(f)
        if old.get("enabled") and old.get("ticker"):
            strategies = [{
                "ticker": old["ticker"],
                "timeframe": old["timeframe"],
                "signal_type": old["signal_type"],
                "params": old.get("params", {}),
                "enabled_at": old.get("enabled_at"),
            }]
            _write_strategies(strategies)
            os.remove(_LEGACY_FILE)
            return strategies
        # Legacy file says disabled — clean up
        os.remove(_LEGACY_FILE)

    return []


def _write_strategies(strategies: list[dict]):
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(_STRATEGIES_FILE, "w") as f:
        json.dump(strategies, f, indent=2)


def _ensure_tasks_dict(app) -> dict[str, asyncio.Task]:
    if not hasattr(app.state, "strategy_tasks"):
        app.state.strategy_tasks = {}
    return app.state.strategy_tasks


def _build_response(strategies: list[dict]) -> EnabledStrategiesResponse:
    return EnabledStrategiesResponse(
        strategies=[
            EnabledStrategyEntry(
                ticker=s["ticker"],
                timeframe=s["timeframe"],
                signal_type=s["signal_type"],
                params=s.get("params"),
                enabled_at=s.get("enabled_at"),
            )
            for s in strategies
        ]
    )


@router.get("/strategies/enabled", response_model=EnabledStrategiesResponse)
def get_enabled_strategies():
    """Return all currently enabled strategies."""
    return _build_response(_read_strategies())


@router.post("/strategies/enable", response_model=EnabledStrategiesResponse)
async def enable_strategy(body: EnableStrategyRequest, request: Request):
    """Enable a strategy for live trading. Multiple strategies can run concurrently."""
    from app.tasks.strategy_signal import StrategySignalTask

    ticker = body.ticker.upper()
    key = _strategy_key(ticker, body.signal_type, body.timeframe)
    tasks = _ensure_tasks_dict(request.app)

    # Cancel existing task for this exact strategy if already running
    existing = tasks.get(key)
    if existing and not existing.done():
        existing.cancel()
        logger.info(f"Cancelled existing task for {key}")

    # Build config
    config = {
        "ticker": ticker,
        "timeframe": body.timeframe,
        "signal_type": body.signal_type,
        "params": body.params,
        "enabled_at": datetime.now(timezone.utc).isoformat(),
    }

    # Update persisted list (replace if same key exists, else append)
    strategies = _read_strategies()
    strategies = [
        s for s in strategies
        if _strategy_key(s["ticker"], s["signal_type"], s["timeframe"]) != key
    ]
    strategies.append(config)
    _write_strategies(strategies)

    # Start the signal task
    task_obj = StrategySignalTask(request.app, config)
    tasks[key] = asyncio.create_task(task_obj.run())

    logger.info(f"Strategy enabled: {ticker} {body.signal_type} @ {body.timeframe} "
                f"({len(strategies)} total)")

    return _build_response(strategies)


@router.post("/strategies/disable", response_model=EnabledStrategiesResponse)
async def disable_strategy(body: DisableStrategyRequest, request: Request):
    """Disable a specific strategy."""
    ticker = body.ticker.upper()
    key = _strategy_key(ticker, body.signal_type, body.timeframe)
    tasks = _ensure_tasks_dict(request.app)

    # Cancel the task
    existing = tasks.pop(key, None)
    if existing and not existing.done():
        existing.cancel()
        logger.info(f"Strategy task cancelled: {key}")

    # Remove from persisted list
    strategies = _read_strategies()
    strategies = [
        s for s in strategies
        if _strategy_key(s["ticker"], s["signal_type"], s["timeframe"]) != key
    ]
    _write_strategies(strategies)

    logger.info(f"Strategy disabled: {key} ({len(strategies)} remaining)")

    return _build_response(strategies)
