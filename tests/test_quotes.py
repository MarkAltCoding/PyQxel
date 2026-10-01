"""Tests for the live quote WebSocket. The quote fetcher is replaced with fakes."""

import json
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from app.api.v1.endpoints import quotes
from app.data.fetcher import DataFetchError, SymbolNotFoundError
from app.main import app
from app.models.quote import MAX_SUBSCRIPTIONS, Quote

client = TestClient(app)

URL = "/api/v1/ws/quotes"


def _quote(symbol: str, price: float = 100.0) -> Quote:
    return Quote(
        symbol=symbol,
        price=price,
        previous_close=99.0,
        change=price - 99.0,
        change_percent=(price - 99.0) / 99.0,
        as_of=datetime(2026, 10, 1, 14, 30, tzinfo=timezone.utc),
        source="yfinance",
    )


def _serve_quotes(
    monkeypatch: pytest.MonkeyPatch, outcomes: dict[str, float | Exception]
) -> list[str]:
    """Answer quote fetches from ``outcomes``; return the symbols fetched, in order."""
    fetched: list[str] = []

    async def fake_fetch(symbol: str) -> Quote:
        fetched.append(symbol)
        outcome = outcomes[symbol]
        if isinstance(outcome, Exception):
            raise outcome
        return _quote(symbol, outcome)

    monkeypatch.setattr(quotes, "fetch_quote", fake_fetch)
    return fetched


def _receive_until(socket: WebSocketTestSession, type_: str) -> dict[str, Any]:
    """Return the next message of ``type_``, skipping others."""
    while True:
        message: dict[str, Any] = socket.receive_json()
        if message["type"] == type_:
            return message


def test_symbols_in_query_are_followed_and_quoted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Symbols given on connect are confirmed, normalized, and quoted at once."""
    _serve_quotes(monkeypatch, {"AAPL": 190.0, "MSFT": 410.0})

    with client.websocket_connect(f"{URL}?symbols=aapl,%20msft") as socket:
        assert socket.receive_json() == {
            "type": "subscriptions",
            "symbols": ["AAPL", "MSFT"],
        }
        received = {socket.receive_json()["quote"]["symbol"] for _ in range(2)}

    assert received == {"AAPL", "MSFT"}


def test_subscribe_and_unsubscribe_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    """Commands change the followed symbols, and new symbols are quoted straight away."""
    _serve_quotes(monkeypatch, {"AAPL": 190.0, "TSLA": 250.0})

    with client.websocket_connect(URL) as socket:
        socket.send_json({"action": "subscribe", "symbols": ["TSLA", "tsla"]})
        assert socket.receive_json() == {"type": "subscriptions", "symbols": ["TSLA"]}
        update = socket.receive_json()
        assert update["type"] == "quote"
        assert update["quote"]["symbol"] == "TSLA"
        assert update["quote"]["price"] == 250.0

        socket.send_json({"action": "subscribe", "symbols": ["AAPL"]})
        assert _receive_until(socket, "subscriptions")["symbols"] == ["TSLA", "AAPL"]
        assert _receive_until(socket, "quote")["quote"]["symbol"] == "AAPL"

        socket.send_json({"action": "unsubscribe", "symbols": ["TSLA"]})
        assert _receive_until(socket, "subscriptions")["symbols"] == ["AAPL"]


@pytest.mark.parametrize(
    "command",
    [
        "not json",
        '{"action": "follow", "symbols": ["AAPL"]}',
        '{"action": "subscribe", "symbols": []}',
        '{"action": "subscribe", "symbols": ["BAD$SYM"]}',
    ],
)
def test_invalid_commands_are_reported_without_closing(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    """A malformed command gets an error message, and the connection keeps working."""
    _serve_quotes(monkeypatch, {"AAPL": 190.0})

    with client.websocket_connect(URL) as socket:
        socket.send_text(command)
        error = socket.receive_json()
        assert error["type"] == "error"
        assert error["detail"].startswith("Invalid command")

        socket.send_json({"action": "subscribe", "symbols": ["AAPL"]})
        assert _receive_until(socket, "quote")["quote"]["symbol"] == "AAPL"


def test_invalid_query_symbols_are_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bad symbols on connect are reported, leaving the connection open with none followed."""
    _serve_quotes(monkeypatch, {"AAPL": 190.0})

    with client.websocket_connect(f"{URL}?symbols=AAPL,BAD$SYM") as socket:
        assert socket.receive_json()["type"] == "error"
        socket.send_json({"action": "subscribe", "symbols": ["AAPL"]})
        assert socket.receive_json() == {"type": "subscriptions", "symbols": ["AAPL"]}


def test_subscription_limit_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    """A subscription that would exceed the per-connection limit is rejected whole."""
    symbols = [f"S{i}" for i in range(MAX_SUBSCRIPTIONS)]
    _serve_quotes(monkeypatch, {symbol: 1.0 for symbol in symbols})

    with client.websocket_connect(URL) as socket:
        socket.send_json({"action": "subscribe", "symbols": symbols})
        assert _receive_until(socket, "subscriptions")["symbols"] == symbols
        socket.send_json({"action": "subscribe", "symbols": ["EXTRA"]})
        error = _receive_until(socket, "error")
        assert str(MAX_SUBSCRIPTIONS) in error["detail"]


def test_unknown_symbol_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A symbol no provider knows is reported and unsubscribed; the others carry on."""
    _serve_quotes(
        monkeypatch,
        {"AAPL": 190.0, "ZZZZ": SymbolNotFoundError("Unknown ticker symbol 'ZZZZ'.")},
    )

    with client.websocket_connect(f"{URL}?symbols=AAPL,ZZZZ") as socket:
        assert socket.receive_json()["symbols"] == ["AAPL", "ZZZZ"]
        messages = [socket.receive_json() for _ in range(3)]

    assert "quote" in {m["type"] for m in messages}
    assert {
        "type": "error",
        "detail": "Unknown ticker symbol 'ZZZZ'.",
        "symbol": "ZZZZ",
    } in messages
    assert {"type": "subscriptions", "symbols": ["AAPL"]} in messages


def test_invalid_interval_is_refused() -> None:
    """Polling faster than the minimum interval is refused at the handshake."""
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"{URL}?interval=1") as socket:
            socket.receive_json()


class _RecordingSocket:
    """Collects messages a session sends, standing in for the WebSocket."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


@pytest.mark.asyncio
async def test_unchanged_quotes_and_repeat_failures_are_sent_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Polls send a quote only when it changes, and report a failing symbol only once."""
    outcomes: dict[str, float | Exception] = {
        "AAPL": 190.0,
        "MSFT": DataFetchError("Could not fetch a quote for 'MSFT'."),
    }
    _serve_quotes(monkeypatch, outcomes)
    socket = _RecordingSocket()
    session = quotes._QuoteSession(socket, interval=15)  # type: ignore[arg-type]
    session.symbols = {"AAPL": None, "MSFT": None}

    await session.publish_once()
    await session.publish_once()
    outcomes["AAPL"] = 191.0
    outcomes["MSFT"] = 410.0
    await session.publish_once()

    sent = [json.loads(message) for message in socket.sent]
    assert [(m["type"], m.get("symbol") or m["quote"]["symbol"]) for m in sent] == [
        ("quote", "AAPL"),
        ("error", "MSFT"),
        ("quote", "AAPL"),
        ("quote", "MSFT"),
    ]
    assert "Retrying every 15 seconds" in sent[1]["detail"]
    assert [m["quote"]["price"] for m in sent if m["type"] == "quote"] == [
        190.0,
        191.0,
        410.0,
    ]
