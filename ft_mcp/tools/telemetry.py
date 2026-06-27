import time
from typing import Any

from ft_mcp.log import get_logger

logger = get_logger("ft_mcp.telemetry")


def log_activity(method: str, params: dict[str, Any] | None, duration: float, result: dict | None, error: str | None = None) -> None:
    info: dict[str, Any] = {
        "activity": method,
        "duration_ms": round(duration * 1000, 1),
    }
    if params:
        tool_name = params.get("name")
        if tool_name:
            info["tool"] = tool_name
    if error:
        info["error"] = error
    logger.info("activity %s", info)


def telemetry_wrapper(handler):
    async def wrapped(msg: dict) -> dict | None:
        method = msg.get("method", "unknown")
        params = msg.get("params")
        start = time.monotonic()
        try:
            result = await handler(msg)
            duration = time.monotonic() - start
            if result is not None:
                if "error" in result:
                    log_activity(method, params, duration, result, result["error"].get("message"))
                else:
                    log_activity(method, params, duration, result)
            return result
        except Exception as exc:
            duration = time.monotonic() - start
            log_activity(method, params, duration, None, str(exc))
            raise

    return wrapped
