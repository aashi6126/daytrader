"""Market regime classification for dynamic delta selection.

Maps signal types to initial regimes, then validates against market data
(ADX, ATR, EMA, VWAP) to produce a final regime with confidence score.
"""

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd


class Regime(str, Enum):
    BREAKOUT = "breakout"
    TREND_CONTINUATION = "trend_continuation"
    CHOP = "chop"
    UNKNOWN = "unknown"


@dataclass
class RegimeResult:
    initial_regime: Regime
    final_regime: Regime
    confidence: float
    valid: bool
    reason: str


SIGNAL_REGIME_MAP = {
    "orb": Regime.BREAKOUT,
    "orb_direction": Regime.BREAKOUT,
    "bb_squeeze": Regime.BREAKOUT,
    "ema_cross": Regime.TREND_CONTINUATION,
    "vwap_cross": Regime.TREND_CONTINUATION,
    "ema_vwap": Regime.TREND_CONTINUATION,
    "confluence": Regime.TREND_CONTINUATION,
    "vwap_reclaim": Regime.TREND_CONTINUATION,
    "rsi_reversal": Regime.CHOP,
    "vwap_rsi": Regime.CHOP,
}


def compute_adx(df, period=14):
    high = df["high"]
    low = df["low"]
    close = df["close"]

    plus_dm = high.diff()
    minus_dm = low.diff().abs()

    tr = np.maximum(
        high - low,
        np.maximum(
            abs(high - close.shift()),
            abs(low - close.shift()),
        ),
    )

    atr = tr.rolling(period).mean()

    plus_di = 100 * (plus_dm.rolling(period).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(period).mean() / atr)

    dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100

    adx = dx.rolling(period).mean()

    return adx.iloc[-1]


def compute_atr(df, period=14):
    high = df["high"]
    low = df["low"]
    close = df["close"]

    tr = np.maximum(
        high - low,
        np.maximum(
            abs(high - close.shift()),
            abs(low - close.shift()),
        ),
    )

    return tr.rolling(period).mean().iloc[-1]


def compute_ema(df, length):
    return df["close"].ewm(span=length).mean().iloc[-1]


def compute_vwap(df):
    pv = (df["close"] * df["volume"]).sum()
    v = df["volume"].sum()
    return pv / v


class RegimeClassifier:

    def classify(self, signal_type: str, df: pd.DataFrame) -> RegimeResult:
        initial = SIGNAL_REGIME_MAP.get(signal_type, Regime.UNKNOWN)

        if df.empty or len(df) < 21:
            return RegimeResult(
                initial_regime=initial,
                final_regime=initial,
                confidence=0.5,
                valid=False,
                reason="insufficient bars for regime validation",
            )

        adx = compute_adx(df)
        atr = compute_atr(df)
        ema9 = compute_ema(df, 9)
        ema21 = compute_ema(df, 21)
        vwap = compute_vwap(df)
        price = df["close"].iloc[-1]

        # Metrics
        trend_strength = "CHOP"
        if adx >= 25:
            trend_strength = "STRONG"
        elif adx >= 18:
            trend_strength = "WEAK"

        ema_distance = abs(ema9 - ema21)
        structured = ema_distance > (0.2 * atr)

        vwap_distance = abs(price - vwap)
        expansion = vwap_distance > (0.5 * atr)

        last_range = df.tail(5)["high"].max() - df.tail(5)["low"].min()
        compression = last_range < atr

        # Confidence score
        score = 0
        if adx > 25:
            score += 30
        if expansion:
            score += 25
        if structured:
            score += 25
        if compression:
            score += 20

        confidence = score / 100

        # Final decision
        valid = False
        final = Regime.UNKNOWN
        reason = ""

        if initial == Regime.BREAKOUT:
            if compression and expansion:
                valid = True
                final = Regime.BREAKOUT
                reason = "valid breakout"
            else:
                reason = "breakout failed validation"

        elif initial == Regime.TREND_CONTINUATION:
            if trend_strength != "CHOP" and structured:
                valid = True
                final = Regime.TREND_CONTINUATION
                reason = "valid trend continuation"
            else:
                reason = "trend continuation failed"

        elif initial == Regime.CHOP:
            if trend_strength == "CHOP":
                valid = True
                final = Regime.CHOP
                reason = "valid chop"
            else:
                reason = "chop invalid — market trending"

        return RegimeResult(
            initial_regime=initial,
            final_regime=final,
            confidence=confidence,
            valid=valid,
            reason=reason,
        )
