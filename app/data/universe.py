"""Raw data for the screened universe: listed companies, their prices and their size.

The universe starts from the SEC's list of companies with exchange-listed tickers, which
covers every US-listed stock whose issuer files with the SEC. Index memberships such as
the S&P 500 or the Russell indices are licensed, so size and liquidity filters stand in
for them. Prices come from yfinance in batches. Market caps, sectors and industries come
from Nasdaq's screener feed in one request; market caps it lacks come from shares
outstanding reported to the SEC, or from yfinance one stock at a time.

All of this is fetched by the refresh job, not while serving requests.
"""

import asyncio
import logging
import math
import re
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx2
import pandas as pd
import yfinance as yf

from app.data.sec_edgar import edgar_get

logger = logging.getLogger(__name__)

LISTINGS_URL: str = "https://www.sec.gov/files/company_tickers_exchange.json"
NASDAQ_SCREENER_URL: str = "https://api.nasdaq.com/api/screener/stocks"
"""Nasdaq's public stock screener feed: every US-listed stock's market cap, sector and
industry in one response."""
FRAMES_URL: str = "https://data.sec.gov/api/xbrl/frames/{taxonomy}/{concept}/shares/{period}.json"

EXCHANGES: frozenset[str] = frozenset({"NYSE", "Nasdaq", "CBOE"})
"""Exchanges whose listings are screened; over-the-counter quotes are not."""

COMMON_STOCK: re.Pattern[str] = re.compile(r"[A-Z]{1,5}(?:-[A-Z])?")
"""Tickers of common shares: up to five letters, or a share class such as BRK-B."""

NON_COMMON_SUFFIXES: frozenset[str] = frozenset({"W", "U", "R"})
"""Fifth letters Nasdaq gives warrants, units and rights."""

NON_COMMON_NAME: re.Pattern[str] = re.compile(
    r"\bpreferred\b|\bnotes?\b|debentures?|\bwarrants?\b|\bunits?\b|\brights?\b|%",
    re.IGNORECASE,
)
"""Words in a security's name that mark it as something other than common shares, such
as Alphabet's convertible preferred depositary shares GOOGN, whose ticker looks common."""

PRICE_BATCH_SIZE: int = 200
"""Tickers per yfinance download; larger batches are throttled more often."""

PRICE_RETRIES: int = 2
"""Retries of a failed batch before its tickers are skipped."""

MARKET_CAP_WORKERS: int = 4
"""Concurrent market cap lookups; Yahoo throttles more than a few at a time."""

MARKET_CAP_ROUNDS: int = 3
"""Passes over lookups that failed, as opposed to those that found no market cap."""

MARKET_CAP_PAUSE_SECONDS: float = 60.0
"""Wait before retrying failed lookups, long enough for Yahoo's throttling to lift."""


@dataclass(frozen=True)
class Profile:
    """A security's name, market cap and classification from Nasdaq's screener feed."""

    name: str
    market_cap: float | None
    sector: str | None
    industry: str | None


@dataclass(frozen=True)
class Listing:
    """An exchange-listed ticker and the SEC registrant behind it."""

    symbol: str
    cik: int
    name: str
    exchange: str


def is_common_stock(symbol: str) -> bool:
    """Whether ``symbol`` looks like common shares rather than preferreds, units or warrants.

    EDGAR writes preferred series as ``JPM-PC``, and Nasdaq ends warrant, unit and right
    tickers with W, U or R as a fifth letter.
    """
    if not COMMON_STOCK.fullmatch(symbol):
        return False
    return not (len(symbol) == 5 and symbol[-1] in NON_COMMON_SUFFIXES)


def is_common_name(name: str) -> bool:
    """Whether a security's name describes common shares; ADRs of common shares count."""
    return not NON_COMMON_NAME.search(name)


def parse_listings(payload: dict[str, Any]) -> list[Listing]:
    """Read listed common stocks from the SEC's ticker and exchange file."""
    fields: list[str] = payload["fields"]
    index = {name: fields.index(name) for name in ("cik", "name", "ticker", "exchange")}
    listings: dict[str, Listing] = {}
    for row in payload["data"]:
        symbol = str(row[index["ticker"]] or "").upper()
        exchange = row[index["exchange"]]
        if exchange not in EXCHANGES or not is_common_stock(symbol) or symbol in listings:
            continue
        listings[symbol] = Listing(
            symbol=symbol,
            cik=int(row[index["cik"]]),
            name=str(row[index["name"]]),
            exchange=str(exchange),
        )
    return sorted(listings.values(), key=lambda listing: listing.symbol)


def _number(value: object) -> float | None:
    """Parse a number the feed writes as text, mapping blanks and non-positive values to None."""
    try:
        number = float(str(value).replace("$", "").replace(",", ""))
    except ValueError:
        return None
    return number if math.isfinite(number) and number > 0 else None


def parse_profiles(payload: dict[str, Any]) -> dict[str, Profile]:
    """Read Nasdaq's screener rows, keyed by ticker as EDGAR writes it (``BRK/B`` is BRK-B)."""
    rows: list[dict[str, Any]] = payload.get("data", {}).get("rows") or []
    profiles: dict[str, Profile] = {}
    for row in rows:
        if not row.get("symbol"):
            continue
        symbol = str(row["symbol"]).strip().upper().replace("/", "-")
        profiles[symbol] = Profile(
            name=str(row.get("name") or "").strip(),
            market_cap=_number(row.get("marketCap")),
            sector=str(row.get("sector") or "").strip() or None,
            industry=str(row.get("industry") or "").strip() or None,
        )
    return profiles


async def fetch_profiles() -> dict[str, Profile]:
    """Fetch every US-listed stock's market cap, sector and industry from Nasdaq.

    Raises:
        httpx2.HTTPError: If the feed cannot be fetched.
    """
    async with httpx2.AsyncClient(
        headers={"User-Agent": "Mozilla/5.0 (PyQxel)", "Accept": "application/json"},
        timeout=60.0,
    ) as client:
        response = await client.get(
            NASDAQ_SCREENER_URL, params={"tableonly": "true", "download": "true"}
        )
        response.raise_for_status()
        return parse_profiles(response.json())


async def fetch_listings(client: httpx2.AsyncClient) -> list[Listing]:
    """Fetch the common stocks listed on US exchanges whose issuers file with the SEC."""
    payload: Any = (await edgar_get(client, LISTINGS_URL)).json()
    return parse_listings(payload)


def _quarters_back(today: date, count: int) -> list[str]:
    """Frame names of the latest ``count`` calendar quarters, newest first, e.g. CY2026Q3I."""
    year, quarter = today.year, (today.month - 1) // 3 + 1
    names: list[str] = []
    for _ in range(count):
        names.append(f"CY{year}Q{quarter}I")
        year, quarter = (year, quarter - 1) if quarter > 1 else (year - 1, 4)
    return names


async def fetch_share_counts(
    client: httpx2.AsyncClient, today: date | None = None
) -> dict[int, float]:
    """Shares outstanding per CIK, from the latest quarters' SEC frames.

    Cover-page counts are preferred, then balance sheet counts. Companies with several
    share classes report each class separately, so they are missing from the frames and
    rely on the market data provider's market cap instead.
    """
    counts: dict[int, float] = {}
    sources = [
        ("dei", "EntityCommonStockSharesOutstanding"),
        ("us-gaap", "CommonStockSharesOutstanding"),
    ]
    # Earlier sources and newer quarters win, so they are applied last.
    for taxonomy, concept in reversed(sources):
        for period in reversed(_quarters_back(today or date.today(), 3)):
            url = FRAMES_URL.format(taxonomy=taxonomy, concept=concept, period=period)
            try:
                payload: Any = (await edgar_get(client, url)).json()
            except httpx2.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    continue  # The quarter has no filings yet.
                raise
            for row in payload.get("data", []):
                try:
                    cik, value = int(row["cik"]), float(row["val"])
                except KeyError, TypeError, ValueError:
                    continue
                if value > 0:
                    counts[cik] = value
    return counts


def _batch_prices(symbols: list[str], start: date) -> dict[str, pd.DataFrame]:
    """Download adjusted closes and volumes for one batch of symbols (blocking)."""
    raw = yf.download(
        symbols,
        start=start.isoformat(),
        interval="1d",
        auto_adjust=True,
        group_by="column",
        threads=True,
        progress=False,
        multi_level_index=True,
    )
    if raw is None or raw.empty:
        return {}
    frames: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        try:
            frame = pd.DataFrame(
                {"Close": raw["Close"][symbol], "Volume": raw["Volume"][symbol]}
            ).dropna(subset=["Close"])
        except KeyError:
            continue
        if not frame.empty:
            frames[symbol] = frame
    return frames


async def download_prices(symbols: Iterable[str], start: date) -> dict[str, pd.DataFrame]:
    """Download daily adjusted closes and volumes since ``start`` for every symbol.

    Batches that keep failing are skipped and their symbols left out, so one throttled
    batch does not end the refresh.

    Returns:
        A frame with ``Close`` and ``Volume`` columns per symbol that has data.
    """
    pending = list(symbols)
    prices: dict[str, pd.DataFrame] = {}
    for first in range(0, len(pending), PRICE_BATCH_SIZE):
        batch = pending[first : first + PRICE_BATCH_SIZE]
        for attempt in range(PRICE_RETRIES + 1):
            try:
                prices.update(await asyncio.to_thread(_batch_prices, batch, start))
                break
            except Exception as exc:  # yfinance raises many unrelated exception types.
                if attempt == PRICE_RETRIES:
                    logger.warning(
                        "Skipping %d symbols after failed downloads: %s", len(batch), exc
                    )
                else:
                    await asyncio.sleep(2.0 ** (attempt + 1))
        logger.info("Downloaded prices for %d of %d symbols.", len(prices), first + len(batch))
    return prices


class LookupFailedError(RuntimeError):
    """Raised when a market cap lookup fails, so that it can be retried."""


def _market_cap(symbol: str) -> float | None:
    """The market cap yfinance reports for ``symbol`` (blocking), or ``None`` if it has none.

    Raises:
        LookupFailedError: If the lookup failed, as it does while Yahoo throttles requests.
    """
    try:
        value = yf.Ticker(symbol).fast_info["market_cap"]
    except Exception as exc:  # yfinance raises many unrelated exception types.
        raise LookupFailedError(str(exc)) from exc
    if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return None
    return float(value)


def _lookup(symbol: str) -> float | None | LookupFailedError:
    """:func:`_market_cap`, returning its failure instead of raising it."""
    try:
        return _market_cap(symbol)
    except LookupFailedError as exc:
        return exc


async def fetch_market_caps(symbols: Iterable[str]) -> dict[str, float]:
    """Market caps from yfinance, which counts every share class.

    Lookups run a few at a time, in the order given, so the most important symbols should
    come first. Failed lookups are retried after a pause; symbols still failing, or
    without a market cap, are left out.
    """
    pending = list(symbols)
    caps: dict[str, float] = {}
    for attempt in range(MARKET_CAP_ROUNDS):
        if attempt:
            logger.info(
                "Retrying %d failed market cap lookups in %.0fs.",
                len(pending),
                MARKET_CAP_PAUSE_SECONDS,
            )
            await asyncio.sleep(MARKET_CAP_PAUSE_SECONDS)

        def lookup(batch: list[str] = pending) -> list[float | None | LookupFailedError]:
            with ThreadPoolExecutor(max_workers=MARKET_CAP_WORKERS) as executor:
                return list(executor.map(_lookup, batch))

        results = await asyncio.to_thread(lookup)
        caps |= {
            symbol: value for symbol, value in zip(pending, results) if isinstance(value, float)
        }
        pending = [
            symbol
            for symbol, value in zip(pending, results)
            if isinstance(value, LookupFailedError)
        ]
        if not pending:
            break
    if pending:
        logger.warning("Market cap lookups failed for %d symbols.", len(pending))
    return caps
