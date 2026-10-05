"""Daily Fama-French factor returns from Ken French's data library.

The library publishes each factor set as a zipped CSV: a free-text description, a
header row beginning with a comma, ``YYYYMMDD`` rows of percent returns, and a
copyright footer. Missing values are written ``-99.99`` or ``-999``. Files are
regenerated monthly from CRSP, so the latest row is usually a month or more behind.

Because the files change monthly, a downloaded copy is reused for
:data:`FRESH_SECONDS`, and an older copy is still served when a fresh download fails.
Copies are kept in process memory and, when ``REDIS_URL`` is set, in Redis so every
worker shares one download.
"""

import asyncio
import io
import json
import logging
import re
import time
import zipfile
from typing import Any, Literal

import httpx2
import numpy as np
import pandas as pd

from app.core.cache import cache_get, cache_set
from app.data.fetcher import DataFetchError
from app.models.factors import MODEL_FACTORS, RISK_FREE, FactorModel

logger = logging.getLogger(__name__)

FactorFile = Literal["ff3", "ff5", "momentum"]
"""Factor files downloaded from the library."""

LIBRARY_URL: str = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp"
FILE_NAMES: dict[FactorFile, str] = {
    "ff3": "F-F_Research_Data_Factors_daily_CSV.zip",
    "ff5": "F-F_Research_Data_5_Factors_2x3_daily_CSV.zip",
    "momentum": "F-F_Momentum_Factor_daily_CSV.zip",
}
FILE_COLUMNS: dict[FactorFile, list[str]] = {
    "ff3": ["Mkt-RF", "SMB", "HML", RISK_FREE],
    "ff5": ["Mkt-RF", "SMB", "HML", "RMW", "CMA", RISK_FREE],
    "momentum": ["Mom"],
}
"""Columns each file must contain."""

HTTP_TIMEOUT_SECONDS: float = 30.0
MAX_RETRIES: int = 2
"""Retries of a failed download before falling back to a stored copy."""
RETRY_DELAY_SECONDS: float = 1.0
"""Wait before the first retry; doubled for each later one."""
RETRY_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})

FRESH_SECONDS: float = 24 * 60 * 60
"""How long a downloaded file is used before the library is asked again."""
STALE_SECONDS: float = 30 * 24 * 60 * 60
"""How long a copy is kept as a fallback for when the library cannot be reached."""

MISSING_VALUES: tuple[float, ...] = (-99.99, -999.0)
_ROW = re.compile(r"^\s*\d{8}\s*,")

_memory: dict[FactorFile, tuple[float, pd.DataFrame]] = {}
"""Last good copy of each file, with the wall-clock time it was downloaded."""


class FactorDataError(DataFetchError):
    """Raised when factor data cannot be downloaded or parsed, and no stored copy exists."""


def parse_factor_csv(text: str) -> pd.DataFrame:
    """Parse one of the library's factor CSVs into a frame of decimal returns.

    Args:
        text: The CSV's contents, with any line endings.

    Returns:
        Returns as decimals (0.01 = 1%), one column per factor as named in the header,
        indexed by date, oldest first. Missing values are ``NaN``.

    Raises:
        FactorDataError: If no header row or no data rows are found.
    """
    lines = text.splitlines()
    header = next((i for i, line in enumerate(lines) if line.lstrip().startswith(",")), None)
    if header is None:
        raise FactorDataError("Factor file has no header row.")
    columns = [name.strip() for name in lines[header].split(",")[1:]]

    rows: list[list[str]] = []
    for line in lines[header + 1 :]:
        if not _ROW.match(line):
            # The first non-data line ends the daily table; footers and any annual
            # tables after it are ignored.
            break
        rows.append([cell.strip() for cell in line.split(",")])
    if not rows:
        raise FactorDataError("Factor file has no daily rows.")
    if any(len(row) != len(columns) + 1 for row in rows):
        raise FactorDataError("Factor file has rows that do not match its header.")

    index = pd.DatetimeIndex(pd.to_datetime([row[0] for row in rows], format="%Y%m%d"))
    values = np.array([row[1:] for row in rows], dtype=float)
    values[np.isin(values, MISSING_VALUES)] = np.nan
    frame = pd.DataFrame(values / 100.0, index=index, columns=columns)
    frame.index.name = "Date"
    frame = frame[~frame.index.duplicated(keep="last")]
    return frame.sort_index()


def _unzip_csv(content: bytes) -> str:
    """Return the text of the single CSV inside a downloaded zip archive."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(names) != 1:
                raise FactorDataError(f"Expected one CSV in the archive, found {len(names)}.")
            return archive.read(names[0]).decode("utf-8", errors="replace")
    except zipfile.BadZipFile as exc:
        raise FactorDataError("Factor download is not a valid zip archive.") from exc


def _validate(frame: pd.DataFrame, file: FactorFile) -> pd.DataFrame:
    """Check a parsed file has its expected columns, and keep only those."""
    expected = FILE_COLUMNS[file]
    missing = [column for column in expected if column not in frame.columns]
    if missing:
        raise FactorDataError(f"Factor file {file!r} is missing columns: {missing}.")
    return frame.loc[:, expected]


async def _download(file: FactorFile, client: httpx2.AsyncClient) -> pd.DataFrame:
    """Download and parse ``file``, retrying transient failures with backoff."""
    url = f"{LIBRARY_URL}/{FILE_NAMES[file]}"
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = await client.get(url, follow_redirects=True)
            if response.status_code not in RETRY_STATUSES:
                response.raise_for_status()
                return _validate(parse_factor_csv(_unzip_csv(response.content)), file)
            error: Exception = httpx2.HTTPStatusError(
                f"Ken French's data library returned {response.status_code}",
                request=response.request,
                response=response,
            )
        except httpx2.HTTPStatusError:
            raise
        except httpx2.TransportError as exc:
            error = exc
        if attempt == MAX_RETRIES:
            raise error
        delay = RETRY_DELAY_SECONDS * 2**attempt
        logger.warning("Factor download %s failed (%s); retrying in %.1fs", url, error, delay)
        await asyncio.sleep(delay)
    raise AssertionError("unreachable")


def _to_json(fetched_at: float, frame: pd.DataFrame) -> str:
    """Serialize a stored copy for Redis, with ``NaN`` as ``null``."""
    return json.dumps(
        {
            "fetched_at": fetched_at,
            "dates": [timestamp.strftime("%Y-%m-%d") for timestamp in frame.index],
            "columns": {
                column: [None if np.isnan(value) else value for value in frame[column]]
                for column in frame.columns
            },
        }
    )


def _from_json(text: str) -> tuple[float, pd.DataFrame]:
    """Rebuild a stored copy serialized by :func:`_to_json`."""
    payload: dict[str, Any] = json.loads(text)
    index = pd.DatetimeIndex(pd.to_datetime(payload["dates"], format="%Y-%m-%d"), name="Date")
    frame = pd.DataFrame(payload["columns"], index=index, dtype=float)
    return float(payload["fetched_at"]), frame


async def _stored_copy(file: FactorFile) -> tuple[float, pd.DataFrame] | None:
    """Return the newest stored copy of ``file`` from memory or Redis, if any."""
    copies = [_memory[file]] if file in _memory else []
    text = await cache_get(f"factors:{file}")
    if text is not None:
        try:
            copies.append(_from_json(text))
        except ValueError, KeyError, TypeError:
            logger.warning("Ignoring an unreadable cached copy of factor file %s.", file)
    return max(copies, key=lambda copy: copy[0], default=None)


async def fetch_factor_file(
    file: FactorFile, client: httpx2.AsyncClient | None = None
) -> pd.DataFrame:
    """Return one of the library's daily factor files as decimal returns.

    A copy downloaded within :data:`FRESH_SECONDS` is reused. Otherwise the file is
    downloaded; if that fails, a copy up to :data:`STALE_SECONDS` old is returned
    instead, with a warning logged.

    Args:
        file: ``"ff3"``, ``"ff5"`` or ``"momentum"``.
        client: Optional shared HTTP client. A short-lived client is created when omitted.

    Returns:
        The file's columns as decimals, indexed by date, oldest first.

    Raises:
        FactorDataError: If the download fails and no stored copy exists.
    """
    stored = await _stored_copy(file)
    now = time.time()
    if stored is not None and now - stored[0] < FRESH_SECONDS:
        _memory[file] = stored
        return stored[1]

    try:
        if client is not None:
            frame = await _download(file, client)
        else:
            async with httpx2.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as own_client:
                frame = await _download(file, own_client)
    except (httpx2.HTTPError, FactorDataError) as exc:
        if stored is not None and now - stored[0] < STALE_SECONDS:
            logger.warning(
                "Could not refresh factor file %s (%s); using the copy from %.1f days ago.",
                file,
                exc,
                (now - stored[0]) / 86_400,
            )
            _memory[file] = stored
            return stored[1]
        raise FactorDataError(
            f"Could not download the Fama-French {file} factors from Ken French's data "
            f"library: {exc}"
        ) from exc

    _memory[file] = (now, frame)
    await cache_set(f"factors:{file}", _to_json(now, frame), STALE_SECONDS)
    return frame


async def fetch_factors(
    model: FactorModel, client: httpx2.AsyncClient | None = None
) -> pd.DataFrame:
    """Return the daily factor returns of ``model`` and the risk-free rate.

    Args:
        model: ``"ff3"``, ``"carhart4"`` (three factors plus momentum) or ``"ff5"``.
        client: Optional shared HTTP client for the downloads.

    Returns:
        One column per factor of the model, in :data:`~app.models.factors.MODEL_FACTORS`
        order, then ``RF``, all as decimals. Only dates on which every column has a
        value are kept, oldest first.

    Raises:
        FactorDataError: If a file cannot be downloaded and no stored copy exists.
    """
    if model == "ff5":
        frame = await fetch_factor_file("ff5", client)
    else:
        frame = await fetch_factor_file("ff3", client)
        if model == "carhart4":
            momentum = await fetch_factor_file("momentum", client)
            frame = frame.join(momentum, how="inner")
    return frame.loc[:, [*MODEL_FACTORS[model], RISK_FREE]].dropna()


def clear_factor_memory() -> None:
    """Forget the in-memory copies, so the next request reads Redis or downloads."""
    _memory.clear()
