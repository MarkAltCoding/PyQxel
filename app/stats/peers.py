"""Where a stock's screening metrics fall among its peers'.

Percentiles count the peers below the stock and half of those tied with it, so they do
not depend on which way is better: a high P/E and a high margin both rank high. A P/E
is compared only among profitable companies, since a negative P/E is a loss, not a
cheap price.
"""

import statistics
from collections.abc import Sequence

from app.models.screener import PeerMetric, ScreenField, ScreenMetrics

PEER_FIELDS: tuple[ScreenField, ...] = (
    "market_cap",
    "avg_dollar_volume",
    "pe_ratio",
    "price_to_sales",
    "ev_to_ebitda",
    "fcf_yield",
    "gross_margin",
    "operating_margin",
    "net_margin",
    "return_on_equity",
    "revenue_growth",
    "earnings_growth",
    "return_1m",
    "return_6m",
    "momentum_12_1",
    "volatility",
    "max_drawdown",
    "beta_market",
)
"""Metrics compared with peers: size, valuation, profitability, growth, price momentum
and risk. The remaining factor betas are in the analysis's own factor exposures."""

MIN_PEERS_WITH_DATA: int = 5
"""Fewest peers with a metric for its median and percentile to be given."""


def _value(stock: ScreenMetrics, field: ScreenField) -> float | None:
    """``field`` of ``stock``, leaving out a P/E that is a loss."""
    value: float | None = getattr(stock, field)
    if field == "pe_ratio" and value is not None and value <= 0:
        return None
    return value


def percentile_of(value: float, others: Sequence[float]) -> float:
    """Percent of ``others`` below ``value``, counting ties as half."""
    if not others:
        raise ValueError("A percentile needs at least one other value.")
    below = sum(other < value for other in others)
    ties = sum(other == value for other in others)
    return 100 * (below + ties / 2) / len(others)


def compare_to_peers(stock: ScreenMetrics, peers: Sequence[ScreenMetrics]) -> list[PeerMetric]:
    """Rank each of :data:`PEER_FIELDS` of ``stock`` among ``peers``, which exclude it."""
    metrics: list[PeerMetric] = []
    for field in PEER_FIELDS:
        own: float | None = getattr(stock, field)
        values = [value for peer in peers if (value := _value(peer, field)) is not None]
        enough = len(values) >= MIN_PEERS_WITH_DATA
        comparable = _value(stock, field)
        metrics.append(
            PeerMetric(
                field=field,
                value=own,
                peer_median=statistics.median(values) if enough else None,
                percentile=(
                    percentile_of(comparable, values) if enough and comparable is not None else None
                ),
                peers_with_data=len(values),
            )
        )
    return metrics
