"""Tests that hostile input is refused cleanly: oversized and deeply nested bodies,
pasted answers built to be slow or to crash the parser, and pasted plans or data that
would make the server do work it should not."""

import json
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.ai.external import ExternalResponseError, parse_response
from app.data.fetcher import fetch_ticker_info
from app.core.request_limits import MAX_BODY_BYTES
from app.main import app
from app.models.research import InvestmentThesis
from tests.test_portfolio_review import REVIEW, _save, market  # noqa: F401

client = TestClient(app, raise_server_exceptions=False)

JSON = {"content-type": "application/json"}


def test_bodies_over_the_limit_are_refused_before_being_read() -> None:
    big = b'{"response": "' + b"a" * (MAX_BODY_BYTES + 1) + b'"}'

    declared = client.post("/api/v1/backtests/assistant/import", content=big, headers=JSON)

    def chunks() -> Iterator[bytes]:
        for start in range(0, len(big), 1 << 20):
            yield big[start : start + (1 << 20)]

    undeclared = client.post("/api/v1/backtests/assistant/import", content=chunks(), headers=JSON)

    assert declared.status_code == 413
    assert undeclared.status_code == 413


def test_bodies_under_the_limit_still_work() -> None:
    reply = '{"explanation": "Fine.", "code": null}'

    response = client.post("/api/v1/backtests/assistant/import", json={"response": reply})

    assert response.status_code == 200


def test_deeply_nested_bodies_are_refused_without_a_server_error() -> None:
    nested = '{"code": "x", "message": "y", "history": ' + "[" * 50_000 + "]" * 50_000 + "}"

    response = client.post("/api/v1/backtests/assistant/prompt", content=nested, headers=JSON)

    assert response.status_code == 422


def test_rejected_input_is_not_echoed_back() -> None:
    secret = "sk-ant-should-not-come-back"

    response = client.post(
        "/api/v1/backtests/assistant/prompt", json={"code": 1, "message": secret, "language": 3}
    )

    assert response.status_code == 422
    assert secret not in response.text
    assert all("input" not in error for error in response.json()["detail"])


@pytest.mark.parametrize(
    "pasted",
    [
        "```{" * 50_000,
        "{" * 200_000,
        '{"a":' + "[" * 199_000,
        "```json\n" + "x" * 199_000,
    ],
)
def test_hostile_pastes_are_refused_quickly(pasted: str) -> None:
    started = time.perf_counter()
    with pytest.raises(ExternalResponseError):
        parse_response(pasted, InvestmentThesis)

    assert time.perf_counter() - started < 0.5


# The market fixture is imported from the portfolio review tests.
def test_a_pasted_plan_prices_only_real_symbols_and_few_of_them(
    market: list[Any], monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    from app.api.v1.endpoints import portfolio

    looked_up: list[str] = []

    async def counted(symbol: str, client: Any = None) -> Any:
        looked_up.append(symbol)
        return await fetch_ticker_info(symbol)

    monkeypatch.setattr(portfolio, "fetch_ticker_info", counted)
    _save({"AAPL": 10, "MSFT": 5, "XOM": 20})
    exported = client.post("/api/v1/portfolio/analysis/prompt", json={}).json()
    review = REVIEW.model_dump(mode="json")
    review["target_allocation"] = [
        {"symbol": f"Z{n}", "target_weight": 0.0, "order_type": "market", "rationale": ""}
        for n in range(500)
    ] + [
        {"symbol": "../../me", "target_weight": 0.5, "order_type": "market", "rationale": ""},
        {"symbol": "AAPL\nX", "target_weight": 0.5, "order_type": "market", "rationale": ""},
    ]
    looked_up.clear()

    response = client.post(
        "/api/v1/portfolio/analysis/import",
        json={"context": exported["context"], "response": json.dumps(review)},
    )

    assert response.status_code == 200, response.text
    assert len(looked_up) <= 50
    assert all(symbol.startswith("Z") for symbol in looked_up)
    planned = {trade["symbol"] for trade in response.json()["plan"]["trades"]}
    assert not planned & {"../../ME", "AAPL\nX"}


def test_pasted_portfolio_data_must_be_plausible(market: list[Any]) -> None:  # noqa: F811
    _save({"AAPL": 10, "XOM": 20})
    context = client.post("/api/v1/portfolio/analysis/prompt", json={}).json()["context"]
    context["holdings"][0]["shares"] = -10

    response = client.post(
        "/api/v1/portfolio/analysis/import",
        json={"context": context, "response": REVIEW.model_dump_json()},
    )

    assert response.status_code == 422
