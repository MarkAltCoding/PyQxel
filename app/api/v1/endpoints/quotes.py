"""Live quote WebSocket.

A client follows up to :data:`~app.models.quote.MAX_SUBSCRIPTIONS` symbols and receives
a quote message whenever one of them is new or has changed. Symbols can be given as
the ``symbols`` query parameter on connect, then changed with JSON commands::

    {"action": "subscribe", "symbols": ["AAPL", "MSFT"]}
    {"action": "unsubscribe", "symbols": ["MSFT"]}

The server answers each command with a ``subscriptions`` message listing what the
connection now follows, sends ``quote`` messages as prices move, and reports problems
with ``error`` messages without closing the connection. Quotes are polled from the
data providers every ``interval`` seconds, so updates are near-real-time rather than
tick-by-tick.
"""

import asyncio
import logging
from contextlib import suppress
from typing import Annotated

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError

from app.data.fetcher import DataFetchError, SymbolNotFoundError, fetch_quote
from app.models.quote import (
    MAX_SUBSCRIPTIONS,
    Quote,
    QuoteCommand,
    QuoteStreamError,
    QuoteUpdate,
    SubscriptionState,
)

logger = logging.getLogger(__name__)

router = APIRouter()

MIN_INTERVAL_SECONDS: float = 5.0
"""Shortest polling interval a client may ask for, to stay within provider rate limits."""

MAX_INTERVAL_SECONDS: float = 300.0
DEFAULT_INTERVAL_SECONDS: float = 15.0


def _quote_changed(previous: Quote | None, current: Quote) -> bool:
    """Whether ``current`` differs from the last quote sent in any market field."""
    if previous is None:
        return True
    fields = ("price", "previous_close", "day_high", "day_low", "volume")
    return any(getattr(previous, name) != getattr(current, name) for name in fields)


def _first_error(exc: ValidationError) -> str:
    """Describe the first problem in a rejected command."""
    error = exc.errors()[0]
    location = ".".join(str(part) for part in error["loc"])
    return f"Invalid command: {location + ': ' if location else ''}{error['msg']}."


class _QuoteSession:
    """The subscriptions of one connection and the quotes already sent on it."""

    def __init__(self, websocket: WebSocket, interval: float) -> None:
        self.websocket = websocket
        self.interval = interval
        self.symbols: dict[str, None] = {}
        """Followed symbols, in subscription order."""
        self.last_sent: dict[str, Quote] = {}
        self.failing: set[str] = set()
        """Symbols whose last fetch failed, so the failure is reported once, not every poll."""
        self.wake = asyncio.Event()

    async def send(self, message: BaseModel) -> None:
        """Send ``message`` as a JSON text frame."""
        await self.websocket.send_text(message.model_dump_json())

    async def send_subscriptions(self) -> None:
        """Tell the client which symbols the connection now follows."""
        await self.send(SubscriptionState(symbols=list(self.symbols)))

    async def apply(self, command: QuoteCommand) -> None:
        """Subscribe to or unsubscribe from the command's symbols, then confirm."""
        if command.action == "subscribe":
            new = [
                symbol for symbol in dict.fromkeys(command.symbols) if symbol not in self.symbols
            ]
            if len(self.symbols) + len(new) > MAX_SUBSCRIPTIONS:
                await self.send(
                    QuoteStreamError(
                        detail=f"A connection may follow at most {MAX_SUBSCRIPTIONS} symbols; "
                        f"it follows {len(self.symbols)}."
                    )
                )
                return
            self.symbols.update(dict.fromkeys(new))
            self.wake.set()
        else:
            for symbol in command.symbols:
                self.drop(symbol)
        await self.send_subscriptions()

    def drop(self, symbol: str) -> None:
        """Stop following ``symbol`` and forget what was sent for it."""
        self.symbols.pop(symbol, None)
        self.last_sent.pop(symbol, None)
        self.failing.discard(symbol)

    async def handle(self, raw: str) -> None:
        """Validate and apply one client message, reporting invalid ones."""
        try:
            command = QuoteCommand.model_validate_json(raw)
        except ValidationError as exc:
            await self.send(QuoteStreamError(detail=_first_error(exc)))
            return
        await self.apply(command)

    async def receive_commands(self) -> None:
        """Apply client commands until the client disconnects."""
        while True:
            message = await self.websocket.receive()
            if message["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(message.get("code", 1000))
            raw = message.get("text")
            if raw is None:
                raw = (message.get("bytes") or b"").decode("utf-8", errors="replace")
            await self.handle(raw)

    async def publish_once(self) -> None:
        """Fetch every followed symbol and send the quotes that are new or changed."""
        symbols = list(self.symbols)
        results = await asyncio.gather(
            *(fetch_quote(symbol) for symbol in symbols), return_exceptions=True
        )
        unknown = False
        for symbol, result in zip(symbols, results):
            if symbol not in self.symbols:
                continue  # Unsubscribed while the fetch was in flight.
            if isinstance(result, Quote):
                self.failing.discard(symbol)
                if _quote_changed(self.last_sent.get(symbol), result):
                    self.last_sent[symbol] = result
                    await self.send(QuoteUpdate(quote=result))
            elif isinstance(result, SymbolNotFoundError):
                self.drop(symbol)
                unknown = True
                await self.send(QuoteStreamError(detail=str(result), symbol=symbol))
            elif isinstance(result, DataFetchError):
                if symbol not in self.failing:
                    self.failing.add(symbol)
                    await self.send(
                        QuoteStreamError(
                            detail=f"{result} Retrying every {self.interval:g} seconds.",
                            symbol=symbol,
                        )
                    )
            elif isinstance(result, BaseException):
                raise result
        if unknown:
            await self.send_subscriptions()

    async def publish_quotes(self) -> None:
        """Poll every ``interval`` seconds, or at once when new symbols are added."""
        while True:
            self.wake.clear()
            if self.symbols:
                await self.publish_once()
            with suppress(TimeoutError):
                await asyncio.wait_for(self.wake.wait(), timeout=self.interval)


@router.websocket("/quotes")
async def quote_stream(
    websocket: WebSocket,
    symbols: Annotated[
        str | None,
        Query(description="Comma-separated symbols to follow from the start, e.g. AAPL,MSFT."),
    ] = None,
    interval: Annotated[
        float,
        Query(
            ge=MIN_INTERVAL_SECONDS,
            le=MAX_INTERVAL_SECONDS,
            description="Seconds between quote polls.",
        ),
    ] = DEFAULT_INTERVAL_SECONDS,
) -> None:
    """Stream quotes for the symbols the client follows until it disconnects."""
    await websocket.accept()
    session = _QuoteSession(websocket, interval)
    if symbols:
        try:
            initial = QuoteCommand(action="subscribe", symbols=symbols.split(","))
        except ValidationError as exc:
            await session.send(QuoteStreamError(detail=_first_error(exc)))
        else:
            await session.apply(initial)

    try:
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(session.receive_commands())
            tasks.create_task(session.publish_quotes())
    except* WebSocketDisconnect:
        logger.debug("Quote stream client disconnected.")
