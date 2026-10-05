"""Monte Carlo simulation of a portfolio's value from its assets' joint daily returns.

Each simulated day is drawn in two parts:

1. **Dependence**: a vector of uniforms, one per asset, from a Gaussian or Student t
   copula fitted to the history (:mod:`app.stats.copulas`), or ``empirical``: a whole
   historical day of rank-transformed returns, resampled. The empirical option keeps
   every pair's own tendency to crash together, which a t copula's single degrees of
   freedom cannot, but it can only recombine days that happened and, resampling days
   independently, it drops volatility clustering.
2. **Marginals**: each uniform is mapped through an asset's return distribution: the
   interpolated quantiles of its historical returns, or a Student t fitted to them.
   With empirical marginals no day is worse than the worst on record; the t can be.

Days are independent. With ``daily`` rebalancing the portfolio is reset to its target
weights every day, so its daily return is the weighted sum of its assets'. With
``none`` (buy-and-hold) it is bought once at the target weights and each holding then
grows on its own, so weights drift toward whatever has done well. Paths are simulated
in batches to bound memory, and a seed reproduces them exactly.
"""

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

from app.models.simulation import (
    MAX_DRAWS,
    MAX_PATH_DAYS,
    DependenceModel,
    Distribution,
    FinalWeight,
    FanPoint,
    MarginalFit,
    MarginalModel,
    Percentile,
    Rebalancing,
    RiskMeasure,
    SimulationSummary,
    TailCheck,
)
from app.stats.copulas import (
    MIN_DEGREES_OF_FREEDOM,
    TAIL_QUANTILE,
    CopulaFit,
    empirical_tail_dependence,
    fit_copulas,
    pseudo_observations,
)

TRADING_DAYS: int = 252
"""Days per year, for annualizing volatility."""


BATCH_DRAWS: int = 2_000_000
"""Asset-day draws generated at a time, bounding memory."""

TAIL_SAMPLE_DAYS: int = 200_000
"""Simulated days kept to compare joint tail frequencies with history."""

MIN_RETURN: float = -0.95
"""Floor on a simulated daily return; a fitted t can otherwise imply losing over 100%."""

CONFIDENCE_LEVELS: tuple[float, ...] = (0.95, 0.99)
DISTRIBUTION_PERCENTILES: tuple[float, ...] = (1, 5, 10, 25, 50, 75, 90, 95, 99)
FAN_PERCENTILES: tuple[float, ...] = (5, 25, 50, 75, 95)

MIN_TAIL_GAP: float = 0.1
"""Smallest shortfall in joint tail frequency flagged, whatever the sampling noise."""


class SimulationError(ValueError):
    """Raised for simulation settings that are invalid or too large to run."""


@dataclass(frozen=True)
class _Marginal:
    """One asset's return distribution, as a quantile function."""

    sorted_returns: np.ndarray
    t_params: tuple[float, float, float] | None
    """Degrees of freedom, location and scale of a fitted Student t, if used."""

    def quantiles(self, uniforms: np.ndarray) -> np.ndarray:
        """Map uniforms in (0, 1) to daily returns."""
        draws: np.ndarray
        if self.t_params is not None:
            df, loc, scale = self.t_params
            draws = stats.t.ppf(uniforms, df, loc=loc, scale=scale)
        else:
            draws = _empirical_quantiles(self.sorted_returns, uniforms)
        bounded: np.ndarray = np.maximum(draws, MIN_RETURN)
        return bounded


def _empirical_quantiles(sorted_returns: np.ndarray, uniforms: np.ndarray) -> np.ndarray:
    """Interpolate the quantile function of ``sorted_returns`` at ``uniforms``.

    A uniform of ``k / (n + 1)``, the ``k``-th pseudo-observation, maps exactly to the
    ``k``-th smallest return, so resampled historical ranks give back historical returns.
    """
    count = len(sorted_returns)
    position = np.clip(uniforms * (count + 1) - 1, 0, count - 1)
    lower = np.floor(position).astype(int)
    upper = np.minimum(lower + 1, count - 1)
    fraction = position - lower
    quantiles: np.ndarray = (
        sorted_returns[lower] + (sorted_returns[upper] - sorted_returns[lower]) * fraction
    )
    return quantiles


def _fit_marginal(returns: np.ndarray, model: MarginalModel) -> _Marginal:
    """Prepare one asset's quantile function, fitting a Student t when asked."""
    if model == "empirical":
        return _Marginal(sorted_returns=np.sort(returns), t_params=None)
    df, loc, scale = stats.t.fit(returns)
    if df < MIN_DEGREES_OF_FREEDOM:
        # Below two degrees of freedom the variance is infinite; fix it at the floor.
        df, loc, scale = stats.t.fit(returns, f0=MIN_DEGREES_OF_FREEDOM)
    return _Marginal(sorted_returns=np.sort(returns), t_params=(df, loc, scale))


def _draw_uniforms(
    rng: np.random.Generator,
    count: int,
    dependence: DependenceModel,
    copula: CopulaFit | None,
    history: np.ndarray,
) -> np.ndarray:
    """Draw ``count`` days of uniforms, one column per asset, with the chosen dependence."""
    dimension = history.shape[1]
    if dimension == 1:
        return rng.uniform(size=(count, 1))
    if dependence == "empirical":
        return history[rng.integers(0, len(history), size=count)]
    assert copula is not None
    if dependence == "gaussian":
        normal = rng.multivariate_normal(
            np.zeros(dimension), copula.gaussian_correlation, size=count, method="cholesky"
        )
        return stats.norm.cdf(normal)
    df = copula.degrees_of_freedom
    normal = rng.multivariate_normal(
        np.zeros(dimension), copula.t_correlation, size=count, method="cholesky"
    )
    mixing = np.sqrt(rng.chisquare(df, size=count) / df)
    return stats.t.cdf(normal / mixing[:, None], df)


def _distribution(values: np.ndarray) -> Distribution:
    """Summarize ``values`` by mean, standard deviation and percentiles."""
    levels = np.percentile(values, DISTRIBUTION_PERCENTILES)
    return Distribution(
        mean=float(values.mean()),
        std=float(values.std(ddof=1)) if len(values) > 1 else 0.0,
        percentiles=[
            Percentile(percentile=p, value=float(v))
            for p, v in zip(DISTRIBUTION_PERCENTILES, levels)
        ],
    )


def _risk(returns: np.ndarray, initial_value: float) -> list[RiskMeasure]:
    """Value at Risk and Conditional Value at Risk of horizon returns, as positive losses."""
    measures: list[RiskMeasure] = []
    for confidence in CONFIDENCE_LEVELS:
        cutoff = float(np.quantile(returns, 1 - confidence))
        var = -cutoff
        cvar = -float(returns[returns <= cutoff].mean())
        measures.append(
            RiskMeasure(
                confidence=confidence,
                value_at_risk=var,
                conditional_value_at_risk=cvar,
                value_at_risk_amount=var * initial_value,
                conditional_value_at_risk_amount=cvar * initial_value,
            )
        )
    return measures


def _tail_checks(symbols: list[str], history: np.ndarray, simulated: np.ndarray) -> list[TailCheck]:
    """Compare each pair's joint worst-5% frequency in the simulation with history.

    A shortfall is flagged when it exceeds both :data:`MIN_TAIL_GAP` and twice the
    standard error of the historical frequency, which rests on only the few days in
    an asset's tail.
    """
    historical, _ = empirical_tail_dependence(history)
    simulated_tail, _ = empirical_tail_dependence(pseudo_observations(pd.DataFrame(simulated)))
    tail_days = len(history) * TAIL_QUANTILE
    checks: list[TailCheck] = []
    for i in range(len(symbols)):
        for j in range(i + 1, len(symbols)):
            observed, modeled = float(historical[i, j]), float(simulated_tail[i, j])
            noise = 2 * math.sqrt(max(observed * (1 - observed), 1e-12) / tail_days)
            checks.append(
                TailCheck(
                    symbols=(symbols[i], symbols[j]),
                    historical_lower_tail=observed,
                    simulated_lower_tail=modeled,
                    understated=observed - modeled > max(MIN_TAIL_GAP, noise),
                )
            )
    return checks


def _validate(
    returns: pd.DataFrame, weights: np.ndarray, horizon: int, paths: int, initial_value: float
) -> None:
    """Reject inconsistent or oversized settings."""
    if returns.empty or returns.isna().any().any():
        raise SimulationError("Returns must be a non-empty table without missing values.")
    if not math.isclose(float(weights.sum()), 1.0, abs_tol=1e-6):
        raise SimulationError("Weights must sum to 1.")
    if horizon < 1 or paths < 2 or initial_value <= 0:
        raise SimulationError(
            "horizon must be at least 1, paths at least 2, initial_value positive."
        )
    if paths * horizon > MAX_PATH_DAYS:
        raise SimulationError(
            f"paths x horizon is {paths * horizon:,}; at most {MAX_PATH_DAYS:,} are allowed."
        )
    draws = paths * horizon * returns.shape[1]
    if draws > MAX_DRAWS:
        raise SimulationError(
            f"paths x horizon x assets is {draws:,}; at most {MAX_DRAWS:,} are allowed. "
            "Use fewer paths, a shorter horizon or fewer assets."
        )


def simulate_portfolio(
    returns: pd.DataFrame,
    weights: dict[str, float],
    horizon: int,
    paths: int,
    dependence: DependenceModel = "student_t",
    marginals: MarginalModel = "empirical",
    initial_value: float = 1.0,
    seed: int | None = None,
    copula: CopulaFit | None = None,
    rebalancing: Rebalancing = "daily",
) -> SimulationSummary:
    """Simulate a portfolio's value ``horizon`` trading days ahead.

    Args:
        returns: Aligned simple daily returns, one column per asset, as built by
            :func:`app.stats.panel.return_panel`.
        weights: Target weight of each column, summing to one.
        horizon: Trading days to simulate.
        paths: Number of simulated paths.
        dependence: ``"gaussian"``, ``"student_t"`` or ``"empirical"``.
        marginals: ``"empirical"`` or ``"student_t"``.
        initial_value: Portfolio value at day 0.
        seed: Seed for the random generator; one is chosen and reported when omitted.
        copula: Copulas already fitted to ``returns``; fitted here when omitted.
        rebalancing: ``"daily"`` to reset to the target weights every day, or ``"none"``
            to buy once and hold, letting weights drift.

    Returns:
        The distribution of terminal value and return, expected return, probability of
        loss, VaR and CVaR at 95% and 99%, maximum drawdowns, daily value percentiles for
        a fan chart, how each asset was modeled, and a check of joint crash frequency.

    Raises:
        SimulationError: If the settings are invalid or the simulation too large.
    """
    symbols = [str(column) for column in returns.columns]
    missing = [symbol for symbol in symbols if symbol not in weights]
    extra = sorted(set(weights) - set(symbols))
    if missing or extra:
        raise SimulationError(
            "There must be one weight per asset; "
            + "; ".join(
                part
                for part in (
                    f"missing: {', '.join(missing)}" if missing else "",
                    f"no returns for: {', '.join(extra)}" if extra else "",
                )
                if part
            )
            + "."
        )
    weight_vector = np.array([weights[symbol] for symbol in symbols], dtype=float)
    _validate(returns, weight_vector, horizon, paths, initial_value)

    dimension = len(symbols)
    values = returns.to_numpy(dtype=float)
    history = pseudo_observations(returns)
    if dimension > 1 and dependence != "empirical" and copula is None:
        copula = fit_copulas(returns)
    fitted = [_fit_marginal(values[:, column], marginals) for column in range(dimension)]

    if seed is None:
        seed = int(np.random.default_rng().integers(2**32))
    rng = np.random.default_rng(seed)

    growth = np.empty((paths, horizon))
    final_weights = np.zeros(dimension)
    tail_sample: list[np.ndarray] = []
    kept = 0
    batch = max(1, BATCH_DRAWS // (horizon * dimension))
    for start in range(0, paths, batch):
        size = min(batch, paths - start)
        uniforms = _draw_uniforms(rng, size * horizon, dependence, copula, history)
        draws = np.column_stack(
            [fitted[column].quantiles(uniforms[:, column]) for column in range(dimension)]
        )
        assets = draws.reshape(size, horizon, dimension)
        if rebalancing == "daily":
            growth[start : start + size] = np.cumprod(1.0 + assets @ weight_vector, axis=1)
        else:
            holdings = np.cumprod(1.0 + assets, axis=1) * weight_vector
            growth[start : start + size] = holdings.sum(axis=2)
            final_weights += (holdings[:, -1] / holdings[:, -1].sum(axis=1, keepdims=True)).sum(
                axis=0
            )
        if kept < TAIL_SAMPLE_DAYS:
            tail_sample.append(draws[: TAIL_SAMPLE_DAYS - kept])
            kept += len(tail_sample[-1])

    path_values = initial_value * np.hstack([np.ones((paths, 1)), growth])
    terminal = path_values[:, -1]
    terminal_returns = terminal / initial_value - 1.0
    peaks = np.maximum.accumulate(path_values, axis=1)
    drawdowns = (path_values / peaks - 1.0).min(axis=1)
    fan = np.percentile(path_values, FAN_PERCENTILES, axis=0)

    checks = _tail_checks(symbols, history, np.vstack(tail_sample)) if dimension > 1 else []
    warnings = [
        f"The simulation has {check.symbols[0]} and {check.symbols[1]} in their worst 5% "
        f"of days together {check.simulated_lower_tail:.0%} of the time, against "
        f"{check.historical_lower_tail:.0%} historically, so it understates how often they "
        "crash together. The empirical dependence model keeps each pair's history."
        for check in checks
        if check.understated
    ]

    scale = math.sqrt(TRADING_DAYS)
    return SimulationSummary(
        paths=paths,
        horizon=horizon,
        initial_value=initial_value,
        dependence=dependence,
        marginals=marginals,
        rebalancing=rebalancing,
        mean_final_weights=(
            None
            if rebalancing == "daily"
            else [
                FinalWeight(symbol=symbol, weight=float(weight / paths))
                for symbol, weight in zip(symbols, final_weights)
            ]
        ),
        copula_degrees_of_freedom=(
            copula.degrees_of_freedom
            if dependence == "student_t" and copula is not None and dimension > 1
            else None
        ),
        seed=seed,
        expected_return=float(terminal_returns.mean()),
        median_return=float(np.median(terminal_returns)),
        probability_of_loss=float((terminal_returns < 0).mean()),
        terminal_value=_distribution(terminal),
        terminal_return=_distribution(terminal_returns),
        risk=_risk(terminal_returns, initial_value),
        max_drawdown=_distribution(drawdowns),
        fan_chart=[
            FanPoint(
                day=day,
                p05=fan[0, day],
                p25=fan[1, day],
                p50=fan[2, day],
                p75=fan[3, day],
                p95=fan[4, day],
            )
            for day in range(horizon + 1)
        ],
        marginal_fits=[
            MarginalFit(
                symbol=symbol,
                model=marginals,
                mean_return=float(values[:, column].mean()),
                volatility=float(values[:, column].std(ddof=1)) * scale,
                degrees_of_freedom=(
                    None if (params := fitted[column].t_params) is None else params[0]
                ),
            )
            for column, symbol in enumerate(symbols)
        ],
        tail_checks=checks,
        warnings=warnings,
    )
