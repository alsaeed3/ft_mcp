import hashlib
import os

from ft_mcp.tools.registry import Tool

# realpath (not abspath) so the root itself is canonical and symlinks in a
# requested path are resolved before the containment check below.
ALLOWED_ROOT = os.path.realpath(".")

INPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Path to the file to hash",
        },
        "algorithm": {
            "type": "string",
            "enum": ["md5", "sha1", "sha256"],
            "description": "Hashing algorithm to use",
        },
    },
    "required": ["path", "algorithm"],
    "additionalProperties": False,
}


def sanitize_path(requested_path: str) -> str:
    # realpath resolves symlinks, so a link inside the root that points outside
    # it (e.g. "<root>/evil -> /etc/passwd") resolves to its real target and is
    # then correctly rejected. abspath would not catch this.
    resolved = os.path.realpath(requested_path)
    # Boundary-aware check: a bare startswith would also accept a sibling
    # dir sharing the root as a prefix (e.g. "<root>_evil").
    root_prefix = ALLOWED_ROOT + os.sep
    if resolved != ALLOWED_ROOT and not resolved.startswith(root_prefix):
        raise ValueError(
            f"Path traversal denied: {requested_path} resolves outside allowed root"
        )
    return resolved


async def calculate_file_hash(arguments: dict) -> str:
    requested_path = arguments["path"]
    algorithm = arguments["algorithm"]

    resolved_path = sanitize_path(requested_path)

    if not os.path.isfile(resolved_path):
        raise ValueError(f"File not found or not a regular file: {requested_path}")

    if not os.access(resolved_path, os.R_OK):
        raise ValueError(f"File not readable: {requested_path}")

    try:
        h = hashlib.new(algorithm)
    except ValueError:
        raise ValueError(f"Unsupported algorithm: {algorithm}")

    # O_NOFOLLOW closes the TOCTOU window: if the final component is swapped
    # for a symlink between the checks above and this open, the open fails
    # rather than silently following the link out of the allowed root.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(resolved_path, flags)
    except OSError as exc:
        raise ValueError(f"Cannot open file: {requested_path}") from exc

    with os.fdopen(fd, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)

    return h.hexdigest()


tool = Tool(
    name="calculate_file_hash",
    description="Calculate the hash of a file using the specified algorithm",
    input_schema=INPUT_SCHEMA,
    handler=calculate_file_hash,
)
