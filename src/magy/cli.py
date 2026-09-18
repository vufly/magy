import argparse
import json
import subprocess
import sys
import time

from magy.agy import collect_diagnostics


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="magy",
        description=(
            "Magy: Multi-profile launcher and orchestrator for Antigravity (agy)."
        ),
    )
    parser.add_argument(
        "--profile",
        dest="profile",
        metavar="NAME",
        help="Target profile name for command execution.",
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
    )
    doctor_parser.add_argument(
        "--json",
        action="store_true",
        help="Output diagnostics in JSON format.",
    )

    status_parser = subparsers.add_parser(
        "status",
        help="Show profiles and routing status overview.",
    )
    status_parser.add_argument(
        "--json",
        action="store_true",
        help="Output status in JSON format.",
    )

    profile_parser = subparsers.add_parser(
        "profile",
        help="Manage and bootstrap profiles.",
    )
    profile_subparsers = profile_parser.add_subparsers(
        dest="profile_action", help="Profile actions"
    )

    add_p = profile_subparsers.add_parser("add", help="Add a new profile.")
    add_p.add_argument("name", help="Profile name.")
    add_p.add_argument(
        "--current",
        action="store_true",
        help="Register current user home directory as an external profile.",
    )

    create_p = profile_subparsers.add_parser(
        "create", help="Create a profile directory layout (alias to add)."
    )
    create_p.add_argument("name", help="Profile name.")

    auth_p = profile_subparsers.add_parser(
        "auth",
        help="Launch Agy interactively to authenticate a profile.",
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
    )
    run_p.add_argument("name", help="Profile name.")
    run_p.add_argument(
        "extra_args",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded to agy.",
    )

    list_p = profile_subparsers.add_parser("list", help="List registered profiles.")
    list_p.add_argument(
        "--json",
        action="store_true",
        help="Output profiles in JSON format.",
    )

    show_p = profile_subparsers.add_parser(
        "show", help="Show details for a specific profile."
    )
    show_p.add_argument("name", help="Profile name.")
    show_p.add_argument(
        "--json",
        action="store_true",
        help="Output profile in JSON format.",
    )

    enable_p = profile_subparsers.add_parser("enable", help="Enable a profile.")
    enable_p.add_argument("name", help="Profile name.")

    disable_p = profile_subparsers.add_parser("disable", help="Disable a profile.")
    disable_p.add_argument("name", help="Profile name.")

    reset_p = profile_subparsers.add_parser(
        "reset-health", help="Reset health of a profile to healthy."
    )
    reset_p.add_argument("name", help="Profile name.")

    remove_p = profile_subparsers.add_parser("remove", help="Remove a profile.")
    remove_p.add_argument("name", help="Profile name.")
    remove_p.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Force removal without interactive confirmation.",
    )

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
    print(f"Total Profiles:    {status['total_profiles']}")
    print(f"Enabled Profiles:  {status['enabled_profiles']}")
    print(f"Healthy Profiles:  {status['healthy_profiles']}")
    print(f"Untested Profiles: {status['untested_profiles']}")
    print(f"In Cooldown:       {status['cooldown_profiles']}")
    print(f"Current Cursor:    {status['cursor'] or 'none'}")
    return 0


def format_cooldown(cooldown_until: float | None, reason: str | None) -> str:
    if cooldown_until is None:
        return "-"
    remaining = max(0.0, cooldown_until - time.time())
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
        except (ValueError, FileNotFoundError, PermissionError, OSError) as e:
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
        except (ValueError, FileNotFoundError, PermissionError, OSError) as e:
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
            print(f"  Health:           {p.health}")
            if p.cooldown_until:
                cd_str = format_cooldown(p.cooldown_until, p.cooldown_reason)
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

    else:
        parser.parse_args(["profile", "--help"])
        return 0


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

    if "-h" in pre_args or "--help" in pre_args:
        parser.print_help()
        return 0

    if "-V" in pre_args or "--version" in pre_args:
        print(f"magy {v_str}")
        return 0

    # Subcommands
    if argv[0] in ("doctor", "status", "profile"):
        args, remaining = parser.parse_known_args(argv)
        if args.subcommand == "doctor":
            if remaining:
                parser.error(f"Unrecognized arguments: {' '.join(remaining)}")
            return run_doctor(args)
        elif args.subcommand == "status":
            if remaining:
                parser.error(f"Unrecognized arguments: {' '.join(remaining)}")
            return run_status(args)
        elif args.subcommand == "profile":
            return handle_profile_command(args, remaining, parser)

    # Check for misplaced subcommands when no '--' was given
    if post_args is None:
        for sub in ("doctor", "status", "profile"):
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
        pre_parser.add_argument("--profile", dest="profile")
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
        if argv[0] == "--profile":
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
        else:
            agy_args = list(argv)

    # Select profile (explicit or round-robin)
    from magy.profiles import run_in_profile
    from magy.routing import NoAvailableProfileError, select_profile

    try:
        selected = select_profile(explicit_name=target_profile)
    except (ValueError, NoAvailableProfileError) as e:
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
        )
    except subprocess.TimeoutExpired as e:
        print(
            f"magy: error: command timed out after {e.timeout} seconds",
            file=sys.stderr,
        )
        return 124
    except (ValueError, FileNotFoundError, PermissionError, OSError) as e:
        print(f"magy: error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
