"""Tests for StrategyAdapter — regime & volatility-aware parameter adjustment."""

import pytest

from app.config import Settings
from app.services.delta_resolver import DeltaResolution
from app.services.regime_classifier import Regime, RegimeResult
from app.services.strategy_adapter import (
    CLAMP_HOLD,
    CLAMP_PT,
    CLAMP_SL,
    CLAMP_TS,
    AdaptedParams,
    StrategyAdapter,
)


def _make_regime_result(
    regime: Regime, confidence: float = 0.6, valid: bool = True
) -> RegimeResult:
    return RegimeResult(
        initial_regime=regime,
        final_regime=regime,
        confidence=confidence,
        valid=valid,
        reason="test",
    )


def _make_resolution(
    regime: Regime = Regime.TREND_CONTINUATION,
    confidence: float = 0.6,
    vix: float = 20.0,
    valid: bool = True,
) -> DeltaResolution:
    return DeltaResolution(
        delta_target=0.45,
        regime_result=_make_regime_result(regime, confidence, valid),
        vix_level=vix,
        is_late_day=False,
        regime_delta=0.45,
        expected_move_delta=None,
        expected_move_dollars=None,
        vix_delta=None,
        time_delta=None,
        reason="test",
    )


@pytest.fixture
def adapter():
    return StrategyAdapter()


@pytest.fixture
def base_settings():
    return Settings()


# ── Passthrough tests ──────────────────────────────────────────────


def test_passthrough_when_no_resolution(adapter, base_settings):
    """When resolution is None, return base params unchanged."""
    result = adapter.adapt(None, None, base_settings)

    assert result.adapter_applied is False
    assert result.stop_loss_percent == base_settings.STOP_LOSS_PERCENT
    assert result.profit_target_percent == base_settings.PROFIT_TARGET_PERCENT
    assert result.trailing_stop_percent == base_settings.TRAILING_STOP_PERCENT
    assert result.max_hold_minutes == base_settings.MAX_HOLD_MINUTES
    assert result.regime is None
    assert result.vix_at_entry is None


# ── Regime-specific tests ──────────────────────────────────────────


def test_breakout_regime_widens_params(adapter, base_settings):
    """BREAKOUT should widen stops and targets."""
    resolution = _make_resolution(Regime.BREAKOUT, vix=20.0)
    result = adapter.adapt(resolution, None, base_settings)

    assert result.adapter_applied is True
    assert result.regime == "breakout"
    # BREAKOUT: SL×1.3, PT×1.5, TS×1.2, hold×1.2
    assert result.stop_loss_percent > base_settings.STOP_LOSS_PERCENT
    assert result.profit_target_percent > base_settings.PROFIT_TARGET_PERCENT
    assert result.trailing_stop_percent > base_settings.TRAILING_STOP_PERCENT
    assert result.max_hold_minutes > base_settings.MAX_HOLD_MINUTES


def test_trend_continuation_no_change(adapter, base_settings):
    """TREND_CONTINUATION should keep params at base (1.0× multipliers)."""
    resolution = _make_resolution(Regime.TREND_CONTINUATION, vix=20.0)
    result = adapter.adapt(resolution, None, base_settings)

    assert result.adapter_applied is True
    assert result.stop_loss_percent == round(base_settings.STOP_LOSS_PERCENT, 1)
    assert result.profit_target_percent == round(base_settings.PROFIT_TARGET_PERCENT, 1)
    assert result.trailing_stop_percent == round(base_settings.TRAILING_STOP_PERCENT, 1)
    assert result.max_hold_minutes == base_settings.MAX_HOLD_MINUTES


def test_chop_regime_tightens_params(adapter, base_settings):
    """CHOP should tighten stops and targets."""
    resolution = _make_resolution(Regime.CHOP, vix=20.0)
    result = adapter.adapt(resolution, None, base_settings)

    assert result.adapter_applied is True
    assert result.regime == "chop"
    # CHOP: SL×0.7, PT×0.6, TS×0.7, hold×0.6
    assert result.stop_loss_percent < base_settings.STOP_LOSS_PERCENT
    assert result.profit_target_percent < base_settings.PROFIT_TARGET_PERCENT
    assert result.trailing_stop_percent < base_settings.TRAILING_STOP_PERCENT
    assert result.max_hold_minutes < base_settings.MAX_HOLD_MINUTES


def test_unknown_regime_no_change(adapter, base_settings):
    """UNKNOWN should keep params at base (1.0× multipliers)."""
    resolution = _make_resolution(Regime.UNKNOWN, vix=20.0)
    result = adapter.adapt(resolution, None, base_settings)

    assert result.adapter_applied is True
    assert result.stop_loss_percent == round(base_settings.STOP_LOSS_PERCENT, 1)
    assert result.profit_target_percent == round(base_settings.PROFIT_TARGET_PERCENT, 1)


# ── VIX overlay tests ─────────────────────────────────────────────


def test_high_vix_widens_stops_and_reduces_risk(adapter, base_settings):
    """VIX > 25 should widen stops/targets and reduce risk."""
    resolution = _make_resolution(Regime.TREND_CONTINUATION, vix=30.0)
    result = adapter.adapt(resolution, None, base_settings)

    # TREND_CONT×1.0 then VIX_HIGH×1.3 for stops
    assert result.stop_loss_percent == round(base_settings.STOP_LOSS_PERCENT * 1.3, 1)
    assert result.profit_target_percent == round(base_settings.PROFIT_TARGET_PERCENT * 1.3, 1)
    # Risk reduced: ×0.7
    assert result.max_risk_per_trade == round(base_settings.MAX_RISK_PER_TRADE * 0.7, 2)


def test_low_vix_tightens_stops(adapter, base_settings):
    """VIX < 15 should tighten stops/targets."""
    resolution = _make_resolution(Regime.TREND_CONTINUATION, vix=12.0)
    result = adapter.adapt(resolution, None, base_settings)

    # TREND_CONT×1.0 then VIX_LOW×0.8 for stops
    assert result.stop_loss_percent == round(base_settings.STOP_LOSS_PERCENT * 0.8, 1)
    assert result.profit_target_percent == round(base_settings.PROFIT_TARGET_PERCENT * 0.8, 1)
    # Risk unchanged
    assert result.max_risk_per_trade == base_settings.MAX_RISK_PER_TRADE


def test_neutral_vix_no_change(adapter, base_settings):
    """VIX between 15-25 should not change stops."""
    resolution = _make_resolution(Regime.TREND_CONTINUATION, vix=20.0)
    result = adapter.adapt(resolution, None, base_settings)

    assert result.stop_loss_percent == round(base_settings.STOP_LOSS_PERCENT, 1)
    assert result.max_risk_per_trade == base_settings.MAX_RISK_PER_TRADE


# ── Confidence scaling tests ───────────────────────────────────────


def test_low_confidence_reduces_risk(adapter, base_settings):
    """Confidence < 0.4 should halve max risk."""
    resolution = _make_resolution(Regime.TREND_CONTINUATION, confidence=0.3, vix=20.0)
    result = adapter.adapt(resolution, None, base_settings)

    assert result.max_risk_per_trade == round(base_settings.MAX_RISK_PER_TRADE * 0.5, 2)
    assert result.regime_confidence == 0.3


def test_medium_confidence_no_risk_change(adapter, base_settings):
    """Confidence 0.4-0.7 should not change risk."""
    resolution = _make_resolution(Regime.TREND_CONTINUATION, confidence=0.5, vix=20.0)
    result = adapter.adapt(resolution, None, base_settings)

    assert result.max_risk_per_trade == base_settings.MAX_RISK_PER_TRADE


def test_high_confidence_no_risk_change(adapter, base_settings):
    """Confidence > 0.7 should not change risk."""
    resolution = _make_resolution(Regime.TREND_CONTINUATION, confidence=0.85, vix=20.0)
    result = adapter.adapt(resolution, None, base_settings)

    assert result.max_risk_per_trade == base_settings.MAX_RISK_PER_TRADE


# ── Combined scenario tests ───────────────────────────────────────


def test_breakout_high_vix_combined(adapter, base_settings):
    """BREAKOUT + high VIX: stops get very wide, risk is reduced."""
    resolution = _make_resolution(Regime.BREAKOUT, confidence=0.75, vix=28.0)
    result = adapter.adapt(resolution, None, base_settings)

    # SL: 25 × 1.3 (breakout) × 1.3 (VIX) = 42.25
    assert result.stop_loss_percent == pytest.approx(42.2, abs=0.1)
    # PT: 40 × 1.5 × 1.3 = 78
    assert result.profit_target_percent == 78.0
    # Risk: 300 × 0.7 (VIX) = 210 (high conf → no further reduction)
    assert result.max_risk_per_trade == 210.0


def test_chop_low_confidence_combined(adapter, base_settings):
    """CHOP + low confidence: tight params, risk halved."""
    resolution = _make_resolution(Regime.CHOP, confidence=0.3, vix=20.0)
    result = adapter.adapt(resolution, None, base_settings)

    # SL: 25 × 0.7 = 17.5
    assert result.stop_loss_percent == pytest.approx(17.5, abs=0.1)
    # PT: 40 × 0.6 = 24
    assert result.profit_target_percent == 24.0
    # Risk: 300 × 0.5 (low conf) = 150
    assert result.max_risk_per_trade == 150.0


# ── Clamping tests ────────────────────────────────────────────────


def test_stop_loss_clamped_to_max(adapter, base_settings):
    """Stop loss should not exceed upper clamp bound."""
    # BREAKOUT (1.3) + high VIX (1.3) on 25% base = 42.25% (no clamping needed now)
    resolution = _make_resolution(Regime.BREAKOUT, vix=30.0)
    result = adapter.adapt(resolution, None, base_settings)

    assert result.stop_loss_percent <= CLAMP_SL[1]


def test_trailing_stop_clamped_to_min(adapter, base_settings):
    """Trailing stop should not go below lower clamp bound."""
    # CHOP (0.7) + low VIX (0.8) on 20% base = 11.2% — above floor
    # Use strategy_params with very low trailing stop to test floor
    low_params = {"param_trailing_stop_percent": 3.0}
    resolution = _make_resolution(Regime.CHOP, vix=12.0)
    result = adapter.adapt(resolution, low_params, base_settings)

    # 3.0 × 0.7 (chop) × 0.8 (low VIX) = 1.68 → clamped to 3.0
    assert result.trailing_stop_percent >= CLAMP_TS[0]


def test_hold_time_clamped_to_range(adapter, base_settings):
    """Max hold time should be within clamp range."""
    resolution = _make_resolution(Regime.BREAKOUT, vix=20.0)
    result = adapter.adapt(resolution, None, base_settings)

    assert CLAMP_HOLD[0] <= result.max_hold_minutes <= CLAMP_HOLD[1]


# ── Strategy params override tests ────────────────────────────────


def test_strategy_params_override_globals(adapter, base_settings):
    """Per-strategy params should be used as base instead of global settings."""
    strategy_params = {
        "param_stop_loss_percent": 30.0,
        "param_profit_target_percent": 25.0,
        "param_trailing_stop_percent": 10.0,
        "param_max_hold_minutes": 45,
    }
    resolution = _make_resolution(Regime.TREND_CONTINUATION, vix=20.0)
    result = adapter.adapt(resolution, strategy_params, base_settings)

    # TREND_CONT × 1.0 → base values preserved
    assert result.stop_loss_percent == 30.0
    assert result.profit_target_percent == 25.0
    assert result.trailing_stop_percent == 10.0
    assert result.max_hold_minutes == 45


def test_partial_strategy_params_fallback_to_global(adapter, base_settings):
    """Missing strategy params should fall back to global settings."""
    strategy_params = {"param_stop_loss_percent": 40.0}
    resolution = _make_resolution(Regime.TREND_CONTINUATION, vix=20.0)
    result = adapter.adapt(resolution, strategy_params, base_settings)

    assert result.stop_loss_percent == 40.0
    assert result.profit_target_percent == round(base_settings.PROFIT_TARGET_PERCENT, 1)


# ── Metadata tests ────────────────────────────────────────────────


def test_metadata_populated(adapter, base_settings):
    """AdaptedParams should include regime, confidence, and VIX metadata."""
    resolution = _make_resolution(Regime.BREAKOUT, confidence=0.72, vix=22.5)
    result = adapter.adapt(resolution, None, base_settings)

    assert result.regime == "breakout"
    assert result.regime_confidence == 0.72
    assert result.vix_at_entry == 22.5
    assert result.adapter_applied is True
    assert len(result.adjustment_summary) > 0


def test_vix_none_handled(adapter, base_settings):
    """When VIX is None, adapter should still work (neutral overlay)."""
    resolution = _make_resolution(Regime.TREND_CONTINUATION, vix=20.0)
    resolution.vix_level = None
    result = adapter.adapt(resolution, None, base_settings)

    assert result.adapter_applied is True
    assert result.vix_at_entry is None
    # No VIX overlay — pure regime (TREND_CONT 1.0×)
    assert result.stop_loss_percent == round(base_settings.STOP_LOSS_PERCENT, 1)


def test_invalid_regime_falls_back_to_unknown(adapter, base_settings):
    """When regime_result is valid=False, should use initial_regime."""
    resolution = _make_resolution(Regime.BREAKOUT, confidence=0.3, vix=20.0, valid=False)
    result = adapter.adapt(resolution, None, base_settings)

    # valid=False → uses initial_regime (BREAKOUT)
    assert result.regime == "breakout"
    assert result.adapter_applied is True
