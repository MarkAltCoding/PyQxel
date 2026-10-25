"""Analyst recommendations, earnings, insider trading, key metrics, peers and symbol search
from Finnhub, on the user's own key.

Finnhub's free plan covers each of these for US companies, as checked against a free key
in October 2026: monthly analyst rating counts, the last four quarters' earnings
surprises, three months of the upcoming earnings calendar, insider transactions filed on
SEC Form 4 and the monthly insider sentiment built from them, basic financials (key
metrics), peers, and symbol search. It does not cover price history, price targets,
estimates, dividends or international listings. They change at most daily, so answers are kept
for :data:`SIGNALS_TTL_SECONDS` in memory and in Redis, under the provider's name, and
only served to users who query Finnhub themselves.

Each part is fetched on its own, and one Finnhub cannot supply, such as a calendar a plan
does not cover, is left out with a notice rather than failing the rest.
"""

import asyncio
import json
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable
from datetime import date, timedelta
from typing import Any, TypeVar

import httpx2

from app.core.cache import cache_get, cache_set
from app.data.fetcher import (
    DataFetchError,
    FinnhubRateLimitError,
    ProviderNotConfiguredError,
    ProviderPlanError,
    SymbolNotFoundError,
    finnhub_get,
    finnhub_key,
    finnhub_symbol,
)
from app.models.metrics import CompanyMetrics, KeyMetrics
from app.models.screener import SearchMatch
from app.models.signals import (
    EarningsDate,
    EarningsSurprise,
    InsiderSentimentMonth,
    InsiderTrade,
    MarketSignals,
    RecommendationMonth,
)

logger = logging.getLogger(__name__)

SIGNALS_TTL_SECONDS: float = 6 * 60 * 60
"""How long each Finnhub answer is reused."""
MEMORY_SIZE: int = 512
"""Answers kept in process memory, for deployments without Redis."""
RECOMMENDATION_MONTHS: int = 6
"""Months of analyst ratings kept."""
EARNINGS_LOOKAHEAD_DAYS: int = 90
"""Days ahead the earnings calendar is searched, enough to reach the next quarter's report."""
INSIDER_DAYS: int = 90
"""Days of insider transactions summarized."""
OPEN_MARKET_CODES: frozenset[str] = frozenset({"P", "S"})
"""Form 4 codes of open-market purchases and sales."""
INSIDER_SENTIMENT_MONTHS: int = 12
"""Months of insider sentiment kept."""
SEARCH_TTL_SECONDS: float = 24 * 60 * 60
"""How long a symbol search's answer is reused."""

PERCENT, MILLIONS, PLAIN = 0.01, 1e6, 1.0
METRIC_FIELDS: dict[str, tuple[str, float]] = {
    "week_52_high": ("52WeekHigh", PLAIN),
    "week_52_low": ("52WeekLow", PLAIN),
    "return_52_week": ("52WeekPriceReturnDaily", PERCENT),
    "return_ytd": ("yearToDatePriceReturnDaily", PERCENT),
    "relative_to_sp500_52_week": ("priceRelativeToS&P50052Week", PERCENT),
    "beta": ("beta", PLAIN),
    "average_volume_10_day": ("10DayAverageTradingVolume", MILLIONS),
    "average_volume_3_month": ("3MonthAverageTradingVolume", MILLIONS),
    "market_cap": ("marketCapitalization", MILLIONS),
    "enterprise_value": ("enterpriseValue", MILLIONS),
    "pe_ttm": ("peTTM", PLAIN),
    "forward_pe": ("forwardPE", PLAIN),
    "peg_ttm": ("pegTTM", PLAIN),
    "ps_ttm": ("psTTM", PLAIN),
    "pb": ("pb", PLAIN),
    "ev_ebitda_ttm": ("evEbitdaTTM", PLAIN),
    "ev_revenue_ttm": ("evRevenueTTM", PLAIN),
    "price_to_fcf_ttm": ("pfcfShareTTM", PLAIN),
    "dividend_yield": ("dividendYieldIndicatedAnnual", PERCENT),
    "dividend_per_share_ttm": ("dividendPerShareTTM", PLAIN),
    "payout_ratio_ttm": ("payoutRatioTTM", PERCENT),
    "dividend_growth_5y": ("dividendGrowthRate5Y", PERCENT),
    "gross_margin_ttm": ("grossMarginTTM", PERCENT),
    "operating_margin_ttm": ("operatingMarginTTM", PERCENT),
    "net_margin_ttm": ("netProfitMarginTTM", PERCENT),
    "roe_ttm": ("roeTTM", PERCENT),
    "roa_ttm": ("roaTTM", PERCENT),
    "roi_ttm": ("roiTTM", PERCENT),
    "revenue_growth_ttm": ("revenueGrowthTTMYoy", PERCENT),
    "eps_growth_ttm": ("epsGrowthTTMYoy", PERCENT),
    "revenue_growth_5y": ("revenueGrowth5Y", PERCENT),
    "eps_growth_5y": ("epsGrowth5Y", PERCENT),
    "eps_ttm": ("epsTTM", PLAIN),
    "book_value_per_share": ("bookValuePerShareQuarterly", PLAIN),
    "current_ratio": ("currentRatioQuarterly", PLAIN),
    "quick_ratio": ("quickRatioQuarterly", PLAIN),
    "debt_to_equity": ("totalDebt/totalEquityQuarterly", PLAIN),
    "interest_coverage_ttm": ("netInterestCoverageTTM", PLAIN),
}
"""The Finnhub basic financials field each :class:`KeyMetrics` field is read from, and the
factor that converts it: Finnhub gives ratios in percent and totals in millions."""

_memory: "OrderedDict[str, tuple[float, str]]" = OrderedDict()
"""Cache key -> (time fetched, JSON answer)."""

T = TypeVar("T")


def clear_signals_memory() -> None:
    """Forget the Finnhub answers kept in process memory."""
    _memory.clear()


def _api_key() -> str:
    api_key = finnhub_key()
    if api_key is None:
        raise ProviderNotConfiguredError(
            "Analyst ratings, earnings and insider trades come from Finnhub on your own "
            "key: add it with PUT /api/v1/me/credentials."
        )
    return api_key


async def _get(
    path: str,
    params: dict[str, str],
    client: httpx2.AsyncClient | None,
    ttl: float = SIGNALS_TTL_SECONDS,
) -> Any:
    """A Finnhub answer, reused for ``ttl`` seconds."""
    api_key = _api_key()
    key = "finnhub:" + path + ":" + ":".join(f"{k}={v}" for k, v in sorted(params.items()))
    entry = _memory.get(key)
    text = entry[1] if entry and time.monotonic() - entry[0] < ttl else None
    if text is None:
        text = await cache_get(key)
    if text is not None:
        try:
            return json.loads(text)
        except ValueError:
            logger.warning("Ignoring an unreadable cached Finnhub %s answer.", path)
    payload = await finnhub_get(path, params, api_key, client)
    text = json.dumps(payload)
    _memory[key] = (time.monotonic(), text)
    _memory.move_to_end(key)
    while len(_memory) > MEMORY_SIZE:
        _memory.popitem(last=False)
    await cache_set(key, text, ttl)
    return payload


def _date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _count(value: object) -> int:
    number = _number(value)
    return max(0, int(number)) if number is not None else 0


def _name(value: object) -> str:
    """An insider's name as filed, in title case when filed in capitals."""
    name = str(value or "").strip() or "Unknown"
    return name.title() if name.isupper() else name


def _strings(payload: Any) -> list[str]:
    return [item for item in payload if isinstance(item, str)] if isinstance(payload, list) else []


def _rows(payload: Any) -> list[dict[str, Any]]:
    return [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []


async def fetch_recommendations(
    symbol: str, client: httpx2.AsyncClient | None = None
) -> list[RecommendationMonth]:
    """Monthly counts of analysts' ratings, newest first."""
    payload = await _get("stock/recommendation", {"symbol": finnhub_symbol(symbol)}, client)
    months: list[RecommendationMonth] = []
    for row in _rows(payload):
        period = _date(row.get("period"))
        if period is None:
            continue
        months.append(
            RecommendationMonth(
                period=period,
                strong_buy=_count(row.get("strongBuy")),
                buy=_count(row.get("buy")),
                hold=_count(row.get("hold")),
                sell=_count(row.get("sell")),
                strong_sell=_count(row.get("strongSell")),
            )
        )
    months.sort(key=lambda month: month.period, reverse=True)
    return [month for month in months if month.total > 0][:RECOMMENDATION_MONTHS]


async def fetch_earnings_surprises(
    symbol: str, client: httpx2.AsyncClient | None = None
) -> list[EarningsSurprise]:
    """Reported against estimated EPS for recent quarters, newest first."""
    payload = await _get("stock/earnings", {"symbol": finnhub_symbol(symbol)}, client)
    quarters: list[EarningsSurprise] = []
    for row in _rows(payload):
        period = _date(row.get("period"))
        if period is None:
            continue
        percent = _number(row.get("surprisePercent"))
        quarters.append(
            EarningsSurprise(
                period=period,
                year=_count(row.get("year")) or None,
                quarter=_count(row.get("quarter")) or None,
                actual=_number(row.get("actual")),
                estimate=_number(row.get("estimate")),
                surprise=_number(row.get("surprise")),
                # Finnhub gives the surprise in percent.
                surprise_percent=None if percent is None else percent / 100,
            )
        )
    return sorted(quarters, key=lambda quarter: quarter.period, reverse=True)


async def fetch_next_earnings(
    symbol: str, today: date, client: httpx2.AsyncClient | None = None
) -> EarningsDate | None:
    """The next scheduled earnings report within :data:`EARNINGS_LOOKAHEAD_DAYS`, if any."""
    end = today + timedelta(days=EARNINGS_LOOKAHEAD_DAYS)
    payload = await _get(
        "calendar/earnings",
        {"symbol": finnhub_symbol(symbol), "from": today.isoformat(), "to": end.isoformat()},
        client,
    )
    rows = _rows(payload.get("earningsCalendar") if isinstance(payload, dict) else None)
    upcoming: list[EarningsDate] = []
    for row in rows:
        day = _date(row.get("date"))
        if day is None or day < today:
            continue
        upcoming.append(
            EarningsDate(
                date=day,
                hour=row.get("hour") or None,
                year=_count(row.get("year")) or None,
                quarter=_count(row.get("quarter")) or None,
                eps_estimate=_number(row.get("epsEstimate")),
                revenue_estimate=_number(row.get("revenueEstimate")),
            )
        )
    return min(upcoming, key=lambda entry: entry.date, default=None)


async def fetch_insider_trades(
    symbol: str, today: date, client: httpx2.AsyncClient | None = None
) -> list[InsiderTrade]:
    """Open-market insider purchases and sales over :data:`INSIDER_DAYS`, newest first."""
    start = today - timedelta(days=INSIDER_DAYS)
    payload = await _get(
        "stock/insider-transactions",
        {"symbol": finnhub_symbol(symbol), "from": start.isoformat(), "to": today.isoformat()},
        client,
    )
    rows = _rows(payload.get("data") if isinstance(payload, dict) else None)
    trades: list[InsiderTrade] = []
    for row in rows:
        code, day = row.get("transactionCode"), _date(row.get("transactionDate"))
        change = _number(row.get("change"))
        if code not in OPEN_MARKET_CODES or day is None or day < start or not change:
            continue
        if row.get("isDerivative"):
            continue
        price = _number(row.get("transactionPrice"))
        price = price if price is not None and price > 0 else None
        trades.append(
            InsiderTrade(
                name=_name(row.get("name")),
                transaction_date=day,
                filing_date=_date(row.get("filingDate")),
                code=code,
                shares=change,
                price=price,
                value=None if price is None else change * price,
            )
        )
    return sorted(trades, key=lambda trade: trade.transaction_date, reverse=True)


async def fetch_insider_sentiment(
    symbol: str, today: date, client: httpx2.AsyncClient | None = None
) -> list[InsiderSentimentMonth]:
    """Monthly insider sentiment for the last year, newest first."""
    start = today - timedelta(days=366)
    payload = await _get(
        "stock/insider-sentiment",
        {"symbol": finnhub_symbol(symbol), "from": start.isoformat(), "to": today.isoformat()},
        client,
    )
    rows = _rows(payload.get("data") if isinstance(payload, dict) else None)
    months: list[InsiderSentimentMonth] = []
    for row in rows:
        year, month, mspr = (
            _count(row.get("year")),
            _count(row.get("month")),
            _number(row.get("mspr")),
        )
        if not year or not 1 <= month <= 12 or mspr is None:
            continue
        months.append(
            InsiderSentimentMonth(
                month=date(year, month, 1),
                mspr=max(-100.0, min(100.0, mspr)),
                net_shares=_number(row.get("change")) or 0.0,
            )
        )
    months.sort(key=lambda entry: entry.month, reverse=True)
    return months[:INSIDER_SENTIMENT_MONTHS]


PART_NAMES: tuple[str, ...] = (
    "analyst ratings",
    "earnings results",
    "the earnings calendar",
    "insider trades",
    "insider sentiment",
)


def _part(name: str, result: T | BaseException) -> tuple[T | None, str | None]:
    """A part's answer, or why Finnhub could not supply it."""
    if isinstance(result, ProviderNotConfiguredError):
        raise result
    if isinstance(result, ProviderPlanError):
        return None, f"your Finnhub plan does not cover {name}"
    if isinstance(result, FinnhubRateLimitError):
        return None, f"{name} hit Finnhub's rate limit"
    if isinstance(result, DataFetchError):
        return None, f"{name} could not be fetched"
    if isinstance(result, BaseException):
        raise result
    return result, None


async def fetch_signals(
    symbol: str, today: date, client: httpx2.AsyncClient | None = None
) -> MarketSignals:
    """Analyst ratings, earnings results and dates, and insider trades for ``symbol``.

    Every open-market insider trade of the last :data:`INSIDER_DAYS` is listed, and
    ``insiders`` is left for :func:`app.stats.signals.summarize_signals` to total.

    Raises:
        ProviderNotConfiguredError: If the user has no Finnhub key, or it is rejected.
    """
    symbol = symbol.strip().upper()
    _api_key()
    parts: list[Awaitable[Any]] = [
        fetch_recommendations(symbol, client),
        fetch_earnings_surprises(symbol, client),
        fetch_next_earnings(symbol, today, client),
        fetch_insider_trades(symbol, today, client),
        fetch_insider_sentiment(symbol, today, client),
    ]
    results = await asyncio.gather(*parts, return_exceptions=True)
    (recommendations, a), (earnings, b), (next_earnings, c), (trades, d), (sentiment, e) = (
        _part(name, result) for name, result in zip(PART_NAMES, results)
    )
    missing = [reason for reason in (a, b, c, d, e) if reason]
    notice = None
    if missing:
        notice = f"Left out: {'; '.join(missing)}."
    return MarketSignals(
        symbol=symbol,
        recommendations=recommendations or [],
        earnings=earnings or [],
        next_earnings=next_earnings,
        insider_trades=trades or [],
        insider_sentiment=sentiment or [],
        insider_window=(None if trades is None else (today - timedelta(days=INSIDER_DAYS), today)),
        notice=notice,
    )


def _metrics(payload: Any) -> KeyMetrics:
    """Read Finnhub's basic financials, converting percents and millions."""
    values = payload.get("metric") if isinstance(payload, dict) else None
    if not isinstance(values, dict):
        values = {}
    fields: dict[str, Any] = {}
    for name, (source, factor) in METRIC_FIELDS.items():
        number = _number(values.get(source))
        if number is not None:
            fields[name] = number * factor
    fields["week_52_high_date"] = _date(values.get("52WeekHighDate"))
    fields["week_52_low_date"] = _date(values.get("52WeekLowDate"))
    return KeyMetrics(**fields)


async def fetch_company_metrics(
    symbol: str, client: httpx2.AsyncClient | None = None
) -> CompanyMetrics:
    """``symbol``'s key metrics and peers.

    Raises:
        ProviderNotConfiguredError: If the user has no Finnhub key, or it is rejected.
        ProviderPlanError: If the plan does not cover the company, as for foreign listings.
        SymbolNotFoundError: If Finnhub has no metrics for ``symbol``.
        DataFetchError: If Finnhub fails.
    """
    symbol = symbol.strip().upper()
    params = {"symbol": finnhub_symbol(symbol)}
    results: tuple[Any, Any] = await asyncio.gather(
        _get("stock/metric", params | {"metric": "all"}, client),
        _get("stock/peers", params, client),
        return_exceptions=True,
    )
    payload, peers = results
    if isinstance(payload, BaseException):
        raise payload
    metrics = _metrics(payload)
    if not metrics.model_dump(exclude_none=True):
        raise SymbolNotFoundError(f"Finnhub has no metrics for {symbol!r}.")
    if isinstance(peers, ProviderNotConfiguredError):
        raise peers
    if isinstance(peers, DataFetchError):
        logger.info("Finnhub has no peers for %s: %s", symbol, peers)
    elif isinstance(peers, BaseException):
        raise peers
    own = finnhub_symbol(symbol)
    names = [peer for peer in _strings(peers) if peer != own]
    return CompanyMetrics(symbol=symbol, metrics=metrics, peers=names)


async def search_symbols(
    query: str, limit: int, client: httpx2.AsyncClient | None = None
) -> list[SearchMatch]:
    """US listings whose symbol or name matches ``query``, as Finnhub finds them.

    Raises:
        ProviderNotConfiguredError: If the user has no Finnhub key, or it is rejected.
        DataFetchError: If Finnhub fails.
    """
    payload = await _get(
        "search", {"q": query.strip(), "exchange": "US"}, client, ttl=SEARCH_TTL_SECONDS
    )
    rows = _rows(payload.get("result") if isinstance(payload, dict) else None)
    matches: list[SearchMatch] = []
    for row in rows:
        symbol, name = row.get("symbol"), row.get("description")
        if not isinstance(symbol, str) or not symbol or not isinstance(name, str) or not name:
            continue
        matches.append(
            SearchMatch(
                # Finnhub names share classes with a dot; the app, as Yahoo does, with a dash.
                symbol=symbol.replace(".", "-"),
                name=name.title() if name.isupper() else name,
                kind=row.get("type") or None,
                source="finnhub",
            )
        )
    return matches[:limit]
