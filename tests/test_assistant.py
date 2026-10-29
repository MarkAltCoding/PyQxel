"""Tests for the custom strategy coding assistant. Claude is faked."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.ai.agent import AIRateLimitError, Written
from app.ai.assistant import assistant_messages
from app.api.v1.endpoints import assistant
from app.db.session import get_sessionmaker
from app.db.users import User, ai_usage, create_api_key
from app.main import app
from app.models.assistant import AssistantReply, AssistantRequest, AssistantTurn, LastRun
from app.models.research import AnalysisDelta
from tests.test_api import _sse_events

client = TestClient(app)


def _signed_in(user: User) -> dict[str, str]:
    """Headers of a real API key for ``user``, with an Anthropic key, as production sends."""

    async def issue() -> str:
        async with get_sessionmaker()() as session:
            return (await create_api_key(session, user.id, "test")).key

    return {"Authorization": f"Bearer {asyncio.run(issue())}", "X-Anthropic-Key": "sk-ant-test-key"}


CODE = "def strategy(bars):\n    return bars['Close'].rolling(5).mean()\n"
FIXED = CODE.replace(".mean()", ".mean().gt(bars['Close']).astype(float).fillna(0.0)")
REPLY = AssistantReply(explanation="Rolling means are NaN until they fill.", code=FIXED)


def test_the_question_carries_the_current_code_and_last_run() -> None:
    request = AssistantRequest(
        language="python",
        code=CODE,
        message="Why does it fail?",
        symbol="SPY",
        interval="1d",
        history=[
            AssistantTurn(role="assistant", content="Hello."),
            AssistantTurn(role="user", content="Write a trend strategy."),
            AssistantTurn(role="assistant", content="Here it is."),
        ],
        last_run=LastRun(
            error_kind="result", message="The position is NaN on 4 bars.", line=2, output="hi"
        ),
    )

    messages = assistant_messages(request)

    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    question = messages[-1]["content"]
    assert isinstance(question, str)
    assert '<editor language="Python" symbol="SPY" interval="1d">' in question
    assert CODE in question
    assert '<last_run outcome="failed" error="result" line="2">' in question
    assert "<printed>\nhi\n</printed>" in question
    assert question.endswith("Why does it fail?")


def test_a_successful_run_is_described_without_an_error() -> None:
    request = AssistantRequest(code=CODE, message="Explain.", last_run=LastRun(output="ok"))

    question = assistant_messages(request)[-1]["content"]

    assert isinstance(question, str)
    assert '<last_run outcome="succeeded">' in question


@pytest.fixture
def asked(monkeypatch: pytest.MonkeyPatch) -> list[AssistantRequest]:
    questions: list[AssistantRequest] = []

    async def write(request: AssistantRequest, *, client: Any) -> tuple[AssistantReply, str]:
        questions.append(request)
        return REPLY, "claude-opus-5-5"

    monkeypatch.setattr(assistant, "write_assistant_reply", write)
    return questions


def test_the_assistant_answers_with_revised_code(asked: list[AssistantRequest]) -> None:
    response = client.post(
        "/api/v1/backtests/assistant",
        json={"language": "cpp", "code": "void strategy();", "message": "Fix it."},
    )

    assert response.status_code == 200
    assert response.json() == {"reply": REPLY.model_dump(), "model": "claude-opus-5-5"}
    assert asked[0].language == "cpp"


@pytest.mark.usefixtures("real_auth")
def test_answers_count_against_the_ai_limits(asked: list[AssistantRequest], user: User) -> None:
    headers = _signed_in(user)
    client.put("/api/v1/me/limits", headers=headers, json={"ai_requests_per_hour": 2})
    question = {"code": CODE, "message": "Explain."}

    answers = [
        client.post("/api/v1/backtests/assistant", headers=headers, json=question) for _ in range(3)
    ]

    async def usage() -> int:
        async with get_sessionmaker()() as session:
            return (await ai_usage(session, user.id)).last_hour

    assert [a.status_code for a in answers] == [200, 200, 429]
    assert asyncio.run(usage()) == 2
    assert len(asked) == 2


@pytest.mark.parametrize(
    "body",
    [
        {"code": CODE, "message": "   "},
        {"code": "x" * 50_001, "message": "Explain."},
        {"code": CODE, "message": "Explain.", "language": "rust"},
        {"code": CODE, "message": "x", "history": [{"role": "user", "content": "x"}] * 21},
    ],
)
def test_oversized_or_malformed_questions_are_refused(
    asked: list[AssistantRequest], body: dict[str, Any]
) -> None:
    assert client.post("/api/v1/backtests/assistant", json=body).status_code == 422
    assert asked == []


def test_the_answer_streams_with_reasoning(monkeypatch: pytest.MonkeyPatch) -> None:
    async def stream(request: AssistantRequest, *, client: Any) -> AsyncIterator[object]:
        yield AnalysisDelta(channel="thinking", text="Reading the traceback.")
        yield AnalysisDelta(channel="report", text='{"explanation"')
        yield Written(output=REPLY, model="claude-opus-5-5")

    monkeypatch.setattr(assistant, "stream_assistant_reply", stream)

    response = client.post(
        "/api/v1/backtests/assistant/stream", json={"code": CODE, "message": "Fix it."}
    )

    events = _sse_events(response.text)
    assert [name for name, _ in events] == ["thinking", "report", "result"]
    assert events[2][1] == {"reply": REPLY.model_dump(), "model": "claude-opus-5-5"}


def test_stream_errors_arrive_as_events(monkeypatch: pytest.MonkeyPatch) -> None:
    async def stream(request: AssistantRequest, *, client: Any) -> AsyncIterator[object]:
        nothing: tuple[object, ...] = ()
        for item in nothing:  # A generator that fails before yielding anything.
            yield item
        raise AIRateLimitError("slow down", 9)

    monkeypatch.setattr(assistant, "stream_assistant_reply", stream)

    response = client.post(
        "/api/v1/backtests/assistant/stream", json={"code": CODE, "message": "Fix it."}
    )

    assert _sse_events(response.text) == [
        ("error", {"status": 503, "detail": "slow down", "retry_after": 9})
    ]
