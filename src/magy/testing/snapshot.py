import os
import stat
from pathlib import Path
from typing import Any


def take_snapshot(target_dir: Path) -> dict[str, dict]:
    """Capture metadata snapshot without reading file contents."""
    snapshot = {}
    if not target_dir.exists():
        return snapshot

    for root, dirs, files in os.walk(target_dir):
        for name in sorted(dirs + files):
            p = Path(root) / name
            try:
                st = p.stat()
                rel = str(p.relative_to(target_dir))
                snapshot[rel] = {
                    "size": st.st_size,
                    "mode": oct(stat.S_IMODE(st.st_mode)),
                    "mtime": st.st_mtime,
                    "is_dir": p.is_dir(),
                }
            except OSError:
                pass
    return snapshot


def compare_snapshots(
    before: dict[str, dict],
    after: dict[str, dict],
    ignore_prefixes: list[str] | None = None,
) -> dict[str, Any]:
    """Compare two metadata snapshots and return added, removed, and modified paths."""
    if ignore_prefixes is None:
        ignore_prefixes = []

    added = []
    removed = []
    modified = []

    all_keys = set(before.keys()) | set(after.keys())
    for k in sorted(all_keys):
        if any(k.startswith(pfx) for pfx in ignore_prefixes):
            continue
        if k not in before:
            added.append(k)
        elif k not in after:
            removed.append(k)
        else:
            b = before[k]
            a = after[k]
            if (
                b["size"] != a["size"]
                or b["mode"] != a["mode"]
                or b["mtime"] != a["mtime"]
            ):
                modified.append(k)

    return {
        "added": added,
        "removed": removed,
        "modified": modified,
        "identical": len(added) == 0 and len(removed) == 0 and len(modified) == 0,
    }
