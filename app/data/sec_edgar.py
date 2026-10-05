"""Async SEC EDGAR client for a company's latest annual and quarterly reports.

A ticker is mapped to its SEC CIK, the company's filing index is read, and the primary
document of the latest 10-K, plus any 10-Q filed after it, is downloaded. Only the
Risk Factors and Management's Discussion and Analysis (MD&A) items are kept: they are
the narrative sections that add most to price statistics, and a whole 10-K would cost
far more tokens than it adds.

The SEC asks automated clients to identify themselves with a User-Agent that includes
a contact email and to stay under 10 requests a second, so requests carry
``SEC_USER_AGENT`` and throttled requests are retried with backoff. Filings never
change once accepted, so extracted sections are cached by document URL.
"""

import asyncio
import logging
import re
import time
import warnings
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx2
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from app.core.config import get_settings
from app.models.research import Filing, FilingForm, FilingSection

logger = logging.getLogger(__name__)

TICKERS_URL: str = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL: str = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVES_URL: str = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"

HTTP_TIMEOUT_SECONDS: float = 30.0
"""Primary documents of large filers run to several megabytes."""

MAX_RETRIES: int = 3
"""Retries of a throttled or failed request before giving up."""

RETRY_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})

TICKER_MAP_TTL_SECONDS: float = 24 * 60 * 60
"""How long the ticker-to-CIK map is reused; the SEC regenerates it daily."""

SECTION_CACHE_SIZE: int = 64
"""Filings whose extracted sections are kept in memory."""

BLOCK_TAGS: list[str] = [
    "p",
    "div",
    "br",
    "tr",
    "li",
    "table",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
]
"""Elements that start a new line in a rendered filing."""

_SEP = r"[\s.:\-–—]*"
"""Punctuation and whitespace between an item number and its title."""


@dataclass(frozen=True)
class SectionPattern:
    """Where an item starts and which headings can follow it."""

    title: str
    start: re.Pattern[str]
    end: re.Pattern[str]


def _heading(pattern: str) -> re.Pattern[str]:
    """Compile ``pattern`` to match only at the start of a line, ignoring case."""
    return re.compile(rf"(?:^|\n)[ \t]*{pattern}", re.IGNORECASE)


SECTIONS: dict[FilingForm, tuple[SectionPattern, ...]] = {
    "10-K": (
        SectionPattern(
            "Item 1A. Risk Factors",
            _heading(rf"item\s*1a{_SEP}risk\s+factors"),
            _heading(r"item\s*(?:1b|1c|2)\b"),
        ),
        SectionPattern(
            "Item 7. Management's Discussion and Analysis",
            _heading(rf"item\s*7{_SEP}management[’']?s\s+discussion"),
            _heading(r"item\s*(?:7a|8)\b"),
        ),
    ),
    "10-Q": (
        SectionPattern(
            "Part I, Item 2. Management's Discussion and Analysis",
            _heading(rf"item\s*2{_SEP}management[’']?s\s+discussion"),
            _heading(r"item\s*(?:3|4)\b"),
        ),
        SectionPattern(
            "Part II, Item 1A. Risk Factors",
            _heading(rf"item\s*1a{_SEP}risk\s+factors"),
            _heading(r"item\s*[2-6]\b"),
        ),
    ),
}
"""The items kept from each form, in reading order."""


class FilingFetchError(RuntimeError):
    """Raised when filings cannot be retrieved from EDGAR."""


class EdgarNotConfiguredError(FilingFetchError):
    """Raised when ``SEC_USER_AGENT`` is not set."""


class CompanyNotFoundError(FilingFetchError):
    """Raised when no SEC registrant uses the symbol, as for indices and many foreign listings."""


_ticker_map: tuple[float, dict[str, int]] | None = None
_section_cache: "OrderedDict[tuple[str, int], list[FilingSection]]" = OrderedDict()


def clear_caches() -> None:
    """Forget the cached ticker map and extracted sections."""
    global _ticker_map
    _ticker_map = None
    _section_cache.clear()


def _retry_delay(response: httpx2.Response | None, attempt: int) -> float:
    """Return how long to wait before retry ``attempt``, honouring ``Retry-After``."""
    if response is not None:
        value = response.headers.get("retry-after")
        try:
            return min(max(float(value), 0.0), 30.0) if value is not None else 2.0**attempt
        except ValueError:
            pass
    return 2.0**attempt


async def _get(client: httpx2.AsyncClient, url: str) -> httpx2.Response:
    """GET ``url``, retrying throttling, server errors and transport failures with backoff."""
    for attempt in range(MAX_RETRIES + 1):
        response: httpx2.Response | None = None
        try:
            response = await client.get(url)
            if response.status_code not in RETRY_STATUSES:
                response.raise_for_status()
                return response
            error: Exception = httpx2.HTTPStatusError(
                f"EDGAR returned {response.status_code}",
                request=response.request,
                response=response,
            )
        except httpx2.HTTPStatusError:
            raise
        except httpx2.TransportError as exc:
            error = exc
        if attempt == MAX_RETRIES:
            raise error
        delay = _retry_delay(response, attempt)
        logger.warning("EDGAR request to %s failed (%s); retrying in %.1fs", url, error, delay)
        await asyncio.sleep(delay)
    raise AssertionError("unreachable")


async def _cik_for(symbol: str, client: httpx2.AsyncClient) -> int:
    """Return the SEC CIK registered for ``symbol``."""
    global _ticker_map
    if _ticker_map is None or time.monotonic() - _ticker_map[0] > TICKER_MAP_TTL_SECONDS:
        payload: Any = (await _get(client, TICKERS_URL)).json()
        mapping = {
            str(entry["ticker"]).upper(): int(entry["cik_str"])
            for entry in payload.values()
            if isinstance(entry, dict) and "ticker" in entry and "cik_str" in entry
        }
        _ticker_map = (time.monotonic(), mapping)
    # EDGAR writes share classes with a dash (BRK-B); some sources use a dot (BRK.B).
    cik = _ticker_map[1].get(symbol) or _ticker_map[1].get(symbol.replace(".", "-"))
    if cik is None:
        raise CompanyNotFoundError(f"SEC EDGAR has no registrant with ticker {symbol!r}.")
    return cik


def _parse_date(value: object) -> date | None:
    """Parse an ISO date from the submissions index, mapping blanks to ``None``."""
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _latest_filings(submissions: dict[str, Any]) -> list[tuple[FilingForm, dict[str, Any]]]:
    """Pick the latest 10-K and any 10-Q filed after it from a submissions index.

    Amendments are skipped: they often restate only part of a filing.
    """
    recent: dict[str, list[Any]] = submissions.get("filings", {}).get("recent", {})
    rows = [
        {key: values[i] for key, values in recent.items() if i < len(values)}
        for i in range(len(recent.get("form", [])))
    ]
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        form = row.get("form")
        filed = _parse_date(row.get("filingDate"))
        if form not in ("10-K", "10-Q") or filed is None or not row.get("primaryDocument"):
            continue
        if form not in latest or filed > latest[form]["filed"]:
            latest[form] = row | {"filed": filed}

    chosen: list[tuple[FilingForm, dict[str, Any]]] = []
    annual = latest.get("10-K")
    quarterly = latest.get("10-Q")
    if annual is not None:
        chosen.append(("10-K", annual))
    if quarterly is not None and (annual is None or quarterly["filed"] > annual["filed"]):
        chosen.append(("10-Q", quarterly))
    return chosen


def _document_text(html: str) -> str:
    """Return the visible text of a filing document, one block element per line.

    Filings often split a heading across inline elements ("RIS" + "K FACTORS"), so
    line breaks are added only at block elements, and table cells are joined by spaces.
    """
    with warnings.catch_warnings():
        # Inline XBRL documents are XHTML; parsing them as HTML is intended.
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(html, "lxml")
    # Inline XBRL keeps its machine-readable facts in a hidden header.
    for tag in soup.find_all(["ix:header", "script", "style"]):
        tag.decompose()
    for tag in soup.find_all(BLOCK_TAGS):
        tag.insert_before("\n")
        tag.append("\n")
    for tag in soup.find_all(["td", "th"]):
        tag.append(" ")
    text = soup.get_text()
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _extract_section(text: str, pattern: SectionPattern) -> str | None:
    """Return the longest span from a ``pattern`` start heading to the next end heading.

    The table of contents repeats every heading, so the first match is usually a
    one-line entry; the real item is the longest span.
    """
    best: str | None = None
    for start in pattern.start.finditer(text):
        end = pattern.end.search(text, start.end())
        span = text[start.start() : end.start() if end else len(text)].strip()
        if best is None or len(span) > len(best):
            best = span
    return best


def _extract_sections(html: str, form: FilingForm, max_chars: int) -> list[FilingSection]:
    """Extract the items of ``form`` listed in :data:`SECTIONS`, cut at ``max_chars``."""
    text = _document_text(html)
    sections: list[FilingSection] = []
    for pattern in SECTIONS[form]:
        span = _extract_section(text, pattern)
        # A span shorter than a paragraph is a table-of-contents line or a stub.
        if span is None or len(span) < 200:
            continue
        truncated = len(span) > max_chars
        sections.append(
            FilingSection(title=pattern.title, text=span[:max_chars], truncated=truncated)
        )
    return sections


async def _filing(
    cik: int, form: FilingForm, row: dict[str, Any], client: httpx2.AsyncClient, max_chars: int
) -> Filing:
    """Download one filing's primary document and extract its sections."""
    accession = str(row["accessionNumber"])
    url = ARCHIVES_URL.format(
        cik=cik, accession=accession.replace("-", ""), document=row["primaryDocument"]
    )
    key = (url, max_chars)
    sections = _section_cache.get(key)
    if sections is None:
        html = (await _get(client, url)).text
        sections = await asyncio.to_thread(_extract_sections, html, form, max_chars)
        _section_cache[key] = sections
        while len(_section_cache) > SECTION_CACHE_SIZE:
            _section_cache.popitem(last=False)
    else:
        _section_cache.move_to_end(key)
    return Filing(
        form=form,
        accession_number=accession,
        filed=row["filed"],
        period_of_report=_parse_date(row.get("reportDate")),
        url=url,
        sections=sections,
    )


async def _fetch(symbol: str, client: httpx2.AsyncClient, max_chars: int) -> list[Filing]:
    """Fetch the latest filings for ``symbol`` with ``client``."""
    cik = await _cik_for(symbol, client)
    submissions: Any = (await _get(client, SUBMISSIONS_URL.format(cik=cik))).json()
    chosen = _latest_filings(submissions)
    return list(
        await asyncio.gather(*(_filing(cik, form, row, client, max_chars) for form, row in chosen))
    )


async def fetch_latest_filings(
    symbol: str, client: httpx2.AsyncClient | None = None
) -> list[Filing]:
    """Fetch the latest 10-K, and any 10-Q filed after it, for ``symbol``.

    Args:
        symbol: Ticker symbol, e.g. ``"AAPL"``. Case and surrounding whitespace are ignored.
        client: Optional HTTP client, which must send the SEC User-Agent itself. A
            short-lived client is created when omitted.

    Returns:
        The filings, oldest first. A filing whose sections could not be located has an
        empty ``sections`` list. The list is empty when the company files neither form,
        as foreign private issuers filing 20-F and 40-F reports do.

    Raises:
        EdgarNotConfiguredError: If ``SEC_USER_AGENT`` is not set.
        CompanyNotFoundError: If no SEC registrant has ``symbol`` as a ticker.
        FilingFetchError: If EDGAR cannot be reached or returns an unexpected response.
    """
    symbol = symbol.strip().upper()
    settings = get_settings()
    try:
        if client is not None:
            return await _fetch(symbol, client, settings.sec_section_max_chars)
        if not settings.sec_user_agent:
            raise EdgarNotConfiguredError(
                "SEC filings are disabled: set SEC_USER_AGENT to a name and contact email."
            )
        async with httpx2.AsyncClient(
            headers={"User-Agent": settings.sec_user_agent},
            timeout=HTTP_TIMEOUT_SECONDS,
            follow_redirects=True,
        ) as own_client:
            return await _fetch(symbol, own_client, settings.sec_section_max_chars)
    except FilingFetchError:
        raise
    except httpx2.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            raise FilingFetchError(
                "SEC EDGAR refused the request; check that SEC_USER_AGENT names you and a "
                "contact email, and that requests stay under 10 a second."
            ) from exc
        raise FilingFetchError(f"Could not fetch SEC filings for {symbol!r}.") from exc
    except (httpx2.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise FilingFetchError(f"Could not fetch SEC filings for {symbol!r}.") from exc
