"""Schemas for multi-asset requests: a set of symbols, or a weighted portfolio of them."""

import math
from collections import Counter
from datetime import date
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


Matrix = list[list[float]]
"""A square matrix as rows, in the order of the response's ``symbols``."""


class CopulaRequest(BaseModel):
    """Assets whose dependence to model."""

    symbols: SymbolList
    period: PortfolioPeriod = Field(default="5y", description="Lookback window of daily bars.")


class GaussianCopula(BaseModel):
    """A Gaussian copula: correlated, but with no tendency to crash together."""

    correlation: Matrix = Field(description="Correlation of the normal scores of the ranks.")
    log_likelihood: float
    aic: float


class StudentTCopula(BaseModel):
    """A Student t copula: correlated, with joint extremes likelier at fewer degrees of freedom."""

    correlation: Matrix = Field(description="sin(pi tau / 2) from Kendall's tau.")
    degrees_of_freedom: float = Field(
        description="Fitted by maximum likelihood; lower means more joint extremes."
    )
    degrees_of_freedom_at_bound: bool = Field(
        description="True when the fit reached the upper bound of 100: no sign of fat joint "
        "tails, and the copula is effectively Gaussian."
    )
    log_likelihood: float
    aic: float


class PairDependence(BaseModel):
    """How two assets move together, especially in extremes."""

    symbols: tuple[str, str]
    kendall_tau: float = Field(ge=-1, le=1, description="Rank correlation.")
    tail_dependence: float = Field(
        ge=0,
        le=1,
        description="Under the t copula, the limiting chance that one asset has an extreme "
        "day given the other does, in either tail. Zero under the Gaussian copula.",
    )
    empirical_lower_tail: float = Field(
        ge=0,
        le=1,
        description="Observed share of one asset's worst 5% of days on which the other was "
        "also in its worst 5%; about 0.05 for independent assets.",
    )
    empirical_upper_tail: float = Field(ge=0, le=1, description="The same for the best 5% of days.")


class CopulaFitResponse(BaseModel):
    """Gaussian and Student t copulas fitted to several assets' daily returns, and compared."""

    symbols: list[str]
    period: PortfolioPeriod
    start: date = Field(description="First day whose return is in the fit.")
    end: date = Field(description="Last day whose return is in the fit.")
    observations: int = Field(ge=1, description="Days on which every asset has a return.")
    excluded_dates: int = Field(
        ge=0, description="Dates some assets traded but not all, left out of the fit."
    )
    kendall_tau: Matrix
    gaussian: GaussianCopula
    student_t: StudentTCopula
    preferred: Literal["gaussian", "student_t"] = Field(
        description="The copula with the lower AIC."
    )
    aic_difference: float = Field(
        ge=0,
        description="How much lower the preferred copula's AIC is. Above about 10 is strong "
        "evidence for it.",
    )
    pairs: list[PairDependence]
    warnings: list[str] = Field(default_factory=list)
    notice: str | None = Field(
        default=None, description="Explains any part of the window without data."
    )
