from datetime import date, datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.config import Settings
from app.models import Alert, AlertStatus, Trade, TradeDirection, TradeStatus
from app.schemas import TradingViewAlert
from app.services.option_selector import OptionSelector
from app.services.schwab_client import SchwabService
from app.services.trade_manager import TradeManager
from app.services.ws_manager import WebSocketManager
from tests.mocks.mock_schwab import MockSchwabClient


@pytest.fixture
def trade_manager_deps(mock_schwab, ws_manager):
    schwab_svc = SchwabService(mock_schwab)
    selector = OptionSelector(schwab_svc)
    return TradeManager(schwab_svc, selector, ws_manager)


def test_daily_trade_count_empty(db_session, trade_manager_deps):
    count = trade_manager_deps.get_daily_trade_count(db_session)
    assert count == 0


def test_daily_trade_count_excludes_cancelled(db_session, trade_manager_deps):
    trade = Trade(
        trade_date=date.today(),
        direction=TradeDirection.CALL,
        option_symbol="SPY_TEST",
        strike_price=600.0,
        expiration_date=date.today(),
        entry_order_id="123",
        entry_quantity=1,
        status=TradeStatus.CANCELLED,
    )
    db_session.add(trade)
    db_session.commit()

    count = trade_manager_deps.get_daily_trade_count(db_session)
    assert count == 0


def test_daily_trade_count_counts_active(db_session, trade_manager_deps):
    for status in [TradeStatus.PENDING, TradeStatus.FILLED, TradeStatus.CLOSED]:
        trade = Trade(
            trade_date=date.today(),
            direction=TradeDirection.CALL,
            option_symbol=f"SPY_{status.value}",
            strike_price=600.0,
            expiration_date=date.today(),
            entry_order_id=f"ord_{status.value}",
            entry_quantity=1,
            status=status,
        )
        db_session.add(trade)
    db_session.commit()

    count = trade_manager_deps.get_daily_trade_count(db_session)
    assert count == 3


@pytest.mark.asyncio
async def test_process_alert_success(db_session, trade_manager_deps):
    alert = TradingViewAlert(
        ticker="SPY", action="BUY_CALL", secret="test-secret", price=600.0
    )
    db_alert = Alert(
        raw_payload=alert.model_dump_json(),
        ticker="SPY",
        direction=TradeDirection.CALL,
        signal_price=600.0,
        status=AlertStatus.RECEIVED,
    )
    db_session.add(db_alert)
    db_session.flush()

    result = await trade_manager_deps.process_alert(db_session, db_alert, alert)

    assert result.status == "accepted"
    assert result.trade_id is not None

    trade = db_session.query(Trade).filter(Trade.id == result.trade_id).first()
    assert trade is not None
    assert trade.status == TradeStatus.PENDING
    assert trade.direction == TradeDirection.CALL
    assert trade.entry_order_id is not None


@pytest.mark.asyncio
async def test_process_alert_at_limit(db_session, trade_manager_deps):
    # Create 10 existing trades
    for i in range(10):
        trade = Trade(
            trade_date=date.today(),
            direction=TradeDirection.CALL,
            option_symbol=f"SPY_TEST_{i}",
            strike_price=600.0,
            expiration_date=date.today(),
            entry_order_id=str(i),
            entry_quantity=1,
            status=TradeStatus.FILLED,
        )
        db_session.add(trade)
    db_session.commit()

    alert = TradingViewAlert(
        ticker="SPY", action="BUY_CALL", secret="test-secret", price=600.0
    )
    db_alert = Alert(
        raw_payload=alert.model_dump_json(),
        ticker="SPY",
        direction=TradeDirection.CALL,
        signal_price=600.0,
        status=AlertStatus.RECEIVED,
    )
    db_session.add(db_alert)
    db_session.flush()

    result = await trade_manager_deps.process_alert(db_session, db_alert, alert)

    assert result.status == "rejected"
    assert "limit" in result.message.lower()


@pytest.mark.asyncio
async def test_process_alert_put_signal(db_session, trade_manager_deps):
    alert = TradingViewAlert(
        ticker="SPY", action="BUY_PUT", secret="test-secret", price=600.0
    )
    db_alert = Alert(
        raw_payload=alert.model_dump_json(),
        ticker="SPY",
        direction=TradeDirection.PUT,
        signal_price=600.0,
        status=AlertStatus.RECEIVED,
    )
    db_session.add(db_alert)
    db_session.flush()

    result = await trade_manager_deps.process_alert(db_session, db_alert, alert)

    assert result.status == "accepted"
    trade = db_session.query(Trade).filter(Trade.id == result.trade_id).first()
    assert trade.direction == TradeDirection.PUT


@pytest.mark.asyncio
async def test_vix_circuit_breaker_rejects_trade(db_session, trade_manager_deps):
    """Trades should be rejected when VIX >= circuit breaker threshold."""
    alert = TradingViewAlert(
        ticker="SPY", action="BUY_CALL", secret="test-secret", price=600.0,
    )
    db_alert = Alert(
        raw_payload="{}", ticker="SPY", direction=TradeDirection.CALL,
        signal_price=600.0, status=AlertStatus.RECEIVED,
    )
    db_session.add(db_alert)
    db_session.flush()

    # Mock VIX to be above circuit breaker
    mock_streaming = MagicMock()
    mock_vix_snap = MagicMock()
    mock_vix_snap.is_stale = False
    mock_vix_snap.last = 32.0
    mock_streaming.get_equity_quote.return_value = mock_vix_snap

    # Mock time to be within market hours (11:00 AM ET)
    mock_now = datetime(2026, 3, 2, 11, 0, tzinfo=ZoneInfo("America/New_York"))
    with patch("app.services.trade_manager.datetime") as mock_dt, \
         patch("app.dependencies.get_streaming_service", return_value=mock_streaming):
        mock_dt.now.return_value = mock_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        result = await trade_manager_deps.process_alert(db_session, db_alert, alert)

    assert result.status == "rejected"
    assert "VIX circuit breaker" in result.message


@pytest.mark.asyncio
async def test_event_calendar_blocks_afternoon_trades(db_session, trade_manager_deps):
    """Afternoon trades should be blocked on FOMC/CPI days."""
    alert = TradingViewAlert(
        ticker="SPY", action="BUY_CALL", secret="test-secret", price=600.0,
    )
    db_alert = Alert(
        raw_payload="{}", ticker="SPY", direction=TradeDirection.CALL,
        signal_price=600.0, status=AlertStatus.RECEIVED,
    )
    db_session.add(db_alert)
    db_session.flush()

    # Mock: current time is 1:00 PM ET, and today is a blocked day
    mock_now = datetime(2026, 3, 18, 13, 0, tzinfo=ZoneInfo("America/New_York"))
    with patch("app.services.trade_manager.datetime") as mock_dt, \
         patch.object(TradeManager, "_is_event_afternoon_blocked", return_value=(True, "Event day (2026-03-18)")):
        mock_dt.now.return_value = mock_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        # Also need to mock VIX check to not interfere
        with patch("app.dependencies.get_streaming_service") as mock_stream:
            mock_snap = MagicMock()
            mock_snap.is_stale = False
            mock_snap.last = 18.0  # low VIX, should pass
            mock_stream.return_value.get_equity_quote.return_value = mock_snap
            result = await trade_manager_deps.process_alert(db_session, db_alert, alert)

    assert result.status == "rejected"
    assert "Afternoon trading blocked" in result.message
