"""Tests for reading the articles that matter most through Anthropic's web fetch tool: what
the model's answer is split into, and which reader a request gets."""

import asyncio
from typing import Any

import pytest

from app.ai.articles import UNREADABLE, sections
from app.core.credentials import ProviderKeys, use_provider_keys
from app.data import articles

READER = articles.read_articles


def test_each_article_is_taken_from_under_its_own_link() -> None:
    text = (
        "=== https://news.example/a\n# Acme beats\nRevenue rose 12%.\n\n"
        f"=== https://news.example/b\n{UNREADABLE}\n"
        "=== https://elsewhere.example/c\nNot asked for.\n"
    )

    found = sections(text, ["https://news.example/a", "https://news.example/b"])

    assert found == {"https://news.example/a": "# Acme beats\nRevenue rose 12%."}


@pytest.fixture
def real_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    """The article reader itself, which the suite otherwise replaces."""
    monkeypatch.setattr(articles, "read_articles", READER)


@pytest.mark.usefixtures("real_reader")
def test_without_the_admins_leave_the_web_fetch_tool_reads_the_articles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[Any] = []

    async def remote(urls: list[str], max_chars: int) -> dict[str, str]:
        asked.append(urls)
        return {url: "Written up." for url in urls}

    async def no_pages(*_: object) -> None:
        raise AssertionError("the server must not read the page itself")

    monkeypatch.setattr(articles, "_page", no_pages)
    articles.use_remote_reader(remote)
    try:
        use_provider_keys(ProviderKeys())
        found = asyncio.run(articles.read_articles(["https://news.example/a"], 100))
    finally:
        articles.use_remote_reader(None)

    assert asked == [["https://news.example/a"]]
    assert found == {"https://news.example/a": "Written up."}


@pytest.mark.usefixtures("real_reader")
def test_the_admins_own_requests_read_the_page_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    async def remote(urls: list[str], max_chars: int) -> dict[str, str]:
        raise AssertionError("the web fetch tool must not be used")

    async def page(client: object, url: str) -> str:
        return "<article><p>" + "Acme raised its outlook for the year. " * 20 + "</p></article>"

    async def public(url: str) -> bool:
        return True

    monkeypatch.setattr(articles, "_page", page)
    monkeypatch.setattr(articles, "_public", public)
    articles.clear_article_cache()
    articles.use_remote_reader(remote)
    try:
        use_provider_keys(ProviderKeys(restricted_sources=True))
        found = asyncio.run(articles.read_articles(["https://news.example/a"], 2000))
    finally:
        articles.use_remote_reader(None)
        use_provider_keys(ProviderKeys())

    assert "raised its outlook" in found["https://news.example/a"]


@pytest.mark.usefixtures("real_reader")
def test_the_admin_reads_pages_directly_while_the_server_uses_restricted_sources() -> None:
    from app.db.app_settings import article_pages_allowed

    try:
        use_provider_keys(ProviderKeys(admin=True))
        assert asyncio.run(article_pages_allowed()), "the server-wide switch is on by default"
        use_provider_keys(ProviderKeys())
        assert not asyncio.run(article_pages_allowed()), "others use the web fetch tool"
    finally:
        use_provider_keys(ProviderKeys())
