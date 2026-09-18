import sys
import time
from pathlib import Path
from typing import Any

from magy.config import get_state_dir
from magy.profiles import (
    ProfileMetadata,
    get_profile,
    load_profiles,
)
from magy.storage import atomic_write_json, get_lock, read_json


class NoAvailableProfileError(Exception):
    def __init__(
        self,
        reasons: dict[str, str],
        earliest_cooldown: float | None = None,
    ):
        self.reasons = reasons
        self.earliest_cooldown = earliest_cooldown
        lines = ["No available profile found:"]
        for name, reason in sorted(reasons.items()):
            lines.append(f"  - {name}: {reason}")
        if earliest_cooldown is not None:
            remaining = max(0.0, earliest_cooldown - time.time())
            lines.append(f"Earliest cooldown expires in {remaining:.1f}s")
        super().__init__("\n".join(lines))


def get_routing_file_path() -> Path:
    """Return path to routing.json in magy state directory."""
    return get_state_dir() / "routing.json"


def select_profile(
    explicit_name: str | None = None,
    now: float | None = None,
) -> ProfileMetadata:
    """Select a profile for execution.

    If explicit_name is given, validates it is registered and enabled.
    If automatic, uses locked round-robin advancing cursor past unhealthy profiles.
    """
    if now is None:
        now = time.time()

    if explicit_name:
        profile = get_profile(explicit_name)
        if profile is None:
            raise ValueError(f"Profile '{explicit_name}' does not exist")
        if not profile.enabled:
            raise ValueError(f"Profile '{explicit_name}' is disabled")
        if not profile.is_available(now):
            sys.stderr.write(
                f"[magy] warning: profile '{explicit_name}' is in {profile.health} "
                f"state ({profile.cooldown_reason or 'cooldown'})\n"
            )
            sys.stderr.flush()
        return profile

    routing_path = get_routing_file_path()
    lock = get_lock(routing_path, timeout=5.0)

    with lock:
        profiles = load_profiles()
        if not profiles:
            raise NoAvailableProfileError({"all": "No profiles registered."})

        # Deterministic sorting
        names = sorted(profiles.keys())
        available_names = [n for n in names if profiles[n].is_available(now)]

        if not available_names:
            reasons = {}
            for n in names:
                p = profiles[n]
                if not p.enabled:
                    reasons[n] = "disabled"
                elif p.cooldown_until is not None:
                    remaining = max(0.0, p.cooldown_until - now)
                    reasons[n] = (
                        f"{p.health} ({remaining:.1f}s cooldown remaining: "
                        f"{p.cooldown_reason or 'limit'})"
                    )
                else:
                    reasons[n] = f"{p.health} ({p.cooldown_reason or 'unavailable'})"
            earliest = min(
                (
                    p.cooldown_until
                    for p in profiles.values()
                    if p.cooldown_until is not None
                ),
                default=None,
            )
            raise NoAvailableProfileError(reasons, earliest)

        # Read routing cursor
        routing_state = read_json(
            routing_path, lock=False, default={"cursor": None}
        )
        cursor = routing_state.get("cursor")

        if cursor in names:
            idx = names.index(cursor)
            ordered_search = names[idx + 1 :] + names[: idx + 1]
            selected_name = next(
                (n for n in ordered_search if n in available_names),
                available_names[0],
            )
        else:
            selected_name = available_names[0]

        # Update cursor under lock
        routing_state["cursor"] = selected_name
        routing_state["updated_at"] = now
        atomic_write_json(routing_path, routing_state, lock=False)

    return profiles[selected_name]


def get_routing_status() -> dict[str, Any]:
    """Get current routing status including cursor and profiles overview."""
    profiles = load_profiles()
    routing_path = get_routing_file_path()
    routing_state = read_json(
        routing_path, lock=True, default={"cursor": None}
    )

    now = time.time()
    total = len(profiles)
    enabled = sum(1 for p in profiles.values() if p.enabled)
    healthy = sum(1 for p in profiles.values() if p.enabled and p.health == "healthy")
    cooldown = sum(
        1
        for p in profiles.values()
        if p.enabled and p.cooldown_until is not None and p.cooldown_until > now
    )

    return {
        "cursor": routing_state.get("cursor"),
        "total_profiles": total,
        "enabled_profiles": enabled,
        "healthy_profiles": healthy,
        "cooldown_profiles": cooldown,
    }
