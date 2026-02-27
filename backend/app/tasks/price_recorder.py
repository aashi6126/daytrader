import asyncio
import logging
from datetime import date

from app.config import Settings
from app.database import SessionLocal
from app.models import Trade, TradePriceSnapshot, TradeStatus

logger = logging.getLogger(__name__)
settings = Settings()


class PriceRecorderTask:
    """Records every streaming price change for open trades to the database.

    Polls the streaming cache every SNAPSHOT_RECORD_INTERVAL_SECONDS and writes
    a TradePriceSnapshot whenever a quote has been updated since the last recording.
    Only active when streaming is running (not DRY_RUN/PAPER_TRADE).
    """

    def __init__(self, app):
        self.app = app

    async def run(self):
        from app.dependencies import get_streaming_service

        logger.info("PriceRecorderTask started")
        streaming = get_streaming_service()
        # Per-trade: last recorded snap.updated_at timestamp
        _last_recorded: dict[int, float] = {}

        while True:
            try:
                await asyncio.sleep(settings.SNAPSHOT_RECORD_INTERVAL_SECONDS)
                if not streaming.is_active:
                    continue

                db = SessionLocal()
                try:
                    open_trades = (
                        db.query(Trade)
                        .filter(Trade.trade_date == date.today())
                        .filter(
                            Trade.status.in_(
                                [TradeStatus.FILLED, TradeStatus.STOP_LOSS_PLACED]
                            )
                        )
                        .all()
                    )

                    wrote_any = False
                    for trade in open_trades:
                        snap = streaming.get_option_quote(trade.option_symbol)
                        if not snap or snap.is_stale or snap.bid <= 0 or snap.ask <= 0:
                            continue

                        last_ts = _last_recorded.get(trade.id, 0.0)
                        if snap.updated_at <= last_ts:
                            continue  # No new data since last recording

                        # Update high-water mark (BID-based, consistent with exit_engine)
                        if snap.bid > (trade.highest_price_seen or 0):
                            trade.highest_price_seen = snap.bid
                            db.flush()

                        db.add(
                            TradePriceSnapshot(
                                trade_id=trade.id,
                                price=snap.mid,
                                highest_price_seen=trade.highest_price_seen
                                or snap.bid,
                            )
                        )
                        _last_recorded[trade.id] = snap.updated_at
                        wrote_any = True

                    if wrote_any:
                        db.commit()

                    # Cleanup closed trades from tracking dict
                    open_ids = {t.id for t in open_trades}
                    for tid in list(_last_recorded):
                        if tid not in open_ids:
                            del _last_recorded[tid]
                finally:
                    db.close()

            except asyncio.CancelledError:
                logger.info("PriceRecorderTask cancelled")
                break
            except Exception as e:
                logger.exception(f"PriceRecorderTask error: {e}")
                await asyncio.sleep(5)
