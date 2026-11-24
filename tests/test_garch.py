"""Tests for the GARCH volatility model.

The fit is replaced with a fake in most tests, so they check what is done with its
result; one test fits the real ``arch`` model to a simulated GARCH process.
"""

import math
from dataclasses import replace
from typing import Any

import numpy as np
import pandas as pd
import pytest

from app.models.volatility import GarchFit, VolatilityForecastStep, VolatilityPoint
from app.stats import garch
from app.stats.garch import ModelFitError, RawFit, fit_garch, summarize_garch
from app.stats.volatility import MAX_HORIZON, InsufficientDataError, log_returns


def _closes(count: int, seed: int = 0) -> pd.Series:
    """Build ``count`` business-day closes following a random walk."""
    rng = np.random.default_rng(seed)
    index = pd.bdate_range("2024-01-01", periods=count)
    return pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, count))), index=index)


def _raw(observations: int, horizon: int = 3) -> RawFit:
    """A converged fit, in per-bar percent."""
    return RawFit(
        names=["mu", "omega", "alpha1", "beta1"],
        estimates=[0.05, 0.02, 0.08, 0.9],
        std_errors=[0.01, None, 0.02, 0.03],
        sigma=[1.0 if bar < observations - 1 else 2.0 for bar in range(observations)],
        forecast_sigma=[1.5 for _ in range(horizon)],
        persistence=0.98,
        unconditional_sigma=1.0,
        log_likelihood=-500.0,
        aic=3.1,
        bic=3.2,
    )


def _serve_fit(monkeypatch: pytest.MonkeyPatch, raw: RawFit | Exception) -> list[tuple[Any, ...]]:
    """Make the model's fit return ``raw`` (or raise it), recording what it was given."""
    calls: list[tuple[Any, ...]] = []

    def fake_fit(returns: np.ndarray, horizon: int, distribution: str) -> RawFit:
        calls.append((returns, horizon, distribution))
        if isinstance(raw, Exception):
            raise raw
        return raw

    monkeypatch.setattr(garch, "_fit_arch", fake_fit)
    return calls


def test_log_returns_drops_invalid_closes() -> None:
    """Missing, non-positive, and duplicated closes are removed before differencing."""
    index = pd.to_datetime(["2026-01-05", "2026-01-02", "2026-01-06", "2026-01-07", "2026-01-07"])
    closes = pd.Series([math.nan, 100.0, -1.0, 110.0, 121.0], index=index)

    returns = log_returns(closes)

    assert list(returns.index) == [pd.Timestamp("2026-01-07")]
    assert returns.iloc[0] == pytest.approx(100.0 * math.log(1.21))


@pytest.mark.asyncio
async def test_fit_rejects_short_series(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fewer returns than the minimum fail before anything is fitted."""
    calls = _serve_fit(monkeypatch, _raw(1))

    with pytest.raises(InsufficientDataError, match="at least"):
        await fit_garch(_closes(garch.MIN_OBSERVATIONS), periods_per_year=252)
    assert calls == []


@pytest.mark.asyncio
async def test_fit_rejects_constant_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    """A series that never moves has no volatility to model."""
    _serve_fit(monkeypatch, _raw(1))
    flat = pd.Series(50.0, index=pd.bdate_range("2024-01-01", periods=600))

    with pytest.raises(InsufficientDataError, match="never change"):
        await fit_garch(flat, periods_per_year=252)


@pytest.mark.parametrize("horizon", [0, MAX_HORIZON + 1])
@pytest.mark.asyncio
async def test_fit_rejects_out_of_range_horizon(horizon: int) -> None:
    """Forecast horizons outside the supported range are rejected."""
    with pytest.raises(ValueError, match="horizon"):
        await fit_garch(_closes(600), periods_per_year=252, horizon=horizon)


@pytest.mark.asyncio
async def test_fit_passes_percent_returns_and_annualizes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The model is given percent log returns and its per-bar percent fit is annualized."""
    closes = _closes(600)
    calls = _serve_fit(monkeypatch, _raw(599))

    fit = await fit_garch(closes, periods_per_year=252, horizon=3, distribution="norm")

    returns, horizon, distribution = calls[0]
    assert (horizon, distribution) == (3, "norm")
    np.testing.assert_allclose(returns, 100.0 * np.diff(np.log(closes.to_numpy())))

    scale = math.sqrt(252) / 100.0
    assert fit.observations == 599
    assert fit.distribution == "norm"
    assert fit.current_volatility == pytest.approx(2.0 * scale)
    assert fit.long_run_volatility == pytest.approx(scale)
    assert fit.realized_volatility == pytest.approx(float(np.std(returns, ddof=1)) * scale)
    assert [step.volatility for step in fit.forecast] == pytest.approx([1.5 * scale] * 3)
    assert [step.step for step in fit.forecast] == [1, 2, 3]
    assert fit.half_life == pytest.approx(math.log(0.5) / math.log(0.98))
    assert fit.conditional_volatility[0].timestamp == closes.index[1].to_pydatetime()
    assert fit.parameters[1].name == "omega"
    assert fit.parameters[1].std_error is None
    assert len(fit.warnings) == 1
    assert "599 returns" in fit.warnings[0]


@pytest.mark.asyncio
async def test_fit_without_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    """A long sample with moderate persistence and clear clustering has no warnings."""
    count = garch.RECOMMENDED_OBSERVATIONS + 1
    _serve_fit(monkeypatch, _raw(count - 1))

    fit = await fit_garch(_closes(count), periods_per_year=252)

    assert fit.warnings == []


@pytest.mark.asyncio
async def test_fit_warns_on_boundary_estimates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Near-integrated persistence and no ARCH effect are each flagged."""
    count = garch.RECOMMENDED_OBSERVATIONS + 1
    _serve_fit(
        monkeypatch,
        replace(_raw(count - 1), estimates=[0.05, 0.001, 0.0, 0.999], persistence=0.999),
    )

    fit = await fit_garch(_closes(count), periods_per_year=252)

    assert len(fit.warnings) == 2
    assert "Persistence is 0.999" in fit.warnings[0]
    assert "no volatility clustering" in fit.warnings[1]


@pytest.mark.asyncio
async def test_fit_without_mean_reversion(monkeypatch: pytest.MonkeyPatch) -> None:
    """Persistence at or above 1 has no half-life or long-run level."""
    _serve_fit(monkeypatch, replace(_raw(599), persistence=1.0, unconditional_sigma=None))

    fit = await fit_garch(_closes(600), periods_per_year=252)

    assert fit.half_life is None
    assert fit.long_run_volatility is None


@pytest.mark.asyncio
async def test_fit_reports_non_convergence(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fit the optimizer could not finish raises, saying so."""
    _serve_fit(monkeypatch, ModelFitError("GARCH fit failed: the optimizer did not converge."))

    with pytest.raises(ModelFitError, match="did not converge"):
        await fit_garch(_closes(600), periods_per_year=252)


@pytest.mark.asyncio
async def test_fit_rejects_mismatched_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """A volatility series that does not line up with the returns is an invalid fit."""
    _serve_fit(monkeypatch, _raw(10))

    with pytest.raises(ModelFitError, match="volatilities"):
        await fit_garch(_closes(600), periods_per_year=252)


@pytest.mark.parametrize("distribution", ["norm", "std"])
@pytest.mark.asyncio
async def test_the_real_model_recovers_a_garch_process(distribution: str) -> None:
    """``arch`` fits a simulated GARCH(1,1) and recovers its persistence and level."""
    rng = np.random.default_rng(7)
    count = 1500
    variance = np.empty(count)
    shocks = np.empty(count)
    variance[0] = 1.0
    for t in range(count):
        if t:
            variance[t] = 0.05 + 0.1 * shocks[t - 1] ** 2 + 0.85 * variance[t - 1]
        shocks[t] = math.sqrt(variance[t]) * rng.standard_normal()
    index = pd.bdate_range("2020-01-01", periods=count + 1)
    closes = pd.Series(
        100.0 * np.exp(np.concatenate([[0.0], np.cumsum(shocks / 100.0)])), index=index
    )

    fit = await fit_garch(
        closes, periods_per_year=252, horizon=5, distribution=distribution  # type: ignore[arg-type]
    )

    params = {parameter.name: parameter.estimate for parameter in fit.parameters}
    expected = {"mu", "omega", "alpha1", "beta1"} | ({"shape"} if distribution == "std" else set())
    assert set(params) == expected
    assert 0.8 < fit.persistence < 1.0
    assert all(parameter.std_error is not None for parameter in fit.parameters)
    assert len(fit.conditional_volatility) == count
    assert len(fit.forecast) == 5
    # True long-run variance is 0.05 / (1 - 0.95) = 1 (%^2 per day).
    assert fit.long_run_volatility == pytest.approx(math.sqrt(252) / 100.0, rel=0.3)


@pytest.mark.asyncio
async def test_summary_places_today_and_averages_the_forecast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An analysis gets today's percentile and horizon averages, not the in-sample series."""
    count = garch.RECOMMENDED_OBSERVATIONS + 1
    _serve_fit(
        monkeypatch,
        replace(
            _raw(count - 1, horizon=63),
            forecast_sigma=[2.0 if step == 1 else 1.0 for step in range(1, 64)],
        ),
    )
    fit = await fit_garch(_closes(count), periods_per_year=252, horizon=63)

    summary = summarize_garch(fit)

    scale = math.sqrt(252) / 100  # The model works in daily percent.
    # Today's 2% daily volatility is above every other fitted day's 1%.
    assert summary.current_percentile == pytest.approx(100 * (count - 1.5) / (count - 1))
    assert summary.current_volatility == pytest.approx(2.0 * scale)
    assert [step.days for step in summary.forecast] == [1, 5, 21, 63]
    assert summary.forecast[0].volatility == pytest.approx(2.0 * scale)
    # Five days: variances 4, 1, 1, 1, 1 average 8 / 5.
    assert summary.forecast[1].volatility == pytest.approx(math.sqrt(8 / 5) * scale)
    assert summary.start == _closes(count).index[1].date()
    assert summary.end == _closes(count).index[-1].date()
    assert summary.half_life_days == fit.half_life


def test_summary_leaves_out_horizons_beyond_the_forecast() -> None:
    fit = GarchFit(
        distribution="std",
        observations=2,
        parameters=[],
        persistence=0.9,
        current_volatility=0.2,
        realized_volatility=0.2,
        conditional_volatility=[
            VolatilityPoint(timestamp=pd.Timestamp("2025-01-06").to_pydatetime(), volatility=0.2)
        ],
        forecast=[VolatilityForecastStep(step=step, volatility=0.2) for step in range(1, 6)],
        log_likelihood=0.0,
        aic=0.0,
        bic=0.0,
    )

    assert [step.days for step in summarize_garch(fit).forecast] == [1, 5]
