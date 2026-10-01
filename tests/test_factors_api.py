"""Tests for the factor regression route.

Price history and factor downloads are replaced with fakes, so no test touches the network.
"""

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints import factors as factors_route
from app.data.factors import FactorDataError
from app.data.fetcher import SymbolNotFoundError
from app.main import app
from app.models.factors import MODEL_FACTORS, RISK_FREE

client = TestClient(app)

FACTOR_END = pd.Timestamp("2026-08-31")


def _factor_table(days: int) -> pd.DataFrame:
    """Every factor and RF on the ``days`` business days ending at ``FACTOR_END``."""
    rng = np.random.default_rng(0)
    index = pd.bdate_range(end=FACTOR_END, periods=days)
    columns = ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "Mom"]
    frame = pd.DataFrame(rng.normal(0, 0.01, size=(days, 6)), index=index, columns=columns)
    frame["RF"] = 0.0001
    return frame


def _price_history(table: pd.DataFrame, extra_days: int = 0) -> pd.DataFrame:
    """OHLCV bars, stamped at midnight New York time, moving 1.1x the market plus noise.

    ``extra_days`` more bars follow the factor data, as when it is a month behind.
    """
    rng = np.random.default_rng(1)
    returns = 1.1 * table["Mkt-RF"] + table["RF"] + rng.normal(0, 0.005, size=len(table))
    index = pd.bdate_range(end=FACTOR_END, periods=len(table) + 1)
    index = index.append(pd.bdate_range(FACTOR_END + pd.offsets.BDay(1), periods=extra_days))
    closes = 100.0 * np.concatenate(([1.0], np.cumprod(1.0 + returns.to_numpy())))
    closes = np.concatenate((closes, closes[-1] * 1.001 ** np.arange(1, extra_days + 1)))
    frame = pd.DataFrame({column: closes for column in ["Open", "High", "Low", "Close"]})
    frame["Volume"] = 1_000_000
    frame.index = index.tz_localize("America/New_York")
    return frame


def _serve(
    monkeypatch: pytest.MonkeyPatch,
    prices: pd.DataFrame | Exception,
    table: pd.DataFrame | Exception,
) -> list[tuple[str, str]]:
    """Fake the price and factor fetches; return the (period, model) requested."""
    calls: list[tuple[str, str]] = []
    state = {"period": ""}

    async def fake_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
        assert interval == "1d"
        state["period"] = period
        if isinstance(prices, Exception):
            raise prices
        return prices

    async def fake_factors(model: str) -> pd.DataFrame:
        calls.append((state["period"], model))
        if isinstance(table, Exception):
            raise table
        return table.loc[:, [*MODEL_FACTORS[model], RISK_FREE]]  # type: ignore[index]

    monkeypatch.setattr(factors_route, "fetch_price_history", fake_history)
    monkeypatch.setattr(factors_route, "fetch_factors", fake_factors)
    return calls


def test_defaults_to_three_factors_over_five_years(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no options, the stock is regressed on Fama-French three factors over 5y."""
    table = _factor_table(1_200)
    calls = _serve(monkeypatch, _price_history(table), table)

    response = client.get("/api/v1/stocks/spy/factors")

    assert response.status_code == 200
    body = response.json()
    assert calls == [("5y", "ff3")]
    assert body["symbol"] == "SPY"
    assert body["model"] == "ff3"
    fit = body["fit"]
    assert [exposure["factor"] for exposure in fit["exposures"]] == ["Mkt-RF", "SMB", "HML"]
    market = fit["exposures"][0]
    assert market["estimate"] == pytest.approx(1.1, abs=0.05)
    assert set(market) == {
        "factor",
        "estimate",
        "std_error",
        "t_stat",
        "p_value",
        "variance_share",
    }
    assert fit["observations"] == 1_200
    assert fit["end"] == "2026-08-31"
    assert body["factor_data_end"] == "2026-08-31"
    assert "Kenneth R. French" in body["source"]


@pytest.mark.parametrize(
    ("model", "factors"),
    [
        ("carhart4", ["Mkt-RF", "SMB", "HML", "Mom"]),
        ("ff5", ["Mkt-RF", "SMB", "HML", "RMW", "CMA"]),
    ],
)
def test_other_models(monkeypatch: pytest.MonkeyPatch, model: str, factors: list[str]) -> None:
    """Each model reports its own factors in order."""
    table = _factor_table(600)
    _serve(monkeypatch, _price_history(table), table)

    response = client.get("/api/v1/stocks/AAPL/factors", params={"model": model, "period": "2y"})

    assert response.status_code == 200
    assert [e["factor"] for e in response.json()["fit"]["exposures"]] == factors


def test_notice_counts_days_after_the_factor_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """Recent days past the published factors are left out and the notice says how many."""
    table = _factor_table(600)
    _serve(monkeypatch, _price_history(table, extra_days=22), table)

    body = client.get("/api/v1/stocks/AAPL/factors").json()

    assert body["fit"]["end"] == "2026-08-31"
    assert body["fit"]["observations"] == 600
    assert body["notice"].endswith(
        "currently end 2026-08-31, so the last 22 trading days of AAPL returns are not included."
    )


def test_unknown_symbol_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """A symbol no provider knows is a 404."""
    _serve(monkeypatch, SymbolNotFoundError("Unknown ticker symbol 'ZZZZ'."), _factor_table(300))

    response = client.get("/api/v1/stocks/ZZZZ/factors")

    assert response.status_code == 404


def test_factor_library_failure_is_502(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the factor library cannot be reached, the error names it."""
    table = _factor_table(300)
    message = "Could not download the Fama-French ff3 factors from Ken French's data library"
    _serve(monkeypatch, _price_history(table), FactorDataError(message))

    response = client.get("/api/v1/stocks/AAPL/factors")

    assert response.status_code == 502
    assert response.json() == {"detail": message}


def test_too_little_overlap_is_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """A recent listing with few days in the factor data is a 422 that explains itself."""
    table = _factor_table(300)
    _serve(monkeypatch, _price_history(table.iloc[-80:]), table)

    response = client.get("/api/v1/stocks/NEW/factors", params={"period": "1y"})

    assert response.status_code == 422
    assert "factor data is published about a month late" in response.json()["detail"]


@pytest.mark.parametrize(
    "params",
    [{"model": "ff4"}, {"period": "6mo"}, {"period": "max"}],
)
def test_invalid_options_are_rejected(params: dict[str, str]) -> None:
    """Unknown models and unsupported periods fail validation."""
    assert client.get("/api/v1/stocks/AAPL/factors", params=params).status_code == 422
