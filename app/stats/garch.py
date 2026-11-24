"""GARCH(1,1) volatility model, estimated with the ``arch`` package.

Prices are cleaned and turned into log returns first, so the model only sees a finite,
gap-free series. Returns are fitted in percent, which keeps the optimizer well scaled,
and every volatility is converted back to an annualized decimal (0.25 = 25%) before it
leaves this module. The model is a constant mean with GARCH(1,1) variance and normal or
Student t innovations, fitted by maximum likelihood with robust (QML) standard errors;
the fit runs in a worker thread, since it takes a moment of CPU.
"""

import asyncio
import math
import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.models.volatility import (
    GarchContext,
    GarchDistribution,
    GarchFit,
    GarchHorizon,
    GarchParameter,
    VolatilityForecastStep,
    VolatilityPoint,
)
from app.stats.volatility import (
    annualization_scale,
    log_returns,
    require_horizon,
    require_returns,
)

MIN_OBSERVATIONS: int = 480
"""Fewest returns worth fitting at all; shorter samples give unusable estimates."""

RECOMMENDED_OBSERVATIONS: int = 1000
"""Returns needed for reasonably precise estimates; fits below this carry a warning."""

NEAR_INTEGRATED_PERSISTENCE: float = 0.995
"""Persistence above which the long-run level and half-life are not trustworthy."""

MIN_ARCH_EFFECT: float = 0.01
"""``alpha1`` below which the fit found no volatility clustering."""

CONTEXT_HORIZONS: tuple[int, ...] = (1, 5, 21, 63)
"""Trading days ahead summarized for an analysis: a day, a week, a month, and a quarter."""

SHORT_SAMPLE_ADVICE: str = (
    "Request a longer period or a shorter interval, or use the EWMA model, which "
    "needs far fewer returns."
)


class ModelFitError(RuntimeError):
    """Raised when the model cannot be fit to otherwise valid returns."""


def _validate(returns: pd.Series) -> None:
    """Reject return series that are too short or have no variation."""
    require_returns(returns, MIN_OBSERVATIONS, "GARCH", SHORT_SAMPLE_ADVICE)


PARAMETER_NAMES: dict[str, str] = {
    "mu": "mu",
    "omega": "omega",
    "alpha[1]": "alpha1",
    "beta[1]": "beta1",
    "nu": "shape",
}
"""``arch``'s parameter names, and the names the API reports them by."""


@dataclass(frozen=True)
class RawFit:
    """A fit in per-bar percent, before annualizing."""

    names: list[str]
    estimates: list[float]
    std_errors: list[float | None]
    sigma: list[float]
    forecast_sigma: list[float]
    persistence: float
    unconditional_sigma: float | None
    log_likelihood: float
    aic: float
    bic: float


def _fit_arch(returns_pct: np.ndarray, horizon: int, distribution: GarchDistribution) -> RawFit:
    """Fit the model to ``returns_pct`` and forecast ``horizon`` bars (blocking).

    Raises:
        ModelFitError: If the optimizer fails or the estimates are not finite.
    """
    from arch import arch_model  # Imported when first used, to keep start-up quick.

    model = arch_model(
        returns_pct,
        mean="constant",
        vol="garch",
        p=1,
        q=1,
        dist="t" if distribution == "std" else "normal",
        rescale=False,
    )
    try:
        with warnings.catch_warnings():
            # Convergence is checked below; the optimizer's own warnings add nothing.
            warnings.simplefilter("ignore")
            result = model.fit(disp="off", cov_type="robust", show_warning=False)
    except (ValueError, np.linalg.LinAlgError) as exc:
        raise ModelFitError(f"GARCH fit failed: {exc}") from exc
    if result.convergence_flag != 0:
        raise ModelFitError("GARCH fit failed: the optimizer did not converge.")
    params = result.params
    errors = result.std_err
    names = [PARAMETER_NAMES.get(str(name), str(name)) for name in params.index]
    estimates = [float(value) for value in params.to_numpy()]
    if not all(math.isfinite(value) for value in estimates):
        raise ModelFitError("GARCH fit produced estimates that are not finite.")
    std_errors = [
        float(value) if math.isfinite(float(value)) else None for value in errors.to_numpy()
    ]
    omega, alpha, beta = (float(params[name]) for name in ("omega", "alpha[1]", "beta[1]"))
    persistence = alpha + beta
    forecast = result.forecast(horizon=horizon, reindex=False).variance.to_numpy()[-1]
    observations = len(returns_pct)
    return RawFit(
        names=names,
        estimates=estimates,
        std_errors=std_errors,
        sigma=[float(value) for value in np.asarray(result.conditional_volatility)],
        forecast_sigma=[math.sqrt(float(value)) for value in forecast],
        persistence=persistence,
        unconditional_sigma=(
            math.sqrt(omega / (1 - persistence)) if 0 <= persistence < 1 and omega > 0 else None
        ),
        log_likelihood=float(result.loglikelihood),
        # Per observation, as the API has always reported them.
        aic=float(result.aic) / observations,
        bic=float(result.bic) / observations,
    )


def _required(value: float | None, name: str) -> float:
    """Return ``value``, treating a missing one as a failed fit."""
    if value is None:
        raise ModelFitError(f"GARCH fit produced no value for {name}.")
    return value


def _warnings(observations: int, persistence: float, alpha1: float | None) -> list[str]:
    """Describe why a fit's estimates may be unreliable, if they are."""
    warnings: list[str] = []
    if observations < RECOMMENDED_OBSERVATIONS:
        warnings.append(
            f"Fit uses {observations} returns; at least {RECOMMENDED_OBSERVATIONS} are "
            "recommended. Parameter estimates are imprecise; use a longer period."
        )
    if persistence >= NEAR_INTEGRATED_PERSISTENCE:
        warnings.append(
            f"Persistence is {persistence:.3f}, close to 1, so shocks barely decay. The "
            "long-run volatility and half-life are unreliable; a regime change in the "
            "window is a common cause."
        )
    if alpha1 is not None and alpha1 < MIN_ARCH_EFFECT:
        warnings.append(
            f"alpha1 is {alpha1:.3f}: no volatility clustering was detected, so the model "
            "adds little over realized volatility."
        )
    return warnings


def _build_fit(
    raw: RawFit,
    returns: pd.Series,
    periods_per_year: int,
    distribution: GarchDistribution,
) -> GarchFit:
    """Convert a fit in per-bar percent to annualized decimal volatilities."""
    scale = annualization_scale(periods_per_year)
    if len(raw.sigma) != len(returns):
        raise ModelFitError(
            f"The fit returned {len(raw.sigma)} volatilities for {len(returns)} returns."
        )
    parameters = [
        GarchParameter(name=name, estimate=estimate, std_error=error)
        for name, estimate, error in zip(raw.names, raw.estimates, raw.std_errors)
    ]
    alpha1 = next((param.estimate for param in parameters if param.name == "alpha1"), None)
    persistence = raw.persistence
    return GarchFit(
        distribution=distribution,
        observations=len(returns),
        parameters=parameters,
        persistence=persistence,
        half_life=math.log(0.5) / math.log(persistence) if 0 < persistence < 1 else None,
        current_volatility=raw.sigma[-1] * scale,
        long_run_volatility=(
            None if raw.unconditional_sigma is None else raw.unconditional_sigma * scale
        ),
        realized_volatility=float(returns.std(ddof=1)) * scale,
        conditional_volatility=[
            VolatilityPoint(
                timestamp=pd.Timestamp(timestamp).to_pydatetime(),
                volatility=value * scale,
            )
            for timestamp, value in zip(returns.index, raw.sigma)
        ],
        forecast=[
            VolatilityForecastStep(step=step, volatility=value * scale)
            for step, value in enumerate(raw.forecast_sigma, start=1)
        ],
        log_likelihood=raw.log_likelihood,
        aic=raw.aic,
        bic=raw.bic,
        warnings=_warnings(len(returns), persistence, alpha1),
    )


async def fit_garch(
    closes: pd.Series,
    periods_per_year: int,
    horizon: int = 10,
    distribution: GarchDistribution = "std",
) -> GarchFit:
    """Fit a constant-mean GARCH(1,1) to the log returns of ``closes`` and forecast it.

    Args:
        closes: Close prices indexed by bar timestamp.
        periods_per_year: Bars per year, used to annualize (252 for daily bars).
        horizon: Bars ahead to forecast, from 1 to
            :data:`~app.stats.volatility.MAX_HORIZON`.
        distribution: Innovation distribution, ``"norm"`` or ``"std"`` (Student t).

    Returns:
        Parameter estimates, in-sample and forecast volatility, fit statistics, and
        warnings when the sample is short or the estimates sit at a boundary.

    Raises:
        ValueError: If ``periods_per_year`` or ``horizon`` is out of range.
        InsufficientDataError: If there are too few returns or prices never change.
        ModelFitError: If the optimizer fails to converge or gives invalid estimates.
    """
    annualization_scale(periods_per_year)  # Validates before any fitting.
    require_horizon(horizon)

    returns = log_returns(closes)
    _validate(returns)

    raw = await asyncio.to_thread(_fit_arch, returns.to_numpy(dtype=float), horizon, distribution)
    return _build_fit(raw, returns, periods_per_year, distribution)


def summarize_garch(fit: GarchFit) -> GarchContext:
    """Summarize a daily fit for an analysis, without its in-sample series.

    Each horizon's volatility is the square root of the mean forecast variance over that
    many days, which is the volatility of the return over the whole horizon. Horizons
    beyond the fit's forecast are left out.
    """
    if not fit.conditional_volatility:
        raise ValueError("A GARCH fit has at least one conditional volatility.")
    current = fit.current_volatility
    history = [point.volatility for point in fit.conditional_volatility]
    below = sum(value < current for value in history)
    ties = sum(value == current for value in history)
    variances = [step.volatility**2 for step in fit.forecast]
    return GarchContext(
        distribution=fit.distribution,
        start=fit.conditional_volatility[0].timestamp.date(),
        end=fit.conditional_volatility[-1].timestamp.date(),
        observations=fit.observations,
        current_volatility=current,
        current_percentile=100 * (below + ties / 2) / len(history),
        long_run_volatility=fit.long_run_volatility,
        realized_volatility=fit.realized_volatility,
        persistence=fit.persistence,
        half_life_days=fit.half_life,
        parameters=fit.parameters,
        forecast=[
            GarchHorizon(days=days, volatility=math.sqrt(sum(variances[:days]) / days))
            for days in CONTEXT_HORIZONS
            if days <= len(variances)
        ],
        warnings=fit.warnings,
    )
