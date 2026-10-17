"""Schemas for strategy backtests and their performance metrics."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.factors import FactorContext, FactorModel
from app.models.portfolio import Holding, Portfolio, PortfolioPeriod
from app.models.stock import SYMBOL_PATTERN, HistoryCoverage

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


class TimeSeriesMomentum(BaseModel):
    """Hold the asset while its own past return is positive: time-series momentum.

    The return is measured from ``lookback`` bars ago to ``skip`` bars ago; skipping the
    latest month sidesteps its short-term reversal. When the return is negative the
    strategy is flat, or short when ``allow_short`` is set.
    """

    type: Literal["time_series_momentum"] = "time_series_momentum"
    lookback: int = Field(
        default=252, ge=5, le=756, description="Bars back the return starts; 252 is a year."
    )
    skip: int = Field(
        default=0, ge=0, le=126, description="Most recent bars left out of the return."
    )
    allow_short: bool = Field(default=False, description="Go short instead of flat.")

    @model_validator(mode="after")
    def _skip_within_lookback(self) -> Self:
        """Reject a skip that leaves no return to measure."""
        if self.skip >= self.lookback:
            raise ValueError("skip must be shorter than lookback")
        return self


class MeanReversion(BaseModel):
    """Buy a stretched fall below the moving average and sell once the price reverts.

    The z-score is the close's distance from its ``window``-bar average in rolling
    standard deviations. A long opens when it falls to ``-entry_z`` and closes once it
    rises back to ``-exit_z``; with ``allow_short`` a short mirrors it above the average.
    """

    type: Literal["mean_reversion"] = "mean_reversion"
    window: int = Field(default=20, ge=5, le=250, description="Moving-average window, in bars.")
    entry_z: float = Field(
        default=2.0, gt=0, le=5, description="Distance from the average that opens a trade."
    )
    exit_z: float = Field(
        default=0.0,
        ge=0,
        le=5,
        description="Distance from the average at which a trade closes; 0 waits for the "
        "average itself.",
    )
    allow_short: bool = Field(default=False, description="Also short stretched rises.")

    @model_validator(mode="after")
    def _exit_inside_entry(self) -> Self:
        """Reject an exit band that a new trade would close in at once."""
        if self.exit_z >= self.entry_z:
            raise ValueError("exit_z must be smaller than entry_z")
        return self


class Rsi(BaseModel):
    """Buy when the relative strength index is oversold and sell once it is overbought.

    RSI uses Wilder's smoothing of up and down closes over ``period`` bars. A long opens
    when it falls to ``oversold`` and closes when it reaches ``overbought``; with
    ``allow_short`` a short opens at ``overbought`` and closes at ``oversold``.
    """

    type: Literal["rsi"] = "rsi"
    period: int = Field(default=14, ge=2, le=100, description="RSI window, in bars.")
    oversold: float = Field(
        default=30.0, gt=0, lt=50, description="RSI at or below which a long opens."
    )
    overbought: float = Field(
        default=70.0, gt=50, lt=100, description="RSI at or above which a long closes."
    )
    allow_short: bool = Field(default=False, description="Also short when overbought.")


class BollingerBands(BaseModel):
    """Trade the close against bands ``num_std`` rolling standard deviations around its
    ``window``-bar average.

    ``reversion`` buys a close at or below the lower band and sells at the middle band;
    ``breakout`` buys a close at or above the upper band and sells when it falls back to
    the middle band. With ``allow_short`` each mirrors on the other band.
    """

    type: Literal["bollinger_bands"] = "bollinger_bands"
    window: int = Field(default=20, ge=5, le=250, description="Moving-average window, in bars.")
    num_std: float = Field(
        default=2.0, gt=0, le=5, description="Band width, in rolling standard deviations."
    )
    mode: Literal["reversion", "breakout"] = Field(
        default="reversion", description="Fade moves to the bands, or follow breaks through them."
    )
    allow_short: bool = Field(default=False, description="Also trade the opposite band short.")


class DonchianBreakout(BaseModel):
    """Buy a close above the highest close of the previous ``entry`` bars; sell a close
    below the lowest close of the previous ``exit`` bars.

    The channel is built from closes, so it needs no intraday highs and lows. With
    ``allow_short`` a short opens below the ``entry``-bar low and closes above the
    ``exit``-bar high.
    """

    type: Literal["donchian_breakout"] = "donchian_breakout"
    entry: int = Field(default=20, ge=5, le=250, description="Breakout channel, in bars.")
    exit: int = Field(default=10, ge=2, le=250, description="Exit channel, in bars.")
    allow_short: bool = Field(default=False, description="Also short breakdowns.")

    @model_validator(mode="after")
    def _exit_within_entry(self) -> Self:
        """Reject an exit channel longer than the entry one."""
        if self.exit > self.entry:
            raise ValueError("exit must not be longer than entry")
        return self


class DualMomentum(BaseModel):
    """Hold the asset while its return over ``lookback`` bars is positive and at least
    the ``benchmark``'s over the same bars: absolute and relative momentum together.

    Otherwise the strategy is flat. As in time-series momentum, the most recent ``skip``
    bars are left out of both returns.
    """

    type: Literal["dual_momentum"] = "dual_momentum"
    lookback: int = Field(
        default=252, ge=20, le=756, description="Bars back the returns start; 252 is a year."
    )
    skip: int = Field(
        default=0, ge=0, le=126, description="Most recent bars left out of the returns."
    )
    benchmark: str = Field(
        default="SPY",
        pattern=SYMBOL_PATTERN,
        description="Symbol whose return the asset must match or beat.",
    )

    @field_validator("benchmark")
    @classmethod
    def _upper(cls, value: str) -> str:
        """Normalize the benchmark to upper case."""
        return value.upper()

    @model_validator(mode="after")
    def _skip_within_lookback(self) -> Self:
        """Reject a skip that leaves no return to measure."""
        if self.skip >= self.lookback:
            raise ValueError("skip must be shorter than lookback")
        return self


class VolatilityTargeting(BaseModel):
    """Stay long, sized so that recent volatility matches ``target_volatility``.

    The position is the target divided by the annualized standard deviation of the last
    ``window`` returns, capped at ``max_leverage``. It is rebalanced only when the target
    moves more than ``rebalance_threshold`` from the position held, to limit trading.
    Above 1.0 the rest is borrowed, at the risk-free rate when cash earns interest.
    """

    type: Literal["volatility_targeting"] = "volatility_targeting"
    target_volatility: float = Field(
        default=0.15, gt=0, le=1, description="Annualized volatility to aim for (0.15 = 15%)."
    )
    window: int = Field(
        default=20, ge=5, le=252, description="Returns the volatility is measured over."
    )
    max_leverage: float = Field(
        default=1.0, gt=0, le=2, description="Largest position, as a multiple of capital."
    )
    rebalance_threshold: float = Field(
        default=0.1,
        ge=0,
        le=1,
        description="Change in target position needed to trade; 0 rebalances every bar.",
    )


class Macd(BaseModel):
    """Hold the asset while the MACD line is above its signal line.

    The MACD line is the ``fast``-bar exponential average of closes minus the ``slow``-bar
    one, and the signal line is its ``signal``-bar exponential average. Below it the
    strategy is flat, or short when ``allow_short`` is set.
    """

    type: Literal["macd"] = "macd"
    fast: int = Field(default=12, ge=2, le=100, description="Fast exponential average, in bars.")
    slow: int = Field(default=26, ge=3, le=250, description="Slow exponential average, in bars.")
    signal: int = Field(default=9, ge=2, le=100, description="Signal-line average, in bars.")
    allow_short: bool = Field(default=False, description="Go short instead of flat.")

    @model_validator(mode="after")
    def _fast_below_slow(self) -> Self:
        """Reject averages where the fast one is not faster than the slow one."""
        if self.fast >= self.slow:
            raise ValueError("fast must be shorter than slow")
        return self


StrategySpec = Annotated[
    BuyAndHold
    | SmaCrossover
    | TimeSeriesMomentum
    | MeanReversion
    | Rsi
    | BollingerBands
    | DonchianBreakout
    | DualMomentum
    | VolatilityTargeting
    | Macd,
    Field(discriminator="type"),
]
"""A strategy and its parameters, chosen by ``type``."""

StrategyType = Literal[
    "buy_and_hold",
    "sma_crossover",
    "time_series_momentum",
    "mean_reversion",
    "rsi",
    "bollinger_bands",
    "donchian_breakout",
    "dual_momentum",
    "volatility_targeting",
    "macd",
]
"""The ``type`` of each strategy."""


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
    cash_interest: bool = Field(
        default=False,
        description="Let capital not invested in the asset earn the risk-free rate (the "
        "one-month T-bill, from Ken French's data library), as would short sale proceeds.",
    )
    attribution: FactorModel | None = Field(
        default="carhart4",
        description="Factor model to regress the strategy's daily returns on, separating "
        "alpha from factor exposure; null to skip.",
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
    exposure: float = Field(
        ge=0,
        le=2,
        description="Average share of capital in positions, long or short; above 1 when "
        "leveraged.",
    )
    cash_interest: bool = False
    attribution: FactorContext | None = Field(
        default=None,
        description="The strategy's alpha and factor betas; null when skipped or not "
        "estimable, as for weekly bars.",
    )
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


class PortfolioBacktestRequest(Portfolio):
    """A strategy run on each holding of a portfolio, rebalanced to its weights every day."""

    strategy: StrategySpec = Field(default_factory=SmaCrossover)
    period: PortfolioPeriod = Field(default="5y", description="Lookback window of daily bars.")
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
    cash_interest: bool = Field(
        default=False,
        description="Let capital not invested earn the risk-free rate (the one-month T-bill).",
    )
    attribution: FactorModel | None = Field(
        default="carhart4",
        description="Factor model to regress the portfolio's daily returns on; null to skip.",
    )


class AssetBacktest(BaseModel):
    """One holding's part in a portfolio backtest."""

    symbol: str
    weight: float = Field(ge=0, le=1)
    contribution: float = Field(
        description="Sum of the holding's weighted daily returns, net of its trading costs: "
        "its share of the portfolio's arithmetic return."
    )
    exposure: float = Field(ge=0, le=1, description="Share of days the holding was held.")
    trades: int = Field(ge=0, description="Days on which its position changed.")


class PortfolioBacktestResponse(BaseModel):
    """A strategy's performance on a portfolio, against holding the same weights."""

    holdings: list[Holding]
    period: PortfolioPeriod
    strategy: StrategySpec
    cost_bps: float
    risk_free_rate: float
    cash_interest: bool
    observations: int = Field(ge=1, description="Days on which every holding traded.")
    excluded_dates: int = Field(
        ge=0, description="Dates some holdings traded and others did not, left out."
    )
    metrics: PerformanceMetrics
    benchmark: PerformanceMetrics = Field(
        description="Holding the same weights, rebalanced daily, without costs."
    )
    trades: int = Field(ge=0, description="Days on which any position changed.")
    exposure: float = Field(
        ge=0,
        le=2,
        description="Average share of capital in positions, long or short; above 1 when "
        "leveraged.",
    )
    assets: list[AssetBacktest]
    attribution: FactorContext | None = None
    equity_curve: list[EquityPoint]
    notice: str | None = None
    disclaimer: str = (
        "Backtested on historical adjusted prices. Past performance does not predict "
        "future results."
    )
