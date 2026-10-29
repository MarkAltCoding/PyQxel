"""Risk statistics of a weighted portfolio, from its holdings' aligned daily returns.

The portfolio's returns are those of fixed weights rebalanced daily: the weights the
account holds today, applied to the whole window. They describe how today's portfolio
would have behaved, not what the account earned. A holding whose returns never change,
such as a money-market fund held as cash, has no correlation with anything, so it is
left out of the correlations.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

TRADING_DAYS: int = 252
"""Daily returns in a year, for annualizing."""


@dataclass(frozen=True)
class AssetStats:
    """One holding's statistics over the window; decimals, annualized."""

    annualized_return: float
    volatility: float
    max_drawdown: float
    risk_contribution: float
    """Share of the portfolio's variance; the shares sum to 1."""
    correlation_to_rest: float | None
    """Correlation with the rest of the portfolio; ``None`` when it is the only holding,
    or when it or the rest never moves."""


@dataclass(frozen=True)
class PortfolioStats:
    """The portfolio's statistics over the window, and each holding's."""

    start: pd.Timestamp
    end: pd.Timestamp
    observations: int
    annualized_return: float
    volatility: float
    max_drawdown: float
    current_drawdown: float
    diversification_ratio: float
    effective_holdings: float
    average_correlation: float | None
    assets: dict[str, AssetStats]
    correlations: pd.DataFrame
    """Correlations of the holdings whose returns vary."""


def annualized_return(returns: pd.Series) -> float:
    """Compound annual growth of ``returns``, decimal daily returns."""
    growth = float(np.prod(1.0 + returns.to_numpy(dtype=float)))
    return float(growth ** (TRADING_DAYS / len(returns)) - 1.0)


def drawdowns(returns: pd.Series) -> pd.Series:
    """Decline of the compounded value from its running peak, at each date (zero or less)."""
    wealth = (1.0 + returns).cumprod()
    return wealth / wealth.cummax().clip(lower=1.0) - 1.0


def portfolio_stats(returns: pd.DataFrame, weights: pd.Series) -> PortfolioStats:
    """Statistics of ``weights`` held over ``returns``.

    Args:
        returns: Aligned decimal daily returns, one column per holding, oldest first.
        weights: Each holding's weight, indexed like ``returns``' columns; summing to 1.

    Returns:
        The portfolio's and each holding's statistics, and the correlation matrix.
    """
    weights = weights.reindex(returns.columns).astype(float)
    combined = returns @ weights
    covariance = returns.cov() * TRADING_DAYS
    w = weights.to_numpy(dtype=float)
    portfolio_variance = float(w @ covariance.to_numpy(dtype=float) @ w)
    volatility = float(np.sqrt(max(portfolio_variance, 0.0)))
    asset_volatility = np.sqrt(np.diag(covariance))
    marginal = covariance @ weights
    contribution = (
        weights * marginal / portfolio_variance if portfolio_variance > 0 else weights * 0.0
    )
    moving = [symbol for symbol in returns.columns if float(returns[symbol].std()) > 0]
    correlations = returns[moving].corr()
    path = drawdowns(combined)

    assets: dict[str, AssetStats] = {}
    for position, symbol in enumerate(returns.columns):
        rest_weights = weights.drop(symbol)
        to_rest: float | None = None
        if symbol in moving and len(rest_weights) and float(rest_weights.sum()) > 0:
            rest = returns[rest_weights.index] @ (rest_weights / rest_weights.sum())
            if float(rest.std()) > 0:
                to_rest = float(returns[symbol].corr(rest))
        assets[symbol] = AssetStats(
            annualized_return=annualized_return(returns[symbol]),
            volatility=float(asset_volatility[position]),
            max_drawdown=float(drawdowns(returns[symbol]).min()),
            risk_contribution=float(contribution[symbol]),
            correlation_to_rest=to_rest,
        )

    pairs = correlations.to_numpy()[np.triu_indices(len(moving), k=1)]
    return PortfolioStats(
        start=returns.index[0],
        end=returns.index[-1],
        observations=len(returns),
        annualized_return=annualized_return(combined),
        volatility=volatility,
        max_drawdown=float(path.min()),
        current_drawdown=float(path.iloc[-1]),
        diversification_ratio=(
            float(weights @ asset_volatility) / volatility if volatility > 0 else 1.0
        ),
        effective_holdings=float(1.0 / (weights**2).sum()),
        average_correlation=float(pairs.mean()) if len(pairs) else None,
        assets=assets,
        correlations=correlations,
    )


Pair = tuple[str, str, float]
"""Two holdings and the correlation of their daily returns."""


def ranked_pairs(correlations: pd.DataFrame, count: int) -> tuple[list[Pair], list[Pair]]:
    """The ``count`` most and least correlated pairs, as ``(first, second, correlation)``."""
    symbols = [str(symbol) for symbol in correlations.columns]
    matrix = correlations.to_numpy(dtype=float)
    pairs: list[Pair] = [
        (symbols[i], symbols[j], float(matrix[i, j]))
        for i in range(len(symbols))
        for j in range(i + 1, len(symbols))
    ]
    pairs.sort(key=lambda pair: pair[2], reverse=True)
    most = pairs[:count]
    least = [pair for pair in reversed(pairs) if pair not in most][:count]
    return most, least
