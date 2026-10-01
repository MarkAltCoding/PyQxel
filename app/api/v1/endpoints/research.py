"""AI research routes: Claude-written investment theses and risk summaries.

Reports are grounded in price statistics and, where available, SEC filings. Each report
can be fetched whole, or streamed as Server-Sent Events while Claude writes it. Reports
are stored, and one for the same options is reused for a while instead of being paid for
again. Stored reports can be listed and read back under ``/analyses``.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.sse import EventSourceResponse, ServerSentEvent
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import (
    AINotConfiguredError,
    AIRateLimitError,
    AIRefusalError,
    AnalysisError,
    WrittenReport,
    stream_analysis,
    write_analysis,
)
from app.ai.cache import analysis_slot, cached_analysis, remember_analysis
from app.api.v1.endpoints.stocks import (
    PERIODS_PER_YEAR,
    Symbol,
    history_coverage,
    upstream_error,
)
from app.data.fetcher import (
    DataFetchError,
    SymbolNotFoundError,
    fetch_price_history,
    fetch_ticker_info,
)
from app.data.sec_edgar import (
    CompanyNotFoundError,
    EdgarNotConfiguredError,
    FilingFetchError,
    fetch_latest_filings,
)
from app.db.analyses import get_analysis, list_analyses
from app.db.session import get_session
from app.models.research import (
    AnalysisContext,
    AnalysisDelta,
    AnalysisKind,
    AnalysisList,
    AnalysisRequest,
    AnalysisResponse,
    AnalysisStreamError,
    Filing,
    InvestmentThesis,
    RiskSummary,
)
from app.models.stock import SYMBOL_PATTERN, TickerInfo
from app.stats.indicators import summarize_prices
from app.stats.volatility import InsufficientDataError

logger = logging.getLogger(__name__)

router = APIRouter()
"""Routes under ``/stocks`` that write analyses."""

results_router = APIRouter()
"""Routes under ``/analyses`` that read stored analyses."""


async def _no_filings() -> list[Filing]:
    """Stand in for the filings fetch when a request opts out of filings."""
    return []


def _filing_notices(symbol: str, result: list[Filing] | BaseException) -> list[str]:
    """Describe filings that could not be read, so the report can say what it lacks."""
    if isinstance(result, EdgarNotConfiguredError):
        return ["SEC filings are not configured, so none were read."]
    if isinstance(result, CompanyNotFoundError):
        return [f"{symbol} has no SEC filings, so none were read."]
    if isinstance(result, FilingFetchError):
        return ["SEC filings could not be fetched, so none were read."]
    if isinstance(result, BaseException):
        raise result
    notices: list[str] = []
    if not result:
        notices.append(f"{symbol} has no 10-K or 10-Q filings on SEC EDGAR.")
    for filing in result:
        if not filing.sections:
            notices.append(
                f"The sections of the {filing.form} filed {filing.filed} could not be "
                "located, so it was not read."
            )
        elif truncated := [section.title for section in filing.sections if section.truncated]:
            notices.append(
                f"In the {filing.form} filed {filing.filed}, {', '.join(truncated)} "
                "exceeded the length limit and were cut short."
            )
    return notices


def _ai_error(exc: AnalysisError) -> HTTPException:
    """Translate an AI failure into an HTTP error.

    Missing credentials and rate limits are 503s (with ``Retry-After`` when known),
    refusals are 422s, and other upstream failures are 502s.
    """
    if isinstance(exc, AIRateLimitError):
        headers = None if exc.retry_after is None else {"Retry-After": str(exc.retry_after)}
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc), headers=headers
        )
    if isinstance(exc, AINotConfiguredError):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, AIRefusalError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@dataclass(frozen=True)
class PreparedAnalysis:
    """Everything gathered for an analysis before Claude is asked to write it."""

    symbol: str
    request: AnalysisRequest
    context: AnalysisContext
    filings: list[Filing]
    cached: AnalysisResponse | None = None
    """A recent report for the same request, which makes asking Claude unnecessary."""

    def response(self, report: InvestmentThesis | RiskSummary, model: str) -> AnalysisResponse:
        """Wrap a finished report with the data it was written from."""
        return AnalysisResponse(
            symbol=self.symbol,
            kind=self.request.kind,
            report=report,
            context=self.context,
            model=model,
            generated_at=datetime.now(timezone.utc),
        )


async def prepare_analysis(
    symbol: Symbol,
    request: Annotated[AnalysisRequest, Body()] = AnalysisRequest(),
) -> PreparedAnalysis:
    """Fetch the snapshot, prices and filings an analysis is grounded in.

    Runs as a dependency, so a streamed analysis fails with an HTTP status before its
    stream opens: 404 for unknown symbols, 422 when the window has too few bars, and
    502 when the price provider fails. A missing snapshot or filings only adds a notice.

    When a recent report for the same request is cached, nothing is fetched and the
    report is returned with the context it was written from.
    """
    cached = await cached_analysis(symbol, request)
    if cached is not None:
        return PreparedAnalysis(
            symbol=cached.symbol,
            request=request,
            context=cached.context,
            filings=[],
            cached=cached,
        )

    info_result, history_result, filings_result = await asyncio.gather(
        fetch_ticker_info(symbol),
        fetch_price_history(symbol, period=request.period, interval="1d"),
        fetch_latest_filings(symbol) if request.include_filings else _no_filings(),
        return_exceptions=True,
    )
    if isinstance(history_result, DataFetchError):
        raise upstream_error(history_result) from history_result
    if isinstance(history_result, BaseException):
        raise history_result
    symbol = symbol.upper()

    notices: list[str] = []
    if isinstance(info_result, SymbolNotFoundError):
        raise upstream_error(info_result) from info_result
    if isinstance(info_result, DataFetchError):
        info_result = TickerInfo(symbol=symbol, source="unavailable")
        notices.append("The ticker snapshot could not be fetched; only prices are available.")
    elif isinstance(info_result, BaseException):
        raise info_result

    filings: list[Filing] = []
    if request.include_filings:
        notices.extend(_filing_notices(symbol, filings_result))
        if not isinstance(filings_result, BaseException):
            filings = [filing for filing in filings_result if filing.sections]

    coverage, coverage_notice = history_coverage(symbol, request.period, "1d", history_result)
    if coverage_notice:
        notices.insert(0, coverage_notice)
    try:
        prices = summarize_prices(history_result["Close"], PERIODS_PER_YEAR["1d"])
    except InsufficientDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    context = AnalysisContext(
        ticker=info_result,
        period=request.period,
        prices=prices,
        coverage=coverage,
        filings=[filing.reference() for filing in filings],
        notice=" ".join(notices) or None,
    )
    return PreparedAnalysis(symbol=symbol, request=request, context=context, filings=filings)


Prepared = Annotated[PreparedAnalysis, Depends(prepare_analysis)]


@router.post(
    "/{symbol}/analysis",
    response_model=AnalysisResponse,
    summary="AI-written investment thesis or risk summary",
)
async def create_analysis(prepared: Prepared) -> AnalysisResponse:
    """Have Claude write an investment thesis or risk summary for ``symbol``.

    The report is grounded in the ticker snapshot, return, volatility and drawdown
    statistics computed from daily adjusted closes over ``period``, and, unless
    ``include_filings`` is false, the Risk Factors and MD&A sections of the latest 10-K
    and any later 10-Q from SEC EDGAR. All of it is described in ``context``. When the
    snapshot or filings cannot be fetched the report is written without them and
    ``context.notice`` says so.

    Every report is stored. A report written for the same options, model and effort
    within ``ANALYSIS_CACHE_TTL_SECONDS`` (24 hours by default) is returned again with
    ``cached`` set and no charge, unless ``refresh`` is true. Identical requests that
    arrive together are written once.

    Returns 404 for unknown symbols, 422 when the window has too few bars or the model
    declines, 502 when a provider fails, and 503 when the AI service is unconfigured or
    rate limited.
    """
    if prepared.cached is not None:
        return prepared.cached
    async with analysis_slot(prepared.symbol, prepared.request):
        cached = await cached_analysis(prepared.symbol, prepared.request)
        if cached is not None:
            return cached
        try:
            report, model = await write_analysis(
                prepared.context, prepared.request.kind, prepared.filings
            )
        except AnalysisError as exc:
            raise _ai_error(exc) from exc
        return await remember_analysis(prepared.request, prepared.response(report, model))


@router.post(
    "/{symbol}/analysis/stream",
    response_class=EventSourceResponse,
    summary="Stream an AI-written thesis or risk summary as Server-Sent Events",
)
async def stream_analysis_events(prepared: Prepared) -> AsyncIterator[ServerSentEvent]:
    """Stream the analysis of ``create_analysis`` as Claude writes it.

    Data is gathered first, so unknown symbols, short windows and provider failures
    return the same 404, 422 and 502 statuses as the non-streaming route. Once the
    stream opens, it sends these events, each with a JSON ``data`` payload:

    * ``context``: the :class:`AnalysisContext` the report is written from.
    * ``thinking``: an :class:`AnalysisDelta` summarizing the model's reasoning.
    * ``report``: an :class:`AnalysisDelta` of report JSON; fragments concatenate to it.
    * ``fallback``: a :class:`ModelFallback`, when a model declines part-way and another
      continues the report.
    * ``result``: the final :class:`AnalysisResponse`, with the validated report.
    * ``error``: an :class:`AnalysisStreamError` giving the status the non-streaming
      route would have returned. Fragments already sent should then be discarded.

    The stream ends after ``result`` or ``error``. Idle periods carry keep-alive comments.
    A reused report, as described for ``create_analysis``, is sent as ``context`` then
    ``result`` alone.
    """
    async with analysis_slot(prepared.symbol, prepared.request):
        cached = prepared.cached or await cached_analysis(prepared.symbol, prepared.request)
        if cached is not None:
            yield ServerSentEvent(event="context", data=cached.context)
            yield ServerSentEvent(event="result", data=cached)
            return
        yield ServerSentEvent(event="context", data=prepared.context)
        try:
            async for item in stream_analysis(
                prepared.context, prepared.request.kind, prepared.filings
            ):
                if isinstance(item, WrittenReport):
                    response = await remember_analysis(
                        prepared.request, prepared.response(item.report, item.model)
                    )
                    yield ServerSentEvent(event="result", data=response)
                elif isinstance(item, AnalysisDelta):
                    yield ServerSentEvent(event=item.channel, data=item)
                else:
                    yield ServerSentEvent(event="fallback", data=item)
        except AnalysisError as exc:
            error = _ai_error(exc)
            yield ServerSentEvent(
                event="error",
                data=AnalysisStreamError(
                    status=error.status_code,
                    detail=str(error.detail),
                    retry_after=exc.retry_after if isinstance(exc, AIRateLimitError) else None,
                ),
            )


Session = Annotated[AsyncSession, Depends(get_session)]


def _database_error(exc: SQLAlchemyError) -> HTTPException:
    """Log a database failure and translate it into a 503."""
    logger.error("Analysis database request failed: %s", exc)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="The analysis database is unavailable.",
    )


@results_router.get("", response_model=AnalysisList, summary="List stored analyses")
async def read_analyses(
    session: Session,
    symbol: Annotated[
        str | None, Query(pattern=SYMBOL_PATTERN, description="Only this symbol's reports.")
    ] = None,
    kind: Annotated[AnalysisKind | None, Query(description="Only reports of this kind.")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AnalysisList:
    """Return stored analyses' headlines, newest first. Reading them costs nothing.

    Returns 503 when the database is unavailable.
    """
    try:
        return await list_analyses(session, symbol, kind, limit, offset)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc


@results_router.get(
    "/{analysis_id}", response_model=AnalysisResponse, summary="A stored analysis in full"
)
async def read_analysis(analysis_id: UUID, session: Session) -> AnalysisResponse:
    """Return a stored analysis with the data it was written from.

    Returns 404 for unknown IDs and 503 when the database is unavailable.
    """
    try:
        result = await get_analysis(session, analysis_id)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No analysis with ID {analysis_id}."
        )
    return result
