import concurrent.futures

import pytest

from magy.profiles import (
    add_profile,
    disable_profile,
    update_profile_health,
)
from magy.routing import (
    NoAvailableProfileError,
    get_routing_status,
    select_profile,
)


def test_round_robin_selection_order():
    add_profile("prof-a", kind="managed")
    add_profile("prof-b", kind="managed")
    add_profile("prof-c", kind="managed")

    p1 = select_profile()
    p2 = select_profile()
    p3 = select_profile()
    p4 = select_profile()

    assert p1.name == "prof-a"
    assert p2.name == "prof-b"
    assert p3.name == "prof-c"
    assert p4.name == "prof-a"


def test_round_robin_skips_disabled_profile():
    add_profile("p-1", kind="managed")
    add_profile("p-2", kind="managed")
    disable_profile("p-1")

    p = select_profile()
    assert p.name == "p-2"

    p_next = select_profile()
    assert p_next.name == "p-2"


def test_round_robin_skips_cooldown_until_expiry():
    add_profile("prof-1", kind="managed")
    add_profile("prof-2", kind="managed")

    p1 = update_profile_health(
        "prof-1",
        health="rate-limited",
        cooldown_seconds=50.0,
        reason="429",
    )
    t_start = p1.last_failure_at or 0.0

    # During cooldown, only prof-2 is available
    p_during = select_profile(now=t_start + 10.0)
    assert p_during.name == "prof-2"

    # After cooldown expires, prof-1 becomes eligible again
    p_after = select_profile(now=t_start + 60.0)
    assert p_after.name == "prof-1"


def test_select_profile_explicit():
    add_profile("explicit-1", kind="managed")
    add_profile("explicit-2", kind="managed")

    p = select_profile(explicit_name="explicit-2")
    assert p.name == "explicit-2"


def test_select_profile_explicit_disabled_fails():
    add_profile("disabled-exp", kind="managed")
    disable_profile("disabled-exp")

    with pytest.raises(ValueError, match="is disabled"):
        select_profile(explicit_name="disabled-exp")


def test_select_profile_explicit_nonexistent_fails():
    with pytest.raises(ValueError, match="does not exist"):
        select_profile(explicit_name="ghost")


def test_no_available_profile_error_reports_reasons():
    add_profile("unhealthy-1", kind="managed")
    update_profile_health(
        "unhealthy-1",
        health="quota-exhausted",
        cooldown_seconds=300.0,
        reason="No quota",
    )

    with pytest.raises(NoAvailableProfileError) as exc_info:
        select_profile(now=100.0)

    err = str(exc_info.value)
    assert "unhealthy-1" in err
    assert "quota-exhausted" in err


def test_concurrent_cursor_advancement():
    for i in range(5):
        add_profile(f"worker-{i}", kind="managed")

    def _worker():
        return select_profile().name

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(_worker) for _ in range(25)]
        selected_names = [f.result() for f in futures]

    assert len(selected_names) == 25
    # Every worker should have been selected approximately equally
    for i in range(5):
        count = selected_names.count(f"worker-{i}")
        assert count == 5


def test_routing_status():
    add_profile("stat-a", kind="managed")
    add_profile("stat-b", kind="managed")
    select_profile()

    status = get_routing_status()
    assert status["total_profiles"] == 2
    assert status["enabled_profiles"] == 2
    assert status["healthy_profiles"] == 2
    assert status["cursor"] == "stat-a"
