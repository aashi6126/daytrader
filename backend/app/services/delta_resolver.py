"""Dynamic delta target resolution based on market regime, expected move, VIX,
and time of day.

Blends four factors into a single delta target:
  1. Market regime (from RegimeClassifier) — weight 40%
  2. Expected move (ATR-based)             — weight 30%
  3. VIX level (high/low/neutral)          — weight 20%
  4. Time of day (late day >= 2 PM ET)     — weight 10%

When a factor is neutral / inactive, its weight redistributes
proportionally to the active factors.
"""

import logging
import math
from dataclasses import dataclass
from datetime import time
from typing import Optional

import pandas as pd

from app.services.regime_classifier import Regime, RegimeClassifier, RegimeResult

logger = logging.getLogger(__name__)


# ── Delta ranges per condition ────────────────────────────────────


@dataclass
class DeltaRange:
    low: float
    high: float

    @property
    def midpoint(self) -> float:
        return (self.low + self.high) / 2


REGIME_DELTA_RANGES: dict[Regime, DeltaRange] = {
    Regime.BREAKOUT: DeltaRange(0.50, 0.65),
    Regime.TREND_CONTINUATION: DeltaRange(0.40, 0.55),
    Regime.CHOP: DeltaRange(0.25, 0.40),
    Regime.UNKNOWN: DeltaRange(0.40, 0.55),  # default to trend range
}

VIX_HIGH_THRESHOLD = 25.0
VIX_LOW_THRESHOLD = 15.0
VIX_HIGH_RANGE = DeltaRange(0.35, 0.50)
VIX_LOW_RANGE = DeltaRange(0.50, 0.70)

LATE_DAY_START = time(14, 0)
LATE_DAY_RANGE = DeltaRange(0.60, 0.75)

# Expected-move ranges: expected_move = ATR_5min * sqrt(bars_in_horizon)
# Thresholds are relative to underlying price (% move)
EXPECTED_MOVE_SMALL_RANGE = DeltaRange(0.30, 0.40)   # small move
EXPECTED_MOVE_MEDIUM_RANGE = DeltaRange(0.40, 0.55)   # medium move
EXPECTED_MOVE_LARGE_RANGE = DeltaRange(0.55, 0.70)    # large move
# Boundaries: small < 0.3% of underlying, large > 0.7%
EXPECTED_MOVE_SMALL_PCT = 0.30
EXPECTED_MOVE_LARGE_PCT = 0.70

WEIGHT_REGIME = 0.40
WEIGHT_EXPECTED_MOVE = 0.30
WEIGHT_VIX = 0.20
WEIGHT_TIME = 0.10


# ── Result object ─────────────────────────────────────────────────


@dataclass
class DeltaResolution:
    delta_target: float
    regime_result: Optional[RegimeResult]
    vix_level: Optional[float]
    is_late_day: bool
    regime_delta: float
    expected_move_delta: Optional[float]
    expected_move_dollars: Optional[float]
    vix_delta: Optional[float]
    time_delta: Optional[float]
    reason: str


# ── Resolver ──────────────────────────────────────────────────────


class DeltaResolver:

    def __init__(self, classifier: Optional[RegimeClassifier] = None):
        self.classifier = classifier or RegimeClassifier()

    def resolve(
        self,
        signal_type: str,
        df: pd.DataFrame,
        vix: Optional[float] = None,
        current_time: Optional[time] = None,
        atr: Optional[float] = None,
        hold_minutes: Optional[int] = None,
        underlying_price: Optional[float] = None,
    ) -> DeltaResolution:
        """Compute blended delta target from regime, expected move, VIX, and time-of-day."""

        # 1. Classify regime
        regime_result = self.classifier.classify(signal_type, df)
        regime = regime_result.final_regime if regime_result.valid else regime_result.initial_regime
        regime_range = REGIME_DELTA_RANGES[regime]
        regime_delta = regime_range.midpoint

        reasons = [
            f"regime={regime.value} -> {regime_delta:.2f} "
            f"({regime_range.low}-{regime_range.high})"
        ]

        # 2. Expected move factor
        expected_move_delta: Optional[float] = None
        expected_move_dollars: Optional[float] = None
        if atr is not None and atr > 0 and hold_minutes and hold_minutes > 0:
            bars_in_horizon = hold_minutes / 5  # 5-min bars
            expected_move_dollars = atr * math.sqrt(bars_in_horizon)

            # Classify move size relative to underlying price
            if underlying_price and underlying_price > 0:
                move_pct = (expected_move_dollars / underlying_price) * 100
                if move_pct <= EXPECTED_MOVE_SMALL_PCT:
                    em_range = EXPECTED_MOVE_SMALL_RANGE
                    label = "small"
                elif move_pct >= EXPECTED_MOVE_LARGE_PCT:
                    em_range = EXPECTED_MOVE_LARGE_RANGE
                    label = "large"
                else:
                    em_range = EXPECTED_MOVE_MEDIUM_RANGE
                    label = "medium"
                expected_move_delta = em_range.midpoint
                reasons.append(
                    f"exp_move=${expected_move_dollars:.2f} ({move_pct:.2f}%, {label}) "
                    f"-> {expected_move_delta:.2f}"
                )

        # 3. VIX adjustment
        vix_delta: Optional[float] = None
        if vix is not None:
            if vix >= VIX_HIGH_THRESHOLD:
                vix_delta = VIX_HIGH_RANGE.midpoint
                reasons.append(f"VIX={vix:.1f} (high) -> {vix_delta:.2f}")
            elif vix <= VIX_LOW_THRESHOLD:
                vix_delta = VIX_LOW_RANGE.midpoint
                reasons.append(f"VIX={vix:.1f} (low) -> {vix_delta:.2f}")
            else:
                reasons.append(f"VIX={vix:.1f} (neutral)")

        # 4. Time-of-day adjustment
        is_late_day = False
        time_delta: Optional[float] = None
        if current_time is not None and current_time >= LATE_DAY_START:
            is_late_day = True
            time_delta = LATE_DAY_RANGE.midpoint
            reasons.append(f"late_day ({current_time.strftime('%H:%M')}) -> {time_delta:.2f}")

        # 5. Blend
        delta_target = self._blend(
            regime_delta, expected_move_delta, vix_delta, time_delta,
            regime_result.confidence,
        )
        delta_target = max(0.20, min(0.80, round(delta_target, 2)))

        reasons.append(f"blended={delta_target:.2f}")
        reason_str = "; ".join(reasons)
        logger.info(f"DeltaResolver: {reason_str}")

        return DeltaResolution(
            delta_target=delta_target,
            regime_result=regime_result,
            vix_level=vix,
            is_late_day=is_late_day,
            regime_delta=regime_delta,
            expected_move_delta=expected_move_delta,
            expected_move_dollars=expected_move_dollars,
            vix_delta=vix_delta,
            time_delta=time_delta,
            reason=reason_str,
        )

    def _blend(
        self,
        regime_delta: float,
        expected_move_delta: Optional[float],
        vix_delta: Optional[float],
        time_delta: Optional[float],
        regime_confidence: float,
    ) -> float:
        """Weighted blend with normalization for inactive factors."""
        active_weights: dict[str, float] = {}
        active_deltas: dict[str, float] = {}

        active_weights["regime"] = WEIGHT_REGIME * max(regime_confidence, 0.3)
        active_deltas["regime"] = regime_delta

        if expected_move_delta is not None:
            active_weights["expected_move"] = WEIGHT_EXPECTED_MOVE
            active_deltas["expected_move"] = expected_move_delta

        if vix_delta is not None:
            active_weights["vix"] = WEIGHT_VIX
            active_deltas["vix"] = vix_delta

        if time_delta is not None:
            active_weights["time"] = WEIGHT_TIME
            active_deltas["time"] = time_delta

        total_weight = sum(active_weights.values())
        if total_weight <= 0:
            return regime_delta

        return sum(
            (w / total_weight) * active_deltas[k]
            for k, w in active_weights.items()
        )

    def resolve_for_backtest(
        self,
        signal_type: str,
        df: pd.DataFrame,
        vix: float,
        signal_time: time,
        atr: Optional[float] = None,
        hold_minutes: Optional[int] = None,
        underlying_price: Optional[float] = None,
    ) -> float:
        """Simplified resolution for backtest engine. Returns the delta float."""
        result = self.resolve(
            signal_type=signal_type,
            df=df,
            vix=vix,
            current_time=signal_time,
            atr=atr,
            hold_minutes=hold_minutes,
            underlying_price=underlying_price,
        )
        return result.delta_target
