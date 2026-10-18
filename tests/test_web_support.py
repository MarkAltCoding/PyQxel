"""Tests for the API routes the web frontend relies on, and for serving it."""

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import portfolio
from app.data.fetcher import DataFetchError
from app.db.users import User
from app.jobs.openapi import SCHEMA_PATH, openapi_schema, render
from app.main import WORKER_CONTENT_SECURITY_POLICY, app, mount_web
from app.models.stock import TickerInfo
from tests.conftest import make_user
from tests.test_screener_api import STOCKS, _stock, _store

client = TestClient(app)


# ── Search ──


def _search(q: str, **params: int) -> list[str]:
    response = client.get("/api/v1/search", params={"q": q, **params})
    assert response.status_code == 200, response.text
    return [item["symbol"] for item in response.json()["items"]]


def test_search_ranks_exact_symbol_then_prefix_then_name() -> None:
    """An exact ticker comes first, then tickers starting with it, then names containing it."""
    _store(
        [
            _stock("ABCX", 1, name="Something Else"),
            _stock("ZZZ", 2, name="Abc Holdings"),
            _stock("ABC", 3, name="The Abc Company"),
        ]
    )

    assert _search("abc") == ["ABC", "ABCX", "ZZZ"]


def test_search_orders_ties_by_market_cap() -> None:
    """Among equally good matches, larger companies come first."""
    _store(STOCKS)

    assert _search("inc") == ["BIG", "CHEAP", "LOSS", "THIN"]


def test_search_treats_wildcards_literally() -> None:
    """``%`` and ``_`` in a query are characters, not patterns."""
    _store([_stock("A", 1, name="100% Juice"), _stock("B", 2, name="Plain")])

    assert _search("%") == ["A"]
    assert _search("_") == []


def test_search_limits_results() -> None:
    """``limit`` caps the matches returned."""
    _store(STOCKS)

    assert len(_search("inc", limit=2)) == 2


def test_search_without_a_universe_is_empty() -> None:
    """Before the screener has run, search finds nothing rather than failing."""
    assert _search("aapl") == []


def test_search_needs_a_query() -> None:
    """An empty query is rejected."""
    assert client.get("/api/v1/search", params={"q": ""}).status_code == 422


def test_screener_status_lists_sectors() -> None:
    """The status names each sector once, for the sector filter."""
    _store(STOCKS)

    status = client.get("/api/v1/screener/status").json()

    assert status["sectors"] == ["Finance", "Technology"]


# ── Holdings ──


PRICES = {"AAPL": 200.0, "MSFT": 400.0}


@pytest.fixture
def priced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Price AAPL and MSFT; every other symbol fails to fetch."""

    async def fake_info(symbol: str) -> TickerInfo:
        if symbol not in PRICES:
            raise DataFetchError(f"No data for {symbol}.")
        return TickerInfo(symbol=symbol, name=f"{symbol} Corp", price=PRICES[symbol], source="test")

    monkeypatch.setattr(portfolio, "fetch_ticker_info", fake_info)


def test_holdings_start_empty(priced: None) -> None:
    """A new account has no saved portfolio."""
    body = client.get("/api/v1/portfolio/holdings").json()

    assert body["holdings"] == []
    assert body["total_value"] == 0


def test_saved_holdings_are_valued_and_weighted(priced: None) -> None:
    """Saved holdings come back in order, priced, with weights summing to one."""
    saved = client.put(
        "/api/v1/portfolio/holdings",
        json={"holdings": [{"symbol": "msft", "shares": 5}, {"symbol": "AAPL", "shares": 10}]},
    )
    assert saved.status_code == 200, saved.text

    body = client.get("/api/v1/portfolio/holdings").json()

    assert [h["symbol"] for h in body["holdings"]] == ["MSFT", "AAPL"]
    assert body["total_value"] == pytest.approx(4000.0)
    assert [h["weight"] for h in body["holdings"]] == [pytest.approx(0.5), pytest.approx(0.5)]
    assert body["holdings"][0]["name"] == "MSFT Corp"
    assert body["notice"] is None


def test_unpriced_holdings_are_kept_without_weight(priced: None) -> None:
    """A holding whose price cannot be fetched stays listed, named in the notice."""
    client.put(
        "/api/v1/portfolio/holdings",
        json={"holdings": [{"symbol": "AAPL", "shares": 1}, {"symbol": "GONE", "shares": 3}]},
    )

    body = client.get("/api/v1/portfolio/holdings").json()

    gone = body["holdings"][1]
    assert gone["symbol"] == "GONE" and gone["shares"] == 3
    assert gone["price"] is None and gone["weight"] is None
    assert body["holdings"][0]["weight"] == pytest.approx(1.0)
    assert "GONE" in body["notice"]


def test_saving_replaces_the_portfolio(priced: None) -> None:
    """Each save replaces the whole portfolio; an empty list clears it."""
    client.put("/api/v1/portfolio/holdings", json={"holdings": [{"symbol": "AAPL", "shares": 1}]})
    client.put("/api/v1/portfolio/holdings", json={"holdings": [{"symbol": "MSFT", "shares": 2}]})
    assert [h["symbol"] for h in client.get("/api/v1/portfolio/holdings").json()["holdings"]] == [
        "MSFT"
    ]

    client.put("/api/v1/portfolio/holdings", json={"holdings": []})

    assert client.get("/api/v1/portfolio/holdings").json()["holdings"] == []


@pytest.mark.parametrize(
    "holdings",
    [
        [{"symbol": "AAPL", "shares": 0}],
        [{"symbol": "AAPL", "shares": -1}],
        [{"symbol": "AAPL", "shares": 1}, {"symbol": "aapl", "shares": 2}],
        [{"symbol": f"S{i}", "shares": 1} for i in range(21)],
        [{"symbol": "not a symbol", "shares": 1}],
    ],
)
def test_invalid_holdings_are_rejected(priced: None, holdings: list[dict[str, object]]) -> None:
    """Shares must be positive, symbols valid and distinct, and at most 20 of them."""
    response = client.put("/api/v1/portfolio/holdings", json={"holdings": holdings})

    assert response.status_code == 422


def test_holdings_belong_to_their_account(
    priced: None, monkeypatch: pytest.MonkeyPatch, user: User
) -> None:
    """Another account neither sees nor changes this account's portfolio."""
    from app.api.auth import authenticate

    client.put("/api/v1/portfolio/holdings", json={"holdings": [{"symbol": "AAPL", "shares": 1}]})
    other = asyncio.run(make_user("other@example.com", None, None))

    async def as_other() -> User:
        return other

    monkeypatch.setitem(app.dependency_overrides, authenticate, as_other)
    assert client.get("/api/v1/portfolio/holdings").json()["holdings"] == []
    client.put("/api/v1/portfolio/holdings", json={"holdings": [{"symbol": "MSFT", "shares": 1}]})

    async def as_user() -> User:
        return user

    monkeypatch.setitem(app.dependency_overrides, authenticate, as_user)
    assert [h["symbol"] for h in client.get("/api/v1/portfolio/holdings").json()["holdings"]] == [
        "AAPL"
    ]


# ── Serving the frontend ──


@pytest.fixture
def web_client(tmp_path: Path) -> TestClient:
    """An app serving a small built frontend from ``tmp_path``."""
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<div id=root></div>")
    (tmp_path / "assets" / "app-1a2b.js").write_text("console.log(1)")
    (tmp_path / "assets" / "app-1a2b.css").write_text("body{}")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    (tmp_path.parent / "secret.txt").write_text("secret")
    application = FastAPI()

    @application.get("/api/v1/ping")
    async def ping() -> dict[str, str]:
        return {"pong": "yes"}

    assert mount_web(application, tmp_path)
    return TestClient(application)


def test_frontend_routes_get_the_app(web_client: TestClient) -> None:
    """The frontend's own paths load index.html, revalidated on each visit."""
    for path in ("/", "/stocks/AAPL", "/screener"):
        response = web_client.get(path)
        assert response.status_code == 200
        assert "root" in response.text
        assert response.headers["cache-control"] == "no-cache"


def test_built_files_are_served(web_client: TestClient) -> None:
    """Assets and files at the root are served as they are."""
    assert web_client.get("/assets/app-1a2b.js").text == "console.log(1)"
    assert web_client.get("/favicon.svg").text == "<svg/>"


def test_scripts_carry_the_worker_content_security_policy(web_client: TestClient) -> None:
    """JavaScript assets, the strategy worker's among them, set the policy a worker runs
    under: this origin only, WebAssembly but no eval."""
    script = web_client.get("/assets/app-1a2b.js")
    style = web_client.get("/assets/app-1a2b.css")

    assert script.headers["content-security-policy"] == WORKER_CONTENT_SECURITY_POLICY
    assert "'wasm-unsafe-eval'" in WORKER_CONTENT_SECURITY_POLICY
    assert "'unsafe-eval'" not in WORKER_CONTENT_SECURITY_POLICY.replace("'wasm-unsafe-eval'", "")
    assert "content-security-policy" not in style.headers
    assert "content-security-policy" not in web_client.get("/").headers


def test_api_routes_win_and_unknown_api_paths_are_404(web_client: TestClient) -> None:
    """The API is not shadowed, and a mistyped API path is a 404, not the app."""
    assert web_client.get("/api/v1/ping").json() == {"pong": "yes"}
    assert web_client.get("/api/v1/nope").status_code == 404
    assert web_client.get("/healthz").status_code == 404


def test_files_outside_the_build_are_not_served(web_client: TestClient) -> None:
    """Paths climbing out of the build folder get the app, never the file."""
    response = web_client.get("/..%2Fsecret.txt")

    assert "secret" not in response.text


def test_nothing_is_mounted_without_a_build(tmp_path: Path) -> None:
    """Without a built frontend, the API runs alone."""
    assert not mount_web(FastAPI(), tmp_path)


# ── The frontend's types ──


def test_committed_openapi_schema_is_current() -> None:
    """web/openapi.json, which the frontend's types come from, matches the API.

    Regenerate with ``python -m app.jobs.openapi`` and ``npm run gen:api`` in ``web/``.
    """
    assert SCHEMA_PATH.read_text(encoding="utf-8") == render(openapi_schema())
