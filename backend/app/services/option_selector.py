import logging
from dataclasses import dataclass
from datetime import date
from typing import Optional

from app.config import Settings
from app.services.schwab_client import SchwabService

logger = logging.getLogger(__name__)
settings = Settings()


@dataclass
class OptionContract:
    symbol: str
    strike: float
    bid: float
    ask: float
    mid: float
    delta: float
    expiration: date

    @property
    def spread_percent(self) -> float:
        if self.mid == 0:
            return float("inf")
        return ((self.ask - self.bid) / self.mid) * 100


class OptionSelector:
    def __init__(self, schwab_service: SchwabService):
        self.schwab = schwab_service

    def select_contract(
        self, direction: str, spy_price: Optional[float] = None
    ) -> OptionContract:
        chain = self.schwab.get_option_chain(
            symbol="SPY",
            contract_type=direction,
            strike_count=20,
        )

        underlying_price = chain.get("underlyingPrice", spy_price)
        if underlying_price is None:
            raise ValueError("Cannot determine SPY price for option selection")

        logger.info(f"Option chain underlyingPrice={underlying_price}, alert spy_price={spy_price}")

        date_map_key = "callExpDateMap" if direction == "CALL" else "putExpDateMap"
        date_map = chain.get(date_map_key, {})

        if not date_map:
            raise ValueError(f"No {direction} options available for today")

        logger.info(f"Expiration dates in chain: {list(date_map.keys())}")

        today_str = date.today().isoformat()
        today_contracts = None
        for exp_key, strikes in date_map.items():
            if today_str in exp_key:
                today_contracts = strikes
                break

        if today_contracts is None:
            raise ValueError(f"No 0DTE contracts found for {today_str}")

        strike_keys = sorted([float(k) for k in today_contracts.keys()])
        logger.info(
            f"0DTE strikes returned ({len(strike_keys)}): "
            f"{strike_keys[:5]}...{strike_keys[-5:]} (underlying={underlying_price})"
        )

        best_contract = None
        best_score = float("inf")

        for strike_str, contracts in today_contracts.items():
            for contract_data in contracts:
                delta = abs(contract_data.get("delta", 0))
                bid = contract_data.get("bid", 0)
                ask = contract_data.get("ask", 0)
                mid = (bid + ask) / 2
                symbol = contract_data.get("symbol", "")
                strike = float(strike_str)

                if bid <= 0 or ask <= 0:
                    continue

                spread_pct = ((ask - bid) / mid * 100) if mid > 0 else float("inf")
                if spread_pct > settings.OPTION_MAX_SPREAD_PERCENT:
                    continue

                delta_distance = abs(delta - settings.OPTION_DELTA_TARGET)
                score = delta_distance + (spread_pct / 100)

                if score < best_score:
                    best_score = score
                    best_contract = OptionContract(
                        symbol=symbol,
                        strike=strike,
                        bid=bid,
                        ask=ask,
                        mid=mid,
                        delta=delta,
                        expiration=date.today(),
                    )

        if best_contract is None:
            raise ValueError(
                "No suitable 0DTE option contract found (all too illiquid)"
            )

        logger.info(
            f"Selected: {best_contract.symbol} strike={best_contract.strike} "
            f"delta={best_contract.delta:.2f} bid={best_contract.bid} "
            f"ask={best_contract.ask} spread={best_contract.spread_percent:.1f}%"
        )
        return best_contract
