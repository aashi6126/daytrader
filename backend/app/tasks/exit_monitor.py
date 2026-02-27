import asyncio
import logging
from datetime import date

from app.config import Settings
from app.database import SessionLocal
from app.models import Trade, TradeStatus

logger = logging.getLogger(__name__)
settings = Settings()


async def _wait_any_event(events: list[asyncio.Event], timeout: float):
    """Wait until any event is set, or timeout expires."""

    async def _wait_single(evt: asyncio.Event):
        await evt.wait()

    tasks = [asyncio.create_task(_wait_single(e)) for e in events]
    try:
        await asyncio.wait(tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()


class ExitMonitorTask:
    """Evaluates exit conditions for open positions.

    When streaming is active, wakes immediately on any subscribed option
    price change. Falls back to EXIT_CHECK_INTERVAL_SECONDS timer otherwise.
    """

    def __init__(self, app):
        self.app = app

    async def run(self):
        from app.dependencies import get_streaming_service, get_ws_manager
        from app.services.exit_engine import ExitEngine
        from app.services.order_manager import OrderManager
        from app.services.schwab_client import SchwabService

        logger.info("ExitMonitorTask started")
        streaming = get_streaming_service()
        _subscribed_symbols: set[str] = set()

        while True:
            try:
                # Event-driven: wait for ANY subscribed option quote to update,
                # or fall back to timer if streaming is inactive
                if streaming.is_active and _subscribed_symbols:
                    events = []
                    for sym in _subscribed_symbols:
                        evt = streaming.get_option_event(sym)
                        if evt:
                            events.append(evt)
                    if events:
                        await _wait_any_event(
                            events, timeout=settings.EXIT_CHECK_INTERVAL_SECONDS
                        )
                        # Clear all events after wake
                        for evt in events:
                            evt.clear()
                    else:
                        await asyncio.sleep(settings.EXIT_CHECK_INTERVAL_SECONDS)
                else:
                    await asyncio.sleep(settings.EXIT_CHECK_INTERVAL_SECONDS)

                db = SessionLocal()
                try:
                    schwab = SchwabService(self.app.state.schwab_client)
                    ws = get_ws_manager()
                    order_mgr = OrderManager(schwab, ws, streaming_service=streaming)
                    exit_engine = ExitEngine(schwab, order_mgr, streaming_service=streaming)

                    open_trades = (
                        db.query(Trade)
                        .filter(Trade.trade_date == date.today())
                        .filter(
                            Trade.status.in_(
                                [
                                    TradeStatus.FILLED,
                                    TradeStatus.STOP_LOSS_PLACED,
                                ]
                            )
                        )
                        .all()
                    )

                    # Dynamic subscription management
                    current_symbols = {t.option_symbol for t in open_trades}
                    new_symbols = current_symbols - _subscribed_symbols
                    old_symbols = _subscribed_symbols - current_symbols

                    for sym in new_symbols:
                        await streaming.subscribe_option(sym)
                        _subscribed_symbols.add(sym)
                    for sym in old_symbols:
                        await streaming.unsubscribe_option(sym)
                        _subscribed_symbols.discard(sym)

                    for trade in open_trades:
                        await exit_engine.evaluate_position(
                            db, trade,
                            skip_snapshot=streaming.is_active,
                        )
                finally:
                    db.close()

            except asyncio.CancelledError:
                for sym in _subscribed_symbols:
                    await streaming.unsubscribe_option(sym)
                logger.info("ExitMonitorTask cancelled")
                break
            except Exception as e:
                logger.exception(f"ExitMonitorTask error: {e}")
                await asyncio.sleep(5)
