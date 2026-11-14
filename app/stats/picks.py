"""Choosing the candidates Claude picks from: the screener's leaders on many measures.

The screener holds thousands of securities, too many to show Claude, and most are not
worth showing. Each :class:`Lens` is a screen that takes the leaders on one measure, among
securities liquid and large enough to buy; the candidates are their union, so the list
holds what is strongest on value, growth, quality, momentum, and steadiness at once, and
Claude judges between them. Nothing here reads the database: the endpoint runs each lens's
screen and hands the results to :func:`merge_candidates`.
"""

from dataclasses import dataclass

from app.models.picks import MAX_CANDIDATES, PickCandidate
from app.models.screener import (
    AssetType,
    RankedStock,
    ScreenField,
    ScreenFilter,
    ScreenRequest,
    ScreenSort,
)

MIN_MARKET_CAP: float = 300_000_000.0
"""Smallest company offered as a candidate: below it, figures are noisy and trading thin."""

MIN_DOLLAR_VOLUME: float = 5_000_000.0
"""Least traded a day, in dollars, for a candidate to be easy to buy and sell."""

MIN_PRICE: float = 2.0
"""Lowest share price offered, leaving out penny stocks."""


@dataclass(frozen=True)
class Lens:
    """One measure the screener's leaders are taken on."""

    name: str
    """What a candidate found through it is said to lead on."""
    field: ScreenField
    descending: bool
    count: int
    asset_type: AssetType = "stock"
    filters: tuple[ScreenFilter, ...] = ()
    """Bounds beyond the baseline, which keep out figures too extreme to be meaningful."""

    def request(self) -> ScreenRequest:
        """The screen that finds this lens's leaders."""
        baseline = [
            ScreenFilter(field="avg_dollar_volume", min=MIN_DOLLAR_VOLUME),
            ScreenFilter(field="price", min=MIN_PRICE),
        ]
        if self.asset_type == "stock":
            baseline.append(ScreenFilter(field="market_cap", min=MIN_MARKET_CAP))
        return ScreenRequest(
            universe="all",
            asset_types=[self.asset_type],
            filters=[*baseline, *self.filters],
            sort=ScreenSort(field=self.field, descending=self.descending),
            limit=self.count,
        )


def _bounded(
    name: ScreenField, low: float | None = None, high: float | None = None
) -> ScreenFilter:
    return ScreenFilter(field=name, min=low, max=high)


LENSES: tuple[Lens, ...] = (
    Lens("largest companies", "market_cap", True, 40),
    Lens("12-month momentum", "momentum_12_1", True, 30),
    Lens("6-month return", "return_6m", True, 20),
    Lens("1-month return", "return_1m", True, 20),
    Lens(
        "1-month pullback in an uptrend",
        "return_1m",
        False,
        15,
        filters=(_bounded("momentum_12_1", low=0.1),),
    ),
    Lens("free cash flow yield", "fcf_yield", True, 30, filters=(_bounded("fcf_yield", high=0.5),)),
    Lens("low P/E", "pe_ratio", False, 20, filters=(_bounded("pe_ratio", low=1.0),)),
    Lens(
        "revenue growth",
        "revenue_growth",
        True,
        25,
        filters=(_bounded("revenue_growth", high=5.0),),
    ),
    Lens(
        "earnings growth",
        "earnings_growth",
        True,
        20,
        filters=(_bounded("earnings_growth", high=10.0), _bounded("pe_ratio", low=1.0)),
    ),
    Lens(
        "return on equity",
        "return_on_equity",
        True,
        25,
        filters=(_bounded("return_on_equity", high=2.0),),
    ),
    Lens("operating margin", "operating_margin", True, 20),
    Lens("low volatility", "volatility", False, 25),
    Lens("most traded ETFs", "avg_dollar_volume", True, 40, asset_type="etf"),
    Lens("ETF 12-month momentum", "momentum_12_1", True, 15, asset_type="etf"),
    Lens("low-volatility ETFs", "volatility", False, 10, asset_type="etf"),
)
"""The measures candidates are taken on, in the order their leaders are listed."""


def lenses_for(asset_types: list[AssetType]) -> list[Lens]:
    """The lenses for the kinds of security wanted; every one when none is named."""
    return [lens for lens in LENSES if not asset_types or lens.asset_type in asset_types]


def merge_candidates(found: list[tuple[Lens, list[RankedStock]]]) -> list[PickCandidate]:
    """The union of every lens's leaders, each security once with the lenses it led on, in
    the order first found, up to :data:`~app.models.picks.MAX_CANDIDATES`."""
    candidates: dict[str, PickCandidate] = {}
    for lens, stocks in found:
        for stock in stocks:
            known = candidates.get(stock.symbol)
            if known is not None:
                if lens.name not in known.lenses:
                    known.lenses.append(lens.name)
                continue
            if len(candidates) >= MAX_CANDIDATES:
                continue
            candidates[stock.symbol] = PickCandidate(
                **stock.model_dump(include=set(PickCandidate.model_fields) - {"lenses"}),
                lenses=[lens.name],
            )
    return list(candidates.values())
