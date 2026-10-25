"""Financial statements as reported to the SEC, relayed by Finnhub, for when EDGAR cannot
be read.

Finnhub's as-reported financials, which its free plan covers for US companies, list each
10-K and 10-Q with the XBRL concepts and values its statements tag. They are the same
figures EDGAR's company facts hold, so they are rearranged into that document's form and
read by :func:`app.data.fundamentals.normalize`, giving the same TTM figures, growth and
fiscal-year history. A 10-Q's income and cash flow figures run from the start of the
fiscal year, as the TTM calculation expects.

This makes fundamentals work when the server has no ``SEC_USER_AGENT`` or EDGAR is down.
Finnhub's relay sometimes leaves a balance sheet item out of the latest filings, so that
item can be from an earlier balance sheet; its ``as_of`` says which.
"""

import asyncio
from datetime import date
from typing import Any

import httpx2

from app.data.finnhub import cached_get
from app.data.fetcher import finnhub_symbol
from app.data.fundamentals import FinancialsNotFoundError, normalize
from app.models.fundamentals import Financials

AS_REPORTED_TTL_SECONDS: float = 24 * 60 * 60
"""How long Finnhub's as-reported filings are reused; they change only when one is filed."""
TAXONOMIES: frozenset[str] = frozenset({"us-gaap", "ifrs-full", "dei"})
"""Concept prefixes read; companies' own extension concepts are not."""
SECTIONS_WITH_PERIODS: frozenset[str] = frozenset({"ic", "cf"})
"""Statements whose figures cover a period; the balance sheet's are at a date."""


def _day(value: object) -> str | None:
    """The ISO date at the start of Finnhub's ``YYYY-MM-DD HH:MM:SS`` timestamps."""
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _unit(unit: object) -> str | None:
    """Finnhub's unit as company facts names it: currencies in capitals, and shares."""
    text = str(unit or "").strip()
    if text.lower() == "shares":
        return "shares"
    if len(text) == 3 and text.isalpha():
        return text.upper()
    return None


def company_facts(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Rearrange Finnhub's as-reported filings into an EDGAR company facts document."""
    facts: dict[str, dict[str, Any]] = {}
    for report in reports:
        start, end, filed = (_day(report.get(key)) for key in ("startDate", "endDate", "filedDate"))
        form, sections = report.get("form"), report.get("report")
        if end is None or filed is None or not isinstance(sections, dict):
            continue
        for section, items in sections.items():
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict):
                    continue
                taxonomy, _, concept = str(item.get("concept") or "").partition("_")
                unit, value = _unit(item.get("unit")), item.get("value")
                if taxonomy not in TAXONOMIES or not concept or unit is None:
                    continue
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    continue
                row: dict[str, Any] = {"end": end, "val": value, "form": form, "filed": filed}
                if section in SECTIONS_WITH_PERIODS:
                    if start is None:
                        continue
                    row["start"] = start
                units = facts.setdefault(taxonomy, {}).setdefault(concept, {"units": {}})["units"]
                units.setdefault(unit, []).append(row)
    return {"facts": facts}


async def fetch_finnhub_financials(
    symbol: str, client: httpx2.AsyncClient | None = None
) -> Financials:
    """``symbol``'s financials from the 10-Ks and 10-Qs Finnhub relays.

    Raises:
        ProviderNotConfiguredError: If the user has no Finnhub key, or it is rejected.
        DataFetchError: If Finnhub fails or the plan does not cover the company.
        FinancialsNotFoundError: If Finnhub has no statements for the company.
    """
    params = {"symbol": finnhub_symbol(symbol)}
    quarterly, annual = await asyncio.gather(
        cached_get(
            "stock/financials-reported",
            params | {"freq": "quarterly"},
            client,
            ttl=AS_REPORTED_TTL_SECONDS,
        ),
        cached_get(
            "stock/financials-reported",
            params | {"freq": "annual"},
            client,
            ttl=AS_REPORTED_TTL_SECONDS,
        ),
    )
    reports = [
        report
        for payload in (quarterly, annual)
        for report in (payload.get("data") if isinstance(payload, dict) else None) or []
        if isinstance(report, dict)
    ]
    cik = next((int(r["cik"]) for r in reports if str(r.get("cik") or "").isdigit()), 0)
    financials = normalize(company_facts(reports), cik)
    if financials.latest_period_end is None:
        raise FinancialsNotFoundError(f"Finnhub has no financial statements for {symbol}.")
    return financials.model_copy(update={"source": "finnhub"})
