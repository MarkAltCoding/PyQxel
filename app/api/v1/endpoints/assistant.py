"""The coding assistant for custom backtest strategies.

Claude answers questions about the strategy in the editor and can return a revised
version of it. The conversation stays in the browser and is sent with each question;
the server keeps only a count of answers, against the account's AI limits.
"""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Body, Depends
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import SecretStr
from sqlalchemy.exc import SQLAlchemyError

from app.ai.agent import AIRateLimitError, AnalysisError, Written, anthropic_client
from app.ai.assistant import stream_assistant_reply, write_assistant_reply
from app.api.auth import CurrentUser
from app.api.v1.endpoints.research import ai_error, anthropic_key, check_ai_limits
from app.db.portfolio_analyses import record_assistant_request
from app.db.session import get_sessionmaker
from app.models.assistant import AssistantRequest, AssistantResponse
from app.models.research import AnalysisDelta, AnalysisStreamError

logger = logging.getLogger(__name__)

router = APIRouter()


@dataclass(frozen=True)
class PreparedQuestion:
    """A question that may be put to Claude, and the key it is billed to."""

    user_id: str
    request: AssistantRequest
    api_key: SecretStr


async def prepare_question(
    user: CurrentUser, request: Annotated[AssistantRequest, Body()]
) -> PreparedQuestion:
    """Check the key and the AI limits before any answer is written, so a streamed answer
    fails with an HTTP status before its stream opens."""
    api_key = anthropic_key()
    await check_ai_limits(user)
    return PreparedQuestion(user_id=user.id, request=request, api_key=api_key)


Prepared = Annotated[PreparedQuestion, Depends(prepare_question)]


async def _count(user_id: str, model: str) -> None:
    """Count an answer against the AI limits; a database failure only loses the count."""
    try:
        async with get_sessionmaker()() as session:
            await record_assistant_request(session, user_id, model)
    except SQLAlchemyError as exc:
        logger.error("Could not count an assistant answer: %s", exc)


@router.post("", response_model=AssistantResponse, summary="Ask Claude about your strategy")
async def ask_assistant(prepared: Prepared) -> AssistantResponse:
    """Have Claude answer a question about the custom strategy in ``code``.

    Send the code in the editor, its ``language``, the question, the conversation so far
    in ``history``, and ``last_run`` with the error or output of the last run when there
    is one. ``reply.code`` is the complete revised source when Claude changed the code,
    and null otherwise. Nothing is stored but a count of answers.

    It is written with, and billed to, your own Anthropic key (the ``X-Anthropic-Key``
    header), within your AI limits. Returns 403 without a valid Anthropic key, 422 when
    the model declines, 429 beyond your AI limits, 502 when Anthropic fails, and 503
    when it rate limits your account.
    """
    try:
        async with anthropic_client(prepared.api_key) as client:
            reply, model = await write_assistant_reply(prepared.request, client=client)
    except AnalysisError as exc:
        raise ai_error(exc) from exc
    await _count(prepared.user_id, model)
    return AssistantResponse(reply=reply, model=model)


@router.post(
    "/stream",
    response_class=EventSourceResponse,
    summary="Stream Claude's answer about your strategy as Server-Sent Events",
)
async def stream_assistant(prepared: Prepared) -> AsyncIterator[ServerSentEvent]:
    """Stream the answer of ``ask_assistant`` as Claude writes it.

    Sends ``thinking`` fragments summarizing the model's reasoning, ``report`` fragments
    of the reply's JSON, ``fallback`` when a model declines part-way and another
    continues, then ``result`` (the :class:`AssistantResponse`) or ``error`` (an
    :class:`AnalysisStreamError`). Failures before the stream opens return the same
    statuses as ``ask_assistant``.
    """
    try:
        async with anthropic_client(prepared.api_key) as client:
            async for item in stream_assistant_reply(prepared.request, client=client):
                if isinstance(item, Written):
                    await _count(prepared.user_id, item.model)
                    yield ServerSentEvent(
                        event="result",
                        data=AssistantResponse(reply=item.output, model=item.model),
                    )
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
