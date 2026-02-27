from datetime import date, datetime, time

import pytest
import pytz

from app.models import ExitReason, Trade, TradeDirection, TradeStatus
from app.services.exit_engine import ExitEngine
from app.services.order_manager import OrderManager
from app.services.schwab_client import SchwabService
from tests.mocks.mock_schwab import MockSchwabClient

ET = pytz.timezone("US/Eastern")


def _make_open_trade(db_session, entry_price=2.00, option_symbol="SPY_TEST_OPT",
                     entry_filled_at=None):
    trade = Trade(
        trade_date=date.today(),
        direction=TradeDirection.CALL,
        option_symbol=option_symbol,
        strike_price=601.0,
        expiration_date=date.today(),
        entry_order_id="entry_test",
        entry_price=entry_price,
        entry_quantity=1,
        highest_price_seen=entry_price,
        stop_loss_price=entry_price * 0.60,  # 40% stop-loss
        entry_filled_at=entry_filled_at,
        status=TradeStatus.STOP_LOSS_PLACED,
    )
    db_session.add(trade)
    db_session.commit()
    return trade


def _make_exit_engine(mock_schwab, ws_manager):
    schwab_svc = SchwabService(mock_schwab)
    order_mgr = OrderManager(schwab_svc, ws_manager)
    return ExitEngine(schwab_svc, order_mgr)


@pytest.mark.asyncio
async def test_stop_loss_trigger(db_session, mock_schwab, ws_manager):
    trade = _make_open_trade(db_session, entry_price=2.00)
    trade.stop_loss_order_id = None  # App-managed stop

    # Price dropped to 1.15 (below 1.20 stop = 2.00 * 0.60)
    mock_schwab.set_quote("SPY_TEST_OPT", bid=1.10, ask=1.20)

    engine = _make_exit_engine(mock_schwab, ws_manager)
    now = datetime(2026, 2, 7, 14, 0, tzinfo=ET)  # 2 PM ET
    result = await engine.evaluate_position(db_session, trade, now_et=now)

    assert result == ExitReason.STOP_LOSS
    assert trade.status == TradeStatus.EXITING


@pytest.mark.asyncio
async def test_stop_loss_not_triggered_above_threshold(db_session, mock_schwab, ws_manager):
    trade = _make_open_trade(db_session, entry_price=2.00)
    trade.stop_loss_order_id = None  # App-managed stop

    # Price at 1.30 (above 1.20 stop)
    mock_schwab.set_quote("SPY_TEST_OPT", bid=1.25, ask=1.35)

    engine = _make_exit_engine(mock_schwab, ws_manager)
    now = datetime(2026, 2, 7, 14, 0, tzinfo=ET)
    result = await engine.evaluate_position(db_session, trade, now_et=now)

    assert result is None


@pytest.mark.asyncio
async def test_profit_target_40_percent(db_session, mock_schwab, ws_manager):
    trade = _make_open_trade(db_session, entry_price=2.00)

    # Price at 2.85 = +42.5% gain (above 40% target)
    mock_schwab.set_quote("SPY_TEST_OPT", bid=2.80, ask=2.90)

    engine = _make_exit_engine(mock_schwab, ws_manager)
    now = datetime(2026, 2, 7, 14, 0, tzinfo=ET)
    result = await engine.evaluate_position(db_session, trade, now_et=now)

    assert result == ExitReason.PROFIT_TARGET


@pytest.mark.asyncio
async def test_profit_target_not_triggered_below_threshold(db_session, mock_schwab, ws_manager):
    trade = _make_open_trade(db_session, entry_price=2.00)
    trade.stop_loss_order_id = "some_order"  # Has Schwab stop

    # Price at 2.70 = +35% gain (below 40% target)
    mock_schwab.set_quote("SPY_TEST_OPT", bid=2.65, ask=2.75)

    engine = _make_exit_engine(mock_schwab, ws_manager)
    now = datetime(2026, 2, 7, 14, 0, tzinfo=ET)
    result = await engine.evaluate_position(db_session, trade, now_et=now)

    assert result is None


@pytest.mark.asyncio
async def test_trailing_stop_trigger(db_session, mock_schwab, ws_manager):
    trade = _make_open_trade(db_session, entry_price=2.00)
    trade.highest_price_seen = 2.50  # Was up 25%

    # Trail stop = 2.50 * 0.85 = 2.125
    # Current mid = 2.10 < 2.125 -> trailing stop triggered
    mock_schwab.set_quote("SPY_TEST_OPT", bid=2.05, ask=2.15)

    engine = _make_exit_engine(mock_schwab, ws_manager)
    now = datetime(2026, 2, 7, 14, 0, tzinfo=ET)
    result = await engine.evaluate_position(db_session, trade, now_et=now)

    assert result == ExitReason.TRAILING_STOP


@pytest.mark.asyncio
async def test_trailing_stop_not_active_at_entry(db_session, mock_schwab, ws_manager):
    """Trailing stop requires price to have gone above entry."""
    trade = _make_open_trade(db_session, entry_price=2.00)
    trade.stop_loss_order_id = "some_order"  # Has Schwab stop

    # Price at 1.90, below entry. highest_price_seen = 2.00 (entry) = not > entry
    mock_schwab.set_quote("SPY_TEST_OPT", bid=1.85, ask=1.95)

    engine = _make_exit_engine(mock_schwab, ws_manager)
    now = datetime(2026, 2, 7, 14, 0, tzinfo=ET)
    result = await engine.evaluate_position(db_session, trade, now_et=now)

    assert result is None


@pytest.mark.asyncio
async def test_trailing_stop_not_triggered_above_trail(db_session, mock_schwab, ws_manager):
    trade = _make_open_trade(db_session, entry_price=2.00)
    trade.highest_price_seen = 2.50  # Was up 25%

    # Trail stop = 2.50 * 0.85 = 2.125
    # Current mid = 2.20 > 2.125 -> no trigger
    mock_schwab.set_quote("SPY_TEST_OPT", bid=2.15, ask=2.25)

    engine = _make_exit_engine(mock_schwab, ws_manager)
    now = datetime(2026, 2, 7, 14, 0, tzinfo=ET)
    result = await engine.evaluate_position(db_session, trade, now_et=now)

    assert result is None


@pytest.mark.asyncio
async def test_high_water_mark_updates(db_session, mock_schwab, ws_manager):
    trade = _make_open_trade(db_session, entry_price=2.00)
    trade.stop_loss_order_id = "some_order"

    # Price risen to 2.10, higher than initial 2.00
    mock_schwab.set_quote("SPY_TEST_OPT", bid=2.05, ask=2.15)

    engine = _make_exit_engine(mock_schwab, ws_manager)
    now = datetime(2026, 2, 7, 14, 0, tzinfo=ET)
    await engine.evaluate_position(db_session, trade, now_et=now)

    assert trade.highest_price_seen == pytest.approx(2.10, abs=0.01)


@pytest.mark.asyncio
async def test_force_exit_330pm(db_session, mock_schwab, ws_manager):
    trade = _make_open_trade(db_session, entry_price=2.00)

    mock_schwab.set_quote("SPY_TEST_OPT", bid=2.05, ask=2.15)

    engine = _make_exit_engine(mock_schwab, ws_manager)
    now = datetime(2026, 2, 7, 15, 1, tzinfo=ET)  # 3:01 PM ET
    result = await engine.evaluate_position(db_session, trade, now_et=now)

    assert result == ExitReason.TIME_BASED


@pytest.mark.asyncio
async def test_no_exit_before_330pm(db_session, mock_schwab, ws_manager):
    trade = _make_open_trade(db_session, entry_price=2.00)
    trade.stop_loss_order_id = "some_order"

    # Price at 2.10 = +5%, no triggers
    mock_schwab.set_quote("SPY_TEST_OPT", bid=2.05, ask=2.15)

    engine = _make_exit_engine(mock_schwab, ws_manager)
    now = datetime(2026, 2, 7, 14, 59, tzinfo=ET)  # 2:59 PM ET
    result = await engine.evaluate_position(db_session, trade, now_et=now)

    assert result is None


@pytest.mark.asyncio
async def test_max_hold_time_exit(db_session, mock_schwab, ws_manager):
    # Filled at 10:00 AM UTC
    filled_at = datetime(2026, 2, 7, 10, 0, 0)
    trade = _make_open_trade(db_session, entry_price=2.00, entry_filled_at=filled_at)
    trade.stop_loss_order_id = "some_order"

    mock_schwab.set_quote("SPY_TEST_OPT", bid=2.05, ask=2.15)

    engine = _make_exit_engine(mock_schwab, ws_manager)
    # 3 hours + 1 minute after fill (13:01 UTC = 8:01 AM ET, before 3:30 PM)
    now_utc = datetime(2026, 2, 7, 13, 1, 0, tzinfo=pytz.utc)
    now_et = now_utc.astimezone(ET)
    result = await engine.evaluate_position(db_session, trade, now_et=now_et)

    assert result == ExitReason.MAX_HOLD_TIME


@pytest.mark.asyncio
async def test_max_hold_time_not_reached(db_session, mock_schwab, ws_manager):
    # Filled at 10:00 AM UTC
    filled_at = datetime(2026, 2, 7, 10, 0, 0)
    trade = _make_open_trade(db_session, entry_price=2.00, entry_filled_at=filled_at)
    trade.stop_loss_order_id = "some_order"

    mock_schwab.set_quote("SPY_TEST_OPT", bid=2.05, ask=2.15)

    engine = _make_exit_engine(mock_schwab, ws_manager)
    # 2 hours 59 min after fill (12:59 UTC)
    now_utc = datetime(2026, 2, 7, 12, 59, 0, tzinfo=pytz.utc)
    now_et = now_utc.astimezone(ET)
    result = await engine.evaluate_position(db_session, trade, now_et=now_et)

    assert result is None


@pytest.mark.asyncio
async def test_time_exit_priority_over_profit(db_session, mock_schwab, ws_manager):
    """Time exit takes priority even if profit target would also trigger."""
    trade = _make_open_trade(db_session, entry_price=2.00)

    # Price at 2.90 = +45%, would trigger profit target
    mock_schwab.set_quote("SPY_TEST_OPT", bid=2.85, ask=2.95)

    engine = _make_exit_engine(mock_schwab, ws_manager)
    now = datetime(2026, 2, 7, 15, 35, tzinfo=ET)  # 3:35 PM
    result = await engine.evaluate_position(db_session, trade, now_et=now)

    # Time-based exit has higher priority
    assert result == ExitReason.TIME_BASED


@pytest.mark.asyncio
async def test_already_exiting_skipped(db_session, mock_schwab, ws_manager):
    trade = _make_open_trade(db_session, entry_price=2.00)
    trade.status = TradeStatus.EXITING

    engine = _make_exit_engine(mock_schwab, ws_manager)
    now = datetime(2026, 2, 7, 14, 0, tzinfo=ET)
    result = await engine.evaluate_position(db_session, trade, now_et=now)

    assert result is None
