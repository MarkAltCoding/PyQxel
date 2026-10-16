"""Tests for Alpha Vantage: errors and caching, the market data it stands in for, the ticker
info and financial statements it fills in.

Alpha Vantage is reached through a mock transport, so no test touches the network.
"""

from collections.abc import Callable, Iterator
from datetime import date, timedelta
from typing import Any

import httpx2
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.api.v1.endpoints.stocks import history_coverage
from app.core.credentials import ProviderKeys, use_provider_keys
from app.data import alpha_vantage, exchange_rates, fetcher
from app.data.alpha_vantage import (
    BALANCE,
    CASH_FLOW,
    INCOME,
    fetch_financials_filled,
    fill_financials,
    parse_statements,
)
from app.data.fetcher import (
    DataFetchError,
    ProviderNotConfiguredError,
    ProviderPlanError,
    SymbolNotFoundError,
    alpha_vantage_get,
)
from app.data.fundamentals import FinancialsNotFoundError
from app.models.fundamentals import Financials
from tests.financials import PERIOD_END, sample_financials

pytestmark = pytest.mark.asyncio

KEY = "av-user-key"

Answer = Callable[[dict[str, str]], object]


@pytest.fixture(autouse=True)
def alpha_vantage_only(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Act as a user with only an Alpha Vantage key, the yfinance fallback off."""
    settings = Settings(yfinance_fallback=False)
    monkeypatch.setattr(fetcher, "get_settings", lambda: settings)
    monkeypatch.setattr(fetcher, "ALPHA_VANTAGE_SPACING_SECONDS", 0.0)
    fetcher.clear_alpha_vantage_memory()
    exchange_rates.clear_memory()
    use_provider_keys(ProviderKeys(alpha_vantage=SecretStr(KEY)))
    yield
    use_provider_keys(ProviderKeys())


def _serve(monkeypatch: pytest.MonkeyPatch, answer: Answer) -> list[dict[str, str]]:
    """Answer Alpha Vantage and FMP requests with ``answer(params)``; return their params.

    FMP requests carry ``path`` in their params.
    """
    requests: list[dict[str, str]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        params = dict(request.url.params)
        if "financialmodelingprep" in request.url.host:
            params["path"] = request.url.path.removeprefix("/stable/")
        requests.append(params)
        result = answer(params)
        return result if isinstance(result, httpx2.Response) else httpx2.Response(200, json=result)

    transport = httpx2.MockTransport(handler)
    real_client = httpx2.AsyncClient

    def client(**kwargs: Any) -> httpx2.AsyncClient:
        return real_client(transport=transport)

    monkeypatch.setattr("app.data.fetcher.httpx2.AsyncClient", client)
    return requests


OVERVIEW = {"Symbol": "ACME", "Name": "Acme Corp", "CIK": "123456", "Currency": "USD",
            "Exchange": "NYSE", "Sector": "TECHNOLOGY", "Industry": "SOFTWARE",
            "MarketCapitalization": "5000000000"}  # fmt: skip


# Errors and caching.


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({"Error Message": "Invalid API call."}, SymbolNotFoundError),
        ({"Information": "The **demo** API key is for demo purposes only. "
          "Please claim your free API key... the apikey is invalid."}, ProviderNotConfiguredError),
        ({"Information": "Our standard API rate limit is 25 requests per day. Please "
          "subscribe to any of the premium plans."}, DataFetchError),
        ({"Information": "Thank you for using Alpha Vantage! This is a premium endpoint."},
         ProviderPlanError),
    ],
)  # fmt: skip
async def test_errors_in_the_body_are_classified(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, str], error: type[Exception]
) -> None:
    """Alpha Vantage answers 200 and names the problem; a rate limit is not a plan error."""
    _serve(monkeypatch, lambda params: payload)

    with pytest.raises(error) as raised:
        await alpha_vantage_get("TIME_SERIES_DAILY_ADJUSTED", {"symbol": "ACME"}, KEY)

    assert type(raised.value) is error


async def test_a_premium_refusal_of_a_free_function_means_a_bad_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Alpha Vantage answers an invalid key as if a free function were premium."""
    _serve(
        monkeypatch,
        lambda params: {"Information": "Thank you for using Alpha Vantage! This is a "
                        "premium endpoint. You may subscribe to any of the premium plans."},
    )  # fmt: skip

    with pytest.raises(ProviderNotConfiguredError, match="rejected your API key"):
        await alpha_vantage_get("GLOBAL_QUOTE", {"symbol": "IBM"}, KEY)
    # The overview is premium, so its refusal says nothing about the key.
    with pytest.raises(ProviderPlanError):
        await alpha_vantage_get("OVERVIEW", {"symbol": "IBM"}, KEY)
    with pytest.raises(ProviderPlanError):
        await alpha_vantage_get("TIME_SERIES_DAILY_ADJUSTED", {"symbol": "IBM"}, KEY)


async def test_answers_and_unknown_symbols_are_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    """With 25 requests a day, neither an answer nor a miss is asked for twice."""
    requests = _serve(
        monkeypatch,
        lambda params: OVERVIEW if params["symbol"] == "ACME" else {"Error Message": "x"},
    )

    for _ in range(2):
        assert (await alpha_vantage_get("OVERVIEW", {"symbol": "ACME"}, KEY))["Name"]
        with pytest.raises(SymbolNotFoundError):
            await alpha_vantage_get("OVERVIEW", {"symbol": "NOPE"}, KEY)

    assert len(requests) == 2
    assert requests[0]["apikey"] == KEY


async def test_a_premium_refusal_is_remembered(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _serve(monkeypatch, lambda params: {"Information": "This is a premium endpoint."})

    for symbol in ("ACME", "OTHER"):
        with pytest.raises(ProviderPlanError):
            await alpha_vantage_get("TIME_SERIES_DAILY_ADJUSTED", {"symbol": symbol}, KEY)

    assert len(requests) == 1


async def test_requests_are_spaced_and_a_burst_refusal_is_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Free keys refuse more than a request a second; requests wait their turn."""
    monkeypatch.setattr(fetcher, "ALPHA_VANTAGE_SPACING_SECONDS", 1.1)
    waits: list[float] = []

    async def no_wait(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(fetcher.asyncio, "sleep", no_wait)
    answers: list[object] = [
        OVERVIEW,
        {"Information": "Burst pattern detected. Please consider spreading out your free "
                        "API requests more sparingly (1 request per second)."},
        OVERVIEW | {"Symbol": "OTHER"},
    ]  # fmt: skip
    requests = _serve(monkeypatch, lambda params: answers.pop(0))

    await alpha_vantage_get("OVERVIEW", {"symbol": "ACME"}, KEY)
    other = await alpha_vantage_get("OVERVIEW", {"symbol": "OTHER"}, KEY)

    assert other["Symbol"] == "OTHER"
    assert len(requests) == 3
    # The clock stands still while sleeping is faked, so the turns queue up.
    assert waits == pytest.approx([1.1, 2.2], abs=0.05)


# Market data it stands in for.


async def test_alpha_vantage_alone_serves_info_quotes_and_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily = {
        "2026-10-14": {"1. open": "10", "2. high": "12", "3. low": "9", "4. close": "11",
                       "5. adjusted close": "5.5", "6. volume": "1000"},
        "2026-10-15": {"1. open": "11", "2. high": "13", "3. low": "10", "4. close": "12",
                       "5. adjusted close": "12", "6. volume": "2000"},
    }  # fmt: skip

    def answer(params: dict[str, str]) -> object:
        return {
            "OVERVIEW": OVERVIEW,
            "GLOBAL_QUOTE": {"Global Quote": {"05. price": "12.0", "08. previous close": "11.0",
                                              "03. high": "13", "04. low": "10",
                                              "06. volume": "2000"}},
            "TIME_SERIES_DAILY_ADJUSTED": {"Time Series (Daily)": daily},
        }[params["function"]]  # fmt: skip

    _serve(monkeypatch, answer)

    info = await fetcher.fetch_ticker_info("acme")
    quote = await fetcher.fetch_quote("ACME")
    history = await fetcher.fetch_price_history("ACME", period="1mo", interval="1d")

    assert (info.source, info.name, info.sector, info.market_cap) == (
        "alpha_vantage",
        "Acme Corp",
        "Technology",
        5e9,
    )
    assert (quote.source, quote.price, quote.change) == ("alpha_vantage", 12.0, 1.0)
    # The split-halved bar is scaled by its adjusted close over its close.
    assert history["Close"].tolist() == [5.5, 12.0]
    assert history["Open"].iloc[0] == pytest.approx(5.0)
    assert str(history.index.tz) == "America/New_York"


FREE_DAILY = {
    "2026-10-14": {"1. open": "10", "2. high": "12", "3. low": "9", "4. close": "11",
                   "5. volume": "1000"},
    "2026-10-15": {"1. open": "11", "2. high": "13", "3. low": "10", "4. close": "12",
                   "5. volume": "2000"},
}  # fmt: skip
PREMIUM = {"Information": "Thank you for using Alpha Vantage! This is a premium endpoint."}


async def test_a_free_key_gets_recent_unadjusted_bars_with_a_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def answer(params: dict[str, str]) -> object:
        if params["function"] == "TIME_SERIES_DAILY_ADJUSTED":
            return PREMIUM
        assert (params["function"], params["outputsize"]) == ("TIME_SERIES_DAILY", "compact")
        return {"Time Series (Daily)": FREE_DAILY}

    _serve(monkeypatch, answer)

    history = await fetcher.fetch_price_history("ACME", period="1y", interval="1d")
    coverage, notice = history_coverage("ACME", "1y", "1d", history)

    assert history["Close"].tolist() == [11.0, 12.0]
    assert history["Volume"].tolist() == [1000.0, 2000.0]
    assert coverage == "partial"
    assert notice is not None and "free plan" in notice and "not adjusted" in notice
    # The notice survives the cache.
    again = await fetcher.fetch_price_history("ACME", period="1y", interval="1d")
    assert again.attrs == history.attrs


async def test_intraday_bars_need_a_paid_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Free keys get no intraday series at all, so no request is spent on a fallback."""
    requests = _serve(monkeypatch, lambda params: PREMIUM)

    with pytest.raises(ProviderPlanError):
        await fetcher.fetch_price_history("ACME", period="1d", interval="1m")

    assert len(requests) == 1


async def test_a_free_key_gets_info_from_symbol_search_and_a_quote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def answer(params: dict[str, str]) -> object:
        return {
            "OVERVIEW": PREMIUM,
            "SYMBOL_SEARCH": {"bestMatches": [
                {"1. symbol": "ACMEX", "2. name": "Acme Extra", "8. currency": "USD"},
                {"1. symbol": "ACME", "2. name": "Acme Corp", "8. currency": "USD"},
            ]},
            "GLOBAL_QUOTE": {"Global Quote": {"05. price": "12.5"}},
        }[params["function"]]  # fmt: skip

    requests = _serve(monkeypatch, answer)

    info = await fetcher.fetch_ticker_info("ACME")
    await fetcher.fetch_ticker_info("ACME")

    assert (info.source, info.name, info.currency, info.price) == (
        "alpha_vantage",
        "Acme Corp",
        "USD",
        12.5,
    )
    assert info.sector is None and info.market_cap is None
    # The premium refusal is remembered, and the rest is cached.
    assert [params["function"] for params in requests] == [
        "OVERVIEW",
        "SYMBOL_SEARCH",
        "GLOBAL_QUOTE",
    ]


async def test_with_yfinance_on_it_answers_instead_of_the_free_bars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """yfinance has the full adjusted history, so it beats 100 unadjusted days."""
    settings = Settings(yfinance_fallback=True)
    monkeypatch.setattr(fetcher, "get_settings", lambda: settings)
    requests = _serve(monkeypatch, lambda params: PREMIUM)
    full = fetcher._alpha_vantage_frame(
        {day: bar | {"5. adjusted close": bar["4. close"]} for day, bar in FREE_DAILY.items()},
        "America/New_York",
        True,
    )
    monkeypatch.setattr(fetcher, "_yfinance_history", lambda symbol, period, interval: full)

    history = await fetcher.fetch_price_history("ACME", period="1y", interval="1d")

    assert fetcher.HISTORY_NOTICE not in history.attrs
    assert [params["function"] for params in requests] == ["TIME_SERIES_DAILY_ADJUSTED"]


async def test_alpha_vantage_answers_when_the_fmp_plan_does_not_cover_a_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_provider_keys(ProviderKeys(fmp=SecretStr("fmp-key"), alpha_vantage=SecretStr(KEY)))

    def answer(params: dict[str, str]) -> object:
        if "path" in params:
            return httpx2.Response(402, json={})
        return {"Global Quote": {"05. price": "50", "08. previous close": "40"}}

    requests = _serve(monkeypatch, answer)

    quote = await fetcher.fetch_quote("ACME")

    assert quote.source == "alpha_vantage"
    assert [params.get("path", params.get("function")) for params in requests][-1] == (
        "GLOBAL_QUOTE"
    )


async def test_exchange_rates_come_from_alpha_vantage(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(
        monkeypatch,
        lambda params: {"Realtime Currency Exchange Rate": {"5. Exchange Rate": "0.031"}},
    )

    assert await exchange_rates.fetch_exchange_rate("TWD", "USD") == 0.031


# Ticker info it fills in.


async def test_fields_fmp_leaves_empty_are_filled(monkeypatch: pytest.MonkeyPatch) -> None:
    use_provider_keys(ProviderKeys(fmp=SecretStr("fmp-key"), alpha_vantage=SecretStr(KEY)))
    profile = [{"companyName": "Acme", "currency": "USD", "exchange": "NYSE",
                "sector": "", "industry": None, "marketCap": 4e9, "price": 20.0}]  # fmt: skip
    _serve(monkeypatch, lambda params: profile if "path" in params else OVERVIEW)

    info = await fetcher.fetch_ticker_info("ACME")

    assert (info.source, info.name, info.market_cap) == ("fmp", "Acme", 4e9)
    assert (info.sector, info.industry) == ("Technology", "Software")
    assert info.filled_fields == ["industry", "sector"]


async def test_complete_info_costs_no_alpha_vantage_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_provider_keys(ProviderKeys(fmp=SecretStr("fmp-key"), alpha_vantage=SecretStr(KEY)))
    profile = [{"companyName": "Acme", "currency": "USD", "exchange": "NYSE",
                "sector": "Technology", "industry": "Software", "marketCap": 4e9}]  # fmt: skip
    requests = _serve(monkeypatch, lambda params: profile)

    info = await fetcher.fetch_ticker_info("ACME")

    assert info.filled_fields == []
    assert all("path" in params for params in requests)


# Financial statements.


def _quarter_ends(latest: date, count: int) -> list[date]:
    """``count`` quarter ends, latest first, 91 days apart."""
    return [latest - timedelta(days=91 * index) for index in range(count)]


def _reports(ends: list[date], **fields: Callable[[int], float]) -> list[dict[str, str]]:
    return [
        {"fiscalDateEnding": end.isoformat(), "reportedCurrency": "USD"}
        | {name: str(value(index)) for name, value in fields.items()}
        for index, end in enumerate(ends)
    ]


def _statements(latest: date) -> dict[str, dict[str, Any]]:
    """Eight quarters and two years of each statement, ending at ``latest``.

    Quarterly revenue is 110 in the latest year and 100 the year before.
    """
    quarters = _quarter_ends(latest, 8)
    years = [quarters[0], quarters[4]]
    return {
        INCOME: {
            "quarterlyReports": _reports(
                quarters,
                totalRevenue=lambda i: 110 if i < 4 else 100,
                operatingIncome=lambda i: 20,
                netIncome=lambda i: 10,
                grossProfit=lambda i: "None",  # type: ignore[arg-type,return-value]
            ),
            "annualReports": _reports(years, totalRevenue=lambda i: 440 if i == 0 else 400),
        },
        CASH_FLOW: {
            "quarterlyReports": _reports(
                quarters,
                operatingCashflow=lambda i: 30,
                capitalExpenditures=lambda i: -5,
                depreciationDepletionAndAmortization=lambda i: 4,
            ),
            "annualReports": [],
        },
        BALANCE: {
            "quarterlyReports": _reports(
                quarters,
                cashAndCashEquivalentsAtCarryingValue=lambda i: 70,
                shortLongTermDebtTotal=lambda i: 200 - i,
                totalShareholderEquity=lambda i: 500,
                commonStockSharesOutstanding=lambda i: 1000,
            ),
            "annualReports": [],
        },
    }


async def test_statements_give_ttm_totals_growth_and_derived_items() -> None:
    latest = date(2026, 6, 30)

    statements = parse_statements(_statements(latest))

    assert statements is not None
    revenue = statements.flows["revenue"]
    assert (revenue.ttm, revenue.ttm_end) == (440.0, latest)
    assert revenue.ttm_growth == pytest.approx(0.1)
    assert [year.value for year in revenue.fiscal_years] == [400.0, 440.0]
    assert "gross_profit" not in statements.flows
    # Capital expenditure is kept positive, so FCF is operating cash flow less it.
    assert statements.flows["capital_expenditure"].ttm == 20.0
    assert statements.flows["free_cash_flow"].ttm == 100.0
    assert statements.flows["ebitda"].ttm == 96.0
    debt = statements.balances["total_debt"]
    assert (debt.value, debt.as_of, debt.year_ago) == (200.0, latest, 196.0)


async def test_gaps_are_filled_only_from_the_same_period_and_currency() -> None:
    sec = sample_financials(total_debt=None, gross_profit=None, ebitda=None)
    aligned = parse_statements(_statements(PERIOD_END))
    older = parse_statements(_statements(PERIOD_END - timedelta(days=91)))
    assert aligned is not None and older is not None

    filled = fill_financials(sec, aligned)

    assert filled.filled_items == ["ebitda", "total_debt"]
    assert filled.total_debt is not None and filled.total_debt.value == 200.0
    assert filled.revenue == sec.revenue  # Items the filings have are kept.
    assert filled.gross_profit is None  # Alpha Vantage has none either.
    assert fill_financials(sec, older).filled_items == []
    assert fill_financials(sec.model_copy(update={"currency": "EUR"}), aligned) == sec.model_copy(
        update={"currency": "EUR"}
    )


def _sec_returning(monkeypatch: pytest.MonkeyPatch, result: Financials | Exception) -> None:
    async def fake(symbol: str) -> Financials:
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(alpha_vantage, "fetch_financials", fake)


async def test_statements_stand_in_when_edgar_has_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _sec_returning(monkeypatch, FinancialsNotFoundError("none"))
    statements = _statements(date(2026, 6, 30))
    _serve(monkeypatch, lambda params: (statements | {"OVERVIEW": OVERVIEW})[params["function"]])

    financials = await fetch_financials_filled("ACME")

    assert (financials.source, financials.cik, financials.entity_name) == (
        "alpha_vantage",
        123456,
        "Acme Corp",
    )
    assert financials.accounting_standard is None
    assert financials.latest_period_end == date(2026, 6, 30)
    assert financials.net_income is not None and financials.net_income.ttm == 40.0


async def test_edgars_error_stands_when_alpha_vantage_has_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _sec_returning(monkeypatch, FinancialsNotFoundError("none on EDGAR"))
    _serve(monkeypatch, lambda params: {"Error Message": "Invalid API call."})

    with pytest.raises(FinancialsNotFoundError, match="none on EDGAR"):
        await fetch_financials_filled("ACME")


async def test_only_the_statements_covering_gaps_are_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _sec_returning(monkeypatch, sample_financials(total_debt=None))
    statements = _statements(PERIOD_END)
    requests = _serve(monkeypatch, lambda params: statements[params["function"]])

    financials = await fetch_financials_filled("ACME")

    assert [params["function"] for params in requests] == [BALANCE]
    assert financials.filled_items == ["total_debt"]


async def test_without_a_key_edgar_is_the_only_source(monkeypatch: pytest.MonkeyPatch) -> None:
    use_provider_keys(ProviderKeys())
    _sec_returning(monkeypatch, sample_financials(total_debt=None))
    requests = _serve(monkeypatch, lambda params: {})

    financials = await fetch_financials_filled("ACME")

    assert financials.total_debt is None and requests == []
