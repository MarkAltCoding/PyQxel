"""Factor returns estimated from ETF prices, for when Ken French's library is turned off or
cannot be reached.

Each factor is the daily return of one ETF over another, from prices fetched on the
user's own price-history keys like any other history, and the risk-free rate is the
Treasury's 4-week bill (:mod:`app.data.treasury`):

=======  ===================================================  ===========
Factor   Stand-in                                             Since
=======  ===================================================  ===========
Mkt-RF   SPY (the S&P 500) less the bill rate                 2002
SMB      IWM (Russell 2000, small) less IVV (S&P 500, large)  2002
HML      IWD (Russell 1000 value) less IWF (1000 growth)      2002
Mom      MTUM (momentum) less IVV                             2013
RMW      QUAL (quality, profitable firms) less IVV            2013
=======  ===================================================  ===========

They are estimates: the academic factors are long-short portfolios rebuilt every year,
and these are long-only funds net of their fees, so betas on them point the same way and
are of a similar size, but not equal. No ETF stands in for the investment factor (CMA), so
the five-factor model has none. The dates start where the bill rates and every ETF a model
needs have prices.
"""

import asyncio
import logging

import pandas as pd

from app.data.fetcher import DataFetchError, fetch_price_history
from app.data.treasury import fetch_bill_returns
from app.models.factors import MODEL_FACTORS, RISK_FREE, FactorModel

logger = logging.getLogger(__name__)

FACTOR_SOURCE: str = "source"
"""Key in a factor table's ``attrs`` naming where it came from: :data:`ETF_SOURCE` here."""
ETF_SOURCE: str = "etf"

LEGS: dict[str, tuple[str, str | None]] = {
    "Mkt-RF": ("SPY", None),
    "SMB": ("IWM", "IVV"),
    "HML": ("IWD", "IWF"),
    "Mom": ("MTUM", "IVV"),
    "RMW": ("QUAL", "IVV"),
}
"""Each factor's long ETF and the ETF it is measured against; ``None`` for the bill rate."""

ETF_NOTICE: str = (
    "Factor returns are estimated from ETFs (SPY, IWM, IVV, IWD, IWF, and MTUM for "
    "momentum) on your price-history key, as Ken French's factor data is not used here: "
    "the betas point the same way as the academic factors' but are not equal to them."
)
"""Said wherever factors estimated from ETFs are shown."""


class NoEtfStandInError(DataFetchError):
    """Raised for a model with a factor no ETF stands in for."""


async def fetch_etf_factors(model: FactorModel) -> pd.DataFrame:
    """Daily returns of ``model``'s factors estimated from ETFs, and the risk-free rate, as
    decimals, oldest first; ``attrs[FACTOR_SOURCE]`` is :data:`ETF_SOURCE`.

    Raises:
        NoEtfStandInError: If a factor of ``model`` has no ETF stand-in (CMA).
        DataFetchError: If an ETF's prices or the bill rates cannot be fetched.
    """
    names = MODEL_FACTORS[model]
    missing = [name for name in names if name not in LEGS]
    if missing:
        raise NoEtfStandInError(
            f"The {', '.join(missing)} factor has no ETF stand-in, so the {model.upper()} "
            "model needs Ken French's factor data, which is not used here."
        )
    symbols = sorted({etf for name in names for etf in LEGS[name] if etf is not None})
    frames = await asyncio.gather(
        *(fetch_price_history(symbol, period="max", interval="1d") for symbol in symbols)
    )
    closes = pd.DataFrame(
        {
            symbol: pd.Series(
                frame["Close"].to_numpy(),
                index=pd.DatetimeIndex(frame.index).tz_localize(None).normalize(),
            )
            for symbol, frame in zip(symbols, frames, strict=True)
        }
    )
    returns = closes.sort_index().pct_change(fill_method=None)
    start = pd.Timestamp(returns.dropna().index[0]).date() if not returns.dropna().empty else None
    if start is None:
        raise DataFetchError("The ETFs that stand in for the factors share no trading days.")
    bills = await fetch_bill_returns(start)
    table = pd.DataFrame(index=returns.index)
    for name in names:
        long, short = LEGS[name]
        table[name] = returns[long] - (
            bills.reindex(returns.index) if short is None else returns[short]
        )
    table[RISK_FREE] = bills.reindex(returns.index)
    table = table.dropna()
    if table.empty:
        raise DataFetchError("The ETFs and bill rates that stand in for the factors share no days.")
    table.attrs[FACTOR_SOURCE] = ETF_SOURCE
    return table
