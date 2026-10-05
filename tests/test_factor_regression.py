"""Tests for the multi-factor regression of daily excess returns on Fama-French factors.

Returns are simulated from known betas and alpha, so the fit can be checked against
the truth without any network access.
"""

import math

import numpy as np
import pandas as pd
import pytest

from app.models.factors import MODEL_FACTORS
from app.stats.factors import (
    RECOMMENDED_OBSERVATIONS,
    TRADING_DAYS,
    daily_returns,
    fit_factor_model,
    hac_lags,
)
from app.stats.volatility import InsufficientDataError

BETAS = {"Mkt-RF": 1.2, "SMB": 0.5, "HML": -0.3, "RMW": 0.2, "CMA": 0.1, "Mom": -0.4}
DAILY_ALPHA = 0.0002
NOISE = 0.01


def _factors(days: int, seed: int = 0) -> pd.DataFrame:
    """Daily factor returns on business days, with every factor and a constant RF."""
    rng = np.random.default_rng(seed)
    index = pd.bdate_range("2020-01-02", periods=days)
    frame = pd.DataFrame(
        rng.normal(0.0, 0.01, size=(days, len(BETAS))), index=index, columns=list(BETAS)
    )
    frame["RF"] = 0.0001
    return frame


def _closes(
    factors: pd.DataFrame,
    model: str = "ff5",
    alpha: float = DAILY_ALPHA,
    noise: float = NOISE,
    tz: str | None = "America/New_York",
    seed: int = 1,
) -> pd.Series:
    """Closes whose returns are RF + alpha + the model's factors times BETAS + noise.

    Timestamps are midnight exchange time, as yfinance stamps daily bars.
    """
    rng = np.random.default_rng(seed)
    names = MODEL_FACTORS[model]  # type: ignore[index]
    returns = (
        factors["RF"]
        + alpha
        + factors[names] @ pd.Series({name: BETAS[name] for name in names})
        + rng.normal(0.0, noise, size=len(factors))
    )
    prices = 100.0 * (1.0 + returns).cumprod()
    start = factors.index[0] - pd.tseries.offsets.BDay(1)
    prices = pd.concat([pd.Series([100.0], index=[start]), prices])
    index = pd.DatetimeIndex(prices.index)
    prices.index = index if tz is None else index.tz_localize(tz)
    closes: pd.Series = prices
    return closes


@pytest.mark.parametrize("model", ["ff3", "carhart4", "ff5"])
def test_recovers_known_betas_and_alpha(model: str) -> None:
    """Betas, alpha and residual volatility come back close to the simulated values."""
    factors = _factors(2_000)

    fit = fit_factor_model(_closes(factors, model), factors, model)  # type: ignore[arg-type]

    expected = MODEL_FACTORS[model]  # type: ignore[index]
    assert [exposure.factor for exposure in fit.exposures] == expected
    for exposure in fit.exposures:
        assert exposure.estimate == pytest.approx(BETAS[exposure.factor], abs=0.05)
        assert exposure.p_value is not None and exposure.p_value < 0.01
    assert fit.alpha.estimate == pytest.approx(DAILY_ALPHA * TRADING_DAYS, abs=0.06)
    assert fit.residual_volatility == pytest.approx(NOISE * math.sqrt(TRADING_DAYS), rel=0.05)
    assert fit.observations == 2_000
    assert fit.hac_lags == hac_lags(2_000)
    assert fit.warnings == []


def test_variance_shares_sum_to_r_squared() -> None:
    """Per-factor shares add up to R-squared, and systematic plus idiosyncratic to one."""
    factors = _factors(1_000)

    fit = fit_factor_model(_closes(factors), factors, "ff5")

    assert sum(exposure.variance_share for exposure in fit.exposures) == pytest.approx(
        fit.r_squared
    )
    assert fit.systematic_share + fit.idiosyncratic_share == pytest.approx(1.0)
    assert 0 < fit.adjusted_r_squared < fit.r_squared < 1
    total = fit.total_volatility**2
    assert fit.residual_volatility**2 == pytest.approx(total * (1 - fit.r_squared), rel=0.01)


def test_alpha_inference_is_annualized_consistently() -> None:
    """Annualizing scales alpha and its error alike, leaving the t-statistic unchanged."""
    factors = _factors(1_500)

    fit = fit_factor_model(_closes(factors), factors, "ff3")

    assert fit.alpha.t_stat == pytest.approx(fit.alpha.estimate / fit.alpha.std_error)


def test_alpha_standard_errors_are_calibrated() -> None:
    """With no true alpha, standard errors match the estimates' spread and tests reject ~5%.

    One sample can land in the 5% tail by chance, so this checks many.
    """
    estimates, errors, rejections = [], [], 0
    for seed in range(200):
        factors = _factors(1_000, seed=seed + 1_000)
        alpha = fit_factor_model(_closes(factors, alpha=0.0, seed=seed), factors, "ff3").alpha
        estimates.append(alpha.estimate)
        errors.append(alpha.std_error)
        rejections += alpha.p_value is not None and alpha.p_value < 0.05

    assert float(np.mean(errors)) == pytest.approx(float(np.std(estimates)), rel=0.15)
    assert 2 <= rejections <= 20


def test_days_are_matched_by_exchange_date() -> None:
    """Midnight New York timestamps line up with the factor file's plain dates."""
    factors = _factors(300)
    aware = fit_factor_model(_closes(factors, tz="America/New_York"), factors, "ff3")
    naive = fit_factor_model(_closes(factors, tz=None), factors, "ff3")

    assert aware.observations == naive.observations == 300
    assert aware.exposures == naive.exposures
    assert aware.end == factors.index[-1].date()


def test_days_missing_from_either_series_are_left_out() -> None:
    """Holidays in one calendar only, and returns after the factor data ends, are dropped."""
    factors = _factors(400)
    closes = _closes(factors)
    published = factors.iloc[:350].drop(factors.index[[10, 20, 30]])

    fit = fit_factor_model(closes, published, "ff3")

    assert fit.observations == 347
    assert fit.end == factors.index[349].date()


def test_only_the_models_factors_are_used() -> None:
    """Extra columns in the factor table are ignored."""
    factors = _factors(600)
    closes = _closes(factors, "ff3")

    fit = fit_factor_model(closes, factors, "ff3")

    assert {exposure.factor for exposure in fit.exposures} == {"Mkt-RF", "SMB", "HML"}


def test_short_and_unexplained_samples_carry_warnings() -> None:
    """Under two years of returns, or factors that explain little, are flagged."""
    factors = _factors(RECOMMENDED_OBSERVATIONS - 1)

    fit = fit_factor_model(_closes(factors, noise=0.2), factors, "ff3")

    assert len(fit.warnings) == 2
    assert "recommended" in fit.warnings[0]
    assert "idiosyncratic" in fit.warnings[1]


def test_too_little_overlap_is_rejected() -> None:
    """Fewer than the minimum days in common with the factor data is an error."""
    factors = _factors(300)

    with pytest.raises(InsufficientDataError, match="at least 120 days"):
        fit_factor_model(_closes(factors), factors.iloc[:100], "ff3")


def test_flat_prices_are_rejected() -> None:
    """A price that never moves has excess returns equal to minus RF; nothing to fit."""
    factors = _factors(300).assign(RF=0.0)
    closes = pd.Series(100.0, index=factors.index.insert(0, pd.Timestamp("2020-01-01")))

    with pytest.raises(InsufficientDataError, match="never change"):
        fit_factor_model(closes, factors, "ff3")


def test_daily_returns_drop_bad_prices() -> None:
    """Missing and non-positive closes are skipped, so returns span valid closes."""
    index = pd.date_range("2026-01-05", periods=4, tz="America/New_York")
    closes = pd.Series([100.0, math.nan, 0.0, 110.0], index=index)

    returns = daily_returns(closes)

    assert returns.tolist() == pytest.approx([0.10])
    assert isinstance(returns.index, pd.DatetimeIndex) and returns.index.tz is None
    assert returns.index[0] == pd.Timestamp("2026-01-08")


@pytest.mark.parametrize(("observations", "lags"), [(100, 4), (250, 4), (1_250, 7), (2_500, 8)])
def test_newey_west_lags_follow_the_usual_rule(observations: int, lags: int) -> None:
    """Lags grow slowly with the sample: floor(4 (n / 100) ^ (2 / 9))."""
    assert hac_lags(observations) == lags
