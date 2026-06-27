import asyncio
import json
import sys

from ft_mcp.log import get_logger

logger = get_logger("ft_mcp.transport")


async def create_stdio_transport():
    loop = asyncio.get_running_loop()
    writer = sys.stdout.buffer

    async def read_line():
        line = await loop.run_in_executor(None, sys.stdin.buffer.readline)
        if line == b"":
            return None
        return line.decode("utf-8").rstrip("\n")

    def write_message(obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        writer.write(data.encode("utf-8"))
        writer.write(b"\n")
        writer.flush()

    return read_line, write_message
