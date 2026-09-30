"""Tests for the Claude research agent. A fake client stands in for the Anthropic API."""

from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast

import anthropic
import httpx2
import pytest
from anthropic import AsyncAnthropic

from app.ai import agent
from app.ai.agent import (
    FALLBACK_BETA,
    AINotConfiguredError,
    AIRateLimitError,
    AIRefusalError,
    AIUnavailableError,
    AnalysisError,
    write_analysis,
)
from app.core.config import get_settings
from app.models.research import (
    AnalysisContext,
    AnalysisKind,
    InvestmentThesis,
    PriceSummary,
    RiskSummary,
)
from app.models.stock import TickerInfo

pytestmark = pytest.mark.asyncio

REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")

THESIS = InvestmentThesis(
    headline="Momentum with elevated volatility.",
    stance="bullish",
    conviction="medium",
    summary="Prices rose 25% over the year.",
    supporting_points=["25% period return."],
    counterpoints=["30% realized volatility."],
    what_would_change_the_view=["A drawdown beyond 20%."],
    data_limitations=["No fundamentals."],
)


def _context() -> AnalysisContext:
    """Build a minimal analysis context."""
    return AnalysisContext(
        ticker=TickerInfo(symbol="AAPL", name="Apple Inc.", source="yfinance"),
        period="1y",
        prices=PriceSummary(
            start=datetime(2024, 1, 2),
            end=datetime(2024, 12, 31),
            observations=250,
            first_close=100.0,
            last_close=125.0,
            high=130.0,
            low=95.0,
            period_return=0.25,
            realized_volatility=0.3,
            max_drawdown=-0.15,
            current_drawdown=-0.04,
            best_return=0.05,
            worst_return=-0.06,
        ),
        coverage="full",
    )


def _response(**overrides: Any) -> SimpleNamespace:
    """Build a parsed response with a thesis, overriding any field."""
    fields: dict[str, Any] = {
        "stop_reason": "end_turn",
        "stop_details": None,
        "parsed_output": THESIS,
        "model": "claude-opus-5-5",
    }
    return SimpleNamespace(**(fields | overrides))


class FakeClient:
    """Records ``beta.messages.parse`` calls and returns or raises a fixed result."""

    def __init__(self, result: SimpleNamespace | Exception) -> None:
        self.calls: list[dict[str, Any]] = []
        self._result = result
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    async def _parse(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


async def _write(
    client: FakeClient, kind: AnalysisKind
) -> tuple[InvestmentThesis | RiskSummary, str]:
    """Run :func:`write_analysis` on the standard context with ``client``."""
    return await write_analysis(_context(), kind, client=cast(AsyncAnthropic, client))


async def test_thesis_request_is_grounded_and_structured() -> None:
    """The request carries the snapshot, the thesis schema, effort and fallbacks."""
    client = FakeClient(_response())

    report, model = await _write(client, "thesis")

    assert report == THESIS
    assert model == "claude-opus-5-5"
    settings = get_settings()
    call = client.calls[0]
    assert call["model"] == settings.anthropic_model
    assert call["output_format"] is InvestmentThesis
    assert call["thinking"] == {"type": "adaptive"}
    assert call["output_config"] == {"effort": settings.anthropic_effort}
    assert call["fallbacks"] == "default"
    assert call["betas"] == [FALLBACK_BETA]
    content = call["messages"][0]["content"]
    assert content.startswith(agent.TASKS["thesis"])
    assert '"symbol": "AAPL"' in content
    assert '"period_return": 0.25' in content


async def test_risk_request_uses_risk_schema() -> None:
    """Risk summaries are parsed against the risk schema."""
    client = FakeClient(_response())

    await _write(client, "risk")

    assert client.calls[0]["output_format"] is RiskSummary


async def test_returns_fallback_model() -> None:
    """When a fallback model serves the request, its ID is returned."""
    client = FakeClient(_response(model="claude-opus-4-8"))

    _, model = await _write(client, "thesis")

    assert model == "claude-opus-4-8"


async def test_refusal_raises() -> None:
    """A refusal after fallbacks is reported with its category."""
    client = FakeClient(
        _response(
            stop_reason="refusal",
            stop_details=SimpleNamespace(category="general_harms"),
            parsed_output=None,
        )
    )

    with pytest.raises(AIRefusalError, match="general_harms"):
        await _write(client, "thesis")


async def test_truncated_response_raises() -> None:
    """A response cut off at ``max_tokens`` is not returned as a report."""
    client = FakeClient(_response(stop_reason="max_tokens", parsed_output=None))

    with pytest.raises(AnalysisError, match="truncated"):
        await _write(client, "thesis")


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            anthropic.AuthenticationError(
                "bad key", response=httpx2.Response(401, request=REQUEST), body=None
            ),
            AINotConfiguredError,
        ),
        (
            anthropic.InternalServerError(
                "overloaded", response=httpx2.Response(529, request=REQUEST), body=None
            ),
            AIUnavailableError,
        ),
        (anthropic.APIConnectionError(request=REQUEST), AIUnavailableError),
        (anthropic.APITimeoutError(request=REQUEST), AIUnavailableError),
        (
            anthropic.BadRequestError(
                "invalid", response=httpx2.Response(400, request=REQUEST), body=None
            ),
            AnalysisError,
        ),
    ],
)
async def test_api_errors_are_translated(error: Exception, expected: type[Exception]) -> None:
    """SDK errors become analysis errors the API layer can map to status codes."""
    with pytest.raises(expected):
        await _write(FakeClient(error), "thesis")


async def test_rate_limit_carries_retry_after() -> None:
    """Rate limits keep the server's ``retry-after`` hint."""
    error = anthropic.RateLimitError(
        "slow down",
        response=httpx2.Response(429, headers={"retry-after": "12"}, request=REQUEST),
        body=None,
    )

    with pytest.raises(AIRateLimitError) as caught:
        await _write(FakeClient(error), "thesis")

    assert caught.value.retry_after == 12
