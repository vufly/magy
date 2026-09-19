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
    assert status["untested_profiles"] == 2
    assert status["healthy_profiles"] == 0
    assert status["cursor"] == "stat-a"

    # When one is verified healthy
    update_profile_health("stat-a", "healthy", is_success=True)
    status2 = get_routing_status()
    assert status2["untested_profiles"] == 1
    assert status2["healthy_profiles"] == 1


def test_selection_persists_last_selected_at():
    from magy.profiles import get_profile

    add_profile("sel-p1", kind="managed")
    add_profile("sel-p2", kind="managed")

    p1_initial = get_profile("sel-p1")
    assert p1_initial.last_selected_at is None

    t0 = 1000.0
    sel = select_profile(now=t0)
    assert sel.name == "sel-p1"
    assert sel.last_selected_at == t0

    # Verify persisted in registry
    p1_after = get_profile("sel-p1")
    assert p1_after.last_selected_at == t0

    # Explicit selection also persists timestamp
    t1 = 2000.0
    sel_exp = select_profile(explicit_name="sel-p2", now=t1)
    assert sel_exp.name == "sel-p2"
    assert sel_exp.last_selected_at == t1
    p2_after = get_profile("sel-p2")
    assert p2_after.last_selected_at == t1


def _mp_worker(barrier, queue, state_dir, config_dir, data_dir):
    import os

    from magy.routing import select_profile

    os.environ["MAGY_STATE_DIR"] = state_dir
    os.environ["MAGY_CONFIG_DIR"] = config_dir
    os.environ["MAGY_DATA_DIR"] = data_dir
    try:
        barrier.wait(timeout=5.0)
        sel = select_profile()
        queue.put(sel.name)
    except Exception as e:
        queue.put(f"ERROR: {e}")


def test_spawned_process_round_robin_distribution():
    """M5: Start selectors concurrently across OS processes using a barrier."""
    import multiprocessing
    import os
    from collections import Counter

    add_profile("proc-1", kind="managed")
    add_profile("proc-2", kind="managed")
    add_profile("proc-3", kind="managed")

    ctx = multiprocessing.get_context("fork" if hasattr(os, "fork") else None)
    barrier = ctx.Barrier(6)
    queue = ctx.Queue()

    state_dir = os.environ["MAGY_STATE_DIR"]
    config_dir = os.environ["MAGY_CONFIG_DIR"]
    data_dir = os.environ["MAGY_DATA_DIR"]

    procs = [
        ctx.Process(
            target=_mp_worker,
            args=(barrier, queue, state_dir, config_dir, data_dir),
        )
        for _ in range(6)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=10.0)

    results = []
    while not queue.empty():
        results.append(queue.get())

    assert len(results) == 6
    assert all(not r.startswith("ERROR") for r in results)
    counts = Counter(results)
    assert counts == {"proc-1": 2, "proc-2": 2, "proc-3": 2}

    status = get_routing_status()
    assert status["cursor"] in ("proc-1", "proc-2", "proc-3")


def test_monotonic_last_selected_at():
    """M3: Keep last_selected_at monotonic even if clock moves backward."""
    add_profile("mono-p", kind="managed")
    sel1 = select_profile(explicit_name="mono-p", now=2000.0)
    assert sel1.last_selected_at == 2000.0

    sel2 = select_profile(explicit_name="mono-p", now=1000.0)
    assert sel2.last_selected_at == 2000.0


def test_select_profile_retries_on_concurrent_removal(monkeypatch):
    """M3: Deterministic handling of concurrent lifecycle changes."""
    import magy.routing

    add_profile("retry-p1", kind="managed")
    add_profile("retry-p2", kind="managed")

    original_record = magy.routing.record_profile_selection
    first_call = True

    def _flaky_record(
        name: str,
        selected_at: float | None = None,
        expected_incarnation_id: str | None = None,
    ):
        nonlocal first_call
        if first_call and name == "retry-p1":
            first_call = False
            raise KeyError(f"Profile '{name}' does not exist")
        return original_record(name, selected_at, expected_incarnation_id)

    monkeypatch.setattr("magy.routing.record_profile_selection", _flaky_record)

    sel = select_profile(now=1000.0)
    assert sel.name == "retry-p2"


def test_record_profile_selection_rejects_recreated_incarnation():
    from magy.profiles import get_profile, record_profile_selection, remove_profile

    add_profile("selection-incarnation-p", kind="managed")
    original = get_profile("selection-incarnation-p")
    assert original is not None

    remove_profile("selection-incarnation-p")
    add_profile("selection-incarnation-p", kind="managed")

    with pytest.raises(ValueError, match="recreated during selection"):
        record_profile_selection(
            "selection-incarnation-p",
            1000.0,
            expected_incarnation_id=original.incarnation_id,
        )
