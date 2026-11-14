"""Tests for the web search Claude makes for a report: findings and their sources.

Anthropic is faked; nothing here reaches the web.
"""

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast

import pytest
from anthropic import AsyncAnthropic
from fastapi.testclient import TestClient

from app.ai import web
from app.ai.agent import AINotConfiguredError, AnalysisError, analysis_research_task
from app.ai.prompts import PICKS_SYSTEM_PROMPT, PORTFOLIO_SYSTEM_PROMPT, SYSTEM_PROMPT
from app.ai.web import (
    WEB_FAILED_NOTICE,
    add_web_research,
    findings_in,
    stream_web_research,
    with_web_research,
)
from app.main import app
from app.models.research import AnalysisContext, AnalysisDelta, ModelFallback
from app.models.stock import TickerInfo
from app.models.web import WebFinding, WebResearch, WebSearchQuery, WebSource
from tests.test_agent import FakeStream, FakeStreamClient, _event, _thinking_delta
from tests.test_api import THESIS, _recent_frame, _serve_analysis, _serve_stream, _sse_events

client = TestClient(app)


def _citation(url: str, title: str = "A page", cited: str = "the passage") -> SimpleNamespace:
    return SimpleNamespace(
        type="web_search_result_location", url=url, title=title, cited_text=cited
    )


def _text(text: str, *citations: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text, citations=list(citations) or None)


def _message(
    blocks: list[SimpleNamespace], stop_reason: str = "end_turn", searches: int = 1
) -> SimpleNamespace:
    return SimpleNamespace(
        content=blocks,
        stop_reason=stop_reason,
        stop_details=None,
        model="claude-opus-5-5",
        usage=SimpleNamespace(server_tool_use=SimpleNamespace(web_search_requests=searches)),
    )


def _search(query: str) -> SimpleNamespace:
    return _event(
        "content_block_stop",
        content_block=SimpleNamespace(type="server_tool_use", input={"query": query}),
    )


RESEARCH = WebResearch(
    searched_at="2026-11-13T12:00:00Z",  # type: ignore[arg-type]
    queries=["apple earnings"],
    searches=1,
    findings=[
        WebFinding(
            id=1,
            text="Apple reported record revenue.",
            sources=[WebSource(url="https://www.reuters.com/a", title="Reuters")],
        )
    ],
)


def test_only_statements_tied_to_a_page_become_findings() -> None:
    """Every finding has a source: uncited text is dropped, and so is anything that is
    not an ordinary web address, since findings are shown as links."""
    message = _message(
        [
            _text("Here is what I found. "),
            _text(
                "Apple  reported record\nrevenue.",
                _citation("https://www.reuters.com/a", "Reuters"),
                _citation("https://www.reuters.com/a", "Reuters"),
                _citation("https://apnews.com/b", "AP"),
            ),
            _text("A script.", _citation("javascript:alert(1)")),
            SimpleNamespace(type="server_tool_use", input={"query": "q"}),
            _text("Margins widened.", _citation("https://www.ft.com/c", "")),
        ]
    )

    findings = findings_in(cast(Any, message), start=4)

    assert [(f.id, f.text) for f in findings] == [
        (4, "Apple reported record revenue."),
        (5, "Margins widened."),
    ]
    assert [s.url for s in findings[0].sources] == [
        "https://www.reuters.com/a",
        "https://apnews.com/b",
    ]
    assert findings[1].sources[0].title is None


@pytest.mark.asyncio
async def test_research_streams_reasoning_searches_and_findings() -> None:
    stream = FakeStream(
        [_thinking_delta("Looking for results."), _search(" apple earnings ")],
        _message([_text("Apple reported record revenue.", _citation("https://reuters.com/a"))]),
    )
    fake = FakeStreamClient(stream)

    items = [
        item async for item in stream_web_research("About AAPL.", client=cast(AsyncAnthropic, fake))
    ]

    assert items[0] == AnalysisDelta(channel="thinking", text="Looking for results.")
    assert items[1] == WebSearchQuery(query="apple earnings")
    found = items[2]
    assert isinstance(found, WebResearch)
    assert found.queries == ["apple earnings"] and found.searches == 1
    assert [f.text for f in found.findings] == ["Apple reported record revenue."]
    call = fake.calls[0]
    assert call["tools"] == [{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}]
    assert (
        "output_config" in call and "format" not in call["output_config"]
    ), "the search is not a structured request, which could not carry its citations"
    assert call["messages"] == [{"role": "user", "content": "About AAPL."}]


@pytest.mark.asyncio
async def test_a_paused_search_is_resumed_and_its_findings_numbered_on() -> None:
    """``pause_turn`` is answered by sending the paused turn back unchanged."""
    paused = _message([_text("First.", _citation("https://a.example/1"))], "pause_turn")
    done = _message([_text("Second.", _citation("https://a.example/2"))])
    streams = iter([FakeStream([], paused), FakeStream([], done)])
    calls: list[dict[str, Any]] = []

    def stream(**kwargs: Any) -> FakeStream:
        calls.append(kwargs)
        return next(streams)

    fake = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))

    items = [item async for item in stream_web_research("Task.", client=cast(AsyncAnthropic, fake))]

    found = items[-1]
    assert isinstance(found, WebResearch)
    assert [(f.id, f.text) for f in found.findings] == [(1, "First."), (2, "Second.")]
    assert found.searches == 2
    assert calls[1]["messages"] == [
        {"role": "user", "content": "Task."},
        {"role": "assistant", "content": paused.content},
    ]


@pytest.mark.asyncio
async def test_a_declined_search_is_an_error() -> None:
    fake = FakeStreamClient(FakeStream([], _message([], "refusal")))

    with pytest.raises(AnalysisError, match="declined to search"):
        [item async for item in stream_web_research("Task.", client=cast(AsyncAnthropic, fake))]


def _context() -> AnalysisContext:
    from tests.test_agent import _context as agent_context

    return agent_context()


@pytest.mark.asyncio
async def test_a_failed_search_leaves_the_report_to_be_written_with_a_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing(task: str, *, client: Any) -> AsyncIterator[object]:
        empty: list[object] = []
        for nothing in empty:
            yield nothing
        raise AnalysisError("The AI service rejected the request.")

    monkeypatch.setattr(web, "stream_web_research", failing)

    context = await add_web_research(_context(), "Task.", client=cast(AsyncAnthropic, None))

    assert context.web_research is None
    assert context.notice is not None and context.notice.endswith(WEB_FAILED_NOTICE)


@pytest.mark.asyncio
async def test_a_rejected_key_still_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """The report would fail on a bad key too, so the search does not hide it."""

    async def rejected(task: str, *, client: Any) -> AsyncIterator[object]:
        empty: list[object] = []
        for nothing in empty:
            yield nothing
        raise AINotConfiguredError("Anthropic credentials are missing or invalid.")

    monkeypatch.setattr(web, "stream_web_research", rejected)

    with pytest.raises(AINotConfiguredError):
        await add_web_research(_context(), "Task.", client=cast(AsyncAnthropic, None))


@pytest.mark.asyncio
async def test_findings_join_the_context_after_the_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def found(task: str, *, client: Any) -> AsyncIterator[object]:
        yield WebSearchQuery(query="apple earnings")
        yield RESEARCH

    monkeypatch.setattr(web, "stream_web_research", found)

    items = [
        item
        async for item in with_web_research(_context(), "Task.", client=cast(AsyncAnthropic, None))
    ]

    assert items[0] == WebSearchQuery(query="apple earnings")
    assert isinstance(items[1], AnalysisContext) and items[1].web_research == RESEARCH


def test_every_report_is_told_how_to_cite_the_web() -> None:
    for prompt in (SYSTEM_PROMPT, PORTFOLIO_SYSTEM_PROMPT, PICKS_SYSTEM_PROMPT):
        assert "web_research" in prompt and "square brackets" in prompt


def test_the_research_task_names_the_security_and_what_is_missing() -> None:
    task = analysis_research_task(_context(), "risk")

    assert "risk summary" in task and "AAPL" in task and "price targets" in task


# The stock report's routes.


def _fake_web(monkeypatch: pytest.MonkeyPatch, tasks: list[str]) -> None:
    async def found(task: str, *, client: Any) -> AsyncIterator[object]:
        tasks.append(task)
        yield AnalysisDelta(channel="thinking", text="Searching.")
        yield WebSearchQuery(query="apple earnings")
        yield RESEARCH

    monkeypatch.setattr(web, "stream_web_research", found)


def test_a_report_that_searches_the_web_is_written_from_its_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _serve_analysis(
        monkeypatch, TickerInfo(symbol="AAPL", source="yfinance"), _recent_frame(), THESIS
    )
    tasks: list[str] = []
    _fake_web(monkeypatch, tasks)

    plain = client.post("/api/v1/stocks/AAPL/analysis", json={})
    searched = client.post("/api/v1/stocks/AAPL/analysis", json={"web_search": True})
    again = client.post("/api/v1/stocks/AAPL/analysis", json={"web_search": True})

    assert plain.status_code == 200 and plain.json()["context"]["web_research"] is None
    assert searched.status_code == 200, searched.text
    body = searched.json()
    assert body["cached"] is False, "a report that searches the web is always written anew"
    assert again.json()["cached"] is False
    finding = body["context"]["web_research"]["findings"][0]
    assert finding["id"] == 1 and finding["sources"][0]["url"] == "https://www.reuters.com/a"
    assert len(tasks) == 2 and "AAPL" in tasks[0]
    assert calls[0][0].web_research is None
    assert calls[1][0].web_research == RESEARCH, "Claude writes from the findings"


def test_a_streamed_report_shows_each_search(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ai.agent import WrittenReport

    items: list[AnalysisDelta | ModelFallback | WrittenReport] = [
        WrittenReport(report=THESIS, model="claude-opus-5-5")
    ]
    calls = _serve_stream(monkeypatch, items)
    _fake_web(monkeypatch, [])

    response = client.post("/api/v1/stocks/AAPL/analysis/stream", json={"web_search": True})

    events = _sse_events(response.text)
    assert [name for name, _ in events] == ["context", "thinking", "search", "result"]
    assert events[2][1] == {"query": "apple earnings"}
    result = cast(dict[str, Any], events[3][1])
    assert result["context"]["web_research"]["searches"] == 1
    assert calls[0][0].web_research == RESEARCH
