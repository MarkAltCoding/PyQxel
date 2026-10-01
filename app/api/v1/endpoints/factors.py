"""Multi-factor regression routes."""

import asyncio
from typing import Annotated

import pandas as pd
from fastapi import APIRouter, HTTPException, Query, status

from app.api.v1.endpoints.stocks import Symbol, history_coverage, upstream_error
from app.data.factors import fetch_factors
from app.data.fetcher import DataFetchError, fetch_price_history
from app.models.factors import FactorModel, FactorPeriod, FactorRegression
from app.stats.factors import daily_returns, fit_factor_model
from app.stats.volatility import InsufficientDataError

router = APIRouter()


def _lag_notice(symbol: str, returns: pd.Series, factor_end: pd.Timestamp) -> str | None:
    """Say how many of the stock's latest returns fall after the published factor data."""
    excluded = int((returns.index > factor_end).sum())
    if excluded == 0:
        return None
    days = "trading day" if excluded == 1 else "trading days"
    return (
        f"Fama-French factors are published monthly and currently end "
        f"{factor_end.date()}, so the last {excluded} {days} of {symbol} returns are "
        "not included."
    )


@router.get(
    "/{symbol}/factors",
    response_model=FactorRegression,
    summary="Fama-French factor exposures (multi-factor regression)",
)
async def get_factor_exposures(
    symbol: Symbol,
    model: Annotated[
        FactorModel,
        Query(
            description="``ff3`` (market, size, value), ``carhart4`` (plus momentum) or "
            "``ff5`` (plus profitability and investment)."
        ),
    ] = "ff3",
    period: Annotated[FactorPeriod, Query(description="Lookback window of daily bars.")] = "5y",
) -> FactorRegression:
    """Regress ``symbol``'s daily excess returns on a Fama-French factor model.

    Returns annualized alpha, a beta for each factor with Newey-West t-statistics and
    p-values, R-squared, idiosyncratic volatility, and how much of the return variance
    each factor explains. Returns and volatilities are decimals (0.25 = 25%).

    Factor data is published monthly, about a month late, so the window ends at
    ``factor_data_end`` and ``notice`` says how many recent days were left out.

    Returns 404 for unknown symbols, 422 when too few days overlap the factor data,
    and 502 when the price provider or the factor library fails.
    """
    prices_result, factors_result = await asyncio.gather(
        fetch_price_history(symbol, period=period, interval="1d"),
        fetch_factors(model),
        return_exceptions=True,
    )
    for result in (prices_result, factors_result):
        if isinstance(result, DataFetchError):
            raise upstream_error(result) from result
        if isinstance(result, BaseException):
            raise result
    assert isinstance(prices_result, pd.DataFrame) and isinstance(factors_result, pd.DataFrame)
    symbol = symbol.upper()

    try:
        fit = fit_factor_model(prices_result["Close"], factors_result, model)
    except InsufficientDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    factor_end = pd.Timestamp(factors_result.index[-1])
    coverage, coverage_notice = history_coverage(symbol, period, "1d", prices_result)
    notices = [
        notice
        for notice in (
            coverage_notice,
            _lag_notice(symbol, daily_returns(prices_result["Close"]), factor_end),
        )
        if notice
    ]
    return FactorRegression(
        symbol=symbol,
        model=model,
        period=period,
        fit=fit,
        factor_data_end=factor_end.date(),
        coverage=coverage,
        notice=" ".join(notices) or None,
    )
