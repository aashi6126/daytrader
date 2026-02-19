from datetime import date, datetime

import pytest

from app.models import ExitReason, Trade, TradeDirection, TradeStatus
from app.services.order_manager import OrderManager
from app.services.schwab_client import SchwabService
from app.services.ws_manager import WebSocketManager
from tests.mocks.mock_schwab import MockSchwabClient


def _make_trade(db_session, mock_schwab, status=TradeStatus.PENDING, entry_price=None):
    """Helper to create a trade with an order in the mock."""
    schwab_svc = SchwabService(mock_schwab)
    order = SchwabService.build_option_buy_order("SPY_TEST_OPT", 1, 1.60)
    order_id = schwab_svc.place_order(order)

    trade = Trade(
        trade_date=date.today(),
        direction=TradeDirection.CALL,
        option_symbol="SPY_TEST_OPT",
        strike_price=601.0,
        expiration_date=date.today(),
        entry_order_id=order_id,
        entry_price=entry_price,
        entry_quantity=1,
        status=status,
        highest_price_seen=entry_price,
    )
    db_session.add(trade)
    db_session.commit()
    return trade, order_id


@pytest.mark.asyncio
async def test_check_entry_fill_filled(db_session, mock_schwab, ws_manager):
    trade, order_id = _make_trade(db_session, mock_schwab)
    mock_schwab.simulate_fill(order_id, 1.55)

    order_mgr = OrderManager(SchwabService(mock_schwab), ws_manager)
    changed = await order_mgr.check_entry_fill(db_session, trade)

    assert changed is True
    assert trade.entry_price == 1.55
    assert trade.status == TradeStatus.STOP_LOSS_PLACED
    assert trade.stop_loss_price is not None
    assert trade.stop_loss_price == pytest.approx(1.55 * 0.60, abs=0.01)


@pytest.mark.asyncio
async def test_check_entry_fill_still_working(db_session, mock_schwab, ws_manager):
    trade, order_id = _make_trade(db_session, mock_schwab)
    # Don't simulate fill - order stays WORKING

    order_mgr = OrderManager(SchwabService(mock_schwab), ws_manager)
    changed = await order_mgr.check_entry_fill(db_session, trade)

    assert changed is False
    assert trade.status == TradeStatus.PENDING


@pytest.mark.asyncio
async def test_check_entry_fill_cancelled(db_session, mock_schwab, ws_manager):
    trade, order_id = _make_trade(db_session, mock_schwab)
    mock_schwab.simulate_cancel(order_id)

    order_mgr = OrderManager(SchwabService(mock_schwab), ws_manager)
    changed = await order_mgr.check_entry_fill(db_session, trade)

    assert changed is True
    assert trade.status == TradeStatus.CANCELLED


@pytest.mark.asyncio
async def test_stop_loss_placement(db_session, mock_schwab, ws_manager):
    trade, order_id = _make_trade(db_session, mock_schwab)
    mock_schwab.simulate_fill(order_id, 2.00)

    order_mgr = OrderManager(SchwabService(mock_schwab), ws_manager)
    await order_mgr.check_entry_fill(db_session, trade)

    assert trade.stop_loss_price == pytest.approx(1.20, abs=0.01)
    assert trade.stop_loss_order_id is not None
    assert trade.status == TradeStatus.STOP_LOSS_PLACED


@pytest.mark.asyncio
async def test_place_exit_order_market(db_session, mock_schwab, ws_manager):
    trade, order_id = _make_trade(
        db_session, mock_schwab, status=TradeStatus.STOP_LOSS_PLACED, entry_price=2.00
    )
    trade.stop_loss_order_id = None  # No Schwab stop order

    order_mgr = OrderManager(SchwabService(mock_schwab), ws_manager)
    await order_mgr.place_exit_order(db_session, trade, ExitReason.PROFIT_TARGET)

    assert trade.status == TradeStatus.EXITING
    assert trade.exit_order_id is not None
    assert trade.exit_reason == ExitReason.PROFIT_TARGET


@pytest.mark.asyncio
async def test_check_exit_fill(db_session, mock_schwab, ws_manager):
    schwab_svc = SchwabService(mock_schwab)

    # Create a trade that is EXITING
    exit_order = SchwabService.build_option_sell_order("SPY_TEST_OPT", 1)
    exit_order_id = schwab_svc.place_order(exit_order)

    trade = Trade(
        trade_date=date.today(),
        direction=TradeDirection.CALL,
        option_symbol="SPY_TEST_OPT",
        strike_price=601.0,
        expiration_date=date.today(),
        entry_order_id="entry_123",
        entry_price=2.00,
        entry_quantity=1,
        exit_order_id=exit_order_id,
        exit_reason=ExitReason.PROFIT_TARGET,
        status=TradeStatus.EXITING,
    )
    db_session.add(trade)
    db_session.commit()

    mock_schwab.simulate_fill(exit_order_id, 2.40)

    order_mgr = OrderManager(schwab_svc, ws_manager)
    changed = await order_mgr.check_exit_fill(db_session, trade)

    assert changed is True
    assert trade.status == TradeStatus.CLOSED
    assert trade.exit_price == 2.40
    assert trade.pnl_dollars == pytest.approx(40.0, abs=0.01)  # (2.40 - 2.00) * 1 * 100
    assert trade.pnl_percent == pytest.approx(20.0, abs=0.1)


@pytest.mark.asyncio
async def test_pnl_calculation_negative(db_session, mock_schwab, ws_manager):
    schwab_svc = SchwabService(mock_schwab)
    exit_order_id = schwab_svc.place_order(
        SchwabService.build_option_sell_order("SPY_TEST_OPT", 1)
    )

    trade = Trade(
        trade_date=date.today(),
        direction=TradeDirection.CALL,
        option_symbol="SPY_TEST_OPT",
        strike_price=601.0,
        expiration_date=date.today(),
        entry_order_id="entry_456",
        entry_price=2.00,
        entry_quantity=1,
        exit_order_id=exit_order_id,
        exit_reason=ExitReason.STOP_LOSS,
        status=TradeStatus.EXITING,
    )
    db_session.add(trade)
    db_session.commit()

    mock_schwab.simulate_fill(exit_order_id, 1.80)

    order_mgr = OrderManager(schwab_svc, ws_manager)
    await order_mgr.check_exit_fill(db_session, trade)

    assert trade.pnl_dollars == pytest.approx(-20.0, abs=0.01)
    assert trade.pnl_percent == pytest.approx(-10.0, abs=0.1)


def test_extract_fill_price():
    order_data = {
        "orderActivityCollection": [
            {"executionLegs": [{"price": 1.55}]}
        ]
    }
    assert OrderManager._extract_fill_price(order_data) == 1.55


def test_extract_fill_price_fallback():
    order_data = {"price": "1.60", "orderActivityCollection": []}
    assert OrderManager._extract_fill_price(order_data) == 1.60


def test_check_stop_loss_fill(db_session, mock_schwab, ws_manager):
    schwab_svc = SchwabService(mock_schwab)
    stop_order_id = schwab_svc.place_order(
        SchwabService.build_stop_loss_order("SPY_TEST_OPT", 1, 1.80)
    )

    trade = Trade(
        trade_date=date.today(),
        direction=TradeDirection.CALL,
        option_symbol="SPY_TEST_OPT",
        strike_price=601.0,
        expiration_date=date.today(),
        entry_order_id="entry_789",
        entry_price=2.00,
        entry_quantity=1,
        stop_loss_order_id=stop_order_id,
        stop_loss_price=1.80,
        status=TradeStatus.STOP_LOSS_PLACED,
    )
    db_session.add(trade)
    db_session.commit()

    mock_schwab.simulate_fill(stop_order_id, 1.78)

    order_mgr = OrderManager(schwab_svc, ws_manager)
    changed = order_mgr.check_stop_loss_fill(db_session, trade)

    assert changed is True
    assert trade.status == TradeStatus.CLOSED
    assert trade.exit_price == 1.78
    assert trade.exit_reason == ExitReason.STOP_LOSS
    assert trade.pnl_dollars == pytest.approx(-22.0, abs=0.01)
