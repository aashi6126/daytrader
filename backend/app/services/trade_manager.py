from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Alert, AlertStatus, ExitReason, Trade, TradeDirection, TradeEventType, TradeStatus
from app.schemas import TradingViewAlert, WebhookResponse
from app.services.option_selector import OptionSelector
from app.services.schwab_client import SchwabService
from app.services.trade_events import log_trade_event
from app.services.ws_manager import WebSocketManager

logger = logging.getLogger(__name__)
settings = Settings()

ACTIVE_STATUSES = [TradeStatus.FILLED, TradeStatus.STOP_LOSS_PLACED]


class TradeManager:
    def __init__(
        self,
        schwab_service: SchwabService,
        option_selector: OptionSelector,
        ws_manager: WebSocketManager,
    ):
        self.schwab = schwab_service
        self.selector = option_selector
        self.ws_manager = ws_manager

    def get_daily_trade_count(self, db: Session) -> int:
        today = date.today()
        count = (
            db.query(func.count(Trade.id))
            .filter(Trade.trade_date == today)
            .filter(Trade.status != TradeStatus.CANCELLED)
            .scalar()
        )
        return count or 0

    def get_daily_pnl(self, db: Session) -> float:
        today = date.today()
        total = (
            db.query(func.sum(Trade.pnl_dollars))
            .filter(Trade.trade_date == today)
            .filter(Trade.status == TradeStatus.CLOSED)
            .scalar()
        )
        return total or 0.0

    def _get_consecutive_losses(self, db: Session) -> int:
        """Count consecutive losses from the most recent closed trades today.

        Only counts signal-based trades (tradingview, orb_auto); manual trades
        (retake, test, etc.) are ignored so they don't trigger the pause.
        """
        recent_closed = (
            db.query(Trade)
            .filter(Trade.trade_date == date.today())
            .filter(Trade.status == TradeStatus.CLOSED)
            .filter(Trade.source.in_(["tradingview", "orb_auto"]))
            .order_by(Trade.exit_filled_at.desc())
            .all()
        )
        count = 0
        for t in recent_closed:
            if (t.pnl_dollars or 0) < 0:
                count += 1
            else:
                break
        return count

    def _get_active_trade(self, db: Session) -> Trade | None:
        """Get the most recent active (open) trade."""
        return (
            db.query(Trade)
            .filter(Trade.status.in_(ACTIVE_STATUSES))
            .order_by(Trade.id.desc())
            .first()
        )

    async def _close_trade(self, db: Session, trade: Trade, reason: ExitReason, log_msg: str) -> None:
        """Close a trade via market sell. Reusable by reverse-close and CLOSE signal."""
        log_trade_event(db, trade.id, TradeEventType.CLOSE_SIGNAL, log_msg)

        # Cancel stop-loss if present
        if trade.stop_loss_order_id:
            try:
                self.schwab.cancel_order(trade.stop_loss_order_id)
                log_trade_event(
                    db, trade.id, TradeEventType.STOP_LOSS_CANCELLED,
                    f"Stop-loss order {trade.stop_loss_order_id} cancelled",
                    details={"order_id": trade.stop_loss_order_id},
                )
                logger.info(f"Trade #{trade.id}: cancelled stop-loss {trade.stop_loss_order_id}")
            except Exception as e:
                logger.warning(f"Trade #{trade.id}: could not cancel stop-loss: {e}")

        # Determine remaining quantity (account for scale-out)
        remaining_qty = trade.entry_quantity - (trade.scaled_out_quantity or 0)

        sell_order = SchwabService.build_option_sell_order(
            option_symbol=trade.option_symbol,
            quantity=remaining_qty,
            order_type="MARKET",
        )
        order_id = self.schwab.place_order(sell_order)

        trade.exit_order_id = order_id
        trade.exit_reason = reason
        trade.status = TradeStatus.EXITING

        log_trade_event(
            db, trade.id, TradeEventType.EXIT_ORDER_PLACED,
            f"Market sell order placed ({remaining_qty} contracts), order={order_id}",
            details={"order_id": order_id, "exit_reason": reason.value, "order_type": "MARKET", "quantity": remaining_qty},
        )
        db.flush()

        logger.info(f"Trade #{trade.id}: closing ({reason.value}), market sell order={order_id}")

    async def process_alert(
        self,
        db: Session,
        db_alert: Alert,
        alert: TradingViewAlert,
    ) -> WebhookResponse:
        # 1. Check daily trade count limit
        trade_count = self.get_daily_trade_count(db)
        if trade_count >= settings.MAX_DAILY_TRADES:
            db_alert.status = AlertStatus.REJECTED
            db_alert.rejection_reason = (
                f"Daily trade limit reached ({settings.MAX_DAILY_TRADES})"
            )
            db.commit()
            logger.warning(
                f"Trade rejected: daily limit ({trade_count}/{settings.MAX_DAILY_TRADES})"
            )
            return WebhookResponse(
                status="rejected",
                message=f"Daily trade limit reached ({trade_count}/{settings.MAX_DAILY_TRADES})",
            )

        # 1b. Check daily loss limit
        daily_pnl = self.get_daily_pnl(db)
        if daily_pnl <= -settings.MAX_DAILY_LOSS:
            db_alert.status = AlertStatus.REJECTED
            db_alert.rejection_reason = (
                f"Daily loss limit reached (${daily_pnl:.2f})"
            )
            db.commit()
            logger.warning(
                f"Trade rejected: daily loss limit (${daily_pnl:.2f} <= -${settings.MAX_DAILY_LOSS:.2f})"
            )
            return WebhookResponse(
                status="rejected",
                message=f"Daily loss limit reached (${daily_pnl:.2f}). Max loss: ${settings.MAX_DAILY_LOSS:.2f}",
            )

        # 1c. Consecutive loss check — pause if too many losses in a row
        consec_losses = self._get_consecutive_losses(db)
        if consec_losses >= settings.MAX_CONSECUTIVE_LOSSES:
            db_alert.status = AlertStatus.REJECTED
            db_alert.rejection_reason = f"{consec_losses} consecutive losses — paused"
            db.commit()
            logger.warning(f"Trade rejected: {consec_losses} consecutive losses (max {settings.MAX_CONSECUTIVE_LOSSES})")
            return WebhookResponse(
                status="rejected",
                message=f"Trading paused: {consec_losses} consecutive losses",
            )

        # 1d. Trade cooldown — reject if last trade was created too recently
        cooldown_cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.TRADE_COOLDOWN_MINUTES)
        recent_trade = (
            db.query(Trade)
            .filter(Trade.trade_date == date.today())
            .filter(Trade.status != TradeStatus.CANCELLED)
            .filter(Trade.created_at >= cooldown_cutoff)
            .first()
        )
        if recent_trade:
            db_alert.status = AlertStatus.REJECTED
            db_alert.rejection_reason = f"Trade cooldown ({settings.TRADE_COOLDOWN_MINUTES} min)"
            db.commit()
            logger.info(f"Trade rejected: cooldown active (trade #{recent_trade.id} is recent)")
            return WebhookResponse(
                status="rejected",
                message=f"Trade cooldown: must wait {settings.TRADE_COOLDOWN_MINUTES} min between trades",
            )

        # 1e. Signal debounce — reject if opposite-direction alert arrived recently
        debounce_cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.SIGNAL_DEBOUNCE_MINUTES)
        opposite_direction = TradeDirection.PUT if alert.direction == TradeDirection.CALL else TradeDirection.CALL
        recent_opposite = (
            db.query(Alert)
            .filter(Alert.received_at >= debounce_cutoff)
            .filter(Alert.direction == opposite_direction)
            .first()
        )
        if recent_opposite:
            db_alert.status = AlertStatus.REJECTED
            db_alert.rejection_reason = f"Signal debounce (opposite {opposite_direction.value} within {settings.SIGNAL_DEBOUNCE_MINUTES} min)"
            db.commit()
            logger.info(f"Trade rejected: signal debounce, opposite {opposite_direction.value} alert at {recent_opposite.received_at}")
            return WebhookResponse(
                status="rejected",
                message=f"Signal debounce: opposite direction alert within {settings.SIGNAL_DEBOUNCE_MINUTES} min",
            )

        # 1f. Volatility filter — reject if recent signal prices show consolidation
        vol_cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
        recent_prices = (
            db.query(Alert.signal_price)
            .filter(Alert.received_at >= vol_cutoff)
            .filter(Alert.signal_price.isnot(None))
            .all()
        )
        if len(recent_prices) >= 3:
            prices = [p[0] for p in recent_prices]
            price_range = max(prices) - min(prices)
            if price_range < settings.MIN_PRICE_RANGE:
                db_alert.status = AlertStatus.REJECTED
                db_alert.rejection_reason = f"Low volatility (${price_range:.2f} range in 5 min)"
                db.commit()
                logger.info(f"Trade rejected: low volatility, ${price_range:.2f} range < ${settings.MIN_PRICE_RANGE:.2f}")
                return WebhookResponse(
                    status="rejected",
                    message=f"Low volatility: ${price_range:.2f} range in last 5 min (need ${settings.MIN_PRICE_RANGE:.2f})",
                )

        # 2. Handle existing positions
        active_trade = self._get_active_trade(db)
        if active_trade:
            if active_trade.direction == alert.direction:
                # Same direction already open — reject (#2)
                db_alert.status = AlertStatus.REJECTED
                db_alert.rejection_reason = f"Already in {alert.direction.value} position (trade #{active_trade.id})"
                db.commit()
                logger.info(f"Trade rejected: already in {alert.direction.value} (trade #{active_trade.id})")
                return WebhookResponse(
                    status="rejected",
                    message=f"Already in {alert.direction.value} position",
                )
            else:
                # Opposite direction — close existing, then open new (#1)
                logger.info(f"Reverse signal: closing {active_trade.direction.value} trade #{active_trade.id} for new {alert.direction.value}")
                await self._close_trade(
                    db, active_trade, ExitReason.SIGNAL,
                    f"Reverse signal: closing {active_trade.direction.value} for incoming {alert.direction.value}",
                )
                db.flush()

        # 3. Select 0DTE contract
        contract = self.selector.select_contract(
            direction=alert.direction.value,
            spy_price=alert.price,
        )

        # 4. Place entry order at 5% below mid-price (captures post-alert pullback)
        mid_price = round((contract.bid + contract.ask) / 2, 2)
        entry_limit_price = round(mid_price * (1 - settings.ENTRY_LIMIT_BELOW_PERCENT / 100), 2)
        order = SchwabService.build_option_buy_order(
            option_symbol=contract.symbol,
            quantity=settings.DEFAULT_QUANTITY,
            limit_price=entry_limit_price,
        )
        order_id = self.schwab.place_order(order)

        # 5. Create trade record
        source = alert.source if alert.source else "tradingview"
        trade = Trade(
            trade_date=date.today(),
            direction=alert.direction,
            option_symbol=contract.symbol,
            strike_price=contract.strike,
            expiration_date=contract.expiration,
            entry_order_id=order_id,
            entry_quantity=settings.DEFAULT_QUANTITY,
            alert_option_price=mid_price,
            entry_is_fallback=False,
            status=TradeStatus.PENDING,
            source=source,
        )
        db.add(trade)
        db.flush()

        log_trade_event(
            db, trade.id, TradeEventType.ALERT_RECEIVED,
            f"Webhook received: {alert.action} at SPY ${alert.price}",
            details={"action": alert.action, "spy_price": alert.price, "source": source},
        )
        log_trade_event(
            db, trade.id, TradeEventType.CONTRACT_SELECTED,
            f"Selected {contract.symbol} strike=${contract.strike} delta={contract.delta:.2f}",
            details={
                "symbol": contract.symbol, "strike": contract.strike,
                "delta": contract.delta, "bid": contract.bid, "ask": contract.ask,
                "spread_percent": round(contract.spread_percent, 1),
            },
        )
        log_trade_event(
            db, trade.id, TradeEventType.ENTRY_ORDER_PLACED,
            f"Buy {settings.DEFAULT_QUANTITY}x at ${entry_limit_price:.2f} "
            f"({settings.ENTRY_LIMIT_BELOW_PERCENT}% below mid ${mid_price:.2f}), "
            f"timeout={settings.ENTRY_LIMIT_TIMEOUT_MINUTES}min, order={order_id}",
            details={
                "order_id": order_id, "limit_price": entry_limit_price,
                "mid_price": mid_price, "discount_percent": settings.ENTRY_LIMIT_BELOW_PERCENT,
                "timeout_minutes": settings.ENTRY_LIMIT_TIMEOUT_MINUTES,
                "quantity": settings.DEFAULT_QUANTITY,
            },
        )

        # Link alert
        db_alert.status = AlertStatus.PROCESSED
        db_alert.trade_id = trade.id
        db.commit()

        logger.info(
            f"Trade #{trade.id}: {alert.direction.value} "
            f"{contract.symbol} {settings.DEFAULT_QUANTITY}x @ {entry_limit_price:.2f}, order={order_id}"
        )

        # 6. Notify dashboard
        await self.ws_manager.broadcast(
            {
                "event": "trade_created",
                "data": {
                    "trade_id": trade.id,
                    "direction": alert.direction.value,
                    "option_symbol": contract.symbol,
                    "strike_price": contract.strike,
                    "status": TradeStatus.PENDING.value,
                },
            }
        )

        return WebhookResponse(
            status="accepted",
            message=f"Trade #{trade.id} placed: {contract.symbol}",
            trade_id=trade.id,
        )

    async def retake_trade(
        self,
        db: Session,
        trade_id: int,
    ) -> WebhookResponse:
        """Re-enter the same direction as a previous trade with a fresh 0DTE contract."""
        original = db.query(Trade).filter(Trade.id == trade_id).first()
        if not original:
            return WebhookResponse(status="rejected", message=f"Trade #{trade_id} not found")
        if original.status not in (TradeStatus.CLOSED, TradeStatus.CANCELLED):
            return WebhookResponse(
                status="rejected",
                message=f"Trade #{trade_id} is {original.status.value}, must be CLOSED or CANCELLED",
            )

        direction = original.direction

        # Safety checks
        trade_count = self.get_daily_trade_count(db)
        if trade_count >= settings.MAX_DAILY_TRADES:
            return WebhookResponse(status="rejected", message=f"Daily trade limit reached ({trade_count}/{settings.MAX_DAILY_TRADES})")

        daily_pnl = self.get_daily_pnl(db)
        if daily_pnl <= -settings.MAX_DAILY_LOSS:
            return WebhookResponse(status="rejected", message=f"Daily loss limit reached (${daily_pnl:.2f})")

        consec_losses = self._get_consecutive_losses(db)
        if consec_losses >= settings.MAX_CONSECUTIVE_LOSSES:
            return WebhookResponse(status="rejected", message=f"Trading paused: {consec_losses} consecutive losses")

        active_trade = self._get_active_trade(db)
        if active_trade and active_trade.direction == direction:
            return WebhookResponse(status="rejected", message=f"Already in {direction.value} position (trade #{active_trade.id})")

        # Get current SPY price
        spy_data = self.schwab.get_quote("SPY")
        spy_price = spy_data.get("SPY", {}).get("quote", {}).get("lastPrice")
        if not spy_price:
            return WebhookResponse(status="rejected", message="Could not get current SPY price")

        # Select fresh 0DTE contract
        contract = self.selector.select_contract(direction=direction.value, spy_price=spy_price)

        # Place entry order at 5% below mid-price
        mid_price = round((contract.bid + contract.ask) / 2, 2)
        entry_limit_price = round(mid_price * (1 - settings.ENTRY_LIMIT_BELOW_PERCENT / 100), 2)
        order = SchwabService.build_option_buy_order(
            option_symbol=contract.symbol,
            quantity=settings.DEFAULT_QUANTITY,
            limit_price=entry_limit_price,
        )
        order_id = self.schwab.place_order(order)

        # Create new trade
        trade = Trade(
            trade_date=date.today(),
            direction=direction,
            option_symbol=contract.symbol,
            strike_price=contract.strike,
            expiration_date=contract.expiration,
            entry_order_id=order_id,
            entry_quantity=settings.DEFAULT_QUANTITY,
            alert_option_price=mid_price,
            entry_is_fallback=False,
            status=TradeStatus.PENDING,
            source="retake",
        )
        db.add(trade)
        db.flush()

        log_trade_event(
            db, trade.id, TradeEventType.ALERT_RECEIVED,
            f"Retake of trade #{original.id} ({direction.value}) at SPY ${spy_price}",
            details={"original_trade_id": original.id, "direction": direction.value, "spy_price": spy_price},
        )
        log_trade_event(
            db, trade.id, TradeEventType.CONTRACT_SELECTED,
            f"Selected {contract.symbol} strike=${contract.strike} delta={contract.delta:.2f}",
            details={
                "symbol": contract.symbol, "strike": contract.strike,
                "delta": contract.delta, "bid": contract.bid, "ask": contract.ask,
                "spread_percent": round(contract.spread_percent, 1),
            },
        )
        log_trade_event(
            db, trade.id, TradeEventType.ENTRY_ORDER_PLACED,
            f"Buy {settings.DEFAULT_QUANTITY}x at ${entry_limit_price:.2f} "
            f"({settings.ENTRY_LIMIT_BELOW_PERCENT}% below mid ${mid_price:.2f}), "
            f"timeout={settings.ENTRY_LIMIT_TIMEOUT_MINUTES}min, order={order_id}",
            details={
                "order_id": order_id, "limit_price": entry_limit_price,
                "mid_price": mid_price, "discount_percent": settings.ENTRY_LIMIT_BELOW_PERCENT,
                "timeout_minutes": settings.ENTRY_LIMIT_TIMEOUT_MINUTES,
                "quantity": settings.DEFAULT_QUANTITY,
            },
        )

        db.commit()

        logger.info(
            f"Retake trade #{trade.id} (from #{original.id}): {direction.value} "
            f"{contract.symbol} {settings.DEFAULT_QUANTITY}x @ {entry_limit_price:.2f}, order={order_id}"
        )

        await self.ws_manager.broadcast({
            "event": "trade_created",
            "data": {
                "trade_id": trade.id,
                "direction": direction.value,
                "option_symbol": contract.symbol,
                "strike_price": contract.strike,
                "status": TradeStatus.PENDING.value,
            },
        })

        return WebhookResponse(
            status="accepted",
            message=f"Retake trade #{trade.id} placed: {contract.symbol}",
            trade_id=trade.id,
        )

    async def close_open_position(
        self,
        db: Session,
        db_alert: Alert,
    ) -> WebhookResponse:
        """Close the most recent active trade via market sell order."""
        active_trade = self._get_active_trade(db)

        if not active_trade:
            db_alert.status = AlertStatus.REJECTED
            db_alert.rejection_reason = "No open positions to close"
            db.commit()
            logger.info("CLOSE signal received but no open positions")
            return WebhookResponse(
                status="rejected",
                message="No open positions to close",
            )

        await self._close_trade(db, active_trade, ExitReason.SIGNAL, "CLOSE webhook received")

        # Link alert to trade
        db_alert.status = AlertStatus.PROCESSED
        db_alert.trade_id = active_trade.id
        db.commit()

        logger.info(
            f"Trade #{active_trade.id}: CLOSE signal, market sell placed"
        )

        return WebhookResponse(
            status="accepted",
            message=f"Closing trade #{active_trade.id}: {active_trade.option_symbol}",
            trade_id=active_trade.id,
        )
