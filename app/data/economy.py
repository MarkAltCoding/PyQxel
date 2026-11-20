"""The economy's backdrop from public US government sources, with no key.

* **The Treasury yield curve:** the Treasury's daily par yields, from one month to thirty
  years, one CSV file a year, the same site :mod:`app.data.treasury` reads bill rates
  from.
* **Inflation and jobs:** the Bureau of Labor Statistics' consumer prices (all items, and
  core, without food and energy), the unemployment rate, and payroll employment, from its
  public API's first version, which needs no key and answers 25 queries a day from one
  address; one query asks for every series at once, and the answer is kept for twelve
  hours.

Both are works of the US government, in the public domain, so free to use in a paid
product, and every account is served the same copy.
"""

import csv
import io
import json
import logging
import time
from datetime import date
from typing import Any

import httpx2

from app.core.cache import cache_get, cache_set
from app.data.fetcher import DataFetchError

logger = logging.getLogger(__name__)

YIELD_CURVE_URL: str = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve"
    "&field_tdr_date_value={year}&page&_format=csv"
)
"""One year of the Treasury's daily par yield curve, as CSV."""

BLS_URL: str = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
"""The Bureau of Labor Statistics' public API, first version, which needs no key."""

BLS_SERIES: dict[str, str] = {
    "cpi": "CUUR0000SA0",
    "core_cpi": "CUUR0000SA0L1E",
    "unemployment": "LNS14000000",
    "payrolls": "CES0000000001",
}
"""Each figure's series: consumer prices for all urban consumers (all items, and less food
and energy, not seasonally adjusted, as the yearly rate is quoted), the unemployment rate
(seasonally adjusted), and total nonfarm payrolls in thousands (seasonally adjusted)."""

CURVE_TTL_SECONDS: float = 6 * 60 * 60
"""How long this year's yield curve file is reused; a row is added each business day."""
PAST_CURVE_TTL_SECONDS: float = 30 * 24 * 60 * 60
"""How long last year's file is reused; it no longer changes."""
BLS_TTL_SECONDS: float = 12 * 60 * 60
"""How long the BLS figures are reused: two of its 25 daily queries at most."""
HTTP_TIMEOUT_SECONDS: float = 30.0

_memory: dict[str, tuple[float, Any]] = {}
"""Cache key -> (time fetched, parsed answer)."""


async def _cached(key: str, ttl: float) -> Any:
    remembered = _memory.get(key)
    if remembered is not None and time.monotonic() - remembered[0] < ttl:
        return remembered[1]
    text = await cache_get(key)
    if text is None:
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    _memory[key] = (time.monotonic(), value)
    return value


async def _keep(key: str, value: Any, ttl: float) -> None:
    _memory[key] = (time.monotonic(), value)
    await cache_set(key, json.dumps(value), ttl)


def parse_yield_curve(text: str) -> dict[str, dict[str, float]]:
    """Each date's yields in one year's CSV, as ISO date -> tenor (``1 Mo``, ``10 Yr``) ->
    percent; tenors without a yield that day are left out."""
    curve: dict[str, dict[str, float]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        try:
            month, day, year = (int(part) for part in str(row.get("Date") or "").split("/"))
        except ValueError:
            continue
        yields: dict[str, float] = {}
        for tenor, value in row.items():
            if tenor == "Date" or not value:
                continue
            try:
                yields[tenor] = float(value)
            except ValueError:
                continue
        if yields:
            curve[date(year, month, day).isoformat()] = yields
    return curve


async def fetch_yield_curve(today: date) -> dict[str, dict[str, float]]:
    """The daily yield curve of this year and last, as :func:`parse_yield_curve` gives it.

    Raises:
        DataFetchError: If the Treasury cannot be reached.
    """
    curve: dict[str, dict[str, float]] = {}
    async with httpx2.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        for year in (today.year - 1, today.year):
            ttl = CURVE_TTL_SECONDS if year >= today.year else PAST_CURVE_TTL_SECONDS
            key = f"treasury:curve:{year}"
            found = await _cached(key, ttl)
            if found is None:
                try:
                    response = await client.get(YIELD_CURVE_URL.format(year=year))
                    response.raise_for_status()
                except httpx2.HTTPError as exc:
                    raise DataFetchError(
                        f"The Treasury's yield curve for {year} could not be fetched."
                    ) from exc
                found = parse_yield_curve(response.text)
                await _keep(key, found, ttl)
            curve.update(found)
    return curve


def parse_bls(payload: Any) -> dict[str, dict[str, float]]:
    """Each figure of :data:`BLS_SERIES` by month, as name -> ``YYYY-MM`` -> value.

    Raises:
        DataFetchError: If the BLS refused the query.
    """
    if not isinstance(payload, dict) or payload.get("status") != "REQUEST_SUCCEEDED":
        message = payload.get("message") if isinstance(payload, dict) else None
        raise DataFetchError(f"The Bureau of Labor Statistics refused the query: {message}")
    names = {series: name for name, series in BLS_SERIES.items()}
    figures: dict[str, dict[str, float]] = {}
    for series in payload.get("Results", {}).get("series", []):
        name = names.get(series.get("seriesID"))
        if name is None:
            continue
        months: dict[str, float] = {}
        for point in series.get("data", []):
            period = str(point.get("period", ""))
            if not period.startswith("M") or period == "M13":
                continue
            try:
                months[f"{point['year']}-{period[1:]}"] = float(point["value"])
            except KeyError, ValueError:
                continue
        figures[name] = months
    return figures


async def fetch_bls(today: date) -> dict[str, dict[str, float]]:
    """Two years of each figure of :data:`BLS_SERIES`, as :func:`parse_bls` gives them.

    Raises:
        DataFetchError: If the BLS cannot be reached or refuses the query.
    """
    key = f"bls:figures:{today.year}"
    found = await _cached(key, BLS_TTL_SECONDS)
    if found is not None:
        return dict(found)
    query = {
        "seriesid": list(BLS_SERIES.values()),
        "startyear": str(today.year - 2),
        "endyear": str(today.year),
    }
    try:
        async with httpx2.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            response = await client.post(BLS_URL, json=query)
            response.raise_for_status()
            payload = response.json()
    except (httpx2.HTTPError, ValueError) as exc:
        raise DataFetchError("The Bureau of Labor Statistics could not be reached.") from exc
    figures = parse_bls(payload)
    await _keep(key, figures, BLS_TTL_SECONDS)
    return figures


def clear_memory() -> None:
    """Forget remembered answers (for tests)."""
    _memory.clear()
