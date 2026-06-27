# Architecture

This document explains how `ft_mcp` is built and *why* each decision was made. It is the
companion to the [README](../README.md), which covers usage.

The server is deliberately split into three layers that never reach across each other:

```
Transport  ─▶  JSON-RPC router  ─▶  Business logic (tools / prompts / resources)
(bytes)        (envelopes)           (domain behaviour)
```

A message flows *down* as it is read and validated, and a response flows *up* as it is
serialized and flushed. No tool ever imports the transport; the transport never knows
what a "tool" is. This is what makes each layer independently testable and lets the same
router serve both the stdio and HTTP transports unchanged.

---

## 1. Transport layer — `ft_mcp/transport`

### stdio framing (`stdio.py`)

MCP over stdio is **newline-delimited JSON-RPC**: exactly one UTF-8 JSON object per line,
terminated by `\n`, with no embedded newlines inside the object.

Two invariants make or break the connection:

1. **`stdout` is the protocol channel and must stay pure.** Any non-protocol byte on
   stdout — a stray `print`, a traceback, a debug line — corrupts framing and
   permanently breaks the host connection. So:
   - the writer serializes with `json.dumps(obj, ensure_ascii=False, separators=(",", ":"))`
     (compact, no spurious whitespace) and appends a single `\n`;
   - it **flushes after every write**, because a buffered response is an invisible
     response;
   - all logging goes to **stderr, unconditionally** (`log.py` attaches only a
     `StreamHandler(sys.stderr)` and sets `propagate = False`).

2. **Reading must not block the event loop.** A synchronous `sys.stdin.readline()` in a
   `while True` loop would freeze `asyncio` and stall every in-flight tool. Instead the
   blocking read is pushed off the loop with `loop.run_in_executor(None, sys.stdin.buffer.readline)`
   and `await`-ed, so the loop stays free to run other coroutines while a line is pending.
   An empty `b""` read signals **EOF**, which unwinds the read loop and lets the process
   exit cleanly with no orphaned children.

### HTTP + SSE (`http.py`, bonus)

The bonus transport implements the Streamable-HTTP + SSE pattern:

- `GET /mcp/sse` opens a `text/event-stream`, allocates a session id, and immediately
  emits an `endpoint` event telling the client where to `POST`. The stream is kept alive
  with periodic `ping` events.
- `POST /mcp/messages?sessionId=…` accepts a JSON-RPC payload, runs it through the
  **same router**, and enqueues each response onto the matching SSE session — this is the
  POST→SSE correlation that lets a request/response protocol ride a one-way event stream.

It binds to `127.0.0.1` and validates the `Origin` header (browser origins must be
loopback) for DNS-rebinding protection; see [§5](#5-security-model).

---

## 2. JSON-RPC 2.0 router — `ft_mcp/router`

The router (`dispatch.py`) turns each raw line into at most one response. Its contract:
**it never raises and never crashes** — every failure path produces a well-formed
JSON-RPC error instead.

### Parse → classify → dispatch

1. **Parse.** `json.loads(line)`. A `JSONDecodeError` yields `-32700 Parse error` with
   `id: null` (the id is unknowable on a parse failure).
2. **Envelope check.** The payload must be an object with `"jsonrpc": "2.0"`; otherwise
   `-32600 Invalid Request`, `id: null`.
3. **Classify** by the presence of `id` and `method`:

   | `id` | `method` | Classified as | Output |
   | --- | --- | --- | --- |
   | present | present | **Request** | a response |
   | absent | present | **Notification** | none |
   | present | absent | **Response** (from the peer) | ignored/logged |
   | absent | absent | malformed | `-32600` |

4. **Dispatch.** Requests are looked up in a `method → handler` table; notifications in a
   separate table. The dispatch tables are populated in `__main__.build_router()`, which
   is the single place transport, router, and tools are wired together.

### Error taxonomy

| Code | Meaning | Raised when |
| --- | --- | --- |
| `-32700` | Parse error | line is not valid JSON |
| `-32600` | Invalid Request | not a `2.0` object / unclassifiable |
| `-32601` | Method not found | unknown method, or unknown tool name in `tools/call` |
| `-32602` | Invalid params | `tools/call` arguments fail the tool's JSON Schema |
| `-32603` | Internal error | an unexpected exception escapes a handler |

A subtle ordering choice: an **unknown method is resolved before the lifecycle gate**.
`-32601` is correct regardless of session state, so it must not be masked by the
"not initialized yet" guard — otherwise a typo'd method on a fresh connection would
wrongly report "not initialized" instead of "method not found".

### Lifecycle state machine — `lifecycle.py`

MCP is **stateful**: tools may not be discovered or called until the handshake completes.

```
        initialize (request)            notifications/initialized
UNINITIALIZED ───────────▶ INITIALIZING ────────────────────────▶ OPERATIONAL
     │                                                                  │
     └────────── every other method here ──▶ -32600 "not initialized" ─┘
```

- **`initialize`** reads `params.protocolVersion`. If the client's version is supported
  it is echoed back; otherwise the server replies with its own latest supported version
  (`2025-11-25`) so the client can renegotiate. The response carries `serverInfo`
  (`name`, `version`) and a `capabilities` object that explicitly advertises
  `"tools": {}` (plus `prompts` and `resources`).
- **`notifications/initialized`** flips the session to `OPERATIONAL`. It is a
  notification, so it produces **no response**.
- Any request other than `initialize` before the handshake completes is rejected, so no
  tool discovery or execution can leak in early.

---

## 3. Business logic — `ft_mcp/tools`

### Registry and schemas (`registry.py`)

A `Tool` is a small dataclass — `name`, `description`, `input_schema`, and an async
`handler`. The registry maps names to tools and renders the `tools/list` payload as
`{name, description, inputSchema}` triples.

Every tool declares a **JSON Schema (draft 2020-12)** `inputSchema`. No-argument tools
still declare `"type": "object", "properties": {}, "additionalProperties": false` so the
model is told, unambiguously, that the tool takes nothing — which stops it hallucinating
arguments.

### Why validation is hand-rolled

`tools/call` arguments are validated against the schema (`dispatch.validate_schema` /
`check_type`) covering `required`, `additionalProperties: false`, JSON types, and `enum`
constraints — entirely with the standard library, no external validator. Keeping the core
dependency-free is a feature, not an accident: the stdio server installs and runs with
nothing but CPython. Validation failures return `-32602` *before* the handler runs.

### Error segregation — the critical distinction

This is the part of MCP that is easy to get wrong:

- A **protocol** failure (bad method, schema-invalid params) is a JSON-RPC `error`.
- A **tool business-logic** failure (file not found, upstream timeout) is a **successful**
  JSON-RPC response whose `result` is a `ToolResult` carrying `isError: true` and the
  error message in a `text` content block.

The router enforces this: `handle_tools_call` wraps the handler in `try/except` and, on
exception, returns `result: { content: [...], isError: true }` — never `-32603`. The
reasoning is that the *model* consumes tool errors as feedback to adapt its next step; a
JSON-RPC error would instead read as a broken server and abort the interaction.

---

## 4. Concurrency model

The server runs a single `asyncio` event loop. The stdio read loop awaits one line at a
time, but because handlers are coroutines, a long-running tool yields control back to the
loop at every `await`, so the read side stays responsive. The blocking `stdin` read is
isolated in a thread-pool executor precisely so it cannot pin the loop.

The HTTP transport reuses this model via `aiohttp`'s own loop; each SSE session owns an
`asyncio.Queue`, and `POST` handlers simply enqueue responses for the SSE coroutine to
drain — no shared mutable state beyond the session registry.

---

## 5. Security model

All tool inputs are treated as untrusted LLM output.

**`calculate_file_hash`** is the worked example of defense-in-depth:

| Threat | Mitigation |
| --- | --- |
| Directory traversal (`../../etc/passwd`) | `os.path.realpath` + boundary-aware containment against an allowed root |
| Symlink escaping the root | `realpath` resolves links to their real target *before* the containment check |
| Prefix-sibling bypass (`<root>_evil`) | compare against `root + os.sep`, not a bare `startswith(root)` |
| TOCTOU swap after validation | open with `O_RDONLY \| O_NOFOLLOW` so a last-moment symlink swap fails the open |
| Unbounded memory on large files | stream in 64 KiB chunks into the hash object |

**HTTP transport**: binds to loopback, validates `Origin` (browser clients must be
loopback) for DNS-rebinding protection, and echoes only the *validated* origin in CORS
headers — never `*`.

---

## 6. Testing strategy

`tests/test_protocol.py` tests the server the way a real host does: it spawns
`python -m ft_mcp` as a subprocess, pipes raw JSON-RPC lines into stdin, and asserts the
exact bytes that come back on stdout. This is intentionally **end-to-end at the protocol
boundary** rather than unit-mocking the router, because the things most likely to break
MCP compliance — framing, flushing, stdout purity, EOF shutdown — only exist at that
boundary. Covered cases:

- parse error → `-32700` / `id: null`
- missing `jsonrpc` → `-32600` / `id: null`
- unknown method → `-32601` with the echoed id
- full `initialize` handshake → `protocolVersion` / `serverInfo` / `capabilities.tools`
- `tools/list` and `tools/call` after the handshake
- a failing tool surfaces as `isError: true`, **not** a JSON-RPC error
- unknown tool name → `-32601`
- no non-JSON ever leaks to stdout
- clean exit on EOF
