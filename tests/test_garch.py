"""Tests for the GARCH volatility wrapper.

The R call is replaced with a fake in all but the last test, which runs the real
model and is skipped when R or the ``rugarch`` package is unavailable.
"""

import math
from typing import Any

import numpy as np
import pandas as pd
import pytest

from app.stats import garch, r_bridge
from app.stats.garch import ModelFitError, fit_garch
from app.stats.r_bridge import RError, RUnavailableError, RValue
from app.stats.volatility import MAX_HORIZON, InsufficientDataError, log_returns


def _closes(count: int, seed: int = 0) -> pd.Series:
    """Build ``count`` business-day closes following a random walk."""
    rng = np.random.default_rng(seed)
    index = pd.bdate_range("2024-01-01", periods=count)
    return pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, count))), index=index)


def _r_output(observations: int, horizon: int = 3) -> dict[str, RValue]:
    """Build a converged result shaped like ``pyqxel_fit_garch``'s, in per-bar percent."""
    return {
        "converged": [True],
        "message": [""],
        "coef_names": ["mu", "omega", "alpha1", "beta1"],
        "coef_values": [0.05, 0.02, 0.08, 0.9],
        "coef_std_errors": [0.01, None, 0.02, 0.03],
        "sigma": [1.0 if bar < observations - 1 else 2.0 for bar in range(observations)],
        "forecast_sigma": [1.5 for _ in range(horizon)],
        "persistence": [0.98],
        "unconditional_sigma": [1.0],
        "log_likelihood": [-500.0],
        "aic": [3.1],
        "bic": [3.2],
    }


def _serve_r(monkeypatch: pytest.MonkeyPatch, output: dict[str, RValue]) -> list[tuple[Any, ...]]:
    """Make the wrapper's R call return ``output`` and record the arguments it received."""
    calls: list[tuple[Any, ...]] = []

    async def fake_call_r(script: str, function: str, *args: object) -> dict[str, RValue]:
        calls.append((script, function, *args))
        return output

    monkeypatch.setattr(garch, "call_r", fake_call_r)
    return calls


def test_log_returns_drops_invalid_closes() -> None:
    """Missing, non-positive and duplicated closes are removed before differencing."""
    index = pd.to_datetime(["2026-01-05", "2026-01-02", "2026-01-06", "2026-01-07", "2026-01-07"])
    closes = pd.Series([math.nan, 100.0, -1.0, 110.0, 121.0], index=index)

    returns = log_returns(closes)

    assert list(returns.index) == [pd.Timestamp("2026-01-07")]
    assert returns.iloc[0] == pytest.approx(100.0 * math.log(1.21))


@pytest.mark.asyncio
async def test_fit_rejects_short_series(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fewer returns than the minimum fail before R is called."""
    calls = _serve_r(monkeypatch, {})

    with pytest.raises(InsufficientDataError, match="at least"):
        await fit_garch(_closes(garch.MIN_OBSERVATIONS), periods_per_year=252)
    assert calls == []


@pytest.mark.asyncio
async def test_fit_rejects_constant_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    """A series that never moves has no volatility to model."""
    _serve_r(monkeypatch, {})
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
    """R receives percent log returns and its per-bar percent output is annualized."""
    closes = _closes(600)
    calls = _serve_r(monkeypatch, _r_output(599))

    fit = await fit_garch(closes, periods_per_year=252, horizon=3, distribution="norm")

    script, function, returns, horizon, distribution = calls[0]
    assert (script, function, horizon, distribution) == (
        garch.R_SCRIPT,
        garch.R_FUNCTION,
        3,
        "norm",
    )
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
    _serve_r(monkeypatch, _r_output(count - 1))

    fit = await fit_garch(_closes(count), periods_per_year=252)

    assert fit.warnings == []


@pytest.mark.asyncio
async def test_fit_warns_on_boundary_estimates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Near-integrated persistence and no ARCH effect are each flagged."""
    count = garch.RECOMMENDED_OBSERVATIONS + 1
    output = _r_output(count - 1)
    output["coef_values"] = [0.05, 0.001, 0.0, 0.999]
    output["persistence"] = [0.999]
    _serve_r(monkeypatch, output)

    fit = await fit_garch(_closes(count), periods_per_year=252)

    assert len(fit.warnings) == 2
    assert "Persistence is 0.999" in fit.warnings[0]
    assert "no volatility clustering" in fit.warnings[1]


@pytest.mark.asyncio
async def test_fit_without_mean_reversion(monkeypatch: pytest.MonkeyPatch) -> None:
    """Persistence at or above 1 has no half-life or long-run level."""
    output = _r_output(599)
    output["persistence"] = [1.0]
    output["unconditional_sigma"] = [None]
    _serve_r(monkeypatch, output)

    fit = await fit_garch(_closes(600), periods_per_year=252)

    assert fit.half_life is None
    assert fit.long_run_volatility is None


@pytest.mark.asyncio
async def test_fit_reports_non_convergence(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unconverged fit raises with R's diagnostic message."""
    _serve_r(monkeypatch, {"converged": [False], "message": ["Optimizer did not converge."]})

    with pytest.raises(ModelFitError, match="did not converge"):
        await fit_garch(_closes(600), periods_per_year=252)


@pytest.mark.asyncio
async def test_fit_rejects_mismatched_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """A volatility series that does not line up with the returns is an invalid fit."""
    _serve_r(monkeypatch, _r_output(10))

    with pytest.raises(ModelFitError, match="volatilities"):
        await fit_garch(_closes(600), periods_per_year=252)


@pytest.mark.asyncio
async def test_fit_wraps_r_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Errors raised inside R become model fit errors."""

    async def failing_call_r(script: str, function: str, *args: object) -> dict[str, RValue]:
        raise RError("R function failed")

    monkeypatch.setattr(garch, "call_r", failing_call_r)

    with pytest.raises(ModelFitError, match="R function failed"):
        await fit_garch(_closes(600), periods_per_year=252)


@pytest.mark.asyncio
async def test_fit_propagates_missing_r(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing R installation is reported as such, not as a fit failure."""

    async def unavailable_call_r(script: str, function: str, *args: object) -> dict[str, RValue]:
        raise RUnavailableError("R is unavailable")

    monkeypatch.setattr(garch, "call_r", unavailable_call_r)

    with pytest.raises(RUnavailableError):
        await fit_garch(_closes(600), periods_per_year=252)


def _r_with_rugarch() -> bool:
    """Report whether the embedded R session can load ``rugarch``."""
    try:
        robjects = r_bridge._robjects()
        with robjects.default_converter.context():
            return bool(robjects.r('isTRUE(requireNamespace("rugarch", quietly = TRUE))')[0])
    except Exception:
        return False


@pytest.mark.skipif(not _r_with_rugarch(), reason="R with the rugarch package is not installed")
@pytest.mark.asyncio
async def test_fit_with_r() -> None:
    """The real R model fits a GARCH process and recovers a plausible volatility."""
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

    fit = await fit_garch(closes, periods_per_year=252, horizon=5, distribution="norm")

    params = {parameter.name: parameter.estimate for parameter in fit.parameters}
    assert set(params) == {"mu", "omega", "alpha1", "beta1"}
    assert 0.8 < fit.persistence < 1.0
    assert len(fit.conditional_volatility) == count
    assert len(fit.forecast) == 5
    # True long-run variance is 0.05 / (1 - 0.95) = 1 (%^2 per day).
    assert fit.long_run_volatility == pytest.approx(math.sqrt(252) / 100.0, rel=0.3)
