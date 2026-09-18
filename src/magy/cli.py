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
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.subcommand == "doctor":
        return run_doctor(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
