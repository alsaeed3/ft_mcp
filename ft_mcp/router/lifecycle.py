from enum import Enum
from typing import Any

from ft_mcp.log import get_logger

logger = get_logger("ft_mcp.lifecycle")

SUPPORTED_PROTOCOL_VERSIONS = ["2025-11-25", "2025-03-26"]
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]

SERVER_INFO = {
    "name": "ft_mcp_server",
    "version": "1.0.0",
}


class SessionState(Enum):
    UNINITIALIZED = "uninitialized"
    INITIALIZING = "initializing"
    OPERATIONAL = "operational"


class LifecycleManager:
    def __init__(self) -> None:
        self.state = SessionState.UNINITIALIZED
        self._capabilities: dict[str, Any] = {"tools": {}}

    def add_capability(self, key: str, value: Any) -> None:
        self._capabilities[key] = value

    async def handle_initialize(self, msg: dict) -> dict:
        params = msg.get("params", {})
        client_version = params.get("protocolVersion", "")

        if client_version in SUPPORTED_PROTOCOL_VERSIONS:
            protocol_version = client_version
        else:
            protocol_version = LATEST_PROTOCOL_VERSION
            logger.info(
                "client version %s not supported, replying with %s",
                client_version,
                protocol_version,
            )

        self.state = SessionState.INITIALIZING

        return {
            "result": {
                "protocolVersion": protocol_version,
                "serverInfo": dict(SERVER_INFO),
                "capabilities": dict(self._capabilities),
            }
        }

    async def handle_initialized(self, msg: dict) -> None:
        if self.state == SessionState.UNINITIALIZED:
            logger.warning("notifications/initialized received before initialize")
        self.state = SessionState.OPERATIONAL
        logger.info("session is now OPERATIONAL")

    def check_method_allowed(self, method: str) -> str | None:
        if method == "initialize":
            return None
        if method == "notifications/initialized":
            return None
        if self.state != SessionState.OPERATIONAL:
            return "Server not initialized. Call initialize first."
        return None
