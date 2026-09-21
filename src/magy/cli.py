import argparse
import json
import subprocess
import sys
import time

from magy.agy import collect_diagnostics


def build_parser() -> argparse.ArgumentParser:
    fmt = argparse.RawDescriptionHelpFormatter
    parser = argparse.ArgumentParser(
        prog="magy",
        description=(
            "Magy: Multi-profile launcher and orchestrator for Antigravity (agy).\n\n"
            "Routes Antigravity CLI commands across isolated profiles (~/.gemini)\n"
            "using round-robin selection or explicit profile targeting (-p).\n"
            "Tracks profile health, enforces cooldown backoff, and ensures zero\n"
            "credential leakage to or from the host environment."
        ),
        epilog=(
            "Commands & Modes:\n"
            "  magy [OPTIONS] [COMMAND...]     Run agy with automatic round-robin\n"
            "  magy -p <name> [COMMAND...]     Run agy with an explicit profile\n"
            "  magy [OPTIONS] -- [AGY_ARGS...] Forward arguments directly to agy\n"
            "  magy <subcommand> [OPTIONS]     Execute a Magy management command\n\n"
            "Examples:\n"
            "  # Run agy with automatic round-robin profile selection:\n"
            '  magy "Analyze performance in src/"\n'
            "  magy -- pytest -q\n\n"
            "  # Run agy with an explicit profile:\n"
            '  magy -p work "Refactor user authentication"\n'
            "  magy --profile=personal -- git status\n\n"
            "  # Check environment prerequisites and health:\n"
            "  magy doctor\n\n"
            "  # View profiles and routing health overview:\n"
            "  magy status\n\n"
            "  # Manage and inspect profiles:\n"
            "  magy profile list\n"
            "  magy profile show work\n"
            "  magy profile add work\n"
            "  magy profile auth work\n\n"
            "  # View detailed help for any subcommand:\n"
            "  magy help profile\n"
            "  magy profile add --help\n"
            "  magy help doctor"
        ),
        formatter_class=fmt,
    )
    parser.add_argument(
        "-p",
        "--profile",
        dest="profile",
        metavar="NAME",
        help="Target profile name for command execution (overrides round-robin).",
    )
    parser.add_argument(
        "--version",
        "-V",
        action="version",
        version="%(prog)s 0.1.0",
    )

    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Check environment, executable discovery, and prerequisites.",
        description=(
            "Check environment, executable discovery, and prerequisites.\n\n"
            "Validates storage directory permissions, resolves the official agy\n"
            "executable (or configured custom resolver), checks agy version\n"
            "compatibility, and reports system health status."
        ),
        epilog=("Examples:\n  magy doctor\n  magy doctor --json"),
        formatter_class=fmt,
    )
    doctor_parser.add_argument(
        "--json",
        action="store_true",
        help="Output diagnostics in JSON format.",
    )

    status_parser = subparsers.add_parser(
        "status",
        help="Show profiles and routing status overview.",
        description=(
            "Show profiles and routing status overview.\n\n"
            "Displays round-robin cursor state, profile counts (total, enabled,\n"
            "healthy, untested, in cooldown), and health summaries."
        ),
        epilog=("Examples:\n  magy status\n  magy status --json"),
        formatter_class=fmt,
    )
    status_parser.add_argument(
        "--json",
        action="store_true",
        help="Output status in JSON format.",
    )

    profile_parser = subparsers.add_parser(
        "profile",
        help="Manage and bootstrap profiles.",
        description=(
            "Manage and bootstrap isolated Antigravity profiles.\n\n"
            "Profiles store separate authentication tokens, configuration, and home\n"
            "directories under ~/.local/share/magy/profiles/<name>/home.\n"
            "Magy automatically cycles between enabled, healthy profiles during\n"
            "round-robin routing."
        ),
        epilog=(
            "Profile Actions:\n"
            "  add           Register a new profile (managed or external)\n"
            "  create        Create a profile directory layout (alias for 'add')\n"
            "  auth          Launch agy interactively to authenticate a profile\n"
            "  run           Run an agy command directly within a profile environment\n"
            "  list          List registered profiles and health status\n"
            "  show          Show details for a specific profile\n"
            "  enable        Enable a profile for round-robin routing\n"
            "  disable       Disable a profile from round-robin routing\n"
            "  reset-health  Reset profile health to healthy and clear cooldown\n"
            "  remove        Delete a profile and its isolated directory\n"
            "  help          Show help for an action (e.g. 'magy profile help add')\n\n"
            "Examples:\n"
            "  magy profile add work\n"
            "  magy profile auth work\n"
            "  magy profile list\n"
            "  magy profile show work\n"
            "  magy profile disable work\n"
            "  magy profile enable work\n"
            "  magy profile reset-health work\n"
            "  magy profile remove work --force\n"
            "  magy profile add --help"
        ),
        formatter_class=fmt,
    )
    profile_subparsers = profile_parser.add_subparsers(
        dest="profile_action", help="Profile actions"
    )

    add_p = profile_subparsers.add_parser(
        "add",
        help="Add a new profile.",
        description=(
            "Add a new profile to Magy.\n\n"
            "Creates an isolated managed profile directory with safe settings\n"
            "synchronization, or links an existing directory with --current."
        ),
        epilog=(
            "Examples:\n"
            "  magy profile add work\n"
            "  magy profile add personal\n"
            "  magy profile add default --current"
        ),
        formatter_class=fmt,
    )
    add_p.add_argument("name", help="Profile name.")
    add_p.add_argument(
        "--current",
        action="store_true",
        help="Register current user home directory (~/.gemini) as an external profile.",
    )

    create_p = profile_subparsers.add_parser(
        "create",
        help="Create a profile directory layout (alias to add).",
        description=(
            "Create a managed profile directory layout "
            "(alias for 'magy profile add <name>')."
        ),
        epilog=("Examples:\n  magy profile create work"),
        formatter_class=fmt,
    )
    create_p.add_argument("name", help="Profile name.")

    auth_p = profile_subparsers.add_parser(
        "auth",
        help="Launch Agy interactively to authenticate a profile.",
        description=(
            "Launch Antigravity (agy) interactively to authenticate a profile.\n\n"
            "Runs an interactive terminal session with HOME redirected to the\n"
            "profile's isolated directory. Complete the OAuth login flow to store\n"
            "credentials strictly inside this profile."
        ),
        epilog=("Examples:\n  magy profile auth work\n  magy profile auth personal"),
        formatter_class=fmt,
    )
    auth_p.add_argument("name", help="Profile name.")
    auth_p.add_argument(
        "extra_args",
        nargs=argparse.REMAINDER,
        help="Optional extra arguments to pass to agy during authentication.",
    )

    run_p = profile_subparsers.add_parser(
        "run",
        help="Run an Agy command within a profile environment.",
        description=(
            "Run an Antigravity (agy) command within a profile environment.\n\n"
            "Directly executes agy with HOME redirected to the designated profile,\n"
            "bypassing automatic round-robin selection. Updates profile health."
        ),
        epilog=(
            "Examples:\n"
            "  magy profile run work -- models\n"
            '  magy profile run work -- "Refactor database schema"'
        ),
        formatter_class=fmt,
    )
    run_p.add_argument("name", help="Profile name.")
    run_p.add_argument(
        "extra_args",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded to agy.",
    )

    list_p = profile_subparsers.add_parser(
        "list",
        help="List registered profiles.",
        description=(
            "List all registered profiles.\n\n"
            "Displays a summary table of profile names, kinds (managed/external),\n"
            "enabled status, current health classification, and remaining cooldown."
        ),
        epilog=("Examples:\n  magy profile list\n  magy profile list --json"),
        formatter_class=fmt,
    )
    list_p.add_argument(
        "--json",
        action="store_true",
        help="Output profiles in JSON format.",
    )

    show_p = profile_subparsers.add_parser(
        "show",
        help="Show details for a specific profile.",
        description=(
            "Show detailed information for a specific profile.\n\n"
            "Displays profile type, home path, enabled status, health status,\n"
            "active cooldown, and timestamps of selection, success, and failure."
        ),
        epilog=("Examples:\n  magy profile show work\n  magy profile show work --json"),
        formatter_class=fmt,
    )
    show_p.add_argument("name", help="Profile name.")
    show_p.add_argument(
        "--json",
        action="store_true",
        help="Output profile in JSON format.",
    )

    enable_p = profile_subparsers.add_parser(
        "enable",
        help="Enable a profile.",
        description=(
            "Enable a previously disabled profile.\n\n"
            "Restores the profile to the round-robin pool with previous health."
        ),
        epilog=("Examples:\n  magy profile enable work"),
        formatter_class=fmt,
    )
    enable_p.add_argument("name", help="Profile name.")

    disable_p = profile_subparsers.add_parser(
        "disable",
        help="Disable a profile.",
        description=(
            "Disable a profile.\n\n"
            "Excludes the profile from round-robin while preserving configuration,\n"
            "tokens, and health status for when it is re-enabled."
        ),
        epilog=("Examples:\n  magy profile disable work"),
        formatter_class=fmt,
    )
    disable_p.add_argument("name", help="Profile name.")

    reset_p = profile_subparsers.add_parser(
        "reset-health",
        help="Reset health of a profile to healthy.",
        description=(
            "Reset profile health to healthy.\n\n"
            "Clears active cooldown timer, failure reason, and error classification,\n"
            "immediately making the profile eligible for round-robin selection."
        ),
        epilog=("Examples:\n  magy profile reset-health work"),
        formatter_class=fmt,
    )
    reset_p.add_argument("name", help="Profile name.")

    remove_p = profile_subparsers.add_parser(
        "remove",
        help="Remove a profile.",
        description=(
            "Remove a profile from Magy.\n\n"
            "Deletes the profile record and its isolated home directory and storage.\n"
            "Active profiles running operations cannot be removed."
        ),
        epilog=(
            "Examples:\n  magy profile remove work\n  magy profile remove work --force"
        ),
        formatter_class=fmt,
    )
    remove_p.add_argument("name", help="Profile name.")
    remove_p.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Force removal without interactive confirmation.",
    )

    help_p = profile_subparsers.add_parser(
        "help",
        help="Show help for a profile action (e.g., 'magy profile help add').",
        description=(
            "Show help for a profile action.\n\n"
            "Displays detailed usage and options for any profile action."
        ),
        epilog=(
            "Examples:\n"
            "  magy profile help add\n"
            "  magy profile help auth\n"
            "  magy profile help list\n"
            "  magy profile help show\n"
            "  magy profile help remove"
        ),
        formatter_class=fmt,
    )
    help_p.add_argument(
        "action",
        nargs="?",
        help="Profile action to display help for (e.g. 'add', 'auth', 'list', 'show').",
    )

    help_parser = subparsers.add_parser(
        "help",
        help="Show help for magy or a specific subcommand.",
        description=(
            "Show help for magy or a specific subcommand.\n\n"
            "Displays top-level help or detailed help for any subcommand\n"
            "(e.g., 'doctor', 'status', 'profile', or 'profile <action>')."
        ),
        epilog=(
            "Examples:\n"
            "  magy help\n"
            "  magy help doctor\n"
            "  magy help status\n"
            "  magy help profile\n"
            "  magy help profile add\n"
            "  magy help profile auth\n"
            "  magy help profile list\n"
            "  magy help profile show\n"
            "  magy help profile remove"
        ),
        formatter_class=fmt,
    )
    help_parser.add_argument(
        "topic",
        nargs="*",
        help="Topic to display help for (e.g. 'profile', 'doctor', 'status').",
    )

    worker_p = subparsers.add_parser("worker", help=argparse.SUPPRESS)
    worker_p.add_argument("run_id", help="Run ID")

    return parser


def run_doctor(args: argparse.Namespace) -> int:
    diag = collect_diagnostics()

    if getattr(args, "json", False):
        data = {
            "magy_version": diag.magy_version,
            "python_version": diag.python_version,
            "platform": diag.platform_info,
            "paths": {
                "config_dir": str(diag.config_dir) if diag.config_dir else None,
                "data_dir": str(diag.data_dir) if diag.data_dir else None,
                "state_dir": str(diag.state_dir) if diag.state_dir else None,
            },
            "config_error": diag.config_error,
            "agy": {
                "executable": str(diag.executable) if diag.executable else None,
                "discovery_source": diag.discovery_source,
                "version": diag.agy_version,
                "compatibility": diag.compatibility_note,
            },
            "healthy": diag.is_healthy,
            "missing_prerequisites": diag.missing_prerequisites,
        }
        print(json.dumps(data, indent=2))
        return 0 if diag.is_healthy else 1

    print("Magy Diagnostics")
    print("================")
    print(f"Magy Version:    {diag.magy_version}")
    print(f"Python:          {diag.python_version}")
    print(f"Platform:        {diag.platform_info}")
    print()
    print("Storage Roots:")
    print(f"  Config:        {diag.config_dir or 'ERROR'}")
    print(f"  Data:          {diag.data_dir or 'ERROR'}")
    print(f"  State:         {diag.state_dir or 'ERROR'}")
    print()
    if diag.config_error:
        print(f"Config Error:    {diag.config_error}")
        print()
    print("Antigravity (Agy):")
    if diag.executable:
        print(f"  Executable:    {diag.executable} (via {diag.discovery_source})")
        print(f"  Version:       {diag.agy_version or 'unknown'}")
        if diag.compatibility_note:
            print(f"  Compatibility: {diag.compatibility_note}")
    else:
        print(f"  Executable:    NOT FOUND ({diag.discovery_source})")
    print()

    if diag.is_healthy:
        print("Status: OK - all prerequisites satisfied.")
        return 0
    else:
        print("Status: FAILED - missing prerequisites:")
        for item in diag.missing_prerequisites:
            print(f"  - {item}")
        return 1


def run_status(args: argparse.Namespace) -> int:
    from magy.profiles import load_profiles
    from magy.routing import get_routing_status

    status = get_routing_status()
    profiles = load_profiles()

    if getattr(args, "json", False):
        out = {
            **status,
            "profiles": [p.to_dict() for p in profiles.values()],
        }
        print(json.dumps(out, indent=2))
        return 0

    print("Magy Status")
    print("===========")
    print(f"Total Profiles:     {status['total_profiles']}")
    print(f"Enabled Profiles:   {status['enabled_profiles']}")
    print(f"Available Profiles: {status['available_profiles']}")
    print(f"Healthy Profiles:   {status['healthy_profiles']}")
    print(f"Untested Profiles:  {status['untested_profiles']}")
    print(f"In Cooldown:        {status['cooldown_profiles']}")
    print(f"Current Cursor:     {status['cursor'] or 'none'}")
    return 0


def format_cooldown(cooldown_until: float | None, reason: str | None) -> str:
    if cooldown_until is None:
        return "-"
    remaining = cooldown_until - time.time()
    if remaining <= 0:
        return "-"
    reason_str = f" ({reason})" if reason else ""
    return f"{remaining:.0f}s remaining{reason_str}"


def handle_profile_command(
    args: argparse.Namespace, remaining: list[str], parser: argparse.ArgumentParser
) -> int:
    from magy.profiles import (
        add_profile,
        disable_profile,
        enable_profile,
        get_profile,
        load_profiles,
        remove_profile,
        reset_profile_health,
        run_in_profile,
    )

    action = args.profile_action
    if action and any(h in remaining for h in ("help", "-h", "--help")):
        try:
            parser.parse_args(["profile", action, "--help"])
        except SystemExit as exc:
            if exc.code == 0:
                return 0
            raise
        return 0

    if (
        action
        in (
            "add",
            "create",
            "auth",
            "run",
            "show",
            "enable",
            "disable",
            "reset-health",
            "remove",
        )
        and getattr(args, "name", None) == "help"
    ):
        try:
            parser.parse_args(["profile", action, "--help"])
        except SystemExit as exc:
            if exc.code == 0:
                return 0
            raise
        return 0

    if action in ("add", "create"):
        if remaining:
            parser.error(f"Unrecognized arguments: {' '.join(remaining)}")
        is_current = getattr(args, "current", False)
        kind = "external" if is_current else "managed"
        try:
            if action == "create":
                add_profile(args.name, kind="managed")
                print(f"Created profile '{args.name}'")
            else:
                add_profile(args.name, kind=kind)
                print(f"Added {kind} profile '{args.name}'")
            return 0
        except (
            RuntimeError,
            ValueError,
            FileNotFoundError,
            PermissionError,
            OSError,
        ) as e:
            print(f"magy: error: {e}", file=sys.stderr)
            return 1

    elif action in ("auth", "run"):
        if remaining:
            parser.error(f"Unrecognized arguments: {' '.join(remaining)}")
        extra = list(getattr(args, "extra_args", []))
        if extra and extra[0] == "--":
            extra = extra[1:]
        try:
            return run_in_profile(args.name, extra, update_health=True)
        except subprocess.TimeoutExpired as e:
            print(
                f"magy: error: command timed out after {e.timeout} seconds",
                file=sys.stderr,
            )
            return 124
        except (
            RuntimeError,
            ValueError,
            KeyError,
            FileNotFoundError,
            PermissionError,
            OSError,
        ) as e:
            print(f"magy: error: {e}", file=sys.stderr)
            return 1

    elif action == "list":
        if remaining:
            parser.error(f"Unrecognized arguments: {' '.join(remaining)}")
        profiles = load_profiles()
        if getattr(args, "json", False):
            print(json.dumps([p.to_dict() for p in profiles.values()], indent=2))
            return 0

        if not profiles:
            print("No profiles registered.")
            return 0

        headers = (
            f"{'NAME':<20} {'KIND':<10} {'ENABLED':<9} {'HEALTH':<16} {'COOLDOWN'}"
        )
        print(headers)
        print("-" * len(headers))
        for p in profiles.values():
            en = "yes" if p.enabled else "no"
            cd = format_cooldown(p.cooldown_until, p.cooldown_reason)
            print(f"{p.name:<20} {p.kind:<10} {en:<9} {p.health:<16} {cd}")
        return 0

    elif action == "show":
        if remaining:
            parser.error(f"Unrecognized arguments: {' '.join(remaining)}")
        try:
            p = get_profile(args.name)
            if p is None:
                print(f"magy: error: Profile '{args.name}' not found", file=sys.stderr)
                return 1
            if getattr(args, "json", False):
                print(json.dumps(p.to_dict(), indent=2))
                return 0

            print(f"Profile: {p.name}")
            print(f"  Kind:             {p.kind}")
            print(f"  Home:             {p.resolved_home()}")
            print(f"  Enabled:          {'yes' if p.enabled else 'no'}")
            print(f"  Available:        {'yes' if p.is_available() else 'no'}")
            print(f"  Health:           {p.health}")
            cd_str = format_cooldown(p.cooldown_until, p.cooldown_reason)
            if cd_str != "-":
                print(f"  Cooldown:         {cd_str}")
            if p.last_selected_at:
                print(f"  Last Selected:    {time.ctime(p.last_selected_at)}")
            if p.last_success_at:
                print(f"  Last Success:     {time.ctime(p.last_success_at)}")
            if p.last_failure_at:
                print(f"  Last Failure:     {time.ctime(p.last_failure_at)}")
            return 0
        except ValueError as e:
            print(f"magy: error: {e}", file=sys.stderr)
            return 1

    elif action == "enable":
        if remaining:
            parser.error(f"Unrecognized arguments: {' '.join(remaining)}")
        try:
            enable_profile(args.name)
            print(f"Enabled profile '{args.name}'")
            return 0
        except (KeyError, ValueError) as e:
            print(f"magy: error: {e}", file=sys.stderr)
            return 1

    elif action == "disable":
        if remaining:
            parser.error(f"Unrecognized arguments: {' '.join(remaining)}")
        try:
            disable_profile(args.name)
            print(f"Disabled profile '{args.name}'")
            return 0
        except (KeyError, ValueError) as e:
            print(f"magy: error: {e}", file=sys.stderr)
            return 1

    elif action == "reset-health":
        if remaining:
            parser.error(f"Unrecognized arguments: {' '.join(remaining)}")
        try:
            reset_profile_health(args.name)
            print(f"Reset health for profile '{args.name}' to healthy")
            return 0
        except (KeyError, ValueError) as e:
            print(f"magy: error: {e}", file=sys.stderr)
            return 1

    elif action == "remove":
        if remaining:
            parser.error(f"Unrecognized arguments: {' '.join(remaining)}")
        force = getattr(args, "force", False)
        if not force:
            if not sys.stdin.isatty():
                print(
                    f"magy: error: Profile removal of '{args.name}' "
                    "requires --force in noninteractive mode",
                    file=sys.stderr,
                )
                return 1
            prompt_msg = (
                f"Are you sure you want to remove profile '{args.name}'? [y/N]: "
            )
            confirm = input(prompt_msg)
            if confirm.strip().lower() not in ("y", "yes"):
                print("Cancelled.")
                return 0
        try:
            remove_profile(args.name, force=force)
            print(f"Removed profile '{args.name}'")
            return 0
        except (KeyError, ValueError, RuntimeError, OSError) as e:
            print(f"magy: error: {e}", file=sys.stderr)
            return 1

    elif action == "help":
        target = getattr(args, "action", None)
        try:
            if target:
                parser.parse_args(["profile", target, "--help"])
            else:
                parser.parse_args(["profile", "--help"])
        except SystemExit as exc:
            if exc.code == 0:
                return 0
            raise
        return 0

    else:
        try:
            parser.parse_args(["profile", "--help"])
        except SystemExit as exc:
            if exc.code == 0:
                return 0
            raise
        return 0


def handle_help_command(topics: list[str], parser: argparse.ArgumentParser) -> int:
    clean_topics = [t for t in topics if t != "help"]
    if not clean_topics or clean_topics[0] in ("-h", "--help", "-p", "--profile"):
        parser.print_help()
        return 0

    cmd = clean_topics[0]
    sub_topics = clean_topics[1:]

    if cmd == "profile":
        if not sub_topics:
            try:
                parser.parse_args(["profile", "--help"])
            except SystemExit as exc:
                if exc.code == 0:
                    return 0
                raise
            return 0
        action = sub_topics[0]
        try:
            parser.parse_args(["profile", action, "--help"])
        except SystemExit as exc:
            if exc.code == 0:
                return 0
            raise
        return 0
    elif cmd in ("doctor", "status", "worker"):
        try:
            parser.parse_args([cmd, "--help"])
        except SystemExit as exc:
            if exc.code == 0:
                return 0
            raise
        return 0
    else:
        print(
            f"magy: error: unknown help topic '{' '.join(clean_topics)}'. "
            f"Run 'magy help' or 'magy --help' for available commands.",
            file=sys.stderr,
        )
        return 2


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    parser = build_parser()

    if not argv:
        parser.print_help()
        return 0

    try:
        from importlib.metadata import version as pkg_version

        v_str = pkg_version("magy")
    except Exception:
        v_str = "0.1.0"

    if "--" in argv:
        dash_idx = argv.index("--")
        pre_args = argv[:dash_idx]
        post_args = argv[dash_idx + 1 :]
    else:
        pre_args = list(argv)
        post_args = None

    # Handle explicit top-level help command
    if pre_args and pre_args[0] == "help":
        return handle_help_command(pre_args[1:], parser)

    # Subcommands
    if argv[0] in ("doctor", "status", "profile", "worker"):
        if len(argv) > 1 and argv[1] == "help":
            return handle_help_command(argv, parser)
        try:
            args, remaining = parser.parse_known_args(argv)
        except SystemExit as exc:
            if exc.code == 0:
                return 0
            raise
        if args.subcommand == "doctor":
            if remaining:
                if any(h in remaining for h in ("help", "-h", "--help")):
                    try:
                        parser.parse_args(["doctor", "--help"])
                    except SystemExit as exc:
                        if exc.code == 0:
                            return 0
                        raise
                    return 0
                parser.error(f"Unrecognized arguments: {' '.join(remaining)}")
            return run_doctor(args)
        elif args.subcommand == "status":
            if remaining:
                if any(h in remaining for h in ("help", "-h", "--help")):
                    try:
                        parser.parse_args(["status", "--help"])
                    except SystemExit as exc:
                        if exc.code == 0:
                            return 0
                        raise
                    return 0
                parser.error(f"Unrecognized arguments: {' '.join(remaining)}")
            return run_status(args)
        elif args.subcommand == "profile":
            return handle_profile_command(args, remaining, parser)
        elif args.subcommand == "worker":
            if remaining:
                parser.error(f"Unrecognized arguments: {' '.join(remaining)}")
            from magy.worker import run_worker

            return run_worker(args.run_id)

    if "-h" in pre_args or "--help" in pre_args:
        parser.print_help()
        return 0

    if "-V" in pre_args or "--version" in pre_args:
        print(f"magy {v_str}")
        return 0

    # Check for misplaced subcommands when no '--' was given
    if post_args is None:
        for sub in ("doctor", "status", "profile", "help"):
            if sub in argv:
                parser.error(
                    f"misplaced subcommand '{sub}': subcommands must precede "
                    f"options or use 'magy {sub}'"
                )

    # Agy passthrough mode
    target_profile: str | None = None
    agy_args: list[str] = []

    if post_args is not None:
        pre_parser = argparse.ArgumentParser(prog="magy", add_help=False)
        pre_parser.add_argument("-p", "--profile", dest="profile")
        try:
            pre_args_parsed, pre_remaining = pre_parser.parse_known_args(pre_args)
        except SystemExit:
            parser.error("argument --profile: expected one argument")
        if pre_remaining:
            parser.error(f"Unrecognized arguments: {' '.join(pre_remaining)}")
        target_profile = pre_args_parsed.profile
        if target_profile == "":
            parser.error("argument --profile: cannot be empty")
        agy_args = list(post_args)
    else:
        if argv[0] in ("-p", "--profile"):
            if len(argv) < 2:
                parser.error("argument --profile: expected one argument")
            if argv[1] == "--":
                parser.error("argument --profile: expected one argument")
            target_profile = argv[1]
            if not target_profile:
                parser.error("argument --profile: cannot be empty")
            agy_args = argv[2:]
        elif argv[0].startswith("--profile="):
            target_profile = argv[0].split("=", 1)[1]
            if not target_profile:
                parser.error("argument --profile: cannot be empty")
            agy_args = argv[1:]
        elif argv[0].startswith("-p="):
            target_profile = argv[0].split("=", 1)[1]
            if not target_profile:
                parser.error("argument --profile: cannot be empty")
            agy_args = argv[1:]
        elif argv[0].startswith("-p") and len(argv[0]) > 2:
            target_profile = argv[0][2:]
            agy_args = argv[1:]
        else:
            agy_args = list(argv)

    # Select profile (explicit or round-robin)
    from magy.profiles import run_in_profile
    from magy.routing import NoAvailableProfileError, select_profile

    try:
        selected = select_profile(explicit_name=target_profile)
    except (KeyError, ValueError, NoAvailableProfileError) as e:
        print(f"magy: error: {e}", file=sys.stderr)
        return 1

    # Print selected profile to stderr so stdout remains scriptable
    sys.stderr.write(f"[magy] using profile: {selected.name}\n")
    sys.stderr.flush()

    try:
        return run_in_profile(
            selected.name,
            agy_args,
            inject_log_file=True,
            sync_settings=True,
            update_health=True,
            expected_incarnation_id=selected.incarnation_id,
        )
    except subprocess.TimeoutExpired as e:
        print(
            f"magy: error: command timed out after {e.timeout} seconds",
            file=sys.stderr,
        )
        return 124
    except (
        KeyError,
        RuntimeError,
        ValueError,
        FileNotFoundError,
        PermissionError,
        OSError,
    ) as e:
        print(f"magy: error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
