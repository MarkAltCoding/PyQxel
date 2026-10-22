"""Tests for the API routes the web frontend relies on, and for serving it."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.testclient import TestClient

from app.api.v1.endpoints import portfolio
from app.data.fetcher import DataFetchError
from app.db.users import User
from app.jobs.openapi import SCHEMA_PATH, openapi_schema, render
from app.main import app
from app import web
from app.web import (
    PAGE_CONTENT_SECURITY_POLICY,
    WORKER_CONTENT_SECURITY_POLICY,
    CompressApiResponses,
    accepts_brotli,
    mount_web,
)
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
    (tmp_path / "assets" / "worker-9z.js").write_text("compute()" * 100)
    (tmp_path / "assets" / "worker-9z.js.br").write_bytes(b"brotli worker")
    (tmp_path / "clang").mkdir()
    (tmp_path / "clang" / "llvm.core.wasm").write_bytes(b"\0asm" + b"x" * 4000)
    (tmp_path / "clang" / "llvm.core.wasm.br").write_bytes(b"brotli wasm")
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


@pytest.mark.parametrize("path", ["/", "/stocks/AAPL", "/settings"])
def test_pages_carry_the_page_security_headers(web_client: TestClient, path: str) -> None:
    """The app's pages, which hold the API key in storage, run only the app's own scripts
    and talk only to this origin."""
    response = web_client.get(path)

    policy = response.headers["content-security-policy"]
    assert policy == PAGE_CONTENT_SECURITY_POLICY
    directives = dict(part.split(" ", 1) for part in policy.split("; "))
    assert directives["script-src"] == "'self'"
    assert directives["connect-src"] == "'self'"
    assert directives["frame-ancestors"] == "'none'"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "same-origin"


def test_api_and_assets_do_not_carry_the_page_policy(web_client: TestClient) -> None:
    assert "content-security-policy" not in web_client.get("/api/v1/ping").headers
    assert "content-security-policy" not in web_client.get("/assets/app-1a2b.css").headers


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


@pytest.mark.parametrize("path", ["/clang/llvm.core.wasm", "/assets/worker-9z.js"])
def test_brotli_copies_go_to_clients_that_accept_brotli(web_client: TestClient, path: str) -> None:
    """A file's build-time Brotli copy is sent, under the file's own type, only to
    clients that accept Brotli; both answers say they vary by encoding."""
    brotli = web_client.get(path, headers={"Accept-Encoding": "gzip, deflate, br, zstd"})
    plain = web_client.get(path, headers={"Accept-Encoding": "gzip"})
    refused = web_client.get(path, headers={"Accept-Encoding": "br;q=0, gzip"})

    assert brotli.headers["content-encoding"] == "br"
    assert brotli.headers["content-type"] == plain.headers["content-type"]
    assert brotli.headers["content-type"].split(";")[0] in ("application/wasm", "text/javascript")
    assert brotli.headers["vary"] == plain.headers["vary"] == "Accept-Encoding"
    assert "content-encoding" not in plain.headers and "content-encoding" not in refused.headers
    assert brotli.content in (b"brotli wasm", b"brotli worker")
    assert plain.content == refused.content != brotli.content
    assert brotli.headers["etag"] != plain.headers["etag"]


def test_files_without_a_brotli_copy_are_sent_as_they_are(web_client: TestClient) -> None:
    response = web_client.get("/favicon.svg", headers={"Accept-Encoding": "br"})

    assert response.text == "<svg/>"
    assert "content-encoding" not in response.headers and "vary" not in response.headers


@pytest.mark.parametrize("path", ["/clang/llvm.core.wasm", "/assets/app-1a2b.js", "/"])
def test_unchanged_files_are_not_sent_again(web_client: TestClient, path: str) -> None:
    """A client that already has a file, by its ETag, gets a 304 without a body."""
    headers = {"Accept-Encoding": "br"}
    first = web_client.get(path, headers=headers)
    again = web_client.get(path, headers=headers | {"If-None-Match": first.headers["etag"]})

    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["etag"] == first.headers["etag"]


def test_caching_follows_whether_names_change_with_content(web_client: TestClient) -> None:
    """Fingerprinted assets are kept for good; files with fixed names are revalidated."""
    assert "immutable" in web_client.get("/assets/app-1a2b.js").headers["cache-control"]
    assert web_client.get("/clang/llvm.core.wasm").headers["cache-control"] == "no-cache"
    assert web_client.get("/stocks/AAPL").headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("path", ["/", "/clang/llvm.core.wasm", "/assets/app-1a2b.js"])
def test_head_requests_are_answered(web_client: TestClient, path: str) -> None:
    """HEAD gets GET's status and headers without the body."""
    head = web_client.head(path)
    get = web_client.get(path)

    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == get.headers["content-length"]
    assert head.headers["etag"] == get.headers["etag"]


def test_large_files_are_streamed_without_a_length(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Files over the threshold are streamed without a stated length, which servers send
    chunked, as Cloud Run allows responses over 32 MiB only that way; HEAD still reports
    the size."""
    monkeypatch.setattr(web, "STREAM_ABOVE_BYTES", 1000)
    monkeypatch.setattr(web, "CHUNK_BYTES", 512)

    response = web_client.get("/clang/llvm.core.wasm", headers={"Accept-Encoding": "gzip"})
    head = web_client.head("/clang/llvm.core.wasm", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert response.content == b"\0asm" + b"x" * 4000
    assert "content-length" not in response.headers
    assert response.headers["content-type"] == "application/wasm"
    assert head.headers["content-length"] == "4004"


@pytest.mark.parametrize(
    ("header", "accepted"),
    [
        ("gzip, deflate, br, zstd", True),
        ("br", True),
        ("BR;q=0.5", True),
        ("br;q=0", False),
        ("br; q=0.0", False),
        ("gzip, deflate", False),
        ("", False),
        ("brotli", False),
    ],
)
def test_accept_encoding_is_read_for_brotli(header: str, accepted: bool) -> None:
    assert accepts_brotli(header) is accepted


def test_only_api_responses_are_gzipped() -> None:
    """Large API answers are gzipped; other paths and event streams are left alone."""
    application = FastAPI()
    application.add_middleware(CompressApiResponses, prefix="/api/v1")
    body = "price " * 1000

    @application.get("/api/v1/big", response_class=PlainTextResponse)
    async def big() -> str:
        return body

    @application.get("/api/v1/small", response_class=PlainTextResponse)
    async def small() -> str:
        return "ok"

    @application.get("/big.txt", response_class=PlainTextResponse)
    async def outside() -> str:
        return body

    @application.get("/api/v1/stream")
    async def stream() -> StreamingResponse:
        async def events() -> AsyncIterator[str]:
            yield "event: delta\ndata: " + body + "\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    client = TestClient(application)
    gzip = {"Accept-Encoding": "gzip"}

    compressed = client.get("/api/v1/big", headers=gzip)
    assert compressed.headers["content-encoding"] == "gzip" and compressed.text == body
    assert "content-encoding" not in client.get("/api/v1/small", headers=gzip).headers
    assert "content-encoding" not in client.get("/big.txt", headers=gzip).headers
    assert "content-encoding" not in client.get("/api/v1/stream", headers=gzip).headers


# ── The frontend's types ──


def test_committed_openapi_schema_is_current() -> None:
    """web/openapi.json, which the frontend's types come from, matches the API.

    Regenerate with ``python -m app.jobs.openapi`` and ``npm run gen:api`` in ``web/``.
    """
    assert SCHEMA_PATH.read_text(encoding="utf-8") == render(openapi_schema())
