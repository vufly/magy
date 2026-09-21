import asyncio
import sys
import time
from dataclasses import dataclass
from typing import Annotated, NoReturn

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field, StrictBool

from magy.profiles import load_profiles
from magy.routing import get_routing_status
from magy.runs import (
    MAX_RESULT_CHUNK_BYTES,
    MIN_RESULT_CHUNK_BYTES,
    TERMINAL_STATUSES,
    RunResult,
    RunStatus,
    cancel_run,
    get_run_result,
    get_run_status,
    start_run,
)


class StrictMCPServer(MCPServer):
    async def list_tools(self):
        tools = await super().list_tools()
        for tool in tools:
            tool.input_schema = {
                **tool.input_schema,
                "additionalProperties": False,
            }
        return tools

    async def call_tool(self, name, arguments, context=None):
        tool = next(
            (
                candidate
                for candidate in await self.list_tools()
                if candidate.name == name
            ),
            None,
        )
        if tool is not None:
            allowed = set(tool.input_schema.get("properties", {}))
            if set(arguments) - allowed:
                raise ToolError("Unknown tool input property")

        for field in ("auto_approval", "sandbox"):
            if (
                field in arguments
                and arguments[field] is not None
                and type(arguments[field]) is not bool
            ):
                raise ToolError(f"{field} must be a boolean")
        if "timeout" in arguments and arguments["timeout"] is not None:
            if type(arguments["timeout"]) not in (int, float):
                raise ToolError("timeout must be a number")
        for field in ("offset", "limit"):
            if field in arguments and type(arguments[field]) is not int:
                raise ToolError(f"{field} must be an integer")
        if "additional_dirs" in arguments and arguments["additional_dirs"] is not None:
            directories = arguments["additional_dirs"]
            if not isinstance(directories, list) or any(
                not isinstance(directory, str) for directory in directories
            ):
                raise ToolError("additional_dirs must be an array of strings")

        return await super().call_tool(name, arguments, context)


@dataclass
class PublicProfile:
    name: str
    kind: str
    enabled: bool
    health: str
    available: bool
    cooldown_until: float | None
    cooldown_reason: str | None
    last_selected_at: float | None
    last_success_at: float | None
    last_failure_at: float | None


@dataclass
class RoutingSummary:
    cursor: str | None
    total_profiles: int
    enabled_profiles: int
    available_profiles: int
    healthy_profiles: int
    untested_profiles: int
    cooldown_profiles: int


@dataclass
class ProfilesResponse:
    profiles: list[PublicProfile]
    routing: RoutingSummary


def _raise_tool_error(message: str, exc: Exception) -> NoReturn:
    raise ToolError(message) from exc


def create_mcp_server() -> MCPServer:
    """Create and configure the Magy MCP server with all delegation tools."""
    server = StrictMCPServer("magy")

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
        structured_output=True,
    )
    def handle_magy_run_start(
        prompt: Annotated[str, Field(min_length=1)],
        workspace: str | None = None,
        profile: str | None = None,
        model: str | None = None,
        agent: str | None = None,
        effort: str | None = None,
        mode: str | None = None,
        timeout: Annotated[float | None, Field(gt=0)] = None,
        sandbox: StrictBool | None = None,
        additional_dirs: list[str] | None = None,
        auto_approval: StrictBool = True,
        idempotency_key: str | None = None,
    ) -> RunStatus:
        try:
            return start_run(
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
        except Exception as exc:
            _raise_tool_error("Run could not be started", exc)

    @server.tool(
        name="magy_run_wait",
        description=(
            "Wait for an execution run to finish or reach terminal state, returning "
            "its current status. Clamped to safe MCP timeout limits (0.1s to 60s) "
            "to prevent gateway timeouts."
        ),
        structured_output=True,
    )
    async def handle_magy_run_wait(run_id: str, timeout: float = 20.0) -> RunStatus:
        try:
            clamped_timeout = max(0.1, min(float(timeout), 60.0))
            deadline = time.monotonic() + clamped_timeout
            while time.monotonic() < deadline:
                status = await asyncio.to_thread(get_run_status, run_id)
                if status.status in TERMINAL_STATUSES:
                    return status
                await asyncio.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
            return await asyncio.to_thread(get_run_status, run_id)
        except Exception as exc:
            _raise_tool_error("Run could not be waited", exc)

    @server.tool(
        name="magy_run_status",
        description=(
            "Get the current status of an execution run. Omits prompt, raw "
            "command arguments, and credential paths."
        ),
        structured_output=True,
    )
    def handle_magy_run_status(run_id: str) -> RunStatus:
        try:
            return get_run_status(run_id)
        except Exception as exc:
            _raise_tool_error("Run status is unavailable", exc)

    @server.tool(
        name="magy_run_result",
        description=(
            "Retrieve bounded UTF-8 output chunks with stable byte offsets from an "
            "execution run's stdout log. Maximum chunk size is 1 MiB."
        ),
        structured_output=True,
    )
    def handle_magy_run_result(
        run_id: str,
        offset: Annotated[int, Field(ge=0)] = 0,
        limit: Annotated[
            int,
            Field(ge=MIN_RESULT_CHUNK_BYTES, le=MAX_RESULT_CHUNK_BYTES),
        ] = 65536,
    ) -> RunResult:
        try:
            return get_run_result(run_id, offset=offset, limit=limit)
        except Exception as exc:
            _raise_tool_error("Run result is unavailable", exc)

    @server.tool(
        name="magy_run_cancel",
        description=(
            "Cancel a running or queued execution run, terminating its entire "
            "process tree."
        ),
        structured_output=True,
    )
    def handle_magy_run_cancel(run_id: str) -> RunStatus:
        try:
            return cancel_run(run_id)
        except Exception as exc:
            _raise_tool_error("Run could not be cancelled", exc)

    @server.tool(
        name="magy_profiles",
        description=(
            "List all registered profiles, their health, cooldowns, and availability, "
            "along with round-robin routing status. Private home paths and internal "
            "incarnation identifiers are omitted."
        ),
        structured_output=True,
    )
    def handle_magy_profiles() -> ProfilesResponse:
        try:
            profiles = load_profiles()
            routing = get_routing_status()
            now = time.time()
            return ProfilesResponse(
                profiles=[
                    PublicProfile(
                        name=profile.name,
                        kind=profile.kind,
                        enabled=profile.enabled,
                        health=profile.health,
                        available=profile.is_available(now),
                        cooldown_until=profile.cooldown_until,
                        cooldown_reason=profile.cooldown_reason,
                        last_selected_at=profile.last_selected_at,
                        last_success_at=profile.last_success_at,
                        last_failure_at=profile.last_failure_at,
                    )
                    for profile in profiles.values()
                ],
                routing=RoutingSummary(**routing),
            )
        except Exception as exc:
            _raise_tool_error("Profiles are unavailable", exc)

    return server


def main(argv: list[str] | None = None) -> int:
    """Run Magy MCP server with stdio transport."""
    if argv is None:
        argv = sys.argv[1:]
    if "-h" in argv or "--help" in argv:
        print("Usage: magy-mcp")
        print()
        print("Run the Magy Model Context Protocol (MCP) server over stdio.")
        return 0

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
