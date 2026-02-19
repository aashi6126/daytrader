from datetime import date

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
