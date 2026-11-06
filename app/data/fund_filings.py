"""What a mutual fund or ETF holds, from its N-PORT filings with the SEC.

Every US mutual fund and ETF reports its whole portfolio to the SEC on Form N-PORT each
month, and the SEC publishes the quarter-end ones about 60 days later: each holding's
name, CUSIP, value, share of net assets, kind of asset (stock, bond, mortgage-backed,
cash) and issuer (company, Treasury, agency, municipality), and for a bond its maturity
and coupon. It is public data, so it needs no key, and every account may be shown it.

The SEC's mutual fund ticker list maps a ticker to its fund (a "series"; VOO and VFIAX are
two share classes of one), whose latest filing is found through EDGAR's feed of the
series' filings. A filing is large (17,000 holdings and 20 MB for a total bond market
fund), so what is kept of it is a summary, reused for :data:`FILING_TTL_SECONDS`. Unit
investment trusts such as SPY do not file N-PORT.
"""

import asyncio
import logging
import re
import time
import xml.etree.ElementTree as ElementTree
from collections import defaultdict
from datetime import date
from io import BytesIO

import httpx2
from pydantic import BaseModel, Field

from app.core.cache import cache_get, cache_set
from app.data.fetcher import DataFetchError, SymbolNotFoundError
from app.data.sec_edgar import edgar_client, edgar_get
from app.models.stock import FundBonds

logger = logging.getLogger(__name__)

FUND_TICKERS_URL: str = "https://www.sec.gov/files/company_tickers_mf.json"
"""The SEC's list of mutual fund and ETF share classes: CIK, series, class and ticker."""
SERIES_FILINGS_URL: str = (
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={series}&type=NPORT-P"
    "&dateb=&owner=include&count=5&output=atom"
)
"""EDGAR's feed of a fund series' N-PORT filings, newest first."""
FUND_TICKERS_TTL_SECONDS: float = 24 * 60 * 60
"""How long the ticker list is reused; funds are added to it now and then."""
FILING_TTL_SECONDS: float = 7 * 24 * 60 * 60
"""How long a fund's filing summary is reused: the SEC publishes one a quarter."""
TOP_HOLDINGS: int = 25
"""Largest holdings of any kind a summary lists."""
NAMESPACE: str = "{http://www.sec.gov/edgar/nport}"
MIN_BOND_WEIGHT: float = 0.02
"""Least share of net assets in bonds for a fund's bond figures to be given; a stock fund's
few Treasury bills, held as cash, are not a bond portfolio."""

ASSET_CLASSES: dict[str, str] = {
    "EC": "Stocks",
    "EP": "Stocks",
    "DBT": "Bonds",
    "ABS-MBS": "Bonds",
    "ABS-O": "Bonds",
    "ABS-CBDO": "Bonds",
    "ABS-APCP": "Bonds",
    "LON": "Bonds",
    "SN": "Bonds",
    "STIV": "Cash",
}
"""The broad class of each N-PORT asset category; derivatives (D...) and others are
``Other``."""

BOND_ISSUERS: dict[str, str] = {
    "UST": "US Treasury",
    "USGA": "US agencies and mortgages",
    "USGSE": "US agencies and mortgages",
    "CORP": "Corporate",
    "MUN": "Municipal",
    "NUSS": "Foreign governments",
}
"""The kind of issuer of each N-PORT issuer category, for a bond; others are ``Other``."""


class FiledHolding(BaseModel):
    """One holding, as a fund's filing reports it."""

    name: str
    weight: float = Field(description="Share of the fund's net assets, as a decimal.")
    asset_class: str = Field(description="Stocks, Bonds, Cash or Other.")
    cusip: str | None = None


class FundFiling(BaseModel):
    """A summary of a fund's latest N-PORT filing."""

    series_id: str
    series_name: str
    as_of: date = Field(description="The date the holdings are reported as of.")
    net_assets: float | None = Field(default=None, description="US dollars.")
    holdings_count: int = Field(ge=0)
    asset_mix: dict[str, float] = Field(
        description="Share of net assets in stocks, bonds, cash and other, as decimals."
    )
    top_holdings: list[FiledHolding] = Field(
        description="The largest issuers, of any kind, each with its issues' combined weight."
    )
    stocks: list[FiledHolding] = Field(
        description="Every stock it holds, largest first, for statistics weighted by them."
    )
    bonds: FundBonds | None = Field(default=None, description="Null when it holds no bonds.")


_tickers: tuple[float, dict[str, str]] | None = None
"""(time read, ticker -> series ID), for deployments without Redis."""
_filings: dict[str, tuple[float, FundFiling]] = {}
"""Series ID -> (time read, summary), for deployments without Redis."""


async def fund_series(symbol: str, client: httpx2.AsyncClient) -> str | None:
    """The SEC series ID of the fund ``symbol`` is a share class of; ``None`` when it is
    not on the SEC's list of fund tickers (a stock, or a trust such as SPY)."""
    global _tickers
    if _tickers is None or time.monotonic() - _tickers[0] > FUND_TICKERS_TTL_SECONDS:
        payload = (await edgar_get(client, FUND_TICKERS_URL)).json()
        fields: list[str] = payload.get("fields") or []
        position = {name: index for index, name in enumerate(fields)}
        found: dict[str, str] = {}
        for row in payload.get("data") or []:
            try:
                found[str(row[position["symbol"]]).upper()] = str(row[position["seriesId"]])
            except IndexError, KeyError, TypeError:
                continue
        _tickers = (time.monotonic(), found)
    return _tickers[1].get(symbol.strip().upper())


def _number(text: str | None) -> float | None:
    """A filing's number, or ``None``."""
    try:
        return float(text) if text not in (None, "", "N/A") else None
    except ValueError:
        return None


def parse_filing(content: bytes) -> FundFiling:
    """Summarize an N-PORT filing's XML (blocking: a bond fund's takes a second or two).

    Raises:
        DataFetchError: If it is not a readable N-PORT filing.
    """
    series_id = series_name = ""
    as_of: date | None = None
    net_assets: float | None = None
    holdings: list[FiledHolding] = []
    mix: dict[str, float] = defaultdict(float)
    bond_weight = coupon_sum = coupon_weight = maturity_sum = maturity_weight = 0.0
    bond_count = 0
    issuers: dict[str, float] = defaultdict(float)
    bond_rows: list[tuple[float, date | None, float | None]] = []

    def text(element: ElementTree.Element, tag: str) -> str | None:
        found = element.find(f".//{NAMESPACE}{tag}")
        return None if found is None or found.text is None else found.text.strip()

    try:
        for _, element in ElementTree.iterparse(BytesIO(content), events=("end",)):
            tag = element.tag.removeprefix(NAMESPACE)
            if tag == "genInfo":
                series_id = text(element, "seriesId") or ""
                series_name = (text(element, "seriesName") or "").title()
                as_of = date.fromisoformat(text(element, "repPdDate") or "")
            elif tag == "fundInfo":
                net_assets = _number(text(element, "netAssets"))
            elif tag == "invstOrSec":
                weight = (_number(text(element, "pctVal")) or 0.0) / 100
                category = text(element, "assetCat") or ""
                asset_class = ASSET_CLASSES.get(category, "Other")
                mix[asset_class] += weight
                name = text(element, "name") or text(element, "title") or "Unnamed holding"
                holdings.append(
                    FiledHolding(
                        name=name,
                        weight=weight,
                        asset_class=asset_class,
                        cusip=text(element, "cusip"),
                    )
                )
                if asset_class == "Bonds":
                    bond_count += 1
                    bond_weight += weight
                    issuer = text(element, "issuerCat")
                    if issuer is None:
                        conditional = element.find(f".//{NAMESPACE}issuerConditional")
                        issuer = None if conditional is None else conditional.get("issuerCat")
                    issuers[BOND_ISSUERS.get(issuer or "", "Other")] += weight
                    due = text(element, "maturityDt")
                    bond_rows.append(
                        (
                            weight,
                            date.fromisoformat(due) if due else None,
                            _number(text(element, "annualizedRt")),
                        )
                    )
                element.clear()
    except (ElementTree.ParseError, ValueError) as exc:
        raise DataFetchError(f"The SEC's N-PORT filing could not be read: {exc}") from exc
    if not series_id or as_of is None:
        raise DataFetchError("The SEC's N-PORT filing names no fund or date.")
    for weight, maturity, rate in bond_rows:
        if weight <= 0:
            continue
        if maturity is not None:
            maturity_sum += weight * max(0.0, (maturity - as_of).days / 365.25)
            maturity_weight += weight
        if rate is not None:
            coupon_sum += weight * rate / 100
            coupon_weight += weight
    holdings.sort(key=lambda holding: -holding.weight)
    # A bond fund holds many issues of one borrower, such as hundreds of Treasury notes,
    # and a stock fund two share classes of one company: its largest holdings are listed
    # by issuer, each with its issues' combined weight.
    by_issuer: dict[str, FiledHolding] = {}
    for holding in holdings:
        kept = by_issuer.get(holding.name)
        by_issuer[holding.name] = (
            holding
            if kept is None
            else kept.model_copy(update={"weight": kept.weight + holding.weight})
        )
    top = sorted(by_issuer.values(), key=lambda holding: -holding.weight)[:TOP_HOLDINGS]
    bonds = (
        FundBonds(
            weight=bond_weight,
            count=bond_count,
            average_coupon=coupon_sum / coupon_weight if coupon_weight else None,
            average_maturity_years=maturity_sum / maturity_weight if maturity_weight else None,
            issuers={
                kind: share / bond_weight
                for kind, share in sorted(issuers.items(), key=lambda item: -item[1])
                if share > 0
            },
        )
        if bond_count and bond_weight >= MIN_BOND_WEIGHT
        else None
    )
    return FundFiling(
        series_id=series_id,
        series_name=series_name,
        as_of=as_of,
        net_assets=net_assets,
        holdings_count=len(holdings),
        asset_mix={kind: share for kind, share in sorted(mix.items(), key=lambda i: -i[1])},
        top_holdings=top,
        stocks=[holding for holding in holdings if holding.asset_class == "Stocks"],
        bonds=bonds,
    )


async def fetch_fund_filing(symbol: str) -> FundFiling:
    """A summary of the latest N-PORT filing of the fund ``symbol`` is a share class of.

    Raises:
        SymbolNotFoundError: If ``symbol`` is not a fund on the SEC's list, or its fund has
            filed no N-PORT.
        DataFetchError: If EDGAR cannot be reached or the filing cannot be read.
    """
    try:
        async with edgar_client() as client:
            series = await fund_series(symbol, client)
            if series is None:
                raise SymbolNotFoundError(f"{symbol} is not on the SEC's list of fund tickers.")
            remembered = _filings.get(series)
            if remembered is not None and time.monotonic() - remembered[0] < FILING_TTL_SECONDS:
                return remembered[1]
            cached = await cache_get(f"nport:{series}")
            if cached is not None:
                try:
                    filing = FundFiling.model_validate_json(cached)
                    _filings[series] = (time.monotonic(), filing)
                    return filing
                except ValueError:
                    logger.warning("Ignoring an unreadable cached N-PORT summary of %s.", series)
            feed = (await edgar_get(client, SERIES_FILINGS_URL.format(series=series))).text
            links = re.findall(r"<filing-href>(.*?)</filing-href>", feed)
            if not links:
                raise SymbolNotFoundError(f"{symbol}'s fund has filed no N-PORT with the SEC.")
            folder = links[0].rsplit("/", 1)[0]
            content = (await edgar_get(client, f"{folder}/primary_doc.xml")).content
    except httpx2.HTTPError as exc:
        raise DataFetchError(f"The SEC's fund filings could not be reached: {exc}") from exc
    filing = await asyncio.to_thread(parse_filing, content)
    _filings[series] = (time.monotonic(), filing)
    await cache_set(f"nport:{series}", filing.model_dump_json(), FILING_TTL_SECONDS)
    return filing


def clear_memory() -> None:
    """Forget the ticker list and filings kept in memory."""
    global _tickers
    _tickers = None
    _filings.clear()
