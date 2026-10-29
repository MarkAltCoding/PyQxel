"""A cap on how large a request body may be, enforced before it is read into memory.

The largest bodies the API takes, a custom backtest's positions or a pasted answer with
its context, are well under a megabyte or two; anything far beyond is refused with 413,
on its declared ``Content-Length`` or, when it has none, as soon as the bytes received
pass the cap.
"""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_BODY_BYTES: int = 4 * 1024 * 1024
"""Largest request body accepted."""


class BodySizeLimit:
    """ASGI middleware refusing request bodies over :data:`MAX_BODY_BYTES`."""

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def _refuse(self, send: Send) -> None:
        body = b'{"detail":"The request is too large."}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None:
            try:
                too_large = int(declared) > self.max_bytes
            except ValueError:
                too_large = True
            if too_large:
                await self._refuse(send)
                return

        received = 0
        refused = False

        async def counted_receive() -> Message:
            # Past the cap, the refusal is sent here and the app is told the client left,
            # so it stops reading and nothing more is buffered.
            nonlocal received, refused
            if refused:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    refused = True
                    await self._refuse(send)
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            if not refused:
                await send(message)

        try:
            await self.app(scope, counted_receive, guarded_send)
        except Exception:
            # The app may raise on the disconnect it was told of; the refusal is sent.
            if not refused:
                raise
