"""Tests for the SEC EDGAR client.

A mock transport stands in for sec.gov, so no test touches the network.
"""

from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.data import sec_edgar
from app.data.sec_edgar import (
    CompanyNotFoundError,
    EdgarNotConfiguredError,
    FilingFetchError,
    fetch_latest_filings,
)

pytestmark = pytest.mark.asyncio

CIK = 320193
PARAGRAPH = "The company faces meaningful competition and supply chain risk. " * 20
MDA_PARAGRAPH = "Net sales increased driven by services revenue growth. " * 20

TEN_K_HTML = f"""<html><body>
<div style="display:none"><ix:header>Item 1A. Risk Factors hidden XBRL facts</ix:header></div>
<table>
<tr><td>Item 1A.</td><td>Risk Factors</td><td>5</td></tr>
<tr><td>Item 1B.</td><td>Unresolved Staff Comments</td><td>17</td></tr>
<tr><td>Item 7.</td><td>Management&#8217;s Discussion and Analysis</td><td>20</td></tr>
<tr><td>Item 7A.</td><td>Quantitative and Qualitative Disclosures</td><td>28</td></tr>
</table>
<p>Item 1. Business</p>
<p>See Item 1A. Risk Factors for more. {PARAGRAPH}</p>
<p>Item&#160;1A.&#160;&#160;&#160;&#160;Risk Factors</p>
<p>{PARAGRAPH}</p>
<p>Item 1B. Unresolved Staff Comments</p>
<p>None.</p>
<p>Item 7. Management&#8217;s Discussion and Analysis of Financial Condition</p>
<p>{MDA_PARAGRAPH}</p>
<p>Item 7A. Quantitative and Qualitative Disclosures About Market Risk</p>
<p>Interest rate risk.</p>
<p>Item 8. Financial Statements</p>
</body></html>"""

TEN_Q_HTML = f"""<html><body>
<p>PART I</p>
<p>Item 2. Management's Discussion and Analysis</p>
<p>{MDA_PARAGRAPH}</p>
<p>Item 3. Quantitative and Qualitative Disclosures</p>
<p>PART II</p>
<p>Item 1A. Risk Factors</p>
<p>{PARAGRAPH}</p>
<p>Item 2. Unregistered Sales of Equity Securities</p>
</body></html>"""

TICKERS: dict[str, Any] = {
    "0": {"cik_str": CIK, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 1067983, "ticker": "BRK-B", "title": "Berkshire Hathaway Inc."},
}


def _submissions(rows: list[tuple[str, str, str, str, str]]) -> dict[str, Any]:
    """Build a submissions index from (form, accession, filed, period, document) rows."""
    keys = ["form", "accessionNumber", "filingDate", "reportDate", "primaryDocument"]
    return {"filings": {"recent": {key: [row[i] for row in rows] for i, key in enumerate(keys)}}}


SUBMISSIONS = _submissions(
    [
        ("8-K", "0000320193-25-000010", "2025-02-01", "2025-02-01", "8k.htm"),
        ("10-Q", "0000320193-25-000008", "2025-01-31", "2024-12-28", "10q.htm"),
        ("10-K/A", "0000320193-24-000200", "2024-12-15", "2024-09-28", "10ka.htm"),
        ("10-K", "0000320193-24-000123", "2024-11-01", "2024-09-28", "10k.htm"),
        ("10-Q", "0000320193-24-000081", "2024-08-02", "2024-06-29", "old10q.htm"),
    ]
)

Handler = Callable[[httpx.Request], httpx.Response]


def _sec(overrides: dict[str, httpx.Response] | None = None) -> tuple[Handler, list[str]]:
    """Build a handler serving the fixtures above, and the list of URLs it was asked for."""
    requested: list[str] = []
    documents = {"10k.htm": TEN_K_HTML, "10q.htm": TEN_Q_HTML}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested.append(url)
        if overrides and url in overrides:
            return overrides[url]
        if url == sec_edgar.TICKERS_URL:
            return httpx.Response(200, json=TICKERS)
        if url == sec_edgar.SUBMISSIONS_URL.format(cik=CIK):
            return httpx.Response(200, json=SUBMISSIONS)
        document = url.rsplit("/", 1)[-1]
        if document in documents:
            return httpx.Response(200, text=documents[document])
        return httpx.Response(404)

    return handler, requested


def _client(handler: Handler) -> httpx.AsyncClient:
    """Build a client routed to ``handler``."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Start each test with empty caches, default settings and no retry delays."""
    sec_edgar.clear_caches()
    monkeypatch.setattr(sec_edgar, "get_settings", lambda: Settings(sec_user_agent="Test t@e.com"))

    async def no_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr(sec_edgar.asyncio, "sleep", no_sleep)
    yield
    sec_edgar.clear_caches()


async def test_fetches_latest_10k_and_later_10q() -> None:
    """The latest 10-K and the 10-Q filed after it are read; amendments and older 10-Qs are not."""
    handler, requested = _sec()

    filings = await fetch_latest_filings("aapl", client=_client(handler))

    assert [(f.form, str(f.filed)) for f in filings] == [
        ("10-K", "2024-11-01"),
        ("10-Q", "2025-01-31"),
    ]
    annual = filings[0]
    assert annual.accession_number == "0000320193-24-000123"
    assert str(annual.period_of_report) == "2024-09-28"
    assert annual.url == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/10k.htm"
    )
    assert not any(url.endswith(("10ka.htm", "old10q.htm", "8k.htm")) for url in requested)


async def test_extracts_sections_past_the_table_of_contents() -> None:
    """Each item is the body text, not its table-of-contents line or a cross-reference."""
    handler, _ = _sec()

    annual, quarterly = await fetch_latest_filings("AAPL", client=_client(handler))

    risk, mda = annual.sections
    assert risk.title == "Item 1A. Risk Factors"
    assert risk.text.startswith("Item 1A. Risk Factors")
    assert "competition and supply chain risk" in risk.text
    assert "Unresolved Staff Comments" not in risk.text
    assert "hidden XBRL" not in risk.text
    assert not risk.truncated
    assert mda.title == "Item 7. Management's Discussion and Analysis"
    assert "services revenue growth" in mda.text
    assert "Interest rate risk" not in mda.text

    assert [s.title for s in quarterly.sections] == [
        "Part I, Item 2. Management's Discussion and Analysis",
        "Part II, Item 1A. Risk Factors",
    ]
    assert "Unregistered Sales" not in quarterly.sections[1].text


async def test_long_sections_are_cut_and_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sections over the configured length are truncated and marked as such."""
    settings = Settings(sec_user_agent="Test t@e.com", sec_section_max_chars=1_000)
    monkeypatch.setattr(sec_edgar, "get_settings", lambda: settings)
    handler, _ = _sec()

    annual, _ = await fetch_latest_filings("AAPL", client=_client(handler))

    assert all(len(s.text) == 1_000 and s.truncated for s in annual.sections)


async def test_share_class_dot_matches_edgar_dash() -> None:
    """``BRK.B`` resolves to EDGAR's ``BRK-B``."""
    submissions_url = sec_edgar.SUBMISSIONS_URL.format(cik=1067983)
    handler, requested = _sec({submissions_url: httpx.Response(200, json=_submissions([]))})

    filings = await fetch_latest_filings("BRK.B", client=_client(handler))

    assert filings == []
    assert submissions_url in requested


async def test_unknown_ticker_is_company_not_found() -> None:
    """Symbols without an SEC registrant, such as indices, are reported as such."""
    handler, _ = _sec()

    with pytest.raises(CompanyNotFoundError):
        await fetch_latest_filings("^GSPC", client=_client(handler))


async def test_throttling_is_retried() -> None:
    """A 429 is retried after the server's delay and then succeeds."""
    responses = iter(
        [httpx.Response(429, headers={"retry-after": "1"}), httpx.Response(200, json=TICKERS)]
    )
    base, requested = _sec()

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == sec_edgar.TICKERS_URL:
            requested.append(str(request.url))
            return next(responses)
        return base(request)

    filings = await fetch_latest_filings("AAPL", client=_client(handler))

    assert len(filings) == 2
    assert requested.count(sec_edgar.TICKERS_URL) == 2


async def test_persistent_server_errors_fail() -> None:
    """Server errors that outlast the retries become a fetch error."""
    handler, requested = _sec({sec_edgar.TICKERS_URL: httpx.Response(503)})

    with pytest.raises(FilingFetchError):
        await fetch_latest_filings("AAPL", client=_client(handler))

    assert requested.count(sec_edgar.TICKERS_URL) == sec_edgar.MAX_RETRIES + 1


async def test_forbidden_explains_user_agent() -> None:
    """A 403, which the SEC sends for unidentified or excessive traffic, is explained."""
    handler, _ = _sec({sec_edgar.TICKERS_URL: httpx.Response(403)})

    with pytest.raises(FilingFetchError, match="SEC_USER_AGENT"):
        await fetch_latest_filings("AAPL", client=_client(handler))


async def test_requires_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without ``SEC_USER_AGENT`` no request is made."""
    monkeypatch.setattr(sec_edgar, "get_settings", lambda: Settings(sec_user_agent=None))

    with pytest.raises(EdgarNotConfiguredError):
        await fetch_latest_filings("AAPL")


async def test_ticker_map_and_sections_are_cached() -> None:
    """A second fetch rereads only the submissions index."""
    handler, requested = _sec()
    client = _client(handler)

    await fetch_latest_filings("AAPL", client=client)
    first = len(requested)
    await fetch_latest_filings("AAPL", client=client)

    assert requested[first:] == [sec_edgar.SUBMISSIONS_URL.format(cik=CIK)]
