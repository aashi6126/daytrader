import asyncio
import logging
from datetime import date, datetime, time, timedelta

import pytz

from app.config import Settings
from app.database import SessionLocal
from app.models import OptionChainContract, OptionChainSnapshot, TradeDirection

logger = logging.getLogger(__name__)
settings = Settings()
ET = pytz.timezone("US/Eastern")

MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)


class DataRecorderTask:
    """Records SPY 0DTE option chain snapshots during market hours for backtesting."""

    def __init__(self, app):
        self.app = app

    def _is_market_hours(self, now_et: datetime) -> bool:
        if now_et.weekday() >= 5:
            return False
        return MARKET_OPEN <= now_et.time() <= MARKET_CLOSE

    def _seconds_until_market_open(self, now_et: datetime) -> float:
        days_ahead = 0
        if now_et.weekday() == 5:  # Saturday
            days_ahead = 2
        elif now_et.weekday() == 6:  # Sunday
            days_ahead = 1
        elif now_et.time() >= MARKET_CLOSE:
            # After close, wait until tomorrow (or Monday if Friday)
            days_ahead = 3 if now_et.weekday() == 4 else 1

        next_open = now_et.replace(
            hour=MARKET_OPEN.hour,
            minute=MARKET_OPEN.minute,
            second=0,
            microsecond=0,
        ) + timedelta(days=days_ahead)

        return max((next_open - now_et).total_seconds(), 0)

    def _process_chain_map(
        self, db, snapshot_id: int, date_map: dict, direction: TradeDirection
    ) -> int:
        today_str = date.today().isoformat()
        today_contracts = None
        for exp_key, strikes in date_map.items():
            if today_str in exp_key:
                today_contracts = strikes
                break

        if not today_contracts:
            return 0

        count = 0
        for strike_str, contracts in today_contracts.items():
            for c in contracts:
                bid = c.get("bid", 0)
                ask = c.get("ask", 0)
                if bid <= 0 or ask <= 0:
                    continue

                db.add(
                    OptionChainContract(
                        snapshot_id=snapshot_id,
                        option_symbol=c.get("symbol", ""),
                        contract_type=direction,
                        strike_price=float(strike_str),
                        expiration_date=date.today(),
                        bid=bid,
                        ask=ask,
                        mid=(bid + ask) / 2,
                        delta=c.get("delta"),
                        open_interest=c.get("openInterest"),
                        volume=c.get("totalVolume"),
                    )
                )
                count += 1

        return count

    async def _record_snapshot(self, db):
        from app.services.schwab_client import SchwabService

        schwab = SchwabService(self.app.state.schwab_client)

        call_chain = schwab.get_option_chain(
            symbol="SPY",
            contract_type="CALL",
            strike_count=settings.DATA_RECORDER_STRIKE_COUNT,
        )
        put_chain = schwab.get_option_chain(
            symbol="SPY",
            contract_type="PUT",
            strike_count=settings.DATA_RECORDER_STRIKE_COUNT,
        )

        underlying_price = call_chain.get("underlyingPrice")
        if not underlying_price:
            logger.warning("No underlyingPrice in chain response, skipping snapshot")
            return

        snapshot = OptionChainSnapshot(
            snapshot_date=date.today(),
            snapshot_time=datetime.utcnow(),
            underlying_symbol="SPY",
            underlying_price=underlying_price,
        )
        db.add(snapshot)
        db.flush()

        count = 0
        count += self._process_chain_map(
            db, snapshot.id, call_chain.get("callExpDateMap", {}), TradeDirection.CALL
        )
        count += self._process_chain_map(
            db, snapshot.id, put_chain.get("putExpDateMap", {}), TradeDirection.PUT
        )

        db.commit()
        logger.info(
            f"Recorded snapshot: SPY={underlying_price:.2f}, {count} contracts"
        )

    async def run(self):
        logger.info("DataRecorderTask started")

        while True:
            try:
                now_et = datetime.now(ET)

                if not self._is_market_hours(now_et):
                    sleep_seconds = self._seconds_until_market_open(now_et)
                    logger.info(
                        f"DataRecorder: outside market hours, sleeping "
                        f"{sleep_seconds:.0f}s until next open"
                    )
                    await asyncio.sleep(sleep_seconds)
                    continue

                db = SessionLocal()
                try:
                    await self._record_snapshot(db)
                except Exception as e:
                    db.rollback()
                    raise e
                finally:
                    db.close()

                await asyncio.sleep(settings.DATA_RECORDER_INTERVAL_SECONDS)

            except asyncio.CancelledError:
                logger.info("DataRecorderTask cancelled")
                break
            except Exception as e:
                logger.exception(f"DataRecorderTask error: {e}")
                await asyncio.sleep(5)
