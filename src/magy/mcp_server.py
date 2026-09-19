import sys
from typing import Any

from mcp.server.mcpserver import MCPServer

from magy.profiles import load_profiles
from magy.routing import get_routing_status
from magy.runs import (
    cancel_run,
    get_run_result,
    get_run_status,
    start_run,
    wait_run,
)


def create_mcp_server() -> MCPServer:
    """Create and configure the Magy MCP server with all delegation tools."""
    server = MCPServer("magy")

    @server.tool(
        name="magy_run_start",
        description=(
            "Start a detached, asynchronous execution run with Agy. "
            "WARNING: auto_approval defaults to True, enabling "
            "--dangerously-skip-permissions to auto-approve tool requests in "
            "headless mode without prompting. Set auto_approval=False if user "
            "confirmation is strictly required for tool actions (note: headless "
            "runs may fail if interaction is required)."
        ),
    )
    def handle_magy_run_start(
        prompt: str,
        workspace: str | None = None,
        profile: str | None = None,
        model: str | None = None,
        agent: str | None = None,
        effort: str | None = None,
        mode: str | None = None,
        timeout: float | None = None,
        sandbox: bool | None = None,
        additional_dirs: list[str] | None = None,
        auto_approval: bool = True,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        try:
            status = start_run(
                prompt=prompt,
                workspace=workspace,
                profile=profile,
                model=model,
                agent=agent,
                effort=effort,
                mode=mode,
                timeout=timeout,
                sandbox=sandbox,
                additional_dirs=additional_dirs,
                auto_approval=auto_approval,
                idempotency_key=idempotency_key,
            )
            return status.to_dict()
        except Exception as e:
            return {"error": str(e), "status": "failed"}

    @server.tool(
        name="magy_run_wait",
        description=(
            "Wait for an execution run to finish or reach terminal state, returning "
            "its current status. Clamped to safe MCP timeout limits (0.1s to 60s) "
            "to prevent gateway timeouts."
        ),
    )
    def handle_magy_run_wait(
        run_id: str,
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        try:
            status = wait_run(run_id, timeout=timeout)
            return status.to_dict()
        except Exception as e:
            return {"error": str(e), "run_id": run_id, "status": "failed"}

    @server.tool(
        name="magy_run_status",
        description=(
            "Get the current status of an execution run. Omits prompt, raw "
            "command arguments, and credential paths."
        ),
    )
    def handle_magy_run_status(
        run_id: str,
    ) -> dict[str, Any]:
        try:
            status = get_run_status(run_id)
            return status.to_dict()
        except Exception as e:
            return {"error": str(e), "run_id": run_id, "status": "failed"}

    @server.tool(
        name="magy_run_result",
        description=(
            "Retrieve bounded output chunks with stable byte offsets from an "
            "execution run's stdout log."
        ),
    )
    def handle_magy_run_result(
        run_id: str,
        offset: int = 0,
        limit: int = 65536,
    ) -> dict[str, Any]:
        try:
            res = get_run_result(run_id, offset=offset, limit=limit)
            return res.to_dict()
        except Exception as e:
            return {
                "error": str(e),
                "run_id": run_id,
                "status": "failed",
                "content": "",
                "offset": offset,
                "next_offset": offset,
                "eof": True,
                "is_json": False,
            }

    @server.tool(
        name="magy_run_cancel",
        description=(
            "Cancel a running or queued execution run, terminating its entire "
            "process tree."
        ),
    )
    def handle_magy_run_cancel(
        run_id: str,
    ) -> dict[str, Any]:
        try:
            status = cancel_run(run_id)
            return status.to_dict()
        except Exception as e:
            return {"error": str(e), "run_id": run_id, "status": "failed"}

    @server.tool(
        name="magy_profiles",
        description=(
            "List all registered profiles, their health, cooldowns, and availability, "
            "along with round-robin routing status."
        ),
    )
    def handle_magy_profiles() -> dict[str, Any]:
        try:
            profiles = load_profiles()
            routing = get_routing_status()
            return {
                "profiles": [p.to_dict() for p in profiles.values()],
                "routing": routing,
            }
        except Exception as e:
            return {"error": str(e), "profiles": [], "routing": {}}

    return server


def main(argv: list[str] | None = None) -> int:
    """Run Magy MCP server with stdio transport."""
    server = create_mcp_server()
    try:
        server.run(transport="stdio")
        return 0
    except (KeyboardInterrupt, SystemExit):
        return 0
    except Exception as e:
        print(f"MCP server error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
