"""Tests for the Monte Carlo portfolio simulation.

Histories are simulated with known properties, so results are checked against what the
model must produce, without any network access.
"""

from typing import Any

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from app.stats import monte_carlo
from app.stats.copulas import fit_copulas, pseudo_observations
from app.stats.monte_carlo import (
    MAX_DRAWS,
    MIN_RETURN,
    SimulationError,
    _draw_uniforms,
    _empirical_quantiles,
    _fit_marginal,
    simulate_portfolio,
)


def _normal_history(days: int = 2_000, seed: int = 0) -> pd.DataFrame:
    """Two assets with independent normal daily returns of different means and risks."""
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"A": rng.normal(0.0005, 0.01, days), "B": rng.normal(0.0002, 0.02, days)})


def _crash_history(days: int = 2_000, seed: int = 1) -> pd.DataFrame:
    """Two loosely related assets that share their crashes: 5% of days both fall hard."""
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.0005, 0.01, (days, 2))
    crashes = rng.uniform(size=days) < 0.05
    returns[crashes] -= rng.uniform(0.03, 0.06, (int(crashes.sum()), 1))
    return pd.DataFrame(returns, columns=["A", "B"])


def test_one_day_simulation_matches_the_history() -> None:
    """With historical marginals, a one-day horizon reproduces the daily distribution."""
    history = _normal_history()[["A"]]

    summary = simulate_portfolio(
        history, {"A": 1.0}, horizon=1, paths=200_000, marginals="empirical", seed=1
    )

    daily = history["A"]
    assert summary.expected_return == pytest.approx(daily.mean(), abs=1e-4)
    assert summary.terminal_return.std == pytest.approx(daily.std(), rel=0.02)
    assert summary.risk[0].value_at_risk == pytest.approx(-daily.quantile(0.05), rel=0.03)
    assert summary.probability_of_loss == pytest.approx((daily < 0).mean(), abs=0.01)


def test_returns_compound_over_the_horizon() -> None:
    """The mean horizon return compounds the mean daily return of the weighted assets."""
    history = _normal_history()
    weights = {"A": 0.7, "B": 0.3}
    daily = 0.7 * history["A"].mean() + 0.3 * history["B"].mean()

    summary = simulate_portfolio(
        history, weights, horizon=63, paths=20_000, dependence="empirical", seed=2
    )

    assert summary.expected_return == pytest.approx((1 + daily) ** 63 - 1, abs=0.003)


def test_weights_are_reset_every_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """Assets alternately gaining and losing 10% hold a 50/50 portfolio's value flat.

    Daily rebalancing makes each day's portfolio return the weighted sum of the assets',
    here zero; buy-and-hold would drift as one asset grew larger than the other.
    """
    history = pd.DataFrame({"UP": [0.10, 0.10, 0.10], "DOWN": [-0.10, -0.10, -0.10]})
    monkeypatch.setattr(
        monte_carlo,
        "_draw_uniforms",
        lambda rng, count, dependence, copula, history: np.full((count, 2), 0.5),
    )

    summary = simulate_portfolio(
        history,
        {"UP": 0.5, "DOWN": 0.5},
        horizon=10,
        paths=4,
        dependence="empirical",
        initial_value=1_000,
    )

    assert summary.terminal_value.mean == pytest.approx(1_000)
    assert all(point.p50 == pytest.approx(1_000) for point in summary.fan_chart)


def test_seed_reproduces_paths_and_is_reported() -> None:
    """The same seed gives the same result; without one, the seed used is returned."""
    history = _normal_history(500)
    args: dict[str, Any] = {"weights": {"A": 0.5, "B": 0.5}, "horizon": 21, "paths": 2_000}

    first = simulate_portfolio(history, seed=42, **args)
    again = simulate_portfolio(history, seed=42, **args)
    other = simulate_portfolio(history, seed=43, **args)
    unseeded = simulate_portfolio(history, **args)
    replay = simulate_portfolio(history, seed=unseeded.seed, **args)

    assert first == again
    assert first.terminal_value != other.terminal_value
    assert unseeded.seed is not None
    assert replay == unseeded


def test_given_copula_matches_fitting_it_here() -> None:
    """Passing copulas already fitted to the returns gives the same paths as fitting them."""
    history = _normal_history(500)
    args: dict[str, Any] = {"weights": {"A": 0.5, "B": 0.5}, "horizon": 5, "paths": 500, "seed": 3}

    fitted_here = simulate_portfolio(history, **args)
    given = simulate_portfolio(history, copula=fit_copulas(history), **args)

    assert given == fitted_here


def test_risk_measures_are_consistent() -> None:
    """CVaR is at least VaR, 99% is beyond 95%, and amounts scale by the starting value."""
    summary = simulate_portfolio(
        _crash_history(),
        {"A": 0.5, "B": 0.5},
        horizon=21,
        paths=10_000,
        initial_value=250_000,
        seed=4,
    )

    at_95, at_99 = summary.risk
    assert (at_95.confidence, at_99.confidence) == (0.95, 0.99)
    assert at_95.conditional_value_at_risk >= at_95.value_at_risk
    assert at_99.value_at_risk >= at_95.value_at_risk
    assert at_99.conditional_value_at_risk >= at_99.value_at_risk
    assert at_95.value_at_risk_amount == pytest.approx(at_95.value_at_risk * 250_000)
    percentile_5 = summary.terminal_return.percentiles[1]
    assert percentile_5.percentile == 5
    assert at_95.value_at_risk == pytest.approx(-percentile_5.value, rel=1e-6)


def test_fan_chart_and_drawdowns() -> None:
    """The fan starts at the initial value, widens in order, and drawdowns are never positive."""
    summary = simulate_portfolio(
        _normal_history(), {"A": 0.5, "B": 0.5}, horizon=30, paths=5_000, seed=5
    )

    assert len(summary.fan_chart) == 31
    start, end = summary.fan_chart[0], summary.fan_chart[-1]
    assert start.p05 == start.p95 == 1.0
    assert end.p05 < end.p25 < end.p50 < end.p75 < end.p95
    assert end.p50 == pytest.approx(summary.terminal_value.percentiles[4].value)
    assert summary.max_drawdown.percentiles[-1].value <= 0
    assert summary.max_drawdown.mean < 0


def test_paths_that_only_rise_have_no_drawdown() -> None:
    """With every historical day a gain, no path ever falls below its peak."""
    rng = np.random.default_rng(6)
    history = pd.DataFrame({"A": rng.uniform(0.001, 0.01, 500)})

    summary = simulate_portfolio(history, {"A": 1.0}, horizon=20, paths=1_000, seed=6)

    assert summary.max_drawdown.mean == 0
    assert summary.probability_of_loss == 0


def test_gaussian_copula_understates_shared_crashes() -> None:
    """Crashes that come together are flagged under a Gaussian copula and kept by empirical."""
    history = _crash_history()
    args: dict[str, Any] = {
        "weights": {"A": 0.5, "B": 0.5},
        "horizon": 5,
        "paths": 20_000,
        "seed": 7,
    }

    gaussian = simulate_portfolio(history, dependence="gaussian", **args)
    empirical = simulate_portfolio(history, dependence="empirical", **args)

    (check,) = gaussian.tail_checks
    assert check.understated
    assert check.historical_lower_tail > 0.6
    assert "understates how often they crash together" in gaussian.warnings[0]
    (kept,) = empirical.tail_checks
    assert kept.simulated_lower_tail == pytest.approx(kept.historical_lower_tail, abs=0.03)
    assert not kept.understated
    assert empirical.warnings == []
    assert empirical.risk[1].value_at_risk > gaussian.risk[1].value_at_risk


def test_copula_draws_carry_the_fitted_correlation() -> None:
    """Gaussian and t copula draws have the fitted correlation between normal scores."""
    rng = np.random.default_rng(8)
    correlation = np.array([[1.0, 0.7], [0.7, 1.0]])
    history = pd.DataFrame(rng.multivariate_normal([0, 0], correlation, 3_000), columns=["A", "B"])
    copula = fit_copulas(history)

    for dependence in ("gaussian", "student_t"):
        uniforms = _draw_uniforms(
            np.random.default_rng(9), 100_000, dependence, copula, pseudo_observations(history)
        )
        assert uniforms.min() > 0 and uniforms.max() < 1
        tau = stats.kendalltau(uniforms[:10_000, 0], uniforms[:10_000, 1]).statistic
        assert tau == pytest.approx(copula.kendall_tau[0, 1], abs=0.03)


def test_empirical_dependence_resamples_historical_days() -> None:
    """Every empirical draw is one of the historical days' rank vectors."""
    history = pseudo_observations(_normal_history(50))

    uniforms = _draw_uniforms(np.random.default_rng(10), 1_000, "empirical", None, history)

    historical_rows = {tuple(row) for row in history}
    assert all(tuple(row) in historical_rows for row in uniforms)


def test_empirical_quantiles_return_historical_values_at_their_ranks() -> None:
    """The k-th pseudo-observation maps to the k-th smallest return; between, it interpolates."""
    returns = np.array([-0.02, 0.0, 0.01, 0.05])

    mapped = _empirical_quantiles(returns, np.array([0.2, 0.4, 0.8, 0.5, 0.01, 0.99]))

    assert mapped.tolist() == pytest.approx([-0.02, 0.0, 0.05, 0.005, -0.02, 0.05])


def test_marginals_respect_history_or_fit_a_t() -> None:
    """Empirical draws stay within history; a t fit recovers heavy tails and is floored."""
    rng = np.random.default_rng(11)
    t_returns = 0.01 * rng.standard_t(4, 5_000)
    uniforms = rng.uniform(1e-6, 1 - 1e-6, 100_000)

    empirical = _fit_marginal(t_returns, "empirical").quantiles(uniforms)
    fitted = _fit_marginal(t_returns, "student_t")

    assert empirical.min() >= t_returns.min() and empirical.max() <= t_returns.max()
    assert fitted.t_params is not None
    assert fitted.t_params[0] == pytest.approx(4, rel=0.25)
    assert fitted.quantiles(np.array([1e-12]))[0] == MIN_RETURN


def test_infinite_variance_fits_are_floored() -> None:
    """Cauchy-like returns would fit under two degrees of freedom; the floor applies."""
    rng = np.random.default_rng(12)

    fitted = _fit_marginal(0.01 * rng.standard_cauchy(5_000), "student_t")

    assert fitted.t_params is not None
    assert fitted.t_params[0] == monte_carlo.MIN_DEGREES_OF_FREEDOM


def test_summary_describes_the_model() -> None:
    """The summary reports settings, each asset's fit, and the copula's degrees of freedom."""
    history = _crash_history()

    summary = simulate_portfolio(
        history,
        {"A": 0.6, "B": 0.4},
        horizon=5,
        paths=1_000,
        dependence="student_t",
        marginals="student_t",
        seed=13,
    )

    assert (summary.dependence, summary.marginals, summary.horizon, summary.paths) == (
        "student_t",
        "student_t",
        5,
        1_000,
    )
    assert summary.copula_degrees_of_freedom is not None
    assert [fit.symbol for fit in summary.marginal_fits] == ["A", "B"]
    assert all(fit.degrees_of_freedom is not None for fit in summary.marginal_fits)
    assert summary.marginal_fits[0].volatility == pytest.approx(history["A"].std() * np.sqrt(252))


def test_single_asset_needs_no_copula() -> None:
    """One asset simulates without fitting a copula or checking pairs."""
    summary = simulate_portfolio(
        _normal_history()[["B"]], {"B": 1.0}, horizon=5, paths=1_000, seed=14
    )

    assert summary.copula_degrees_of_freedom is None
    assert summary.tail_checks == []


@pytest.mark.parametrize(
    ("weights", "options", "message"),
    [
        ({"A": 0.5}, {}, "one weight per asset; missing: B."),
        ({"A": 0.5, "B": 0.25, "C": 0.25}, {}, "no returns for: C."),
        ({"A": 0.5, "B": 0.6}, {}, "sum to 1"),
        ({"A": 0.5, "B": 0.5}, {"horizon": 0}, "horizon must be at least 1"),
        ({"A": 0.5, "B": 0.5}, {"paths": 1}, "paths at least 2"),
        ({"A": 0.5, "B": 0.5}, {"initial_value": 0}, "initial_value positive"),
        ({"A": 0.5, "B": 0.5}, {"paths": 20_000, "horizon": 252}, "paths x horizon is"),
    ],
)
def test_invalid_settings_are_rejected(
    weights: dict[str, float], options: dict[str, object], message: str
) -> None:
    """Missing or unbalanced weights, empty runs and oversized runs are refused."""
    settings = {"horizon": 5, "paths": 100, **options}

    with pytest.raises(SimulationError, match=message):
        simulate_portfolio(_normal_history(300), weights, **settings)  # type: ignore[arg-type]


def test_draw_budget_counts_assets() -> None:
    """Many assets over many paths exceed the draw budget before any work is done."""
    history = pd.DataFrame(
        np.random.default_rng(15).normal(0, 0.01, (300, 20)), columns=[f"S{i}" for i in range(20)]
    )
    paths = MAX_DRAWS // (252 * 20) + 1

    with pytest.raises(SimulationError, match="paths x horizon x assets"):
        simulate_portfolio(history, {f"S{i}": 0.05 for i in range(20)}, horizon=252, paths=paths)


def test_missing_returns_are_rejected() -> None:
    """Returns with gaps must be aligned first."""
    history = _normal_history(300)
    history.iloc[3, 0] = np.nan

    with pytest.raises(SimulationError, match="without missing values"):
        simulate_portfolio(history, {"A": 0.5, "B": 0.5}, horizon=5, paths=100)
