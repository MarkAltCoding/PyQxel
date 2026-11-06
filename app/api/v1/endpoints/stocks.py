"""Stock quote and price history routes."""

import math
from typing import Annotated, cast

import pandas as pd
from fastapi import APIRouter, HTTPException, Path, Query, status

from app.data.cash import is_money_market, money_market_message
from app.data.funds import fetch_fund_profile
from app.data.fetcher import (
    HISTORY_NOTICE,
    HISTORY_SHORTFALL,
    HISTORY_STALE,
    PROVIDER_NAMES,
    DataFetchError,
    Shortfall,
    keys_to_add,
    shortfall_text,
    ProviderNotConfiguredError,
    ProviderPlanError,
    SymbolNotFoundError,
    fetch_price_history,
    fetch_ticker_info,
)
from app.models.stock import (
    SYMBOL_PATTERN,
    FundProfile,
    HistoryCoverage,
    HistoryInterval,
    HistoryPeriod,
    OHLCVBar,
    PriceHistory,
    TickerInfo,
)
from app.models.usage import HistoryShortfall
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

PERIOD_OFFSETS: dict[str, pd.DateOffset] = {
    "1mo": pd.DateOffset(months=1),
    "3mo": pd.DateOffset(months=3),
    "6mo": pd.DateOffset(months=6),
    "1y": pd.DateOffset(years=1),
    "2y": pd.DateOffset(years=2),
    "5y": pd.DateOffset(years=5),
    "10y": pd.DateOffset(years=10),
}
"""Calendar length of each period.

``1d`` and ``5d`` count trading days and ``max`` is open-ended.
"""

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
            close=float(cast(float, row.Close)),
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


def history_coverage(
    symbol: str, period: HistoryPeriod, interval: HistoryInterval, frame: pd.DataFrame
) -> tuple[HistoryCoverage, str | None]:
    """Classify how much of the requested window ``frame`` covers, with a note on any gap.

    A provider's own note on the bars, such as a free plan serving only recent ones,
    explains the gap instead.
    """
    if frame.empty:
        return "none", (
            f"No price data exists for {symbol} in the requested {period} window "
            f"at {interval} bars."
        )
    provider_notice: str | None = frame.attrs.get(HISTORY_NOTICE)
    first = pd.Timestamp(frame.index[0])
    start = _requested_start(period, pd.Timestamp.now(tz=first.tz))
    if start is None or first - start <= COVERAGE_TOLERANCE:
        return "full", provider_notice
    return "partial", provider_notice or (
        f"No price data exists for {symbol} before {first.date()}; the requested {period} "
        f"window starts {start.date()}. Showing all available bars."
    )


def history_shortfalls(frame: pd.DataFrame) -> list[HistoryShortfall]:
    """Why each provider passed over did not serve ``frame``'s symbol a fuller history."""
    found: list[Shortfall] = frame.attrs.get(HISTORY_SHORTFALL) or []
    return [
        HistoryShortfall(
            provider=item.provider,
            name=PROVIDER_NAMES.get(item.provider, item.provider),
            reason=item.reason,
            resets_at=item.resets_at,
            message=item.message,
        )
        for item in found
    ]


def short_history(
    symbol: str, period: HistoryPeriod, interval: HistoryInterval, frame: pd.DataFrame
) -> str | None:
    """A plain warning when a plan served ``symbol`` less history than ``period`` asks for:
    which key ran out or which plan is short, when it resets, and what to do; ``None``
    when the bars span the window, or begin later only because the listing does.

    A stored copy that merely ends a few days early is not short, and says so in its own
    notice instead.
    """
    notice: str | None = frame.attrs.get(HISTORY_NOTICE)
    if frame.empty or notice is None or frame.attrs.get(HISTORY_STALE):
        return None
    coverage, _ = history_coverage(symbol, period, interval, frame)
    if coverage != "partial":
        return None
    first = pd.Timestamp(frame.index[0]).date()
    shortfalls: list[Shortfall] = frame.attrs.get(HISTORY_SHORTFALL) or []
    reasons = shortfall_text(shortfalls, symbol)
    limited = [item for item in shortfalls if item.reason == "limit"]
    steps: list[str] = []
    if limited:
        steps.append(
            "Wait until then and run it again for the full history, or run it now on the "
            "shorter one"
        )
    hint = keys_to_add("history", symbol)
    if hint:
        steps.append(hint.rstrip("."))
    if not steps:
        steps.append("Run it on the shorter history, or choose a shorter window")
    return (
        f"Only part of the {period} window: {symbol}'s prices begin "
        f"{first:%b} {first.day}, {first.year}. {notice}"
        + (f" {reasons}" if reasons else "")
        + f" What to do: {'; or '.join(steps)}."
    )


def upstream_error(exc: DataFetchError) -> HTTPException:
    """Translate a provider failure into an HTTP error.

    403 when the user has no usable FMP key, 402 when their FMP plan does not cover the
    request, 404 for unknown symbols, and 502 for other provider failures.
    """
    if isinstance(exc, ProviderNotConfiguredError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, ProviderPlanError):
        return HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(exc))
    if isinstance(exc, SymbolNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.get("/{symbol}", response_model=TickerInfo, summary="Ticker snapshot")
async def get_ticker_info(symbol: Symbol) -> TickerInfo:
    """Return descriptive and pricing information for ``symbol``."""
    try:
        return await fetch_ticker_info(symbol)
    except DataFetchError as exc:
        raise upstream_error(exc) from exc


@router.get("/{symbol}/fund", response_model=FundProfile, summary="A fund's holdings and costs")
async def get_fund_profile(symbol: Symbol) -> FundProfile:
    """Return what the ETF or mutual fund ``symbol`` holds and costs: its expense ratio,
    yield and turnover from Alpha Vantage on your key; its holdings, mix of stocks, bonds
    and cash, and a bond fund's coupon, maturity and issuers from its SEC filings; and the
    valuation, margins, growth, size and sectors weighted from the companies it holds, from
    your screener or Finnhub. ``notes`` says what is missing and why.

    Returns 403 when the SEC has no filing for ``symbol`` and you have no Alpha Vantage key,
    and 404 when no source describes it as a fund, as for a stock.
    """
    try:
        return await fetch_fund_profile(symbol)
    except DataFetchError as exc:
        raise upstream_error(exc) from exc


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
        raise upstream_error(exc) from exc
    symbol = symbol.upper()
    coverage, notice = history_coverage(symbol, period, interval, frame)
    return PriceHistory(
        symbol=symbol,
        period=period,
        interval=interval,
        bars=_frame_to_bars(frame),
        coverage=coverage,
        notice=notice,
        short_history=short_history(symbol, period, interval, frame),
        shortfall=history_shortfalls(frame),
        stale=bool(frame.attrs.get(HISTORY_STALE)),
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
        raise upstream_error(exc) from exc
    symbol = symbol.upper()
    coverage, notice = history_coverage(symbol, period, interval, frame)
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
        detail = money_market_message(symbol) if is_money_market(symbol) else str(exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail
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
