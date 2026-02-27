import pytest

from app.services.option_selector import IVRankTooHighError, OptionSelector
from app.services.schwab_client import SchwabService
from tests.mocks.mock_schwab import MockSchwabClient, MockResponse


def test_select_call_contract(mock_schwab):
    selector = OptionSelector(SchwabService(mock_schwab))
    contract = selector.select_contract("CALL", underlying_price=600.0)

    assert contract is not None
    assert "C" in contract.symbol
    assert contract.bid > 0
    assert contract.ask > 0
    assert contract.delta > 0


def test_select_put_contract(mock_schwab):
    selector = OptionSelector(SchwabService(mock_schwab))
    contract = selector.select_contract("PUT", underlying_price=600.0)

    assert contract is not None
    assert "P" in contract.symbol


def test_delta_targeting(mock_schwab):
    """The contract closest to 0.45 delta should be selected."""
    selector = OptionSelector(SchwabService(mock_schwab))
    contract = selector.select_contract("CALL", underlying_price=600.0)

    # The 601 strike has delta=0.45, the 602 has delta=0.25
    # 601 should win (exact match to target)
    assert contract.strike == 601.0
    assert contract.delta == 0.45


def test_rejects_wide_spread(mock_schwab):
    """Contracts with spread > 10% should be filtered out."""
    from datetime import date

    today = date.today().isoformat()
    today_fmt = date.today().strftime("%y%m%d")

    # Override with a wide-spread-only chain
    original_fn = mock_schwab.option_chains

    def wide_spread_chain(*args, **kwargs):
        return MockResponse(
            {
                "symbol": "SPY",
                "status": "SUCCESS",
                "underlyingPrice": 600.00,
                "callExpDateMap": {
                    f"{today}:0": {
                        "601.0": [
                            {
                                "symbol": f"SPY   {today_fmt}C00601000",
                                "bid": 0.50,
                                "ask": 1.50,  # 100% spread
                                "delta": 0.45,
                            }
                        ],
                    }
                },
                "putExpDateMap": {},
            }
        )

    mock_schwab.option_chains = wide_spread_chain

    selector = OptionSelector(SchwabService(mock_schwab))
    with pytest.raises(ValueError, match="illiquid"):
        selector.select_contract("CALL", underlying_price=600.0)


def test_rejects_zero_bid(mock_schwab):
    from datetime import date

    today = date.today().isoformat()
    today_fmt = date.today().strftime("%y%m%d")

    def zero_bid_chain(*args, **kwargs):
        return MockResponse(
            {
                "symbol": "SPY",
                "status": "SUCCESS",
                "underlyingPrice": 600.00,
                "callExpDateMap": {
                    f"{today}:0": {
                        "601.0": [
                            {
                                "symbol": f"SPY   {today_fmt}C00601000",
                                "bid": 0,
                                "ask": 0.05,
                                "delta": 0.45,
                            }
                        ],
                    }
                },
                "putExpDateMap": {},
            }
        )

    mock_schwab.option_chains = zero_bid_chain

    selector = OptionSelector(SchwabService(mock_schwab))
    with pytest.raises(ValueError, match="illiquid"):
        selector.select_contract("CALL", underlying_price=600.0)


def test_no_0dte_contracts(mock_schwab):
    """When only far-future contracts exist with no volume, all get filtered out."""
    def empty_chain(*args, **kwargs):
        return MockResponse(
            {
                "symbol": "SPY",
                "status": "SUCCESS",
                "underlyingPrice": 600.00,
                "callExpDateMap": {
                    "2099-12-31:365": {
                        "601.0": [
                            {
                                "symbol": "SPY_FUTURE",
                                "bid": 5.0,
                                "ask": 5.10,
                                "delta": 0.50,
                            }
                        ],
                    }
                },
                "putExpDateMap": {},
            }
        )

    mock_schwab.option_chains = empty_chain

    selector = OptionSelector(SchwabService(mock_schwab))
    with pytest.raises(ValueError, match="illiquid"):
        selector.select_contract("CALL", underlying_price=600.0)


def test_empty_chain(mock_schwab):
    def empty_chain(*args, **kwargs):
        return MockResponse(
            {
                "symbol": "SPY",
                "status": "SUCCESS",
                "underlyingPrice": 600.00,
                "callExpDateMap": {},
                "putExpDateMap": {},
            }
        )

    mock_schwab.option_chains = empty_chain

    selector = OptionSelector(SchwabService(mock_schwab))
    with pytest.raises(ValueError, match="No CALL options"):
        selector.select_contract("CALL", underlying_price=600.0)


def test_iv_rank_computation(mock_schwab):
    """IV rank should be computable from historical daily bars."""
    selector = OptionSelector(SchwabService(mock_schwab))
    # Mock generates ~19% annualized vol; ATM IV of 0.18 should be within range
    rank = selector.compute_iv_rank("SPY", current_atm_iv=0.18)
    assert rank is not None
    assert 0 <= rank <= 100


def test_iv_rank_too_high_rejects(mock_schwab, monkeypatch):
    """When IV rank exceeds threshold, select_contract should raise."""
    import app.services.option_selector as os_mod

    # Set a very low threshold so the mock data triggers rejection
    monkeypatch.setattr(os_mod.settings, "IV_RANK_MAX", 0.1)

    # Clear cache to force recomputation
    os_mod._IV_RANK_CACHE.clear()

    selector = OptionSelector(SchwabService(mock_schwab))
    with pytest.raises(IVRankTooHighError):
        selector.select_contract("CALL", underlying_price=600.0)


def test_iv_rank_pass_allows_trade(mock_schwab, monkeypatch):
    """When IV rank is below threshold, trade should proceed normally."""
    import app.services.option_selector as os_mod

    # Set threshold high enough that mock data passes
    monkeypatch.setattr(os_mod.settings, "IV_RANK_MAX", 99.0)

    os_mod._IV_RANK_CACHE.clear()

    selector = OptionSelector(SchwabService(mock_schwab))
    contract = selector.select_contract("CALL", underlying_price=600.0)
    assert contract is not None
    assert contract.delta > 0
