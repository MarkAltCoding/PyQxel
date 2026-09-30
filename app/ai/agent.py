"""Claude-written investment theses and risk summaries grounded in fetched market data.

Claude sees only the :class:`~app.models.research.AnalysisContext` and SEC filing
sections it is given, and returns a report through structured outputs, so the response
always validates against :class:`~app.models.research.InvestmentThesis` or
:class:`~app.models.research.RiskSummary`.
"""

import logging
from functools import lru_cache

import anthropic
from anthropic import AsyncAnthropic
from anthropic.types.beta import BetaTextBlockParam
from pydantic import ValidationError

from app.ai.prompts import SYSTEM_PROMPT, TASKS, render_filings
from app.core.config import get_settings
from app.models.research import (
    AnalysisContext,
    AnalysisKind,
    Filing,
    InvestmentThesis,
    RiskSummary,
)

logger = logging.getLogger(__name__)

MAX_TOKENS: int = 16000
"""Output ceiling, covering adaptive thinking plus the report."""

FALLBACK_BETA: str = "server-side-fallback-2026-07-01"
"""Beta enabling ``fallbacks="default"``: a declined request is re-run on another model."""


class AnalysisError(RuntimeError):
    """Raised when a report cannot be written."""


class AINotConfiguredError(AnalysisError):
    """Raised when no valid Anthropic credentials are available."""


class AIRateLimitError(AnalysisError):
    """Raised when the Anthropic API is still rate limiting after the SDK's retries."""

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class AIUnavailableError(AnalysisError):
    """Raised on network failures, timeouts or Anthropic server errors."""


class AIRefusalError(AnalysisError):
    """Raised when every model declines the request."""


@lru_cache
def get_anthropic_client() -> AsyncAnthropic:
    """Return the shared :class:`AsyncAnthropic` client, created on first use.

    Uses ``ANTHROPIC_API_KEY`` from the settings when set; otherwise the SDK resolves
    credentials itself (environment variables or an ``ant auth login`` profile). The SDK
    retries connection errors, 429s and 5xx responses with backoff.
    """
    settings = get_settings()
    api_key = settings.anthropic_api_key
    return AsyncAnthropic(
        api_key=api_key.get_secret_value() if api_key is not None else None,
        timeout=settings.anthropic_timeout_seconds,
    )


async def close_anthropic_client() -> None:
    """Close the shared client's connection pool if it was created."""
    if get_anthropic_client.cache_info().currsize:
        await get_anthropic_client().close()
        get_anthropic_client.cache_clear()


def _retry_after(error: anthropic.RateLimitError) -> int | None:
    """Return the ``retry-after`` header of ``error`` in whole seconds, if present."""
    value = error.response.headers.get("retry-after")
    try:
        return max(1, round(float(value))) if value is not None else None
    except ValueError:
        return None


def _user_content(
    context: AnalysisContext, kind: AnalysisKind, filings: list[Filing]
) -> str | list[BetaTextBlockParam]:
    """Build the user turn: filings first, then the task and snapshot.

    Filings are the bulk of the input and identical across requests about the same
    company, so they lead and end in a cache breakpoint.
    """
    request = (
        f"{TASKS[kind]}\n\n<snapshot>\n{context.model_dump_json(indent=2)}\n</snapshot>"
    )
    if not any(filing.sections for filing in filings):
        return request
    return [
        {
            "type": "text",
            "text": render_filings(filings),
            "cache_control": {"type": "ephemeral"},
        },
        {"type": "text", "text": request},
    ]


async def write_analysis(
    context: AnalysisContext,
    kind: AnalysisKind,
    filings: list[Filing] | None = None,
    client: AsyncAnthropic | None = None,
) -> tuple[InvestmentThesis | RiskSummary, str]:
    """Have Claude write a ``kind`` report from ``context``.

    Args:
        context: Market data the report must be grounded in.
        kind: ``"thesis"`` or ``"risk"``.
        filings: SEC filings whose sections are given to Claude alongside ``context``.
        client: Client to use; the shared client from
            :func:`get_anthropic_client` when omitted.

    Returns:
        The validated report and the ID of the model that wrote it, which differs from
        the configured model when a declined request fell back to another model.

    Raises:
        AINotConfiguredError: If credentials are missing or rejected.
        AIRateLimitError: If rate limits persist after retries.
        AIUnavailableError: If the API cannot be reached or fails.
        AIRefusalError: If the request is declined.
        AnalysisError: If the response is truncated or does not match the schema.
    """
    settings = get_settings()
    schema = InvestmentThesis if kind == "thesis" else RiskSummary
    try:
        client = client or get_anthropic_client()
        response = await client.beta.messages.parse(
            model=settings.anthropic_model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _user_content(context, kind, filings or [])}],
            thinking={"type": "adaptive"},
            output_config={"effort": settings.anthropic_effort},
            output_format=schema,
            fallbacks="default",
            betas=[FALLBACK_BETA],
        )
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
        raise AINotConfiguredError("Anthropic credentials are missing or invalid.") from exc
    except anthropic.RateLimitError as exc:
        raise AIRateLimitError(
            "The AI service is rate limited; try again shortly.", _retry_after(exc)
        ) from exc
    except anthropic.APIStatusError as exc:
        if exc.status_code >= 500:
            raise AIUnavailableError(
                f"The AI service returned an error ({exc.status_code})."
            ) from exc
        logger.error("Anthropic rejected the analysis request: %s", exc.message)
        raise AnalysisError("The AI service rejected the request.") from exc
    except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
        raise AIUnavailableError("Could not reach the AI service.") from exc
    except ValidationError as exc:
        raise AnalysisError("The AI response did not match the report schema.") from exc
    except anthropic.AnthropicError as exc:
        # Raised before any request when the SDK finds no credentials to use.
        raise AINotConfiguredError(str(exc)) from exc

    if response.stop_reason == "refusal":
        category = response.stop_details.category if response.stop_details else None
        raise AIRefusalError(
            f"The AI model declined to write this report (category: {category or 'unspecified'})."
        )
    if response.stop_reason == "max_tokens":
        raise AnalysisError("The AI response was truncated before the report was complete.")
    report = response.parsed_output
    if report is None:
        raise AnalysisError("The AI response did not contain a valid report.")
    return report, response.model
