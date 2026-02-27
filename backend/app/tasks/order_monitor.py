import asyncio
import logging
from datetime import date

from app.config import Settings
from app.database import SessionLocal
from app.models import Trade, TradePriceSnapshot, TradeStatus

logger = logging.getLogger(__name__)
settings = Settings()


class OrderMonitorTask:
    """Polls Schwab for order status updates every ORDER_POLL_INTERVAL_SECONDS."""

    def __init__(self, app):
        self.app = app

    async def run(self):
        from app.dependencies import get_streaming_service, get_ws_manager
        from app.services.order_manager import OrderManager
        from app.services.schwab_client import SchwabService

        logger.info("OrderMonitorTask started")
        streaming = get_streaming_service()
        _subscribed_symbols: set[str] = set()

        while True:
            try:
                await asyncio.sleep(settings.ORDER_POLL_INTERVAL_SECONDS)
                db = SessionLocal()
                try:
                    schwab = SchwabService(self.app.state.schwab_client)
                    ws = get_ws_manager()
                    order_mgr = OrderManager(schwab, ws, streaming_service=streaming)

                    active_trades = (
                        db.query(Trade)
                        .filter(Trade.trade_date == date.today())
                        .filter(
                            Trade.status.in_(
                                [
                                    TradeStatus.PENDING,
                                    TradeStatus.EXITING,
                                    TradeStatus.FILLED,
                                    TradeStatus.STOP_LOSS_PLACED,
                                ]
                            )
                        )
                        .all()
                    )

                    # Dynamic subscription for pending trades' option symbols
                    current_symbols = {
                        t.option_symbol
                        for t in active_trades
                        if t.status == TradeStatus.PENDING
                    }
                    new_symbols = current_symbols - _subscribed_symbols
                    old_symbols = _subscribed_symbols - current_symbols

                    for sym in new_symbols:
                        await streaming.subscribe_option(sym)
                        _subscribed_symbols.add(sym)
                    for sym in old_symbols:
                        await streaming.unsubscribe_option(sym)
                        _subscribed_symbols.discard(sym)

                    for trade in active_trades:
                        if trade.status == TradeStatus.PENDING:
                            if not streaming.is_active:
                                # Record price while waiting for fill (REST-only mode)
                                mid = order_mgr._get_current_mid(trade.option_symbol)
                                if mid is not None:
                                    db.add(TradePriceSnapshot(
                                        trade_id=trade.id,
                                        price=mid,
                                        highest_price_seen=mid,
                                    ))
                                    db.commit()
                            await order_mgr.check_entry_fill(db, trade)
                        elif trade.status == TradeStatus.EXITING:
                            await order_mgr.check_exit_fill(db, trade)
                        elif trade.status in (
                            TradeStatus.FILLED,
                            TradeStatus.STOP_LOSS_PLACED,
                        ):
                            order_mgr.check_stop_loss_fill(db, trade)
                finally:
                    db.close()

            except asyncio.CancelledError:
                for sym in _subscribed_symbols:
                    await streaming.unsubscribe_option(sym)
                logger.info("OrderMonitorTask cancelled")
                break
            except Exception as e:
                logger.exception(f"OrderMonitorTask error: {e}")
                await asyncio.sleep(5)
