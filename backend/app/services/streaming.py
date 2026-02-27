"""Schwab WebSocket streaming service for real-time market data.

Manages a single StreamAsync connection, handles subscriptions dynamically
as trades open/close, and caches the latest quotes in memory. Consumers
check the cache first and fall back to REST when data is stale (>30s).
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from app.config import Settings

logger = logging.getLogger(__name__)
settings = Settings()

# ── Field indices (from schwabdev/translate.py) ─────────────────────

# LEVELONE_OPTIONS: 0=Symbol, 2=Bid, 3=Ask, 4=Last, 5=High, 6=Low,
# 7=Close, 8=Volume, 9=OI, 10=IV, 28=Delta, 29=Gamma, 30=Theta, 31=Vega
OPTION_FIELDS = "0,2,3,4,5,6,7,8,9,10,28,29,30,31"

# LEVELONE_EQUITIES: 0=Symbol, 1=Bid, 2=Ask, 3=Last, 8=Volume
EQUITY_FIELDS = "0,1,2,3,8"


# ── Quote cache dataclass ───────────────────────────────────────────


@dataclass
class QuoteSnapshot:
    symbol: str
    bid: float = 0.0
    ask: float = 0.0
    last: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: int = 0
    open_interest: int = 0
    iv: float = 0.0
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    updated_at: float = 0.0

    @property
    def mid(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return self.last

    @property
    def spread_pct(self) -> float:
        mid = self.mid
        if mid > 0 and self.bid > 0 and self.ask > 0:
            return ((self.ask - self.bid) / mid) * 100
        return 0.0

    @property
    def is_stale(self) -> bool:
        return (time.time() - self.updated_at) > settings.STREAMING_STALE_SECONDS


# ── StreamingService ────────────────────────────────────────────────


class StreamingService:
    """Central streaming service managing the Schwab WebSocket."""

    def __init__(self):
        self._stream = None
        self._started = False

        # Caches
        self._option_quotes: dict[str, QuoteSnapshot] = {}
        self._equity_quotes: dict[str, QuoteSnapshot] = {}
        self._account_events: list[dict] = []

        # Per-symbol asyncio Events for wake-on-update
        self._option_events: dict[str, asyncio.Event] = {}
        self._equity_events: dict[str, asyncio.Event] = {}
        self._account_event = asyncio.Event()

        # Connection tracking
        self._last_message_time: float = 0.0

    async def start(self, schwab_client):
        """Initialize and start the streaming connection."""
        from schwabdev import StreamAsync

        self._stream = StreamAsync(schwab_client)
        await self._stream.start(receiver=self._on_message)
        self._started = True
        logger.info("StreamingService: streaming started")

        # Subscribe to always-on symbols
        await self.subscribe_equity("SPY")
        await self.subscribe_equity("QQQ")
        await self.subscribe_equity("$VIX.X")

    async def stop(self):
        """Gracefully stop the streaming connection."""
        if self._stream and self._started:
            await self._stream.stop()
            self._started = False
            logger.info("StreamingService: streaming stopped")

    @property
    def is_active(self) -> bool:
        if self._stream is None:
            return False
        return self._stream.active

    # ── Subscription management ──────────────────────────────────

    async def subscribe_option(self, symbol: str):
        """Subscribe to real-time option quotes for a symbol."""
        if not self._stream:
            return
        if symbol not in self._option_quotes:
            self._option_quotes[symbol] = QuoteSnapshot(symbol=symbol)
            self._option_events[symbol] = asyncio.Event()
        req = self._stream.level_one_options(symbol, OPTION_FIELDS, command="ADD")
        await self._stream.send(req)
        logger.info(f"StreamingService: subscribed to option {symbol}")

    async def unsubscribe_option(self, symbol: str):
        """Unsubscribe from option quotes."""
        if not self._stream:
            return
        req = self._stream.level_one_options(symbol, OPTION_FIELDS, command="UNSUBS")
        await self._stream.send(req)
        self._option_quotes.pop(symbol, None)
        self._option_events.pop(symbol, None)
        logger.info(f"StreamingService: unsubscribed from option {symbol}")

    async def subscribe_equity(self, symbol: str):
        """Subscribe to real-time equity quotes."""
        if not self._stream:
            return
        if symbol not in self._equity_quotes:
            self._equity_quotes[symbol] = QuoteSnapshot(symbol=symbol)
            self._equity_events[symbol] = asyncio.Event()
        req = self._stream.level_one_equities(symbol, EQUITY_FIELDS, command="ADD")
        await self._stream.send(req)
        logger.info(f"StreamingService: subscribed to equity {symbol}")

    async def subscribe_account_activity(self):
        """Subscribe to account activity (order fills)."""
        if not self._stream:
            return
        req = self._stream.account_activity()
        await self._stream.send(req)
        logger.info("StreamingService: subscribed to account activity")

    # ── Data retrieval ───────────────────────────────────────────

    def get_option_quote(self, symbol: str) -> Optional[QuoteSnapshot]:
        """Get the latest cached option quote. Returns None if stale or not subscribed."""
        snap = self._option_quotes.get(symbol)
        if snap and not snap.is_stale:
            return snap
        return None

    def get_equity_quote(self, symbol: str) -> Optional[QuoteSnapshot]:
        """Get the latest cached equity quote. Returns None if stale or not subscribed."""
        snap = self._equity_quotes.get(symbol)
        if snap and not snap.is_stale:
            return snap
        return None

    def get_option_event(self, symbol: str) -> Optional[asyncio.Event]:
        """Get the asyncio Event for a specific option symbol (for await)."""
        return self._option_events.get(symbol)

    def pop_account_events(self) -> list[dict]:
        """Pop and return all pending account activity events."""
        events = self._account_events[:]
        self._account_events.clear()
        self._account_event.clear()
        return events

    # ── Message handler ──────────────────────────────────────────

    async def _on_message(self, message: str):
        """Receiver callback invoked by StreamAsync for every message."""
        self._last_message_time = time.time()

        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.warning(f"StreamingService: invalid JSON: {message[:200]}")
            return

        # Handle "response" messages (subscription confirmations)
        if "response" in data:
            for resp in data["response"]:
                svc = resp.get("service", "")
                cmd = resp.get("command", "")
                content = resp.get("content", {})
                code = content.get("code", -1)
                msg = content.get("msg", "")
                if code != 0:
                    logger.warning(
                        f"StreamingService: {svc} {cmd} error code={code} msg={msg}"
                    )
                else:
                    logger.debug(f"StreamingService: {svc} {cmd} OK")
            return

        # Handle "data" messages (actual streaming data)
        if "data" not in data:
            return

        for item in data["data"]:
            service = item.get("service", "")
            contents = item.get("content", [])

            if service == "LEVELONE_OPTIONS":
                self._process_option_quotes(contents)
            elif service == "LEVELONE_EQUITIES":
                self._process_equity_quotes(contents)
            elif service == "ACCT_ACTIVITY":
                self._process_account_activity(contents)

    def _process_option_quotes(self, contents: list):
        now = time.time()
        for entry in contents:
            symbol = entry.get("key", "")
            if symbol not in self._option_quotes:
                self._option_quotes[symbol] = QuoteSnapshot(symbol=symbol)

            snap = self._option_quotes[symbol]
            # Update only fields present in the delta update
            if "2" in entry:
                snap.bid = float(entry["2"])
            if "3" in entry:
                snap.ask = float(entry["3"])
            if "4" in entry:
                snap.last = float(entry["4"])
            if "5" in entry:
                snap.high = float(entry["5"])
            if "6" in entry:
                snap.low = float(entry["6"])
            if "7" in entry:
                snap.close = float(entry["7"])
            if "8" in entry:
                snap.volume = int(entry["8"])
            if "9" in entry:
                snap.open_interest = int(entry["9"])
            if "10" in entry:
                snap.iv = float(entry["10"])
            if "28" in entry:
                snap.delta = float(entry["28"])
            if "29" in entry:
                snap.gamma = float(entry["29"])
            if "30" in entry:
                snap.theta = float(entry["30"])
            if "31" in entry:
                snap.vega = float(entry["31"])
            snap.updated_at = now

            # Signal waiters
            event = self._option_events.get(symbol)
            if event:
                event.set()

    def _process_equity_quotes(self, contents: list):
        now = time.time()
        for entry in contents:
            symbol = entry.get("key", "")
            if symbol not in self._equity_quotes:
                self._equity_quotes[symbol] = QuoteSnapshot(symbol=symbol)

            snap = self._equity_quotes[symbol]
            if "1" in entry:
                snap.bid = float(entry["1"])
            if "2" in entry:
                snap.ask = float(entry["2"])
            if "3" in entry:
                snap.last = float(entry["3"])
            if "8" in entry:
                snap.volume = int(entry["8"])
            snap.updated_at = now

            event = self._equity_events.get(symbol)
            if event:
                event.set()

    def _process_account_activity(self, contents: list):
        for entry in contents:
            self._account_events.append(entry)
            self._account_event.set()
            logger.info(f"StreamingService: account activity: {entry}")
