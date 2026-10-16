from contextlib import suppress

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class _BodyTooLarge(Exception):
    pass


class BodySizeLimit:
    """Rejects request bodies over `max_bytes` while they stream in, without trusting
    Content-Length, so an oversized upload is refused before it is buffered to disk."""

    def __init__(self, app: ASGIApp, *, max_bytes: int, path_prefix: str) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.path_prefix = path_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(self.path_prefix):
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(scope, receive, send)
            return

        received = 0
        exceeded = False
        started = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    exceeded = True
                    raise _BodyTooLarge()
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started
            # Frameworks may turn the receive error into their own response; replace it.
            if exceeded:
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        with suppress(_BodyTooLarge):
            await self.app(scope, limited_receive, guarded_send)
        if exceeded and not started:
            await self._reject(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            {
                "error": {
                    "code": "payload_too_large",
                    "message": "Upload exceeds the size limit.",
                    "trace_id": None,
                }
            },
            status_code=413,
        )
        await response(scope, receive, send)
