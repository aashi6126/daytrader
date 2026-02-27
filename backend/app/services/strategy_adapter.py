"""Dynamic strategy parameter adapter.

Adjusts stop loss, profit target, trailing stop, max hold, and position
sizing based on market regime and volatility context from DeltaResolution.

Regime multiplier matrix:
              Stop   Profit  Trail  Hold
  BREAKOUT    1.3×   1.5×    1.2×   1.2×
  TREND_CONT  1.0×   1.0×    1.0×   1.0×
  CHOP        0.7×   0.6×    0.7×   0.6×
  UNKNOWN     1.0×   1.0×    1.0×   1.0×

VIX overlay (multiplicative):
  High (>25): stops/targets × 1.3, max_risk × 0.7
  Neutral:    no change
  Low  (<15): stops/targets × 0.8

Confidence scaling (risk only):
  Low  (<0.4): max_risk × 0.5
  Med  (0.4–0.7): no change
  High (>0.7): no change
"""

import logging
from dataclasses import dataclass
from typing import Optional

from app.config import Settings
from app.services.delta_resolver import DeltaResolution
from app.services.regime_classifier import Regime

logger = logging.getLogger(__name__)


# ── Regime multipliers ──────────────────────────────────────────


@dataclass
class RegimeMultipliers:
    stop_loss: float
    profit_target: float
    trailing_stop: float
    max_hold: float


REGIME_MULTIPLIERS: dict[Regime, RegimeMultipliers] = {
    Regime.BREAKOUT: RegimeMultipliers(
        stop_loss=1.3, profit_target=1.5, trailing_stop=1.2, max_hold=1.2,
    ),
    Regime.TREND_CONTINUATION: RegimeMultipliers(
        stop_loss=1.0, profit_target=1.0, trailing_stop=1.0, max_hold=1.0,
    ),
    Regime.CHOP: RegimeMultipliers(
        stop_loss=0.7, profit_target=0.6, trailing_stop=0.7, max_hold=0.6,
    ),
    Regime.UNKNOWN: RegimeMultipliers(
        stop_loss=1.0, profit_target=1.0, trailing_stop=1.0, max_hold=1.0,
    ),
}


# ── VIX thresholds ──────────────────────────────────────────────

VIX_HIGH_THRESHOLD = 25.0
VIX_LOW_THRESHOLD = 15.0

VIX_HIGH_STOP_MULT = 1.3
VIX_HIGH_RISK_MULT = 0.7
VIX_LOW_STOP_MULT = 0.8

# ── Confidence thresholds ───────────────────────────────────────

CONFIDENCE_LOW_THRESHOLD = 0.4
CONFIDENCE_LOW_RISK_MULT = 0.5

# ── Clamping ranges ─────────────────────────────────────────────

CLAMP_SL = (5.0, 95.0)
CLAMP_PT = (5.0, 200.0)
CLAMP_TS = (3.0, 50.0)
CLAMP_HOLD = (10, 360)


# ── Result ──────────────────────────────────────────────────────


@dataclass
class AdaptedParams:
    stop_loss_percent: float
    profit_target_percent: float
    trailing_stop_percent: float
    max_hold_minutes: int
    max_risk_per_trade: float
    regime: Optional[str]
    regime_confidence: Optional[float]
    vix_at_entry: Optional[float]
    adapter_applied: bool
    adjustment_summary: str


# ── Adapter ─────────────────────────────────────────────────────


class StrategyAdapter:

    def adapt(
        self,
        resolution: Optional[DeltaResolution],
        base_params: Optional[dict],
        app_settings: Settings,
    ) -> AdaptedParams:
        """Adapt trade parameters based on regime and volatility context.

        Args:
            resolution: Full DeltaResolution from the resolver (None if disabled/failed).
            base_params: Per-strategy params dict (from strategy_signal), or None.
            app_settings: Global settings for fallback values.

        Returns:
            AdaptedParams with adjusted values and metadata.
        """
        # 1. Extract base values
        base_sl = _get_param(base_params, "param_stop_loss_percent", app_settings.STOP_LOSS_PERCENT)
        base_pt = _get_param(base_params, "param_profit_target_percent", app_settings.PROFIT_TARGET_PERCENT)
        base_ts = _get_param(base_params, "param_trailing_stop_percent", app_settings.TRAILING_STOP_PERCENT)
        base_hold = int(_get_param(base_params, "param_max_hold_minutes", app_settings.MAX_HOLD_MINUTES))
        base_risk = app_settings.MAX_RISK_PER_TRADE

        # 2. Passthrough if no resolution
        if resolution is None:
            return AdaptedParams(
                stop_loss_percent=base_sl,
                profit_target_percent=base_pt,
                trailing_stop_percent=base_ts,
                max_hold_minutes=base_hold,
                max_risk_per_trade=base_risk,
                regime=None,
                regime_confidence=None,
                vix_at_entry=None,
                adapter_applied=False,
                adjustment_summary="passthrough (no resolution)",
            )

        # 3. Regime adjustment
        regime_result = resolution.regime_result
        regime = regime_result.final_regime if (regime_result and regime_result.valid) else (
            regime_result.initial_regime if regime_result else Regime.UNKNOWN
        )
        confidence = regime_result.confidence if regime_result else 0.5
        mult = REGIME_MULTIPLIERS.get(regime, REGIME_MULTIPLIERS[Regime.UNKNOWN])

        sl = base_sl * mult.stop_loss
        pt = base_pt * mult.profit_target
        ts = base_ts * mult.trailing_stop
        hold = base_hold * mult.max_hold
        risk_mult = 1.0

        parts = [
            f"regime={regime.value}({confidence:.2f}conf) "
            f"SL:{base_sl:.0f}->{sl:.1f}({mult.stop_loss}x) "
            f"PT:{base_pt:.0f}->{pt:.1f}({mult.profit_target}x) "
            f"TS:{base_ts:.0f}->{ts:.1f}({mult.trailing_stop}x) "
            f"hold:{base_hold}->{hold:.0f}({mult.max_hold}x)"
        ]

        # 4. VIX overlay
        vix = resolution.vix_level
        if vix is not None and vix >= VIX_HIGH_THRESHOLD:
            sl *= VIX_HIGH_STOP_MULT
            pt *= VIX_HIGH_STOP_MULT
            ts *= VIX_HIGH_STOP_MULT
            risk_mult *= VIX_HIGH_RISK_MULT
            parts.append(f"VIX={vix:.1f}(high) stops*{VIX_HIGH_STOP_MULT} risk*{VIX_HIGH_RISK_MULT}")
        elif vix is not None and vix <= VIX_LOW_THRESHOLD:
            sl *= VIX_LOW_STOP_MULT
            pt *= VIX_LOW_STOP_MULT
            ts *= VIX_LOW_STOP_MULT
            parts.append(f"VIX={vix:.1f}(low) stops*{VIX_LOW_STOP_MULT}")
        else:
            parts.append(f"VIX={vix or 'N/A'}(neutral)")

        # 5. Confidence scaling (risk only)
        if confidence < CONFIDENCE_LOW_THRESHOLD:
            risk_mult *= CONFIDENCE_LOW_RISK_MULT
            parts.append(f"low_conf({confidence:.2f}) risk*{CONFIDENCE_LOW_RISK_MULT}")

        # 6. Clamp
        sl = max(CLAMP_SL[0], min(sl, CLAMP_SL[1]))
        pt = max(CLAMP_PT[0], min(pt, CLAMP_PT[1]))
        ts = max(CLAMP_TS[0], min(ts, CLAMP_TS[1]))
        hold = int(max(CLAMP_HOLD[0], min(hold, CLAMP_HOLD[1])))
        risk = round(base_risk * risk_mult, 2)

        parts.append(f"risk={base_risk:.0f}->{risk:.0f}({risk_mult:.2f}x)")
        summary = "; ".join(parts)

        logger.info(f"StrategyAdapter: {summary}")

        return AdaptedParams(
            stop_loss_percent=round(sl, 1),
            profit_target_percent=round(pt, 1),
            trailing_stop_percent=round(ts, 1),
            max_hold_minutes=hold,
            max_risk_per_trade=risk,
            regime=regime.value,
            regime_confidence=round(confidence, 2),
            vix_at_entry=round(vix, 2) if vix is not None else None,
            adapter_applied=True,
            adjustment_summary=summary,
        )


def _get_param(params: Optional[dict], key: str, default: float) -> float:
    """Extract a parameter with fallback to default."""
    if params:
        val = params.get(key)
        if val is not None:
            return float(val)
    return float(default)
