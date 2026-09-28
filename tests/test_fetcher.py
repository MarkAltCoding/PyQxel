"""Tests for how the market data fetcher classifies provider failures.

Provider calls are replaced with fakes so no test touches the network.
"""

import httpx
import pandas as pd
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.data import fetcher
from app.data.fetcher import DataFetchError, SymbolNotFoundError
from app.models.stock import TickerInfo

pytestmark = pytest.mark.asyncio


def _use_fmp_key(monkeypatch: pytest.MonkeyPatch, key: str | None) -> None:
    """Configure the FMP fallback key seen by the fetcher."""
    settings = Settings(financial_data_api_key=SecretStr(key) if key else None)
    monkeypatch.setattr(fetcher, "get_settings", lambda: settings)


def _yf_info_raising(error: Exception) -> object:
    """Build a fake ``_yfinance_info`` that raises ``error``."""

    def fake(symbol: str) -> TickerInfo:
        raise error

    return fake


def _fmp_info_raising(error: Exception) -> object:
    """Build a fake ``_fmp_info`` that raises ``error``."""

    async def fake(symbol: str, api_key: str, client: httpx.AsyncClient) -> TickerInfo:
        raise error

    return fake


async def test_info_unknown_symbol_without_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """yfinance having no quote, with no fallback configured, means not found."""
    _use_fmp_key(monkeypatch, None)
    monkeypatch.setattr(fetcher, "_yfinance_info", _yf_info_raising(SymbolNotFoundError("none")))

    with pytest.raises(SymbolNotFoundError):
        await fetcher.fetch_ticker_info("ZZZZ")


async def test_info_transport_failure_without_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """A yfinance exception, with no fallback configured, is a general fetch error."""
    _use_fmp_key(monkeypatch, None)
    monkeypatch.setattr(fetcher, "_yfinance_info", _yf_info_raising(ConnectionError("down")))

    with pytest.raises(DataFetchError) as caught:
        await fetcher.fetch_ticker_info("AAPL")
    assert not isinstance(caught.value, SymbolNotFoundError)


async def test_info_unknown_to_both_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the fallback also has no profile, the symbol is not found."""
    _use_fmp_key(monkeypatch, "key")
    monkeypatch.setattr(fetcher, "_yfinance_info", _yf_info_raising(SymbolNotFoundError("none")))
    monkeypatch.setattr(fetcher, "_fmp_info", _fmp_info_raising(SymbolNotFoundError("none")))

    with pytest.raises(SymbolNotFoundError):
        await fetcher.fetch_ticker_info("ZZZZ")


async def test_info_fallback_transport_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the fallback cannot answer, not-found cannot be confirmed and a fetch error is raised."""
    _use_fmp_key(monkeypatch, "key")
    monkeypatch.setattr(fetcher, "_yfinance_info", _yf_info_raising(SymbolNotFoundError("none")))
    request = httpx.Request("GET", fetcher.FMP_PROFILE_URL)
    transport_error = httpx.ConnectError("down", request=request)
    monkeypatch.setattr(fetcher, "_fmp_info", _fmp_info_raising(transport_error))

    with pytest.raises(DataFetchError) as caught:
        await fetcher.fetch_ticker_info("ZZZZ")
    assert not isinstance(caught.value, SymbolNotFoundError)


def _yf_history_returning(frame: pd.DataFrame) -> object:
    """Build a fake ``_yfinance_history`` that returns ``frame``."""
    return lambda symbol, period, interval: frame


def _info_check(monkeypatch: pytest.MonkeyPatch, error: Exception | None) -> list[str]:
    """Replace the existence check with a fake that raises ``error``; return its calls."""
    calls: list[str] = []

    async def fake(symbol: str) -> TickerInfo:
        calls.append(symbol)
        if error is not None:
            raise error
        return TickerInfo(symbol=symbol, source="test")

    monkeypatch.setattr(fetcher, "fetch_ticker_info", fake)
    return calls


EMPTY_YF_FRAME = pd.DataFrame(columns=[*fetcher.OHLCV_COLUMNS, "Adj Close"])


async def test_history_empty_for_unknown_symbol_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty frame for a symbol no provider recognizes means not found."""
    monkeypatch.setattr(fetcher, "_yfinance_history", _yf_history_returning(EMPTY_YF_FRAME))
    _info_check(monkeypatch, SymbolNotFoundError("none"))

    with pytest.raises(SymbolNotFoundError):
        await fetcher.fetch_price_history("ZZZZ")


async def test_history_empty_for_real_symbol_returns_empty_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty frame for a known symbol is returned as an empty OHLCV frame."""
    monkeypatch.setattr(fetcher, "_yfinance_history", _yf_history_returning(EMPTY_YF_FRAME))
    calls = _info_check(monkeypatch, None)

    history = await fetcher.fetch_price_history("aapl")

    assert calls == ["AAPL"]
    assert history.empty
    assert list(history.columns) == fetcher.OHLCV_COLUMNS


async def test_history_empty_when_existence_unknown_is_fetch_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the existence check itself fails, the result is a general fetch error."""
    monkeypatch.setattr(fetcher, "_yfinance_history", _yf_history_returning(EMPTY_YF_FRAME))
    _info_check(monkeypatch, DataFetchError("down"))

    with pytest.raises(DataFetchError) as caught:
        await fetcher.fetch_price_history("AAPL")
    assert not isinstance(caught.value, SymbolNotFoundError)


async def test_history_without_closes_returns_empty_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rows that all lack a close leave no usable bars, without checking existence."""
    frame = pd.DataFrame(
        {"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [float("nan")], "Volume": [0]},
        index=pd.DatetimeIndex(["2026-09-25"]),
    )
    monkeypatch.setattr(fetcher, "_yfinance_history", _yf_history_returning(frame))
    calls = _info_check(monkeypatch, None)

    history = await fetcher.fetch_price_history("AAPL")

    assert history.empty
    assert calls == []
