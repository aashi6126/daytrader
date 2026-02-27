"""Multi-factor option contract selector.

Scores candidates on seven weighted factors:
  1. Delta proximity    (35%)  — how close to the resolved delta target
  2. Expected move      (25%)  — strike placement relative to IV-implied move
  3. Spread tightness   (15%)  — bid-ask spread as fraction of mid
  4. Theta efficiency   (10%)  — delta per dollar of theta decay
  5. Liquidity          (5%)   — log-scaled open interest
  6. Gamma efficiency   (5%)   — gamma per dollar spent (higher = better)
  7. Flow signal        (5%)   — volume/OI ratio (institutional activity)

Hard filters applied before scoring:
  - bid/ask must be positive
  - mid >= MIN_OPTION_PRICE
  - spread% <= OPTION_MAX_SPREAD_PERCENT
  - minimum volume >= MIN_VOLUME
  - minimum open interest >= MIN_OI
  - implied volatility <= MAX_IV (reject overpriced contracts)
  - correct delta sign (positive for calls, negative for puts)
"""

import logging
import math
import time as _time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from app.config import Settings
from app.services.schwab_client import SchwabService

logger = logging.getLogger(__name__)
settings = Settings()

_0DTE_TICKERS = {"SPY", "QQQ"}

# ── Scoring weights ──────────────────────────────────────────────
WEIGHT_DELTA = 0.35
WEIGHT_EXPECTED_MOVE = 0.25
WEIGHT_SPREAD = 0.15
WEIGHT_THETA = 0.10
WEIGHT_LIQUIDITY = 0.05
WEIGHT_GAMMA = 0.05
WEIGHT_FLOW = 0.05

# Hard filter thresholds
MIN_VOLUME = 10
MIN_OI = 500
MAX_IV = 150.0  # reject contracts with IV > 150% (Schwab returns IV as percentage, e.g. 26.3 = 26.3%)

# Default IV assumption when chain data is missing
DEFAULT_IV = 0.20

# IV rank cache: {ticker: (iv_rank, timestamp)}
_IV_RANK_CACHE: dict[str, tuple[float, float]] = {}
IV_RANK_CACHE_TTL = 3600  # 1 hour


class IVRankTooHighError(Exception):
    """Raised when IV rank exceeds the configured threshold."""
    def __init__(self, ticker: str, iv_rank: float, threshold: float):
        self.ticker = ticker
        self.iv_rank = iv_rank
        self.threshold = threshold
        super().__init__(
            f"{ticker} IV Rank {iv_rank:.1f}% >= {threshold:.0f}% — options too expensive"
        )


@dataclass
class OptionContract:
    symbol: str
    strike: float
    bid: float
    ask: float
    mid: float
    delta: float
    expiration: date
    gamma: float = 0.0
    theta: float = 0.0
    open_interest: int = 0
    volume: int = 0

    @property
    def spread_percent(self) -> float:
        if self.mid == 0:
            return float("inf")
        return ((self.ask - self.bid) / self.mid) * 100


class OptionSelector:
    def __init__(self, schwab_service: SchwabService):
        self.schwab = schwab_service

    def compute_iv_rank(self, ticker: str, current_atm_iv: float) -> Optional[float]:
        """Compute IV Rank: where current ATM IV sits relative to 1-year HV range.

        IV Rank = (current_iv - 52w_low_hv) / (52w_high_hv - 52w_low_hv) × 100

        Uses 20-day rolling realized volatility from daily price data as the
        historical baseline. Result is cached for 1 hour per ticker.
        """
        # Check cache
        cached = _IV_RANK_CACHE.get(ticker)
        if cached:
            rank, ts = cached
            if _time.time() - ts < IV_RANK_CACHE_TTL:
                logger.info(f"IV Rank for {ticker}: {rank:.1f}% (cached)")
                return rank

        try:
            candles = self.schwab.fetch_daily_bars(ticker, period_months=12)
            if len(candles) < 30:
                logger.warning(f"Not enough daily bars for {ticker} IV rank ({len(candles)} bars)")
                return None

            # Compute daily log returns
            closes = [c["close"] for c in candles]
            log_returns = [
                math.log(closes[i] / closes[i - 1])
                for i in range(1, len(closes))
                if closes[i - 1] > 0
            ]

            if len(log_returns) < 25:
                return None

            # 20-day rolling realized volatility (annualized)
            window = 20
            hv_series = []
            for i in range(window, len(log_returns) + 1):
                chunk = log_returns[i - window : i]
                std = (sum((r - sum(chunk) / len(chunk)) ** 2 for r in chunk) / (len(chunk) - 1)) ** 0.5
                hv = std * math.sqrt(252)  # annualize
                hv_series.append(hv)

            if not hv_series:
                return None

            hv_min = min(hv_series)
            hv_max = max(hv_series)

            if hv_max - hv_min < 0.001:
                # Flat vol — can't compute meaningful rank
                return None

            iv_rank = ((current_atm_iv - hv_min) / (hv_max - hv_min)) * 100
            iv_rank = max(0.0, min(100.0, iv_rank))

            # Cache result
            _IV_RANK_CACHE[ticker] = (iv_rank, _time.time())

            logger.info(
                f"IV Rank for {ticker}: {iv_rank:.1f}% "
                f"(ATM IV={current_atm_iv:.1%}, HV range={hv_min:.1%}-{hv_max:.1%})"
            )
            return iv_rank

        except Exception as e:
            logger.warning(f"IV rank computation failed for {ticker}: {e}")
            return None

    @staticmethod
    def _next_weekly_expiry(trade_date: date) -> date:
        """Find the next Friday expiry (weekly options)."""
        days_until_friday = (4 - trade_date.weekday()) % 7
        if days_until_friday == 0:
            return trade_date
        return trade_date + timedelta(days=days_until_friday)

    def select_contract(
        self,
        direction: str,
        underlying_price: Optional[float] = None,
        ticker: str = "SPY",
        delta_target: Optional[float] = None,
    ) -> OptionContract:
        effective_delta = delta_target if delta_target is not None else settings.OPTION_DELTA_TARGET

        # Determine target expiry before fetching chain
        is_0dte = ticker.upper() in _0DTE_TICKERS
        if is_0dte:
            target_expiry = date.today()
        else:
            target_expiry = self._next_weekly_expiry(date.today())

        chain = self.schwab.get_option_chain(
            symbol=ticker,
            contract_type=direction,
            strike_count=20,
            from_date=target_expiry,
            to_date=target_expiry,
        )

        chain_price = chain.get("underlyingPrice", underlying_price)
        if chain_price is None:
            raise ValueError(f"Cannot determine {ticker} price for option selection")

        logger.info(f"Option chain for {ticker}: underlyingPrice={chain_price}, alert_price={underlying_price}")

        date_map_key = "callExpDateMap" if direction == "CALL" else "putExpDateMap"
        date_map = chain.get(date_map_key, {})

        if not date_map:
            raise ValueError(f"No {direction} options available for {ticker}")

        logger.info(f"Expiration dates in chain: {list(date_map.keys())}")

        # Find contracts for target expiry
        target_str = target_expiry.isoformat()
        target_contracts = None
        for exp_key, strikes in date_map.items():
            if target_str in exp_key:
                target_contracts = strikes
                break

        if target_contracts is None:
            # Fallback: use nearest available expiry
            all_dates = sorted(date_map.keys())
            if all_dates:
                target_contracts = date_map[all_dates[0]]
                fallback_date = all_dates[0].split(":")[0]
                logger.warning(f"No contracts for {target_str}, using nearest: {fallback_date}")
                target_expiry = date.fromisoformat(fallback_date)
            else:
                raise ValueError(f"No option contracts found for {ticker}")

        # DTE for expected move (minimum 1 day fraction for 0DTE)
        dte = max((target_expiry - date.today()).days, 1)

        strike_keys = sorted([float(k) for k in target_contracts.keys()])
        logger.info(
            f"{ticker} strikes returned ({len(strike_keys)}): "
            f"{strike_keys[:5]}...{strike_keys[-5:]} (underlying={chain_price})"
        )

        # Extract ATM implied volatility for expected move calculation
        # Schwab returns IV as percentage (e.g. 26.3 = 26.3%), convert to decimal
        atm_iv_raw = self._estimate_atm_iv(target_contracts, chain_price)
        atm_iv = atm_iv_raw / 100.0 if atm_iv_raw > 5.0 else atm_iv_raw
        expected_move = chain_price * atm_iv * math.sqrt(dte / 365)
        logger.info(
            f"Expected move: ${expected_move:.2f} "
            f"(IV={atm_iv:.1%}, DTE={dte}, underlying=${chain_price:.2f})"
        )

        # IV Rank gate — reject when options are too expensive
        if settings.IV_RANK_MAX < 100:
            iv_rank = self.compute_iv_rank(ticker, atm_iv)
            if iv_rank is not None and iv_rank >= settings.IV_RANK_MAX:
                raise IVRankTooHighError(ticker, iv_rank, settings.IV_RANK_MAX)

        # Score and rank all contracts
        candidates: list[tuple[float, OptionContract, dict]] = []

        for strike_str, contracts in target_contracts.items():
            for contract_data in contracts:
                raw_delta = contract_data.get("delta", 0)
                delta = abs(raw_delta)
                bid = contract_data.get("bid", 0)
                ask = contract_data.get("ask", 0)
                mid = (bid + ask) / 2
                symbol = contract_data.get("symbol", "")
                strike = float(strike_str)
                gamma = abs(contract_data.get("gamma", 0))
                theta = contract_data.get("theta", 0)
                oi = int(contract_data.get("openInterest", 0))
                vol = int(contract_data.get("totalVolume", 0))

                # ── Hard filters ─────────────────────────────────
                if bid <= 0 or ask <= 0:
                    continue
                if mid < settings.MIN_OPTION_PRICE:
                    continue

                spread_pct = (ask - bid) / mid if mid > 0 else float("inf")
                if spread_pct * 100 > settings.OPTION_MAX_SPREAD_PERCENT:
                    continue

                # Delta sign check
                if direction == "CALL" and raw_delta < 0:
                    continue
                if direction == "PUT" and raw_delta > 0:
                    continue

                # Minimum volume
                if vol < MIN_VOLUME:
                    continue

                # Minimum open interest
                if oi < MIN_OI:
                    continue

                # IV cap — reject overpriced contracts
                contract_iv = contract_data.get("volatility", 0)
                if contract_iv and contract_iv > MAX_IV:
                    continue

                # ── Factor 1: Delta proximity (0 = perfect) ──────
                delta_score = abs(delta - effective_delta) / max(effective_delta, 0.01)

                # ── Factor 2: Expected move distance ─────────────
                # Optimal strike is slightly OTM in the expected-move zone
                if direction == "CALL":
                    optimal_strike = chain_price + expected_move * 0.3
                else:
                    optimal_strike = chain_price - expected_move * 0.3
                move_score = abs(strike - optimal_strike) / max(expected_move, 0.01)
                move_score = min(move_score, 3.0)  # cap outliers

                # ── Factor 3: Spread tightness ───────────────────
                # Normalize so that 10% spread = score 1.0
                spread_score = spread_pct / 0.10

                # ── Factor 4: Liquidity (log-scaled OI) ──────────
                if oi > 0:
                    liquidity_score = 1.0 / math.log(oi + 1)
                else:
                    liquidity_score = 2.0  # penalty for no OI

                # ── Factor 5: Gamma efficiency (gamma / price) ───
                # Higher gamma per dollar = better; normalize so 0.02 = score 1.0
                gamma_per_dollar = gamma / mid if mid > 0 else 0.0
                gamma_score = 0.02 / max(gamma_per_dollar, 0.001) if gamma_per_dollar > 0 else 3.0

                # ── Factor 6: Theta efficiency (delta / |theta|) ─
                # How much directional exposure per dollar of decay
                # Higher ratio = better; invert so lower score = better
                abs_theta = abs(theta)
                if abs_theta > 0:
                    theta_efficiency = delta / abs_theta
                    # Normalize: ratio of 5 = score 1.0 (typical good contract)
                    theta_score = 5.0 / max(theta_efficiency, 0.01)
                else:
                    theta_score = 0.5  # no theta data — neutral

                # ── Factor 7: Flow signal (volume / OI) ──────────
                # High volume relative to OI suggests institutional activity
                if oi > 0:
                    flow_ratio = vol / oi
                    # Normalize: ratio of 1.0 = score 1.0 (lower = better)
                    flow_score = 1.0 / max(flow_ratio, 0.01)
                    flow_score = min(flow_score, 5.0)  # cap penalty for no flow
                else:
                    flow_score = 5.0  # no OI — max penalty

                # ── Composite score (lower = better) ─────────────
                total_score = (
                    WEIGHT_DELTA * delta_score
                    + WEIGHT_EXPECTED_MOVE * move_score
                    + WEIGHT_SPREAD * spread_score
                    + WEIGHT_THETA * theta_score
                    + WEIGHT_LIQUIDITY * liquidity_score
                    + WEIGHT_GAMMA * gamma_score
                    + WEIGHT_FLOW * flow_score
                )

                score_breakdown = {
                    "delta": delta_score,
                    "move": move_score,
                    "spread": spread_score,
                    "theta": theta_score,
                    "liquidity": liquidity_score,
                    "gamma": gamma_score,
                    "flow": flow_score,
                }

                candidates.append((
                    total_score,
                    OptionContract(
                        symbol=symbol,
                        strike=strike,
                        bid=bid,
                        ask=ask,
                        mid=mid,
                        delta=delta,
                        expiration=target_expiry,
                        gamma=gamma,
                        theta=theta,
                        open_interest=oi,
                        volume=vol,
                    ),
                    score_breakdown,
                ))

        if not candidates:
            raise ValueError(
                f"No suitable option contract found for {ticker} (all too illiquid)"
            )

        # Sort by composite score (lower = better)
        candidates.sort(key=lambda x: x[0])

        # Log top 3 candidates
        for i, (score, c, breakdown) in enumerate(candidates[:3]):
            logger.info(
                f"  #{i+1} {c.symbol} strike={c.strike} "
                f"delta={c.delta:.2f} gamma={c.gamma:.4f} "
                f"OI={c.open_interest} vol={c.volume} "
                f"spread={c.spread_percent:.1f}% "
                f"score={score:.4f} "
                f"[d={breakdown['delta']:.2f} m={breakdown['move']:.2f} "
                f"s={breakdown['spread']:.2f} t={breakdown['theta']:.2f} "
                f"l={breakdown['liquidity']:.2f} g={breakdown['gamma']:.2f} "
                f"f={breakdown['flow']:.2f}]"
            )

        best_score, best_contract, best_breakdown = candidates[0]
        logger.info(
            f"Selected: {best_contract.symbol} strike={best_contract.strike} "
            f"delta={best_contract.delta:.2f} (target={effective_delta:.2f}) "
            f"gamma={best_contract.gamma:.4f} OI={best_contract.open_interest} "
            f"bid={best_contract.bid} ask={best_contract.ask} "
            f"spread={best_contract.spread_percent:.1f}% "
            f"composite_score={best_score:.4f}"
        )
        return best_contract

    @staticmethod
    def _estimate_atm_iv(
        contracts_by_strike: dict, underlying_price: float
    ) -> float:
        """Extract implied volatility from the nearest ATM contract."""
        best_distance = float("inf")
        best_iv = DEFAULT_IV

        for strike_str, contracts in contracts_by_strike.items():
            strike = float(strike_str)
            distance = abs(strike - underlying_price)
            if distance < best_distance:
                for c in contracts:
                    iv = c.get("volatility", 0)
                    if iv and iv > 0:
                        best_distance = distance
                        best_iv = iv
                        break

        return best_iv
