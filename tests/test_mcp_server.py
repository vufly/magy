import asyncio
import json

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

    asyncio.run(_test())


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
