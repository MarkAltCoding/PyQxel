"""Claude's picks: the securities Claude would buy now and hold for a period you choose.

The picks come from the screener: its leaders on value, growth, quality, momentum, and
steadiness are the candidates, Claude picks among them for the holding period and profile
given, and each pick is stored with the screener's figures for it. The screener's own
table can then show the picks alone (``symbols`` of a screen), and a portfolio review is
given the latest picks as candidates for new investments.
"""

import hashlib
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import SecretStr
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import AIRateLimitError, AnalysisError, Written, anthropic_client
from app.ai.external import PASTED_MODEL, ExternalResponseError, parse_response, render_prompt
from app.ai.picks import picks_messages, picks_research_task, stream_picks, write_picks
from app.ai.prompts import PICKS_SYSTEM_PROMPT
from app.ai.web import add_web_research, with_web_research
from app.api.auth import CurrentUser
from app.api.v1.endpoints.markets import fetch_market_context
from app.api.v1.endpoints.research import ai_error, anthropic_key, check_ai_limits
from app.core.config import get_settings
from app.data.fetcher import DataFetchError
from app.db.picks import (
    delete_picks,
    find_recent_picks,
    get_picks,
    latest_picks,
    list_picks,
    save_picks,
)
from app.db.screener import (
    Universe,
    screen,
    shared_universe,
    shows_universe,
    universe_counts,
    universe_stocks,
    user_universe,
)
from app.db.session import get_session, get_sessionmaker
from app.models.external import PicksImport, PicksPromptExport
from app.models.markets import MarketContext
from app.models.picks import (
    MAX_PICKS,
    MIN_PICKS,
    Pick,
    PicksContext,
    PicksList,
    PicksReport,
    PicksRequest,
    PicksResponse,
    PicksUniverse,
)
from app.models.research import AnalysisDelta, AnalysisStreamError
from app.models.stock import SYMBOL_PATTERN
from app.models.web import WebSearchQuery
from app.stats.picks import lenses_for, merge_candidates

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


async def _market() -> tuple[MarketContext | None, str | None]:
    """The market backdrop, or a notice saying it is missing."""
    try:
        return await fetch_market_context(date.today()), None
    except DataFetchError as exc:
        logger.info("No market backdrop for Claude's picks: %s", exc)
        return None, "The market calendar and headlines could not be fetched."


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
    market, market_notice = await _market()
    notices = [
        market_notice,
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
        market=market,
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

    No Anthropic key is needed and nothing counts against your AI limits; the web is not
    searched. Paste ``prompt`` into Claude, then send its answer to ``/picks/import`` with
    the ``context`` returned here. Fails as ``/picks`` does when there is nothing to pick
    from.
    """
    try:
        context = await picks_context(session, user.id, request)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc
    return PicksPromptExport(
        prompt=render_prompt(PICKS_SYSTEM_PROMPT, picks_messages(context), PicksReport),
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
