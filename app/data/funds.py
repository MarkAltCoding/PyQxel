"""What an ETF or mutual fund holds and costs, and the figures of the companies it holds.

Three sources are combined, every one the user can reach:

* **Alpha Vantage's** ``ETF_PROFILE``, which its free plan answers (tested November 5,
  2026) for one of its 25 requests a day, reused for a day: the expense ratio, yield,
  turnover, inception, leverage, sector weights and current holdings with tickers.
* **The SEC's N-PORT filings** (:mod:`app.data.fund_filings`), public and needing no key:
  every holding of a mutual fund or ETF as of the latest published quarter end, the mix of
  stocks, bonds and cash, and a bond fund's coupon, maturity and issuers.
* **The companies' own figures**, to weight into the fund's (valuation, margins, growth,
  size, sectors): from the screener the user may see, which costs nothing, then Finnhub's
  free basic financials on the user's key for up to :data:`FINNHUB_LOOKUPS` of the largest
  holdings it lacks, each reused for a day.

Tiingo's fund metrics need its mutual fund add-on and Finnhub's fund profiles a paid plan;
FMP's could not be tested.
"""

import asyncio
import logging
import time
from datetime import date, datetime, timezone
from typing import Any

from app.core.credentials import current_provider_keys
from app.data.fetcher import (
    DataFetchError,
    Provider,
    ProviderLimitError,
    ProviderNotConfiguredError,
    SymbolNotFoundError,
    _name_words,
    alpha_vantage_get,
    alpha_vantage_key,
    alpha_vantage_number,
    finnhub_symbol,
)
from app.data.fund_filings import FundFiling, fetch_fund_filing
from app.data.sec_edgar import edgar_client
from app.data.universe import fetch_listings
from app.db.screener import CompanyFigures, company_figures
from app.db.session import get_sessionmaker
from app.models.screener import ScreenedStock
from app.models.stock import FundHolding, FundProfile, FundWeight
from app.stats.fund_stats import HARMONIC_FIELDS, MEAN_FIELDS, HeldCompany, holdings_stats

logger = logging.getLogger(__name__)

FUND_PROVIDERS: tuple[Provider, ...] = ("alpha_vantage",)
"""The providers on the user's keys that can answer a fund's holdings and costs, cheapest
first, beside the SEC's public filings; Settings' list of what each key covers mirrors it
(``web/src/lib/keyCoverage.ts``)."""

TOP_HOLDINGS: int = 10
"""Largest holdings a profile lists."""

FINNHUB_LOOKUPS: int = 25
"""Most holdings whose figures are asked of Finnhub, largest first, when the screener the
user may see lacks them: the 25 largest hold about half of an S&P 500 fund."""

FINNHUB_FIGURES_TTL_SECONDS: float = 24 * 60 * 60
"""How long a company's figures from Finnhub are reused."""

LISTINGS_TTL_SECONDS: float = 24 * 60 * 60
"""How long the SEC's list of listed companies, read when the database has none, is
reused."""

_listings: tuple[float, list[tuple[str, str]]] | None = None
"""(time read, (symbol, name) of every listed company), when the database has none."""


def _date(value: object) -> date | None:
    """An ISO date, or ``None``."""
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _title(name: str) -> str:
    """A sector Alpha Vantage writes in capitals, as "Information Technology"."""
    return " ".join(word if word in ("&",) else word.capitalize() for word in name.split())


def parse_etf_profile(symbol: str, payload: dict[str, Any]) -> FundProfile:
    """Read Alpha Vantage's ``ETF_PROFILE`` answer, with every holding it lists.

    Raises:
        SymbolNotFoundError: If it describes no fund.
    """
    sectors = [
        FundWeight(name=_title(str(row.get("sector") or "")), weight=weight)
        for row in payload.get("sectors") or []
        if isinstance(row, dict)
        and row.get("sector")
        and (weight := alpha_vantage_number(row.get("weight"))) is not None
    ]
    holdings = [
        FundHolding(
            symbol=str(row.get("symbol")) if row.get("symbol") not in (None, "", "n/a") else None,
            name=str(row.get("description") or row.get("symbol") or ""),
            weight=weight,
        )
        for row in payload.get("holdings") or []
        if isinstance(row, dict)
        and (row.get("description") or row.get("symbol"))
        and (weight := alpha_vantage_number(row.get("weight"))) is not None
    ]
    net_assets = alpha_vantage_number(payload.get("net_assets"))
    if net_assets is None and not sectors and not holdings:
        raise SymbolNotFoundError(f"Alpha Vantage has no fund profile for {symbol!r}.")
    leveraged = str(payload.get("leveraged") or "").upper()
    sectors.sort(key=lambda row: -row.weight)
    holdings.sort(key=lambda row: -row.weight)
    return FundProfile(
        symbol=symbol,
        net_assets=net_assets if net_assets is not None and net_assets >= 0 else None,
        expense_ratio=alpha_vantage_number(payload.get("net_expense_ratio")),
        dividend_yield=alpha_vantage_number(payload.get("dividend_yield")),
        portfolio_turnover=alpha_vantage_number(payload.get("portfolio_turnover")),
        inception_date=_date(payload.get("inception_date")),
        leveraged=True if leveraged == "YES" else False if leveraged == "NO" else None,
        sectors=sectors,
        holdings=holdings,
        holdings_count=len(holdings) or None,
        holdings_source="alpha_vantage" if holdings else None,
        source="alpha_vantage",
    )


async def _alpha_vantage_profile(symbol: str, notes: list[str]) -> FundProfile | None:
    """The fund's profile from Alpha Vantage, or ``None``, saying why in ``notes``."""
    api_key = alpha_vantage_key()
    if api_key is None:
        notes.append(
            "Add an Alpha Vantage key in Settings for the fund's expense ratio, yield and "
            "turnover: its free plan serves them."
        )
        return None
    try:
        payload = await alpha_vantage_get("ETF_PROFILE", {"symbol": symbol}, api_key)
        return parse_etf_profile(symbol, payload)
    except SymbolNotFoundError:
        notes.append(f"Alpha Vantage has no costs for {symbol}, as for most mutual funds.")
    except ProviderLimitError:
        notes.append(
            "Your Alpha Vantage key has used its 25 free requests for the day, so the fund's "
            "expense ratio, yield and turnover are left out until it resets."
        )
    except DataFetchError as exc:
        notes.append(f"The fund's costs from Alpha Vantage are left out: {exc}")
    except (TypeError, ValueError) as exc:
        logger.warning("Alpha Vantage's fund profile of %s was unreadable: %s", symbol, exc)
        notes.append("Alpha Vantage's answer could not be read, so the fund's costs are left out.")
    return None


async def _filing(symbol: str, notes: list[str]) -> FundFiling | None:
    """The fund's latest SEC filing, or ``None``, saying why in ``notes`` when it failed."""
    try:
        return await fetch_fund_filing(symbol)
    except SymbolNotFoundError:
        return None
    except DataFetchError as exc:
        notes.append(f"The fund's holdings from the SEC are left out: {exc}")
        return None


async def _listed_names(known: CompanyFigures) -> list[tuple[str, str]]:
    """(symbol, name) of listed companies: the database's copy of the SEC's list, else the
    list read from the SEC itself, kept for a day."""
    global _listings
    if known.listings:
        return known.listings
    if _listings is None or time.monotonic() - _listings[0] > LISTINGS_TTL_SECONDS:
        try:
            async with edgar_client() as client:
                read = await fetch_listings(client)
        except Exception as exc:  # The names only help match holdings to tickers.
            logger.info("The SEC's list of companies could not be read: %s", exc)
            return [(stock.symbol, stock.name) for stock in known.stocks]
        _listings = (time.monotonic(), [(listing.symbol, listing.name) for listing in read])
    return _listings[1]


def _key(name: str) -> str:
    """A company name reduced for matching: "APPLE INC" and "Apple Inc." alike."""
    return " ".join(_name_words(name))


def _figures(stock: ScreenedStock) -> dict[str, float | None]:
    """A screened stock's figures, by the names :func:`holdings_stats` reads."""
    return {
        field: getattr(stock, field) for field in (*MEAN_FIELDS, *HARMONIC_FIELDS, "market_cap")
    }


async def _finnhub_figures(symbols: list[str]) -> dict[str, dict[str, float | None]]:
    """Figures of ``symbols`` from Finnhub's free basic financials on the user's key, each
    reused for a day; those it fails on are left out."""
    if not symbols or current_provider_keys().finnhub is None:
        return {}
    from app.data.finnhub import cached_get  # It imports this module's neighbors.
    from app.data.screener_metrics import parse_screen_metrics

    async def one(symbol: str) -> tuple[str, dict[str, float | None] | None]:
        try:
            payload = await cached_get(
                "stock/metric",
                {"symbol": finnhub_symbol(symbol), "metric": "all"},
                None,
                ttl=FINNHUB_FIGURES_TTL_SECONDS,
            )
        except DataFetchError as exc:
            logger.info("No Finnhub figures for %s: %s", symbol, exc)
            return symbol, None
        metrics = parse_screen_metrics(payload)
        return symbol, None if metrics is None else metrics.model_dump()

    found = await asyncio.gather(*(one(symbol) for symbol in symbols))
    return {symbol: figures for symbol, figures in found if figures is not None}


async def _weighted(
    profile: FundProfile, stocks: list[tuple[str | None, str, float]], lookups: int
) -> FundProfile:
    """``profile`` with figures weighted from ``stocks``, the fund's stock holdings as
    (ticker or ``None``, name, weight)."""
    try:
        async with get_sessionmaker()() as session:
            known = await company_figures(session)
    except Exception as exc:  # A database outage leaves the figures to Finnhub.
        logger.warning("The screener's figures could not be read: %s", exc)
        known = CompanyFigures(stocks=[], refreshed_at=None, own=False, listings=[])
    screened = {stock.symbol: stock for stock in known.stocks}
    by_name: dict[str, str] = {}
    for symbol, name in [
        *(await _listed_names(known)),
        *((s.symbol, s.name) for s in known.stocks),
    ]:
        by_name.setdefault(_key(name), symbol)
    tickers = [(symbol or by_name.get(_key(name)), weight) for symbol, name, weight in stocks]
    missing = sorted(
        {symbol for symbol, _ in tickers if symbol and symbol not in screened},
        key=lambda symbol: -max(w for s, w in tickers if s == symbol),
    )[:lookups]
    fetched = await _finnhub_figures(missing)
    companies = [
        HeldCompany(
            weight=weight,
            figures=(
                _figures(screened[symbol])
                if symbol in screened
                else fetched.get(symbol) if symbol else None
            ),
            sector=screened[symbol].sector if symbol in screened else None,
        )
        for symbol, weight in tickers
    ]
    stats = holdings_stats(companies)
    if stats is None:
        return profile.model_copy(
            update={
                "notes": [
                    *profile.notes,
                    "The figures of the companies it holds could not be looked up: "
                    + (
                        "add a Finnhub key in Settings, or turn on the nightly screener refresh."
                        if current_provider_keys().finnhub is None
                        else "none were found in your screener or on Finnhub."
                    ),
                ]
            }
        )
    sources = ([] if not any(s in screened for s, _ in tickers) else ["your screener"]) + (
        ["Finnhub"] if fetched else []
    )
    as_of = (
        datetime.now(timezone.utc).date()
        if fetched or known.refreshed_at is None
        else known.refreshed_at.date()
    )
    update: dict[str, Any] = {"stats": stats, "stats_as_of": as_of, "stats_sources": sources}
    if not profile.sectors and stats.sectors:
        update["sectors"] = [FundWeight(name=n, weight=w) for n, w in stats.sectors.items()]
    return profile.model_copy(update=update)


async def fetch_fund_profile(symbol: str, lookups: int = FINNHUB_LOOKUPS) -> FundProfile:
    """What the ETF or mutual fund ``symbol`` holds and costs, and the figures weighted
    from the companies it holds; ``lookups`` caps the companies asked of Finnhub.

    Raises:
        ProviderNotConfiguredError: If nothing is found and an Alpha Vantage key would help.
        SymbolNotFoundError: If no source describes ``symbol`` as a fund.
    """
    symbol = symbol.strip().upper()
    notes: list[str] = []
    av, filing = await asyncio.gather(_alpha_vantage_profile(symbol, notes), _filing(symbol, notes))
    if av is None and filing is None:
        if alpha_vantage_key() is None:
            raise ProviderNotConfiguredError(
                f"The SEC has no holdings filing for {symbol}. Alpha Vantage, whose free plan "
                "serves ETFs' holdings and costs, may: add an Alpha Vantage key in Settings."
            )
        raise SymbolNotFoundError(f"No holdings or costs could be found for {symbol}.")
    profile = av or FundProfile(symbol=symbol, source="sec")
    stocks: list[tuple[str | None, str, float]]
    if av is not None and av.holdings:
        stocks = [(h.symbol, h.name, h.weight) for h in av.holdings if h.symbol]
    else:
        stocks = [(None, h.name, h.weight) for h in (filing.stocks if filing else [])]
    if filing is not None:
        update: dict[str, Any] = {
            "net_assets": profile.net_assets or filing.net_assets,
            "asset_mix": [FundWeight(name=n, weight=w) for n, w in filing.asset_mix.items()],
            "bonds": filing.bonds,
            "source": "alpha_vantage, sec" if av is not None else "sec",
        }
        if not profile.holdings:
            update |= {
                "holdings": [
                    FundHolding(name=h.name, weight=h.weight, asset_class=h.asset_class)
                    for h in filing.top_holdings
                ],
                "holdings_count": filing.holdings_count,
                "holdings_as_of": filing.as_of,
                "holdings_source": "sec",
            }
        profile = profile.model_copy(update=update)
    profile = profile.model_copy(update={"notes": notes})
    if stocks:
        profile = await _weighted(profile, stocks, lookups)
    elif filing is not None and filing.bonds is not None:
        profile = profile.model_copy(
            update={
                "notes": [
                    *profile.notes,
                    "It holds bonds, not companies, so a company's figures such as P/E do not "
                    "apply: its bonds' coupon, maturity and issuers are shown instead.",
                ]
            }
        )
    return profile.model_copy(update={"holdings": profile.holdings[:TOP_HOLDINGS]})
