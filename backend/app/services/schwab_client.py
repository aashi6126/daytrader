import json
import logging
import os
from datetime import date
from typing import Optional
from urllib.parse import urlencode

from app.config import Settings

logger = logging.getLogger(__name__)
settings = Settings()

_client_instance = None
_dry_run_order_counter = 8000
_dry_run_orders = {}  # order_id -> order payload

# Schwab OAuth2 endpoints
SCHWAB_AUTH_URL = "https://api.schwabapi.com/v1/oauth/authorize"
SCHWAB_TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"


def get_authorization_url() -> str:
    """Build the Schwab OAuth2 authorization URL for the user to visit."""
    params = {
        "client_id": settings.SCHWAB_APP_KEY,
        "redirect_uri": settings.SCHWAB_CALLBACK_URL,
    }
    return f"{SCHWAB_AUTH_URL}?{urlencode(params)}"


def get_schwab_client():
    """
    Create a schwabdev.Client using OAuth2 authorization code flow.

    Token management:
    - Tokens stored in SQLite DB (default: ~/.schwabdev/tokens.db)
    - Access tokens auto-refresh every 30 min (handled by schwabdev)
    - Refresh tokens expire after 7 days (requires re-auth via browser)

    First-time setup: run `python -m scripts.auth_setup`
    """
    global _client_instance
    if _client_instance is None:
        try:
            import schwabdev

            tokens_db = os.path.expanduser(settings.SCHWAB_TOKENS_DB)

            _client_instance = schwabdev.Client(
                settings.SCHWAB_APP_KEY,
                settings.SCHWAB_APP_SECRET,
                settings.SCHWAB_CALLBACK_URL,
                tokens_db=tokens_db,
            )
            logger.info("Schwab client created (OAuth2 tokens loaded)")
        except ImportError:
            raise RuntimeError(
                "schwabdev is not installed. Run: pip install schwabdev"
            )
    return _client_instance


def is_authenticated() -> bool:
    """Check if valid Schwab tokens exist."""
    tokens_db = os.path.expanduser(settings.SCHWAB_TOKENS_DB)
    if not os.path.exists(tokens_db):
        return False
    try:
        import sqlite3

        conn = sqlite3.connect(tokens_db)
        cursor = conn.execute(
            "SELECT access_token, refresh_token FROM schwabdev LIMIT 1"
        )
        row = cursor.fetchone()
        conn.close()
        return row is not None and row[0] is not None
    except Exception:
        return False


class SchwabService:
    def __init__(self, client):
        self.client = client
        self.account_hash = settings.SCHWAB_ACCOUNT_HASH
        self.dry_run = settings.DRY_RUN

    def get_option_chain(
        self,
        symbol: str = "SPY",
        contract_type: str = "CALL",
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        strike_count: int = 20,
    ) -> dict:
        today = date.today()
        resp = self.client.option_chains(
            symbol=symbol,
            contractType=contract_type,
            fromDate=from_date or today,
            toDate=to_date or today,
            strikeCount=strike_count,
            includeUnderlyingQuote=True,
        )
        resp.raise_for_status()
        return resp.json()

    def place_order(self, order: dict) -> str:
        global _dry_run_order_counter
        if self.dry_run:
            _dry_run_order_counter += 1
            order_id = str(_dry_run_order_counter)
            _dry_run_orders[order_id] = order
            logger.info(
                f"[DRY RUN] Order {order_id} — would send to Schwab:\n"
                f"{json.dumps(order, indent=2)}"
            )
            return order_id

        resp = self.client.place_order(self.account_hash, order)
        resp.raise_for_status()
        location = resp.headers.get("location", "")
        order_id = location.split("/")[-1]
        logger.info(f"Order placed, order_id={order_id}")
        return order_id

    def get_order_status(self, order_id: str) -> dict:
        if self.dry_run:
            if order_id not in _dry_run_orders:
                # Order was canceled (removed from _dry_run_orders)
                return {"status": "CANCELED"}

            order = _dry_run_orders[order_id]
            fill_price = float(
                order.get("price")
                or order.get("stopPrice")
                or "0"
            )
            logger.info(f"[DRY RUN] Order {order_id} status → FILLED at {fill_price}")
            return {
                "status": "FILLED",
                "price": str(fill_price),
                "orderActivityCollection": [
                    {
                        "executionLegs": [
                            {"price": fill_price}
                        ]
                    }
                ],
            }

        resp = self.client.order_details(self.account_hash, int(order_id))
        resp.raise_for_status()
        return resp.json()

    def cancel_order(self, order_id: str) -> None:
        if self.dry_run and order_id in _dry_run_orders:
            logger.info(f"[DRY RUN] Cancel order {order_id}")
            del _dry_run_orders[order_id]
            return

        resp = self.client.cancel_order(self.account_hash, int(order_id))
        resp.raise_for_status()
        logger.info(f"Order cancelled: {order_id}")

    def get_quote(self, symbol: str) -> dict:
        resp = self.client.quote(symbol)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def build_option_buy_order(
        option_symbol: str,
        quantity: int,
        limit_price: float,
    ) -> dict:
        return {
            "orderType": "LIMIT",
            "session": "NORMAL",
            "duration": "DAY",
            "orderStrategyType": "SINGLE",
            "price": f"{limit_price:.2f}",
            "orderLegCollection": [
                {
                    "instruction": "BUY_TO_OPEN",
                    "quantity": quantity,
                    "instrument": {
                        "symbol": option_symbol,
                        "assetType": "OPTION",
                    },
                }
            ],
        }

    @staticmethod
    def build_option_sell_order(
        option_symbol: str,
        quantity: int,
        order_type: str = "MARKET",
        limit_price: Optional[float] = None,
    ) -> dict:
        order = {
            "orderType": order_type,
            "session": "NORMAL",
            "duration": "DAY",
            "orderStrategyType": "SINGLE",
            "orderLegCollection": [
                {
                    "instruction": "SELL_TO_CLOSE",
                    "quantity": quantity,
                    "instrument": {
                        "symbol": option_symbol,
                        "assetType": "OPTION",
                    },
                }
            ],
        }
        if order_type == "LIMIT" and limit_price is not None:
            order["price"] = f"{limit_price:.2f}"
        return order

    @staticmethod
    def build_stop_loss_order(
        option_symbol: str,
        quantity: int,
        stop_price: float,
    ) -> dict:
        return {
            "orderType": "STOP",
            "session": "NORMAL",
            "duration": "DAY",
            "orderStrategyType": "SINGLE",
            "stopPrice": f"{stop_price:.2f}",
            "orderLegCollection": [
                {
                    "instruction": "SELL_TO_CLOSE",
                    "quantity": quantity,
                    "instrument": {
                        "symbol": option_symbol,
                        "assetType": "OPTION",
                    },
                }
            ],
        }
