"""Choosing the candidates Claude picks from: the screener's leaders on many measures.

The screener holds thousands of securities, too many to show Claude, and most are not
worth showing. Each :class:`Lens` is a screen that takes the leaders on one measure, among
securities liquid and large enough to buy; the candidates are their union, so the list
holds what is strongest on value, growth, quality, momentum, and steadiness at once, and
Claude judges between them. Nothing here reads the database: the endpoint runs each lens's
screen and hands the results to :func:`merge_candidates`.
"""

import statistics
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import TypeVar

from app.models.markets import EarningsEvent
from app.models.news import NewsArticle, NewsHeadline
from app.models.picks import (
    MAX_CANDIDATE_EVENTS,
    MAX_CANDIDATES,
    CandidateEvents,
    CandidateFiling,
    PickCandidate,
    ScreenerFacts,
    SectorSnapshot,
)
from app.models.alternative import SecFiling
from app.models.signals import EarningsSurprise, InsiderTrade, RecommendationMonth
from app.models.screener import (
    AssetType,
    RankedStock,
    ScreenedStock,
    ScreenField,
    ScreenFilter,
    ScreenRequest,
    ScreenSort,
)
from app.stats.news import company_headline_importance
from app.stats.peers import percentile_of

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


SMALL_MID_CAP: float = 10_000_000_000.0
"""Market cap, in dollars, below which a company counts as small or mid cap."""

LENSES: tuple[Lens, ...] = (
    Lens("largest companies", "market_cap", True, 50),
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
    Lens("gross margin", "gross_margin", True, 15),
    Lens(
        "low EV/EBITDA",
        "ev_to_ebitda",
        False,
        15,
        filters=(_bounded("ev_to_ebitda", low=1.0),),
    ),
    Lens(
        "low price to sales with growth",
        "price_to_sales",
        False,
        15,
        filters=(_bounded("price_to_sales", low=0.05), _bounded("revenue_growth", low=0.1)),
    ),
    Lens("net margin", "net_margin", True, 15),
    Lens("smallest companies", "market_cap", False, 25),
    Lens(
        "value tilt",
        "beta_value",
        True,
        15,
        filters=(_bounded("factor_r_squared", low=0.1),),
    ),
    Lens(
        "small-company tilt",
        "beta_size",
        True,
        15,
        filters=(_bounded("factor_r_squared", low=0.1),),
    ),
    Lens("low volatility", "volatility", False, 25),
    Lens("small drawdown", "max_drawdown", True, 15),
    Lens(
        "low market beta",
        "beta_market",
        False,
        15,
        filters=(_bounded("beta_market", low=0.0), _bounded("factor_r_squared", low=0.1)),
    ),
    # Under-followed companies: small and mid caps, which fewer analysts and large funds
    # cover, on what tends to matter for them.
    Lens(
        "small/mid cap: growth with free cash flow",
        "revenue_growth",
        True,
        20,
        filters=(
            _bounded("market_cap", high=SMALL_MID_CAP),
            _bounded("revenue_growth", high=5.0),
            _bounded("fcf_yield", low=0.02),
        ),
    ),
    Lens(
        "small/mid cap: free cash flow yield",
        "fcf_yield",
        True,
        20,
        filters=(_bounded("market_cap", high=SMALL_MID_CAP), _bounded("fcf_yield", high=0.5)),
    ),
    Lens(
        "small/mid cap: 12-month momentum",
        "momentum_12_1",
        True,
        20,
        filters=(_bounded("market_cap", high=SMALL_MID_CAP),),
    ),
    Lens(
        "small/mid cap: return on equity at a low P/E",
        "return_on_equity",
        True,
        20,
        filters=(
            _bounded("market_cap", high=SMALL_MID_CAP),
            _bounded("return_on_equity", high=2.0),
            _bounded("pe_ratio", low=1.0, high=20.0),
        ),
    ),
    Lens(
        "cheap fast growers",
        "ev_to_ebitda",
        False,
        20,
        filters=(_bounded("ev_to_ebitda", low=1.0), _bounded("revenue_growth", low=0.15, high=5.0)),
    ),
    Lens(
        "recovering after a down year",
        "return_1m",
        True,
        15,
        filters=(_bounded("momentum_12_1", high=-0.1), _bounded("fcf_yield", low=0.0)),
    ),
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


SECTOR_PERCENTILE_FIELDS: tuple[ScreenField, ...] = (
    "pe_ratio",
    "ev_to_ebitda",
    "fcf_yield",
    "revenue_growth",
    "operating_margin",
    "return_on_equity",
    "momentum_12_1",
    "volatility",
)
"""Figures each candidate is ranked on within its sector, as ``<field>_sector_pct``."""

MIN_SECTOR_STOCKS: int = 5
"""Fewest stocks of a sector with a figure for a candidate to be ranked among them on it."""

ALL_STOCKS: str = "All stocks"
"""The name of the whole screener's row among the sector snapshots."""


def _usable(stock: ScreenedStock, field: ScreenField) -> float | None:
    """``field`` of ``stock``, leaving out a P/E that is a loss and anything not finite."""
    value: float | None = getattr(stock, field)
    if value is None or value != value:
        return None
    if field == "pe_ratio" and value <= 0:
        return None
    return value


def _median(values: Sequence[float]) -> float | None:
    return round(statistics.median(values), 4) if values else None


def _snapshot(name: str, stocks: Sequence[ScreenedStock]) -> SectorSnapshot:
    def figures(field: ScreenField) -> list[float]:
        return [value for stock in stocks if (value := _usable(stock, field)) is not None]

    six_months = figures("return_6m")
    return SectorSnapshot(
        sector=name,
        stocks=len(stocks),
        median_pe_ratio=_median(figures("pe_ratio")),
        median_ev_to_ebitda=_median(figures("ev_to_ebitda")),
        median_fcf_yield=_median(figures("fcf_yield")),
        median_revenue_growth=_median(figures("revenue_growth")),
        median_operating_margin=_median(figures("operating_margin")),
        median_return_1m=_median(figures("return_1m")),
        median_return_6m=_median(six_months),
        median_momentum_12_1=_median(figures("momentum_12_1")),
        median_volatility=_median(figures("volatility")),
        rising_6m=(
            round(sum(1 for value in six_months if value > 0) / len(six_months), 4)
            if six_months
            else None
        ),
    )


def _by_sector(stocks: Iterable[ScreenedStock]) -> dict[str, list[ScreenedStock]]:
    sectors: dict[str, list[ScreenedStock]] = {}
    for stock in stocks:
        if stock.asset_type == "stock" and stock.sector:
            sectors.setdefault(stock.sector, []).append(stock)
    return sectors


def sector_snapshots(
    stocks: Sequence[ScreenedStock], only: Collection[str] | None = None
) -> list[SectorSnapshot]:
    """The whole screener's stocks, then each sector's (those in ``only``, when given),
    largest sector first, as medians."""
    companies = [stock for stock in stocks if stock.asset_type == "stock"]
    if not companies:
        return []
    sectors = sorted(
        (
            (name, group)
            for name, group in _by_sector(companies).items()
            if only is None or name in only
        ),
        key=lambda item: (-len(item[1]), item[0]),
    )
    return [_snapshot(ALL_STOCKS, companies)] + [_snapshot(name, group) for name, group in sectors]


Facts = TypeVar("Facts", bound=ScreenerFacts)


def with_sector_percentiles(
    candidates: list[Facts], stocks: Sequence[ScreenedStock]
) -> list[Facts]:
    """``candidates`` with where each stock's figures fall in its sector, as
    ``<field>_sector_pct`` for each of :data:`SECTOR_PERCENTILE_FIELDS`."""
    sectors = _by_sector(stocks)
    values: dict[tuple[str, ScreenField], list[float]] = {}
    for name, group in sectors.items():
        for field in SECTOR_PERCENTILE_FIELDS:
            values[name, field] = [
                value for stock in group if (value := _usable(stock, field)) is not None
            ]
    ranked = []
    for candidate in candidates:
        update: dict[str, float] = {}
        if candidate.asset_type == "stock" and candidate.sector in sectors:
            for field in SECTOR_PERCENTILE_FIELDS:
                own = getattr(candidate, field)
                if own is None or (field == "pe_ratio" and own <= 0):
                    continue
                peers = values[candidate.sector, field]
                if len(peers) >= MIN_SECTOR_STOCKS:
                    update[f"{field}_sector_pct"] = round(percentile_of(own, peers), 1)
        ranked.append(candidate.model_copy(update=update) if update else candidate)
    return ranked


def with_listing_facts(
    candidates: list[Facts],
    earnings: Iterable[EarningsEvent] | None,
    sp500: Collection[str] | None,
) -> list[Facts]:
    """``candidates`` with each one's next earnings report among ``earnings`` and whether
    the S&P 500 holds it; either is left null when its source is ``None``."""
    next_report: dict[str, date] = {}
    for event in earnings or []:
        known = next_report.get(event.symbol)
        if known is None or event.date < known:
            next_report[event.symbol] = event.date
    return [
        candidate.model_copy(
            update={
                "next_earnings": next_report.get(candidate.symbol),
                "sp500": None if sp500 is None else candidate.symbol in sp500,
            }
        )
        for candidate in candidates
    ]


EVENTS_SHORTLIST: int = 60
"""Candidates whose own news, filings, and signals are wanted: one Finnhub request for the
news and one keyless EDGAR request each, cached and shared with the stock page, and three
Finnhub requests for the signals; :func:`app.api.v1.endpoints.picks.plan_requests` cuts
the list to what fits the time the person will wait."""


def events_shortlist(
    candidates: Sequence[PickCandidate],
    count: int = EVENTS_SHORTLIST,
    first: Collection[str] = (),
) -> list[PickCandidate]:
    """The stocks whose news is worth fetching: those in ``first`` (the tickers the person
    added, which no lens ranks), then the likeliest to be picked, those that led on the
    most measures, then the largest. ETFs are left out, since their news is the market's."""
    stocks = [candidate for candidate in candidates if candidate.asset_type == "stock"]
    ordered = sorted(
        stocks,
        key=lambda c: (c.symbol not in first, -len(c.lenses), -(c.market_cap or 0.0), c.symbol),
    )
    return ordered[: min(count, MAX_CANDIDATE_EVENTS)]


HEADLINES_PER_CANDIDATE: int = 12
"""Most headlines given for one candidate or holding."""

FEWEST_HEADLINES: int = 4
"""Fewest headlines given for each one whose news was fetched, however many there are."""

HEADLINE_BUDGET: int = 240
"""Company headlines given to Claude in all, shared evenly among the stocks whose news was
fetched: 30 picks get eight each, 40 holdings six, and ten holdings twelve. The articles
are already fetched (one request per company, or one shared feed), so a larger share costs
tokens, about 70 a headline, not requests."""


def headlines_each(stocks: int) -> int:
    """Headlines each of ``stocks`` gets: an even share of :data:`HEADLINE_BUDGET`, from
    :data:`FEWEST_HEADLINES` to :data:`HEADLINES_PER_CANDIDATE`."""
    if stocks <= 0:
        return HEADLINES_PER_CANDIDATE
    return max(FEWEST_HEADLINES, min(HEADLINES_PER_CANDIDATE, HEADLINE_BUDGET // stocks))


CANDIDATE_SUMMARY_CHARS: int = 200
"""Length a candidate headline's summary is cut to."""

FILINGS_PER_CANDIDATE: int = 6
"""Most SEC filings given for one candidate."""

QUIET_FORMS: tuple[str, ...] = ("424B", "FWP", "SC 13G", "13F", "S-8", "11-K", "CORRESP", "UPLOAD")
"""Forms that seldom bear on a pick, such as a prospectus supplement or a passive holder's
notice, left out to keep the list short."""


def _cut(text: str | None) -> str | None:
    if text is None or len(text) <= CANDIDATE_SUMMARY_CHARS:
        return text
    return text[:CANDIDATE_SUMMARY_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…"


def candidate_events(
    symbol: str,
    articles: list[NewsArticle] | None,
    filings: list[SecFiling] | None,
    now: datetime,
    headlines_kept: int = HEADLINES_PER_CANDIDATE,
) -> CandidateEvents:
    """One candidate's news, its headlines that matter most first chosen then shown
    newest first, and its filings, without the forms in :data:`QUIET_FORMS`. ``None``
    means the source could not be read."""
    headlines: list[NewsHeadline] = []
    if articles:
        scored = sorted(
            articles,
            key=lambda article: company_headline_importance(article, symbol, now),
            reverse=True,
        )[:headlines_kept]
        headlines = [
            NewsHeadline(
                published_at=article.published_at,
                source=article.source,
                headline=article.headline,
                summary=_cut(article.summary),
            )
            for article in sorted(scored, key=lambda article: article.published_at, reverse=True)
        ]
    kept = [
        CandidateFiling(form=filing.form, filed=filing.filed, items=filing.items)
        for filing in filings or []
        if not filing.form.upper().startswith(QUIET_FORMS)
    ][:FILINGS_PER_CANDIDATE]
    return CandidateEvents(
        symbol=symbol,
        news_articles=None if articles is None else len(articles),
        headlines=headlines,
        filings=kept,
    )


def screener_facts(stock: ScreenedStock) -> ScreenerFacts:
    """What Claude is given of a security the screener holds, before its sector ranks,
    earnings date, and index membership are added."""
    return ScreenerFacts(**stock.model_dump(include=set(ScreenerFacts.model_fields)))


BUY_CHANGE_MONTHS: int = 3
"""Months back the change in analysts' buy share is measured from."""


def signal_facts(
    recommendations: list[RecommendationMonth] | None,
    surprises: list[EarningsSurprise] | None,
    trades: list[InsiderTrade] | None,
) -> dict[str, float | int | None]:
    """The signal columns of one stock (:data:`app.ai.picks.SIGNAL_COLUMNS`) from its
    analysts' rating counts (newest first), earnings surprises (newest first), and open-market
    insider trades; a part passed as ``None`` could not be fetched and stays empty."""
    facts: dict[str, float | int | None] = {}
    if recommendations:
        latest = recommendations[0]

        def buy_share(month: RecommendationMonth) -> float | None:
            total = month.strong_buy + month.buy + month.hold + month.sell + month.strong_sell
            return None if total == 0 else (month.strong_buy + month.buy) / total

        facts["analyst_buy"] = latest.strong_buy + latest.buy
        facts["analyst_hold"] = latest.hold
        facts["analyst_sell"] = latest.sell + latest.strong_sell
        if len(recommendations) > BUY_CHANGE_MONTHS:
            now, before = buy_share(latest), buy_share(recommendations[BUY_CHANGE_MONTHS])
            if now is not None and before is not None:
                facts["analyst_buy_change"] = round(now - before, 4)
    if surprises:
        facts["eps_surprise"] = surprises[0].surprise_percent
        recent = [q for q in surprises[:4] if q.surprise is not None]
        if recent:
            facts["eps_beats"] = sum(1 for q in recent if (q.surprise or 0.0) > 0)
    if trades is not None:
        facts["insider_net_value"] = round(
            sum(trade.value or 0.0 for trade in trades if trade.code in ("P", "S")), 2
        )
    return facts


def with_signal_facts(
    rows: Sequence[Facts], signals: dict[str, dict[str, float | int | None]]
) -> list[Facts]:
    """``rows`` with each one's signal columns filled from ``signals``, by symbol."""
    return [
        row.model_copy(update=signals[row.symbol]) if row.symbol in signals else row for row in rows
    ]


def taking_turns(ranked: Sequence[Sequence[str]]) -> list[str]:
    """The symbols of several ranked lists, each list's first, then each one's second, and
    so on, each symbol once: so when only the first few get their own news, every account
    reviewed together gets its largest holdings covered, not just the largest account."""
    order: list[str] = []
    seen: set[str] = set()
    for turn in range(max((len(symbols) for symbols in ranked), default=0)):
        for symbols in ranked:
            if turn < len(symbols) and symbols[turn] not in seen:
                seen.add(symbols[turn])
                order.append(symbols[turn])
    return order
