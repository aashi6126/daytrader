"""Tests for signal type gating in StrategySignalTask."""
import os
os.environ.setdefault("WEBHOOK_SECRET", "test-secret")
os.environ.setdefault("SCHWAB_APP_KEY", "test-key")
os.environ.setdefault("SCHWAB_APP_SECRET", "test-secret-value")
os.environ.setdefault("SCHWAB_ACCOUNT_HASH", "test-hash")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Settings


def _make_task(signal_type: str):
    from app.tasks.strategy_signal import StrategySignalTask
    app = MagicMock()
    config = {
        "ticker": "SPY",
        "timeframe": "5m",
        "signal_type": signal_type,
        "params": {},
    }
    return StrategySignalTask(app, config)


@pytest.mark.asyncio
async def test_blocked_signal_type_does_not_fire():
    task = _make_task("ema_cross")  # not in allowed list
    with patch.object(task, '_get_strategy_params', return_value={}):
        await task._fire_signal("CALL", 600.0)
    # Should not attempt to create alert or trade — just return


@pytest.mark.asyncio
async def test_allowed_signal_type_fires():
    task = _make_task("confluence")  # in allowed list
    # Patch the DB and trade manager internals to verify _fire_signal proceeds
    mock_tm = AsyncMock()
    mock_tm.process_alert = AsyncMock(return_value=MagicMock(status="accepted", message="ok"))

    with patch("app.tasks.strategy_signal.SessionLocal") as mock_db_cls, \
         patch("app.services.trade_manager.TradeManager", return_value=mock_tm), \
         patch("app.dependencies.get_ws_manager", return_value=MagicMock()), \
         patch("app.services.schwab_client.SchwabService"), \
         patch("app.services.option_selector.OptionSelector"):
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        await task._fire_signal("CALL", 600.0)
        # Verify that process_alert was called (signal was not blocked)
        mock_tm.process_alert.assert_called_once()
