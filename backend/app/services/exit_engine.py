import logging
from datetime import datetime, time
from typing import Optional

import pytz
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import ExitReason, Trade, TradeEventType, TradePriceSnapshot, TradeStatus
from app.services.option_selector import _0DTE_TICKERS
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
        streaming_service=None,
    ):
        self.schwab = schwab_service
        self.order_manager = order_manager
        self.streaming = streaming_service

    async def evaluate_position(
        self, db: Session, trade: Trade, now_et: Optional[datetime] = None,
        skip_snapshot: bool = False,
    ) -> Optional[ExitReason]:
        if trade.status not in (TradeStatus.FILLED, TradeStatus.STOP_LOSS_PLACED):
            return None

        if now_et is None:
            now_et = datetime.now(ET)

        price_data = self._get_price_data(trade.option_symbol)
        if price_data is None:
            logger.warning(f"Trade #{trade.id}: could not get current price")
            return None

        current_price = price_data["mid"]
        bid_price = price_data["bid"]
        spread_pct = price_data["spread_pct"]

        # Update high-water mark using BID (what we'd actually get on exit)
        if bid_price > (trade.highest_price_seen or 0):
            trade.highest_price_seen = bid_price
            db.flush()

        # Record price snapshot for post-trade analysis
        # (skipped when PriceRecorderTask handles recording via streaming)
        if not skip_snapshot:
            db.add(TradePriceSnapshot(
                trade_id=trade.id,
                price=current_price,
                highest_price_seen=trade.highest_price_seen or bid_price,
            ))
            db.flush()

        gain_percent = (
            ((current_price - trade.entry_price) / trade.entry_price) * 100
            if trade.entry_price > 0
            else 0
        )

        # Resolve per-trade exit params (strategy-specific) with global fallback
        max_hold = trade.param_max_hold_minutes or settings.MAX_HOLD_MINUTES
        profit_target_pct = trade.param_profit_target_percent or settings.PROFIT_TARGET_PERCENT
        trailing_stop_pct = trade.param_trailing_stop_percent or settings.TRAILING_STOP_PERCENT

        # Priority 1: Force exit (3:30 PM ET) — only for 0DTE options
        is_0dte = (trade.ticker or "").upper() in _0DTE_TICKERS
        force_exit_time = time(settings.FORCE_EXIT_HOUR, settings.FORCE_EXIT_MINUTE)
        if is_0dte and now_et.time() >= force_exit_time:
            logger.warning(f"Trade #{trade.id}: FORCE EXIT at {now_et.time()}")
            log_trade_event(
                db, trade.id, TradeEventType.EXIT_TRIGGERED,
                f"Force exit triggered at {now_et.strftime('%H:%M')} ET (cutoff {settings.FORCE_EXIT_HOUR}:{settings.FORCE_EXIT_MINUTE:02d})",
                details={"reason": "TIME_BASED", "current_time": str(now_et.time()), "current_price": current_price, "gain_percent": round(gain_percent, 2)},
            )
            await self.order_manager.place_exit_order(
                db, trade, ExitReason.TIME_BASED, current_price=current_price
            )
            return ExitReason.TIME_BASED

        # Priority 2: Max hold time
        if trade.entry_filled_at:
            filled_at = trade.entry_filled_at
            if filled_at.tzinfo is None:
                filled_at = pytz.utc.localize(filled_at)
            now_utc = now_et.astimezone(pytz.utc)
            elapsed_minutes = (now_utc - filled_at).total_seconds() / 60
            if elapsed_minutes >= max_hold:
                logger.info(
                    f"Trade #{trade.id}: MAX HOLD TIME ({elapsed_minutes:.0f} min >= {max_hold} min)"
                )
                log_trade_event(
                    db, trade.id, TradeEventType.EXIT_TRIGGERED,
                    f"Max hold time reached ({elapsed_minutes:.0f} min >= {max_hold} min)",
                    details={"reason": "MAX_HOLD_TIME", "elapsed_minutes": round(elapsed_minutes, 1), "max_hold_minutes": max_hold, "current_price": current_price, "gain_percent": round(gain_percent, 2)},
                )
                await self.order_manager.place_exit_order(
                    db, trade, ExitReason.MAX_HOLD_TIME, current_price=current_price
                )
                return ExitReason.MAX_HOLD_TIME

        # Priority 2.5: Entry confirmation delay — don't place Schwab stop yet
        # Two conditions must be met before placing the broker stop:
        #   a) Minimum time elapsed (ENTRY_CONFIRM_SECONDS)
        #   b) Price has ticked above entry (ENTRY_CONFIRM_FAVORABLE_TICK)
        # This prevents immediate stop-outs from bid-ask noise on entry fill.
        if (
            trade.status == TradeStatus.FILLED
            and not trade.stop_loss_order_id
            and trade.entry_filled_at
        ):
            conf_filled_at = trade.entry_filled_at
            if conf_filled_at.tzinfo is None:
                conf_filled_at = pytz.utc.localize(conf_filled_at)
            conf_now_utc = now_et.astimezone(pytz.utc)
            elapsed_secs = (conf_now_utc - conf_filled_at).total_seconds()

            time_ok = (
                elapsed_secs >= settings.ENTRY_CONFIRM_SECONDS
                if settings.ENTRY_CONFIRM_SECONDS > 0
                else True
            )
            tick_ok = (
                trade.highest_price_seen is not None
                and trade.highest_price_seen > trade.entry_price
            ) if settings.ENTRY_CONFIRM_FAVORABLE_TICK else True

            if not time_ok or not tick_ok:
                # During confirmation: only exit on emergency loss
                if current_price <= trade.entry_price * (1 - settings.ENTRY_CONFIRM_EMERGENCY_PCT / 100):
                    logger.warning(
                        f"Trade #{trade.id}: EMERGENCY EXIT during confirmation "
                        f"({gain_percent:.1f}% drop in {elapsed_secs:.0f}s)"
                    )
                    log_trade_event(
                        db, trade.id, TradeEventType.EXIT_TRIGGERED,
                        f"Emergency exit during confirmation ({gain_percent:.1f}% drop, "
                        f"{elapsed_secs:.0f}s after fill)",
                        details={
                            "reason": "STOP_LOSS", "current_price": current_price,
                            "gain_percent": round(gain_percent, 2),
                            "elapsed_seconds": round(elapsed_secs, 1),
                            "emergency_threshold_pct": settings.ENTRY_CONFIRM_EMERGENCY_PCT,
                        },
                    )
                    await self.order_manager.place_exit_order(
                        db, trade, ExitReason.STOP_LOSS, current_price=current_price
                    )
                    return ExitReason.STOP_LOSS
                # Still in confirmation window — skip all other checks
                if not time_ok:
                    logger.debug(
                        f"Trade #{trade.id}: confirmation wait ({elapsed_secs:.0f}s / "
                        f"{settings.ENTRY_CONFIRM_SECONDS}s)"
                    )
                elif not tick_ok:
                    logger.debug(
                        f"Trade #{trade.id}: waiting for favorable tick "
                        f"(peak=${trade.highest_price_seen:.2f}, entry=${trade.entry_price:.2f})"
                    )
                return None
            else:
                # Both conditions met — place the real Schwab stop order
                logger.info(
                    f"Trade #{trade.id}: confirmation complete ({elapsed_secs:.0f}s, "
                    f"peak=${trade.highest_price_seen:.2f} > entry=${trade.entry_price:.2f}), "
                    f"placing stop at ${trade.stop_loss_price:.2f}"
                )
                await self.order_manager._place_stop_loss(db, trade)

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
                    db, trade, ExitReason.STOP_LOSS, current_price=current_price
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
        elif gain_percent >= profit_target_pct and not trade.scaled_out:
            # Fallback: full exit at profit target (scale-out disabled or qty=1)
            # Use limit at mid — price is favorable, try for better fill than bid
            logger.info(
                f"Trade #{trade.id}: PROFIT TARGET ({gain_percent:.1f}% >= {profit_target_pct}%), "
                f"limit exit at mid ${current_price:.2f} (bid ${bid_price:.2f}, spread {spread_pct:.1f}%)"
            )
            log_trade_event(
                db, trade.id, TradeEventType.EXIT_TRIGGERED,
                f"Profit target reached ({gain_percent:.1f}% >= {profit_target_pct}%), "
                f"limit exit at mid ${current_price:.2f} (bid ${bid_price:.2f}, spread {spread_pct:.1f}%)",
                details={
                    "reason": "PROFIT_TARGET", "mid_price": current_price,
                    "bid_price": bid_price, "spread_pct": round(spread_pct, 1),
                    "gain_percent": round(gain_percent, 2),
                    "target_percent": profit_target_pct,
                },
            )
            await self.order_manager.place_exit_order(
                db, trade, ExitReason.PROFIT_TARGET,
                limit_price=current_price, current_price=current_price,
            )
            return ExitReason.PROFIT_TARGET

        # Priority 5: Trailing stop — spread-aware, uses BID price
        # Skip trailing stop when spread is too wide (unreliable prices)
        if spread_pct > settings.EXIT_MAX_SPREAD_PERCENT:
            logger.debug(
                f"Trade #{trade.id}: skipping trailing stop eval, "
                f"spread {spread_pct:.1f}% > {settings.EXIT_MAX_SPREAD_PERCENT}%"
            )
            return None

        # Trailing stop only activates after price has risen meaningfully above entry.
        # Before activation: only the hard stop-loss and breakeven stop protect the position.
        # After scale-out: trailing always active (position already de-risked).
        trail_activation_pct = (
            0 if trade.scaled_out
            else settings.TRAILING_STOP_ACTIVATION_PERCENT
        )
        peak_gain_from_entry = (
            ((trade.highest_price_seen - trade.entry_price) / trade.entry_price) * 100
            if trade.highest_price_seen and trade.entry_price > 0
            else 0
        )

        if trade.highest_price_seen and peak_gain_from_entry >= trail_activation_pct:
            trail_pct = (
                settings.TRAILING_STOP_AFTER_SCALE_OUT_PERCENT
                if trade.scaled_out
                else trailing_stop_pct
            )
            trail_price = trade.highest_price_seen * (1 - trail_pct / 100)
            trade.trailing_stop_price = trail_price
            db.flush()

            # Use BID for trigger — this is what we'd actually receive
            if bid_price <= trail_price:
                remaining_qty = trade.entry_quantity - (trade.scaled_out_quantity or 0)
                logger.info(
                    f"Trade #{trade.id}: TRAILING STOP "
                    f"(bid {bid_price:.2f} <= trail {trail_price:.2f}, "
                    f"spread {spread_pct:.1f}%), "
                    f"selling {remaining_qty} contracts at limit ${bid_price:.2f}"
                )
                log_trade_event(
                    db, trade.id, TradeEventType.EXIT_TRIGGERED,
                    f"Trailing stop hit (bid {bid_price:.2f} <= {trail_price:.2f}, "
                    f"peak={trade.highest_price_seen:.2f}, spread={spread_pct:.1f}%), "
                    f"{remaining_qty} contracts, limit exit at ${bid_price:.2f}",
                    details={
                        "reason": "TRAILING_STOP",
                        "bid_price": bid_price,
                        "mid_price": current_price,
                        "spread_pct": round(spread_pct, 1),
                        "trail_price": round(trail_price, 2),
                        "highest_price": trade.highest_price_seen,
                        "gain_percent": round(gain_percent, 2),
                        "remaining_quantity": remaining_qty,
                    },
                )
                # Exit with LIMIT at bid — avoids filling below bid in wide spreads
                await self.order_manager.place_exit_order(
                    db, trade, ExitReason.TRAILING_STOP,
                    limit_price=bid_price, current_price=current_price,
                )
                return ExitReason.TRAILING_STOP

        return None

    def _get_current_price(self, option_symbol: str) -> Optional[float]:
        """Return mid-price (legacy, used for non-trailing-stop checks)."""
        data = self._get_price_data(option_symbol)
        return data["mid"] if data else None

    def _get_price_data(self, option_symbol: str) -> Optional[dict]:
        """Return bid, ask, mid, and spread_pct for spread-aware decisions.

        Checks streaming cache first, falls back to REST API.
        """
        # Try streaming cache first
        if self.streaming:
            snap = self.streaming.get_option_quote(option_symbol)
            if snap and not snap.is_stale and snap.bid > 0 and snap.ask > 0:
                return {
                    "bid": snap.bid,
                    "ask": snap.ask,
                    "mid": snap.mid,
                    "spread_pct": snap.spread_pct,
                }

        # REST fallback
        try:
            quote = self.schwab.get_quote(option_symbol)
            quote_data = quote.get(option_symbol, {}).get("quote", {})
            bid = quote_data.get("bidPrice", 0)
            ask = quote_data.get("askPrice", 0)
            last = quote_data.get("lastPrice", 0)
            if bid > 0 and ask > 0:
                mid = (bid + ask) / 2
                spread_pct = ((ask - bid) / mid) * 100 if mid > 0 else 999
                return {"bid": bid, "ask": ask, "mid": mid, "spread_pct": spread_pct}
            if last > 0:
                return {"bid": last, "ask": last, "mid": last, "spread_pct": 0}
            return None
        except Exception as e:
            logger.error(f"Error fetching quote for {option_symbol}: {e}")
            return None
