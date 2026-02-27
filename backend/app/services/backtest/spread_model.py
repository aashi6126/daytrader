"""Dynamic bid-ask spread estimation for backtest realism.

Estimates the full bid-ask spread as a fraction of option mid price,
based on four factors:

  1. Delta (moneyness) — deep OTM options have much wider spreads
  2. Time to close     — spreads widen in the last 30-60 minutes
  3. VIX level         — higher vol environments have wider spreads
  4. Option mid price  — cheap options have a minimum dollar spread floor

Calibrated to SPY 0DTE empirical spreads:
  ATM  (|δ| ~0.50): ~4%    of mid
  OTM  (|δ| ~0.25): ~11%   of mid
  Deep (|δ| ~0.10): ~22%   of mid

Usage in backtest engines:
  - Entry cost = mid × (1 + spread/2)   [buying at the ask]
  - Exit  cost = mid × (1 - spread/2)   [selling at the bid]
"""

from typing import Literal

from app.services.backtest.black_scholes import black_scholes

# ── Delta-to-spread anchors (piecewise linear interpolation) ─────
# Each tuple: (abs_delta, spread_as_fraction_of_mid)
_DELTA_ANCHORS = [
    (0.50, 0.04),
    (0.40, 0.065),
    (0.25, 0.11),
    (0.10, 0.22),
    (0.05, 0.35),
]

# Minimum absolute spread in dollars (affects cheap options)
_MIN_SPREAD_DOLLARS = 0.05

# Maximum spread as fraction of mid (cap)
_MAX_SPREAD_PCT = 0.50


def estimate_spread_pct(
    delta: float,
    minutes_to_close: float,
    vix: float,
    option_mid_price: float,
    is_0dte: bool = True,
    liquidity_mult: float = 1.0,
) -> float:
    """Estimate full bid-ask spread as a fraction of option mid price.

    Returns a value like 0.08 meaning 8% of mid.
    Entry slippage = spread/2, exit slippage = spread/2.

    liquidity_mult scales spreads by ticker liquidity tier:
      1.0 = SPY/QQQ (calibration baseline)
      1.5 = large caps (AAPL, NVDA, TSLA, etc.)
      2.5 = mid liquidity (GLD, PLTR)
      4.0 = illiquid small caps
    """
    # 1. Base spread from delta (moneyness)
    abs_delta = min(abs(delta), 0.99)
    abs_delta = max(abs_delta, 0.01)

    if abs_delta >= _DELTA_ANCHORS[0][0]:
        base_spread = _DELTA_ANCHORS[0][1]
    elif abs_delta <= _DELTA_ANCHORS[-1][0]:
        base_spread = _DELTA_ANCHORS[-1][1]
    else:
        base_spread = _DELTA_ANCHORS[-1][1]  # fallback
        for i in range(len(_DELTA_ANCHORS) - 1):
            d_high, s_high = _DELTA_ANCHORS[i]
            d_low, s_low = _DELTA_ANCHORS[i + 1]
            if d_low <= abs_delta <= d_high:
                t = (abs_delta - d_low) / (d_high - d_low)
                base_spread = s_low + t * (s_high - s_low)
                break

    # 2. Time-to-close multiplier
    if is_0dte:
        if minutes_to_close <= 15:
            time_mult = 2.5
        elif minutes_to_close <= 30:
            time_mult = 2.0
        elif minutes_to_close <= 60:
            t = (60 - minutes_to_close) / 30.0
            time_mult = 1.4 + t * 0.6
        elif minutes_to_close <= 120:
            t = (120 - minutes_to_close) / 60.0
            time_mult = 1.1 + t * 0.3
        else:
            time_mult = 1.0
    else:
        if minutes_to_close <= 60:
            time_mult = 1.3
        elif minutes_to_close <= 120:
            time_mult = 1.1
        else:
            time_mult = 1.0

    # 3. VIX multiplier
    if vix <= 20:
        vix_mult = 1.0
    elif vix <= 30:
        vix_mult = 1.0 + (vix - 20) / 10 * 0.3
    else:
        vix_mult = min(1.3 + (vix - 30) / 20 * 0.2, 1.5)

    # 4. Combine (including per-ticker liquidity multiplier)
    spread_pct = base_spread * time_mult * vix_mult * liquidity_mult

    # 5. Minimum dollar spread floor
    if option_mid_price > 0:
        min_spread_pct = _MIN_SPREAD_DOLLARS / option_mid_price
        spread_pct = max(spread_pct, min_spread_pct)

    # 6. Cap
    return min(spread_pct, _MAX_SPREAD_PCT)


def estimate_option_delta_at(
    ticker_price: float,
    strike: float,
    minutes_to_expiry: float,
    vix: float,
    option_type: Literal["CALL", "PUT"] = "CALL",
) -> float:
    """Compute current option delta via Black-Scholes.

    Used to get the delta at exit time (which differs from entry delta)
    so the spread model reflects the option's current moneyness.
    """
    T = minutes_to_expiry / 525600.0
    sigma = vix / 100.0
    result = black_scholes(ticker_price, strike, T, sigma, option_type=option_type)
    return result.delta
