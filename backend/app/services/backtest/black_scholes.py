"""Black-Scholes option pricing for 0DTE backtesting.

Uses math.erf for the normal CDF (no scipy dependency).
VIX is used as the annualized implied volatility input.
"""

import math
from dataclasses import dataclass
from typing import Literal


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


@dataclass
class OptionPrice:
    price: float
    delta: float


def black_scholes(
    S: float,
    K: float,
    T: float,
    sigma: float,
    r: float = 0.05,
    option_type: Literal["CALL", "PUT"] = "CALL",
) -> OptionPrice:
    """Price a European option.

    Args:
        S: underlying price
        K: strike price
        T: time to expiry in years (for 0DTE: minutes_left / 525600)
        sigma: annualized implied vol as decimal (e.g. 0.18)
        r: risk-free rate
        option_type: CALL or PUT
    """
    MIN_T = 1.0 / 525600  # 1 minute in years

    if T < MIN_T:
        if option_type == "CALL":
            return OptionPrice(price=max(S - K, 0.0), delta=1.0 if S > K else 0.0)
        else:
            return OptionPrice(price=max(K - S, 0.0), delta=-1.0 if K > S else 0.0)

    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    exp_rT = math.exp(-r * T)

    if option_type == "CALL":
        price = S * norm_cdf(d1) - K * exp_rT * norm_cdf(d2)
        delta = norm_cdf(d1)
    else:
        price = K * exp_rT * norm_cdf(-d2) - S * norm_cdf(-d1)
        delta = norm_cdf(d1) - 1.0

    return OptionPrice(price=max(price, 0.0), delta=round(delta, 4))


def select_strike_for_delta(
    ticker_price: float,
    target_delta: float,
    minutes_to_expiry: float,
    vix: float,
    option_type: Literal["CALL", "PUT"] = "CALL",
    strike_interval: float = 1.0,
) -> tuple[float, OptionPrice]:
    """Find the strike closest to target delta using real strike intervals."""
    T = minutes_to_expiry / 525600.0
    sigma = vix / 100.0
    atm_strike = round(ticker_price / strike_interval) * strike_interval

    best_strike = atm_strike
    best_opt = black_scholes(ticker_price, atm_strike, T, sigma, option_type=option_type)
    best_diff = abs(abs(best_opt.delta) - target_delta)

    for offset in range(-20, 21):
        strike = atm_strike + offset * strike_interval
        if strike <= 0:
            continue
        opt = black_scholes(ticker_price, strike, T, sigma, option_type=option_type)
        diff = abs(abs(opt.delta) - target_delta)
        if diff < best_diff:
            best_diff = diff
            best_strike = strike
            best_opt = opt

    return best_strike, best_opt


def estimate_option_price_at(
    ticker_price: float,
    strike: float,
    minutes_to_expiry: float,
    vix: float,
    option_type: Literal["CALL", "PUT"] = "CALL",
) -> float:
    """Quick helper: estimate option mid-price at a given moment."""
    T = minutes_to_expiry / 525600.0
    sigma = vix / 100.0
    return black_scholes(ticker_price, strike, T, sigma, option_type=option_type).price
