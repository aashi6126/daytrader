# Pro-Trader Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Harden the daytrader platform with 7 pro-level risk management and signal quality improvements.

**Architecture:** All changes are backend-only. Config defaults change in `config.py`. Signal gating happens in `strategy_signal.py` and `trade_manager.py`. The Signal dataclass gains two optional fields for confidence-based sizing. Order fallback logic is removed from `order_manager.py`. A new event calendar JSON file gates afternoon trades on FOMC/CPI days.

**Tech Stack:** Python, FastAPI, SQLAlchemy, Schwab API, pytest

---

### Task 1: Restrict live signals to ORB + Confluence

**Files:**
- Modify: `backend/app/config.py:11-129` (add setting)
- Modify: `backend/app/tasks/strategy_signal.py:209-424` (gate signal firing)
- Test: `backend/tests/test_strategy_signal_gate.py` (new)

**Step 1: Add config setting**

In `backend/app/config.py`, add after line 113 (`ACTIVE_STRATEGY`):

```python
    # Allowed signal types for live trading (backtest can still test all)
    ALLOWED_LIVE_SIGNAL_TYPES: List[str] = ["orb", "orb_direction", "confluence"]
```

**Step 2: Gate signal firing in StrategySignalTask**

In `backend/app/tasks/strategy_signal.py`, in the `_fire_signal` method (line 209), add a check at the top:

```python
    async def _fire_signal(self, direction: str, ticker_price: float):
        """Create a synthetic alert and route through TradeManager."""
        # Gate: only fire signals for allowed live signal types
        if self.signal_type not in settings.ALLOWED_LIVE_SIGNAL_TYPES:
            logger.info(
                f"StrategySignal: BLOCKED {self.signal_type} for {self.ticker} "
                f"(not in ALLOWED_LIVE_SIGNAL_TYPES)"
            )
            return
```

**Step 3: Write test**

Create `backend/tests/test_strategy_signal_gate.py`:

```python
"""Tests for signal type gating in StrategySignalTask."""
import os
os.environ.setdefault("WEBHOOK_SECRET", "test-secret")
os.environ.setdefault("SCHWAB_APP_KEY", "test-key")
os.environ.setdefault("SCHWAB_APP_SECRET", "test-secret-value")
os.environ.setdefault("SCHWAB_ACCOUNT_HASH", "test-hash")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Settings


def _make_task(signal_type: str):
    from app.tasks.strategy_signal import StrategySignalTask
    app = MagicMock()
    config = {
        "ticker": "SPY",
        "timeframe": "5m",
        "signal_type": signal_type,
        "params": {},
    }
    return StrategySignalTask(app, config)


@pytest.mark.asyncio
async def test_blocked_signal_type_does_not_fire():
    task = _make_task("ema_cross")  # not in allowed list
    with patch.object(task, '_get_strategy_params', return_value={}):
        await task._fire_signal("CALL", 600.0)
    # Should not attempt to create alert or trade — just return


@pytest.mark.asyncio
async def test_allowed_signal_type_fires():
    task = _make_task("confluence")  # in allowed list
    # Patch the DB and trade manager internals to verify _fire_signal proceeds
    with patch("app.tasks.strategy_signal.SessionLocal") as mock_db_cls, \
         patch("app.tasks.strategy_signal.TradeManager") as mock_tm_cls:
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        mock_tm = AsyncMock()
        mock_tm.process_alert = AsyncMock(return_value=MagicMock(status="accepted", message="ok"))
        mock_tm_cls.return_value = mock_tm
        await task._fire_signal("CALL", 600.0)
        mock_tm.process_alert.assert_called_once()
```

**Step 4: Run tests**

Run: `cd backend && python -m pytest tests/test_strategy_signal_gate.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/config.py backend/app/tasks/strategy_signal.py backend/tests/test_strategy_signal_gate.py
git commit -m "feat: restrict live signals to ORB + Confluence only"
```

---

### Task 2: ATR stops as primary, reduce flat % fallback

**Files:**
- Modify: `backend/app/config.py:36` (change STOP_LOSS_PERCENT default)
- Modify: `backend/app/tasks/strategy_signal.py:120` (change atr_period default)
- Test: `backend/tests/test_order_manager.py` (update existing test expectations)

**Step 1: Change config default**

In `backend/app/config.py`, change line 36:

```python
    STOP_LOSS_PERCENT: float = 25.0  # Safety-net fallback only; ATR stops are primary
```

**Step 2: Change atr_period default in StrategySignalTask**

In `backend/app/tasks/strategy_signal.py`, line 120, change the default from 0 to 14:

```python
            atr_period=int(p.get("atr_period", 14)),
```

**Step 3: Update existing test expectations**

In `backend/tests/test_order_manager.py`, line 47, the test checks stop price against the old 60% stop. Update to expect ATR-based stop (the mock trade has no ATR data, so it falls back to 25%):

```python
    assert trade.stop_loss_price == pytest.approx(1.55 * 0.75, abs=0.01)
```

**Step 4: Run tests**

Run: `cd backend && python -m pytest tests/test_order_manager.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/config.py backend/app/tasks/strategy_signal.py backend/tests/test_order_manager.py
git commit -m "feat: ATR stops as primary mechanism, reduce flat % to 25% fallback"
```

---

### Task 3: Move force exit to 3:00 PM, last entry to 2:15 PM

**Files:**
- Modify: `backend/app/config.py:74,78` (change times)
- Test: `backend/tests/test_exit_engine.py` (verify existing tests still pass)

**Step 1: Change config defaults**

In `backend/app/config.py`:

Line 74: `FORCE_EXIT_MINUTE: int = 0` (was 30)
Line 78: `LAST_ENTRY_MINUTE: int = 15` (was 45)

**Step 2: Run existing tests**

Run: `cd backend && python -m pytest tests/test_exit_engine.py -v`
Expected: PASS (exit engine tests use settings which now reflect 3:00 PM)

**Step 3: Commit**

```bash
git add backend/app/config.py
git commit -m "feat: move force exit to 3:00 PM, last entry to 2:15 PM"
```

---

### Task 4: Kill limit order fallback — limit or cancel

**Files:**
- Modify: `backend/app/config.py:45` (reduce timeout)
- Modify: `backend/app/services/order_manager.py:92-177` (replace fallback with cancel)
- Test: `backend/tests/test_order_manager.py` (new test for timeout cancellation)

**Step 1: Reduce timeout**

In `backend/app/config.py`, line 45:

```python
    ENTRY_LIMIT_TIMEOUT_MINUTES: float = 1.0  # Cancel after 60s — don't chase
```

**Step 2: Replace fallback with cancel**

In `backend/app/services/order_manager.py`, replace the fallback block (lines 92-177) with:

```python
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
```

**Step 3: Write test for timeout cancellation**

Add to `backend/tests/test_order_manager.py`:

```python
@pytest.mark.asyncio
async def test_entry_limit_timeout_cancels_trade(db_session, mock_schwab, ws_manager):
    """Limit timeout should cancel the trade, not place a fallback order."""
    trade, order_id = _make_trade(db_session, mock_schwab)
    # Backdate created_at to simulate timeout
    trade.created_at = datetime.utcnow() - timedelta(minutes=5)
    db_session.commit()

    order_mgr = OrderManager(SchwabService(mock_schwab), ws_manager)
    changed = await order_mgr.check_entry_fill(db_session, trade)

    assert changed is True
    assert trade.status == TradeStatus.CANCELLED
    # Should NOT have placed a new order (no fallback)
    assert trade.entry_is_fallback is False
```

Add `from datetime import timedelta` to the imports if not already present.

**Step 4: Run tests**

Run: `cd backend && python -m pytest tests/test_order_manager.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/config.py backend/app/services/order_manager.py backend/tests/test_order_manager.py
git commit -m "feat: kill limit order fallback — cancel on timeout instead of chasing"
```

---

### Task 5: VIX > 28 circuit breaker

**Files:**
- Modify: `backend/app/config.py` (add VIX_CIRCUIT_BREAKER)
- Modify: `backend/app/services/trade_manager.py:248-288` (add VIX check)
- Test: `backend/tests/test_trade_manager.py` (new test)

**Step 1: Add config setting**

In `backend/app/config.py`, add after `MAX_CONSECUTIVE_LOSSES` (line 41):

```python
    # VIX circuit breaker — block all new trades when VIX >= this
    VIX_CIRCUIT_BREAKER: float = 28.0
```

**Step 2: Add VIX check to process_alert**

In `backend/app/services/trade_manager.py`, add a new check after the time-of-day window check (after line 288, before the daily trade count check). Insert this block:

```python
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
```

Also add the same VIX check to `retake_trade()`, after the time-of-day check (after line 674).

**Step 3: Write test**

Add to `backend/tests/test_trade_manager.py`:

```python
@pytest.mark.asyncio
async def test_vix_circuit_breaker_rejects_trade(db_session, trade_manager_deps):
    """Trades should be rejected when VIX >= circuit breaker threshold."""
    alert = TradingViewAlert(
        ticker="SPY", action="BUY_CALL", secret="test-secret", price=600.0,
    )
    db_alert = Alert(
        raw_payload="{}", ticker="SPY", direction=TradeDirection.CALL,
        signal_price=600.0, status=AlertStatus.RECEIVED,
    )
    db_session.add(db_alert)
    db_session.flush()

    # Mock VIX to be above circuit breaker
    mock_streaming = MagicMock()
    mock_vix_snap = MagicMock()
    mock_vix_snap.is_stale = False
    mock_vix_snap.last = 32.0
    mock_streaming.get_equity_quote.return_value = mock_vix_snap

    with patch("app.services.trade_manager.get_streaming_service", return_value=mock_streaming):
        result = await trade_manager_deps.process_alert(db_session, db_alert, alert)

    assert result.status == "rejected"
    assert "VIX circuit breaker" in result.message
```

Add `from unittest.mock import MagicMock, patch` to test imports.

**Step 4: Run tests**

Run: `cd backend && python -m pytest tests/test_trade_manager.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/config.py backend/app/services/trade_manager.py backend/tests/test_trade_manager.py
git commit -m "feat: add VIX >= 28 circuit breaker to block trades in high-vol regimes"
```

---

### Task 6: FOMC/CPI calendar — no afternoon trades on event days

**Files:**
- Create: `backend/data/event_calendar.json`
- Modify: `backend/app/config.py` (add EVENT_CALENDAR_PATH)
- Modify: `backend/app/services/trade_manager.py` (add afternoon block on event days)
- Test: `backend/tests/test_trade_manager.py` (new test)

**Step 1: Create event calendar file**

Create `backend/data/event_calendar.json` with known 2025-2026 FOMC/CPI dates:

```json
{
  "_comment": "Dates when afternoon trading is blocked. FOMC decisions at 2:00 PM ET, CPI at 8:30 AM (but causes afternoon volatility). Update this file before each quarter.",
  "blocked_afternoons": [
    "2025-01-29", "2025-02-12", "2025-03-12", "2025-03-19",
    "2025-04-10", "2025-05-07", "2025-05-13", "2025-06-11", "2025-06-18",
    "2025-07-10", "2025-07-30", "2025-08-13", "2025-09-10", "2025-09-17",
    "2025-10-15", "2025-10-29", "2025-11-12", "2025-12-10", "2025-12-17",
    "2026-01-14", "2026-01-28", "2026-02-11", "2026-02-18",
    "2026-03-11", "2026-03-18", "2026-04-15", "2026-04-29",
    "2026-05-13", "2026-05-06", "2026-06-10", "2026-06-17",
    "2026-07-15", "2026-07-29", "2026-08-12", "2026-09-16", "2026-09-23",
    "2026-10-14", "2026-10-28", "2026-11-12", "2026-12-09", "2026-12-16"
  ]
}
```

**Step 2: Add config setting**

In `backend/app/config.py`, add after `AFTERNOON_WINDOW_ENABLED`:

```python
    # Event calendar: block afternoon trades on FOMC/CPI days
    EVENT_CALENDAR_PATH: str = "data/event_calendar.json"
```

**Step 3: Add afternoon block logic to TradeManager**

In `backend/app/services/trade_manager.py`, add a helper method and a check in `process_alert`. Add this method to the `TradeManager` class:

```python
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
```

Then in `process_alert`, add this check after the time-of-day window check (after the VIX circuit breaker from Task 5). Insert right before the daily trade count check:

```python
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
```

**Step 4: Write test**

Add to `backend/tests/test_trade_manager.py`:

```python
@pytest.mark.asyncio
async def test_event_calendar_blocks_afternoon_trades(db_session, trade_manager_deps):
    """Afternoon trades should be blocked on FOMC/CPI days."""
    alert = TradingViewAlert(
        ticker="SPY", action="BUY_CALL", secret="test-secret", price=600.0,
    )
    db_alert = Alert(
        raw_payload="{}", ticker="SPY", direction=TradeDirection.CALL,
        signal_price=600.0, status=AlertStatus.RECEIVED,
    )
    db_session.add(db_alert)
    db_session.flush()

    # Mock: current time is 1:00 PM ET, and today is a blocked day
    mock_now = datetime(2026, 3, 18, 13, 0, tzinfo=ZoneInfo("America/New_York"))
    with patch("app.services.trade_manager.datetime") as mock_dt, \
         patch.object(TradeManager, "_is_event_afternoon_blocked", return_value=(True, "Event day (2026-03-18)")):
        mock_dt.now.return_value = mock_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        # Also need to mock VIX check to not interfere
        with patch("app.services.trade_manager.get_streaming_service") as mock_stream:
            mock_snap = MagicMock()
            mock_snap.is_stale = False
            mock_snap.last = 18.0  # low VIX, should pass
            mock_stream.return_value.get_equity_quote.return_value = mock_snap
            result = await trade_manager_deps.process_alert(db_session, db_alert, alert)

    assert result.status == "rejected"
    assert "Afternoon trading blocked" in result.message
```

Add `from zoneinfo import ZoneInfo` and `from datetime import datetime` to test imports if not already present.

**Step 5: Run tests**

Run: `cd backend && python -m pytest tests/test_trade_manager.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add backend/data/event_calendar.json backend/app/config.py backend/app/services/trade_manager.py backend/tests/test_trade_manager.py
git commit -m "feat: block afternoon trades on FOMC/CPI event days via calendar"
```

---

### Task 7: Confidence-based position sizing (confluence score + volume)

**Files:**
- Modify: `backend/app/services/backtest/engine.py:304-312` (add fields to Signal)
- Modify: `backend/app/services/backtest/engine.py:560-567` (populate confluence_score + rel_vol)
- Modify: `backend/app/tasks/strategy_signal.py:196-207,404-424` (pass score through)
- Modify: `backend/app/services/trade_manager.py:504-515` (confidence-based sizing)
- Modify: `backend/app/config.py` (add CONFLUENCE_DOUBLE_MIN_SCORE, CONFLUENCE_DOUBLE_MIN_REL_VOL)
- Test: `backend/tests/test_confidence_sizing.py` (new)

**Step 1: Add fields to Signal dataclass**

In `backend/app/services/backtest/engine.py`, modify the Signal dataclass (lines 304-311):

```python
@dataclass
class Signal:
    timestamp: datetime
    direction: Literal["CALL", "PUT"]
    ticker_price: float
    reason: str
    orb_range: Optional[float] = None
    orb_entry_level: Optional[float] = None
    confluence_score: Optional[int] = None
    confluence_max_score: Optional[int] = None
    rel_vol: Optional[float] = None
```

**Step 2: Populate new fields when generating confluence signals**

In `backend/app/services/backtest/engine.py`, modify the confluence signal creation (around lines 560-567). Replace the existing signal direction/reason assignment:

```python
            # Fire signal if score meets minimum confluence threshold
            max_score = 7 if pivots is not None else 6

            # Compute rel_vol for this bar (used for confidence sizing)
            bar_rel_vol = None
            if vol_sma[i] is not None and vol_sma[i] > 0:
                bar_rel_vol = round(bar.volume / vol_sma[i], 2)

            if call_score >= params.min_confluence and call_score > put_score:
                direction = "CALL"
                reason = f"Confluence {call_score}/{max_score}: {', '.join(call_factors)}"
                sig_confluence_score = call_score
                sig_confluence_max = max_score
                sig_rel_vol = bar_rel_vol
            elif put_score >= params.min_confluence and put_score > call_score:
                direction = "PUT"
                reason = f"Confluence {put_score}/{max_score}: {', '.join(put_factors)}"
                sig_confluence_score = put_score
                sig_confluence_max = max_score
                sig_rel_vol = bar_rel_vol
```

Then at the bottom where signals are appended (line 716), add the new fields. Declare `sig_confluence_score`, `sig_confluence_max`, `sig_rel_vol` as `None` at the top of the loop body (alongside `sig_orb_range`/`sig_orb_entry`), then pass them in the Signal constructor:

At the top of the for-loop body (after line 471 `sig_orb_entry: Optional[float] = None`), add:

```python
        sig_confluence_score: Optional[int] = None
        sig_confluence_max: Optional[int] = None
        sig_rel_vol: Optional[float] = None
```

And in the Signal append (line 716):

```python
        if direction:
            signals.append(Signal(
                timestamp=bar.timestamp,
                direction=direction,
                ticker_price=bar.close,
                reason=reason,
                orb_range=sig_orb_range,
                orb_entry_level=sig_orb_entry,
                confluence_score=sig_confluence_score,
                confluence_max_score=sig_confluence_max,
                rel_vol=sig_rel_vol,
            ))
```

**Step 3: Pass score through StrategySignalTask**

In `backend/app/tasks/strategy_signal.py`, modify `_get_strategy_params` (line 196) to accept optional signal data:

```python
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
```

Then update the callers. In `_fire_signal` (line 209), accept an optional `signal` parameter:

```python
    async def _fire_signal(self, direction: str, ticker_price: float, signal=None):
```

And pass it to `_get_strategy_params`:

```python
        strategy_params = self._get_strategy_params(signal)
```

In `_poll_and_check` (line 424), pass the signal object:

```python
                await self._fire_signal(signal.direction, signal.ticker_price, signal=signal)
```

In `_check_confirmations` (lines 362, 370), pass the signal:

```python
                await self._fire_signal(signal.direction, confirm_bar.close, signal=signal)
```

(Note: when confirming, ticker_price changes to confirm_bar.close but the confluence score from the original signal is preserved.)

**Step 4: Add config settings**

In `backend/app/config.py`, add after `MAX_RISK_PER_TRADE`:

```python
    # Confidence-based sizing for confluence strategy
    CONFLUENCE_DOUBLE_MIN_SCORE: int = 6  # Score needed (out of 6) for double size
    CONFLUENCE_DOUBLE_MIN_REL_VOL: float = 2.0  # Relative volume needed for double size
    CONFLUENCE_HALF_MAX_SCORE: int = 5  # Score at or below this = half size
```

**Step 5: Implement confidence-based sizing in TradeManager**

In `backend/app/services/trade_manager.py`, after the position sizing block (around line 513, after `quantity = settings.DEFAULT_QUANTITY`), add:

```python
        # Confidence-based sizing: scale quantity for confluence signals
        confluence_score = strategy_params.get("confluence_score") if strategy_params else None
        if confluence_score is not None:
            max_score = strategy_params.get("confluence_max_score", 6)
            rel_vol = strategy_params.get("rel_vol") or 0
            if (
                confluence_score >= settings.CONFLUENCE_DOUBLE_MIN_SCORE
                and rel_vol >= settings.CONFLUENCE_DOUBLE_MIN_REL_VOL
            ):
                quantity = quantity * 2
                logger.info(
                    f"Confidence sizing: DOUBLE (score={confluence_score}/{max_score}, "
                    f"rel_vol={rel_vol:.1f}x) -> {quantity} contracts"
                )
            elif confluence_score <= settings.CONFLUENCE_HALF_MAX_SCORE:
                quantity = max(1, quantity // 2)
                logger.info(
                    f"Confidence sizing: HALF (score={confluence_score}/{max_score}) "
                    f"-> {quantity} contracts"
                )
```

**Step 6: Write test**

Create `backend/tests/test_confidence_sizing.py`:

```python
"""Tests for confidence-based position sizing."""
import os
os.environ.setdefault("WEBHOOK_SECRET", "test-secret")
os.environ.setdefault("SCHWAB_APP_KEY", "test-key")
os.environ.setdefault("SCHWAB_APP_SECRET", "test-secret-value")
os.environ.setdefault("SCHWAB_ACCOUNT_HASH", "test-hash")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from datetime import datetime, time
from app.services.backtest.engine import Signal


def test_signal_has_confluence_fields():
    sig = Signal(
        timestamp=datetime(2026, 1, 5, 10, 0),
        direction="CALL",
        ticker_price=600.0,
        reason="Confluence 6/6: VWAP, EMA, RSI, MACD, Vol, Candle",
        confluence_score=6,
        confluence_max_score=6,
        rel_vol=2.5,
    )
    assert sig.confluence_score == 6
    assert sig.rel_vol == 2.5


def test_signal_defaults_none():
    sig = Signal(
        timestamp=datetime(2026, 1, 5, 10, 0),
        direction="CALL",
        ticker_price=600.0,
        reason="EMA cross",
    )
    assert sig.confluence_score is None
    assert sig.rel_vol is None


def test_double_sizing_logic():
    """6/6 + rel_vol >= 2.0 should double the quantity."""
    from app.config import Settings
    settings = Settings()
    base_qty = 2
    score = 6
    rel_vol = 2.5
    if score >= settings.CONFLUENCE_DOUBLE_MIN_SCORE and rel_vol >= settings.CONFLUENCE_DOUBLE_MIN_REL_VOL:
        qty = base_qty * 2
    else:
        qty = base_qty
    assert qty == 4


def test_half_sizing_logic():
    """5/6 score should halve the quantity."""
    from app.config import Settings
    settings = Settings()
    base_qty = 2
    score = 5
    if score <= settings.CONFLUENCE_HALF_MAX_SCORE:
        qty = max(1, base_qty // 2)
    else:
        qty = base_qty
    assert qty == 1


def test_normal_sizing_no_confluence():
    """Non-confluence signals should not change sizing."""
    base_qty = 2
    confluence_score = None
    if confluence_score is not None:
        base_qty = 999  # should not reach here
    assert base_qty == 2
```

**Step 7: Run all tests**

Run: `cd backend && python -m pytest tests/test_confidence_sizing.py -v`
Expected: PASS

**Step 8: Commit**

```bash
git add backend/app/services/backtest/engine.py backend/app/tasks/strategy_signal.py backend/app/services/trade_manager.py backend/app/config.py backend/tests/test_confidence_sizing.py
git commit -m "feat: confidence-based sizing — double on 6/6+volume, half on 5/6"
```

---

### Task 8: Final integration test and full test suite

**Step 1: Run full test suite**

Run: `cd backend && python -m pytest tests/ -v`
Expected: All tests PASS

**Step 2: Fix any failures**

If any existing tests broke due to config changes (STOP_LOSS_PERCENT, FORCE_EXIT_MINUTE, etc.), update their expected values to match the new defaults.

**Step 3: Final commit**

```bash
git add -A
git commit -m "fix: update test expectations for new pro-trader defaults"
```
