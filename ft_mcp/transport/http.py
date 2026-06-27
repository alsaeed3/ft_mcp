from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Any

from aiohttp import web

from ft_mcp.log import get_logger
from ft_mcp.router.dispatch import Router

logger = get_logger("ft_mcp.transport.http")

# DNS-rebinding protection. Browsers always send an Origin header; non-browser
# clients (MCP Inspector, curl) send none and are allowed. Any browser origin
# must be loopback, unless explicitly whitelisted in ALLOWED_ORIGINS.
ALLOWED_ORIGINS: list[str] = []
_LOOPBACK_ORIGIN = re.compile(r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$")

routes = web.RouteTableDef()


class SSESession:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.queue: asyncio.Queue[dict] = asyncio.Queue()
        self._response: web.StreamResponse | None = None

    async def send_event(self, event: str, data: str) -> None:
        payload = f"event: {event}\ndata: {data}\n\n"
        if self._response is not None:
            try:
                await self._response.write(payload.encode("utf-8"))
            except (ConnectionResetError, ConnectionAbortedError):
                sessions.pop(self.session_id, None)

    def bind_response(self, response: web.StreamResponse) -> None:
        self._response = response


sessions: dict[str, SSESession] = {}


def is_origin_allowed(origin: str) -> bool:
    if not origin:
        return True
    if _LOOPBACK_ORIGIN.match(origin):
        return True
    return origin in ALLOWED_ORIGINS


def cors_headers(origin: str) -> dict[str, str]:
    """Echo only the validated origin — never a wildcard."""
    if origin:
        return {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}
    return {}


def reject_bad_origin(request: web.Request) -> str:
    origin = request.headers.get("Origin", "")
    if not is_origin_allowed(origin):
        logger.warning("rejected Origin: %s", origin)
        raise web.HTTPForbidden(reason=f"Origin not allowed: {origin}")
    return origin


def make_session_id() -> str:
    return str(uuid.uuid4())


@routes.get("/mcp/sse")
async def handle_sse(request: web.Request) -> web.StreamResponse:
    origin = reject_bad_origin(request)

    session_id = request.query.get("sessionId", make_session_id())
    session = SSESession(session_id)
    sessions[session_id] = session

    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            **cors_headers(origin),
        },
    )
    await response.prepare(request)
    session.bind_response(response)

    endpoint_url = f"/mcp/messages?sessionId={session_id}"
    await session.send_event("endpoint", endpoint_url)

    logger.info("SSE session opened: %s", session_id)

    try:
        while True:
            try:
                msg = await asyncio.wait_for(session.queue.get(), timeout=30)
                await session.send_event("message", json.dumps(msg, ensure_ascii=False))
            except asyncio.TimeoutError:
                await session.send_event("ping", "")
    except (ConnectionResetError, ConnectionAbortedError, asyncio.CancelledError):
        pass
    finally:
        logger.info("SSE session closed: %s", session_id)


@routes.post("/mcp/messages")
async def handle_messages(request: web.Request) -> web.Response:
    origin = reject_bad_origin(request)
    cors = cors_headers(origin)

    session_id = request.query.get("sessionId", "")
    if session_id not in sessions:
        return web.json_response(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32000, "message": "Invalid session"}},
            status=400,
            headers=cors,
        )

    session = sessions[session_id]

    try:
        body = await request.text()
    except Exception:
        return web.json_response(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
            status=400,
            headers=cors,
        )

    router: Router | None = request.app.get("router")
    if router is None:
        return web.json_response(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": "Server not ready"}},
            status=500,
            headers=cors,
        )

    responses = await router.dispatch(body)
    for resp in responses:
        await session.queue.put(resp)

    return web.json_response({"ok": True}, headers=cors)


@routes.options("/mcp/messages")
@routes.options("/mcp/sse")
async def handle_preflight(request: web.Request) -> web.Response:
    origin = reject_bad_origin(request)
    return web.Response(
        headers={
            **cors_headers(origin),
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Origin",
        }
    )


async def create_http_server(router: Router, host: str = "127.0.0.1", port: int = 8080) -> web.Application:
    app = web.Application()
    app["router"] = router
    app.add_routes(routes)
    return app
