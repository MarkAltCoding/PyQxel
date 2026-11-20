"""Claude's picks: the securities Claude would buy now and hold for a period you choose.

The picks come from the screener: its leaders on value, growth, quality, momentum, and
steadiness are the candidates, Claude picks among them for the holding period and profile
given, and each pick is stored with the screener's figures for it. The screener's own
table can then show the picks alone (``symbols`` of a screen), and a portfolio review is
given the latest picks as candidates for new investments.
"""

import asyncio
import hashlib
import logging
import re
from collections.abc import AsyncIterator, Collection
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from fastapi.sse import EventSourceResponse, ServerSentEvent
from httpx2 import AsyncClient
from pydantic import SecretStr
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import AIRateLimitError, AnalysisError, Written, anthropic_client
from app.ai.external import PASTED_MODEL, ExternalResponseError, parse_response, render_prompt
from app.ai.picks import picks_messages, picks_research_task, stream_picks, write_picks
from app.ai.prompts import PICKS_SYSTEM_PROMPT
from app.ai.web import add_web_research, with_web_research
from app.api.auth import CurrentUser
from app.api.v1.endpoints.markets import (
    PicksBackdrop,
    backdrop_days,
    backdrop_finnhub_requests,
    fetch_picks_backdrop,
)
from app.api.v1.endpoints.research import ai_error, anthropic_key, check_ai_limits
from app.api.v1.endpoints.stocks import upstream_error
from app.core.config import get_settings
from app.data.alternative import fetch_company_filings
from app.data.fetcher import DataFetchError, finnhub_key, finnhub_requests_per_minute
from app.data.index_members import cached_index_members
from app.data.news import NewsFetchError, fetch_company_news
from app.data.panel import fetch_close_panel
from app.data.sec_edgar import FilingFetchError, edgar_client
from app.db.picks import (
    delete_picks,
    recent_picks,
    find_recent_picks,
    get_picks,
    latest_picks,
    list_picks,
    save_picks,
)
from app.db.screener import (
    Universe,
    index_symbols,
    load_listings,
    screen,
    shared_universe,
    shows_universe,
    universe_companies,
    universe_counts,
    universe_listings,
    universe_stocks,
    user_universe,
)
from app.db.session import get_session, get_sessionmaker
from app.models.external import PicksImport, PicksPromptExport
from app.models.alternative import SecFiling
from app.models.news import CompanyNews
from app.models.picks import (
    BENCHMARK_NAMES,
    MAX_CANDIDATE_EVENTS,
    MAX_PICKS,
    MIN_PICKS,
    CandidateEvents,
    Pick,
    PickCandidate,
    PicksContext,
    PicksList,
    PicksReport,
    PicksRequest,
    PicksResponse,
    PicksBenchmark,
    PicksPerformance,
    PicksUniverse,
    SavedPicks,
    SavedPicksList,
    ScreenerFacts,
    SectorSnapshot,
)
from app.models.research import AnalysisDelta, AnalysisStreamError
from app.models.stock import SYMBOL_PATTERN
from app.models.web import WebSearchQuery
from app.stats.picks_performance import ends_on, history_period, start_day, track
from app.stats.picks import (
    EVENTS_SHORTLIST,
    HEADLINES_PER_CANDIDATE,
    candidate_events,
    headlines_each,
    events_shortlist,
    lenses_for,
    merge_candidates,
    screener_facts,
    sector_snapshots,
    with_listing_facts,
    with_sector_percentiles,
)

logger = logging.getLogger(__name__)

router = APIRouter()

Session = Annotated[AsyncSession, Depends(get_session)]

NO_SCREENER: str = (
    "Claude's picks are chosen from the screener, which has no securities for your account "
    "yet. Turn on the nightly screener refresh in Settings to build your own; the picks are "
    "available once it has run."
)


def _database_error(exc: SQLAlchemyError) -> HTTPException:
    """Log a database failure and translate it into a 503."""
    logger.error("Picks database request failed: %s", exc)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="The picks database is unavailable.",
    )


async def _universe(session: AsyncSession, user_id: str) -> Universe | None:
    """The screener ``user_id`` may pick from: their own, else the shared one when it may
    be shown to them, else ``None``."""
    own = await user_universe(session, user_id)
    if own is not None:
        return own
    return await shared_universe(session) if await shows_universe(session) else None


async def _market(horizon_days: float | None) -> tuple[PicksBackdrop | None, str | None]:
    """The market backdrop for a holding period of ``horizon_days``, or a notice saying it
    is missing."""
    try:
        return await fetch_picks_backdrop(date.today(), horizon_days), None
    except DataFetchError as exc:
        logger.info("No market backdrop for Claude's picks: %s", exc)
        return None, "The market calendar and headlines could not be fetched."


FILING_DAYS: int = 30
"""Days of a candidate's SEC filings given to Claude."""

NEWS_MARGIN: int = 2
"""Finnhub requests a minute kept free besides the backdrop's and the news', for the pages
the person has open."""


def news_requests_left(horizon_days: float | None) -> int | None:
    """How many companies' news can be asked of Finnhub within the key's minute once the
    backdrop for ``horizon_days`` is fetched; ``None`` without a Finnhub key, when news comes
    from Alpha Vantage's one shared feed and costs nothing per company."""
    api_key = finnhub_key()
    if api_key is None:
        return None
    used = backdrop_finnhub_requests(backdrop_days(horizon_days)) + NEWS_MARGIN
    return max(0, finnhub_requests_per_minute(api_key) - used)


NEWS_REQUESTS_AT_ONCE: int = 6
"""Company news requests to Finnhub in flight together, well inside its 30 a second."""

EDGAR_REQUESTS_AT_ONCE: int = 4
"""Requests to EDGAR in flight together, inside the SEC's ten a second."""


async def _news(symbol: str, slots: asyncio.Semaphore) -> CompanyNews | None:
    """A candidate's news of the last two weeks, or ``None`` when it cannot be had."""
    async with slots:
        try:
            return await fetch_company_news(symbol)
        except NewsFetchError as exc:
            logger.info("No news on %s for Claude's picks: %s", symbol, exc)
            return None


async def _filings(
    cik: int | None, client: AsyncClient | None, since: date, slots: asyncio.Semaphore
) -> list[SecFiling] | None:
    """A candidate's recent SEC filings, or ``None`` when they cannot be had."""
    if cik is None or client is None:
        return None
    async with slots:
        try:
            return await fetch_company_filings(cik, since, client)
        except DataFetchError as exc:
            logger.info("No SEC filings of CIK %s for Claude's picks: %s", cik, exc)
            return None


async def _candidate_events(
    shortlist: list[PickCandidate], ciks: dict[str, int | None]
) -> list[CandidateEvents]:
    """The news and SEC filings of each candidate on ``shortlist``: one Finnhub request
    each (cached for ten minutes and shared with the stock page) and one keyless request
    to EDGAR. A source that cannot be read is left out for that candidate."""
    if not shortlist:
        return []
    today = date.today()
    news_slots = asyncio.Semaphore(NEWS_REQUESTS_AT_ONCE)
    edgar_slots = asyncio.Semaphore(EDGAR_REQUESTS_AT_ONCE)
    edgar: AsyncClient | None
    try:
        edgar = edgar_client()
    except FilingFetchError as exc:
        logger.info("No SEC filings for Claude's picks: %s", exc)
        edgar = None
    try:
        news, filings = await asyncio.gather(
            asyncio.gather(*(_news(c.symbol, news_slots) for c in shortlist)),
            asyncio.gather(
                *(
                    _filings(
                        ciks.get(c.symbol), edgar, today - timedelta(days=FILING_DAYS), edgar_slots
                    )
                    for c in shortlist
                )
            ),
        )
    finally:
        if edgar is not None:
            await edgar.aclose()
    now = datetime.now(timezone.utc)
    # News from Alpha Vantage's shared feed is split among the candidates it came for.
    each = headlines_each(sum(1 for found in news if found and found.provider == "alpha_vantage"))
    return [
        candidate_events(
            candidate.symbol,
            None if found_news is None else found_news.articles,
            found,
            now,
            (
                each
                if found_news is not None and found_news.provider == "alpha_vantage"
                else HEADLINES_PER_CANDIDATE
            ),
        )
        for candidate, found_news, found in zip(shortlist, news, filings, strict=True)
    ]


@dataclass(frozen=True)
class HoldingFacts:
    """What a portfolio review is given of its holdings beyond their prices, gathered as
    for Claude's picks."""

    figures: list[ScreenerFacts]
    """The screener's facts on each holding it holds, in the order asked."""
    sectors: list[SectorSnapshot]
    """The whole screener's medians, then those of the holdings' sectors."""
    events: list[CandidateEvents]
    backdrop: PicksBackdrop | None
    notices: list[str]


async def holding_facts(
    session: AsyncSession,
    user_id: str,
    symbols: list[str],
    funds: Collection[str] = (),
    horizon_days: float | None = None,
) -> HoldingFacts:
    """The screener's figures, sector ranks, next earnings dates, and index membership of
    ``symbols`` (most important first), the news and SEC filings of the first
    :data:`~app.models.picks.MAX_CANDIDATE_EVENTS` that are not ``funds``, and the market
    backdrop, all from what the picks use: the stored screener, keyless SEC data, and a
    request to Finnhub per company (cached) plus the shared calendars. A source that
    cannot be read is left out with a notice."""
    universe = await _universe(session, user_id)
    figures: list[ScreenerFacts] = []
    sectors: list[SectorSnapshot] = []
    sp500: set[str] | None = None
    ciks: dict[str, int | None] = {}
    known_funds = set(funds)
    if universe is not None and universe.refreshed_at is not None:
        found = await universe_stocks(session, universe, symbols)
        figures = [screener_facts(found[symbol]) for symbol in symbols if symbol in found]
        known_funds |= {stock.symbol for stock in found.values() if stock.asset_type == "etf"}
        ciks = {symbol: stock.cik for symbol, stock in found.items()}
        companies = await universe_companies(session, universe)
        figures = with_sector_percentiles(figures, companies)
        sectors = sector_snapshots(
            companies, {figure.sector for figure in figures if figure.sector}
        )
        sp500 = await _sp500(session, universe)
    shortlist = [
        PickCandidate(symbol=symbol, name=symbol, price=0.0, avg_dollar_volume=0.0, lenses=[])
        for symbol in symbols
        if symbol not in known_funds
    ]
    left = news_requests_left(horizon_days)
    shortlist = shortlist[
        : MAX_CANDIDATE_EVENTS if left is None else min(MAX_CANDIDATE_EVENTS, left)
    ]
    (backdrop, market_notice), events = await asyncio.gather(
        _market(horizon_days), _candidate_events(shortlist, ciks)
    )
    figures = with_listing_facts(figures, None if backdrop is None else backdrop.earnings, sp500)
    notices = [
        notice
        for notice in (
            market_notice,
            (
                None
                if universe is not None
                else "The screener's figures (valuations, growth, margins, momentum) are "
                "missing: your account has no screener yet."
            ),
            (
                "No holding's own news could be fetched: it comes from Finnhub on your key, "
                "or a paid Alpha Vantage plan."
                if events and all(event.news_articles is None for event in events)
                else None
            ),
        )
        if notice
    ]
    return HoldingFacts(
        figures=figures, sectors=sectors, events=events, backdrop=backdrop, notices=notices
    )


async def _sp500(session: AsyncSession, universe: Universe) -> set[str] | None:
    """The symbols in ``universe`` the S&P 500 holds, from the SEC's filings of a fund that
    tracks it (keyless, cached for a day); ``None`` when they cannot be read."""
    try:
        listings = [listing for listing, _ in await load_listings(session)]
        if not listings:
            listings = await universe_listings(session, universe)
        classes = (await cached_index_members("sp500", listings)).classes
    except (DataFetchError, FilingFetchError) as exc:
        logger.info("No S&P 500 members for Claude's picks: %s", exc)
        return None
    return await index_symbols(session, universe, classes) if classes else None


async def picks_context(session: AsyncSession, user_id: str, request: PicksRequest) -> PicksContext:
    """Gather the candidates and the backdrop the picks are made from.

    Raises:
        HTTPException: 503 when the account has no screener to pick from, and 422 when the
            screener holds nothing of the kinds asked for that is liquid enough to offer.
    """
    universe = await _universe(session, user_id)
    if universe is None or universe.refreshed_at is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=NO_SCREENER)
    profile = request.profile
    found = [
        (lens, (await screen(session, lens.request(), universe)).items)
        for lens in lenses_for(profile.asset_types)
    ]
    candidates = merge_candidates(found)
    if not candidates:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The screener holds nothing to pick from of the kinds you asked for: "
            "candidates must trade at least $5 million a day.",
        )
    securities, etfs = await universe_counts(session, universe)
    companies = await universe_companies(session, universe)
    candidates = with_sector_percentiles(candidates, companies)
    sp500 = await _sp500(session, universe)
    ciks = {stock.symbol: stock.cik for stock in companies}
    horizon_days = profile.horizon.total_weeks * 7
    left = news_requests_left(horizon_days)
    shortlist = events_shortlist(
        candidates, EVENTS_SHORTLIST if left is None else min(left, EVENTS_SHORTLIST)
    )
    (backdrop, market_notice), events = await asyncio.gather(
        _market(horizon_days), _candidate_events(shortlist, ciks)
    )
    candidates = with_listing_facts(
        candidates, None if backdrop is None else backdrop.earnings, sp500
    )
    notices = [
        market_notice,
        (
            "Each candidate's next earnings date comes from Finnhub's calendar, which could "
            "not be fetched."
            if backdrop is not None and backdrop.earnings is None
            else None
        ),
        (
            "No candidate's own news could be fetched: it comes from Finnhub on your key, "
            "or a paid Alpha Vantage plan."
            if events and all(event.news_articles is None for event in events)
            else None
        ),
        (
            "The screener holds no ETFs, so the candidates are stocks alone."
            if etfs == 0 and "etf" in (profile.asset_types or ["etf"])
            else None
        ),
    ]
    return PicksContext(
        profile=profile,
        horizon=profile.horizon.label,
        horizon_weeks=round(profile.horizon.total_weeks, 1),
        today=date.today(),
        universe=PicksUniverse(
            securities=securities,
            own=universe.own,
            prices_as_of=universe.prices_as_of,
            refreshed_at=universe.refreshed_at,
            has_etfs=etfs > 0,
        ),
        candidates=candidates,
        market=None if backdrop is None else backdrop.market,
        deal_headlines=[] if backdrop is None else backdrop.deals,
        sectors=sector_snapshots(companies),
        candidate_events=events,
        notice=" ".join(notice for notice in notices if notice) or None,
    )


def _request_key(request: PicksRequest, universe: Universe | None) -> str:
    """Fingerprint what the picks were asked for and the screener data they would use, so
    picks are reused only while both are unchanged."""
    refreshed = "" if universe is None or universe.refreshed_at is None else universe.refreshed_at
    own = universe is not None and universe.own
    text = f"{request.profile.model_dump_json()}|{request.web_search}|{refreshed}|{own}"
    return hashlib.sha256(text.encode()).hexdigest()


class TooFewPicksError(ValueError):
    """Raised when an answer holds fewer usable picks than a list needs."""


def settle_picks(report: PicksReport) -> PicksReport:
    """Tidy the picks of an answer: tickers upper-cased, a repeated or malformed one
    dropped, at most :data:`~app.models.picks.MAX_PICKS` kept, and the weights scaled to
    sum to 1 (equal when none is given).

    Raises:
        TooFewPicksError: If fewer than :data:`~app.models.picks.MIN_PICKS` remain.
    """
    picks: dict[str, Pick] = {}
    for pick in report.picks:
        symbol = pick.symbol.strip().upper()
        if re.fullmatch(SYMBOL_PATTERN, symbol) and symbol not in picks:
            picks[symbol] = pick.model_copy(update={"symbol": symbol})
        if len(picks) == MAX_PICKS:
            break
    if len(picks) < MIN_PICKS:
        raise TooFewPicksError(
            f"The answer holds {len(picks)} usable picks; a list needs at least {MIN_PICKS}."
        )
    total = sum(pick.weight for pick in picks.values())
    settled = [
        pick.model_copy(update={"weight": pick.weight / total if total > 0 else 1 / len(picks)})
        for pick in picks.values()
    ]
    return report.model_copy(update={"picks": settled})


@dataclass(frozen=True)
class PreparedPicks:
    """What a list of picks needs before Claude is asked, or recent picks to reuse."""

    user_id: str
    request: PicksRequest
    key: str
    context: PicksContext | None = None
    api_key: SecretStr | None = None
    cached: PicksResponse | None = None


async def prepare_picks(
    user: CurrentUser, request: Annotated[PicksRequest, Body()] = PicksRequest()
) -> PreparedPicks:
    """Reuse recent picks for the same profile and screener data, or gather the candidates
    for new ones. Runs as a dependency, so streamed picks fail with an HTTP status before
    their stream opens."""
    settings = get_settings()
    try:
        async with get_sessionmaker()() as session:
            key = _request_key(request, await _universe(session, user.id))
            if not request.refresh and settings.analysis_cache_ttl_seconds > 0:
                cached = await find_recent_picks(
                    session,
                    user.id,
                    key,
                    settings.anthropic_model,
                    settings.anthropic_effort,
                    datetime.now(timezone.utc)
                    - timedelta(seconds=settings.analysis_cache_ttl_seconds),
                )
                if cached is not None:
                    return PreparedPicks(
                        user_id=user.id,
                        request=request,
                        key=key,
                        cached=cached.model_copy(update={"cached": True}),
                    )
            api_key = anthropic_key()
            await check_ai_limits(user)
            context = await picks_context(session, user.id, request)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc
    return PreparedPicks(
        user_id=user.id, request=request, key=key, context=context, api_key=api_key
    )


Prepared = Annotated[PreparedPicks, Depends(prepare_picks)]


async def _response(
    user_id: str, context: PicksContext, report: PicksReport, model: str
) -> PicksResponse:
    """A finished list with the screener's figures for each pick it holds; the figures
    are left out when the database cannot be read."""
    securities = []
    try:
        async with get_sessionmaker()() as session:
            universe = await _universe(session, user_id)
            if universe is not None:
                found = await universe_stocks(
                    session, universe, [pick.symbol for pick in report.picks]
                )
                securities = [found[p.symbol] for p in report.picks if p.symbol in found]
    except SQLAlchemyError as exc:
        logger.error("Could not read the picks' figures: %s", exc)
    return PicksResponse(
        report=report,
        securities=securities,
        context=context,
        model=model,
        generated_at=datetime.now(timezone.utc),
    )


async def _remember(
    user_id: str, key: str, response: PicksResponse, model: str, effort: str
) -> PicksResponse:
    """Store new picks; if the database fails, return them unsaved rather than lose them."""
    try:
        async with get_sessionmaker()() as session:
            return await save_picks(session, user_id, key, response, model, effort)
    except SQLAlchemyError as exc:
        logger.error("Could not store Claude's picks: %s", exc)
        return response


async def _finish(
    prepared: PreparedPicks, context: PicksContext, written: Written[PicksReport]
) -> PicksResponse:
    """Settle, look up, and store newly written picks.

    Raises:
        AnalysisError: If the answer holds too few usable picks.
    """
    try:
        report = settle_picks(written.output)
    except TooFewPicksError as exc:
        raise AnalysisError(str(exc)) from exc
    settings = get_settings()
    return await _remember(
        prepared.user_id,
        prepared.key,
        await _response(prepared.user_id, context, report, written.model),
        settings.anthropic_model,
        settings.anthropic_effort,
    )


@router.post("", response_model=PicksResponse, summary="Claude's picks for a holding period")
async def create_picks(prepared: Prepared) -> PicksResponse:
    """Have Claude pick the securities to buy now and hold for ``profile.horizon``.

    The candidates are the leaders of your screener (your own, when your nightly refresh
    has built one, else the shared one) on market cap, momentum, recent returns, free
    cash flow yield, P/E, growth, return on equity, margins, and low volatility, and its
    most traded ETFs, each at least $5 million traded a day. Claude ranks five to ten of
    them for the holding period, risk tolerance, amount, and notes given, with a weight,
    reasons, risk, and how to buy and when to sell for each. With ``web_search``, Claude
    searches the web first, and every fact it brings back is in
    ``context.web_research`` with its sources. All the data is in ``context``, and the
    screener's figures for each pick in ``securities``.

    It is written with, and billed to, your own Anthropic key (the ``X-Anthropic-Key``
    header), within your AI limits, and stored under your account. Picks for the same
    profile and screener data within ``ANALYSIS_CACHE_TTL_SECONDS`` are returned again
    with ``cached`` set and no charge, unless ``refresh`` is true.

    Returns 403 without a valid Anthropic key, 422 when the screener holds nothing to pick
    from or the model declines, 429 beyond your AI limits, 502 when Claude's answer cannot
    be used, and 503 when you have no screener yet, the database is unavailable, or
    Anthropic rate limits your account.
    """
    if prepared.cached is not None:
        return prepared.cached
    assert prepared.api_key is not None and prepared.context is not None
    context = prepared.context
    try:
        async with anthropic_client(prepared.api_key) as client:
            if prepared.request.web_search:
                context = await add_web_research(
                    context, picks_research_task(context), client=client
                )
            report, model = await write_picks(context, client=client)
        return await _finish(prepared, context, Written(output=report, model=model))
    except AnalysisError as exc:
        raise ai_error(exc) from exc


@router.post(
    "/stream",
    response_class=EventSourceResponse,
    summary="Stream Claude's picks as Server-Sent Events",
)
async def stream_picks_events(prepared: Prepared) -> AsyncIterator[ServerSentEvent]:
    """Stream the picks of ``create_picks`` as Claude makes them.

    The same failures return the same statuses before the stream opens. Then it sends
    ``context`` (the :class:`PicksContext`), ``thinking`` and ``report`` fragments,
    ``search`` (a :class:`WebSearchQuery`) for each web search when ``web_search`` is on,
    ``fallback`` when a model declines part-way and another continues, and ``result`` (the
    :class:`PicksResponse`) or ``error``, as the stock analysis stream does. Reused picks
    are sent as ``context`` then ``result`` alone.
    """
    if prepared.cached is not None:
        yield ServerSentEvent(event="context", data=prepared.cached.context)
        yield ServerSentEvent(event="result", data=prepared.cached)
        return
    assert prepared.api_key is not None and prepared.context is not None
    context = prepared.context
    yield ServerSentEvent(event="context", data=context)
    try:
        async with anthropic_client(prepared.api_key) as client:
            if prepared.request.web_search:
                async for found in with_web_research(
                    context, picks_research_task(context), client=client
                ):
                    if isinstance(found, AnalysisDelta):
                        yield ServerSentEvent(event=found.channel, data=found)
                    elif isinstance(found, WebSearchQuery):
                        yield ServerSentEvent(event="search", data=found)
                    else:
                        context = found
            async for item in stream_picks(context, client=client):
                if isinstance(item, Written):
                    response = await _finish(prepared, context, item)
                    yield ServerSentEvent(event="result", data=response)
                elif isinstance(item, AnalysisDelta):
                    yield ServerSentEvent(event=item.channel, data=item)
                else:
                    yield ServerSentEvent(event="fallback", data=item)
    except AnalysisError as exc:
        error = ai_error(exc)
        yield ServerSentEvent(
            event="error",
            data=AnalysisStreamError(
                status=error.status_code,
                detail=str(error.detail),
                retry_after=exc.retry_after if isinstance(exc, AIRateLimitError) else None,
            ),
        )


@router.get("", response_model=PicksList, summary="Your stored lists of Claude's picks")
async def read_picks_list(
    session: Session, user: CurrentUser, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> PicksList:
    """Return your stored lists of picks, newest first, each with its tickers but not its
    reasons. Reading them costs nothing. Returns 503 when the database is unavailable."""
    try:
        return await list_picks(session, user.id, limit)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc


@router.get("/latest", response_model=PicksResponse, summary="Your latest Claude's picks")
async def read_latest_picks(session: Session, user: CurrentUser) -> PicksResponse:
    """Return your newest stored picks, whatever they were made for, so they can be shown
    again without a charge. Returns 404 when you have none, and 503 when the database is
    unavailable."""
    try:
        picks = await latest_picks(session, user.id)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc
    if picks is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No picks yet.")
    return picks.model_copy(update={"cached": True})


MAX_TRACKED_LISTS: int = 20
"""Most stored lists measured at once, to bound the prices one request asks for."""

Benchmark = Annotated[
    PicksBenchmark,
    Query(
        description="The fund the picks are compared with: SPY for the S&P 500, QQQ for "
        "the Nasdaq-100, DIA for the Dow, IWM for small caps, or VTI for the whole US market."
    ),
]


async def _closes(lists: list[PicksResponse], benchmark: str) -> Any:
    """Daily adjusted closes of every pick in ``lists`` and the benchmark, back to just
    before the oldest list, leaving out what cannot be priced.

    Raises:
        DataFetchError: If nothing at all can be priced.
    """
    today = date.today()
    oldest = min(start_day(picks.generated_at) for picks in lists)
    symbols = list(
        dict.fromkeys([pick.symbol for picks in lists for pick in picks.report.picks] + [benchmark])
    )
    panel = await fetch_close_panel(
        symbols, history_period(oldest, today), "1d", tolerate_failures=True
    )
    return panel.closes


@router.get(
    "/performance",
    response_model=SavedPicksList,
    summary="Your saved lists of picks, each with how it has done",
)
async def read_saved_picks(
    session: Session,
    user: CurrentUser,
    benchmark: Benchmark = "SPY",
    limit: Annotated[int, Query(ge=1, le=MAX_TRACKED_LISTS)] = MAX_TRACKED_LISTS,
) -> SavedPicksList:
    """Return your stored lists of picks, newest first, each with its gain or loss since it
    was made and the benchmark's over the same days.

    Each list is measured from the last close before it was made, held in Claude's
    weights without rebalancing, until its holding period ends; after that its record
    stays as it was then. Prices come from your keys, as on the Portfolio page, and from
    your stored price history. When nothing can be priced the lists are still returned,
    without returns, and ``notice`` says why. Returns 503 when the database is unavailable.
    """
    try:
        lists = await recent_picks(session, user.id, limit)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc
    notice = None
    performances: dict[str, PicksPerformance] = {}
    if lists:
        try:
            closes = await _closes(lists, benchmark)
            today = date.today()
            performances = {
                str(picks.id): track(picks, closes, benchmark, today) for picks in lists
            }
        except DataFetchError as exc:
            logger.info("Saved picks could not be priced: %s", exc)
            notice = (
                f"The picks could not be priced just now, so their returns are missing: {exc} "
                "A key that prices them, added in Settings, fills them in."
            )
    items = []
    for picks in lists:
        assert picks.id is not None, "stored picks have an ID"
        found = performances.get(str(picks.id))
        items.append(
            SavedPicks(
                id=picks.id,
                generated_at=picks.generated_at,
                horizon=picks.context.horizon,
                risk_tolerance=picks.context.profile.risk_tolerance,
                model=picks.model,
                headline=picks.report.headline,
                symbols=[pick.symbol for pick in picks.report.picks],
                ends_on=ends_on(picks),
                ended=date.today() > ends_on(picks),
                total_return=None if found is None else found.total_return,
                benchmark_return=None if found is None else found.benchmark_return,
                excess_return=None if found is None else found.excess_return,
            )
        )
    return SavedPicksList(
        benchmark=benchmark,
        benchmark_name=BENCHMARK_NAMES[benchmark],
        items=items,
        notice=notice,
    )


@router.get(
    "/{picks_id}/performance",
    response_model=PicksPerformance,
    summary="How a stored list of picks has done",
)
async def read_picks_performance(
    picks_id: UUID, session: Session, user: CurrentUser, benchmark: Benchmark = "SPY"
) -> PicksPerformance:
    """Return each pick's gain or loss since the list was made, the list's, and the
    benchmark's, with a point for each trading day, measured as for ``/performance``
    until the holding period ends.

    Returns 404 for unknown IDs and other accounts' picks, 403 without a key that prices
    them, 502 when they cannot be priced, and 503 when the database is unavailable.
    """
    try:
        picks = await get_picks(session, user.id, picks_id)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc
    if picks is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No picks with ID {picks_id}."
        )
    try:
        closes = await _closes([picks], benchmark)
    except DataFetchError as exc:
        raise upstream_error(exc) from exc
    return track(picks, closes, benchmark, date.today())


@router.post(
    "/prompt",
    response_model=PicksPromptExport,
    summary="The picks prompt, to paste into Claude outside the app",
)
async def export_picks_prompt(
    session: Session,
    user: CurrentUser,
    request: Annotated[PicksRequest, Body()] = PicksRequest(),
) -> PicksPromptExport:
    """Return exactly what a list of picks would send Claude, as one Markdown document.

    No Anthropic key is needed and nothing counts against your AI limits. The app makes no
    web search: with ``web_search``, the prompt tells Claude to search the web itself and
    name its sources, and without it, to keep to the data given. Paste ``prompt`` into
    Claude, then send its answer to ``/picks/import`` with the ``context`` returned here.
    Fails as ``/picks`` does when there is nothing to pick from.
    """
    try:
        context = await picks_context(session, user.id, request)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc
    return PicksPromptExport(
        prompt=render_prompt(
            PICKS_SYSTEM_PROMPT, picks_messages(context), PicksReport, request.web_search
        ),
        filename=f"pyqxel-claudes-picks-{context.horizon.replace(', ', '-').replace(' ', '-')}"
        "-prompt.md",
        context=context,
    )


@router.post(
    "/import",
    response_model=PicksResponse,
    summary="Save picks Claude made outside the app",
)
async def import_picks(
    session: Session, user: CurrentUser, body: Annotated[PicksImport, Body()]
) -> PicksResponse:
    """Check Claude's answer to an exported prompt and store it as your latest picks.

    They are marked as pasted and never reused in place of new picks. Returns 422 when the
    answer does not match the picks' format or holds too few usable picks, and 503 when
    the database is unavailable.
    """
    try:
        report = settle_picks(parse_response(body.response, PicksReport))
    except (ExternalResponseError, TooFewPicksError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    response = await _response(user.id, body.context, report, PASTED_MODEL)
    try:
        key = _request_key(body.request, await _universe(session, user.id))
        return await save_picks(session, user.id, key, response, PASTED_MODEL, PASTED_MODEL)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc


@router.get("/{picks_id}", response_model=PicksResponse, summary="A stored list of picks in full")
async def read_picks(picks_id: UUID, session: Session, user: CurrentUser) -> PicksResponse:
    """Return one of your stored lists of picks with the data it was made from.

    Returns 404 for unknown IDs and other accounts' picks, and 503 when the database is
    unavailable.
    """
    try:
        picks = await get_picks(session, user.id, picks_id)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc
    if picks is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No picks with ID {picks_id}."
        )
    return picks.model_copy(update={"cached": True})


@router.delete(
    "/{picks_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a stored list of picks"
)
async def remove_picks(picks_id: UUID, session: Session, user: CurrentUser) -> Response:
    """Delete one of your stored lists of picks.

    Returns 404 for unknown IDs and other accounts' picks, and 503 when the database is
    unavailable.
    """
    try:
        deleted = await delete_picks(session, user.id, picks_id)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No picks with ID {picks_id}."
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
