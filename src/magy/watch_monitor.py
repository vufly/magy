import sys

from magy.watches import run_watch_monitor


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for detached watch monitor."""
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("Usage: python -m magy.watch_monitor <watch_id>", file=sys.stderr)
        return 1
    return run_watch_monitor(args[0])


if __name__ == "__main__":
    sys.exit(main())
