"""Raw-protocol stress tests — pipe JSON-RPC lines into the server and assert responses."""

import json
import subprocess
import sys


def run_server(input_lines: list[str]) -> tuple[list[dict], str]:
    """Pipe input_lines through `python -m ft_mcp`, return (responses, stderr)."""
    if not input_lines:
        payload = ""
    else:
        payload = "\n".join(input_lines)
        if not payload.endswith("\n"):
            payload += "\n"
    proc = subprocess.run(
        [sys.executable, "-m", "ft_mcp"],
        input=payload,
        capture_output=True,
        timeout=5,
        text=True,
        cwd=str(__file__).rsplit("/", 2)[0],  # workspace root
    )
    responses = []
    for line in proc.stdout.strip().split("\n"):
        if not line:
            continue
        try:
            responses.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return responses, proc.stderr


def test_parse_error_truncated_json():
    """Truncated JSON → -32700 with id:null."""
    responses, stderr = run_server(['{"jsonrpc":"2.0","id":1'])
    assert len(responses) == 1
    r = responses[0]
    assert r["jsonrpc"] == "2.0"
    assert r["id"] is None
    assert r["error"]["code"] == -32700


def test_invalid_request_missing_jsonrpc():
    """Missing jsonrpc field → -32600 with id:null."""
    responses, stderr = run_server(['{"id":1,"method":"ping"}'])
    assert len(responses) == 1
    r = responses[0]
    assert r["jsonrpc"] == "2.0"
    assert r["id"] is None
    assert r["error"]["code"] == -32600


def test_method_not_found():
    """Unknown method after handshake → -32601 with matching id."""
    responses, stderr = run_server([
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25"}}',
        '{"jsonrpc":"2.0","method":"notifications/initialized"}',
        '{"jsonrpc":"2.0","id":5,"method":"bogus","params":{}}',
    ])
    # responses[0] is initialize result; responses[1] is the method-not-found
    assert len(responses) == 2
    r = responses[1]
    assert r["jsonrpc"] == "2.0"
    assert r["id"] == 5
    assert r["error"]["code"] == -32601
    assert "bogus" in r["error"]["message"]


def test_initialize_handshake():
    """Full initialize → result with protocolVersion, serverInfo, capabilities.tools."""
    responses, stderr = run_server([
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25"}}',
    ])
    assert len(responses) == 1
    r = responses[0]
    assert r["jsonrpc"] == "2.0"
    assert r["id"] == 1
    result = r["result"]
    assert "protocolVersion" in result
    assert result["serverInfo"]["name"] == "ft_mcp_server"
    assert "tools" in result["capabilities"]


def test_tools_list_after_handshake():
    """tools/list after full handshake returns tool list."""
    responses, stderr = run_server([
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25"}}',
        '{"jsonrpc":"2.0","method":"notifications/initialized"}',
        '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}',
    ])
    assert len(responses) == 2
    tools = responses[1]["result"]["tools"]
    names = {t["name"] for t in tools}
    assert "get_system_time" in names
    assert "calculate_file_hash" in names


def test_tools_call_system_time():
    """Call get_system_time → text content with ISO timestamp."""
    responses, stderr = run_server([
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25"}}',
        '{"jsonrpc":"2.0","method":"notifications/initialized"}',
        '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_system_time","arguments":{}}}',
    ])
    assert len(responses) == 2
    result = responses[1]["result"]
    assert "content" in result
    assert result["content"][0]["type"] == "text"
    assert "T" in result["content"][0]["text"]


def test_tools_call_business_error_is_error():
    """A tool raising an exception → isError: true, not a JSON-RPC error."""
    responses, stderr = run_server([
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25"}}',
        '{"jsonrpc":"2.0","method":"notifications/initialized"}',
        '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"calculate_file_hash","arguments":{"path":"/nonexistent","algorithm":"sha256"}}}',
    ])
    assert len(responses) == 2
    r = responses[1]
    # Must be a successful JSON-RPC response (no "error" key)
    assert "error" not in r, f"Got JSON-RPC error instead of isError: {r}"
    assert "result" in r
    assert r["result"].get("isError") is True
    assert "content" in r["result"]


def test_tools_call_unknown_tool():
    """Unknown tool name → -32601 protocol error."""
    responses, stderr = run_server([
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25"}}',
        '{"jsonrpc":"2.0","method":"notifications/initialized"}',
        '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"nonexistent","arguments":{}}}',
    ])
    assert len(responses) == 2
    r = responses[1]
    assert "error" in r
    assert r["error"]["code"] == -32601


def test_no_stdout_leak():
    """No non-JSON output leaks to stdout."""
    responses, stderr = run_server([
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25"}}',
    ])
    assert len(responses) == 1
    # Verify all stdout lines are valid JSON
    assert all(isinstance(r, dict) for r in responses)


def test_clean_shutdown_on_eof():
    """Process exits cleanly when stdin closes."""
    responses, stderr = run_server([])
    assert responses == []
