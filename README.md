# ft_mcp

A Model Context Protocol (MCP) server implemented from scratch in Python, without an MCP SDK.

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-10%20passing-brightgreen.svg)](tests/test_protocol.py)

MCP is an open, JSON-RPC 2.0 protocol that lets an AI host — such as Claude Desktop or
the MCP Inspector — discover and invoke tools exposed by an external server over a
standard transport. This project implements the server side of that protocol directly:
the stdio framing, the JSON-RPC routing, the lifecycle handshake, schema validation, and
the tool layer.

The implementation deliberately avoids an MCP SDK so the protocol mechanics stay explicit
rather than hidden behind a library. The core (stdio transport) depends only on the Python
standard library; `aiohttp` is needed only for the optional HTTP/SSE transport.

## Scope

- **Transports:** stdio (primary); Streamable HTTP + SSE (optional).
- **Lifecycle:** `initialize` / `notifications/initialized` with protocol-version
  negotiation and capability advertisement.
- **Tools:** registry, `tools/list`, `tools/call`, JSON Schema (draft 2020-12) validation.
  Two reference tools: `get_system_time` and `calculate_file_hash`.
- **Additional primitives:** `prompts/list`, `prompts/get`, `resources/list`,
  `resources/read`.
- **Diagnostics:** structured logging and per-method timing, written to stderr only.

Supported protocol versions: `2025-11-25` and `2025-03-26`.

## Requirements

Python 3.10 or newer. The stdio server needs nothing else.

## Installation

```bash
git clone https://github.com/alsaeed3/ft_mcp.git
cd ft_mcp

pip install -e .            # core (stdio) only
pip install -e ".[http]"    # add the optional HTTP/SSE transport
pip install -e ".[dev]"     # add pytest
```

Installation is optional; the server also runs directly from the source tree with
`python -m ft_mcp`.

## Usage

The server reads JSON-RPC messages from stdin, writes responses to stdout, and logs to
stderr.

```bash
python -m ft_mcp
```

A full session — handshake, tool discovery, and a tool call — driven by hand:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25"}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_system_time","arguments":{}}}' \
  | python -m ft_mcp
```

### Connecting a host

**MCP Inspector:**

```bash
npx @modelcontextprotocol/inspector python -m ft_mcp
```

**Claude Desktop** — add the server to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ft_mcp": {
      "command": "python",
      "args": ["-m", "ft_mcp"],
      "cwd": "/absolute/path/to/ft_mcp"
    }
  }
}
```

## Architecture

The server is organized into three layers, kept independent of one another so each can be
reasoned about and tested on its own. The same router serves both transports unchanged.

```text
                 ┌──────────────────────────────────────────────┐
   stdin  ─────▶ │  Transport            (ft_mcp/transport)      │
                 │  newline-delimited framing; flush-after-write │
   stdout ◀───── │  stdout carries protocol bytes only          │
                 └───────────────────────┬──────────────────────┘
                                         │ one UTF-8 line per message
                                         ▼
                 ┌──────────────────────────────────────────────┐
                 │  JSON-RPC 2.0 router  (ft_mcp/router)         │
                 │  parse → classify → dispatch                  │
                 │  lifecycle gate; error taxonomy               │
                 └───────────────────────┬──────────────────────┘
                                         │ validated params
                                         ▼
                 ┌──────────────────────────────────────────────┐
                 │  Business logic       (ft_mcp/tools)          │
                 │  tool registry, schemas, prompts, resources   │
                 └──────────────────────────────────────────────┘
```

Two constraints shape the transport layer. First, stdout is the protocol channel, so it
carries nothing but newline-terminated JSON-RPC messages, flushed after each write; all
diagnostics go to stderr. Second, stdin is read without blocking the event loop, so a
long-running tool does not stall message intake.

A more detailed description — the lifecycle state machine, message classification, the
error taxonomy, and the security model — is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Tools

| Tool | Arguments | Description |
| --- | --- | --- |
| `get_system_time` | *(none)* | Returns the current UTC time as an ISO 8601 string. Its schema sets `additionalProperties: false` so callers are not led to invent arguments. |
| `calculate_file_hash` | `path` (string), `algorithm` (`md5` \| `sha1` \| `sha256`) | Streams a file in 64 KiB chunks and returns its hex digest. The path is canonicalized and confined to an allowed root. |

Each tool declares a JSON Schema (draft 2020-12) `inputSchema`. The router validates
`tools/call` arguments against it and returns `-32602` on a mismatch before the handler
runs.

## Error handling

The server distinguishes protocol errors from tool errors, because a host treats them
differently.

| Category | Example | Response |
| --- | --- | --- |
| Protocol | truncated JSON | `error -32700`, `id: null` |
| Protocol | missing `"jsonrpc":"2.0"` | `error -32600`, `id: null` |
| Protocol | unknown method or tool | `error -32601` |
| Protocol | schema-invalid params | `error -32602` |
| Protocol | unexpected handler exception | `error -32603` |
| Tool logic | hashing a missing file | success result with `isError: true` and an error message |

Tool failures are returned as successful responses carrying `isError: true` rather than
JSON-RPC errors, so the model receives the message as feedback. Application-defined codes
(for example the HTTP transport's invalid-session `-32000`) stay outside the reserved
`-32768..-32000` range.

## Security

Tool inputs are treated as untrusted. `calculate_file_hash` resolves paths with
`os.path.realpath` (so symlinks are followed to their target before the containment
check), performs a boundary-aware check against an allowed root, and opens the file with
`O_NOFOLLOW` to avoid a time-of-check/time-of-use swap. The HTTP transport binds to
loopback, validates the `Origin` header against loopback for browser clients
(DNS-rebinding protection), and echoes only the validated origin in CORS headers.

## Optional HTTP/SSE transport

```bash
pip install -e ".[http]"
python -m ft_mcp --http --host 127.0.0.1 --port 8080
```

`GET /mcp/sse` opens an event stream and announces a POST endpoint; `POST /mcp/messages`
accepts JSON-RPC and correlates each reply back onto the matching SSE session.

## Testing

```bash
pip install -e ".[dev]"
python -m pytest -q
```

The suite in [tests/test_protocol.py](tests/test_protocol.py) runs the server as a
subprocess and asserts its responses at the protocol boundary — parse and invalid-request
errors, the initialize handshake, `tools/list` and `tools/call`, tool errors surfacing as
`isError`, stdout purity, and clean shutdown on EOF.

## Project layout

```text
ft_mcp/
├── __main__.py            # entry point and handler wiring (stdio / --http)
├── log.py                 # stderr-only logger
├── transport/
│   ├── stdio.py           # async stdin reader, flush-after-write writer
│   └── http.py            # optional HTTP + SSE transport
├── router/
│   ├── dispatch.py        # JSON-RPC parse, classify, dispatch, validation
│   ├── lifecycle.py       # session state machine
│   └── errors.py          # error codes and helper
└── tools/
    ├── registry.py        # tool dataclass and registry
    ├── system_time.py     # get_system_time
    ├── file_hash.py       # calculate_file_hash
    ├── prompts.py         # prompts and resources
    └── telemetry.py       # per-method timing → stderr

tests/test_protocol.py     # protocol compliance suite
```

## License

[MIT](LICENSE)
