"""Tests for fitting Gaussian and Student t copulas.

Returns are simulated from copulas with known parameters, so fits are checked against
the truth, and likelihoods against scipy's multivariate densities.
"""

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from app.stats.copulas import (
    MAX_DEGREES_OF_FREEDOM,
    asynchronous_trading_warning,
    empirical_tail_dependence,
    fit_copulas,
    gaussian_log_likelihood,
    kendall_tau_matrix,
    nearest_correlation,
    pseudo_observations,
    t_log_likelihood,
    t_tail_dependence,
)

CORRELATION = np.array([[1.0, 0.6, 0.3], [0.6, 1.0, 0.4], [0.3, 0.4, 1.0]])


def _t_copula_returns(df: float, observations: int = 2_500, seed: int = 1) -> pd.DataFrame:
    """Returns whose dependence is a t copula with ``CORRELATION`` and ``df``.

    The marginals are deliberately not t with ``df``, to show the fit ignores them.
    """
    rng = np.random.default_rng(seed)
    normal = rng.multivariate_normal(np.zeros(3), CORRELATION, size=observations)
    mixing = np.sqrt(rng.chisquare(df, size=observations) / df)
    uniforms = stats.t.cdf(normal / mixing[:, None], df)
    return pd.DataFrame(0.01 * stats.laplace.ppf(uniforms), columns=["A", "B", "C"])


def _gaussian_returns(observations: int = 2_500, seed: int = 2) -> pd.DataFrame:
    """Returns whose dependence is a Gaussian copula with ``CORRELATION``."""
    rng = np.random.default_rng(seed)
    normal = rng.multivariate_normal(np.zeros(3), CORRELATION, size=observations)
    return pd.DataFrame(0.01 * normal, columns=["A", "B", "C"])


def test_pseudo_observations_are_scaled_ranks() -> None:
    """Ranks are divided by n + 1, and tied values share their average rank."""
    returns = pd.DataFrame({"A": [0.03, -0.01, 0.02], "B": [0.0, 0.0, 0.05]})

    uniforms = pseudo_observations(returns)

    assert uniforms[:, 0].tolist() == pytest.approx([0.75, 0.25, 0.5])
    assert uniforms[:, 1].tolist() == pytest.approx([0.375, 0.375, 0.75])


def test_kendall_tau_matrix_matches_scipy() -> None:
    """Each entry is scipy's tau-b for that pair; the matrix is symmetric with a unit diagonal."""
    returns = _gaussian_returns(300)

    tau = kendall_tau_matrix(returns)

    expected = stats.kendalltau(returns["A"], returns["C"]).statistic
    assert tau[0, 2] == tau[2, 0] == pytest.approx(expected)
    assert np.diag(tau).tolist() == [1.0, 1.0, 1.0]


def test_nearest_correlation_leaves_valid_matrices_alone() -> None:
    """A positive definite correlation matrix comes back unchanged."""
    np.testing.assert_allclose(nearest_correlation(CORRELATION), CORRELATION, atol=1e-12)


def test_nearest_correlation_repairs_inconsistent_pairs() -> None:
    """Pairwise estimates that cannot coexist become a positive definite correlation matrix."""
    impossible = np.array([[1.0, 0.9, -0.9], [0.9, 1.0, 0.9], [-0.9, 0.9, 1.0]])

    repaired = nearest_correlation(impossible)

    assert np.linalg.eigvalsh(repaired).min() > 0
    np.testing.assert_allclose(np.diag(repaired), 1.0)
    np.testing.assert_allclose(repaired, repaired.T)


def test_gaussian_likelihood_matches_scipy() -> None:
    """The copula density is the joint normal density over the product of its marginals."""
    uniforms = pseudo_observations(_gaussian_returns(200))
    scores = stats.norm.ppf(uniforms)

    expected = np.sum(
        stats.multivariate_normal(np.zeros(3), CORRELATION).logpdf(scores)
        - stats.norm.logpdf(scores).sum(axis=1)
    )

    assert gaussian_log_likelihood(uniforms, CORRELATION) == pytest.approx(expected)
    assert gaussian_log_likelihood(uniforms, np.eye(3)) == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize("df", [3.0, 7.5])
def test_t_likelihood_matches_scipy(df: float) -> None:
    """The t copula density is the joint t density over the product of its marginals."""
    uniforms = pseudo_observations(_t_copula_returns(df, 200))
    points = stats.t.ppf(uniforms, df)

    # scipy-stubs types df as an int, but scipy takes any positive float.
    joint = stats.multivariate_t(np.zeros(3), CORRELATION, df=df)  # type: ignore[call-overload]
    expected = np.sum(joint.logpdf(points) - stats.t.logpdf(points, df).sum(axis=1))

    assert t_log_likelihood(uniforms, CORRELATION, df) == pytest.approx(expected)


@pytest.mark.parametrize("df", [3.0, 6.0])
def test_t_copula_fit_recovers_its_parameters(df: float) -> None:
    """Degrees of freedom and correlations come back close, and AIC prefers the t copula."""
    fit = fit_copulas(_t_copula_returns(df))

    assert fit.degrees_of_freedom == pytest.approx(df, rel=0.2)
    np.testing.assert_allclose(fit.t_correlation, CORRELATION, atol=0.06)
    assert fit.t_aic < fit.gaussian_aic - 10
    assert not fit.degrees_of_freedom_at_bound


def test_gaussian_data_push_degrees_of_freedom_to_the_bound() -> None:
    """Without joint extremes, the t copula collapses to the Gaussian, which AIC favors."""
    fit = fit_copulas(_gaussian_returns())

    assert fit.degrees_of_freedom_at_bound
    assert fit.degrees_of_freedom <= MAX_DEGREES_OF_FREEDOM
    np.testing.assert_allclose(fit.gaussian_correlation, CORRELATION, atol=0.03)
    assert fit.gaussian_aic < fit.t_aic


def test_fit_ignores_each_assets_own_distribution() -> None:
    """Monotone transformations of single assets leave the copula fit unchanged."""
    returns = _t_copula_returns(4.0, 800)
    transformed = returns.assign(A=np.exp(50 * returns["A"]), B=returns["B"] ** 3)

    original, changed = fit_copulas(returns), fit_copulas(transformed)

    assert changed.degrees_of_freedom == pytest.approx(original.degrees_of_freedom)
    np.testing.assert_allclose(changed.gaussian_correlation, original.gaussian_correlation)
    np.testing.assert_allclose(changed.kendall_tau, original.kendall_tau)


def test_fit_reports_matrices_in_column_order() -> None:
    """Symbols follow the columns and every matrix is square in that order."""
    fit = fit_copulas(_gaussian_returns(400)[["C", "A", "B"]])

    assert fit.symbols == ["C", "A", "B"]
    assert fit.observations == 400
    for matrix in (fit.kendall_tau, fit.gaussian_correlation, fit.t_correlation):
        assert matrix.shape == (3, 3)
    assert fit.gaussian_correlation[1, 2] == pytest.approx(0.6, abs=0.1)


def test_t_tail_dependence_follows_the_closed_form() -> None:
    """The textbook value for rho 0.6 and 3 degrees of freedom, and its limits."""
    tail = t_tail_dependence(np.array([[1.0, 0.6], [0.6, 1.0]]), 3.0)

    assert tail[0, 1] == pytest.approx(2 * stats.t.cdf(-1.0, 4))
    assert tail[0, 1] == pytest.approx(0.374, abs=1e-3)
    assert tail[0, 0] == 1.0
    fatter = t_tail_dependence(np.array([[1.0, 0.6], [0.6, 1.0]]), 2.5)[0, 1]
    thinner = t_tail_dependence(np.array([[1.0, 0.6], [0.6, 1.0]]), 30.0)[0, 1]
    assert thinner < tail[0, 1] < fatter


def test_empirical_tails_of_comonotonic_and_independent_assets() -> None:
    """Assets that move as one share every extreme day; independent ones about 5%."""
    rng = np.random.default_rng(3)
    base = rng.normal(size=20_000)
    returns = pd.DataFrame({"A": base, "B": 2 * base, "C": rng.normal(size=20_000)})

    lower, upper = empirical_tail_dependence(pseudo_observations(returns))

    assert lower[0, 1] == pytest.approx(1.0)
    assert upper[0, 1] == pytest.approx(1.0)
    assert lower[0, 2] == pytest.approx(0.05, abs=0.015)


def test_markets_hours_apart_are_flagged() -> None:
    """New York with Tokyo is flagged, naming each zone's symbols."""
    warning = asynchronous_trading_warning(
        {"SPY": "America/New_York", "QQQ": "America/New_York", "7203.T": "Asia/Tokyo"}
    )

    assert warning is not None
    assert "America/New_York: SPY, QQQ; Asia/Tokyo: 7203.T" in warning


@pytest.mark.parametrize(
    "timezones",
    [
        {"SPY": "America/New_York", "CME": "America/Chicago"},
        {"SPY": "America/New_York", "X": None},
        {"SPY": "America/New_York", "X": "Not/AZone"},
    ],
)
def test_nearby_or_unknown_zones_are_not_flagged(timezones: dict[str, str | None]) -> None:
    """Zones an hour apart, or unknown zones, give no warning."""
    assert asynchronous_trading_warning(timezones) is None
