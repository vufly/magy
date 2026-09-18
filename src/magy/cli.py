import argparse
import sys

from magy.agy import collect_diagnostics


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="magy",
        description=(
            "Magy: Multi-profile launcher and orchestrator for Antigravity (agy)."
        ),
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

    profile_parser = subparsers.add_parser(
        "profile",
        help="Manage and bootstrap profiles.",
    )
    profile_subparsers = profile_parser.add_subparsers(
        dest="profile_action", help="Profile actions"
    )

    create_p = profile_subparsers.add_parser(
        "create", help="Create a profile directory layout."
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

    return parser


def run_doctor(args: argparse.Namespace) -> int:
    diag = collect_diagnostics()

    if getattr(args, "json", False):
        import json

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


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    parser = build_parser()
    args, remaining = parser.parse_known_args(argv)

    if args.subcommand == "doctor":
        if remaining:
            parser.error(f"Unrecognized arguments: {' '.join(remaining)}")
        return run_doctor(args)

    if args.subcommand == "profile":
        from magy.profiles import ensure_profile_layout, run_in_profile

        if args.profile_action == "create":
            if remaining:
                parser.error(f"Unrecognized arguments: {' '.join(remaining)}")
            try:
                ensure_profile_layout(args.name)
                print(f"Created profile '{args.name}'")
                return 0
            except (ValueError, PermissionError, OSError) as e:
                print(f"magy: error: {e}", file=sys.stderr)
                return 1

        elif args.profile_action in ("auth", "run"):
            if remaining:
                parser.error(f"Unrecognized arguments: {' '.join(remaining)}")
            extra = list(getattr(args, "extra_args", []))
            if extra and extra[0] == "--":
                extra = extra[1:]
            try:
                return run_in_profile(args.name, extra)
            except (ValueError, FileNotFoundError, PermissionError, OSError) as e:
                print(f"magy: error: {e}", file=sys.stderr)
                return 1
        else:
            parser.parse_args(["profile", "--help"])
            return 0

    if remaining:
        parser.error(f"Unrecognized arguments: {' '.join(remaining)}")

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
