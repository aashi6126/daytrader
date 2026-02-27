"""Live signal polling task for enabled strategies from TopSetups.

Fetches intraday bars from Schwab, runs _generate_signals() from the backtest
engine, and fires trades through TradeManager when new signals are detected.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config import Settings
from app.database import SessionLocal
from app.models import Alert, AlertStatus, TradeDirection
from app.schemas import TradingViewAlert
from app.services.backtest.engine import BacktestParams, _generate_signals
from app.services.backtest.market_data import BarData
from app.services.option_selector import _0DTE_TICKERS

def _minutes_to_time(m: int) -> time:
    """Convert minutes-after-9:30 to a time object."""
    h = 9 + (30 + m) // 60
    mn = (30 + m) % 60
    return time(h, mn)

logger = logging.getLogger(__name__)
settings = Settings()

ET = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)

# Mapping from timeframe string to Schwab frequency and poll interval
_FREQ_MAP = {"1m": 1, "5m": 5, "10m": 10, "15m": 15, "30m": 30}


class StrategySignalTask:
    """Monitors a single enabled strategy and fires trades on new signals."""

    def __init__(self, app, config: dict):
        self.app = app
        self.ticker = config["ticker"]
        self.timeframe = config["timeframe"]
        self.signal_type = config["signal_type"]
        self.params = config["params"]
        self.today: date | None = None
        self.fired_signal_timestamps: set[str] = set()
        # Pending 1-minute confirmation: sig_key -> Signal
        self._pending_confirm: dict[str, object] = {}
        # Prior day OHLC for pivot points (cached per day)
        self._prev_day_high: float | None = None
        self._prev_day_low: float | None = None
        self._prev_day_close: float | None = None

    def _now_et(self) -> datetime:
        return datetime.now(ET)

    def _fetch_prev_day_ohlc(self) -> tuple[float | None, float | None, float | None]:
        """Fetch prior trading day OHLC from Schwab daily bars."""
        now = self._now_et()
        start = datetime.combine(now.date() - timedelta(days=7), time(0, 0), tzinfo=ET)
        end = datetime.combine(now.date(), time(0, 0), tzinfo=ET)

        try:
            resp = self.app.state.schwab_client.price_history(
                self.ticker,
                periodType="month",
                period="1",
                frequencyType="daily",
                frequency=1,
                startDate=start,
                endDate=end,
                needExtendedHoursData=False,
            )
            resp.raise_for_status()
            candles = resp.json().get("candles", [])
        except Exception as e:
            logger.warning(f"StrategySignal: failed to fetch prior day OHLC for {self.ticker}: {e}")
            return None, None, None

        if not candles:
            return None, None, None

        last = candles[-1]
        return float(last["high"]), float(last["low"]), float(last["close"])

    def _reset_day(self, today: date):
        self.today = today
        self.fired_signal_timestamps.clear()
        self._pending_confirm.clear()
        self._prev_day_high, self._prev_day_low, self._prev_day_close = self._fetch_prev_day_ohlc()
        logger.info(
            f"StrategySignal: new day {today}, reset for {self.ticker} {self.signal_type}"
            f" (prev H={self._prev_day_high}, L={self._prev_day_low}, C={self._prev_day_close})"
        )

    def _poll_interval_seconds(self) -> int:
        # Poll every 60s regardless of bar timeframe to detect signals promptly.
        # Previously polled at bar frequency (e.g. 5 min for 5m bars) which caused
        # entries to be 5+ minutes late for 0DTE options.
        return 60

    def _build_engine_params(self) -> BacktestParams:
        p = self.params
        today = date.today()
        return BacktestParams(
            start_date=today,
            end_date=today,
            signal_type=self.signal_type,
            ema_fast=int(p.get("ema_fast", 8)),
            ema_slow=int(p.get("ema_slow", 21)),
            bar_interval=self.timeframe,
            rsi_period=int(p.get("rsi_period", 0)),
            rsi_ob=float(p.get("rsi_ob", 70.0)),
            rsi_os=float(p.get("rsi_os", 30.0)),
            orb_minutes=int(p.get("orb_minutes", 15)),
            atr_period=int(p.get("atr_period", 14)),
            atr_stop_mult=float(p.get("atr_stop_mult", 2.0)),
            afternoon_enabled=True,
            quantity=settings.DEFAULT_QUANTITY,
            stop_loss_percent=float(p.get("stop_loss_percent", 16.0)),
            profit_target_percent=float(p.get("profit_target_percent", 40.0)),
            trailing_stop_percent=float(p.get("trailing_stop_percent", 20.0)),
            max_hold_minutes=int(p.get("max_hold_minutes", 90)),
            min_confluence=int(p.get("min_confluence", 5)),
            vol_threshold=float(p.get("vol_threshold", 1.5)),
            orb_body_min_pct=float(p.get("orb_body_min_pct", 0.4)),
            orb_vwap_filter=bool(p.get("orb_vwap_filter", True)),
            orb_gap_fade_filter=bool(p.get("orb_gap_fade_filter", True)),
            orb_stop_mult=float(p.get("orb_stop_mult", 1.0)),
            orb_target_mult=float(p.get("orb_target_mult", 1.5)),
            max_daily_trades=int(p.get("max_daily_trades", 10)),
            max_daily_loss=float(p.get("max_daily_loss", 2000.0)),
            max_consecutive_losses=int(p.get("max_consecutive_losses", 3)),
            bb_period=int(p.get("bb_period", 20)),
            bb_std_mult=float(p.get("bb_std_mult", 2.0)),
            macd_fast=int(p.get("macd_fast", 12)),
            macd_slow=int(p.get("macd_slow", 26)),
            macd_signal_period=int(p.get("macd_signal_period", 9)),
            entry_confirm_minutes=int(p.get("entry_confirm_minutes", 0)),
            morning_window_start=_minutes_to_time(int(p.get("morning_start_min", 15))),
            morning_window_end=_minutes_to_time(int(p.get("morning_end_min", 105))),
            afternoon_window_start=_minutes_to_time(int(p.get("afternoon_start_min", 195))),
            afternoon_window_end=_minutes_to_time(int(p.get("afternoon_end_min", 320))),
            pivot_enabled=bool(p.get("pivot_enabled", False)),
            pivot_proximity_pct=float(p.get("pivot_proximity_pct", 0.3)),
            pivot_filter_enabled=bool(p.get("pivot_filter_enabled", False)),
        )

    def _fetch_live_bars(self) -> list[BarData]:
        """Fetch today's intraday bars from Schwab price_history."""
        now = self._now_et()
        start = datetime.combine(now.date(), MARKET_OPEN, tzinfo=ET)
        end = now + timedelta(minutes=1)  # slightly ahead to capture current bar

        frequency = _FREQ_MAP.get(self.timeframe, 5)

        try:
            resp = self.app.state.schwab_client.price_history(
                self.ticker,
                periodType="day",
                period="1",
                frequencyType="minute",
                frequency=frequency,
                startDate=start,
                endDate=end,
                needExtendedHoursData=False,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"StrategySignal: failed to fetch bars for {self.ticker}: {e}")
            return []

        bars = []
        for candle in data.get("candles", []):
            ts_ms = candle["datetime"]
            ts = datetime.fromtimestamp(ts_ms / 1000, tz=ET)
            if ts.time() < MARKET_OPEN or ts.time() >= MARKET_CLOSE:
                continue
            bars.append(BarData(
                timestamp=ts,
                open=float(candle["open"]),
                high=float(candle["high"]),
                low=float(candle["low"]),
                close=float(candle["close"]),
                volume=int(candle["volume"]),
            ))

        bars.sort(key=lambda b: b.timestamp)
        return bars

    def _get_strategy_params(self, signal=None) -> dict:
        """Extract per-trade exit params from this strategy's config."""
        p = self.params
        params = {
            "signal_type": self.signal_type,
            "param_stop_loss_percent": float(p.get("stop_loss_percent", 0)) or None,
            "param_profit_target_percent": float(p.get("profit_target_percent", 0)) or None,
            "param_trailing_stop_percent": float(p.get("trailing_stop_percent", 0)) or None,
            "param_max_hold_minutes": int(p.get("max_hold_minutes", 0)) or None,
            "param_atr_stop_mult": float(p.get("atr_stop_mult", 0)) or None,
            "atr_period": int(p.get("atr_period", 0)) or None,
        }
        if signal and signal.confluence_score is not None:
            params["confluence_score"] = signal.confluence_score
            params["confluence_max_score"] = signal.confluence_max_score
            params["rel_vol"] = signal.rel_vol
        return params

    async def _fire_signal(self, direction: str, ticker_price: float, signal=None):
        """Create a synthetic alert and route through TradeManager."""
        # Gate: only fire signals for allowed live signal types
        if self.signal_type not in settings.ALLOWED_LIVE_SIGNAL_TYPES:
            logger.info(
                f"StrategySignal: BLOCKED {self.signal_type} for {self.ticker} "
                f"(not in ALLOWED_LIVE_SIGNAL_TYPES)"
            )
            return

        action = "BUY_CALL" if direction == "CALL" else "BUY_PUT"
        trade_direction = TradeDirection.CALL if direction == "CALL" else TradeDirection.PUT

        strategy_params = self._get_strategy_params(signal)
        logger.info(
            f"StrategySignal: firing {action} for {self.ticker} "
            f"(signal_type={self.signal_type}, price=${ticker_price:.2f}, "
            f"SL={strategy_params.get('param_stop_loss_percent')}%, "
            f"PT={strategy_params.get('param_profit_target_percent')}%)"
        )

        alert = TradingViewAlert(
            ticker=self.ticker,
            action=action,
            secret=settings.WEBHOOK_SECRET,
            price=ticker_price,
            comment=f"Strategy signal: {self.signal_type} on {self.ticker} @ {self.timeframe}",
            source="strategy_signal",
        )

        db = SessionLocal()
        try:
            from app.dependencies import get_ws_manager
            from app.services.option_selector import OptionSelector
            from app.services.schwab_client import SchwabService
            from app.services.trade_manager import TradeManager

            db_alert = Alert(
                raw_payload=json.dumps({
                    "action": action,
                    "source": "strategy_signal",
                    "price": ticker_price,
                    "ticker": self.ticker,
                    "signal_type": self.signal_type,
                }),
                ticker=self.ticker,
                direction=trade_direction,
                signal_price=ticker_price,
                source="strategy_signal",
                status=AlertStatus.RECEIVED,
            )
            db.add(db_alert)
            db.flush()

            schwab = SchwabService(self.app.state.schwab_client)
            selector = OptionSelector(schwab)
            ws = get_ws_manager()
            trade_mgr = TradeManager(schwab, selector, ws, app=self.app)

            result = await trade_mgr.process_alert(db, db_alert, alert, strategy_params=strategy_params)
            logger.info(f"StrategySignal: trade result — {result.status}: {result.message}")

        except Exception as e:
            logger.exception(f"StrategySignal: error firing signal: {e}")
        finally:
            db.close()

    def _fetch_confirm_bars(self) -> list[BarData]:
        """Fetch recent 1-minute bars for signal confirmation."""
        now = self._now_et()
        start = now - timedelta(minutes=15)
        end = now + timedelta(minutes=1)

        try:
            resp = self.app.state.schwab_client.price_history(
                self.ticker,
                periodType="day",
                period="1",
                frequencyType="minute",
                frequency=1,
                startDate=start,
                endDate=end,
                needExtendedHoursData=False,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"StrategySignal: failed to fetch 1m confirm bars for {self.ticker}: {e}")
            return []

        bars = []
        for candle in data.get("candles", []):
            ts_ms = candle["datetime"]
            ts = datetime.fromtimestamp(ts_ms / 1000, tz=ET)
            bars.append(BarData(
                timestamp=ts,
                open=float(candle["open"]),
                high=float(candle["high"]),
                low=float(candle["low"]),
                close=float(candle["close"]),
                volume=int(candle["volume"]),
            ))

        bars.sort(key=lambda b: b.timestamp)
        return bars

    async def _check_confirmations(self):
        """Check pending signals against 1-minute bars for confirmation."""
        if not self._pending_confirm:
            return

        confirm_bars = self._fetch_confirm_bars()
        if not confirm_bars:
            return

        now = self._now_et()
        freq_minutes = _FREQ_MAP.get(self.timeframe, 5)

        for sig_key, signal in list(self._pending_confirm.items()):
            # The confirmation bar starts when the signal bar closes
            confirm_bar_start = signal.timestamp + timedelta(minutes=freq_minutes)
            confirm_bar_end = confirm_bar_start + timedelta(minutes=1)

            # Has the confirmation bar closed?
            if now < confirm_bar_end + timedelta(seconds=5):
                # Not closed yet — expire if too old
                age = (now - signal.timestamp).total_seconds()
                if age > 300:
                    logger.warning(
                        f"StrategySignal: 1m confirmation expired for {signal.direction} "
                        f"at {sig_key} (>5min old)"
                    )
                    del self._pending_confirm[sig_key]
                continue

            # Find the confirmation bar
            confirm_bar = None
            for bar in confirm_bars:
                if bar.timestamp >= confirm_bar_start:
                    confirm_bar = bar
                    break

            if confirm_bar is None:
                age = (now - signal.timestamp).total_seconds()
                if age > 300:
                    logger.warning(
                        f"StrategySignal: 1m confirmation bar not found for {sig_key}, expired"
                    )
                    del self._pending_confirm[sig_key]
                continue

            # Check confirmation: CALL needs green bar, PUT needs red bar
            is_green = confirm_bar.close > confirm_bar.open
            is_red = confirm_bar.close < confirm_bar.open

            if signal.direction == "CALL" and is_green:
                logger.info(
                    f"StrategySignal: 1m CONFIRMED {signal.direction} — "
                    f"bar {confirm_bar.timestamp.strftime('%H:%M')} green "
                    f"(O={confirm_bar.open:.2f} C={confirm_bar.close:.2f})"
                )
                await self._fire_signal(signal.direction, confirm_bar.close, signal=signal)
                del self._pending_confirm[sig_key]
            elif signal.direction == "PUT" and is_red:
                logger.info(
                    f"StrategySignal: 1m CONFIRMED {signal.direction} — "
                    f"bar {confirm_bar.timestamp.strftime('%H:%M')} red "
                    f"(O={confirm_bar.open:.2f} C={confirm_bar.close:.2f})"
                )
                await self._fire_signal(signal.direction, confirm_bar.close, signal=signal)
                del self._pending_confirm[sig_key]
            else:
                bar_color = "green" if is_green else ("red" if is_red else "doji")
                logger.info(
                    f"StrategySignal: 1m REJECTED {signal.direction} — "
                    f"bar {confirm_bar.timestamp.strftime('%H:%M')} {bar_color} "
                    f"(O={confirm_bar.open:.2f} C={confirm_bar.close:.2f})"
                )
                del self._pending_confirm[sig_key]

    async def _poll_and_check(self):
        """Fetch bars, generate signals, fire any new ones."""
        # Check pending 1-minute confirmations first
        if self._pending_confirm:
            await self._check_confirmations()

        bars = self._fetch_live_bars()
        if not bars:
            return

        engine_params = self._build_engine_params()

        try:
            signals = _generate_signals(
                bars, engine_params,
                prev_close=self._prev_day_close,
                prev_high=self._prev_day_high,
                prev_low=self._prev_day_low,
            )
        except Exception as e:
            logger.warning(f"StrategySignal: signal generation error: {e}")
            return

        for signal in signals:
            sig_key = signal.timestamp.isoformat()
            if sig_key in self.fired_signal_timestamps:
                continue

            self.fired_signal_timestamps.add(sig_key)
            logger.info(
                f"StrategySignal: NEW signal — {signal.direction} at {sig_key} "
                f"(price=${signal.ticker_price:.2f}, reason={signal.reason})"
            )

            if settings.ENTRY_CONFIRM_1M:
                freq_minutes = _FREQ_MAP.get(self.timeframe, 5)
                confirm_time = signal.timestamp + timedelta(minutes=freq_minutes + 1)
                logger.info(
                    f"StrategySignal: awaiting 1m confirmation bar "
                    f"(closes ~{confirm_time.strftime('%H:%M:%S')} ET)"
                )
                self._pending_confirm[sig_key] = signal
            else:
                await self._fire_signal(signal.direction, signal.ticker_price, signal=signal)
            # Only process one signal per poll to avoid rapid-fire entries
            break

    async def run(self):
        logger.info(
            f"StrategySignalTask started: {self.ticker} {self.signal_type} @ {self.timeframe}"
        )

        while True:
            try:
                now = self._now_et()
                today = now.date()

                # New day reset
                if self.today != today:
                    self._reset_day(today)

                # Weekend check
                if today.weekday() >= 5:
                    await asyncio.sleep(60)
                    continue

                current_time = now.time()

                # Before market open
                if current_time < MARKET_OPEN:
                    open_dt = datetime.combine(today, MARKET_OPEN, tzinfo=ET)
                    wait = max((open_dt - now).total_seconds(), 1)
                    logger.debug(f"StrategySignal: waiting {wait:.0f}s for market open")
                    await asyncio.sleep(min(wait, 60))
                    continue

                # After force exit time, done for day (0DTE only; weeklies trade until close)
                is_0dte = self.ticker.upper() in _0DTE_TICKERS
                force_exit = time(settings.FORCE_EXIT_HOUR, settings.FORCE_EXIT_MINUTE)
                if is_0dte and current_time >= force_exit:
                    tomorrow = today + timedelta(days=1)
                    next_open = datetime.combine(tomorrow, time(9, 0), tzinfo=ET)
                    wait = max((next_open - self._now_et()).total_seconds(), 60)
                    await asyncio.sleep(min(wait, 300))
                    continue

                # After market close, done for day (all tickers)
                if current_time >= MARKET_CLOSE:
                    tomorrow = today + timedelta(days=1)
                    next_open = datetime.combine(tomorrow, time(9, 0), tzinfo=ET)
                    wait = max((next_open - self._now_et()).total_seconds(), 60)
                    await asyncio.sleep(min(wait, 300))
                    continue

                # Before first entry time, wait
                first_entry = time(settings.FIRST_ENTRY_HOUR, settings.FIRST_ENTRY_MINUTE)
                if current_time < first_entry:
                    logger.debug(
                        f"StrategySignal: before entry window "
                        f"({settings.FIRST_ENTRY_HOUR}:{settings.FIRST_ENTRY_MINUTE:02d} ET), "
                        f"skipping {self.ticker}"
                    )
                    await asyncio.sleep(30)
                    continue

                # After last entry cutoff, stop generating new signals
                # 0DTE: strict cutoff; weeklies: can enter until force-exit time
                last_entry = (
                    time(settings.LAST_ENTRY_HOUR, settings.LAST_ENTRY_MINUTE)
                    if is_0dte
                    else force_exit
                )
                if current_time >= last_entry:
                    logger.debug(
                        f"StrategySignal: past entry cutoff "
                        f"({settings.LAST_ENTRY_HOUR}:{settings.LAST_ENTRY_MINUTE:02d} ET), "
                        f"skipping {self.ticker}"
                    )
                    await asyncio.sleep(60)
                    continue

                # Poll for signals
                await self._poll_and_check()

                # Sleep for the polling interval
                poll_interval = self._poll_interval_seconds()
                await asyncio.sleep(poll_interval)

            except asyncio.CancelledError:
                logger.info("StrategySignalTask cancelled")
                break
            except Exception as e:
                logger.exception(f"StrategySignalTask error: {e}")
                await asyncio.sleep(30)
