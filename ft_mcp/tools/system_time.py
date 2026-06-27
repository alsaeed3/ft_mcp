from datetime import datetime, timezone

from ft_mcp.tools.registry import Tool

INPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


async def get_system_time(arguments: dict) -> str:
    return datetime.now(timezone.utc).isoformat()


tool = Tool(
    name="get_system_time",
    description="Get the current system time in ISO 8601 format",
    input_schema=INPUT_SCHEMA,
    handler=get_system_time,
)
