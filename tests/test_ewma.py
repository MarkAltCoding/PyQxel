"""Tests for the EWMA volatility model."""

import math

import numpy as np
import pandas as pd
import pytest

from app.stats import ewma
from app.stats.ewma import ewma_variance, fit_ewma
from app.stats.volatility import InsufficientDataError, log_returns


def _closes(count: int, seed: int = 0) -> pd.Series:
    """Build ``count`` business-day closes following a random walk."""
    rng = np.random.default_rng(seed)
    index = pd.bdate_range("2024-01-01", periods=count)
    return pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, count))), index=index)


def test_variance_recursion_matches_definition() -> None:
    """Each variance mixes the previous variance with the previous squared return."""
    returns = np.array([1.0, -2.0, 0.5])

    variance = ewma_variance(returns, decay=0.9)

    seed = (1.0 + 4.0 + 0.25) / 3
    expected = [seed]
    for value in returns:
        expected.append(0.9 * expected[-1] + 0.1 * value**2)
    np.testing.assert_allclose(variance, expected)


def test_fit_annualizes_and_aligns_series() -> None:
    """Volatilities are annualized, aligned to returns, and the forecast is flat."""
    closes = _closes(200)
    returns = log_returns(closes)

    fit = fit_ewma(closes, periods_per_year=252, horizon=4, decay=0.94)

    variance = ewma_variance(returns.to_numpy(), 0.94)
    scale = math.sqrt(252) / 100.0
    assert fit.model == "ewma"
    assert fit.observations == len(returns) == 199
    assert len(fit.conditional_volatility) == 199
    assert fit.conditional_volatility[0].timestamp == closes.index[1].to_pydatetime()
    assert fit.current_volatility == pytest.approx(math.sqrt(variance[-2]) * scale)
    assert [step.step for step in fit.forecast] == [1, 2, 3, 4]
    assert [step.volatility for step in fit.forecast] == pytest.approx(
        [math.sqrt(variance[-1]) * scale] * 4
    )
    assert fit.realized_volatility == pytest.approx(float(returns.std(ddof=1)) * scale)
    assert fit.half_life == pytest.approx(math.log(0.5) / math.log(0.94))
    assert fit.warnings == []


def test_fit_tracks_a_volatility_jump() -> None:
    """After volatility triples, the estimate moves most of the way within a few weeks."""
    rng = np.random.default_rng(3)
    shocks = np.concatenate([rng.normal(0, 0.01, 250), rng.normal(0, 0.03, 60)])
    closes = pd.Series(
        100.0 * np.exp(np.cumsum(shocks)), index=pd.bdate_range("2024-01-01", periods=310)
    )

    fit = fit_ewma(closes, periods_per_year=252)

    calm = fit.conditional_volatility[240].volatility
    assert calm == pytest.approx(0.01 * math.sqrt(252), rel=0.35)
    assert fit.current_volatility == pytest.approx(0.03 * math.sqrt(252), rel=0.35)


def test_fit_warns_on_short_history() -> None:
    """A history too short to wash out the seed is flagged but still estimated."""
    fit = fit_ewma(_closes(ewma.MIN_OBSERVATIONS + 1), periods_per_year=252)

    assert fit.observations == ewma.MIN_OBSERVATIONS
    assert len(fit.warnings) == 1
    assert "seed variance" in fit.warnings[0]


def test_fit_rejects_too_few_returns() -> None:
    """Fewer returns than needed to seed the recursion are rejected."""
    with pytest.raises(InsufficientDataError, match="EWMA needs at least"):
        fit_ewma(_closes(ewma.MIN_OBSERVATIONS), periods_per_year=252)


def test_fit_rejects_constant_prices() -> None:
    """A series that never moves has no volatility to estimate."""
    flat = pd.Series(50.0, index=pd.bdate_range("2024-01-01", periods=100))

    with pytest.raises(InsufficientDataError, match="never change"):
        fit_ewma(flat, periods_per_year=252)


@pytest.mark.parametrize("decay", [0.0, 1.0, -0.5])
def test_fit_rejects_out_of_range_decay(decay: float) -> None:
    """Decays outside the open unit interval are rejected."""
    with pytest.raises(ValueError, match="decay"):
        fit_ewma(_closes(100), periods_per_year=252, decay=decay)
