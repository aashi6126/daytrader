from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Alert, AlertStatus, ExitReason, Trade, TradeDirection, TradeEventType, TradeStatus
from app.schemas import TradingViewAlert, WebhookResponse
from app.services.delta_resolver import DeltaResolution, DeltaResolver
from app.services.option_selector import IVRankTooHighError, OptionSelector, _0DTE_TICKERS
from app.services.schwab_client import SchwabService
from app.services.strategy_adapter import StrategyAdapter
from app.services.trade_events import log_trade_event
from app.services.ws_manager import WebSocketManager

logger = logging.getLogger(__name__)
settings = Settings()

ACTIVE_STATUSES = [TradeStatus.PENDING, TradeStatus.FILLED, TradeStatus.STOP_LOSS_PLACED]


class TradeManager:
    def __init__(
        self,
        schwab_service: SchwabService,
        option_selector: OptionSelector,
        ws_manager: WebSocketManager,
        app=None,
    ):
        self.schwab = schwab_service
        self.selector = option_selector
        self.ws_manager = ws_manager
        self.app = app

    @property
    def _use_market_orders(self) -> bool:
        if self.app and hasattr(self.app.state, "use_market_orders"):
            return self.app.state.use_market_orders
        return False

    @staticmethod
    def _is_event_afternoon_blocked() -> tuple[bool, str]:
        """Check if today is a blocked afternoon (FOMC/CPI day).

        Returns (is_blocked, event_description).
        """
        import json
        from pathlib import Path

        cal_path = Path(__file__).resolve().parent.parent / settings.EVENT_CALENDAR_PATH
        if not cal_path.exists():
            return False, ""

        try:
            data = json.loads(cal_path.read_text())
            blocked = data.get("blocked_afternoons", [])
            today_str = date.today().isoformat()
            if today_str in blocked:
                return True, f"Event day ({today_str})"
        except Exception as e:
            logger.warning(f"Event calendar read failed: {e}")
        return False, ""

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
            .filter(Trade.source.in_(["tradingview", "orb_auto", "strategy_signal"]))
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

    def _compute_live_atr(self, ticker: str, period: int = 14) -> Optional[float]:
        """Compute current ATR from today's intraday bars (Wilder smoothing)."""
        try:
            candles = self.schwab.fetch_intraday_bars(ticker, frequency=5)
            if len(candles) < period + 1:
                return None
            trs: list[float] = []
            for i in range(1, len(candles)):
                h, l = candles[i]["high"], candles[i]["low"]
                pc = candles[i - 1]["close"]
                trs.append(max(h - l, abs(h - pc), abs(l - pc)))
            if len(trs) < period:
                return None
            atr = sum(trs[:period]) / period
            for tr in trs[period:]:
                atr = (atr * (period - 1) + tr) / period
            return atr
        except Exception as e:
            logger.warning(f"ATR computation failed for {ticker}: {e}")
            return None

    def _resolve_delta(
        self, alert: TradingViewAlert, strategy_params: dict | None = None,
    ) -> Optional[DeltaResolution]:
        """Resolve dynamic delta target based on regime, expected move, VIX,
        and time-of-day.

        Returns the full DeltaResolution (with regime, VIX, confidence),
        or None when dynamic delta is disabled or resolution fails.
        """
        if not settings.DYNAMIC_DELTA_ENABLED:
            return None

        import pandas as pd

        # Extract signal_type
        signal_type = None
        if strategy_params and strategy_params.get("signal_type"):
            signal_type = strategy_params["signal_type"]
        elif alert.source == "orb_auto":
            signal_type = "orb"

        if not signal_type:
            return None

        try:
            # Fetch bars and build DataFrame
            candles = self.schwab.fetch_intraday_bars(alert.ticker, frequency=5)
            if len(candles) < 21:
                logger.info(f"Delta resolver: only {len(candles)} bars, using default delta")
                return None

            df = pd.DataFrame(candles)
            # Schwab candles have: open, high, low, close, volume, datetime (ms)
            if "datetime" in df.columns:
                df["timestamp"] = pd.to_datetime(df["datetime"], unit="ms")
                df.set_index("timestamp", inplace=True)

            # Compute ATR from the same bars (avoids duplicate API call)
            atr_period = (
                strategy_params.get("atr_period") if strategy_params else None
            ) or settings.ATR_PERIOD_DEFAULT
            atr = self._compute_live_atr(alert.ticker, period=atr_period)

            # Hold horizon in minutes
            hold_minutes = (
                strategy_params.get("param_max_hold_minutes") if strategy_params else None
            ) or settings.MAX_HOLD_MINUTES

            # Fetch VIX (streaming-first, REST fallback)
            from app.dependencies import get_streaming_service

            streaming = get_streaming_service()
            vix_snap = streaming.get_equity_quote("$VIX.X")
            if vix_snap and not vix_snap.is_stale and vix_snap.last > 0:
                vix = vix_snap.last
            else:
                vix = self.schwab.get_vix()

            # Current ET time
            now_et_time = datetime.now(ZoneInfo("America/New_York")).time()

            resolver = DeltaResolver()
            resolution = resolver.resolve(
                signal_type=signal_type,
                df=df,
                vix=vix,
                current_time=now_et_time,
                atr=atr,
                hold_minutes=hold_minutes,
                underlying_price=alert.price,
            )

            logger.info(
                f"Dynamic delta resolved: {resolution.delta_target:.2f} "
                f"(regime={resolution.regime_result.final_regime.value}, "
                f"exp_move=${resolution.expected_move_dollars or 0:.2f}, "
                f"VIX={resolution.vix_level}, late_day={resolution.is_late_day})"
            )
            return resolution

        except Exception as e:
            logger.warning(f"Delta resolution failed, using default: {e}")
            return None

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
        strategy_params: dict | None = None,
    ) -> WebhookResponse:
        # 0. Time-of-day window — block trades outside allowed hours
        # 0DTE tickers (SPY/QQQ) use strict cutoff; weeklies can enter until force-exit time
        now_et = datetime.now(ZoneInfo("America/New_York"))
        is_0dte = alert.ticker.upper() in _0DTE_TICKERS
        first_entry = time(settings.FIRST_ENTRY_HOUR, settings.FIRST_ENTRY_MINUTE)
        last_entry = (
            time(settings.LAST_ENTRY_HOUR, settings.LAST_ENTRY_MINUTE)
            if is_0dte
            else time(settings.FORCE_EXIT_HOUR, settings.FORCE_EXIT_MINUTE)
        )
        if now_et.time() < first_entry:
            db_alert.status = AlertStatus.REJECTED
            db_alert.rejection_reason = (
                f"Before first entry time ({settings.FIRST_ENTRY_HOUR}:{settings.FIRST_ENTRY_MINUTE:02d} ET)"
            )
            db.commit()
            logger.info(
                f"Trade rejected: before entry window "
                f"({now_et.strftime('%H:%M')} ET < "
                f"{settings.FIRST_ENTRY_HOUR}:{settings.FIRST_ENTRY_MINUTE:02d} ET)"
            )
            return WebhookResponse(
                status="rejected",
                message=f"Before first entry time ({settings.FIRST_ENTRY_HOUR}:{settings.FIRST_ENTRY_MINUTE:02d} ET)",
            )
        if now_et.time() >= last_entry:
            cutoff_label = "last entry cutoff" if is_0dte else "force exit time"
            db_alert.status = AlertStatus.REJECTED
            db_alert.rejection_reason = (
                f"Past {cutoff_label} ({last_entry.strftime('%H:%M')} ET)"
            )
            db.commit()
            logger.info(
                f"Trade rejected: past {cutoff_label} "
                f"({now_et.strftime('%H:%M')} ET >= {last_entry.strftime('%H:%M')} ET)"
            )
            return WebhookResponse(
                status="rejected",
                message=f"Past {cutoff_label} ({last_entry.strftime('%H:%M')} ET)",
            )

        # 0b. VIX circuit breaker — block all new trades when VIX is elevated
        if settings.VIX_CIRCUIT_BREAKER > 0:
            try:
                from app.dependencies import get_streaming_service
                streaming = get_streaming_service()
                vix_snap = streaming.get_equity_quote("$VIX.X")
                if vix_snap and not vix_snap.is_stale and vix_snap.last > 0:
                    current_vix = vix_snap.last
                else:
                    current_vix = self.schwab.get_vix()

                if current_vix and current_vix >= settings.VIX_CIRCUIT_BREAKER:
                    db_alert.status = AlertStatus.REJECTED
                    db_alert.rejection_reason = (
                        f"VIX circuit breaker: VIX {current_vix:.1f} >= {settings.VIX_CIRCUIT_BREAKER}"
                    )
                    db.commit()
                    logger.warning(
                        f"Trade rejected: VIX circuit breaker "
                        f"({current_vix:.1f} >= {settings.VIX_CIRCUIT_BREAKER})"
                    )
                    return WebhookResponse(
                        status="rejected",
                        message=f"VIX circuit breaker: VIX at {current_vix:.1f} (threshold: {settings.VIX_CIRCUIT_BREAKER})",
                    )
            except Exception as e:
                logger.warning(f"VIX circuit breaker check failed (allowing trade): {e}")

        # 0c. Event calendar: block afternoon trades on FOMC/CPI days
        afternoon_cutoff = time(12, 0)  # noon ET
        if now_et.time() >= afternoon_cutoff:
            is_blocked, event_desc = self._is_event_afternoon_blocked()
            if is_blocked:
                db_alert.status = AlertStatus.REJECTED
                db_alert.rejection_reason = f"Afternoon blocked: {event_desc}"
                db.commit()
                logger.info(f"Trade rejected: afternoon blocked — {event_desc}")
                return WebhookResponse(
                    status="rejected",
                    message=f"Afternoon trading blocked: {event_desc}",
                )

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

        # 1d. Trade cooldown — per-ticker: reject if last trade for this ticker was too recent
        cooldown_cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.TRADE_COOLDOWN_MINUTES)
        recent_trade = (
            db.query(Trade)
            .filter(Trade.trade_date == date.today())
            .filter(Trade.ticker == alert.ticker)
            .filter(Trade.status != TradeStatus.CANCELLED)
            .filter(Trade.created_at >= cooldown_cutoff)
            .first()
        )
        if recent_trade:
            db_alert.status = AlertStatus.REJECTED
            db_alert.rejection_reason = f"Trade cooldown ({settings.TRADE_COOLDOWN_MINUTES} min)"
            db.commit()
            logger.info(f"Trade rejected: cooldown active for {alert.ticker} (trade #{recent_trade.id} is recent)")
            return WebhookResponse(
                status="rejected",
                message=f"Trade cooldown for {alert.ticker}: must wait {settings.TRADE_COOLDOWN_MINUTES} min between trades",
            )

        # 1d2. Duplicate ticker guard — block if any open position exists on the
        # same ticker (any direction). Wait for it to close before opening another.
        correlated_trade = (
            db.query(Trade)
            .filter(Trade.trade_date == date.today())
            .filter(Trade.ticker == alert.ticker)
            .filter(Trade.status.in_(ACTIVE_STATUSES))
            .first()
        )
        if correlated_trade:
            db_alert.status = AlertStatus.REJECTED
            db_alert.rejection_reason = (
                f"Duplicate ticker guard: {alert.ticker} already has open position "
                f"(trade #{correlated_trade.id}, {correlated_trade.direction.value})"
            )
            db.commit()
            logger.info(
                f"Trade rejected: duplicate ticker — {alert.ticker} already open "
                f"(trade #{correlated_trade.id}, {correlated_trade.direction.value})"
            )
            return WebhookResponse(
                status="rejected",
                message=f"Duplicate ticker guard: {alert.ticker} already has an open position",
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

        # 2b. Resolve dynamic delta + regime context
        resolution = self._resolve_delta(alert, strategy_params)
        delta_target = resolution.delta_target if resolution else None

        # 2c. Adapt strategy params based on regime/volatility
        adapted = None
        if settings.STRATEGY_ADAPTER_ENABLED and resolution:
            adapted = StrategyAdapter().adapt(resolution, strategy_params, settings)

        # 3. Select option contract (0DTE for SPY/QQQ, weekly for others)
        try:
            contract = self.selector.select_contract(
                direction=alert.direction.value,
                underlying_price=alert.price,
                ticker=alert.ticker,
                delta_target=delta_target,
            )
        except IVRankTooHighError as e:
            db_alert.status = AlertStatus.REJECTED
            db_alert.rejection_reason = str(e)
            db.commit()
            logger.info(f"Trade rejected: {e}")
            return WebhookResponse(status="rejected", message=str(e))

        # 3a. Spread-aware stop viability check (Fix 2)
        mid_price = round((contract.bid + contract.ask) / 2, 2)
        spread = contract.ask - contract.bid
        if adapted and adapted.adapter_applied:
            sl_pct = adapted.stop_loss_percent
        else:
            sl_pct = (
                strategy_params.get("param_stop_loss_percent") if strategy_params else None
            ) or settings.STOP_LOSS_PERCENT
        stop_distance = mid_price * sl_pct / 100
        if stop_distance < settings.MIN_STOP_SPREAD_RATIO * spread:
            db_alert.status = AlertStatus.REJECTED
            db_alert.rejection_reason = (
                f"Stop distance ${stop_distance:.2f} < {settings.MIN_STOP_SPREAD_RATIO}x "
                f"spread ${spread:.2f} — trade not viable"
            )
            db.commit()
            logger.info(
                f"Trade rejected: stop distance ${stop_distance:.2f} < "
                f"{settings.MIN_STOP_SPREAD_RATIO}x spread ${spread:.2f}"
            )
            return WebhookResponse(
                status="rejected",
                message=f"Stop distance too tight relative to spread (${stop_distance:.2f} vs ${spread:.2f})",
            )

        # 3b. Compute ATR for stop placement (Fix 3)
        atr_period = (
            strategy_params.get("atr_period") if strategy_params else None
        ) or settings.ATR_PERIOD_DEFAULT
        atr_value = (
            self._compute_live_atr(alert.ticker, period=atr_period)
            if settings.ATR_STOP_ENABLED
            else None
        )
        atr_stop_mult = (
            strategy_params.get("param_atr_stop_mult") if strategy_params else None
        ) or settings.ATR_STOP_MULT_DEFAULT

        # 3c. Dynamic position sizing (Fix 4)
        effective_delta_approx = delta_target if delta_target else settings.OPTION_DELTA_TARGET
        if atr_value and atr_stop_mult:
            stop_distance_per_contract = atr_value * atr_stop_mult * effective_delta_approx
        else:
            stop_distance_per_contract = mid_price * sl_pct / 100
        risk_per_contract = stop_distance_per_contract * 100  # each contract = 100 shares
        effective_max_risk = adapted.max_risk_per_trade if (adapted and adapted.adapter_applied) else settings.MAX_RISK_PER_TRADE
        if risk_per_contract > 0 and effective_max_risk > 0:
            quantity = max(1, min(settings.DEFAULT_QUANTITY, int(effective_max_risk / risk_per_contract)))
        else:
            quantity = settings.DEFAULT_QUANTITY

        # 4. Place entry order
        if self._use_market_orders:
            order = SchwabService.build_option_buy_market_order(
                option_symbol=contract.symbol,
                quantity=quantity,
            )
            order["_sim_price"] = str(mid_price)  # for dry-run fill simulation
            entry_limit_price = None
        else:
            entry_limit_price = round(mid_price * (1 - settings.ENTRY_LIMIT_BELOW_PERCENT / 100), 2)
            order = SchwabService.build_option_buy_order(
                option_symbol=contract.symbol,
                quantity=quantity,
                limit_price=entry_limit_price,
            )
        order_id = self.schwab.place_order(order)

        # 5. Create trade record
        source = alert.source if alert.source else "tradingview"
        trade = Trade(
            trade_date=date.today(),
            ticker=alert.ticker,
            direction=alert.direction,
            option_symbol=contract.symbol,
            strike_price=contract.strike,
            expiration_date=contract.expiration,
            entry_order_id=order_id,
            entry_quantity=quantity,
            alert_option_price=mid_price,
            entry_is_fallback=False,
            status=TradeStatus.PENDING,
            source=source,
        )
        # Store adapted or raw strategy exit params
        if adapted and adapted.adapter_applied:
            trade.param_stop_loss_percent = adapted.stop_loss_percent
            trade.param_profit_target_percent = adapted.profit_target_percent
            trade.param_trailing_stop_percent = adapted.trailing_stop_percent
            trade.param_max_hold_minutes = adapted.max_hold_minutes
            trade.entry_regime = adapted.regime
            trade.entry_regime_confidence = adapted.regime_confidence
            trade.entry_vix = adapted.vix_at_entry
            trade.adapter_applied = True
        elif strategy_params:
            trade.param_stop_loss_percent = strategy_params.get("param_stop_loss_percent")
            trade.param_profit_target_percent = strategy_params.get("param_profit_target_percent")
            trade.param_trailing_stop_percent = strategy_params.get("param_trailing_stop_percent")
            trade.param_max_hold_minutes = strategy_params.get("param_max_hold_minutes")
        # ATR data for stop placement
        trade.entry_atr_value = atr_value
        trade.param_atr_stop_mult = atr_stop_mult if atr_value else None
        trade.param_delta_target = delta_target
        db.add(trade)
        db.flush()

        log_trade_event(
            db, trade.id, TradeEventType.ALERT_RECEIVED,
            f"Signal received: {alert.action} at {alert.ticker} ${alert.price}",
            details={"action": alert.action, "ticker": alert.ticker, "price": alert.price, "source": source},
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
        atr_info = f", ATR={atr_value:.4f}x{atr_stop_mult}" if atr_value else ""
        if self._use_market_orders:
            log_trade_event(
                db, trade.id, TradeEventType.ENTRY_ORDER_PLACED,
                f"Buy {quantity}x at MARKET (mid ${mid_price:.2f}{atr_info}), order={order_id}",
                details={
                    "order_id": order_id, "order_type": "MARKET",
                    "mid_price": mid_price, "quantity": quantity,
                    "atr_value": atr_value, "atr_stop_mult": atr_stop_mult,
                },
            )
        else:
            log_trade_event(
                db, trade.id, TradeEventType.ENTRY_ORDER_PLACED,
                f"Buy {quantity}x at ${entry_limit_price:.2f} "
                f"({settings.ENTRY_LIMIT_BELOW_PERCENT}% below mid ${mid_price:.2f}{atr_info}), "
                f"timeout={settings.ENTRY_LIMIT_TIMEOUT_MINUTES}min, order={order_id}",
                details={
                    "order_id": order_id, "limit_price": entry_limit_price,
                    "mid_price": mid_price, "discount_percent": settings.ENTRY_LIMIT_BELOW_PERCENT,
                    "timeout_minutes": settings.ENTRY_LIMIT_TIMEOUT_MINUTES,
                    "quantity": quantity,
                    "atr_value": atr_value, "atr_stop_mult": atr_stop_mult,
                },
            )

        # Link alert
        db_alert.status = AlertStatus.PROCESSED
        db_alert.trade_id = trade.id
        db.commit()

        price_str = "MARKET" if self._use_market_orders else f"{entry_limit_price:.2f}"
        logger.info(
            f"Trade #{trade.id}: {alert.direction.value} "
            f"{contract.symbol} {quantity}x @ {price_str}, order={order_id}"
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

        # Safety checks — weeklies can enter until force-exit time
        retake_ticker = original.ticker or "SPY"
        now_et = datetime.now(ZoneInfo("America/New_York"))
        is_0dte = retake_ticker.upper() in _0DTE_TICKERS
        first_entry = time(settings.FIRST_ENTRY_HOUR, settings.FIRST_ENTRY_MINUTE)
        last_entry = (
            time(settings.LAST_ENTRY_HOUR, settings.LAST_ENTRY_MINUTE)
            if is_0dte
            else time(settings.FORCE_EXIT_HOUR, settings.FORCE_EXIT_MINUTE)
        )
        if now_et.time() < first_entry:
            return WebhookResponse(status="rejected", message=f"Before first entry time ({settings.FIRST_ENTRY_HOUR}:{settings.FIRST_ENTRY_MINUTE:02d} ET)")
        if now_et.time() >= last_entry:
            cutoff_label = "last entry cutoff" if is_0dte else "force exit time"
            return WebhookResponse(status="rejected", message=f"Past {cutoff_label} ({last_entry.strftime('%H:%M')} ET)")

        # VIX circuit breaker
        if settings.VIX_CIRCUIT_BREAKER > 0:
            try:
                from app.dependencies import get_streaming_service
                streaming = get_streaming_service()
                vix_snap = streaming.get_equity_quote("$VIX.X")
                if vix_snap and not vix_snap.is_stale and vix_snap.last > 0:
                    current_vix = vix_snap.last
                else:
                    current_vix = self.schwab.get_vix()
                if current_vix and current_vix >= settings.VIX_CIRCUIT_BREAKER:
                    return WebhookResponse(
                        status="rejected",
                        message=f"VIX circuit breaker: VIX at {current_vix:.1f} (threshold: {settings.VIX_CIRCUIT_BREAKER})",
                    )
            except Exception as e:
                logger.warning(f"VIX circuit breaker check failed in retake (allowing trade): {e}")

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

        # Get current underlying price
        quote_data = self.schwab.get_quote(retake_ticker)
        underlying_price = quote_data.get(retake_ticker, {}).get("quote", {}).get("lastPrice")
        if not underlying_price:
            return WebhookResponse(status="rejected", message=f"Could not get current {retake_ticker} price")

        # Resolve dynamic delta + regime context for retake
        retake_alert = TradingViewAlert(
            ticker=retake_ticker, action=f"BUY_{direction.value}",
            secret=settings.WEBHOOK_SECRET, price=underlying_price,
            source="retake",
        )
        resolution = self._resolve_delta(retake_alert)
        delta_target = resolution.delta_target if resolution else None

        # Adapt strategy params based on regime/volatility
        adapted = None
        if settings.STRATEGY_ADAPTER_ENABLED and resolution:
            adapted = StrategyAdapter().adapt(resolution, None, settings)

        # Select fresh option contract
        try:
            contract = self.selector.select_contract(direction=direction.value, underlying_price=underlying_price, ticker=retake_ticker, delta_target=delta_target)
        except IVRankTooHighError as e:
            logger.info(f"Retake rejected: {e}")
            return WebhookResponse(status="rejected", message=str(e))

        # Spread viability check (Fix 2)
        mid_price = round((contract.bid + contract.ask) / 2, 2)
        spread = contract.ask - contract.bid
        sl_pct = adapted.stop_loss_percent if (adapted and adapted.adapter_applied) else settings.STOP_LOSS_PERCENT
        stop_distance = mid_price * sl_pct / 100
        if stop_distance < settings.MIN_STOP_SPREAD_RATIO * spread:
            return WebhookResponse(
                status="rejected",
                message=f"Stop distance too tight relative to spread (${stop_distance:.2f} vs ${spread:.2f})",
            )

        # ATR computation (Fix 3)
        atr_value = (
            self._compute_live_atr(retake_ticker, period=settings.ATR_PERIOD_DEFAULT)
            if settings.ATR_STOP_ENABLED
            else None
        )
        atr_stop_mult = settings.ATR_STOP_MULT_DEFAULT

        # Dynamic position sizing (Fix 4)
        retake_delta_approx = delta_target if delta_target else settings.OPTION_DELTA_TARGET
        if atr_value and atr_stop_mult:
            stop_distance_per_contract = atr_value * atr_stop_mult * retake_delta_approx
        else:
            stop_distance_per_contract = mid_price * sl_pct / 100
        risk_per_contract = stop_distance_per_contract * 100
        effective_max_risk = adapted.max_risk_per_trade if (adapted and adapted.adapter_applied) else settings.MAX_RISK_PER_TRADE
        if risk_per_contract > 0 and effective_max_risk > 0:
            quantity = max(1, min(settings.DEFAULT_QUANTITY, int(effective_max_risk / risk_per_contract)))
        else:
            quantity = settings.DEFAULT_QUANTITY

        # Place entry order
        if self._use_market_orders:
            order = SchwabService.build_option_buy_market_order(
                option_symbol=contract.symbol,
                quantity=quantity,
            )
            order["_sim_price"] = str(mid_price)
            entry_limit_price = None
        else:
            entry_limit_price = round(mid_price * (1 - settings.ENTRY_LIMIT_BELOW_PERCENT / 100), 2)
            order = SchwabService.build_option_buy_order(
                option_symbol=contract.symbol,
                quantity=quantity,
                limit_price=entry_limit_price,
            )
        order_id = self.schwab.place_order(order)

        # Create new trade
        trade = Trade(
            trade_date=date.today(),
            ticker=retake_ticker,
            direction=direction,
            option_symbol=contract.symbol,
            strike_price=contract.strike,
            expiration_date=contract.expiration,
            entry_order_id=order_id,
            entry_quantity=quantity,
            alert_option_price=mid_price,
            entry_is_fallback=False,
            status=TradeStatus.PENDING,
            source="retake",
        )
        # Store adapted or default strategy exit params
        if adapted and adapted.adapter_applied:
            trade.param_stop_loss_percent = adapted.stop_loss_percent
            trade.param_profit_target_percent = adapted.profit_target_percent
            trade.param_trailing_stop_percent = adapted.trailing_stop_percent
            trade.param_max_hold_minutes = adapted.max_hold_minutes
            trade.entry_regime = adapted.regime
            trade.entry_regime_confidence = adapted.regime_confidence
            trade.entry_vix = adapted.vix_at_entry
            trade.adapter_applied = True
        trade.entry_atr_value = atr_value
        trade.param_atr_stop_mult = atr_stop_mult if atr_value else None
        trade.param_delta_target = delta_target
        db.add(trade)
        db.flush()

        log_trade_event(
            db, trade.id, TradeEventType.ALERT_RECEIVED,
            f"Retake of trade #{original.id} ({direction.value}) at {retake_ticker} ${underlying_price}",
            details={"original_trade_id": original.id, "direction": direction.value, "ticker": retake_ticker, "price": underlying_price},
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
        if self._use_market_orders:
            log_trade_event(
                db, trade.id, TradeEventType.ENTRY_ORDER_PLACED,
                f"Buy {quantity}x at MARKET (mid ${mid_price:.2f}), order={order_id}",
                details={
                    "order_id": order_id, "order_type": "MARKET",
                    "mid_price": mid_price, "quantity": quantity,
                },
            )
        else:
            log_trade_event(
                db, trade.id, TradeEventType.ENTRY_ORDER_PLACED,
                f"Buy {quantity}x at ${entry_limit_price:.2f} "
                f"({settings.ENTRY_LIMIT_BELOW_PERCENT}% below mid ${mid_price:.2f}), "
                f"timeout={settings.ENTRY_LIMIT_TIMEOUT_MINUTES}min, order={order_id}",
                details={
                    "order_id": order_id, "limit_price": entry_limit_price,
                    "mid_price": mid_price, "discount_percent": settings.ENTRY_LIMIT_BELOW_PERCENT,
                    "timeout_minutes": settings.ENTRY_LIMIT_TIMEOUT_MINUTES,
                    "quantity": quantity,
                },
            )

        db.commit()

        retake_price_str = "MARKET" if self._use_market_orders else f"{entry_limit_price:.2f}"
        logger.info(
            f"Retake trade #{trade.id} (from #{original.id}): {direction.value} "
            f"{contract.symbol} {quantity}x @ {retake_price_str}, order={order_id}"
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
