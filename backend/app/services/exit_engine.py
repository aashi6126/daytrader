import logging
from datetime import datetime, time
from typing import Optional

import pytz
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import ExitReason, Trade, TradeEventType, TradePriceSnapshot, TradeStatus
from app.services.order_manager import OrderManager
from app.services.schwab_client import SchwabService
from app.services.trade_events import log_trade_event

logger = logging.getLogger(__name__)
settings = Settings()
ET = pytz.timezone("US/Eastern")


class ExitEngine:
    """
    Exit strategy for 0DTE options.

    Priority order:
    1. Force exit (3:30 PM ET)
    2. Max hold time (180 minutes from fill)
    3. App-managed stop-loss fallback
    4. Profit target — full exit or optional scale-out
    5. Trailing stop (uses tighter % after scale-out)
    """

    def __init__(
        self,
        schwab_service: SchwabService,
        order_manager: OrderManager,
    ):
        self.schwab = schwab_service
        self.order_manager = order_manager

    async def evaluate_position(
        self, db: Session, trade: Trade, now_et: Optional[datetime] = None
    ) -> Optional[ExitReason]:
        if trade.status not in (TradeStatus.FILLED, TradeStatus.STOP_LOSS_PLACED):
            return None

        if now_et is None:
            now_et = datetime.now(ET)

        current_price = self._get_current_price(trade.option_symbol)
        if current_price is None:
            logger.warning(f"Trade #{trade.id}: could not get current price")
            return None

        # Update high-water mark
        if current_price > (trade.highest_price_seen or 0):
            trade.highest_price_seen = current_price
            db.flush()

        # Record price snapshot for post-trade analysis
        db.add(TradePriceSnapshot(
            trade_id=trade.id,
            price=current_price,
            highest_price_seen=trade.highest_price_seen or current_price,
        ))
        db.flush()

        gain_percent = (
            ((current_price - trade.entry_price) / trade.entry_price) * 100
            if trade.entry_price > 0
            else 0
        )

        # Priority 1: Force exit (3:30 PM ET)
        force_exit_time = time(settings.FORCE_EXIT_HOUR, settings.FORCE_EXIT_MINUTE)
        if now_et.time() >= force_exit_time:
            logger.warning(f"Trade #{trade.id}: FORCE EXIT at {now_et.time()}")
            log_trade_event(
                db, trade.id, TradeEventType.EXIT_TRIGGERED,
                f"Force exit triggered at {now_et.strftime('%H:%M')} ET (cutoff {settings.FORCE_EXIT_HOUR}:{settings.FORCE_EXIT_MINUTE:02d})",
                details={"reason": "TIME_BASED", "current_time": str(now_et.time()), "current_price": current_price, "gain_percent": round(gain_percent, 2)},
            )
            await self.order_manager.place_exit_order(
                db, trade, ExitReason.TIME_BASED
            )
            return ExitReason.TIME_BASED

        # Priority 2: Max hold time
        if trade.entry_filled_at:
            filled_at = trade.entry_filled_at
            if filled_at.tzinfo is None:
                filled_at = pytz.utc.localize(filled_at)
            now_utc = now_et.astimezone(pytz.utc)
            elapsed_minutes = (now_utc - filled_at).total_seconds() / 60
            if elapsed_minutes >= settings.MAX_HOLD_MINUTES:
                logger.info(
                    f"Trade #{trade.id}: MAX HOLD TIME ({elapsed_minutes:.0f} min)"
                )
                log_trade_event(
                    db, trade.id, TradeEventType.EXIT_TRIGGERED,
                    f"Max hold time reached ({elapsed_minutes:.0f} min >= {settings.MAX_HOLD_MINUTES} min)",
                    details={"reason": "MAX_HOLD_TIME", "elapsed_minutes": round(elapsed_minutes, 1), "current_price": current_price, "gain_percent": round(gain_percent, 2)},
                )
                await self.order_manager.place_exit_order(
                    db, trade, ExitReason.MAX_HOLD_TIME
                )
                return ExitReason.MAX_HOLD_TIME

        # Priority 3: App-managed stop-loss fallback
        if not trade.stop_loss_order_id and trade.stop_loss_price:
            if current_price <= trade.stop_loss_price:
                logger.info(
                    f"Trade #{trade.id}: App stop-loss hit "
                    f"({current_price:.2f} <= {trade.stop_loss_price:.2f})"
                )
                log_trade_event(
                    db, trade.id, TradeEventType.EXIT_TRIGGERED,
                    f"App-managed stop-loss hit ({current_price:.2f} <= {trade.stop_loss_price:.2f})",
                    details={"reason": "STOP_LOSS", "current_price": current_price, "stop_price": trade.stop_loss_price, "gain_percent": round(gain_percent, 2)},
                )
                await self.order_manager.place_exit_order(
                    db, trade, ExitReason.STOP_LOSS
                )
                return ExitReason.STOP_LOSS

        # Breakeven stop: once price has risen BREAKEVEN_TRIGGER_PERCENT above entry,
        # move stop-loss to entry price so worst case is $0 loss
        if (
            not trade.breakeven_stop_applied
            and settings.BREAKEVEN_TRIGGER_PERCENT > 0
            and trade.highest_price_seen
            and trade.entry_price
            and trade.highest_price_seen >= trade.entry_price * (1 + settings.BREAKEVEN_TRIGGER_PERCENT / 100)
        ):
            logger.info(
                f"Trade #{trade.id}: moving stop to breakeven "
                f"(peak ${trade.highest_price_seen:.2f} >= "
                f"+{settings.BREAKEVEN_TRIGGER_PERCENT}% threshold)"
            )
            await self.order_manager.move_stop_to_breakeven(db, trade)

        # Priority 4: Multi-tier scale-out
        if settings.SCALE_OUT_ENABLED and trade.entry_quantity >= 2:
            tiers_done = trade.scale_out_count or 0
            remaining = trade.entry_quantity - (trade.scaled_out_quantity or 0)

            # Tier 1: sell TIER_1_QTY at +TIER_1_PERCENT, move stop to breakeven
            if tiers_done == 0 and gain_percent >= settings.SCALE_OUT_TIER_1_PERCENT:
                scale_qty = min(settings.SCALE_OUT_TIER_1_QTY, remaining - 1)
                if scale_qty > 0:
                    logger.info(
                        f"Trade #{trade.id}: TIER 1 SCALE OUT {scale_qty} of {trade.entry_quantity} "
                        f"at {gain_percent:.1f}%"
                    )
                    log_trade_event(
                        db, trade.id, TradeEventType.EXIT_TRIGGERED,
                        f"Tier 1 scale-out: selling {scale_qty} of {trade.entry_quantity} ({gain_percent:.1f}%)",
                        details={
                            "reason": "SCALE_OUT_TIER_1", "current_price": current_price,
                            "gain_percent": round(gain_percent, 2), "scale_qty": scale_qty,
                            "tier": 1, "target_percent": settings.SCALE_OUT_TIER_1_PERCENT,
                        },
                    )
                    await self.order_manager.place_scale_out_order(
                        db, trade, scale_qty, current_price
                    )
                    # Move stop to breakeven — trade becomes risk-free
                    if not trade.breakeven_stop_applied:
                        await self.order_manager.move_stop_to_breakeven(db, trade)
                    return None

            # Tier 2: sell TIER_2_QTY at +TIER_2_PERCENT
            if tiers_done == 1 and gain_percent >= settings.SCALE_OUT_TIER_2_PERCENT:
                scale_qty = min(settings.SCALE_OUT_TIER_2_QTY, remaining - 1)
                if scale_qty > 0:
                    logger.info(
                        f"Trade #{trade.id}: TIER 2 SCALE OUT {scale_qty} of {remaining} remaining "
                        f"at {gain_percent:.1f}%"
                    )
                    log_trade_event(
                        db, trade.id, TradeEventType.EXIT_TRIGGERED,
                        f"Tier 2 scale-out: selling {scale_qty} of {remaining} remaining ({gain_percent:.1f}%)",
                        details={
                            "reason": "SCALE_OUT_TIER_2", "current_price": current_price,
                            "gain_percent": round(gain_percent, 2), "scale_qty": scale_qty,
                            "tier": 2, "target_percent": settings.SCALE_OUT_TIER_2_PERCENT,
                        },
                    )
                    await self.order_manager.place_scale_out_order(
                        db, trade, scale_qty, current_price
                    )
                    return None

            # Tiers done — trailing stop manages runners
        elif gain_percent >= settings.PROFIT_TARGET_PERCENT and not trade.scaled_out:
            # Fallback: full exit at profit target (scale-out disabled or qty=1)
            logger.info(f"Trade #{trade.id}: PROFIT TARGET ({gain_percent:.1f}%)")
            log_trade_event(
                db, trade.id, TradeEventType.EXIT_TRIGGERED,
                f"Profit target reached ({gain_percent:.1f}% >= {settings.PROFIT_TARGET_PERCENT}%)",
                details={
                    "reason": "PROFIT_TARGET", "current_price": current_price,
                    "gain_percent": round(gain_percent, 2),
                    "target_percent": settings.PROFIT_TARGET_PERCENT,
                },
            )
            await self.order_manager.place_exit_order(
                db, trade, ExitReason.PROFIT_TARGET
            )
            return ExitReason.PROFIT_TARGET

        # Priority 5: Trailing stop (tighter after scale-out)
        if trade.highest_price_seen and trade.highest_price_seen > trade.entry_price:
            trail_pct = (
                settings.TRAILING_STOP_AFTER_SCALE_OUT_PERCENT
                if trade.scaled_out
                else settings.TRAILING_STOP_PERCENT
            )
            trail_price = trade.highest_price_seen * (1 - trail_pct / 100)
            trade.trailing_stop_price = trail_price
            db.flush()

            if current_price <= trail_price:
                remaining_qty = trade.entry_quantity - (trade.scaled_out_quantity or 0)
                logger.info(
                    f"Trade #{trade.id}: TRAILING STOP "
                    f"({current_price:.2f} <= {trail_price:.2f}), "
                    f"selling {remaining_qty} contracts"
                )
                log_trade_event(
                    db, trade.id, TradeEventType.EXIT_TRIGGERED,
                    f"Trailing stop hit ({current_price:.2f} <= {trail_price:.2f}, "
                    f"peak={trade.highest_price_seen:.2f}), {remaining_qty} contracts",
                    details={
                        "reason": "TRAILING_STOP", "current_price": current_price,
                        "trail_price": round(trail_price, 2),
                        "highest_price": trade.highest_price_seen,
                        "gain_percent": round(gain_percent, 2),
                        "remaining_quantity": remaining_qty,
                    },
                )
                await self.order_manager.place_exit_order(
                    db, trade, ExitReason.TRAILING_STOP
                )
                return ExitReason.TRAILING_STOP

        return None

    def _get_current_price(self, option_symbol: str) -> Optional[float]:
        try:
            quote = self.schwab.get_quote(option_symbol)
            quote_data = quote.get(option_symbol, {}).get("quote", {})
            bid = quote_data.get("bidPrice", 0)
            ask = quote_data.get("askPrice", 0)
            if bid > 0 and ask > 0:
                return (bid + ask) / 2
            return quote_data.get("lastPrice", None)
        except Exception as e:
            logger.error(f"Error fetching quote for {option_symbol}: {e}")
            return None
