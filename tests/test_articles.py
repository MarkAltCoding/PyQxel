"""Tests for reading the articles behind the news for Claude: which are chosen, how a page
becomes Markdown, and that no link can reach inside the server's own network."""

import asyncio
from datetime import datetime, timezone

import pytest

from app.data import articles
from app.data.markdown import html_to_markdown
from app.models.news import NewsHeadline


def _headline(name: str, rank: int) -> NewsHeadline:
    return NewsHeadline(
        published_at=datetime(2026, 11, 23, tzinfo=timezone.utc), source="Wire", headline=name
    ).linked(f"https://news.example/{name}", rank)


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/computeMetadata/v1/",
        "http://127.0.0.1:8000/health",
        "http://10.0.0.5/",
        "file:///etc/passwd",
        "http://[::1]/",
    ],
)
def test_links_inside_the_servers_network_are_refused(url: str) -> None:
    assert not asyncio.run(articles._public(url))


def test_each_groups_first_article_is_read_before_any_groups_second(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[str] = []

    async def read(urls: list[str], max_chars: int) -> dict[str, str]:
        asked.extend(urls)
        return {url: f"text of {url.rsplit('/', 1)[-1]}" for url in urls}

    monkeypatch.setattr(articles, "read_articles", read)
    groups = [[_headline("a2", 1), _headline("a1", 0)], [_headline("b1", 0), _headline("b2", 1)]]

    read_groups = asyncio.run(articles.with_article_texts(groups, 2, 100, total=3))

    assert [url.rsplit("/", 1)[-1] for url in asked] == ["a1", "b1", "a2"]
    assert [h.text for h in read_groups[0]] == ["text of a2", "text of a1"]
    assert [h.text for h in read_groups[1]] == ["text of b1", None]
    assert "url" not in read_groups[0][0].model_dump(), "the link is never stored"


def test_a_page_keeps_its_story_as_markdown_without_its_menus() -> None:
    html = """<html><body><nav><a>Home</a><a>Markets</a></nav>
    <article><h1>Acme beats estimates</h1><p>Revenue rose 12%.</p>
    <table><tr><th>Quarter</th><th>Revenue</th></tr><tr><td>Q3</td><td>$1.2B</td></tr>
    <tr><td>Q2</td><td>$1.1B</td></tr></table></article><footer>© News</footer></body></html>"""

    text = articles.story_markdown(html)

    assert text.startswith("# Acme beats estimates")
    assert "| Q3 | $1.2B |" in text and "Home" not in text and "© News" not in text


def test_a_filing_table_keeps_its_columns_with_signs_joined_to_numbers() -> None:
    html = """<table><tr><td></td><td colspan="3">2025</td><td colspan="3">2024</td></tr>
    <tr><td>Revenue</td><td>$</td><td>1,234</td><td></td><td>$</td><td>1,000</td><td></td></tr>
    <tr><td>Net loss</td><td></td><td>(12</td><td>)</td><td></td><td>(3</td><td>)</td></tr>
    </table>"""

    assert html_to_markdown(html).splitlines() == [
        "| | 2025 | 2024 |",
        "| --- | --- | --- |",
        "| Revenue | $1,234 | $1,000 |",
        "| Net loss | (12) | (3) |",
    ]
