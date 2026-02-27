from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

from app.config import Settings
from app.database import SessionLocal
from app.models import Alert, AlertStatus, TradeDirection
from app.schemas import TradingViewAlert

logger = logging.getLogger(__name__)
settings = Settings()

ET = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
ORB_END = time(9, 45)
CONFIRM_TIME = time(9, 50)


class ORBState(str, Enum):
    WAITING_FOR_MARKET = "waiting_for_market"
    FETCHING_ORB = "fetching_orb"
    WAITING_CONFIRMATION = "waiting_confirmation"
    CHECKING_CONFIRMATION = "checking_confirmation"
    DONE_FOR_DAY = "done_for_day"


class ORBSignalTask:
    """Fetches the 15-min Opening Range candle (9:30-9:45 ET) from Schwab price history,
    then checks for directional confirmation at 9:50 ET.
    If confirmed, fires a trade via TradeManager.process_alert().
    """

    def __init__(self, app):
        self.app = app
        self.state = ORBState.WAITING_FOR_MARKET
        self.orb_high = 0.0
        self.orb_low = 0.0
        self.orb_open = 0.0
        self.orb_close = 0.0
        self.today: date | None = None

    def _now_et(self) -> datetime:
        return datetime.now(ET)

    def _reset(self):
        self.state = ORBState.WAITING_FOR_MARKET
        self.orb_high = 0.0
        self.orb_low = 0.0
        self.orb_open = 0.0
        self.orb_close = 0.0
        self.today = None

    def _get_spy_price(self) -> float | None:
        try:
            # Try streaming cache first
            from app.dependencies import get_streaming_service

            snap = get_streaming_service().get_equity_quote("SPY")
            if snap and not snap.is_stale and snap.last > 0:
                return snap.last

            # REST fallback
            from app.services.schwab_client import SchwabService

            schwab = SchwabService(self.app.state.schwab_client)
            data = schwab.get_quote("SPY")
            return data.get("SPY", {}).get("quote", {}).get("lastPrice")
        except Exception as e:
            logger.warning(f"ORB: failed to get SPY price: {e}")
            return None

    def _fetch_orb_candle(self) -> bool:
        """Fetch the 9:30 AM 15-min candle from Schwab price history.
        Returns True if ORB was successfully built."""
        try:
            from app.services.schwab_client import SchwabService

            schwab = SchwabService(self.app.state.schwab_client)

            # Request today's 15-min candles
            now = self._now_et()
            start = datetime.combine(now.date(), time(9, 30), tzinfo=ET)
            end = datetime.combine(now.date(), time(9, 46), tzinfo=ET)

            resp = self.app.state.schwab_client.price_history(
                "SPY",
                periodType="day",
                period="1",
                frequencyType="minute",
                frequency=15,
                startDate=start,
                endDate=end,
                needExtendedHoursData=False,
            )
            resp.raise_for_status()
            data = resp.json()

            candles = data.get("candles", [])
            if not candles:
                logger.warning("ORB: no candles returned from price history")
                return False

            # The first candle should be the 9:30 candle
            candle = candles[0]
            self.orb_open = candle["open"]
            self.orb_high = candle["high"]
            self.orb_low = candle["low"]
            self.orb_close = candle["close"]

            logger.info(
                f"ORB: 9:30 candle from Schwab — "
                f"O=${self.orb_open:.2f} H=${self.orb_high:.2f} "
                f"L=${self.orb_low:.2f} C=${self.orb_close:.2f}"
            )
            return True

        except Exception as e:
            logger.warning(f"ORB: failed to fetch price history: {e}")
            return False

    async def run(self):
        logger.info("ORBSignalTask started")

        while True:
            try:
                now = self._now_et()
                today = now.date()

                # New day reset
                if self.today != today:
                    self._reset()
                    self.today = today

                # Weekend check (Saturday=5, Sunday=6)
                if today.weekday() >= 5:
                    await asyncio.sleep(60)
                    continue

                current_time = now.time()

                if self.state == ORBState.WAITING_FOR_MARKET:
                    if current_time >= ORB_END:
                        if current_time >= CONFIRM_TIME:
                            logger.info("ORB: market already past 9:50, skipping today")
                            self.state = ORBState.DONE_FOR_DAY
                            continue
                        # Between 9:45-9:50, go straight to fetching
                        self.state = ORBState.FETCHING_ORB
                    else:
                        # Sleep until 9:45
                        orb_end_dt = datetime.combine(today, ORB_END, tzinfo=ET)
                        wait = max((orb_end_dt - now).total_seconds(), 1)
                        logger.debug(f"ORB: waiting {wait:.0f}s for 9:45 ET")
                        await asyncio.sleep(min(wait, 60))
                    continue

                if self.state == ORBState.FETCHING_ORB:
                    if self._fetch_orb_candle():
                        orb_range = self.orb_high - self.orb_low
                        logger.info(
                            f"ORB: range = ${orb_range:.2f} "
                            f"[{self.orb_low:.2f} - {self.orb_high:.2f}]"
                        )

                        if orb_range < settings.ORB_MIN_RANGE:
                            logger.info(
                                f"ORB: range ${orb_range:.2f} < min ${settings.ORB_MIN_RANGE:.2f}, "
                                f"skipping today (choppy)"
                            )
                            self.state = ORBState.DONE_FOR_DAY
                        else:
                            self.state = ORBState.WAITING_CONFIRMATION
                            logger.info("ORB: range valid, waiting for 9:50 confirmation")
                    else:
                        # Retry after a short delay
                        logger.warning("ORB: failed to fetch candle, retrying in 10s")
                        await asyncio.sleep(10)
                    continue

                if self.state == ORBState.WAITING_CONFIRMATION:
                    if current_time >= CONFIRM_TIME:
                        self.state = ORBState.CHECKING_CONFIRMATION
                    else:
                        confirm_dt = datetime.combine(today, CONFIRM_TIME, tzinfo=ET)
                        wait = max((confirm_dt - now).total_seconds(), 1)
                        await asyncio.sleep(min(wait, 30))
                    continue

                if self.state == ORBState.CHECKING_CONFIRMATION:
                    await self._check_confirmation()
                    self.state = ORBState.DONE_FOR_DAY
                    continue

                if self.state == ORBState.DONE_FOR_DAY:
                    # Sleep until next day
                    tomorrow = today + timedelta(days=1)
                    next_open = datetime.combine(tomorrow, time(9, 0), tzinfo=ET)
                    wait = max((next_open - self._now_et()).total_seconds(), 60)
                    await asyncio.sleep(min(wait, 300))
                    continue

            except asyncio.CancelledError:
                logger.info("ORBSignalTask cancelled")
                break
            except Exception as e:
                logger.exception(f"ORBSignalTask error: {e}")
                await asyncio.sleep(30)

    async def _check_confirmation(self):
        price = self._get_spy_price()
        if price is None:
            logger.warning("ORB: could not get SPY price for confirmation")
            return

        orb_direction = "bullish" if price > self.orb_high else (
            "bearish" if price < self.orb_low else "neutral"
        )

        logger.info(
            f"ORB: confirmation check — SPY=${price:.2f} "
            f"ORB=[{self.orb_low:.2f}-{self.orb_high:.2f}] → {orb_direction}"
        )

        if orb_direction == "neutral":
            logger.info("ORB: no breakout confirmed, skipping today")
            return

        action = "BUY_CALL" if orb_direction == "bullish" else "BUY_PUT"
        direction = TradeDirection.CALL if orb_direction == "bullish" else TradeDirection.PUT

        logger.info(f"ORB: confirmed {orb_direction} — firing {action}")

        # Build synthetic alert
        alert = TradingViewAlert(
            ticker="SPY",
            action=action,
            secret=settings.WEBHOOK_SECRET,
            price=price,
            comment=f"ORB auto: {orb_direction} breakout confirmed at 9:50 ET",
            source="orb_auto",
        )

        db = SessionLocal()
        try:
            from app.dependencies import get_ws_manager
            from app.services.option_selector import OptionSelector
            from app.services.schwab_client import SchwabService
            from app.services.trade_manager import TradeManager

            # Create alert record for audit trail
            db_alert = Alert(
                raw_payload=f'{{"action":"{action}","source":"orb_auto","price":{price}}}',
                ticker="SPY",
                direction=direction,
                signal_price=price,
                source="orb_auto",
                status=AlertStatus.RECEIVED,
            )
            db.add(db_alert)
            db.flush()

            schwab = SchwabService(self.app.state.schwab_client)
            selector = OptionSelector(schwab)
            ws = get_ws_manager()
            trade_mgr = TradeManager(schwab, selector, ws, app=self.app)

            result = await trade_mgr.process_alert(
                db, db_alert, alert,
                strategy_params={"signal_type": "orb"},
            )
            logger.info(f"ORB: trade result — {result.status}: {result.message}")

        except Exception as e:
            logger.exception(f"ORB: error placing trade: {e}")
        finally:
            db.close()
