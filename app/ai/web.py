"""Web research for Claude's reports: what a search finds, each fact with its sources.

A report asked for with ``web_search`` is written in two requests. The first, here, lets
Claude search the web (Anthropic's web search tool, billed per search to the user's key)
and state what it found. The API ties each statement drawn from a page to that page, and
only those statements are kept, as numbered findings, so nothing reaches the report, or the
reader, without a source. The second request writes the report as usual, with the findings
in its snapshot to cite by number.

Structured outputs cannot carry the search's citations, which is why the search is a
request of its own rather than a tool of the request that writes the report.
"""

import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from pydantic import BaseModel

from app.ai.agent import FALLBACK_BETA, AnalysisError, _translated_errors
from app.ai.prompts import WEB_RESEARCH_SYSTEM_PROMPT
from app.core.config import get_settings
from app.models.research import AnalysisDelta
from app.models.web import (
    MAX_FINDING_LENGTH,
    MAX_FINDINGS,
    WebFinding,
    WebResearch,
    WebSearchQuery,
    WebSource,
)

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic
    from anthropic.types.beta import BetaMessage, BetaMessageParam

logger = logging.getLogger(__name__)

WEB_SEARCH_TOOL: str = "web_search_20250305"
"""The web search tool that calls the search directly, so every statement comes back tied
to the pages it rests on, and that every Claude model accepts."""

RESEARCH_MAX_TOKENS: int = 16000
"""Output ceiling of the search request: its reasoning and a short briefing."""

MAX_RESUMES: int = 3
"""Most times a search the API paused (``pause_turn``) is sent back to continue."""


def _web_url(url: str | None) -> str | None:
    """``url`` when it is an ordinary web address, else ``None``; a finding is shown as a
    link, so nothing else may pass."""
    if not url:
        return None
    parts = urlsplit(url)
    return url if parts.scheme in ("http", "https") and parts.netloc else None


def findings_in(response: BetaMessage, start: int = 1) -> list[WebFinding]:
    """The statements of ``response`` the search tied to a page, numbered from ``start``.

    Text without a citation, such as a sentence joining two findings, is dropped: it has
    no source to show.
    """
    findings: list[WebFinding] = []
    for block in response.content:
        if block.type != "text" or not block.citations:
            continue
        sources: dict[str, WebSource] = {}
        for citation in block.citations:
            url = _web_url(getattr(citation, "url", None))
            if citation.type == "web_search_result_location" and url and url not in sources:
                sources[url] = WebSource(
                    url=url, title=citation.title or None, cited_text=citation.cited_text or None
                )
        text = " ".join(block.text.split())
        if sources and text:
            findings.append(
                WebFinding(
                    id=start + len(findings),
                    text=text[:MAX_FINDING_LENGTH],
                    sources=list(sources.values()),
                )
            )
    return findings


def _searches(response: BetaMessage) -> int:
    """How many searches ``response`` was billed for."""
    used = getattr(response.usage, "server_tool_use", None)
    return int(getattr(used, "web_search_requests", 0) or 0)


async def stream_web_research(
    task: str, *, client: AsyncAnthropic
) -> AsyncIterator[AnalysisDelta | WebSearchQuery | WebResearch]:
    """Have Claude search the web for ``task``, yielding its summarized reasoning as
    ``thinking`` fragments, each search as it is made, and finally the
    :class:`~app.models.web.WebResearch` it found.

    Raises:
        AnalysisError: As :func:`app.ai.agent.write_analysis` does, when the request fails
            or the model declines to search.
    """
    settings = get_settings()
    messages: list[BetaMessageParam] = [{"role": "user", "content": task}]
    tool: dict[str, Any] = {
        "type": WEB_SEARCH_TOOL,
        "name": "web_search",
        "max_uses": settings.web_search_max_uses,
    }
    findings: list[WebFinding] = []
    queries: list[str] = []
    searches = 0
    for _ in range(MAX_RESUMES + 1):
        with _translated_errors():
            async with client.beta.messages.stream(
                model=settings.anthropic_model,
                max_tokens=RESEARCH_MAX_TOKENS,
                system=WEB_RESEARCH_SYSTEM_PROMPT,
                messages=messages,
                tools=[tool],  # type: ignore[list-item]
                thinking={"type": "adaptive", "display": "summarized"},
                output_config={"effort": settings.anthropic_effort},
                fallbacks="default",
                betas=[FALLBACK_BETA],
            ) as stream:
                async for event in stream:
                    if event.type == "content_block_delta":
                        if event.delta.type == "thinking_delta":
                            yield AnalysisDelta(channel="thinking", text=event.delta.thinking)
                    elif event.type == "content_block_stop":
                        block = getattr(event, "content_block", None)
                        if block is not None and block.type == "server_tool_use":
                            query = (block.input or {}).get("query")
                            if isinstance(query, str) and query.strip():
                                queries.append(query.strip())
                                yield WebSearchQuery(query=query.strip())
                response = await stream.get_final_message()
        if response.stop_reason == "refusal":
            raise AnalysisError("The AI model declined to search the web for this report.")
        findings += findings_in(response, start=len(findings) + 1)
        searches += _searches(response)
        if response.stop_reason != "pause_turn":
            break
        # A long search is paused by the API; sending its turn back unchanged resumes it.
        messages = [messages[0], {"role": "assistant", "content": response.content}]
    yield WebResearch(
        searched_at=datetime.now(timezone.utc),
        queries=queries,
        searches=max(searches, len(queries)),
        findings=findings[:MAX_FINDINGS],
    )


WEB_FAILED_NOTICE: str = (
    "The web could not be searched just now, so this was written from PyQxel's own data alone."
)
"""Added to a report's notice when its web search fails; the report is still written."""


async def web_research_or_none(
    task: str, *, client: AsyncAnthropic
) -> AsyncIterator[AnalysisDelta | WebSearchQuery | WebResearch | None]:
    """:func:`stream_web_research`, ending in ``None`` in place of raising when the search
    fails for a reason the report can go on without, such as web search being turned off
    for the Anthropic account. A rejected key or a rate limit is still raised, since the
    report would fail on it too.
    """
    from app.ai.agent import AINotConfiguredError, AIRateLimitError

    try:
        async for item in stream_web_research(task, client=client):
            yield item
    except AINotConfiguredError, AIRateLimitError:
        raise
    except AnalysisError as exc:
        logger.warning("The web search for a report failed: %s", exc)
        yield None


async def with_web_research[C: BaseModel](
    context: C, task: str, *, client: AsyncAnthropic
) -> AsyncIterator[AnalysisDelta | WebSearchQuery | C]:
    """Search the web for ``task`` and yield, after the search's progress, ``context`` with
    what was found as its ``web_research``, which the report is then written from.

    When the search fails for a reason the report can go on without, the context comes
    back without findings and with :data:`WEB_FAILED_NOTICE` added to its ``notice``.
    ``context`` is any of the report contexts, which all have both fields.
    """
    async for item in web_research_or_none(task, client=client):
        if isinstance(item, WebResearch):
            yield context.model_copy(update={"web_research": item})
        elif item is None:
            notice = getattr(context, "notice", None)
            yield context.model_copy(
                update={"notice": f"{notice} {WEB_FAILED_NOTICE}" if notice else WEB_FAILED_NOTICE}
            )
        else:
            yield item


async def add_web_research[C: BaseModel](context: C, task: str, *, client: AsyncAnthropic) -> C:
    """:func:`with_web_research` without its progress: ``context`` with the findings."""
    async for item in with_web_research(context, task, client=client):
        if not isinstance(item, (AnalysisDelta, WebSearchQuery)):
            context = item
    return context
