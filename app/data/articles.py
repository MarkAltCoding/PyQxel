"""The text of news articles, read from their publishers' pages, for Claude.

A provider's news feed gives a headline, a link, and at most a short summary. Claude
writes better reports from the articles themselves, so :func:`read_articles` follows the
links of the articles that matter most, keeps the story (the page's ``<article>`` or
``<main>``, without its menus, footers, and side columns), and returns it as Markdown
(:mod:`app.data.markdown`), cut to a length.

Publishers' terms do not clearly allow their pages to be read by a paid product's
server, so the server reads them itself only where the admin allows it
(:func:`app.db.app_settings.article_pages_allowed`). Otherwise each article is read by
Anthropic's web fetch tool on the user's own Anthropic key and written up in Claude's
words (:func:`app.ai.articles.read_with_web_fetch`, registered by the app at startup with
:func:`use_remote_reader`, since this layer makes no AI requests itself). A page that
cannot be read (a paywall, a refusal, a timeout, too little text) is left out quietly:
the headline and summary are still given.

The links come from providers, and a link or a redirect could point inside the server's
own network, such as the cloud's metadata service. Every hop is resolved first and
refused unless every address it names is a public one.
"""

import asyncio
import ipaddress
import logging
import socket
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin, urlsplit

import httpx2
from bs4 import BeautifulSoup

from app.data.markdown import html_to_markdown
from app.models.news import NewsHeadline

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS: float = 8.0
"""How long one page may take; a slow publisher is left out rather than waited for."""

PAGE_SECONDS: float = 15.0
"""How long one page may take in all, redirects included."""

MAX_PAGE_BYTES: int = 3_000_000
"""Pages larger than this are not read."""

MAX_REDIRECTS: int = 5
"""Redirects followed from a provider's link, which often passes through a tracker."""

PAGES_AT_ONCE: int = 8
"""Pages fetched together."""

MIN_ARTICLE_CHARS: int = 400
"""Less text than this is a paywall, a cookie notice, or a video page, not an article."""

CACHE_SIZE: int = 512
"""Articles kept in memory, so the same story is not fetched for every report."""

USER_AGENT: str = "Mozilla/5.0 (compatible; PyQxel news reader)"

PAGE_FURNITURE: list[str] = [
    "nav",
    "header",
    "footer",
    "aside",
    "figure",
    "figcaption",
]
"""Parts of a page that are not the story."""

_cache: OrderedDict[str, str | None] = OrderedDict()
"""Link -> the article's full Markdown, or ``None`` when the page could not be read."""


def clear_article_cache() -> None:
    """Forget every article read, for tests."""
    _cache.clear()


async def _public(url: str) -> bool:
    """Whether ``url`` is http(s) and every address its host resolves to is public."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            parts.hostname,
            parts.port or (443 if parts.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except OSError:
        return False
    for *_, address in infos:
        ip = ipaddress.ip_address(address[0])
        if not ip.is_global or ip.is_multicast:
            return False
    return bool(infos)


def story_markdown(html: str) -> str:
    """The story on an article page as Markdown: its ``<article>`` (the longest, when a
    page has several), else its ``<main>``, else its body, without menus and footers."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(PAGE_FURNITURE):
        tag.decompose()
    articles = soup.find_all("article")
    root = (
        max(articles, key=lambda tag: len(tag.get_text()))
        if articles
        else soup.find("main") or soup.body or soup
    )
    return html_to_markdown(root)


async def _page(client: httpx2.AsyncClient, url: str) -> str | None:
    """The HTML at ``url``, following redirects that stay on public addresses."""
    for _ in range(MAX_REDIRECTS + 1):
        if not await _public(url):
            return None
        async with client.stream("GET", url) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    return None
                url = urljoin(url, location)
                continue
            if response.status_code != 200:
                return None
            if "html" not in response.headers.get("content-type", ""):
                return None
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_PAGE_BYTES:
                    return None
            return body.decode(response.encoding or "utf-8", errors="replace")
    return None


async def _read(client: httpx2.AsyncClient, url: str, slots: asyncio.Semaphore) -> str | None:
    """One article's Markdown, from memory or its page; ``None`` when it cannot be read."""
    if url in _cache:
        _cache.move_to_end(url)
        return _cache[url]
    async with slots:
        try:
            # A page that trickles in is cut off as a whole, not only between bytes.
            html = await asyncio.wait_for(_page(client, url), PAGE_SECONDS)
            text = None if html is None else await asyncio.to_thread(story_markdown, html)
        except (httpx2.HTTPError, TimeoutError, UnicodeError, ValueError) as exc:
            logger.info("The article at %s could not be read: %s", url, exc)
            text = None
    if text is not None and len(text) < MIN_ARTICLE_CHARS:
        text = None
    _cache[url] = text
    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)
    return text


def _cut(text: str, limit: int) -> str:
    """``text`` cut to ``limit`` characters at a paragraph or word boundary, marked."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    boundary = head.rfind("\n\n")
    if boundary < limit // 2:
        boundary = head.rfind(" ")
    return head[: max(boundary, 1)].rstrip() + "\n\n[…the rest of the article is left out]"


RemoteReader = Callable[[list[str], int], Awaitable[dict[str, str]]]
"""Reads articles by link without the server downloading them: the web fetch tool."""

_remote_reader: RemoteReader | None = None

REDIRECT_HOSTS: frozenset[str] = frozenset({"finnhub.io"})
"""Providers whose news links only redirect to the publisher's page, asked once for where
they lead, so the tool is handed the article's own address."""


def use_remote_reader(reader: RemoteReader | None) -> None:
    """Read articles with ``reader`` when the server does not read pages itself."""
    global _remote_reader
    _remote_reader = reader


async def _resolved(client: httpx2.AsyncClient, url: str) -> str:
    """Where a provider's redirecting news link leads; ``url`` itself otherwise. Only the
    provider is asked, never the publisher."""
    if urlsplit(url).hostname not in REDIRECT_HOSTS:
        return url
    try:
        response = await client.get(url)
    except httpx2.HTTPError:
        return url
    location = response.headers.get("location") if response.is_redirect else None
    target = urljoin(url, location) if location else url
    return target if urlsplit(target).scheme in ("http", "https") else url


async def _read_remotely(urls: list[str], max_chars: int) -> dict[str, str]:
    """The articles at ``urls``, read by the remote reader, by their links as given."""
    if _remote_reader is None:
        return {}
    async with httpx2.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False) as client:
        targets = await asyncio.gather(*(_resolved(client, url) for url in urls))
    by_target = dict(zip(targets, urls, strict=True))
    try:
        texts = await _remote_reader(list(by_target), max_chars)
    except Exception as exc:  # Articles add to a report; a failure never stops it.
        logger.warning("Articles could not be read remotely: %s", exc)
        return {}
    return {by_target[target]: _cut(text, max_chars) for target, text in texts.items() if text}


async def read_articles(urls: list[str], max_chars: int) -> dict[str, str]:
    """The text of each article in ``urls`` that could be read, as Markdown cut to
    ``max_chars``, by link: read by the server itself where the admin allows it, else by
    the remote reader (Anthropic's web fetch tool on the user's key)."""
    from app.db.app_settings import article_pages_allowed  # Imports the database.

    wanted = list(dict.fromkeys(url for url in urls if url))
    if not wanted:
        return {}
    if not await article_pages_allowed():
        return await _read_remotely(wanted, max_chars)
    slots = asyncio.Semaphore(PAGES_AT_ONCE)
    async with httpx2.AsyncClient(
        timeout=TIMEOUT_SECONDS,
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
    ) as client:
        texts = await asyncio.gather(*(_read(client, url, slots) for url in wanted))
    return {url: _cut(text, max_chars) for url, text in zip(wanted, texts) if text}


async def with_article_texts(
    groups: list[list[NewsHeadline]], each: int, max_chars: int, total: int | None = None
) -> list[list[NewsHeadline]]:
    """``groups`` of headlines (one company's, or the market's), with the text of the
    ``each`` articles that matter most in every group, as far as ``total`` articles in all
    allows, filled in where their pages could be read. Every group's first article is
    read before any group's second."""
    chosen: list[str] = []
    for rank in range(each):
        for group in groups:
            for headline in group:
                if headline.rank == rank and headline.link:
                    chosen.append(headline.link)
    if total is not None:
        chosen = chosen[:total]
    texts = await read_articles(chosen, max_chars)
    if not texts:
        return groups
    return [
        [
            (
                headline.model_copy(update={"text": texts[headline.link]})
                if headline.link is not None and headline.link in texts
                else headline
            )
            for headline in group
        ]
        for group in groups
    ]
