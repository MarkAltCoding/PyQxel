"""Stock quote and price history routes."""

import math
from typing import Annotated

import pandas as pd
from fastapi import APIRouter, HTTPException, Path, Query, status

from app.data.fetcher import (
    DataFetchError,
    SymbolNotFoundError,
    fetch_price_history,
    fetch_ticker_info,
)
from app.models.stock import (
    HistoryCoverage,
    HistoryInterval,
    HistoryPeriod,
    OHLCVBar,
    PriceHistory,
    TickerInfo,
)

router = APIRouter()

SYMBOL_PATTERN: str = r"^[A-Za-z0-9.\-^=]{1,15}$"
"""Letters, digits and the ``.``, ``-``, ``^``, ``=`` used by class shares, indices and FX."""

PERIOD_OFFSETS: dict[str, pd.DateOffset] = {
    "1mo": pd.DateOffset(months=1),
    "3mo": pd.DateOffset(months=3),
    "6mo": pd.DateOffset(months=6),
    "1y": pd.DateOffset(years=1),
    "2y": pd.DateOffset(years=2),
    "5y": pd.DateOffset(years=5),
    "10y": pd.DateOffset(years=10),
}
"""Calendar length of each period. ``1d`` and ``5d`` count trading days and ``max`` is open-ended."""

COVERAGE_TOLERANCE: pd.Timedelta = pd.Timedelta(days=7)
"""Slack between the window start and the first bar, absorbing weekends and market holidays."""

Symbol = Annotated[
    str,
    Path(pattern=SYMBOL_PATTERN, description="Ticker symbol, e.g. AAPL, BRK-B or ^GSPC."),
]


def _optional_float(value: object) -> float | None:
    """Return ``value`` as a float, mapping missing or NaN values to ``None``."""
    if value is None:
        return None
    number = float(value)  # type: ignore[arg-type]
    return None if math.isnan(number) else number


def _optional_int(value: object) -> int | None:
    """Return ``value`` rounded to an int, mapping missing or NaN values to ``None``."""
    number = _optional_float(value)
    return None if number is None else round(number)


def _frame_to_bars(frame: pd.DataFrame) -> list[OHLCVBar]:
    """Convert a cleaned OHLCV frame from the fetcher into response bars."""
    return [
        OHLCVBar(
            timestamp=pd.Timestamp(index).to_pydatetime(),
            open=_optional_float(row.Open),
            high=_optional_float(row.High),
            low=_optional_float(row.Low),
            close=float(row.Close),
            volume=_optional_int(row.Volume),
        )
        for index, row in zip(frame.index, frame.itertuples(index=False))
    ]


def _requested_start(period: HistoryPeriod, now: pd.Timestamp) -> pd.Timestamp | None:
    """Return the calendar start of a ``period`` window ending at ``now``.

    Returns ``None`` for periods whose start cannot be compared against bar dates.
    """
    if period == "ytd":
        return now.normalize().replace(month=1, day=1)
    offset = PERIOD_OFFSETS.get(period)
    return None if offset is None else now - offset


def _coverage(
    symbol: str, period: HistoryPeriod, interval: HistoryInterval, frame: pd.DataFrame
) -> tuple[HistoryCoverage, str | None]:
    """Classify how much of the requested window ``frame`` covers, with a note on any gap."""
    if frame.empty:
        return "none", (
            f"No price data exists for {symbol} in the requested {period} window "
            f"at {interval} bars."
        )
    first = pd.Timestamp(frame.index[0])
    start = _requested_start(period, pd.Timestamp.now(tz=first.tz))
    if start is None or first - start <= COVERAGE_TOLERANCE:
        return "full", None
    return "partial", (
        f"No price data exists for {symbol} before {first.date()}; the requested {period} "
        f"window starts {start.date()}. Showing all available bars."
    )


def _upstream_error(exc: DataFetchError) -> HTTPException:
    """Translate a provider failure into a 404 for unknown symbols, otherwise a 502."""
    if isinstance(exc, SymbolNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.get("/{symbol}", response_model=TickerInfo, summary="Ticker snapshot")
async def get_ticker_info(symbol: Symbol) -> TickerInfo:
    """Return descriptive and pricing information for ``symbol``."""
    try:
        return await fetch_ticker_info(symbol)
    except DataFetchError as exc:
        raise _upstream_error(exc) from exc


@router.get("/{symbol}/history", response_model=PriceHistory, summary="Adjusted OHLCV history")
async def get_price_history(
    symbol: Symbol,
    period: Annotated[HistoryPeriod, Query(description="Lookback window.")] = "1y",
    interval: Annotated[HistoryInterval, Query(description="Bar size.")] = "1d",
) -> PriceHistory:
    """Return split- and dividend-adjusted OHLCV bars for ``symbol``, oldest first.

    Parts of the window without data are described in ``coverage`` and ``notice`` rather
    than treated as errors. Only a symbol that no provider recognizes returns 404.
    """
    try:
        frame = await fetch_price_history(symbol, period=period, interval=interval)
    except DataFetchError as exc:
        raise _upstream_error(exc) from exc
    symbol = symbol.upper()
    coverage, notice = _coverage(symbol, period, interval, frame)
    return PriceHistory(
        symbol=symbol,
        period=period,
        interval=interval,
        bars=_frame_to_bars(frame),
        coverage=coverage,
        notice=notice,
    )
