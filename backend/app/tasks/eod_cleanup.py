import asyncio
import logging
from datetime import date, datetime, timedelta

import pytz

from app.database import SessionLocal
from app.models import DailySummary, Trade, TradeStatus

logger = logging.getLogger(__name__)
ET = pytz.timezone("US/Eastern")


class EODCleanupTask:
    """Runs at 4:05 PM ET to compute daily summary statistics."""

    def __init__(self, app):
        self.app = app

    async def run(self):
        logger.info("EODCleanupTask started")

        while True:
            try:
                now_et = datetime.now(ET)
                target = now_et.replace(hour=16, minute=5, second=0, microsecond=0)
                if now_et >= target:
                    target += timedelta(days=1)
                wait_seconds = (target - now_et).total_seconds()

                logger.info(
                    f"EODCleanupTask waiting {wait_seconds:.0f}s until {target}"
                )
                await asyncio.sleep(wait_seconds)

                db = SessionLocal()
                try:
                    today = date.today()
                    trades = (
                        db.query(Trade).filter(Trade.trade_date == today).all()
                    )

                    closed = [
                        t for t in trades if t.status == TradeStatus.CLOSED
                    ]
                    winners = [
                        t for t in closed if (t.pnl_dollars or 0) > 0
                    ]
                    losers = [
                        t for t in closed if (t.pnl_dollars or 0) < 0
                    ]
                    total_pnl = sum(t.pnl_dollars or 0 for t in closed)

                    hold_times = []
                    for t in closed:
                        if t.entry_filled_at and t.exit_filled_at:
                            delta = (
                                t.exit_filled_at - t.entry_filled_at
                            ).total_seconds() / 60
                            hold_times.append(delta)

                    summary = DailySummary(
                        trade_date=today,
                        total_trades=len(trades),
                        winning_trades=len(winners),
                        losing_trades=len(losers),
                        total_pnl=total_pnl,
                        largest_win=max(
                            (t.pnl_dollars for t in winners), default=0
                        ),
                        largest_loss=min(
                            (t.pnl_dollars for t in losers), default=0
                        ),
                        win_rate=(
                            (len(winners) / len(closed) * 100) if closed else 0
                        ),
                        avg_hold_time_minutes=(
                            sum(hold_times) / len(hold_times)
                            if hold_times
                            else None
                        ),
                    )
                    db.merge(summary)
                    db.commit()
                    logger.info(
                        f"EOD summary {today}: {len(closed)} trades, "
                        f"PnL=${total_pnl:.2f}"
                    )
                finally:
                    db.close()

            except asyncio.CancelledError:
                logger.info("EODCleanupTask cancelled")
                break
            except Exception as e:
                logger.exception(f"EODCleanupTask error: {e}")
                await asyncio.sleep(60)
