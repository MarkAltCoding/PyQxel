"""Tests for the Claude research agent. A fake client stands in for the Anthropic API."""

from datetime import date, datetime
from types import SimpleNamespace
from typing import Any, cast

import anthropic
import httpx2
import pytest
from anthropic import AsyncAnthropic
from pydantic import SecretStr

from app.ai import agent
from app.ai.agent import (
    FALLBACK_BETA,
    AINotConfiguredError,
    AIRateLimitError,
    AIRefusalError,
    AIUnavailableError,
    AnalysisError,
    WrittenReport,
    stream_analysis,
    write_analysis,
)
from app.ai.prompts import TASKS
from app.core.config import Settings, get_settings
from app.models.research import (
    AnalysisContext,
    AnalysisDelta,
    AnalysisKind,
    Filing,
    FilingSection,
    InvestmentThesis,
    ModelFallback,
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


def _response(texts: list[str] | None = None, **overrides: Any) -> SimpleNamespace:
    """Build a response with one text block per entry of ``texts`` (default: the thesis)."""
    fields: dict[str, Any] = {
        "stop_reason": "end_turn",
        "stop_details": None,
        "content": [
            SimpleNamespace(type="text", text=text)
            for text in (texts if texts is not None else [THESIS.model_dump_json()])
        ],
        "model": "claude-opus-5-5",
    }
    return SimpleNamespace(**(fields | overrides))


class FakeClient:
    """Records ``beta.messages.create`` calls and returns or raises a fixed result."""

    def __init__(self, result: SimpleNamespace | Exception) -> None:
        self.calls: list[dict[str, Any]] = []
        self._result = result
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs: Any) -> SimpleNamespace:
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
    assert call["thinking"] == {"type": "adaptive"}
    assert call["output_config"]["effort"] == settings.anthropic_effort
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "headline" in call["output_config"]["format"]["schema"]["properties"]
    assert call["fallbacks"] == "default"
    assert call["betas"] == [FALLBACK_BETA]
    content = call["messages"][0]["content"]
    assert content.startswith(TASKS["thesis"])
    assert '"symbol": "AAPL"' in content
    assert '"period_return": 0.25' in content


async def test_risk_request_uses_risk_schema() -> None:
    """Risk summaries are constrained to and validated against the risk schema."""
    client = FakeClient(_response())

    with pytest.raises(AnalysisError, match="schema"):
        await _write(client, "risk")
    schema = client.calls[0]["output_config"]["format"]["schema"]
    assert "key_risks" in schema["properties"]


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
            texts=[],
        )
    )

    with pytest.raises(AIRefusalError, match="general_harms"):
        await _write(client, "thesis")


async def test_truncated_response_raises() -> None:
    """A response cut off at ``max_tokens`` is not returned as a report."""
    client = FakeClient(_response(["{"], stop_reason="max_tokens"))

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


def _filing(sections: list[FilingSection]) -> Filing:
    """Build a 10-K with ``sections``."""
    return Filing(
        form="10-K",
        accession_number="0000320193-24-000123",
        filed=date(2024, 11, 1),
        period_of_report=date(2024, 9, 28),
        url="https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl.htm",
        sections=sections,
    )


async def test_filings_lead_the_prompt_in_a_cached_block() -> None:
    """Filing text comes first, tagged and cacheable, then the task and snapshot."""
    client = FakeClient(_response())
    filing = _filing(
        [
            FilingSection(title="Item 1A. Risk Factors", text="Supply risk.", truncated=False),
            FilingSection(title="Item 7. MD&A", text="Revenue grew.", truncated=True),
        ]
    )

    await write_analysis(_context(), "thesis", [filing], client=cast(AsyncAnthropic, client))

    filings_block, request_block = client.calls[0]["messages"][0]["content"]
    assert filings_block["cache_control"] == {"type": "ephemeral"}
    text = filings_block["text"]
    assert text.startswith("<filings>") and text.endswith("</filings>")
    assert '<filing form="10-K" filed="2024-11-01" period="2024-09-28">' in text
    assert '<section title="Item 1A. Risk Factors">\nSupply risk.\n</section>' in text
    assert '<section title="Item 7. MD&A" truncated="true">' in text
    assert request_block["text"].startswith(TASKS["thesis"])
    assert "<snapshot>" in request_block["text"]


async def test_filings_without_sections_are_left_out() -> None:
    """A request whose filings have no text sends only the task and snapshot."""
    client = FakeClient(_response())

    await write_analysis(_context(), "thesis", [_filing([])], client=cast(AsyncAnthropic, client))

    content = client.calls[0]["messages"][0]["content"]
    assert isinstance(content, str) and content.startswith(TASKS["thesis"])


async def test_client_bills_only_the_key_it_is_given(monkeypatch: pytest.MonkeyPatch) -> None:
    """A client uses the requesting user's key, never the server's or the environment's."""
    settings = Settings(anthropic_api_key=SecretStr("sk-server"), anthropic_timeout_seconds=42.0)
    monkeypatch.setattr(agent, "get_settings", lambda: settings)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-environment")

    client = agent.anthropic_client(SecretStr("sk-user"))

    assert client.api_key == "sk-user"
    assert client.timeout == 42.0
    await client.close()


async def test_client_needs_a_key() -> None:
    """An empty key is refused instead of letting the SDK find credentials itself."""
    with pytest.raises(AINotConfiguredError):
        agent.anthropic_client(SecretStr(""))


async def test_unparseable_retry_after_is_ignored() -> None:
    """A malformed ``retry-after`` header is dropped rather than failing the request."""
    error = anthropic.RateLimitError(
        "slow down",
        response=httpx2.Response(429, headers={"retry-after": "soon"}, request=REQUEST),
        body=None,
    )

    with pytest.raises(AIRateLimitError) as caught:
        await _write(FakeClient(error), "thesis")

    assert caught.value.retry_after is None


async def test_missing_credentials_are_not_configured() -> None:
    """The SDK's own credential error, raised before any request, means AI is unconfigured."""
    with pytest.raises(AINotConfiguredError, match="no credentials"):
        await _write(FakeClient(anthropic.AnthropicError("no credentials")), "thesis")


async def test_schema_mismatch_is_an_analysis_error() -> None:
    """A response that fails schema validation is an analysis error."""
    with pytest.raises(AnalysisError, match="schema"):
        await _write(FakeClient(_response(['{"headline": "x"}'])), "thesis")


async def test_missing_report_is_an_analysis_error() -> None:
    """A finished response without report text is not returned."""
    with pytest.raises(AnalysisError, match="schema"):
        await _write(FakeClient(_response([])), "thesis")


async def test_report_split_by_fallback_is_joined() -> None:
    """A report started by one model and finished by its fallback validates as one."""
    report_json = THESIS.model_dump_json()
    client = FakeClient(_response([report_json[:10], report_json[10:]], model="claude-opus-4-8"))

    report, model = await _write(client, "thesis")

    assert (report, model) == (THESIS, "claude-opus-4-8")


def _event(type_: str, **fields: Any) -> SimpleNamespace:
    """Build a stream event."""
    return SimpleNamespace(type=type_, **fields)


def _text_delta(text: str) -> SimpleNamespace:
    return _event("content_block_delta", delta=SimpleNamespace(type="text_delta", text=text))


def _thinking_delta(text: str) -> SimpleNamespace:
    return _event(
        "content_block_delta", delta=SimpleNamespace(type="thinking_delta", thinking=text)
    )


def _final(texts: list[str], **overrides: Any) -> SimpleNamespace:
    """Build the final streamed message with one text block per entry of ``texts``."""
    fields: dict[str, Any] = {
        "stop_reason": "end_turn",
        "stop_details": None,
        "content": [SimpleNamespace(type="text", text=text) for text in texts],
        "model": "claude-opus-5-5",
    }
    return SimpleNamespace(**(fields | overrides))


class FakeStream:
    """An async context manager replaying ``events``, then returning ``final``."""

    def __init__(self, events: list[SimpleNamespace], final: SimpleNamespace) -> None:
        self._events = events
        self._final = final

    async def __aenter__(self) -> "FakeStream":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def __aiter__(self) -> Any:
        for event in self._events:
            yield event

    async def get_final_message(self) -> SimpleNamespace:
        return self._final


class FakeStreamClient:
    """Records ``beta.messages.stream`` calls and replays a stream or raises."""

    def __init__(self, result: FakeStream | Exception) -> None:
        self.calls: list[dict[str, Any]] = []
        self._result = result
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs: Any) -> FakeStream:
        self.calls.append(kwargs)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


async def _collect(
    client: FakeStreamClient, kind: AnalysisKind = "thesis"
) -> list[AnalysisDelta | ModelFallback | WrittenReport]:
    """Run :func:`stream_analysis` to completion and return everything it yielded."""
    return [
        item
        async for item in stream_analysis(_context(), kind, client=cast(AsyncAnthropic, client))
    ]


async def test_stream_yields_thinking_report_and_result() -> None:
    """Reasoning and report fragments are yielded in order, then the validated report."""
    report_json = THESIS.model_dump_json()
    half = len(report_json) // 2
    stream = FakeStream(
        [
            _event("message_start"),
            _thinking_delta("Weighing momentum."),
            _text_delta(report_json[:half]),
            _text_delta(report_json[half:]),
            _event("message_stop"),
        ],
        _final([report_json]),
    )
    client = FakeStreamClient(stream)

    items = await _collect(client)

    assert items[:3] == [
        AnalysisDelta(channel="thinking", text="Weighing momentum."),
        AnalysisDelta(channel="report", text=report_json[:half]),
        AnalysisDelta(channel="report", text=report_json[half:]),
    ]
    assert items[3] == WrittenReport(report=THESIS, model="claude-opus-5-5")
    call = client.calls[0]
    assert call["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "headline" in call["output_config"]["format"]["schema"]["properties"]
    assert call["fallbacks"] == "default"
    assert call["betas"] == [FALLBACK_BETA]


async def test_stream_risk_uses_risk_schema() -> None:
    """A risk summary stream is constrained to and validated against the risk schema."""
    client = FakeStreamClient(FakeStream([], _final([THESIS.model_dump_json()])))

    with pytest.raises(AnalysisError, match="schema"):
        await _collect(client, "risk")
    schema = client.calls[0]["output_config"]["format"]["schema"]
    assert "key_risks" in schema["properties"]


async def test_stream_reports_fallback_and_joins_split_report() -> None:
    """A mid-stream fallback is yielded and the report spanning both models validates."""
    report_json = THESIS.model_dump_json()
    fallback = SimpleNamespace(
        type="fallback",
        from_=SimpleNamespace(model="claude-opus-5-5"),
        to=SimpleNamespace(model="claude-opus-4-8"),
    )
    stream = FakeStream(
        [_event("content_block_start", content_block=fallback)],
        _final([report_json[:10], report_json[10:]], model="claude-opus-4-8"),
    )

    items = await _collect(FakeStreamClient(stream))

    assert items == [
        ModelFallback(from_model="claude-opus-5-5", to_model="claude-opus-4-8"),
        WrittenReport(report=THESIS, model="claude-opus-4-8"),
    ]


async def test_stream_refusal_raises_after_fragments() -> None:
    """A refusal is raised once the stream ends, after the partial fragments."""
    stop_details = SimpleNamespace(category="cyber")
    stream = FakeStream(
        [_text_delta('{"headline": ')],
        _final(['{"headline": '], stop_reason="refusal", stop_details=stop_details),
    )
    received: list[object] = []

    with pytest.raises(AIRefusalError, match="cyber"):
        async for item in stream_analysis(
            _context(), "thesis", client=cast(AsyncAnthropic, FakeStreamClient(stream))
        ):
            received.append(item)
    assert received == [AnalysisDelta(channel="report", text='{"headline": ')]


async def test_stream_truncation_raises() -> None:
    """A stream cut off at the token limit is an analysis error."""
    stream = FakeStream([], _final(["{"], stop_reason="max_tokens"))

    with pytest.raises(AnalysisError, match="truncated"):
        await _collect(FakeStreamClient(stream))


async def test_stream_api_errors_are_translated() -> None:
    """SDK errors raised when the stream opens become analysis errors."""
    error = anthropic.RateLimitError(
        "slow down",
        response=httpx2.Response(429, request=REQUEST, headers={"retry-after": "7"}),
        body=None,
    )

    with pytest.raises(AIRateLimitError) as caught:
        await _collect(FakeStreamClient(error))
    assert caught.value.retry_after == 7
