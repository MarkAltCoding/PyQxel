"""A fund's figures weighted from its holdings: the valuation, profitability, growth, size,
and sector mix of the companies it owns, as a stock's own figures are shown.

Ratios of price to a flow (P/E, price to sales) are combined as a harmonic mean: the
fund's share of the companies' earnings against its share of their prices, which is what
an investor owning the fund owns, and which one company with a huge P/E cannot swamp. Rates
(margins, return on equity, growth) and market caps are weighted means. Each figure is
taken over the holdings that have it, and says how much of the fund's stocks those are.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.models.stock import FundProfile, HoldingsStats

LARGE_CAP: float = 10e9
MID_CAP: float = 2e9
"""Market caps, in US dollars, from which a company counts as large or mid-sized."""

MEAN_FIELDS: tuple[str, ...] = (
    "gross_margin",
    "operating_margin",
    "net_margin",
    "return_on_equity",
    "revenue_growth",
    "earnings_growth",
)
"""Figures combined as a weighted mean."""

HARMONIC_FIELDS: tuple[str, ...] = ("pe_ratio", "price_to_sales")
"""Figures of price over a flow, combined as a weighted harmonic mean."""


@dataclass(frozen=True)
class HeldCompany:
    """One stock a fund holds, with its share of the fund and its figures, where known."""

    weight: float
    figures: dict[str, float | None] | None
    """Its figures by :data:`MEAN_FIELDS`, :data:`HARMONIC_FIELDS` and ``market_cap``;
    ``None`` when none could be found."""
    sector: str | None = None


def _mean(pairs: Sequence[tuple[float, float]]) -> float | None:
    total = sum(weight for weight, _ in pairs)
    return sum(weight * value for weight, value in pairs) / total if total > 0 else None


def _harmonic(pairs: Sequence[tuple[float, float]]) -> float | None:
    """The weighted harmonic mean of ratios; ``None`` when the companies earn nothing
    overall, as a P/E is meaningless then. A loss (a negative ratio) counts against the
    earnings."""
    total = sum(weight for weight, _ in pairs)
    flow = sum(weight / value for weight, value in pairs if value)
    return total / flow if total > 0 and flow > 0 else None


def holdings_stats(companies: Sequence[HeldCompany]) -> HoldingsStats | None:
    """The fund's figures weighted from ``companies``, the stocks it holds; ``None`` when
    none has a figure."""
    stock_weight = sum(company.weight for company in companies if company.weight > 0)
    known = [c for c in companies if c.weight > 0 and c.figures]
    if stock_weight <= 0 or not known:
        return None

    def pairs(field: str) -> list[tuple[float, float]]:
        return [
            (c.weight, float(value))
            for c in known
            if c.figures is not None and (value := c.figures.get(field)) is not None
        ]

    values: dict[str, float | None] = {field: _mean(pairs(field)) for field in MEAN_FIELDS}
    values |= {field: _harmonic(pairs(field)) for field in HARMONIC_FIELDS}
    caps = pairs("market_cap")
    cap_weight = sum(weight for weight, _ in caps)
    size = (
        {
            "large": sum(w for w, cap in caps if cap >= LARGE_CAP) / cap_weight,
            "mid": sum(w for w, cap in caps if MID_CAP <= cap < LARGE_CAP) / cap_weight,
            "small": sum(w for w, cap in caps if cap < MID_CAP) / cap_weight,
        }
        if cap_weight > 0
        else {}
    )
    sectors: dict[str, float] = {}
    classified = sum(c.weight for c in companies if c.sector and c.weight > 0)
    for company in companies:
        if company.sector and company.weight > 0:
            sectors[company.sector] = sectors.get(company.sector, 0.0) + company.weight
    return HoldingsStats(
        coverage=sum(c.weight for c in known) / stock_weight,
        average_market_cap=_mean(caps),
        size_mix=size,
        sectors=(
            {
                name: share / classified
                for name, share in sorted(sectors.items(), key=lambda item: -item[1])
            }
            if classified > 0
            else {}
        ),
        sector_coverage=classified / stock_weight,
        **values,
    )


def look_through(weight: float, fund: FundProfile) -> dict[str, float]:
    """A holding of ``weight`` in ``fund`` split by what the fund holds: its stocks over
    their sectors, in proportion, and its bonds and cash as ``Bonds`` and ``Cash``. What is
    not classified is ``Unclassified``."""
    split: dict[str, float] = {}

    def add(name: str, share: float) -> None:
        if share > 1e-9:
            split[name] = split.get(name, 0.0) + share

    mix = {part.name: part.weight for part in fund.asset_mix} or {"Stocks": 1.0}
    stocks = weight * mix.get("Stocks", 0.0)
    classified = sum(part.weight for part in fund.sectors)
    for part in fund.sectors:
        add(part.name, stocks * part.weight / max(classified, 1.0))
    add("Unclassified", stocks * (1 - min(classified, 1.0)))
    add("Bonds", weight * mix.get("Bonds", 0.0))
    add("Cash", weight * mix.get("Cash", 0.0))
    add("Unclassified", weight * max(0.0, mix.get("Other", 0.0)))
    return split
