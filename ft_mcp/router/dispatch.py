import json
from typing import Any, Callable, Coroutine

from ft_mcp.log import get_logger
from ft_mcp.router.errors import (
    INVALID_REQUEST,
    make_error,
)
from ft_mcp.router.lifecycle import LifecycleManager

logger = get_logger("ft_mcp.router")

Handler = Callable[[dict], Coroutine[Any, Any, dict | None]]


class Router:
    def __init__(self, lifecycle: LifecycleManager | None = None) -> None:
        self._handlers: dict[str, Handler] = {}
        self._notification_handlers: dict[str, Handler] = {}
        self.lifecycle = lifecycle or LifecycleManager()

    def register(self, method: str, handler: Handler) -> None:
        self._handlers[method] = handler

    def register_notification(self, method: str, handler: Handler) -> None:
        self._notification_handlers[method] = handler

    async def dispatch(self, line: str) -> list[dict]:
        responses: list[dict] = []

        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            logger.warning("parse error: %s", exc)
            responses.append({
                "jsonrpc": "2.0",
                "id": None,
                "error": make_error(-32700, "Parse error", str(exc)),
            })
            return responses

        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            logger.warning("invalid request: %s", msg)
            responses.append({
                "jsonrpc": "2.0",
                "id": None,
                "error": make_error(-32600, "Invalid Request"),
            })
            return responses

        msg_id = msg.get("id")
        method = msg.get("method")

        if msg_id is not None and isinstance(method, str):
            result = await self._handle_request(msg_id, method, msg)
            if result is not None:
                responses.append(result)
        elif method is not None and msg_id is None:
            await self._handle_notification(method, msg)
        elif msg_id is not None and method is None:
            logger.debug("ignoring response message: %s", msg)
        else:
            responses.append({
                "jsonrpc": "2.0",
                "id": None,
                "error": make_error(-32600, "Invalid Request"),
            })

        return responses

    async def _handle_request(self, msg_id: Any, method: str, msg: dict) -> dict | None:
        # Resolve the method first: an unknown method is -32601 regardless of
        # session state, so it must not be masked by the not-initialized guard.
        handler = self._handlers.get(method)
        if handler is None:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": make_error(-32601, f"Method not found: {method}"),
            }

        block_reason = self.lifecycle.check_method_allowed(method)
        if block_reason is not None:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": make_error(INVALID_REQUEST, block_reason),
            }

        try:
            result = await handler(msg)
            if result is None:
                return None
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                **result,
            }
        except Exception as exc:
            logger.exception("handler error for %s", method)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": make_error(-32603, "Internal error", str(exc)),
            }

    async def _handle_notification(self, method: str, msg: dict) -> None:
        handler = self._notification_handlers.get(method)
        if handler is None:
            logger.debug("unhandled notification: %s", method)
            return
        try:
            await handler(msg)
        except Exception:
            logger.exception("notification handler error for %s", method)
