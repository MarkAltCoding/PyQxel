"""AI research routes: Claude-written investment theses and risk summaries.

Reports are grounded in price statistics and, where available, SEC filings.
"""

import asyncio
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, status

from app.ai.agent import (
    AINotConfiguredError,
    AIRateLimitError,
    AIRefusalError,
    AnalysisError,
    write_analysis,
)
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
from app.models.research import AnalysisContext, AnalysisRequest, AnalysisResponse, Filing
from app.models.stock import TickerInfo
from app.stats.indicators import summarize_prices
from app.stats.volatility import InsufficientDataError

router = APIRouter()


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


@router.post(
    "/{symbol}/analysis",
    response_model=AnalysisResponse,
    summary="AI-written investment thesis or risk summary",
)
async def create_analysis(
    symbol: Symbol,
    request: Annotated[AnalysisRequest, Body()] = AnalysisRequest(),
) -> AnalysisResponse:
    """Have Claude write an investment thesis or risk summary for ``symbol``.

    The report is grounded in the ticker snapshot, return, volatility and drawdown
    statistics computed from daily adjusted closes over ``period``, and, unless
    ``include_filings`` is false, the Risk Factors and MD&A sections of the latest 10-K
    and any later 10-Q from SEC EDGAR. All of it is described in ``context``. When the
    snapshot or filings cannot be fetched the report is written without them and
    ``context.notice`` says so.

    Returns 404 for unknown symbols, 422 when the window has too few bars or the model
    declines, 502 when a provider fails, and 503 when the AI service is unconfigured or
    rate limited.
    """
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
    try:
        report, model = await write_analysis(context, request.kind, filings)
    except AnalysisError as exc:
        raise _ai_error(exc) from exc
    return AnalysisResponse(
        symbol=symbol,
        kind=request.kind,
        report=report,
        context=context,
        model=model,
        generated_at=datetime.now(timezone.utc),
    )
