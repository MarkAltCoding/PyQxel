"""A company's lobbying, government contracts, patents and SEC filings from Finnhub, on the
user's own key.

Finnhub's free plan covers each for US companies, as checked against a free key in
October 2026: lobbying disclosures filed under the Lobbying Disclosure Act, federal
awards from USAspending.gov, patents and applications published by the USPTO, and the
list of the company's SEC filings. They change at most daily, so answers are reused for
a day. Each part is fetched on its own, and one Finnhub cannot supply is left out with a
notice rather than failing the rest.
"""

import asyncio
from datetime import date, timedelta
from typing import Any

import httpx2

from app.data.fetcher import (
    DataFetchError,
    FinnhubRateLimitError,
    ProviderNotConfiguredError,
    ProviderPlanError,
    finnhub_key,
    finnhub_symbol,
)
from app.data.finnhub import cached_get
from app.models.alternative import (
    AlternativeRecords,
    GovernmentAward,
    LobbyingFiling,
    Patent,
    SecFiling,
)

ALTERNATIVE_TTL_SECONDS: float = 24 * 60 * 60
"""How long each answer is reused."""
LOBBYING_YEARS: int = 2
"""Calendar years of lobbying disclosures fetched, this one included; they are filed
quarterly, weeks after the quarter, so this year's total is partial."""
CONTRACT_DAYS: int = 365
"""Days of federal awards fetched."""
PATENT_DAYS: int = 365
"""Days of patent publications fetched."""
FILING_DAYS: int = 90
"""Days of SEC filings fetched."""
PART_NAMES: tuple[str, ...] = ("lobbying", "government contracts", "patents", "SEC filings")


def _day(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _text(value: object) -> str | None:
    text = " ".join(str(value).split()) if isinstance(value, str) else ""
    return text or None


def _rows(payload: Any, key: str | None = "data") -> list[dict[str, Any]]:
    rows = payload.get(key) if key and isinstance(payload, dict) else payload
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


async def _window(
    path: str, symbol: str, start: date, end: date, client: httpx2.AsyncClient | None
) -> Any:
    params = {"symbol": finnhub_symbol(symbol), "from": start.isoformat(), "to": end.isoformat()}
    return await cached_get(path, params, client, ttl=ALTERNATIVE_TTL_SECONDS)


async def fetch_lobbying(
    symbol: str, today: date, client: httpx2.AsyncClient | None = None
) -> list[LobbyingFiling]:
    """Lobbying disclosures of last year and this one, newest first."""
    start = date(today.year - LOBBYING_YEARS + 1, 1, 1)
    payload = await _window("stock/lobbying", symbol, start, today, client)
    filings: list[LobbyingFiling] = []
    for row in _rows(payload):
        year = row.get("year")
        if not isinstance(year, int) or year < today.year - LOBBYING_YEARS + 1:
            continue
        expenses, income = _number(row.get("expenses")), _number(row.get("income"))
        filings.append(
            LobbyingFiling(
                year=year,
                period=_text(row.get("period")),
                in_house=expenses is not None,
                amount=expenses if expenses is not None else income,
                url=_text(row.get("documentUrl")),
            )
        )
    return sorted(filings, key=lambda f: (f.year, f.period or ""), reverse=True)


async def fetch_contracts(
    symbol: str, today: date, client: httpx2.AsyncClient | None = None
) -> list[GovernmentAward]:
    """Federal awards of the last year, newest first."""
    payload = await _window(
        "stock/usa-spending", symbol, today - timedelta(days=CONTRACT_DAYS), today, client
    )
    awards: list[GovernmentAward] = []
    for row in _rows(payload):
        day, value = _day(row.get("actionDate")), _number(row.get("totalValue"))
        if day is None or value is None:
            continue
        awards.append(
            GovernmentAward(
                action_date=day,
                recipient=_text(row.get("recipientName")) or "Unknown",
                agency=_text(row.get("awardingAgencyName")),
                sub_agency=_text(row.get("awardingSubAgencyName")),
                value=value,
                description=_text(row.get("awardDescription")),
                performance_end=_day(row.get("performanceEndDate")),
                url=_text(row.get("permalink")),
            )
        )
    return sorted(awards, key=lambda award: award.action_date, reverse=True)


async def fetch_patents(
    symbol: str, today: date, client: httpx2.AsyncClient | None = None
) -> list[Patent]:
    """Patents and applications published in the last year, newest first."""
    payload = await _window(
        "stock/uspto-patent", symbol, today - timedelta(days=PATENT_DAYS), today, client
    )
    patents: list[Patent] = []
    for row in _rows(payload):
        description = _text(row.get("description"))
        if description is None:
            continue
        patents.append(
            Patent(
                filing_date=_day(row.get("filingDate")),
                publication_date=_day(row.get("publicationDate")),
                number=_text(row.get("patentNumber")),
                description=description,
                status=_text(row.get("filingStatus")),
                kind=_text(row.get("patentType")),
            )
        )
    return sorted(patents, key=lambda patent: patent.publication_date or date.min, reverse=True)


async def fetch_sec_filings(
    symbol: str, today: date, client: httpx2.AsyncClient | None = None
) -> list[SecFiling]:
    """The company's SEC filings of the last 90 days, newest first."""
    payload = await _window(
        "stock/filings", symbol, today - timedelta(days=FILING_DAYS), today, client
    )
    filings: list[SecFiling] = []
    for row in _rows(payload, key=None):
        form, filed = _text(row.get("form")), _day(row.get("filedDate"))
        if form is None or filed is None:
            continue
        filings.append(
            SecFiling(
                form=form,
                filed=filed,
                url=_text(row.get("filingUrl")) or _text(row.get("reportUrl")),
            )
        )
    return sorted(filings, key=lambda filing: filing.filed, reverse=True)


def _reason(name: str, error: BaseException) -> str:
    if isinstance(error, ProviderPlanError):
        return f"your Finnhub plan does not cover {name}"
    if isinstance(error, FinnhubRateLimitError):
        return f"{name} hit Finnhub's rate limit"
    return f"{name} could not be fetched"


async def fetch_alternative(
    symbol: str, today: date, client: httpx2.AsyncClient | None = None
) -> AlternativeRecords:
    """``symbol``'s lobbying, government contracts, patents and SEC filings.

    Raises:
        ProviderNotConfiguredError: If the user has no Finnhub key, or it is rejected.
    """
    symbol = symbol.strip().upper()
    if finnhub_key() is None:
        raise ProviderNotConfiguredError(
            "Lobbying, government contract, patent and filing data come from Finnhub on "
            "your own key: add it with PUT /api/v1/me/credentials."
        )
    results = await asyncio.gather(
        fetch_lobbying(symbol, today, client),
        fetch_contracts(symbol, today, client),
        fetch_patents(symbol, today, client),
        fetch_sec_filings(symbol, today, client),
        return_exceptions=True,
    )
    parts: dict[str, Any] = {}
    missing: list[str] = []
    for key, name, result in zip(
        ("lobbying", "contracts", "patents", "filings"), PART_NAMES, results
    ):
        if isinstance(result, ProviderNotConfiguredError):
            raise result
        if isinstance(result, DataFetchError):
            missing.append(_reason(name, result))
        elif isinstance(result, BaseException):
            raise result
        else:
            parts[key] = result
    return AlternativeRecords(symbol=symbol, missing=missing, **parts)
