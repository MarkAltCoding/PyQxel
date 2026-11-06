"""What an ETF or mutual fund holds and costs: its net assets, expense ratio, yield,
turnover, sectors and largest holdings.

Alpha Vantage's ``ETF_PROFILE`` is the one free source: its free plan answered it on
November 5, 2026, for one of the plan's 25 requests a day, and the answer is reused for a
day (see :data:`app.data.fetcher.ALPHA_VANTAGE_TTL_SECONDS`). Tiingo's fund metrics need
its mutual fund add-on, and Finnhub's ETF profile a paid plan; FMP's could not be tested.
"""

import logging
from datetime import date
from typing import Any

from app.data.fetcher import (
    DataFetchError,
    Provider,
    ProviderNotConfiguredError,
    SymbolNotFoundError,
    alpha_vantage_get,
    alpha_vantage_key,
    alpha_vantage_number,
)
from app.models.stock import FundHolding, FundProfile, FundWeight

logger = logging.getLogger(__name__)

FUND_PROVIDERS: tuple[Provider, ...] = ("alpha_vantage",)
"""The providers that can answer a fund's holdings and costs, cheapest first; Settings'
list of what each key covers mirrors it (``web/src/lib/keyCoverage.ts``)."""

TOP_HOLDINGS: int = 10
"""Largest holdings a profile lists."""


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
    """Read Alpha Vantage's ``ETF_PROFILE`` answer.

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
        holdings=holdings[:TOP_HOLDINGS],
        holdings_count=len(holdings) or None,
        source="alpha_vantage",
    )


async def fetch_fund_profile(symbol: str) -> FundProfile:
    """The profile of the fund ``symbol``, from Alpha Vantage on the user's key.

    Raises:
        ProviderNotConfiguredError: If the user has no Alpha Vantage key.
        SymbolNotFoundError: If Alpha Vantage has no profile for ``symbol``, as for a stock.
        DataFetchError: If Alpha Vantage fails or the key's daily limit is reached.
    """
    symbol = symbol.strip().upper()
    api_key = alpha_vantage_key()
    if api_key is None:
        raise ProviderNotConfiguredError(
            "A fund's holdings and costs come from Alpha Vantage, whose free plan serves "
            "them: add an Alpha Vantage key in Settings."
        )
    payload = await alpha_vantage_get("ETF_PROFILE", {"symbol": symbol}, api_key)
    try:
        return parse_etf_profile(symbol, payload)
    except DataFetchError:
        raise
    except (TypeError, ValueError) as exc:
        logger.warning("Alpha Vantage's fund profile of %s was unreadable: %s", symbol, exc)
        raise DataFetchError(f"Alpha Vantage's fund profile of {symbol} was unreadable.") from exc
