"""Raw data for the screened universe: listed companies, their prices and their size.

The universe starts from the SEC's list of companies with exchange-listed tickers, which
covers every US-listed stock whose issuer files with the SEC. Index memberships such as
the S&P 500 or the Russell indices are licensed, so size and liquidity filters stand in
for them.

Market data comes from Financial Modeling Prep through an :class:`FmpSession` on the
screener's own key, since every account sees the universe it builds: market caps, sectors
and industries from FMP's company screener, a few requests in all, and dividend-adjusted
daily prices one stock at a time, paced to the plan's rate limit. Market caps the company
screener lacks come from shares outstanding reported to the SEC, or from FMP company
profiles. Without that key, with ``YFINANCE_FALLBACK`` on, the functions taking a session
accept ``None`` and use unlicensed sources instead: yfinance, and Nasdaq's public screener
feed for market caps, sectors and industries. That suits development and personal use, not
a deployment others use.

All of this is fetched by the refresh job, not while serving requests.
"""

import asyncio
import logging
import math
import re
import time
from collections.abc import Awaitable, Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from types import TracebackType
from typing import Any, Self, TypeVar

import httpx2
import pandas as pd
import yfinance as yf

from app.data.fetcher import (
    FMP_BASE_URL,
    DataFetchError,
    ProviderNotConfiguredError,
    ProviderPlanError,
)
from app.data.sec_edgar import edgar_get

logger = logging.getLogger(__name__)

LISTINGS_URL: str = "https://www.sec.gov/files/company_tickers_exchange.json"
SCREENER_EXCHANGES: tuple[str, ...] = ("NYSE", "NASDAQ", "AMEX")
"""Exchanges whose listings are read from FMP's company screener, one request each."""
SCREENER_LIMIT: int = 10_000
"""Rows asked of the company screener per exchange, more than any exchange lists."""
NASDAQ_SCREENER_URL: str = "https://api.nasdaq.com/api/screener/stocks"
"""Nasdaq's public stock screener feed, unlicensed: every US-listed stock's market cap,
sector and industry in one response."""
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
"""Tickers per yfinance download (development only); larger batches are throttled more often."""

PRICE_RETRIES: int = 2
"""Retries of a failed batch before its tickers are skipped."""

MARKET_CAP_WORKERS: int = 4
"""Concurrent market cap lookups; Yahoo throttles more than a few at a time."""

MARKET_CAP_ROUNDS: int = 3
"""Passes over lookups that failed, as opposed to those that found no market cap."""

MARKET_CAP_PAUSE_SECONDS: float = 60.0
"""Wait before retrying failed lookups, long enough for Yahoo's throttling to lift."""

FMP_TIMEOUT_SECONDS: float = 60.0
FMP_CONCURRENCY: int = 8
"""FMP requests in flight at once; the pacer bounds how many start each minute."""
FMP_RETRIES: int = 3
"""Retries of an FMP request that was throttled, failed on FMP's side or timed out."""
FMP_RETRY_PAUSE_SECONDS: float = 15.0
"""Wait before the first retry; later retries wait longer."""
PROGRESS_EVERY: int = 1_000
"""Per-stock requests between progress messages."""


T = TypeVar("T")


@dataclass(frozen=True)
class Profile:
    """A security's name, market cap and classification from a stock screener."""

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
    """``value`` as a positive, finite float, mapping blanks and other values to None."""
    try:
        number = float(str(value).replace("$", "").replace(",", ""))
    except ValueError:
        return None
    return number if math.isfinite(number) and number > 0 else None


def _text(value: object) -> str | None:
    """``value`` stripped, or ``None`` when blank or missing."""
    if value is None:
        return None
    return str(value).strip() or None


def parse_profiles(rows: object) -> dict[str, Profile]:
    """Read FMP company screener rows, keyed by ticker as EDGAR writes it (BRK-B)."""
    profiles: dict[str, Profile] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not row.get("symbol"):
            continue
        symbol = str(row["symbol"]).strip().upper().replace(".", "-").replace("/", "-")
        profiles[symbol] = Profile(
            name=_text(row.get("companyName")) or "",
            market_cap=_number(row.get("marketCap")),
            sector=_text(row.get("sector")),
            industry=_text(row.get("industry")),
        )
    return profiles


def parse_nasdaq_profiles(payload: dict[str, Any]) -> dict[str, Profile]:
    """Read Nasdaq's screener rows, keyed by ticker as EDGAR writes it (``BRK/B`` is BRK-B)."""
    rows: list[dict[str, Any]] = payload.get("data", {}).get("rows") or []
    profiles: dict[str, Profile] = {}
    for row in rows:
        if not row.get("symbol"):
            continue
        symbol = str(row["symbol"]).strip().upper().replace("/", "-")
        profiles[symbol] = Profile(
            name=_text(row.get("name")) or "",
            market_cap=_number(row.get("marketCap")),
            sector=_text(row.get("sector")),
            industry=_text(row.get("industry")),
        )
    return profiles


class RequestPacer:
    """Spaces request starts evenly so no more than ``per_minute`` start in any minute."""

    def __init__(self, per_minute: int) -> None:
        self._interval = 60.0 / per_minute
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        """Wait for the next free slot."""
        async with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self._interval
        if start > now:
            await asyncio.sleep(start - now)


class FmpSession:
    """FMP requests for one refresh, on the screener's key.

    Requests share one HTTP client, are paced to the plan's rate limit, and are retried
    when FMP throttles them, fails on its side or times out. Once FMP rejects the key,
    every later request fails at once rather than calling FMP again.
    """

    def __init__(
        self,
        api_key: str,
        requests_per_minute: int,
        client: httpx2.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._pacer = RequestPacer(requests_per_minute)
        self._slots = asyncio.Semaphore(FMP_CONCURRENCY)
        self._client = client or httpx2.AsyncClient(timeout=FMP_TIMEOUT_SECONDS)
        self._owns_client = client is None
        self._rejected: ProviderNotConfiguredError | None = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get(self, path: str, params: dict[str, str]) -> Any:
        """GET an FMP endpoint and return its JSON.

        Raises:
            ProviderNotConfiguredError: If FMP rejects the key.
            ProviderPlanError: If the plan does not cover the request.
            DataFetchError: If the request keeps failing.
        """
        what = f"{path} {params.get('symbol', '')}".strip()
        problem = "no response"
        for attempt in range(FMP_RETRIES + 1):
            if attempt:
                await asyncio.sleep(FMP_RETRY_PAUSE_SECONDS * attempt)
            if self._rejected is not None:
                raise self._rejected
            await self._pacer.wait()
            try:
                async with self._slots:
                    response = await self._client.get(
                        f"{FMP_BASE_URL}/{path}", params=params | {"apikey": self._api_key}
                    )
            except httpx2.TransportError as exc:
                problem = f"{type(exc).__name__}: {exc}"
                continue
            status = response.status_code
            if status in (401, 403):
                self._rejected = ProviderNotConfiguredError(
                    "Financial Modeling Prep rejected SCREENER_FMP_API_KEY."
                )
                raise self._rejected
            if status == 402:
                raise ProviderPlanError(f"The screener's FMP plan does not cover {what}.")
            if status == 429 or status >= 500:
                problem = f"HTTP {status}"
                continue
            if status >= 400:
                raise DataFetchError(f"FMP answered {what} with HTTP {status}.")
            try:
                return response.json()
            except ValueError as exc:
                raise DataFetchError(f"FMP answered {what} with unreadable JSON.") from exc
        raise DataFetchError(f"FMP {what} failed after {FMP_RETRIES + 1} attempts: {problem}.")


async def fetch_profiles(session: FmpSession | None) -> dict[str, Profile]:
    """Every US-listed stock's name, market cap, sector and industry from FMP, or without
    a session from Nasdaq's public feed.

    Raises:
        DataFetchError: If the screener cannot be read.
    """
    if session is None:
        return await _nasdaq_profiles()
    profiles: dict[str, Profile] = {}
    for exchange in SCREENER_EXCHANGES:
        rows = await session.get(
            "company-screener",
            {
                "exchange": exchange,
                "isEtf": "false",
                "isFund": "false",
                "limit": str(SCREENER_LIMIT),
            },
        )
        if isinstance(rows, list) and len(rows) >= SCREENER_LIMIT:
            logger.warning("FMP's company screener cut %s off at %d rows.", exchange, len(rows))
        for symbol, profile in parse_profiles(rows).items():
            profiles.setdefault(symbol, profile)
    return profiles


async def _nasdaq_profiles() -> dict[str, Profile]:
    """:func:`fetch_profiles` from Nasdaq's public feed, in one request."""
    try:
        async with httpx2.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0 (PyQxel)", "Accept": "application/json"},
            timeout=60.0,
        ) as client:
            response = await client.get(
                NASDAQ_SCREENER_URL, params={"tableonly": "true", "download": "true"}
            )
            response.raise_for_status()
            return parse_nasdaq_profiles(response.json())
    except (httpx2.HTTPError, ValueError) as exc:
        raise DataFetchError(f"Nasdaq's screener feed is unavailable: {exc}") from exc


async def _each_symbol(
    symbols: Iterable[str],
    fetch: Callable[[str], Awaitable[T | None]],
    what: str,
) -> dict[str, T]:
    """Run ``fetch`` for every symbol, concurrently, keeping the values it finds.

    A symbol whose request fails is left out and counted, so one bad symbol does not end
    the refresh; a rejected key does.

    Raises:
        ProviderNotConfiguredError: If FMP rejects the key.
    """
    pending = list(symbols)
    found: dict[str, T] = {}
    failures: list[DataFetchError] = []
    done = 0

    async def one(symbol: str) -> None:
        nonlocal done
        try:
            value = await fetch(symbol)
        except ProviderNotConfiguredError:
            raise
        except DataFetchError as exc:
            failures.append(exc)
            logger.debug("No %s for %s: %s", what, symbol, exc)
        else:
            if value is not None:
                found[symbol] = value
        done += 1
        if done % PROGRESS_EVERY == 0:
            logger.info("Fetched %s for %d of %d symbols.", what, done, len(pending))

    results = await asyncio.gather(*(one(symbol) for symbol in pending), return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result
    if failures:
        logger.warning(
            "No %s for %d symbols whose requests failed, e.g. %s", what, len(failures), failures[0]
        )
    return found


def price_frame(rows: object) -> pd.DataFrame | None:
    """Daily adjusted closes and volumes from FMP's dividend-adjusted bars, oldest first."""
    if not isinstance(rows, list) or not rows:
        return None
    table = pd.DataFrame([row for row in rows if isinstance(row, dict)])
    if not {"date", "adjClose", "volume"} <= set(table.columns):
        return None
    frame = pd.DataFrame(
        {
            "Close": pd.to_numeric(table["adjClose"], errors="coerce").to_numpy(),
            "Volume": pd.to_numeric(table["volume"], errors="coerce").to_numpy(),
        },
        index=pd.DatetimeIndex(pd.to_datetime(table["date"])),
    ).sort_index()
    frame = frame[~frame.index.duplicated(keep="last")].dropna(subset=["Close"])
    return None if frame.empty else frame


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


async def download_prices(
    symbols: Iterable[str], start: date, session: FmpSession | None
) -> dict[str, pd.DataFrame]:
    """Download daily adjusted closes and volumes since ``start`` for every symbol.

    From FMP one symbol at a time, or without a session from yfinance in batches.
    Symbols whose requests keep failing are left out, so they do not end the refresh.

    Returns:
        A frame with ``Close`` and ``Volume`` columns per symbol that has data.

    Raises:
        ProviderNotConfiguredError: If FMP rejects the key.
    """
    if session is not None:
        window = {"from": start.isoformat()}

        async def fetch(symbol: str) -> pd.DataFrame | None:
            rows = await session.get(
                "historical-price-eod/dividend-adjusted", window | {"symbol": symbol}
            )
            return price_frame(rows)

        return await _each_symbol(symbols, fetch, "prices")
    return await _yfinance_prices(symbols, start)


async def _yfinance_prices(symbols: Iterable[str], start: date) -> dict[str, pd.DataFrame]:
    """:func:`download_prices` from yfinance, for development; failing batches are skipped."""
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


async def fetch_market_caps(symbols: Iterable[str], session: FmpSession | None) -> dict[str, float]:
    """Market caps, counting every share class, from FMP company profiles.

    Without a session they come from yfinance, a few at a time in the order given, so the
    most important symbols should come first. Symbols whose lookups keep failing, or
    without a market cap, are left out.

    Raises:
        ProviderNotConfiguredError: If FMP rejects the key.
    """
    if session is not None:

        async def fetch(symbol: str) -> float | None:
            payload = await session.get("profile", {"symbol": symbol})
            if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
                return None
            return _number(payload[0].get("marketCap"))

        return await _each_symbol(symbols, fetch, "market caps")
    return await _yfinance_market_caps(symbols)


async def _yfinance_market_caps(symbols: Iterable[str]) -> dict[str, float]:
    """:func:`fetch_market_caps` from yfinance, for development.

    Failed lookups are retried after a pause, since Yahoo throttles long runs of them.
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
