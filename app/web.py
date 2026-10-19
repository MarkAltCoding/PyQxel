"""Serving the built web frontend, and compressing what the app sends.

The build (``npm run build`` in ``web/``) writes a Brotli-compressed copy beside each
large compressible file, ``name.br``, and browsers that accept Brotli get that copy: the
in-browser C++ compiler drops from 105 MB to 17 MB this way, and Pyodide's runtime from
11 MB to 3 MB. Files are revalidated with their ETag, so a returning visitor downloads
only what changed. Cloud Run refuses HTTP/1 responses over 32 MiB that state their
length, so larger files are streamed in chunks instead.

API responses are compressed with gzip as they are sent. Static files are not: their
compressed copies are made once, at build time, rather than on every request.
"""

import hashlib
import mimetypes
import os
from collections.abc import AsyncIterator, Mapping
from email.utils import formatdate
from pathlib import Path

import anyio
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers
from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

WORKER_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; connect-src 'self'"
)
"""The policy a Web Worker started from the app's JavaScript runs under.

A worker takes its policy from its own script's response, so this bounds the workers
that run custom strategies: they may load scripts and fetch only from this origin, where
the app serves its copies of Pyodide and Clang, compile WebAssembly, and nothing else.
Without ``'unsafe-eval'``, strategy code cannot reach ``eval`` or ``new Function``
through Pyodide's ``js`` module, nor import code from elsewhere. Browsers ignore the
header on the scripts a page loads, so the rest of the app is unaffected.
"""

STREAM_ABOVE_BYTES = 30 * 1024 * 1024
"""Files larger than this are sent in chunks without a stated length: Cloud Run refuses
HTTP/1 responses over 32 MiB that state their length."""
CHUNK_BYTES = 1024 * 1024
"""Bytes read and sent at a time when streaming a file."""

IMMUTABLE = "public, max-age=31536000, immutable"
"""Caching for files whose names change with their content, such as Vite's assets."""
REVALIDATE = "no-cache"
"""Caching for files whose names do not change: kept, but checked on every use."""

NOT_WEB_PREFIXES: tuple[str, ...] = ("api/", "health", "docs", "redoc", "openapi.json")
"""Paths the web frontend never answers, so a mistyped API route still returns 404."""


def accepts_brotli(accept_encoding: str) -> bool:
    """Whether an ``Accept-Encoding`` header accepts Brotli, ``br``, at a quality above 0."""
    for part in accept_encoding.split(","):
        coding, *params = part.split(";")
        if coding.strip().lower() != "br":
            continue
        quality = 1.0
        for param in params:
            name, _, value = param.partition("=")
            if name.strip().lower() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        return quality > 0
    return False


def _etag(stat_result: os.stat_result) -> str:
    """The ETag Starlette's ``FileResponse`` gives a file, so both agree."""
    base = f"{stat_result.st_mtime}-{stat_result.st_size}"
    return f'"{hashlib.md5(base.encode(), usedforsecurity=False).hexdigest()}"'


async def _chunks(path: Path) -> AsyncIterator[bytes]:
    """The file's bytes, :data:`CHUNK_BYTES` at a time."""
    async with await anyio.open_file(path, "rb") as file:
        while chunk := await file.read(CHUNK_BYTES):
            yield chunk


def file_response(
    file: Path, request_headers: Headers, method: str, headers: Mapping[str, str] | None = None
) -> Response:
    """Serve ``file``, or its Brotli copy to a client that accepts Brotli.

    Answers a matching ``If-None-Match`` with 304, and streams a file over
    :data:`STREAM_ABOVE_BYTES` in chunks. ``headers`` are added to the response.
    """
    media_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
    out = dict(headers or {})
    served = file
    compressed = file.with_name(file.name + ".br")
    if compressed.is_file():
        out["Vary"] = "Accept-Encoding"
        if accepts_brotli(request_headers.get("accept-encoding", "")):
            served = compressed
            out["Content-Encoding"] = "br"
    stat_result = served.stat()
    out["ETag"] = _etag(stat_result)
    out["Last-Modified"] = formatdate(stat_result.st_mtime, usegmt=True)

    sent_tags = {tag.strip() for tag in request_headers.get("if-none-match", "").split(",")}
    if out["ETag"] in sent_tags or "*" in sent_tags:
        out.pop("Content-Encoding", None)
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=out)
    if stat_result.st_size > STREAM_ABOVE_BYTES:
        if method == "HEAD":
            response = Response(media_type=media_type, headers=out)
            response.headers["content-length"] = str(stat_result.st_size)
            return response
        return StreamingResponse(_chunks(served), media_type=media_type, headers=out)
    return FileResponse(served, media_type=media_type, headers=out, stat_result=stat_result)


class AssetFiles(StaticFiles):
    """Vite's fingerprinted assets: cached for good, with the worker policy on JavaScript."""

    def file_response(
        self,
        full_path: str | os.PathLike[str],
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        headers = {"Cache-Control": IMMUTABLE}
        if str(full_path).endswith(".js"):
            headers["Content-Security-Policy"] = WORKER_CONTENT_SECURITY_POLICY
        return file_response(Path(full_path), Headers(scope=scope), scope["method"], headers)


def mount_web(application: FastAPI, dist: Path) -> bool:
    """Serve the built web frontend in ``dist`` at ``/``, if it has been built.

    Files are served as they are, or as their Brotli copies; any other path gets
    ``index.html``, so the frontend's own routes (``/stocks/AAPL``) load on refresh.
    Register this after every API route.

    Returns:
        Whether the frontend was found and mounted.
    """
    index = dist / "index.html"
    if not index.is_file():
        return False
    root = dist.resolve()
    if (dist / "assets").is_dir():
        application.mount("/assets", AssetFiles(directory=dist / "assets"), name="assets")

    @application.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def web(request: Request, path: str) -> Response:
        if path.startswith(NOT_WEB_PREFIXES):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
        file = (dist / path).resolve()
        if not (path and file.is_file() and file.is_relative_to(root)):
            # Any other path is one of the frontend's own routes.
            file = index
        # Revalidated on every use: index.html so a deploy's new asset names are picked up
        # at once, and the vendored Pyodide and Clang, whose names stay the same.
        return file_response(file, request.headers, request.method, {"Cache-Control": REVALIDATE})

    return True


class CompressApiResponses:
    """Gzip API responses of a kilobyte or more for clients that accept it.

    Only paths under ``prefix`` are compressed: static files have Brotli copies made at
    build time, and gzipping them, wheels and archives included, on every request would
    spend the instance's CPU for little. Streamed analyses are left as they are.
    """

    def __init__(self, app: ASGIApp, prefix: str) -> None:
        self.app = app
        self.prefix = prefix
        self.gzip = GZipMiddleware(app, minimum_size=1024, compresslevel=6)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith(self.prefix):
            await self.gzip(scope, receive, send)
        else:
            await self.app(scope, receive, send)
