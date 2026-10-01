"""Schemas for multi-asset requests: a set of symbols, or a weighted portfolio of them."""

import math
from collections import Counter
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, Field, StringConstraints, model_validator

from app.models.stock import SYMBOL_PATTERN

MAX_SYMBOLS: int = 20
"""Most symbols one request may name; each is a separate price download."""

WEIGHT_TOLERANCE: float = 1e-6
"""How far portfolio weights may sum from one, absorbing rounding in clients."""

PortfolioPeriod = Literal["1y", "2y", "5y", "10y"]
"""Lookback windows of daily bars a multi-asset model may be fitted on."""

PortfolioSymbol = Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_upper=True, pattern=SYMBOL_PATTERN),
]
"""A ticker symbol, stripped and upper-cased."""


def _unique(symbols: list[str]) -> list[str]:
    """Reject a list naming any symbol twice, after normalization."""
    repeated = sorted(symbol for symbol, count in Counter(symbols).items() if count > 1)
    if repeated:
        raise ValueError(f"Symbols must be unique; repeated: {', '.join(repeated)}.")
    return symbols


SymbolList = Annotated[
    list[PortfolioSymbol],
    Field(min_length=2, max_length=MAX_SYMBOLS),
    AfterValidator(_unique),
]
"""Two to :data:`MAX_SYMBOLS` distinct symbols, normalized."""


class Holding(BaseModel):
    """One position in a portfolio."""

    symbol: PortfolioSymbol
    weight: float = Field(
        gt=0, le=1, description="Share of the portfolio's value, as a decimal (0.25 = 25%)."
    )


class Portfolio(BaseModel):
    """A long-only portfolio: distinct symbols with weights that sum to one."""

    holdings: list[Holding] = Field(min_length=1, max_length=MAX_SYMBOLS)

    @model_validator(mode="after")
    def _check_holdings(self) -> Self:
        """Reject repeated symbols and weights that do not sum to one."""
        _unique(self.symbols)
        total = math.fsum(holding.weight for holding in self.holdings)
        if abs(total - 1.0) > WEIGHT_TOLERANCE:
            raise ValueError(f"Weights must sum to 1; they sum to {total:.6g}.")
        return self

    @property
    def symbols(self) -> list[str]:
        """The portfolio's symbols, in the order given."""
        return [holding.symbol for holding in self.holdings]

    @property
    def weights(self) -> dict[str, float]:
        """Each symbol's weight."""
        return {holding.symbol: holding.weight for holding in self.holdings}
