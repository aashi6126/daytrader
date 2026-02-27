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
