"""Gaussian and Student t copulas: how assets move together, apart from how each moves alone.

Each asset's returns are replaced by their ranks, scaled into (0, 1). These pseudo-
observations keep only the dependence between assets, whatever each asset's own
distribution, so the copula is estimated without assuming any marginal distribution
(the semi-parametric "canonical maximum likelihood" approach).

* The Gaussian copula's correlation is the correlation of the normal scores of the ranks.
  It has no tail dependence: extreme moves in two assets are asymptotically independent.
* The Student t copula's correlation comes from Kendall's tau, ``sin(pi tau / 2)``,
  which holds for every elliptical copula and is robust to outliers. Its degrees of
  freedom are then fitted by maximum likelihood. Fewer degrees of freedom mean more
  joint extremes: assets that crash together.

The two are compared by AIC, which charges the t copula one extra parameter.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd
from scipy import optimize, special, stats

MIN_DEGREES_OF_FREEDOM: float = 2.05
"""Lower bound of the t copula's degrees of freedom; below 2 variances do not exist."""

MAX_DEGREES_OF_FREEDOM: float = 100.0
"""Upper bound; by then the t copula is indistinguishable from the Gaussian."""

TAIL_QUANTILE: float = 0.05
"""Quantile defining a joint extreme for the empirical tail dependence."""

MIN_EIGENVALUE: float = 1e-8
"""Floor for a correlation matrix's eigenvalues when repairing it to be positive definite."""

ASYNCHRONOUS_HOURS: float = 3.0
"""Gap between exchange time zones beyond which same-date returns are not simultaneous."""


@dataclass(frozen=True)
class CopulaFit:
    """Gaussian and Student t copulas fitted to the same returns.

    Matrices are ``d x d`` numpy arrays in the order of :attr:`symbols`.
    """

    symbols: list[str]
    observations: int
    kendall_tau: np.ndarray
    gaussian_correlation: np.ndarray
    gaussian_log_likelihood: float
    t_correlation: np.ndarray
    degrees_of_freedom: float
    t_log_likelihood: float
    empirical_lower_tail: np.ndarray
    """Share of days one asset is in its lowest :data:`TAIL_QUANTILE` when another is."""
    empirical_upper_tail: np.ndarray
    """The same for the highest :data:`TAIL_QUANTILE`."""

    @property
    def dimension(self) -> int:
        """Number of assets."""
        return len(self.symbols)

    @property
    def gaussian_aic(self) -> float:
        """Akaike information criterion of the Gaussian copula: one parameter per pair."""
        return 2 * _pairs(self.dimension) - 2 * self.gaussian_log_likelihood

    @property
    def t_aic(self) -> float:
        """Akaike information criterion of the t copula: one per pair, plus degrees of freedom."""
        return 2 * (_pairs(self.dimension) + 1) - 2 * self.t_log_likelihood

    @property
    def degrees_of_freedom_at_bound(self) -> bool:
        """Whether the fit pushed the degrees of freedom to the upper bound (no fat tails)."""
        return self.degrees_of_freedom >= MAX_DEGREES_OF_FREEDOM * 0.99

    @property
    def t_tail_dependence(self) -> np.ndarray:
        """Each pair's tail dependence under the t copula, the same in both tails."""
        return t_tail_dependence(self.t_correlation, self.degrees_of_freedom)


def _pairs(dimension: int) -> int:
    """Number of distinct asset pairs."""
    return dimension * (dimension - 1) // 2


def pseudo_observations(returns: pd.DataFrame) -> np.ndarray:
    """Return each asset's returns as ranks scaled into (0, 1), ties sharing an average rank."""
    ranks = returns.rank(method="average").to_numpy(dtype=float)
    return ranks / (len(returns) + 1)


def kendall_tau_matrix(returns: pd.DataFrame) -> np.ndarray:
    """Return the matrix of pairwise Kendall rank correlations (tau-b)."""
    values = returns.to_numpy(dtype=float)
    dimension = values.shape[1]
    tau = np.eye(dimension)
    for i in range(dimension):
        for j in range(i + 1, dimension):
            tau[i, j] = tau[j, i] = stats.kendalltau(values[:, i], values[:, j]).statistic
    return tau


def nearest_correlation(matrix: np.ndarray) -> np.ndarray:
    """Return ``matrix`` repaired to a positive definite correlation matrix.

    Pairwise estimates need not be jointly consistent. Negative or zero eigenvalues are
    raised to a small floor and the diagonal rescaled to one, which leaves a valid
    matrix unchanged up to rounding.
    """
    symmetric = (matrix + matrix.T) / 2
    eigenvalues, eigenvectors = np.linalg.eigh(symmetric)
    if eigenvalues.min() > MIN_EIGENVALUE:
        repaired = symmetric
    else:
        clipped = np.maximum(eigenvalues, MIN_EIGENVALUE)
        repaired = (eigenvectors * clipped) @ eigenvectors.T
    scale = np.sqrt(np.diag(repaired))
    correlation = repaired / np.outer(scale, scale)
    np.fill_diagonal(correlation, 1.0)
    return correlation


def _quadratic_forms(points: np.ndarray, inverse: np.ndarray) -> np.ndarray:
    """Return ``x' inverse x`` for every row ``x`` of ``points``."""
    return np.einsum("ij,jk,ik->i", points, inverse, points)


def gaussian_log_likelihood(uniforms: np.ndarray, correlation: np.ndarray) -> float:
    """Log-likelihood of the Gaussian copula with ``correlation`` at ``uniforms``."""
    scores = stats.norm.ppf(uniforms)
    _, log_det = np.linalg.slogdet(correlation)
    inverse = np.linalg.inv(correlation)
    forms = _quadratic_forms(scores, inverse - np.eye(len(correlation)))
    return float(np.sum(-0.5 * log_det - 0.5 * forms))


def t_log_likelihood(uniforms: np.ndarray, correlation: np.ndarray, df: float) -> float:
    """Log-likelihood of the Student t copula with ``correlation`` and ``df`` at ``uniforms``."""
    observations, dimension = uniforms.shape
    points = stats.t.ppf(uniforms, df)
    _, log_det = np.linalg.slogdet(correlation)
    forms = _quadratic_forms(points, np.linalg.inv(correlation))
    constant = (
        special.gammaln((df + dimension) / 2)
        + (dimension - 1) * special.gammaln(df / 2)
        - dimension * special.gammaln((df + 1) / 2)
        - 0.5 * log_det
    )
    return float(
        observations * constant
        - (df + dimension) / 2 * np.sum(np.log1p(forms / df))
        + (df + 1) / 2 * np.sum(np.log1p(points**2 / df))
    )


def fit_degrees_of_freedom(uniforms: np.ndarray, correlation: np.ndarray) -> float:
    """Return the degrees of freedom maximizing the t copula likelihood for ``correlation``."""
    result = optimize.minimize_scalar(
        lambda log_df: -t_log_likelihood(uniforms, correlation, math.exp(log_df)),
        bounds=(math.log(MIN_DEGREES_OF_FREEDOM), math.log(MAX_DEGREES_OF_FREEDOM)),
        method="bounded",
        options={"xatol": 1e-4},
    )
    return float(math.exp(result.x))


def t_tail_dependence(correlation: np.ndarray, df: float) -> np.ndarray:
    """Tail dependence of the t copula: the chance one asset is extreme given another is."""
    rho = np.clip(correlation, -1.0, 1.0)
    with np.errstate(divide="ignore"):
        distance = np.sqrt((df + 1) * (1 - rho) / (1 + rho))
    tail = 2 * stats.t.cdf(-distance, df + 1)
    np.fill_diagonal(tail, 1.0)
    return tail


def empirical_tail_dependence(
    uniforms: np.ndarray, quantile: float = TAIL_QUANTILE
) -> tuple[np.ndarray, np.ndarray]:
    """Return how often assets are jointly in their lower, and upper, ``quantile`` tails.

    Each entry is the share of one asset's extreme days on which the other is also
    extreme, so independent assets score about ``quantile`` and comonotonic ones one.
    """
    lower = (uniforms <= quantile).astype(float)
    upper = (uniforms > 1 - quantile).astype(float)
    return (
        (lower.T @ lower) / np.maximum(lower.sum(axis=0), 1.0),
        (upper.T @ upper) / np.maximum(upper.sum(axis=0), 1.0),
    )


def fit_copulas(returns: pd.DataFrame) -> CopulaFit:
    """Fit Gaussian and Student t copulas to the returns of several assets.

    Args:
        returns: Aligned returns, one column per asset and at least two, without
            missing values, as built by :func:`app.stats.panel.return_panel`.

    Returns:
        Both copulas' parameters and log-likelihoods, Kendall's tau, and empirical tail
        co-movement, with matrices in column order.
    """
    uniforms = pseudo_observations(returns)
    tau = kendall_tau_matrix(returns)

    gaussian = nearest_correlation(np.corrcoef(stats.norm.ppf(uniforms), rowvar=False))
    t_correlation = nearest_correlation(np.sin(np.pi * tau / 2))
    df = fit_degrees_of_freedom(uniforms, t_correlation)
    lower, upper = empirical_tail_dependence(uniforms)

    return CopulaFit(
        symbols=[str(column) for column in returns.columns],
        observations=len(returns),
        kendall_tau=tau,
        gaussian_correlation=gaussian,
        gaussian_log_likelihood=gaussian_log_likelihood(uniforms, gaussian),
        t_correlation=t_correlation,
        degrees_of_freedom=df,
        t_log_likelihood=t_log_likelihood(uniforms, t_correlation, df),
        empirical_lower_tail=lower,
        empirical_upper_tail=upper,
    )


def _utc_offset_hours(name: str, moment: datetime) -> float | None:
    """Return the UTC offset of time zone ``name`` at ``moment``, or ``None`` if unknown."""
    try:
        offset = moment.astimezone(ZoneInfo(name)).utcoffset()
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return None if offset is None else offset.total_seconds() / 3600


def asynchronous_trading_warning(timezones: dict[str, str | None]) -> str | None:
    """Warn when the assets trade in time zones far enough apart that closes are hours apart.

    Same-date returns then cover different hours: news after Tokyo's close reaches New
    York's return that day but Tokyo's the next. Dependence is understated as a result.
    """
    now = datetime.now(timezone.utc)
    offsets = {
        symbol: offset
        for symbol, name in timezones.items()
        if name is not None and (offset := _utc_offset_hours(name, now)) is not None
    }
    if len(offsets) < 2 or max(offsets.values()) - min(offsets.values()) < ASYNCHRONOUS_HOURS:
        return None
    zones: dict[str, list[str]] = {}
    for symbol in offsets:
        zones.setdefault(str(timezones[symbol]), []).append(symbol)
    listing = "; ".join(f"{zone}: {', '.join(symbols)}" for zone, symbols in zones.items())
    return (
        f"The assets trade in time zones hours apart ({listing}), so their daily returns "
        "do not cover the same hours and the dependence between markets is understated."
    )
