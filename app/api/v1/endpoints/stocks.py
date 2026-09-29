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
from app.models.volatility import (
    GarchDistribution,
    VolatilityEstimate,
    VolatilityFit,
    VolatilityInterval,
    VolatilityModel,
    VolatilityPeriod,
)
from app.stats.ewma import DAILY_DECAY, WEEKLY_DECAY, fit_ewma
from app.stats.garch import ModelFitError, fit_garch
from app.stats.r_bridge import RUnavailableError
from app.stats.volatility import MAX_HORIZON, InsufficientDataError

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

PERIODS_PER_YEAR: dict[str, int] = {"1d": 252, "1wk": 52}
"""Bars per year for each volatility interval, used to annualize."""

EWMA_DECAY: dict[str, float] = {"1d": DAILY_DECAY, "1wk": WEEKLY_DECAY}
"""Default EWMA decay for each volatility interval."""

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


@router.get(
    "/{symbol}/volatility",
    response_model=VolatilityEstimate,
    summary="GARCH(1,1) or EWMA volatility estimate and forecast",
)
async def get_volatility(
    symbol: Symbol,
    model: Annotated[
        VolatilityModel,
        Query(description="``garch`` for GARCH(1,1), or ``ewma`` for short histories."),
    ] = "garch",
    period: Annotated[VolatilityPeriod, Query(description="Lookback window to fit.")] = "5y",
    interval: Annotated[VolatilityInterval, Query(description="Bar size of the returns.")] = "1d",
    horizon: Annotated[
        int, Query(ge=1, le=MAX_HORIZON, description="Bars ahead to forecast.")
    ] = 10,
    distribution: Annotated[
        GarchDistribution,
        Query(description="GARCH only: innovation distribution, ``norm`` or Student t ``std``."),
    ] = "std",
    decay: Annotated[
        float | None,
        Query(
            ge=0.5,
            lt=1.0,
            description="EWMA only: weight kept by the previous variance. Defaults to 0.94 "
            "(RiskMetrics) for daily bars and 0.97 for weekly bars.",
        ),
    ] = None,
) -> VolatilityEstimate:
    """Estimate ``symbol``'s volatility from adjusted log returns and forecast it.

    GARCH(1,1) is estimated in R and needs at least 480 returns, with 1,000 or more
    recommended. EWMA fixes its parameters instead of estimating them, so it works on
    short histories such as recent listings, but its forecast does not mean-revert.

    Volatilities are annualized decimals (0.25 = 25%). Estimates that may be unreliable
    are returned with ``fit.warnings``. Returns 422 when the window has too few bars or
    the model cannot be fit, and 503 when R is unavailable for GARCH.
    """
    try:
        frame = await fetch_price_history(symbol, period=period, interval=interval)
    except DataFetchError as exc:
        raise _upstream_error(exc) from exc
    symbol = symbol.upper()
    coverage, notice = _coverage(symbol, period, interval, frame)
    periods_per_year = PERIODS_PER_YEAR[interval]
    closes = frame["Close"]
    fit: VolatilityFit
    try:
        if model == "ewma":
            fit = fit_ewma(
                closes,
                periods_per_year,
                horizon=horizon,
                decay=EWMA_DECAY[interval] if decay is None else decay,
            )
        else:
            fit = await fit_garch(
                closes, periods_per_year, horizon=horizon, distribution=distribution
            )
    except (InsufficientDataError, ModelFitError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except RUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return VolatilityEstimate(
        symbol=symbol,
        period=period,
        interval=interval,
        periods_per_year=periods_per_year,
        fit=fit,
        coverage=coverage,
        notice=notice,
    )
