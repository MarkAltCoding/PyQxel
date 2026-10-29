"""Tests for taking Claude requests outside the app and bringing the answers back.

Market data is faked; nothing calls Claude.
"""

import asyncio
import json
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.ai.cache import cached_analysis
from app.ai.external import PASTED_MODEL, ExternalResponseError, parse_response, render_prompt
from app.ai.prompts import ASSISTANT_SYSTEM_PROMPT, SYSTEM_PROMPT
from app.api.v1.endpoints import research
from app.main import app
from app.models.assistant import AssistantReply
from app.db.users import User
from app.models.research import AnalysisRequest, Filing, FilingSection, InvestmentThesis
from tests.test_agent import THESIS, _context
from tests.test_portfolio_review import REVIEW, _save, market  # noqa: F401

client = TestClient(app)

ANSWER = THESIS.model_dump_json()


# Rendering and parsing.


def test_the_prompt_holds_the_instructions_request_and_answer_format() -> None:
    messages: list[Any] = [
        {"role": "user", "content": "Write a strategy."},
        {"role": "assistant", "content": "Here it is."},
        {"role": "user", "content": [{"type": "text", "text": "<editor>code</editor>"}]},
    ]

    prompt = render_prompt(ASSISTANT_SYSTEM_PROMPT, messages, AssistantReply)

    assert ASSISTANT_SYSTEM_PROMPT in prompt
    conversation = prompt.index("## Our conversation so far")
    assert prompt.index("### Me\n\nWrite a strategy.") > conversation
    assert "### You\n\nHere it is." in prompt
    assert prompt.index("## Request\n\n<editor>code</editor>") > conversation
    schema = prompt.split("```json\n")[1].split("\n```")[0]
    assert json.loads(schema) == AssistantReply.model_json_schema()


@pytest.mark.parametrize(
    "pasted",
    [
        ANSWER,
        f"```json\n{ANSWER}\n```",
        f"Here is the thesis:\n\n```\n{ANSWER}\n```\nLet me know if you need more.",
        f"Sure! {ANSWER} Hope this helps.",
    ],
)
def test_answers_are_read_with_or_without_fences_and_prose(pasted: str) -> None:
    assert parse_response(pasted, InvestmentThesis) == THESIS


@pytest.mark.parametrize(
    ("pasted", "message"),
    [
        ("I can't help with that.", "No JSON object found"),
        ('{"headline": "x",', "cut short"),
        ('{"headline": "x", }', "not valid JSON"),
        ('{"headline": "x"}', "does not match the expected format: stance: Field required"),
    ],
)
def test_bad_answers_say_what_is_wrong(pasted: str, message: str) -> None:
    with pytest.raises(ExternalResponseError, match=message):
        parse_response(pasted, InvestmentThesis)


# Stock analyses.


@pytest.fixture
def gathered(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fake the analysis data; return the symbols it was gathered for."""
    asked: list[str] = []
    filing = Filing(
        form="10-K",
        accession_number="0000320193-24-000123",
        filed=date(2024, 11, 1),
        url="https://www.sec.gov/x",
        sections=[
            FilingSection(title="Item 1A. Risk Factors", text="Supply chain risk.", truncated=False)
        ],
    )

    async def gather(symbol: str, request: Any) -> tuple[str, Any, list[Filing]]:
        asked.append(symbol)
        return symbol.upper(), _context(), [filing]

    monkeypatch.setattr(research, "gather_analysis_data", gather)
    return asked


def test_the_analysis_prompt_needs_no_anthropic_key_and_holds_the_filings(
    gathered: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_key() -> None:
        raise AssertionError("an export must not need an Anthropic key")

    monkeypatch.setattr(research, "anthropic_key", no_key)

    body = client.post("/api/v1/stocks/aapl/analysis/prompt", json={"kind": "thesis"}).json()

    assert SYSTEM_PROMPT in body["prompt"]
    assert "Supply chain risk." in body["prompt"]
    assert '"stance"' in body["prompt"]
    assert body["filename"] == "pyqxel-AAPL-thesis-1y-prompt.md"
    assert body["context"]["ticker"]["symbol"] == "AAPL"


def test_a_pasted_analysis_is_stored_but_never_reused(gathered: list[str], user: User) -> None:
    exported = client.post("/api/v1/stocks/AAPL/analysis/prompt", json={"kind": "thesis"}).json()

    saved = client.post(
        "/api/v1/stocks/AAPL/analysis/import",
        json={
            "request": {"kind": "thesis"},
            "context": exported["context"],
            "response": f"```json\n{ANSWER}\n```",
        },
    )

    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["model"] == PASTED_MODEL and body["id"]
    assert body["report"] == THESIS.model_dump()
    listed = client.get("/api/v1/analyses", params={"symbol": "AAPL"}).json()["items"]
    assert [item["id"] for item in listed] == [body["id"]]
    stored = client.get(f"/api/v1/analyses/{body['id']}").json()
    assert stored["context"] == exported["context"]
    reusable = asyncio.run(cached_analysis(user.id, "AAPL", AnalysisRequest(kind="thesis")))
    assert reusable is None, "the app writes its own analysis rather than reuse a pasted one"


def test_a_pasted_analysis_must_match_its_symbol_and_format(gathered: list[str]) -> None:
    context = client.post("/api/v1/stocks/AAPL/analysis/prompt", json={}).json()["context"]

    wrong_symbol = client.post(
        "/api/v1/stocks/MSFT/analysis/import",
        json={"request": {}, "context": context, "response": ANSWER},
    )
    wrong_kind = client.post(
        "/api/v1/stocks/AAPL/analysis/import",
        json={"request": {"kind": "risk"}, "context": context, "response": ANSWER},
    )

    assert wrong_symbol.status_code == 422 and "not MSFT" in wrong_symbol.json()["detail"]
    assert wrong_kind.status_code == 422
    assert "risk_level: Field required" in wrong_kind.json()["detail"]


# Portfolio reviews.


# The market fixture is imported from the portfolio review tests.
def test_a_pasted_portfolio_review_gets_its_orders(market: list[Any]) -> None:  # noqa: F811
    _save({"AAPL": 10, "MSFT": 5, "XOM": 20})
    exported = client.post("/api/v1/portfolio/analysis/prompt", json={"period": "1y"}).json()

    saved = client.post(
        "/api/v1/portfolio/analysis/import",
        json={"context": exported["context"], "response": REVIEW.model_dump_json()},
    ).json()

    assert '"target_allocation"' in exported["prompt"]
    assert market == [], "nothing was asked of Claude"
    assert saved["model"] == PASTED_MODEL
    trades = {t["symbol"]: (t["action"], t["shares"]) for t in saved["plan"]["trades"]}
    assert trades["VXUS"] == ("buy", 24)
    assert client.get("/api/v1/portfolio/analysis").json()["id"] == saved["id"]
    again = client.post("/api/v1/portfolio/analysis").json()
    assert again["cached"] is False, "a pasted review is never reused for a new one"


# The coding assistant.


def test_an_assistant_prompt_and_pasted_reply() -> None:
    question = {"code": "def strategy(bars): ...", "message": "Explain."}
    reply = {"explanation": "It returns nothing.", "code": None}

    exported = client.post("/api/v1/backtests/assistant/prompt", json=question).json()
    imported = client.post(
        "/api/v1/backtests/assistant/import", json={"response": json.dumps(reply)}
    ).json()

    assert "<editor" in exported["prompt"] and exported["prompt"].rstrip().endswith("```")
    assert imported == {"reply": reply, "model": PASTED_MODEL}
