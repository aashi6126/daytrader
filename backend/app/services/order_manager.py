import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import ExitReason, Trade, TradeEventType, TradeStatus
from app.services.schwab_client import SchwabService
from app.services.trade_events import log_trade_event
from app.services.ws_manager import WebSocketManager

logger = logging.getLogger(__name__)
settings = Settings()


class OrderManager:
    def __init__(self, schwab_service: SchwabService, ws_manager: WebSocketManager, streaming_service=None):
        self.schwab = schwab_service
        self.ws_manager = ws_manager
        self.streaming = streaming_service

    async def check_entry_fill(self, db: Session, trade: Trade) -> bool:
        if trade.status != TradeStatus.PENDING:
            return False

        order_data = self.schwab.get_order_status(trade.entry_order_id)
        schwab_status = order_data.get("status", "").upper()

        if schwab_status == "FILLED":
            fill_price = self._extract_fill_price(order_data)
            trade.entry_price = fill_price
            trade.entry_filled_at = datetime.utcnow()
            trade.status = TradeStatus.FILLED
            trade.highest_price_seen = fill_price

            # Compute stop price but DON'T place Schwab order yet (confirmation delay)
            self._compute_stop_price(trade)

            confirm_desc = f"delay {settings.ENTRY_CONFIRM_SECONDS}s"
            if settings.ENTRY_CONFIRM_FAVORABLE_TICK:
                confirm_desc += " + favorable tick"
            log_trade_event(
                db, trade.id, TradeEventType.ENTRY_FILLED,
                f"Entry filled at ${fill_price:.2f}, stop computed at ${trade.stop_loss_price:.2f} "
                f"(confirm: {confirm_desc})",
                details={
                    "fill_price": fill_price, "order_id": trade.entry_order_id,
                    "stop_loss_price": trade.stop_loss_price,
                    "confirm_delay_seconds": settings.ENTRY_CONFIRM_SECONDS,
                    "confirm_favorable_tick": settings.ENTRY_CONFIRM_FAVORABLE_TICK,
                    "atr_value": trade.entry_atr_value,
                    "atr_stop_mult": trade.param_atr_stop_mult,
                },
            )
            db.commit()

            logger.info(
                f"Trade #{trade.id} FILLED at {fill_price:.2f}, "
                f"stop={trade.stop_loss_price:.2f} (confirmation delay {settings.ENTRY_CONFIRM_SECONDS}s)"
            )

            await self.ws_manager.broadcast(
                {
                    "event": "trade_filled",
                    "data": {
                        "trade_id": trade.id,
                        "entry_price": fill_price,
                        "stop_loss_price": trade.stop_loss_price,
                    },
                }
            )
            return True

        elif schwab_status in ("CANCELED", "REJECTED", "EXPIRED"):
            trade.status = TradeStatus.CANCELLED
            log_trade_event(
                db, trade.id, TradeEventType.ENTRY_CANCELLED,
                f"Entry order {schwab_status.lower()}",
                details={"schwab_status": schwab_status, "order_id": trade.entry_order_id},
            )
            db.commit()
            logger.warning(f"Trade #{trade.id} entry order {schwab_status}")
            await self.ws_manager.broadcast(
                {
                    "event": "trade_cancelled",
                    "data": {"trade_id": trade.id, "reason": schwab_status},
                }
            )
            return True

        # Entry limit timeout: cancel the trade — don't chase
        if (
            not trade.entry_is_fallback
            and trade.created_at
            and settings.ENTRY_LIMIT_TIMEOUT_MINUTES > 0
        ):
            elapsed = (datetime.utcnow() - trade.created_at).total_seconds()
            if elapsed >= settings.ENTRY_LIMIT_TIMEOUT_MINUTES * 60:
                old_order_id = trade.entry_order_id
                try:
                    self.schwab.cancel_order(old_order_id)
                except Exception as e:
                    logger.warning(
                        f"Trade #{trade.id}: could not cancel timed-out limit order: {e}"
                    )
                    return False

                trade.status = TradeStatus.CANCELLED
                log_trade_event(
                    db, trade.id, TradeEventType.ENTRY_CANCELLED,
                    f"Limit order timed out after {settings.ENTRY_LIMIT_TIMEOUT_MINUTES} min — "
                    f"setup expired, not chasing (order {old_order_id})",
                    details={
                        "old_order_id": old_order_id,
                        "elapsed_seconds": round(elapsed, 1),
                        "original_limit_price": trade.alert_option_price,
                    },
                )
                db.commit()

                logger.info(
                    f"Trade #{trade.id}: limit timeout after "
                    f"{elapsed:.0f}s — cancelled (not chasing)"
                )

                await self.ws_manager.broadcast({
                    "event": "trade_cancelled",
                    "data": {
                        "trade_id": trade.id,
                        "reason": "LIMIT_TIMEOUT",
                    },
                })
                return True

        return False

    def _compute_stop_price(self, trade: Trade) -> None:
        """Compute stop_loss_price without placing Schwab order (for confirmation delay)."""
        if trade.entry_atr_value and trade.param_atr_stop_mult:
            delta_approx = trade.param_delta_target or 0.4
            atr_offset = trade.entry_atr_value * trade.param_atr_stop_mult * delta_approx
            trade.stop_loss_price = round(max(trade.entry_price - atr_offset, 0.01), 2)
        else:
            sl_pct = trade.param_stop_loss_percent or settings.STOP_LOSS_PERCENT
            trade.stop_loss_price = round(trade.entry_price * (1 - sl_pct / 100), 2)

    async def _place_stop_loss(self, db: Session, trade: Trade) -> None:
        """Place the actual Schwab STOP order (called after confirmation delay)."""
        # Recompute stop if not already set
        if not trade.stop_loss_price:
            self._compute_stop_price(trade)

        stop_price = trade.stop_loss_price
        remaining_qty = trade.entry_quantity - (trade.scaled_out_quantity or 0)

        order = SchwabService.build_stop_loss_order(
            option_symbol=trade.option_symbol,
            quantity=remaining_qty,
            stop_price=stop_price,
        )

        atr_info = ""
        if trade.entry_atr_value and trade.param_atr_stop_mult:
            atr_info = f", ATR-based (ATR={trade.entry_atr_value:.4f}x{trade.param_atr_stop_mult})"
        else:
            sl_pct = trade.param_stop_loss_percent or settings.STOP_LOSS_PERCENT
            atr_info = f", {sl_pct}% SL"

        try:
            order_id = self.schwab.place_order(order)
            trade.stop_loss_order_id = order_id
            trade.status = TradeStatus.STOP_LOSS_PLACED
            log_trade_event(
                db, trade.id, TradeEventType.STOP_LOSS_PLACED,
                f"Stop-loss placed at ${stop_price:.2f}{atr_info} (order={order_id})",
                details={
                    "stop_price": stop_price, "order_id": order_id,
                    "atr_value": trade.entry_atr_value,
                    "atr_stop_mult": trade.param_atr_stop_mult,
                },
            )
            db.commit()
            logger.info(
                f"Trade #{trade.id} stop-loss at {stop_price:.2f}{atr_info} (order={order_id})"
            )
        except Exception as e:
            logger.warning(
                f"Trade #{trade.id} stop-loss rejected by Schwab: {e}. "
                f"Using app-managed stop-loss."
            )
            trade.status = TradeStatus.STOP_LOSS_PLACED
            log_trade_event(
                db, trade.id, TradeEventType.STOP_LOSS_PLACED,
                f"App-managed stop-loss at ${stop_price:.2f} (Schwab rejected: {e})",
                details={"stop_price": stop_price, "app_managed": True, "error": str(e)},
            )
            db.commit()

    async def move_stop_to_breakeven(self, db: Session, trade: Trade) -> None:
        """Cancel existing stop-loss and re-place at entry price (breakeven)."""
        remaining_qty = trade.entry_quantity - (trade.scaled_out_quantity or 0)
        breakeven_price = trade.entry_price

        # Cancel existing Schwab stop-loss
        if trade.stop_loss_order_id:
            try:
                self.schwab.cancel_order(trade.stop_loss_order_id)
                log_trade_event(
                    db, trade.id, TradeEventType.STOP_LOSS_CANCELLED,
                    f"Stop-loss cancelled for breakeven move",
                    details={"order_id": trade.stop_loss_order_id},
                )
                trade.stop_loss_order_id = None
            except Exception as e:
                logger.warning(f"Trade #{trade.id}: could not cancel stop-loss for breakeven: {e}")
                return

        # Place new stop-loss at breakeven
        trade.stop_loss_price = breakeven_price
        order = SchwabService.build_stop_loss_order(
            option_symbol=trade.option_symbol,
            quantity=remaining_qty,
            stop_price=breakeven_price,
        )
        try:
            order_id = self.schwab.place_order(order)
            trade.stop_loss_order_id = order_id
            log_trade_event(
                db, trade.id, TradeEventType.BREAKEVEN_STOP_MOVED,
                f"Stop-loss moved to breakeven ${breakeven_price:.2f} ({remaining_qty}x, order={order_id})",
                details={"stop_price": breakeven_price, "order_id": order_id, "quantity": remaining_qty},
            )
        except Exception as e:
            logger.warning(f"Trade #{trade.id}: breakeven stop rejected by Schwab: {e}. Using app-managed.")
            log_trade_event(
                db, trade.id, TradeEventType.BREAKEVEN_STOP_MOVED,
                f"App-managed breakeven stop at ${breakeven_price:.2f} (Schwab rejected: {e})",
                details={"stop_price": breakeven_price, "app_managed": True, "error": str(e)},
            )

        trade.breakeven_stop_applied = True
        db.commit()

        logger.info(f"Trade #{trade.id} stop-loss moved to breakeven ${breakeven_price:.2f}")

        await self.ws_manager.broadcast({
            "event": "trade_breakeven_stop",
            "data": {
                "trade_id": trade.id,
                "stop_loss_price": breakeven_price,
            },
        })

    async def place_scale_out_order(
        self,
        db: Session,
        trade: Trade,
        quantity: int,
        current_price: float,
    ) -> None:
        """Scale-out: sell partial position at market, re-place stop-loss for remainder."""
        # Cancel existing stop-loss (will re-place for remaining qty)
        if trade.stop_loss_order_id:
            try:
                self.schwab.cancel_order(trade.stop_loss_order_id)
                log_trade_event(
                    db, trade.id, TradeEventType.STOP_LOSS_CANCELLED,
                    f"Stop-loss cancelled for scale-out",
                    details={"order_id": trade.stop_loss_order_id},
                )
                trade.stop_loss_order_id = None
            except Exception as e:
                logger.warning(f"Trade #{trade.id}: could not cancel stop-loss for scale-out: {e}")

        # Place market sell for scale-out quantity
        order = SchwabService.build_option_sell_order(
            option_symbol=trade.option_symbol,
            quantity=quantity,
            order_type="MARKET",
        )
        order_id = self.schwab.place_order(order)

        # Update trade with cumulative scale-out info
        trade.scaled_out = True
        prev_qty = trade.scaled_out_quantity or 0
        prev_price = trade.scaled_out_price or 0
        if prev_qty + quantity > 0:
            trade.scaled_out_price = ((prev_price * prev_qty) + (current_price * quantity)) / (prev_qty + quantity)
        else:
            trade.scaled_out_price = current_price
        trade.scaled_out_quantity = prev_qty + quantity
        trade.scaled_out_order_id = order_id
        trade.scale_out_count = (trade.scale_out_count or 0) + 1

        tier = trade.scale_out_count
        log_trade_event(
            db, trade.id, TradeEventType.SCALE_OUT,
            f"Tier {tier} scale-out: sold {quantity}x at ~${current_price:.2f} (market), order={order_id}",
            details={
                "order_id": order_id, "quantity": quantity,
                "approx_price": current_price, "tier": tier,
                "total_scaled_out": trade.scaled_out_quantity,
            },
        )

        # Re-place stop-loss for remaining quantity
        remaining_qty = trade.entry_quantity - trade.scaled_out_quantity
        if trade.stop_loss_price and remaining_qty > 0:
            try:
                sl_order = SchwabService.build_stop_loss_order(
                    option_symbol=trade.option_symbol,
                    quantity=remaining_qty,
                    stop_price=trade.stop_loss_price,
                )
                sl_order_id = self.schwab.place_order(sl_order)
                trade.stop_loss_order_id = sl_order_id
                log_trade_event(
                    db, trade.id, TradeEventType.STOP_LOSS_PLACED,
                    f"Stop-loss re-placed for remaining {remaining_qty}x at ${trade.stop_loss_price:.2f} (order={sl_order_id})",
                    details={"order_id": sl_order_id, "quantity": remaining_qty, "stop_price": trade.stop_loss_price},
                )
            except Exception as e:
                logger.warning(f"Trade #{trade.id}: could not re-place stop-loss after scale-out: {e}")
                trade.stop_loss_order_id = None  # App-managed fallback

        db.commit()

        logger.info(
            f"Trade #{trade.id} scale-out: sold {quantity}x, "
            f"remaining {remaining_qty}x, order={order_id}"
        )

        await self.ws_manager.broadcast(
            {
                "event": "trade_scale_out",
                "data": {
                    "trade_id": trade.id,
                    "scale_out_quantity": quantity,
                    "scale_out_price": current_price,
                    "remaining_quantity": remaining_qty,
                },
            }
        )

    async def place_exit_order(
        self,
        db: Session,
        trade: Trade,
        exit_reason: ExitReason,
        limit_price: float = None,
        current_price: float = None,
    ) -> None:
        # Cancel existing stop-loss if managed by Schwab
        if trade.stop_loss_order_id:
            try:
                self.schwab.cancel_order(trade.stop_loss_order_id)
                log_trade_event(
                    db, trade.id, TradeEventType.STOP_LOSS_CANCELLED,
                    f"Stop-loss order {trade.stop_loss_order_id} cancelled for exit",
                    details={"order_id": trade.stop_loss_order_id},
                )
                logger.info(f"Trade #{trade.id} stop-loss cancelled for exit")
            except Exception as e:
                logger.warning(f"Could not cancel stop-loss order: {e}")

        # Sell remaining quantity (accounts for scale-out)
        remaining_qty = trade.entry_quantity - (trade.scaled_out_quantity or 0)

        order_type = "LIMIT" if limit_price else "MARKET"
        order = SchwabService.build_option_sell_order(
            option_symbol=trade.option_symbol,
            quantity=remaining_qty,
            order_type=order_type,
            limit_price=limit_price,
        )
        # For dry-run simulation: store current price so market orders fill realistically
        if current_price and not limit_price:
            order["_sim_price"] = str(current_price)

        order_id = self.schwab.place_order(order)
        trade.exit_order_id = order_id
        trade.exit_reason = exit_reason
        trade.status = TradeStatus.EXITING
        log_trade_event(
            db, trade.id, TradeEventType.EXIT_ORDER_PLACED,
            f"Exit order placed: {order_type} sell {remaining_qty}x, reason={exit_reason.value}, order={order_id}",
            details={"order_id": order_id, "order_type": order_type, "exit_reason": exit_reason.value, "limit_price": limit_price, "quantity": remaining_qty},
        )
        db.commit()

        logger.info(
            f"Trade #{trade.id} exit: reason={exit_reason.value}, "
            f"type={order_type}, qty={remaining_qty}, order={order_id}"
        )

    async def check_exit_fill(self, db: Session, trade: Trade) -> bool:
        if trade.status != TradeStatus.EXITING:
            return False

        order_data = self.schwab.get_order_status(trade.exit_order_id)
        schwab_status = order_data.get("status", "").upper()

        if schwab_status == "FILLED":
            fill_price = self._extract_fill_price(order_data)
            trade.exit_price = fill_price
            trade.exit_filled_at = datetime.utcnow()
            trade.status = TradeStatus.CLOSED

            # PnL: remaining position + scale-out portion (if any)
            remaining_qty = trade.entry_quantity - (trade.scaled_out_quantity or 0)
            remaining_pnl = (fill_price - trade.entry_price) * remaining_qty * 100
            scale_out_pnl = 0.0
            if trade.scaled_out and trade.scaled_out_price:
                scale_out_pnl = (trade.scaled_out_price - trade.entry_price) * (trade.scaled_out_quantity or 0) * 100

            trade.pnl_dollars = remaining_pnl + scale_out_pnl
            trade.pnl_percent = (
                (trade.pnl_dollars / (trade.entry_price * trade.entry_quantity * 100)) * 100
                if trade.entry_price > 0
                else 0
            )
            log_trade_event(
                db, trade.id, TradeEventType.EXIT_FILLED,
                f"Exit filled at ${fill_price:.2f} ({remaining_qty}x) — PnL ${trade.pnl_dollars:.2f} ({trade.pnl_percent:+.1f}%)",
                details={
                    "fill_price": fill_price, "pnl_dollars": trade.pnl_dollars,
                    "pnl_percent": round(trade.pnl_percent, 2),
                    "exit_reason": trade.exit_reason.value if trade.exit_reason else None,
                    "remaining_qty": remaining_qty,
                    "scale_out_pnl": round(scale_out_pnl, 2) if scale_out_pnl else None,
                },
            )
            db.commit()

            logger.info(
                f"Trade #{trade.id} CLOSED: exit={fill_price:.2f}, "
                f"PnL=${trade.pnl_dollars:.2f} ({trade.pnl_percent:+.1f}%)"
            )

            await self.ws_manager.broadcast(
                {
                    "event": "trade_closed",
                    "data": {
                        "trade_id": trade.id,
                        "exit_price": fill_price,
                        "pnl_dollars": trade.pnl_dollars,
                        "pnl_percent": trade.pnl_percent,
                        "exit_reason": (
                            trade.exit_reason.value if trade.exit_reason else None
                        ),
                    },
                }
            )
            return True

        return False

    def check_stop_loss_fill(self, db: Session, trade: Trade) -> bool:
        if not trade.stop_loss_order_id or trade.status == TradeStatus.CLOSED:
            return False

        try:
            order_data = self.schwab.get_order_status(trade.stop_loss_order_id)
            schwab_status = order_data.get("status", "").upper()

            if schwab_status == "FILLED":
                fill_price = self._extract_fill_price(order_data)
                trade.exit_price = fill_price
                trade.exit_filled_at = datetime.utcnow()
                trade.exit_reason = ExitReason.STOP_LOSS
                trade.status = TradeStatus.CLOSED

                # PnL: remaining position + scale-out portion (if any)
                remaining_qty = trade.entry_quantity - (trade.scaled_out_quantity or 0)
                remaining_pnl = (fill_price - trade.entry_price) * remaining_qty * 100
                scale_out_pnl = 0.0
                if trade.scaled_out and trade.scaled_out_price:
                    scale_out_pnl = (trade.scaled_out_price - trade.entry_price) * (trade.scaled_out_quantity or 0) * 100

                trade.pnl_dollars = remaining_pnl + scale_out_pnl
                trade.pnl_percent = (
                    (trade.pnl_dollars / (trade.entry_price * trade.entry_quantity * 100)) * 100
                    if trade.entry_price > 0
                    else 0
                )
                log_trade_event(
                    db, trade.id, TradeEventType.STOP_LOSS_HIT,
                    f"Stop-loss hit at ${fill_price:.2f} ({remaining_qty}x) — PnL ${trade.pnl_dollars:.2f} ({trade.pnl_percent:+.1f}%)",
                    details={
                        "fill_price": fill_price, "pnl_dollars": trade.pnl_dollars,
                        "pnl_percent": round(trade.pnl_percent, 2),
                        "remaining_qty": remaining_qty,
                        "scale_out_pnl": round(scale_out_pnl, 2) if scale_out_pnl else None,
                    },
                )
                db.commit()
                logger.info(
                    f"Trade #{trade.id} STOP-LOSS HIT at {fill_price:.2f}"
                )
                return True
        except Exception as e:
            logger.error(
                f"Error checking stop-loss for trade #{trade.id}: {e}"
            )

        return False

    def _get_current_mid(self, option_symbol: str) -> Optional[float]:
        # Try streaming cache first
        if self.streaming:
            snap = self.streaming.get_option_quote(option_symbol)
            if snap and not snap.is_stale:
                return snap.mid

        # REST fallback
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

    @staticmethod
    def _extract_fill_price(order_data: dict) -> float:
        activities = order_data.get("orderActivityCollection", [])
        if activities:
            execution_legs = activities[0].get("executionLegs", [])
            if execution_legs:
                return execution_legs[0].get("price", 0.0)
        return float(order_data.get("price", 0.0))
