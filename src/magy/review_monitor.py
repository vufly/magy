import sys

from magy.reviews import run_review_monitor


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for detached review monitor."""
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("Usage: python -m magy.review_monitor <review_id>", file=sys.stderr)
        return 1
    return run_review_monitor(args[0])


if __name__ == "__main__":
    sys.exit(main())
