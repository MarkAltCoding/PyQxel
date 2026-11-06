"""Daily Treasury bill rates from the US Treasury, the risk-free rate cash earns.

The Treasury publishes the rates of its bills every business day, as one CSV file per year
since 2002. They are a work of the US government, so in the public domain, and need no
key: every account is served the same copy. They are used for the interest cash earns in a
backtest, and replace Ken French's one-month rate (:mod:`app.data.factors`), which is a
month or more behind and whose library states no license, from 2002 on.

The 4-week bill is used, the nearest to Ken French's one-month bill. Its rate is given as
a coupon equivalent, an annual yield, which is spread over the business days of a year
(:data:`BUSINESS_DAYS`) as a daily return.
"""

import asyncio
import csv
import io
import json
import logging
import time
from datetime import date

import httpx2
import pandas as pd

from app.core.cache import cache_get, cache_set
from app.data.fetcher import DataFetchError

logger = logging.getLogger(__name__)

YEAR_URL: str = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all?type=daily_treasury_bill_rates"
    "&field_tdr_date_value={year}&page&_format=csv"
)
"""One year of daily Treasury bill rates, as CSV."""

FIRST_YEAR: int = 2002
"""The first year the Treasury publishes daily bill rates for."""

RATE_COLUMN: str = "4 WEEKS COUPON EQUIVALENT"
"""The 4-week bill's annual yield, in percent."""

BUSINESS_DAYS: int = 252
"""Business days a year, over which an annual yield is spread as daily returns."""

CURRENT_TTL_SECONDS: float = 6 * 60 * 60
"""How long this year's file is reused; a rate is added each business day."""

PAST_TTL_SECONDS: float = 30 * 24 * 60 * 60
"""How long a past year's file is reused; it no longer changes."""

HTTP_TIMEOUT_SECONDS: float = 30.0
CONCURRENT_YEARS: int = 4
"""Years fetched at once, to be gentle with the Treasury's site."""

_memory: dict[int, tuple[float, dict[str, float]]] = {}
"""Year -> (time fetched, ISO date -> annual yield in percent)."""


def parse_bill_rates(text: str) -> dict[str, float]:
    """The 4-week bill's annual yield, in percent, on each date of one year's CSV; dates
    without one are left out.

    Raises:
        DataFetchError: If ``text`` has rows but no 4-week bill column.
    """
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        return {}
    if RATE_COLUMN not in reader.fieldnames:
        raise DataFetchError("The Treasury's bill rates have no 4-week bill column.")
    rates: dict[str, float] = {}
    for row in reader:
        try:
            month, day, year = (int(part) for part in str(row.get("Date") or "").split("/"))
            rate = float(row.get(RATE_COLUMN) or "")
        except ValueError:
            continue
        rates[date(year, month, day).isoformat()] = rate
    return rates


async def _year(year: int, client: httpx2.AsyncClient) -> dict[str, float]:
    """One year's rates, from memory, Redis or the Treasury."""
    ttl = CURRENT_TTL_SECONDS if year >= date.today().year else PAST_TTL_SECONDS
    remembered = _memory.get(year)
    if remembered is not None and time.monotonic() - remembered[0] < ttl:
        return remembered[1]
    key = f"treasury:bills:{year}"
    cached = await cache_get(key)
    if cached is not None:
        try:
            rates: dict[str, float] = json.loads(cached)
            _memory[year] = (time.monotonic(), rates)
            return rates
        except ValueError:
            logger.warning("Ignoring unreadable cached Treasury bill rates for %d.", year)
    try:
        response = await client.get(YEAR_URL.format(year=year))
        response.raise_for_status()
    except httpx2.HTTPError as exc:
        raise DataFetchError(f"The Treasury's bill rates for {year} could not be fetched.") from exc
    rates = parse_bill_rates(response.text)
    _memory[year] = (time.monotonic(), rates)
    await cache_set(key, json.dumps(rates), ttl)
    return rates


async def fetch_bill_returns(start: date) -> pd.Series:
    """Daily returns of holding 4-week Treasury bills, as decimals, on each business day
    from ``start`` (or 2002) to the latest the Treasury has published.

    Raises:
        DataFetchError: If a year's rates cannot be fetched or read.
    """
    first = max(start.year, FIRST_YEAR)
    years = list(range(first, date.today().year + 1))
    slots = asyncio.Semaphore(CONCURRENT_YEARS)

    async with httpx2.AsyncClient(
        timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True, headers={"User-Agent": "PyQxel"}
    ) as client:

        async def one(year: int) -> dict[str, float]:
            async with slots:
                return await _year(year, client)

        found = await asyncio.gather(*(one(year) for year in years))
    rates = {day: rate for year in found for day, rate in year.items()}
    if not rates:
        raise DataFetchError("The Treasury published no bill rates for the window.")
    annual = pd.Series(rates, dtype=float)
    annual.index = pd.DatetimeIndex(pd.to_datetime(annual.index))
    annual = annual.sort_index()
    daily = (1.0 + annual / 100.0) ** (1.0 / BUSINESS_DAYS) - 1.0
    return daily[daily.index >= pd.Timestamp(start)]


def with_bill_rates(factors: pd.DataFrame | None, bills: pd.Series) -> pd.DataFrame:
    """``factors`` with its risk-free column ``RF`` taken from ``bills`` wherever the
    Treasury published a rate, and extended past the factors' last day to the bills' last;
    only ``RF`` when there are no factors. Ken French's rate is kept before 2002."""
    from app.models.factors import RISK_FREE  # Kept beside the factor names.

    if factors is None:
        return pd.DataFrame({RISK_FREE: bills})
    index = pd.DatetimeIndex(factors.index)
    combined = factors.reindex(index.union(pd.DatetimeIndex(bills.index)))
    combined.loc[bills.index, RISK_FREE] = bills.to_numpy()
    return combined


def clear_memory() -> None:
    """Forget the rates kept in memory."""
    _memory.clear()
