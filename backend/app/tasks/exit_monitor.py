import asyncio
import logging
from datetime import date

from app.config import Settings
from app.database import SessionLocal
from app.models import Trade, TradeStatus

logger = logging.getLogger(__name__)
settings = Settings()


class ExitMonitorTask:
    """Evaluates exit conditions for open positions every EXIT_CHECK_INTERVAL_SECONDS."""

    def __init__(self, app):
        self.app = app

    async def run(self):
        from app.dependencies import get_ws_manager
        from app.services.exit_engine import ExitEngine
        from app.services.order_manager import OrderManager
        from app.services.schwab_client import SchwabService

        logger.info("ExitMonitorTask started")

        while True:
            try:
                await asyncio.sleep(settings.EXIT_CHECK_INTERVAL_SECONDS)
                db = SessionLocal()
                try:
                    schwab = SchwabService(self.app.state.schwab_client)
                    ws = get_ws_manager()
                    order_mgr = OrderManager(schwab, ws)
                    exit_engine = ExitEngine(schwab, order_mgr)

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

                    for trade in open_trades:
                        await exit_engine.evaluate_position(db, trade)
                finally:
                    db.close()

            except asyncio.CancelledError:
                logger.info("ExitMonitorTask cancelled")
                break
            except Exception as e:
                logger.exception(f"ExitMonitorTask error: {e}")
                await asyncio.sleep(5)
