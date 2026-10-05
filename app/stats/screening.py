"""Screening metrics for one stock: liquidity, momentum, risk, valuation and factor betas.

Every metric is computed from data the refresh job has already downloaded, so the whole
universe can be scored without further requests.
"""

import math
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.models.factors import FactorName
from app.models.fundamentals import Financials, FlowItem
from app.models.screener import ScreenMetrics
from app.stats.factors import fit_factor_model
from app.stats.valuation import value_company
from app.stats.volatility import InsufficientDataError

MONTH_DAYS: int = 21
HALF_YEAR_DAYS: int = 126
YEAR_DAYS: int = 252
"""Trading days in a month, half a year and a year."""

DOLLAR_VOLUME_DAYS: int = 63
"""Trading days the average dollar volume covers: about three months."""

STALE_FUNDAMENTALS_DAYS: int = 550
"""Financials whose latest period ended longer ago than this are not used for ratios; the
company has stopped filing on time."""

BETA_FIELDS: dict[FactorName, str] = {
    "Mkt-RF": "beta_market",
    "SMB": "beta_size",
    "HML": "beta_value",
    "Mom": "beta_momentum",
}
"""The Carhart factors and the metric holding each beta."""


@dataclass(frozen=True)
class Liquidity:
    """A stock's latest price and how much of it trades."""

    price: float
    avg_dollar_volume: float


def liquidity(prices: pd.DataFrame) -> Liquidity | None:
    """The latest close and average dollar volume of ``Close`` and ``Volume`` columns."""
    closes = prices["Close"].dropna()
    if closes.empty:
        return None
    recent = prices.tail(DOLLAR_VOLUME_DAYS)
    dollar_volume = float((recent["Close"] * recent["Volume"]).mean())
    if not math.isfinite(dollar_volume):
        return None
    return Liquidity(price=float(closes.iloc[-1]), avg_dollar_volume=dollar_volume)


def _return(closes: pd.Series, start_days_ago: int, end_days_ago: int = 0) -> float | None:
    """Return from ``start_days_ago`` to ``end_days_ago`` trading days before the last close."""
    if len(closes) <= start_days_ago:
        return None
    start = float(closes.iloc[-1 - start_days_ago])
    end = float(closes.iloc[-1 - end_days_ago])
    return end / start - 1.0 if start > 0 else None


def price_metrics(closes: pd.Series) -> dict[str, float | None]:
    """Momentum, volatility and drawdown of daily ``closes``, oldest first."""
    year = closes.tail(YEAR_DAYS + 1)
    returns = year.pct_change().dropna()
    volatility = float(returns.std(ddof=1) * math.sqrt(YEAR_DAYS)) if len(returns) > 20 else None
    drawdown = float((year / year.cummax() - 1.0).min()) if len(year) > 1 else None
    return {
        "return_1m": _return(closes, MONTH_DAYS),
        "return_6m": _return(closes, HALF_YEAR_DAYS),
        "momentum_12_1": _return(closes, YEAR_DAYS, MONTH_DAYS),
        "volatility": volatility,
        "max_drawdown": None if drawdown is None else min(drawdown, 0.0),
    }


def factor_betas(closes: pd.Series, factors: pd.DataFrame) -> dict[str, float | None]:
    """Carhart betas and R-squared over the last year; null when too few days overlap."""
    empty: dict[str, float | None] = {field: None for field in BETA_FIELDS.values()}
    try:
        fit = fit_factor_model(closes.tail(YEAR_DAYS + 1), factors, "carhart4")
    except InsufficientDataError:
        return empty | {"factor_r_squared": None}
    betas: dict[str, float | None] = {
        BETA_FIELDS[exposure.factor]: exposure.estimate for exposure in fit.exposures
    }
    return empty | betas | {"factor_r_squared": fit.r_squared}


def _ttm_growth(item: FlowItem | None) -> float | None:
    """Year-on-year growth of an item's TTM figure."""
    return None if item is None else item.ttm_growth


def fundamental_metrics(
    financials: Financials | None, price: float, market_cap: float, today: date
) -> dict[str, float | None]:
    """Valuation, margins, returns and growth from ``financials`` at ``market_cap``."""
    fields = [
        "pe_ratio",
        "price_to_sales",
        "ev_to_ebitda",
        "fcf_yield",
        "gross_margin",
        "operating_margin",
        "net_margin",
        "return_on_equity",
    ]
    empty: dict[str, float | None] = {field: None for field in fields}
    empty |= {"revenue_growth": None, "earnings_growth": None}
    latest = None if financials is None else financials.latest_period_end
    if financials is None or latest is None:
        return empty
    if today - latest > timedelta(days=STALE_FUNDAMENTALS_DAYS):
        return empty
    valuation = value_company(financials, price, market_cap)
    ratios: dict[str, float | None] = (
        empty if valuation is None else {field: getattr(valuation, field) for field in fields}
    )
    return ratios | {
        "revenue_growth": _ttm_growth(financials.revenue),
        "earnings_growth": _ttm_growth(financials.net_income),
    }


def _finite(value: float | None) -> float | None:
    """``value``, or ``None`` when it is NaN or infinite."""
    return value if value is not None and math.isfinite(value) else None


def screen_metrics(
    prices: pd.DataFrame,
    market_cap: float,
    financials: Financials | None,
    factors: pd.DataFrame | None,
    today: date,
) -> ScreenMetrics | None:
    """Every screening metric of one stock, or ``None`` without a usable price history.

    Args:
        prices: Daily adjusted ``Close`` and ``Volume``, oldest first, covering at least
            the last year for momentum and betas.
        market_cap: Market capitalization in US dollars.
        financials: The company's normalized financials, if any.
        factors: Daily Carhart factor returns with ``RF``; betas are null without them.
        today: The refresh date, for judging whether the financials are current.
    """
    trading = liquidity(prices)
    if trading is None:
        return None
    closes = prices["Close"].dropna()
    metrics: dict[str, float | None] = price_metrics(closes)
    metrics |= fundamental_metrics(financials, trading.price, market_cap, today)
    if factors is not None:
        metrics |= factor_betas(closes, factors)
    return ScreenMetrics(
        price=trading.price,
        market_cap=market_cap,
        avg_dollar_volume=trading.avg_dollar_volume,
        **{name: _finite(value) for name, value in metrics.items()},
    )


def market_cap_ranks(market_caps: dict[str, float]) -> dict[str, int]:
    """Rank symbols by market cap, largest first, from 1."""
    order = np.argsort([-value for value in market_caps.values()], kind="stable")
    symbols = list(market_caps)
    return {symbols[position]: rank + 1 for rank, position in enumerate(order)}
