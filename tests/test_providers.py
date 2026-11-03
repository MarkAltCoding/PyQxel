"""Tests for Tiingo and EODHD, Finnhub's paid candles, and how history prefers a full
answer to a free plan's partial one. Every provider is faked; no test reaches a service."""

from collections.abc import Callable, Iterator
from datetime import date, timedelta
from typing import Any

import httpx2
import pandas as pd
import pytest
from pydantic import SecretStr

from app.core.cache import configure_cache
from app.core.credentials import ProviderKeys, use_provider_keys
from app.data import fetcher
from app.data.fetcher import (
    HISTORY_NOTICE,
    DataFetchError,
    ProviderPlanError,
    SymbolNotFoundError,
    eodhd_symbol,
    fetch_price_history,
    fetch_quote,
    fetch_ticker_info,
)

TIINGO_KEY = "tiingo-user-key"
EODHD_KEY = "eodhd-user-key"

Handler = Callable[[httpx2.Request], httpx2.Response]

REAL_CLIENT = httpx2.AsyncClient
"""The real client, kept before any test fakes it, so a test can fake it more than once."""


@pytest.fixture(autouse=True)
def fresh(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No cache, nothing remembered, and yfinance off."""
    configure_cache(None)
    fetcher._recalled.clear()
    fetcher.clear_quote_cache()
    monkeypatch.setattr(fetcher, "yfinance_fallback_on", lambda: False)
    yield
    use_provider_keys(ProviderKeys())
    fetcher._recalled.clear()


def _serve(monkeypatch: pytest.MonkeyPatch, handler: Handler) -> list[httpx2.Request]:
    """Answer every request the fetcher sends with ``handler``; return the requests."""
    seen: list[httpx2.Request] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(
        "app.data.fetcher.httpx2.AsyncClient",
        lambda **kwargs: REAL_CLIENT(transport=httpx2.MockTransport(record)),
    )
    return seen


def _days(count: int, end: date | None = None) -> list[date]:
    last = end or date.today()
    return [day.date() for day in pd.bdate_range(end=last, periods=count)]


# Tiingo.


def _tiingo_daily(count: int) -> list[dict[str, Any]]:
    return [
        {
            "date": f"{day.isoformat()}T00:00:00.000Z",
            "close": 200.0 + i,
            "adjOpen": 99.0 + i,
            "adjHigh": 101.0 + i,
            "adjLow": 98.0 + i,
            "adjClose": 100.0 + i,
            "adjVolume": 1_000 + i,
        }
        for i, day in enumerate(_days(count))
    ]


@pytest.mark.asyncio
async def test_tiingo_serves_adjusted_daily_bars_with_the_key_in_a_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_provider_keys(ProviderKeys(tiingo=SecretStr(TIINGO_KEY)))
    seen = _serve(monkeypatch, lambda request: httpx2.Response(200, json=_tiingo_daily(300)))

    frame = await fetch_price_history("brk-b", period="1y", interval="1d")

    assert seen[0].url.path == "/tiingo/daily/brk-b/prices"
    assert seen[0].headers["Authorization"] == f"Token {TIINGO_KEY}"
    assert TIINGO_KEY not in str(seen[0].url)
    assert frame["Close"].iloc[-1] == 399.0, "the adjusted close, not the raw one"
    assert str(pd.DatetimeIndex(frame.index).tz) == "America/New_York"
    assert pd.DatetimeIndex(frame.index)[-1].date() == _days(1)[0]
    assert HISTORY_NOTICE not in frame.attrs


@pytest.mark.asyncio
async def test_tiingo_intraday_bars_note_their_iex_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    use_provider_keys(ProviderKeys(tiingo=SecretStr(TIINGO_KEY)))
    bars = [
        {
            "date": "2026-10-30T13:30:00.000Z",
            "open": 1,
            "high": 2,
            "low": 1,
            "close": 2,
            "volume": 5,
        },
        {
            "date": "2026-10-30T13:35:00.000Z",
            "open": 2,
            "high": 3,
            "low": 2,
            "close": 3,
            "volume": 7,
        },
    ]
    seen = _serve(monkeypatch, lambda request: httpx2.Response(200, json=bars))

    frame = await fetch_price_history("AAPL", period="5d", interval="5m")

    assert seen[0].url.path == "/iex/aapl/prices"
    assert seen[0].url.params["resampleFreq"] == "5min"
    assert pd.DatetimeIndex(frame.index)[0].hour == 9, "stamped in New York time"
    assert "IEX" in frame.attrs[HISTORY_NOTICE]


@pytest.mark.asyncio
async def test_tiingo_limits_and_refusals_are_remembered(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reached limit leaves Tiingo out without asking again; a plan refusal is remembered
    for that request, and an unknown ticker is not found."""
    use_provider_keys(ProviderKeys(tiingo=SecretStr(TIINGO_KEY)))
    answers = iter(
        [
            httpx2.Response(
                429, json={"detail": "Error: You have run over your hourly request allocation."}
            ),
        ]
    )
    seen = _serve(monkeypatch, lambda request: next(answers))
    for _ in range(2):
        with pytest.raises(DataFetchError, match="limit"):
            await fetch_price_history("AAPL", period="1y", interval="1d")
    assert len(seen) == 1

    fetcher._recalled.clear()
    seen = _serve(
        monkeypatch,
        lambda request: httpx2.Response(
            400, json={"detail": "Error: Free and Power plans are limited to the DOW 30."}
        ),
    )
    for _ in range(2):
        with pytest.raises(ProviderPlanError):
            await fetcher.tiingo_get("tiingo/fundamentals/pltr/daily", {}, TIINGO_KEY)
    assert len(seen) == 1

    _serve(
        monkeypatch,
        lambda request: httpx2.Response(404, json={"detail": "Error: Ticker not found"}),
    )
    with pytest.raises(SymbolNotFoundError):
        await fetch_price_history("NOPE", period="1y", interval="1d")


@pytest.mark.asyncio
async def test_tiingo_quote_and_profile_leave_out_withheld_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_provider_keys(ProviderKeys(tiingo=SecretStr(TIINGO_KEY)))
    withheld = "Field not available for free/evaluation"

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/iex/":
            return httpx2.Response(
                200, json=[{"ticker": "PLTR", "tngoLast": 180.5, "last": None, "prevClose": 178.0}]
            )
        if request.url.path == "/tiingo/daily/pltr":
            return httpx2.Response(
                200, json={"ticker": "PLTR", "name": "Palantir", "exchangeCode": "NASDAQ"}
            )
        return httpx2.Response(
            200,
            json=[{"ticker": "pltr", "name": "Palantir", "sector": withheld, "industry": withheld}],
        )

    _serve(monkeypatch, handler)

    quote = await fetch_quote("PLTR")
    info = await fetch_ticker_info("PLTR")

    assert (quote.source, quote.price, quote.previous_close) == ("tiingo", 180.5, 178.0)
    assert (info.source, info.name, info.sector, info.industry) == (
        "tiingo",
        "Palantir",
        None,
        None,
    )


# EODHD.


def test_eodhd_names_symbols_by_exchange() -> None:
    assert eodhd_symbol("AAPL") == "AAPL.US"
    assert eodhd_symbol("BRK-B") == "BRK-B.US"
    assert eodhd_symbol("^GSPC") == "GSPC.INDX"
    assert eodhd_symbol("SHEL.L") == "SHEL.LSE"
    assert eodhd_symbol("7203.T") == "7203.TSE"
    assert eodhd_symbol("EURUSD=X") == "EURUSD.FOREX"


def _eodhd_daily(days: list[date]) -> list[dict[str, Any]]:
    return [
        {
            "date": day.isoformat(),
            "open": 10.0,
            "high": 12.0,
            "low": 9.0,
            "close": 10.0,
            "adjusted_close": 5.0,
            "volume": 100,
        }
        for day in days
    ]


@pytest.mark.asyncio
async def test_eodhd_scales_bars_by_the_adjusted_close_and_keeps_its_key_out_of_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_provider_keys(ProviderKeys(eodhd=SecretStr(EODHD_KEY)))
    seen = _serve(monkeypatch, lambda request: httpx2.Response(200, json=_eodhd_daily(_days(200))))

    frame = await fetch_price_history("SHEL.L", period="6mo", interval="1d")

    assert seen[0].url.path == "/api/eod/SHEL.LSE"
    assert list(frame.iloc[-1][["Open", "High", "Low", "Close"]]) == [5.0, 6.0, 4.5, 5.0]
    assert str(pd.DatetimeIndex(frame.index).tz) == "Europe/London"

    _serve(monkeypatch, lambda request: httpx2.Response(500, text="oops"))
    fetcher.clear_quote_cache()
    with pytest.raises(DataFetchError) as failure:
        await fetch_quote("SHEL.L")
    assert EODHD_KEY not in str(failure.value)


@pytest.mark.asyncio
async def test_eodhd_free_year_is_partial_when_more_was_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_provider_keys(ProviderKeys(eodhd=SecretStr(EODHD_KEY)))
    year = [day for day in _days(300) if day >= date.today() - timedelta(days=366)]
    _serve(monkeypatch, lambda request: httpx2.Response(200, json=_eodhd_daily(year)))

    five_years = await fetch_price_history("^GSPC", period="5y", interval="1d")
    one_year = await fetch_price_history("^GSPC", period="1y", interval="1d")

    assert "free plan serves only the last year" in five_years.attrs[HISTORY_NOTICE]
    assert HISTORY_NOTICE not in one_year.attrs


@pytest.mark.asyncio
async def test_eodhd_refusals_and_limits_are_remembered(monkeypatch: pytest.MonkeyPatch) -> None:
    use_provider_keys(ProviderKeys(eodhd=SecretStr(EODHD_KEY)))
    seen = _serve(
        monkeypatch,
        lambda request: httpx2.Response(403, text="Only EOD data allowed for free users."),
    )
    for _ in range(2):
        with pytest.raises(ProviderPlanError):
            await fetch_price_history("AAPL", period="5d", interval="5m")
    assert len(seen) == 1, "the intraday refusal is remembered for the key"

    seen = _serve(
        monkeypatch,
        lambda request: httpx2.Response(402, text="You exceeded your daily API requests limit."),
    )
    for _ in range(2):
        with pytest.raises(DataFetchError, match="daily limit"):
            await fetch_price_history("AAPL", period="1y", interval="1d")
    assert len(seen) == 1


# Finnhub's paid candles, and preferring full history.


@pytest.mark.asyncio
async def test_finnhub_candles_serve_a_paid_plan_noting_they_leave_out_dividends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_provider_keys(ProviderKeys(finnhub=SecretStr("finnhub-key")))
    stamps = [int(pd.Timestamp(day).tz_localize("UTC").timestamp()) for day in _days(3)]
    candles = {
        "s": "ok",
        "t": stamps,
        "o": [1, 2, 3],
        "h": [2, 3, 4],
        "l": [1, 1, 2],
        "c": [2, 3, 4],
        "v": [10, 20, 30],
    }
    _serve(monkeypatch, lambda request: httpx2.Response(200, json=candles))

    frame = await fetch_price_history("AAPL", period="1mo", interval="1d")

    assert list(frame["Close"]) == [2.0, 3.0, 4.0]
    assert "not for dividends" in frame.attrs[HISTORY_NOTICE]


@pytest.mark.asyncio
async def test_a_partial_answer_is_kept_only_until_a_later_key_does_better(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EODHD's free year comes first in the order for indices, but FMP's full window wins;
    without a better answer, the free year is served with its note."""
    use_provider_keys(ProviderKeys(eodhd=SecretStr(EODHD_KEY), fmp=SecretStr("fmp-key")))
    year = [day for day in _days(300) if day >= date.today() - timedelta(days=366)]
    full = [
        {
            "date": day.isoformat(),
            "adjOpen": 1,
            "adjHigh": 1,
            "adjLow": 1,
            "adjClose": 1,
            "volume": 1,
        }
        for day in _days(1200)
    ]

    def handler(request: httpx2.Request) -> httpx2.Response:
        if "eodhd" in request.url.host:
            return httpx2.Response(200, json=_eodhd_daily(year))
        return httpx2.Response(200, json=full)

    _serve(monkeypatch, handler)
    frame = await fetch_price_history("^GSPC", period="5y", interval="1d")
    assert len(frame) == 1200 and HISTORY_NOTICE not in frame.attrs

    def fmp_refuses(request: httpx2.Request) -> httpx2.Response:
        if "eodhd" in request.url.host:
            return httpx2.Response(200, json=_eodhd_daily(year))
        return httpx2.Response(402, text="Premium Query Parameter")

    _serve(monkeypatch, fmp_refuses)
    frame = await fetch_price_history("^GSPC", period="2y", interval="1d")
    assert len(frame) == len(year) and HISTORY_NOTICE in frame.attrs


def test_settings_lists_providers_in_the_servers_order() -> None:
    """Settings' "Market data, by kind" names each kind's providers in the order the server
    tries them (web/src/lib/keyCoverage.ts mirrors PROVIDER_ORDER)."""
    import re
    from pathlib import Path

    from app.core.config import PROJECT_ROOT
    from app.data.fetcher import PROVIDER_ORDER, DataKind, SymbolKind

    source = Path(PROJECT_ROOT, "web/src/lib/keyCoverage.ts").read_text()
    features = re.split(r"\n  \{\n    id: '", source)[1:]
    shown = {
        block.split("'", 1)[0]: re.findall(r"provider: '([a-z_]+)'", block) for block in features
    }
    kinds: dict[str, tuple[DataKind, SymbolKind]] = {
        "history-us": ("history", "us"),
        "history-index": ("history", "index"),
        "history-international": ("history", "international"),
        "intraday": ("intraday", "us"),
        "quotes": ("quote", "us"),
        "profiles": ("info", "us"),
    }
    assert set(shown) == set(kinds)
    for feature, kind in kinds.items():
        assert tuple(shown[feature]) == PROVIDER_ORDER[kind], feature


@pytest.mark.asyncio
async def test_eodhd_profiles_fall_back_to_search_and_quotes_on_the_free_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fundamentals need a paid plan; the free plan's search names a listing, and an index,
    which search leaves out, is described by its quote."""
    use_provider_keys(ProviderKeys(eodhd=SecretStr(EODHD_KEY)))

    def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path.startswith("/api/fundamentals/"):
            return httpx2.Response(403, text="Only EOD data allowed for free users.")
        if path == "/api/search/SHEL":
            return httpx2.Response(
                200,
                json=[
                    {
                        "Code": "SHEL",
                        "Exchange": "LSE",
                        "Name": "Shell plc",
                        "Type": "Common Stock",
                        "Currency": "GBX",
                        "previousClose": 3921,
                    }
                ],
            )
        if path.startswith("/api/search/"):
            return httpx2.Response(200, json=[])
        return httpx2.Response(200, json={"close": 7500.0, "previousClose": 7540.0})

    _serve(monkeypatch, handler)

    shell = await fetch_ticker_info("SHEL.L")
    index = await fetch_ticker_info("^GSPC")

    assert (shell.name, shell.currency, shell.exchange, shell.quote_type) == (
        "Shell plc",
        "GBX",
        "LSE",
        "EQUITY",
    )
    assert (index.price, index.quote_type, index.source) == (7500.0, "INDEX", "eodhd")
