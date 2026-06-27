import argparse
import asyncio
import sys

try:
    from aiohttp import web
except ImportError:
    web = None

from ft_mcp.log import get_logger
from ft_mcp.router.dispatch import Router
from ft_mcp.router.errors import METHOD_NOT_FOUND, INVALID_PARAMS, make_error
from ft_mcp.router.lifecycle import LifecycleManager
from ft_mcp.tools.file_hash import tool as file_hash_tool
from ft_mcp.tools.prompts import init as init_prompts, list_prompts, get_prompt, list_resources, read_resource
from ft_mcp.tools.registry import get_registry
from ft_mcp.tools.system_time import tool as system_time_tool
from ft_mcp.tools.telemetry import telemetry_wrapper

logger = get_logger("ft_mcp")


async def handle_tools_call(msg: dict) -> dict:
    params = msg.get("params", {})
    name = params.get("name", "")
    arguments = params.get("arguments", {})

    registry = get_registry()
    tool = registry.get(name)
    if tool is None:
        return {
            "error": make_error(METHOD_NOT_FOUND, f"Unknown tool: {name}"),
        }

    error = validate_schema(arguments, tool.input_schema)
    if error is not None:
        return {
            "error": make_error(INVALID_PARAMS, error),
        }

    try:
        result = await tool.handler(arguments)
        return {
            "result": {
                "content": [{"type": "text", "text": result}],
            }
        }
    except Exception as exc:
        return {
            "result": {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            }
        }


def validate_schema(arguments: dict, schema: dict) -> str | None:
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    additional = schema.get("additionalProperties", True)

    for prop in required:
        if prop not in arguments:
            return f"Missing required argument: {prop}"

    if not additional:
        for key in arguments:
            if key not in properties:
                return f"Unexpected argument: {key}"

    for key, value in arguments.items():
        prop_schema = properties.get(key)
        if prop_schema is None:
            continue
        type_error = check_type(value, prop_schema)
        if type_error:
            return f"Argument '{key}': {type_error}"

    return None


def check_type(value, schema: dict) -> str | None:
    json_type = schema.get("type")
    if json_type == "string":
        if not isinstance(value, str):
            return f"expected string, got {type(value).__name__}"
        if "enum" in schema and value not in schema["enum"]:
            return f"expected one of {schema['enum']}, got '{value}'"
    elif json_type == "number":
        if not isinstance(value, (int, float)):
            return f"expected number, got {type(value).__name__}"
    elif json_type == "boolean":
        if not isinstance(value, bool):
            return f"expected boolean, got {type(value).__name__}"
    elif json_type == "array":
        if not isinstance(value, list):
            return f"expected array, got {type(value).__name__}"
    elif json_type == "object":
        if not isinstance(value, dict):
            return f"expected object, got {type(value).__name__}"
    return None


def build_router() -> Router:
    lifecycle = LifecycleManager()
    router = Router(lifecycle=lifecycle)
    registry = get_registry()
    registry.register(system_time_tool)
    registry.register(file_hash_tool)

    init_prompts()

    lifecycle.add_capability("prompts", {})
    lifecycle.add_capability("resources", {})

    router.register("initialize", telemetry_wrapper(lifecycle.handle_initialize))
    router.register_notification(
        "notifications/initialized", lifecycle.handle_initialized
    )

    async def handle_tools_list(msg: dict) -> dict:
        return {"result": {"tools": registry.list()}}

    router.register("tools/list", telemetry_wrapper(handle_tools_list))
    router.register("tools/call", telemetry_wrapper(handle_tools_call))

    async def handle_prompts_list(msg: dict) -> dict:
        return {"result": {"prompts": list_prompts()}}

    router.register("prompts/list", telemetry_wrapper(handle_prompts_list))

    async def handle_prompts_get(msg: dict) -> dict:
        params = msg.get("params", {})
        name = params.get("name", "")
        arguments = params.get("arguments")
        result = await get_prompt(name, arguments)
        if result is None:
            return {"error": make_error(METHOD_NOT_FOUND, f"Unknown prompt: {name}")}
        return {"result": result}

    router.register("prompts/get", telemetry_wrapper(handle_prompts_get))

    async def handle_resources_list(msg: dict) -> dict:
        return {"result": {"resources": list_resources()}}

    router.register("resources/list", telemetry_wrapper(handle_resources_list))

    async def handle_resources_read(msg: dict) -> dict:
        params = msg.get("params", {})
        uri = params.get("uri", "")
        result = await read_resource(uri)
        if result is None:
            return {"error": make_error(METHOD_NOT_FOUND, f"Unknown resource: {uri}")}
        return {"result": result}

    router.register("resources/read", telemetry_wrapper(handle_resources_read))

    return router


async def run_stdio() -> None:
    logger.info("starting stdio transport")
    from ft_mcp.transport.stdio import create_stdio_transport

    read_line, write_message = await create_stdio_transport()
    router = build_router()

    try:
        while True:
            line = await read_line()
            if line is None:
                break
            if not line:
                continue
            responses = await router.dispatch(line)
            for resp in responses:
                write_message(resp)
    finally:
        logger.info("shutting down")


async def run_http(host: str, port: int) -> None:
    logger.info("starting HTTP transport on %s:%s", host, port)
    from ft_mcp.transport.http import create_http_server

    router = build_router()
    app = await create_http_server(router, host=host, port=port)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("HTTP server running at http://%s:%s", host, port)

    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        await runner.cleanup()
        logger.info("HTTP server shut down")


def entry() -> None:
    parser = argparse.ArgumentParser(description="ft_mcp MCP server")
    parser.add_argument("--http", action="store_true", help="Run HTTP transport instead of stdio")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="HTTP port (default: 8080)")
    args = parser.parse_args()

    try:
        if args.http:
            asyncio.run(run_http(args.host, args.port))
        else:
            asyncio.run(run_stdio())
    except KeyboardInterrupt:
        logger.info("received keyboard interrupt")
    except Exception:
        logger.exception("unhandled exception in main")
        sys.exit(1)


if __name__ == "__main__":
    entry()
