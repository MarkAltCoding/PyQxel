"""Schemas for Monte Carlo portfolio simulations and their results."""

from datetime import date, datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.models.portfolio import Holding, Portfolio, PortfolioPeriod

MAX_HORIZON_DAYS: int = 1_260
"""Longest horizon, five years of trading days."""

MAX_PATHS: int = 100_000

MAX_PATH_DAYS: int = 2_600_000
"""Most paths x horizon one simulation may run."""

MAX_DRAWS: int = 15_000_000
"""Most paths x horizon x assets one simulation may draw."""

DependenceModel = Literal["gaussian", "student_t", "empirical"]
"""How simulated assets move together: a Gaussian or Student t copula fitted to the
returns, or ``empirical``, which resamples whole historical days of ranks and so keeps
every pair's own joint-crash behavior."""

MarginalModel = Literal["empirical", "student_t"]
"""How each asset's own daily returns are drawn: from its historical returns, or from a
Student t distribution fitted to them, which can exceed the worst day on record."""


Rebalancing = Literal["daily", "none"]
"""``daily``: reset to the target weights every day. ``none``: buy once and hold, so
weights drift with each holding's performance."""


class FinalWeight(BaseModel):
    """A holding's average weight at the horizon, after drifting from its target."""

    symbol: str
    weight: float = Field(ge=0, le=1)


class Percentile(BaseModel):
    """A value below which ``percentile`` percent of simulated outcomes fall."""

    percentile: float = Field(gt=0, lt=100)
    value: float


class Distribution(BaseModel):
    """Summary of a simulated quantity across paths."""

    mean: float
    std: float = Field(ge=0)
    percentiles: list[Percentile]


class RiskMeasure(BaseModel):
    """Value at Risk and Conditional Value at Risk over the horizon at one confidence level.

    Losses are positive fractions of the starting value (0.12 = 12% lost); a negative
    value means the corresponding outcome is still a gain.
    """

    confidence: float = Field(gt=0, lt=1)
    value_at_risk: float = Field(description="Loss exceeded on only (1 - confidence) of paths.")
    conditional_value_at_risk: float = Field(
        description="Average loss on the paths at or beyond the Value at Risk."
    )
    value_at_risk_amount: float = Field(description="Value at Risk in currency units.")
    conditional_value_at_risk_amount: float = Field(
        description="Conditional Value at Risk in currency units."
    )


class FanPoint(BaseModel):
    """Percentiles of portfolio value after ``day`` simulated trading days."""

    day: int = Field(ge=0)
    p05: float
    p25: float
    p50: float
    p75: float
    p95: float


class MarginalFit(BaseModel):
    """How one asset's daily returns were modeled."""

    symbol: str
    model: MarginalModel
    mean_return: float = Field(description="Mean historical daily return.")
    volatility: float = Field(ge=0, description="Annualized historical volatility.")
    degrees_of_freedom: float | None = Field(
        default=None, description="Of the fitted Student t; null for empirical marginals."
    )


class TailCheck(BaseModel):
    """Whether the simulation reproduces how often a pair has its worst days together."""

    symbols: tuple[str, str]
    historical_lower_tail: float = Field(
        ge=0,
        le=1,
        description="Observed share of one asset's worst 5% of days on which the other was "
        "also in its worst 5%.",
    )
    simulated_lower_tail: float = Field(ge=0, le=1, description="The same in the simulation.")
    understated: bool = Field(
        description="True when the simulation shows joint crashes clearly less often than "
        "history, by more than sampling noise explains."
    )


class SimulationSummary(BaseModel):
    """Distribution of a portfolio's value over a simulated horizon.

    Returns are decimals over the whole horizon (0.05 = 5%); values are in the units of
    ``initial_value``.
    """

    paths: int = Field(ge=1)
    horizon: int = Field(ge=1, description="Trading days simulated.")
    initial_value: float = Field(gt=0)
    dependence: DependenceModel
    marginals: MarginalModel
    rebalancing: Rebalancing = "daily"
    mean_final_weights: list[FinalWeight] | None = Field(
        default=None,
        description="Buy-and-hold only: each holding's weight at the horizon, averaged "
        "over paths, showing how far the mix drifted from its targets.",
    )
    copula_degrees_of_freedom: float | None = Field(
        default=None, description="Of the Student t copula; null for other dependence models."
    )
    seed: int | None = Field(default=None, description="Seed that reproduces these paths.")
    expected_return: float = Field(description="Mean return over the horizon.")
    median_return: float
    probability_of_loss: float = Field(ge=0, le=1)
    terminal_value: Distribution = Field(description="Portfolio value at the horizon.")
    terminal_return: Distribution = Field(description="Return over the horizon.")
    risk: list[RiskMeasure]
    max_drawdown: Distribution = Field(
        description="Largest peak-to-trough decline along each path, as a negative fraction."
    )
    fan_chart: list[FanPoint] = Field(description="Value percentiles for each day, from day 0.")
    marginal_fits: list[MarginalFit]
    tail_checks: list[TailCheck]
    warnings: list[str] = Field(default_factory=list)


class SimulationRequest(Portfolio):
    """A portfolio and how to simulate it."""

    period: PortfolioPeriod = Field(
        default="5y", description="History of daily returns the simulation is fitted on."
    )
    horizon: int = Field(
        default=21, ge=1, le=MAX_HORIZON_DAYS, description="Trading days ahead to simulate."
    )
    paths: int = Field(default=10_000, ge=100, le=MAX_PATHS)
    dependence: DependenceModel = Field(
        default="student_t",
        description="``student_t`` or ``gaussian`` copula, or ``empirical`` (resampled "
        "historical days, which keeps each pair's own joint-crash behavior).",
    )
    marginals: MarginalModel = Field(
        default="empirical",
        description="Each asset's returns from its history, or from a fitted Student t.",
    )
    rebalancing: Rebalancing = Field(
        default="daily",
        description="``daily`` resets to the target weights every day; ``none`` buys once "
        "and holds, so weights drift.",
    )
    initial_value: float = Field(default=10_000.0, gt=0, le=1e12)
    seed: int | None = Field(
        default=None, ge=0, lt=2**32, description="Reproduces a previous run's paths."
    )

    @model_validator(mode="after")
    def _within_limits(self) -> Self:
        """Reject runs too large to simulate in one request."""
        path_days = self.paths * self.horizon
        if path_days > MAX_PATH_DAYS:
            raise ValueError(
                f"paths x horizon is {path_days:,}; at most {MAX_PATH_DAYS:,} are allowed."
            )
        draws = path_days * len(self.holdings)
        if draws > MAX_DRAWS:
            raise ValueError(
                f"paths x horizon x assets is {draws:,}; at most {MAX_DRAWS:,} are allowed. "
                "Use fewer paths, a shorter horizon or fewer assets."
            )
        return self


class SimulationResponse(BaseModel):
    """A portfolio simulation, the history it was fitted on, and where it is stored."""

    id: UUID | None = Field(
        default=None,
        description="ID of the stored result, for GET /portfolio/simulations/{id}; null if "
        "it could not be saved.",
    )
    saved_at: datetime | None = None
    holdings: list[Holding]
    period: PortfolioPeriod
    start: date = Field(description="First day of the history the simulation is fitted on.")
    end: date = Field(description="Last day of that history.")
    observations: int = Field(ge=1, description="Days on which every asset has a return.")
    excluded_dates: int = Field(
        ge=0, description="Dates some assets traded but not all, left out of the history."
    )
    simulation: SimulationSummary
    notice: str | None = None
    disclaimer: str = (
        "Simulated from historical returns, which assume the future resembles the fitted "
        "window. Not investment advice."
    )


class SimulationOverview(BaseModel):
    """A stored simulation's headline figures, without its distributions and fan chart."""

    id: UUID
    saved_at: datetime
    symbols: list[str]
    horizon: int
    paths: int
    dependence: DependenceModel
    marginals: MarginalModel
    rebalancing: Rebalancing
    expected_return: float
    probability_of_loss: float
    value_at_risk_95: float
    conditional_value_at_risk_95: float


class SimulationList(BaseModel):
    """One page of stored simulations, newest first."""

    items: list[SimulationOverview]
    total: int = Field(ge=0, description="Stored simulations matching the filters, on any page.")
    limit: int
    offset: int
