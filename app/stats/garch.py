"""GARCH(1,1) volatility model, estimated in R by ``r_scripts/garch.R``.

Prices are cleaned and turned into log returns in Python, so the R script only sees
a finite, gap-free numeric vector. Returns are passed to R in percent, which keeps
the optimizer well scaled, and every volatility is converted back to an annualized
decimal (0.25 = 25%) before it leaves this module.
"""

import math

import pandas as pd

from app.models.volatility import (
    GarchDistribution,
    GarchFit,
    GarchParameter,
    VolatilityForecastStep,
    VolatilityPoint,
)
from app.stats.r_bridge import RError, RUnavailableError, RValue, call_r
from app.stats.volatility import (
    annualization_scale,
    log_returns,
    require_horizon,
    require_returns,
)

R_SCRIPT: str = "garch.R"
R_FUNCTION: str = "pyqxel_fit_garch"

MIN_OBSERVATIONS: int = 480
"""Fewest returns worth fitting at all; shorter samples give unusable estimates."""

RECOMMENDED_OBSERVATIONS: int = 1000
"""Returns needed for reasonably precise estimates; fits below this carry a warning."""

NEAR_INTEGRATED_PERSISTENCE: float = 0.995
"""Persistence above which the long-run level and half-life are not trustworthy."""

MIN_ARCH_EFFECT: float = 0.01
"""``alpha1`` below which the fit found no volatility clustering."""

SHORT_SAMPLE_ADVICE: str = (
    "Request a longer period or a shorter interval, or use the EWMA model, which "
    "needs far fewer returns."
)


class ModelFitError(RuntimeError):
    """Raised when R cannot fit the model to otherwise valid returns."""


def _validate(returns: pd.Series) -> None:
    """Reject return series that are too short or have no variation."""
    require_returns(returns, MIN_OBSERVATIONS, "GARCH", SHORT_SAMPLE_ADVICE)


def _floats(output: dict[str, RValue], name: str) -> list[float | None]:
    """Read a numeric element of the R result."""
    return [None if value is None else float(value) for value in output[name]]


def _scalar(output: dict[str, RValue], name: str) -> float | None:
    """Read a length-one numeric element of the R result."""
    return _floats(output, name)[0]


def _required(value: float | None, name: str) -> float:
    """Return ``value``, treating an ``NA`` from R as a failed fit."""
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
    output: dict[str, RValue],
    returns: pd.Series,
    periods_per_year: int,
    distribution: GarchDistribution,
) -> GarchFit:
    """Convert the R result, in per-bar percent, to annualized decimal volatilities."""
    scale = annualization_scale(periods_per_year)

    def annualize(sigma: float | None, name: str) -> float:
        return _required(sigma, name) * scale

    sigma = _floats(output, "sigma")
    if len(sigma) != len(returns):
        raise ModelFitError(f"R returned {len(sigma)} volatilities for {len(returns)} returns.")
    persistence = _required(_scalar(output, "persistence"), "persistence")
    long_run = _scalar(output, "unconditional_sigma")
    parameters = [
        GarchParameter(name=str(name), estimate=_required(estimate, str(name)), std_error=error)
        for name, estimate, error in zip(
            output["coef_names"],
            _floats(output, "coef_values"),
            _floats(output, "coef_std_errors"),
        )
    ]
    alpha1 = next((param.estimate for param in parameters if param.name == "alpha1"), None)

    return GarchFit(
        distribution=distribution,
        observations=len(returns),
        parameters=parameters,
        persistence=persistence,
        half_life=math.log(0.5) / math.log(persistence) if 0 < persistence < 1 else None,
        current_volatility=annualize(sigma[-1], "sigma"),
        long_run_volatility=None if long_run is None else long_run * scale,
        realized_volatility=float(returns.std(ddof=1)) * scale,
        conditional_volatility=[
            VolatilityPoint(
                timestamp=pd.Timestamp(timestamp).to_pydatetime(),
                volatility=annualize(value, "sigma"),
            )
            for timestamp, value in zip(returns.index, sigma)
        ],
        forecast=[
            VolatilityForecastStep(step=step, volatility=annualize(value, "forecast_sigma"))
            for step, value in enumerate(_floats(output, "forecast_sigma"), start=1)
        ],
        log_likelihood=_required(_scalar(output, "log_likelihood"), "log_likelihood"),
        aic=_required(_scalar(output, "aic"), "aic"),
        bic=_required(_scalar(output, "bic"), "bic"),
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
        RUnavailableError: If R, rpy2 or the ``rugarch`` package cannot be loaded.
        ModelFitError: If the optimizer fails to converge or R returns invalid output.
    """
    annualization_scale(periods_per_year)  # Validates before any R work.
    require_horizon(horizon)

    returns = log_returns(closes)
    _validate(returns)

    try:
        output = await call_r(
            R_SCRIPT, R_FUNCTION, returns.to_numpy(dtype=float), horizon, distribution
        )
    except RUnavailableError:
        raise
    except RError as exc:
        raise ModelFitError(str(exc)) from exc

    if output["converged"] != [True]:
        message = output["message"][0] or "unknown error"
        raise ModelFitError(f"GARCH fit failed: {message}")
    return _build_fit(output, returns, periods_per_year, distribution)
