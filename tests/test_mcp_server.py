import asyncio
import json
import os
import subprocess
import sys
import time

import pytest
from mcp import Client, StdioServerParameters
from mcp.server.mcpserver.exceptions import ToolError

from magy.mcp_server import create_mcp_server
from magy.profiles import add_profile
from magy.runs import get_run_dir


def test_mcp_server_registers_all_six_tools():
    async def _test():
        server = create_mcp_server()
        tools = await server.list_tools()
        tool_names = {t.name for t in tools}

        expected_tools = {
            "magy_run_start",
            "magy_run_wait",
            "magy_run_status",
            "magy_run_result",
            "magy_run_cancel",
            "magy_profiles",
        }
        assert expected_tools == tool_names

        start_tool = next(t for t in tools if t.name == "magy_run_start")
        assert "auto_approval" in start_tool.description
        assert "--dangerously-skip-permissions" in start_tool.description
        assert start_tool.input_schema["additionalProperties"] is False
        assert start_tool.output_schema is not None
        result_tool = next(t for t in tools if t.name == "magy_run_result")
        limit_schema = result_tool.input_schema["properties"]["limit"]
        assert limit_schema["minimum"] == 4
        assert limit_schema["maximum"] == 1024 * 1024

    asyncio.run(_test())


def test_mcp_run_start_and_status(fake_agy, monkeypatch):
    async def _test():
        monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
        add_profile("mcp-p1", kind="managed")

        server = create_mcp_server()
        start_res = await server.call_tool(
            "magy_run_start",
            {"prompt": "Hello MCP", "profile": "mcp-p1"},
        )
        assert start_res.content
        data = json.loads(start_res.content[0].text)
        assert "run_id" in data
        run_id = data["run_id"]
        assert data["status"] in ("queued", "running", "completed")

        status_res = await server.call_tool("magy_run_status", {"run_id": run_id})
        assert status_res.content
        status_data = json.loads(status_res.content[0].text)
        assert status_data["run_id"] == run_id
        assert "prompt" not in status_data
        assert "command" not in status_data

    asyncio.run(_test())


def test_mcp_run_result_and_wait():
    async def _test():
        server = create_mcp_server()
        start_res = await server.call_tool("magy_run_start", {"prompt": "Result test"})
        run_id = json.loads(start_res.content[0].text)["run_id"]

        # Write output to stdout.log and complete run
        run_dir = get_run_dir(run_id)
        (run_dir / "stdout.log").write_text("Hello from MCP output", encoding="utf-8")
        state_file = run_dir / "state.json"
        state_data = json.loads(state_file.read_text(encoding="utf-8"))
        state_data["status"] = "completed"
        state_data["exit_code"] = 0
        state_file.write_text(json.dumps(state_data), encoding="utf-8")

        # Wait for run
        wait_res = await server.call_tool(
            "magy_run_wait", {"run_id": run_id, "timeout": 2.0}
        )
        wait_data = json.loads(wait_res.content[0].text)
        assert wait_data["status"] == "completed"

        # Read result
        res_call = await server.call_tool(
            "magy_run_result", {"run_id": run_id, "offset": 0, "limit": 100}
        )
        res_data = json.loads(res_call.content[0].text)
        assert res_data["content"] == "Hello from MCP output"
        assert res_data["eof"] is True

    asyncio.run(_test())


def test_mcp_run_cancel():
    async def _test():
        server = create_mcp_server()
        start_res = await server.call_tool("magy_run_start", {"prompt": "Cancel test"})
        run_id = json.loads(start_res.content[0].text)["run_id"]

        cancel_res = await server.call_tool("magy_run_cancel", {"run_id": run_id})
        cancel_data = json.loads(cancel_res.content[0].text)
        assert cancel_data["status"] == "cancelled"

    asyncio.run(_test())


def test_mcp_profiles():
    async def _test():
        add_profile("mcp-query-p", kind="managed")
        server = create_mcp_server()

        prof_res = await server.call_tool("magy_profiles", {})
        prof_data = json.loads(prof_res.content[0].text)

        names = [p["name"] for p in prof_data["profiles"]]
        assert "mcp-query-p" in names
        assert "routing" in prof_data
        profile = next(p for p in prof_data["profiles"] if p["name"] == "mcp-query-p")
        assert "home_dir" not in profile
        assert "incarnation_id" not in profile
        assert "available" in profile

    asyncio.run(_test())


def test_mcp_rejects_unknown_auto_approval_field():
    async def _test():
        server = create_mcp_server()
        with pytest.raises(ToolError, match="Unknown tool input property"):
            await server.call_tool(
                "magy_run_start",
                {"prompt": "test", "auto_approve": False},
            )

    asyncio.run(_test())


def test_mcp_strict_runtime_types_and_nullable_sandbox(monkeypatch):
    import magy.mcp_server
    from magy.runs import RunStatus

    monkeypatch.setattr(
        magy.mcp_server,
        "start_run",
        lambda **kwargs: RunStatus(run_id="run_test", status="queued"),
    )

    async def _test():
        server = create_mcp_server()
        with pytest.raises(ToolError, match="offset must be an integer"):
            await server.call_tool(
                "magy_run_result",
                {"run_id": "missing", "offset": True},
            )
        with pytest.raises(ToolError, match="timeout must be a number"):
            await server.call_tool(
                "magy_run_wait",
                {"run_id": "missing", "timeout": "1"},
            )
        with pytest.raises(ToolError, match="additional_dirs must be an array"):
            await server.call_tool(
                "magy_run_start",
                {"prompt": "test", "additional_dirs": '["/tmp"]'},
            )

        result = await server.call_tool(
            "magy_run_start",
            {"prompt": "test", "sandbox": None},
        )
        assert result.structured_content["status"] == "queued"

    asyncio.run(_test())


def test_mcp_errors_are_sanitized_tool_errors():
    async def _test():
        server = create_mcp_server()
        with pytest.raises(ToolError) as exc_info:
            await server.call_tool("magy_run_status", {"run_id": "missing"})

        message = str(exc_info.value)
        assert "Run status is unavailable" in message
        assert "/" not in message

    asyncio.run(_test())


def test_mcp_transport_marks_sanitized_tool_errors():
    async def _test():
        async with Client(create_mcp_server()) as client:
            result = await client.call_tool("magy_run_status", {"run_id": "missing"})

        assert result.is_error is True
        assert "Run status is unavailable" in result.content[0].text
        assert "/" not in result.content[0].text

    asyncio.run(_test())


def test_mcp_stdio_worker_survives_server_restart(fake_agy, monkeypatch):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    monkeypatch.setenv("FAKE_AGY_MODE", "sleep")
    add_profile("mcp-restart-p", kind="managed")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "magy.mcp_server"],
        env=dict(os.environ),
    )

    async def _test():
        async with Client(params) as first_client:
            started = await first_client.call_tool(
                "magy_run_start",
                {
                    "prompt": "Restart survival",
                    "profile": "mcp-restart-p",
                    "timeout": 0.5,
                },
            )
            assert started.is_error is False
            run_id = started.structured_content["run_id"]
            assert started.structured_content["status"] in {"queued", "running"}

        async with Client(params) as second_client:
            status_result = await second_client.call_tool(
                "magy_run_status", {"run_id": run_id}
            )
            assert status_result.is_error is False
            waited = await second_client.call_tool(
                "magy_run_wait",
                {"run_id": run_id, "timeout": 5.0},
            )
            assert waited.is_error is False
            assert waited.structured_content["status"] == "timed_out"
            result = await second_client.call_tool(
                "magy_run_result", {"run_id": run_id}
            )
            assert result.is_error is False
            assert result.structured_content["eof"] is True

    asyncio.run(_test())


def test_mcp_stdio_stdout_contains_only_jsonrpc(tmp_path):
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "magy-test", "version": "1"},
            },
        },
        {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        },
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    proc = subprocess.Popen(
        [sys.executable, "-m", "magy.mcp_server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=dict(os.environ),
    )
    for message in messages:
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

    response_ids = set()
    lines = []
    start_time = time.time()
    while time.time() - start_time < 10.0:
        line = proc.stdout.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        lines.append(line)
        message = json.loads(line)
        assert message.get("jsonrpc") == "2.0"
        if "id" in message:
            response_ids.add(message["id"])
        if {1, 2}.issubset(response_ids):
            break

    proc.stdin.close()
    proc.wait(timeout=5.0)

    assert proc.returncode == 0
    assert lines
    assert {1, 2}.issubset(response_ids)


def test_mcp_server_restart_survival():
    """Run started through MCP can be waited/read from fresh server process."""

    async def _test():
        server1 = create_mcp_server()
        start_res = await server1.call_tool(
            "magy_run_start", {"prompt": "Restart test"}
        )
        run_id = json.loads(start_res.content[0].text)["run_id"]

        # Mark run complete on disk
        run_dir = get_run_dir(run_id)
        (run_dir / "stdout.log").write_text("Output survived restart", encoding="utf-8")
        state_file = run_dir / "state.json"
        state_data = json.loads(state_file.read_text(encoding="utf-8"))
        state_data["status"] = "completed"
        state_data["exit_code"] = 0
        state_file.write_text(json.dumps(state_data), encoding="utf-8")

        # Simulate fresh server process
        server2 = create_mcp_server()
        status_res = await server2.call_tool("magy_run_status", {"run_id": run_id})
        status_data = json.loads(status_res.content[0].text)
        assert status_data["status"] == "completed"

        res_call = await server2.call_tool(
            "magy_run_result", {"run_id": run_id, "offset": 0, "limit": 50}
        )
        res_data = json.loads(res_call.content[0].text)
        assert res_data["content"] == "Output survived restart"
        assert res_data["eof"] is True

    asyncio.run(_test())


def test_mcp_server_help_flag(capsys):
    from magy.mcp_server import main as mcp_main

    ret = mcp_main(["--help"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Usage: magy-mcp" in captured.out

    ret2 = mcp_main(["-h"])
    assert ret2 == 0
    captured2 = capsys.readouterr()
    assert "Usage: magy-mcp" in captured2.out
