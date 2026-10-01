"""Schemas for strategy backtests and their performance metrics."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.models.stock import HistoryCoverage

BacktestPeriod = Literal["1y", "2y", "5y", "10y", "max"]
"""Lookback windows a backtest may cover."""

BacktestInterval = Literal["1d", "1wk"]
"""Bar sizes with a fixed number of periods per year."""


class BuyAndHold(BaseModel):
    """Hold the asset for the whole window."""

    type: Literal["buy_and_hold"] = "buy_and_hold"


class SmaCrossover(BaseModel):
    """Hold the asset while its fast moving average is above its slow one.

    Below it, the strategy is flat, or short when ``allow_short`` is set. No position
    is taken until the slow average has a full window.
    """

    type: Literal["sma_crossover"] = "sma_crossover"
    fast: int = Field(default=50, ge=2, le=250, description="Fast moving-average window, in bars.")
    slow: int = Field(default=200, ge=3, le=500, description="Slow moving-average window, in bars.")
    allow_short: bool = Field(default=False, description="Go short instead of flat.")

    @model_validator(mode="after")
    def _fast_below_slow(self) -> Self:
        """Reject windows where the fast average is not faster than the slow one."""
        if self.fast >= self.slow:
            raise ValueError("fast must be shorter than slow")
        return self


StrategySpec = Annotated[BuyAndHold | SmaCrossover, Field(discriminator="type")]
"""A strategy and its parameters, chosen by ``type``."""


class BacktestRequest(BaseModel):
    """Options for a backtest."""

    strategy: StrategySpec = Field(default_factory=SmaCrossover)
    period: BacktestPeriod = Field(default="5y", description="Lookback window.")
    interval: BacktestInterval = Field(default="1d", description="Bar size.")
    cost_bps: float = Field(
        default=5.0,
        ge=0,
        le=500,
        description="Trading cost per unit of turnover, in basis points (5 = 0.05%).",
    )
    risk_free_rate: float = Field(
        default=0.0,
        ge=-0.05,
        le=0.25,
        description="Annual risk-free rate for Sharpe and Sortino, as a decimal (0.04 = 4%).",
    )


class PerformanceMetrics(BaseModel):
    """Return, risk and drawdown statistics of a periodic return series.

    Returns and volatilities are decimals (0.25 = 25%); volatility, Sharpe and Sortino
    are annualized.
    """

    start: datetime = Field(
        description="When capital was invested: the close before the first counted return."
    )
    end: datetime = Field(description="Last bar whose return is counted.")
    observations: int = Field(ge=1, description="Number of returns.")
    total_return: float = Field(description="Compounded return over the window.")
    annualized_return: float | None = Field(
        default=None,
        description="Compound annual growth rate; null for windows shorter than a year.",
    )
    annualized_volatility: float = Field(ge=0)
    sharpe_ratio: float | None = Field(
        default=None,
        description="Mean excess return over its standard deviation; null when returns "
        "never vary.",
    )
    sortino_ratio: float | None = Field(
        default=None,
        description="Mean excess return over downside deviation below the risk-free rate; "
        "null when no return falls below it.",
    )
    max_drawdown: float = Field(le=0, description="Largest peak-to-trough decline in equity.")
    max_drawdown_peak: datetime | None = Field(
        default=None, description="Bar at which the largest drawdown began; null if none."
    )
    max_drawdown_trough: datetime | None = Field(
        default=None, description="Bar at which the largest drawdown bottomed; null if none."
    )
    max_drawdown_recovery: datetime | None = Field(
        default=None,
        description="First bar at which equity regained the peak; null if not yet recovered.",
    )


class EquityPoint(BaseModel):
    """Growth of one unit of capital at the close of one bar."""

    timestamp: datetime
    strategy: float
    benchmark: float


class BacktestResponse(BaseModel):
    """A strategy's performance, with buy-and-hold on the same bars as the benchmark."""

    id: UUID | None = Field(
        default=None,
        description="ID of the stored result, for GET /backtests/{id}; null if it could not "
        "be saved.",
    )
    saved_at: datetime | None = Field(default=None, description="When the result was stored.")
    symbol: str
    period: BacktestPeriod
    interval: BacktestInterval
    periods_per_year: int
    strategy: StrategySpec
    cost_bps: float
    risk_free_rate: float
    metrics: PerformanceMetrics
    benchmark: PerformanceMetrics = Field(description="Buy-and-hold over the same bars, no costs.")
    trades: int = Field(ge=0, description="Bars on which the position changed.")
    exposure: float = Field(ge=0, le=1, description="Share of bars with a position held.")
    equity_curve: list[EquityPoint]
    coverage: HistoryCoverage
    notice: str | None = None
    disclaimer: str = (
        "Backtested on historical adjusted prices. Past performance does not predict "
        "future results."
    )


class BacktestSummary(BaseModel):
    """A stored backtest's headline numbers, without its equity curve."""

    id: UUID
    saved_at: datetime
    symbol: str
    strategy: str = Field(description="Strategy ``type``; the full spec is on the result.")
    period: BacktestPeriod
    interval: BacktestInterval
    total_return: float
    sharpe_ratio: float | None
    max_drawdown: float
    benchmark_total_return: float


class BacktestList(BaseModel):
    """One page of stored backtests, newest first."""

    items: list[BacktestSummary]
    total: int = Field(ge=0, description="Stored backtests matching the filters, on any page.")
    limit: int
    offset: int
