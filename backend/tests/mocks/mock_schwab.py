from datetime import date


class MockResponse:
    def __init__(self, json_data, status_code=200, headers=None):
        self._json = json_data
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


class MockSchwabClient:
    """Simulates schwabdev.Client for testing."""

    def __init__(self):
        self._order_counter = 1000
        self._orders = {}
        self._quote_overrides = {}

    def linked_accounts(self):
        return MockResponse(
            [{"accountNumber": "12345678", "hashValue": "test-hash-abc123"}]
        )

    def option_chains(self, symbol="SPY", contractType="CALL", **kwargs):
        today = date.today().isoformat()
        today_fmt = date.today().strftime("%y%m%d")
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
                                "bid": 1.50,
                                "ask": 1.60,
                                "delta": 0.45,
                                "openInterest": 5000,
                                "totalVolume": 10000,
                            }
                        ],
                        "602.0": [
                            {
                                "symbol": f"SPY   {today_fmt}C00602000",
                                "bid": 1.20,
                                "ask": 1.35,
                                "delta": 0.25,
                                "openInterest": 3000,
                                "totalVolume": 8000,
                            }
                        ],
                    }
                },
                "putExpDateMap": {
                    f"{today}:0": {
                        "599.0": [
                            {
                                "symbol": f"SPY   {today_fmt}P00599000",
                                "bid": 1.45,
                                "ask": 1.55,
                                "delta": -0.45,
                                "openInterest": 4000,
                                "totalVolume": 9000,
                            }
                        ],
                    }
                },
            }
        )

    def place_order(self, account_hash, order):
        self._order_counter += 1
        order_id = str(self._order_counter)
        self._orders[order_id] = {
            **order,
            "status": "WORKING",
            "orderId": order_id,
            "orderActivityCollection": [],
        }
        return MockResponse(
            json_data=None,
            status_code=201,
            headers={
                "location": f"/accounts/{account_hash}/orders/{order_id}"
            },
        )

    def order_details(self, account_hash, order_id):
        order_id = str(order_id)
        if order_id in self._orders:
            return MockResponse(self._orders[order_id])
        return MockResponse({"error": "Order not found"}, status_code=404)

    def cancel_order(self, account_hash, order_id):
        order_id = str(order_id)
        if order_id in self._orders:
            self._orders[order_id]["status"] = "CANCELED"
        return MockResponse(None, status_code=200)

    def quote(self, symbol):
        if symbol in self._quote_overrides:
            return MockResponse(self._quote_overrides[symbol])
        return MockResponse(
            {
                symbol: {
                    "quote": {
                        "bidPrice": 1.50,
                        "askPrice": 1.60,
                        "lastPrice": 1.55,
                    }
                }
            }
        )

    # --- Test helpers ---

    def simulate_fill(self, order_id: str, fill_price: float):
        order_id = str(order_id)
        if order_id in self._orders:
            self._orders[order_id]["status"] = "FILLED"
            self._orders[order_id]["orderActivityCollection"] = [
                {"executionLegs": [{"price": fill_price}]}
            ]

    def simulate_cancel(self, order_id: str):
        order_id = str(order_id)
        if order_id in self._orders:
            self._orders[order_id]["status"] = "CANCELED"

    def set_quote(self, symbol: str, bid: float, ask: float, last: float = None):
        self._quote_overrides[symbol] = {
            symbol: {
                "quote": {
                    "bidPrice": bid,
                    "askPrice": ask,
                    "lastPrice": last or (bid + ask) / 2,
                }
            }
        }
