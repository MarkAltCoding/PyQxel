"""Tests for downloading and parsing Fama-French factors from Ken French's data library.

The library is replaced with an ``httpx.MockTransport`` serving small files in the
real format, so no test touches the network.
"""

import io
import math
import zipfile
from collections import Counter
from collections.abc import Callable

import httpx
import pandas as pd
import pytest
from fakeredis import FakeAsyncRedis

from app.core.cache import KEY_PREFIX, configure_cache
from app.data import factors
from app.data.factors import (
    FactorDataError,
    clear_factor_memory,
    fetch_factor_file,
    fetch_factors,
    parse_factor_csv,
)
from app.data.fetcher import DataFetchError

pytestmark = pytest.mark.asyncio

DESCRIPTION = (
    "This file was created by using the 202608 CRSP database.\r\n"
    "The Tbill return is the simple daily rate that, over the number of trading days\r\n"
    "compounds to 1-month TBill rate.\r\n"
    "\r\n"
)
FOOTER = "\r\nCopyright 2026 Eugene F. Fama and Kenneth R. French\r\n"

FF3_CSV = (
    DESCRIPTION
    + ",Mkt-RF,SMB,HML,RF\r\n"
    + "20260826,    0.50,   -0.20,    0.10,    0.02\r\n"
    + "20260827,   -1.25,    0.30,   -0.05,    0.02\r\n"
    + "20260828,   -0.34,   -0.51,    0.28,    0.01\r\n"
    + "20260831,   -0.33,   -0.02,   -0.39,    0.01\r\n"
    + FOOTER
)
FF5_CSV = (
    DESCRIPTION
    + ",Mkt-RF,SMB,HML,RMW,CMA,RF\r\n"
    + "20260827,   -1.25,    0.28,   -0.05,    0.40,    0.11,    0.02\r\n"
    + "20260828,   -0.34,   -0.37,    0.28,    1.66,    0.49,    0.01\r\n"
    + "20260831,   -0.33,   -0.29,   -0.39,   -1.09,    0.03,    0.01\r\n"
    + FOOTER
)
MOMENTUM_CSV = (
    "This file was created by using the 202608 CRSP database.\r\n"
    "Missing data are indicated by -99.99 or -999.\r\n"
    "\r\n"
    ",Mom   \r\n"
    "20260827,   0.75\r\n"
    "20260828,  -1.47\r\n"
    "20260831, -99.99\r\n" + FOOTER
)


def _zip(text: str, name: str = "factors.csv") -> bytes:
    """Zip ``text`` as the library does, one CSV per archive."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, text)
    return buffer.getvalue()


FILES: dict[str, bytes] = {
    "F-F_Research_Data_Factors_daily_CSV.zip": _zip(FF3_CSV),
    "F-F_Research_Data_5_Factors_2x3_daily_CSV.zip": _zip(FF5_CSV),
    "F-F_Momentum_Factor_daily_CSV.zip": _zip(MOMENTUM_CSV),
}


def _library(
    respond: Callable[[str, int], httpx.Response] | None = None,
) -> tuple[httpx.AsyncClient, Counter[str]]:
    """A client for a fake library, and a count of requests per file name.

    ``respond`` may answer a request itself, given the file name and how many times
    it was asked for before; returning ``None`` serves the file normally.
    """
    requests: Counter[str] = Counter()

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        requests[name] += 1
        if respond is not None and (custom := respond(name, requests[name])) is not None:
            return custom
        return httpx.Response(200, content=FILES[name])

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), requests


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry failed downloads at once."""
    monkeypatch.setattr(factors, "RETRY_DELAY_SECONDS", 0.0)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """A wall clock the test can move forward."""
    now = [1_800_000_000.0]
    monkeypatch.setattr(factors.time, "time", lambda: now[0])
    return now


async def test_parse_turns_percent_rows_into_decimal_returns() -> None:
    """The description and footer are skipped and percents become decimals by date."""
    frame = parse_factor_csv(FF3_CSV)

    assert list(frame.columns) == ["Mkt-RF", "SMB", "HML", "RF"]
    assert list(frame.index.strftime("%Y-%m-%d")) == [
        "2026-08-26",
        "2026-08-27",
        "2026-08-28",
        "2026-08-31",
    ]
    assert frame.loc["2026-08-27", "Mkt-RF"] == pytest.approx(-0.0125)
    assert frame.loc["2026-08-31", "RF"] == pytest.approx(0.0001)


async def test_parse_marks_missing_values_and_trims_names() -> None:
    """``-99.99`` and ``-999`` become NaN, and padded header names are trimmed."""
    frame = parse_factor_csv(MOMENTUM_CSV.replace("20260828,  -1.47", "20260828, -999"))

    assert list(frame.columns) == ["Mom"]
    assert frame["Mom"].isna().tolist() == [False, True, True]


async def test_parse_stops_at_the_end_of_the_daily_table() -> None:
    """Anything after the first non-data line, like an annual table, is ignored."""
    text = FF3_CSV.replace(
        FOOTER, "\r\n Annual Factors: January-December\r\n,Mkt-RF\r\n1927, 29\r\n"
    )

    assert len(parse_factor_csv(text)) == 4


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (DESCRIPTION + FOOTER, "no header"),
        (DESCRIPTION + ",Mkt-RF,RF\r\n" + FOOTER, "no daily rows"),
        (DESCRIPTION + ",Mkt-RF,RF\r\n20260831, 0.1\r\n", "do not match"),
    ],
)
async def test_parse_rejects_malformed_files(text: str, message: str) -> None:
    """Files without a header, rows, or with ragged rows are factor data errors."""
    with pytest.raises(FactorDataError, match=message):
        parse_factor_csv(text)


@pytest.mark.parametrize(
    ("model", "columns", "rows"),
    [
        ("ff3", ["Mkt-RF", "SMB", "HML", "RF"], 4),
        ("ff5", ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"], 3),
        ("carhart4", ["Mkt-RF", "SMB", "HML", "Mom", "RF"], 2),
    ],
)
async def test_models_combine_their_files(model: str, columns: list[str], rows: int) -> None:
    """Each model gets its factors then RF, on dates where every column has a value.

    Momentum covers only the last three dates and is missing on the last, so the
    four-factor model keeps two.
    """
    client, _ = _library()

    frame = await fetch_factors(model, client)  # type: ignore[arg-type]

    assert list(frame.columns) == columns
    assert len(frame) == rows
    assert not frame.isna().any().any()
    assert frame.index.is_monotonic_increasing


async def test_download_is_reused_within_a_day(clock: list[float]) -> None:
    """A file is downloaded once and reused until it is a day old."""
    client, requests = _library()

    await fetch_factors("ff3", client)
    await fetch_factors("carhart4", client)
    clock[0] += factors.FRESH_SECONDS - 1
    await fetch_factor_file("ff3", client)
    assert requests["F-F_Research_Data_Factors_daily_CSV.zip"] == 1

    clock[0] += 2
    await fetch_factor_file("ff3", client)
    assert requests["F-F_Research_Data_Factors_daily_CSV.zip"] == 2


async def test_redis_shares_downloads_between_processes(clock: list[float]) -> None:
    """A copy stored in Redis serves a process that has none in memory."""
    redis = FakeAsyncRedis(decode_responses=True)
    configure_cache(redis)
    client, requests = _library()

    first = await fetch_factor_file("momentum", client)
    clear_factor_memory()
    second = await fetch_factor_file("momentum", client)

    assert requests["F-F_Momentum_Factor_daily_CSV.zip"] == 1
    pd.testing.assert_frame_equal(second, first)
    ttl = await redis.pttl(KEY_PREFIX + "factors:momentum")
    assert 0 < ttl <= factors.STALE_SECONDS * 1000


async def test_unreadable_redis_copy_is_replaced(clock: list[float]) -> None:
    """A corrupt copy in Redis is ignored and a fresh download stored over it."""
    redis = FakeAsyncRedis(decode_responses=True)
    configure_cache(redis)
    await redis.set(KEY_PREFIX + "factors:ff3", "{broken")
    client, requests = _library()

    frame = await fetch_factor_file("ff3", client)

    assert len(frame) == 4
    assert requests["F-F_Research_Data_Factors_daily_CSV.zip"] == 1
    assert await redis.get(KEY_PREFIX + "factors:ff3") != "{broken"


async def test_transient_failures_are_retried() -> None:
    """Server errors and dropped connections are retried before giving up."""

    def flaky(name: str, attempt: int) -> httpx.Response | None:
        if attempt == 1:
            return httpx.Response(503)
        if attempt == 2:
            raise httpx.ConnectError("connection reset")
        return None

    client, requests = _library(flaky)

    frame = await fetch_factor_file("ff3", client)

    assert len(frame) == 4
    assert requests["F-F_Research_Data_Factors_daily_CSV.zip"] == 3


@pytest.mark.parametrize(
    ("status", "attempts"),
    [(503, factors.MAX_RETRIES + 1), (404, 1)],
)
async def test_failed_download_without_a_copy_is_a_clear_error(status: int, attempts: int) -> None:
    """With nothing stored, a failed download names the library; only 5xx is retried."""
    client, requests = _library(lambda name, attempt: httpx.Response(status))

    with pytest.raises(FactorDataError, match="Ken French's data library") as caught:
        await fetch_factor_file("ff5", client)

    assert isinstance(caught.value, DataFetchError)
    assert requests["F-F_Research_Data_5_Factors_2x3_daily_CSV.zip"] == attempts


@pytest.mark.parametrize(
    "content",
    [b"<html>Maintenance</html>", _zip("a,b\r\n", "readme.txt"), _zip(DESCRIPTION + FOOTER)],
)
async def test_unusable_download_is_a_clear_error(content: bytes) -> None:
    """A page that is not a zip, a zip without a CSV, or an empty table is an error."""
    client, _ = _library(lambda name, attempt: httpx.Response(200, content=content))

    with pytest.raises(FactorDataError):
        await fetch_factor_file("ff3", client)


async def test_file_missing_expected_columns_is_rejected() -> None:
    """A file whose header lacks a factor the model needs is rejected, not misread."""
    renamed = _zip(FF3_CSV.replace(",Mkt-RF,SMB,HML,RF", ",Mkt-RF,SMB,Value,RF"))
    client, _ = _library(lambda name, attempt: httpx.Response(200, content=renamed))

    with pytest.raises(FactorDataError, match="missing columns"):
        await fetch_factor_file("ff3", client)


async def test_stale_copy_is_served_when_the_library_is_down(clock: list[float]) -> None:
    """After a day, a failed refresh falls back to the stored copy, for up to 30 days."""
    down = [False]
    client, requests = _library(lambda name, attempt: httpx.Response(503) if down[0] else None)
    original = await fetch_factor_file("ff3", client)

    down[0] = True
    clock[0] += factors.FRESH_SECONDS + 1
    stale = await fetch_factor_file("ff3", client)
    pd.testing.assert_frame_equal(stale, original)

    clock[0] = clock[0] - factors.FRESH_SECONDS - 1 + factors.STALE_SECONDS + 1
    with pytest.raises(FactorDataError):
        await fetch_factor_file("ff3", client)


async def test_missing_values_stay_out_of_the_model() -> None:
    """Dates with a missing factor never reach a model, so no NaN is regressed on."""
    client, _ = _library()

    frame = await fetch_factors("carhart4", client)

    assert all(math.isfinite(value) for value in frame.to_numpy().ravel())
    assert pd.Timestamp("2026-08-31") not in frame.index
