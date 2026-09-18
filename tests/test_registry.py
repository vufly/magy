import time
from pathlib import Path

import pytest

from magy.profiles import (
    ProfileMetadata,
    add_profile,
    disable_profile,
    enable_profile,
    get_profile,
    get_profile_dir,
    load_profiles,
    remove_profile,
    reset_profile_health,
    update_profile_health,
)


def test_add_and_get_managed_profile():
    meta = add_profile("worker-1", kind="managed")
    assert meta.name == "worker-1"
    assert meta.kind == "managed"
    assert meta.enabled is True
    assert meta.health == "healthy"
    assert meta.cooldown_until is None

    p = get_profile("worker-1")
    assert p is not None
    assert p.name == "worker-1"
    assert p.kind == "managed"
    assert p.is_available() is True


def test_add_external_profile(tmp_path: Path):
    ext_home = tmp_path / "custom_home"
    ext_home.mkdir()

    meta = add_profile("external-p", kind="external", home_dir=ext_home)
    assert meta.name == "external-p"
    assert meta.kind == "external"
    assert meta.resolved_home() == ext_home.resolve()
    assert meta.is_available() is True


def test_add_external_profile_nonexistent_fails(tmp_path: Path):
    nonexistent = tmp_path / "nonexistent"
    with pytest.raises(
        FileNotFoundError, match="External home directory does not exist"
    ):
        add_profile("bad-ext", kind="external", home_dir=nonexistent)


def test_enable_disable_profile():
    add_profile("toggle-p", kind="managed")
    meta = disable_profile("toggle-p")
    assert meta.enabled is False
    assert meta.health == "disabled"
    assert meta.is_available() is False

    meta2 = enable_profile("toggle-p")
    assert meta2.enabled is True
    assert meta2.health == "healthy"
    assert meta2.is_available() is True


def test_update_and_reset_profile_health():
    add_profile("health-p", kind="managed")

    # Update with failure and cooldown
    now = time.time()
    meta = update_profile_health(
        "health-p",
        health="rate-limited",
        cooldown_seconds=60.0,
        reason="Rate limit exceeded",
        is_success=False,
    )
    assert meta.health == "rate-limited"
    assert meta.cooldown_until is not None
    assert meta.cooldown_until >= now + 50.0
    assert meta.cooldown_reason == "Rate limit exceeded"
    assert meta.is_available(now=now) is False
    # After cooldown expires:
    assert meta.is_available(now=now + 70.0) is True

    # Reset health
    reset_meta = reset_profile_health("health-p")
    assert reset_meta.health == "healthy"
    assert reset_meta.cooldown_until is None
    assert reset_meta.cooldown_reason is None
    assert reset_meta.is_available(now=now) is True


def test_remove_profile_managed_cleans_filesystem():
    add_profile("managed-rm", kind="managed")
    p_dir = get_profile_dir("managed-rm")
    assert p_dir.exists()

    remove_profile("managed-rm", force=True)
    assert get_profile("managed-rm") is None
    assert not p_dir.exists()


def test_remove_profile_external_preserves_filesystem(tmp_path: Path):
    ext_home = tmp_path / "external_home"
    ext_home.mkdir()

    add_profile("ext-rm", kind="external", home_dir=ext_home)
    remove_profile("ext-rm", force=True)
    assert get_profile("ext-rm") is None
    # External directory must NOT be deleted!
    assert ext_home.exists()


def test_load_and_save_profiles():
    add_profile("p1", kind="managed")
    add_profile("p2", kind="managed")

    all_p = load_profiles()
    assert "p1" in all_p
    assert "p2" in all_p
    assert len(all_p) == 2


def test_profile_metadata_serialization():
    meta = ProfileMetadata(
        name="test-meta",
        kind="managed",
        home_dir="/tmp/test",
        enabled=True,
        created_at=100.0,
        last_selected_at=105.0,
        last_success_at=110.0,
        last_failure_at=115.0,
        health="rate-limited",
        cooldown_until=150.0,
        cooldown_reason="429 Too Many Requests",
    )
    d = meta.to_dict()
    assert d["name"] == "test-meta"
    assert d["health"] == "rate-limited"

    rebuilt = ProfileMetadata.from_dict(d)
    assert rebuilt.name == meta.name
    assert rebuilt.cooldown_until == 150.0
    assert rebuilt.cooldown_reason == "429 Too Many Requests"
