import os
import stat
from pathlib import Path

import pytest

from magy.profiles import (
    add_profile,
    get_profile_home_dir,
    sync_profile_settings,
)


def test_sync_profile_settings_copies_allowlisted_files(tmp_path: Path):
    real_gemini = tmp_path / "real_gemini"
    real_gemini.mkdir()

    # Create allowlisted files
    (real_gemini / "AGENTS.md").write_text("# Agents configuration", encoding="utf-8")
    (real_gemini / "settings.json").write_text('{"theme": "dark"}', encoding="utf-8")
    (real_gemini / "trustedFolders.json").write_text('["/home/user"]', encoding="utf-8")

    cli_dir = real_gemini / "antigravity-cli"
    cli_dir.mkdir()
    (cli_dir / "settings.json").write_text('{"fontSize": 14}', encoding="utf-8")
    (cli_dir / "keybindings.json").write_text('{"key": "ctrl+c"}', encoding="utf-8")

    # Create denied files (must NOT be copied)
    (cli_dir / "antigravity-oauth-token").write_text("SECRET_TOKEN", encoding="utf-8")
    (cli_dir / "token.json").write_text("OAUTH_DATA", encoding="utf-8")
    (cli_dir / "credentials.db").write_bytes(b"SQLITE")
    (cli_dir / "chat_history.sqlite").write_bytes(b"HISTORY")
    (real_gemini / "trajectories.log").write_text("trajectory data", encoding="utf-8")
    (real_gemini / "installation_id").write_text("id-12345", encoding="utf-8")

    # Sync to managed profile
    add_profile("sync-p", kind="managed")
    copied = sync_profile_settings("sync-p", real_gemini_dir=real_gemini)
    assert len(copied) == 5

    target_gemini = get_profile_home_dir("sync-p") / ".gemini"
    assert (target_gemini / "AGENTS.md").exists()
    assert (target_gemini / "settings.json").exists()
    assert (target_gemini / "trustedFolders.json").exists()
    assert (target_gemini / "antigravity-cli" / "settings.json").exists()
    assert (target_gemini / "antigravity-cli" / "keybindings.json").exists()

    # Verify denied files were NEVER copied
    assert not (target_gemini / "antigravity-cli" / "antigravity-oauth-token").exists()
    assert not (target_gemini / "antigravity-cli" / "token.json").exists()
    assert not (target_gemini / "antigravity-cli" / "credentials.db").exists()
    assert not (target_gemini / "antigravity-cli" / "chat_history.sqlite").exists()
    assert not (target_gemini / "trajectories.log").exists()
    assert not (target_gemini / "installation_id").exists()

    # Verify file permissions where supported
    if os.name != "nt":
        mode = stat.S_IMODE((target_gemini / "settings.json").stat().st_mode)
        assert mode == 0o600


def test_sync_profile_settings_copies_allowlisted_directories(tmp_path: Path):
    real_gemini = tmp_path / "real_gemini"
    real_gemini.mkdir()

    # Commands dir
    cmds = real_gemini / "commands"
    cmds.mkdir()
    (cmds / "test.sh").write_text("#!/bin/sh\necho test\n", encoding="utf-8")
    (cmds / "auth_token.txt").write_text("LEAK", encoding="utf-8")  # Denied pattern!

    # Config dir
    cfg = real_gemini / "config"
    cfg.mkdir()
    (cfg / "rules.json").write_text('{"rule": 1}', encoding="utf-8")

    # Policies dir
    pol = real_gemini / "policies"
    pol.mkdir()
    (pol / "safety.yaml").write_text("level: high", encoding="utf-8")

    add_profile("sync-dirs-p", kind="managed")
    sync_profile_settings("sync-dirs-p", real_gemini_dir=real_gemini)

    target_gemini = get_profile_home_dir("sync-dirs-p") / ".gemini"
    assert (target_gemini / "commands" / "test.sh").exists()
    assert not (target_gemini / "commands" / "auth_token.txt").exists()
    assert (target_gemini / "config" / "rules.json").exists()
    assert (target_gemini / "policies" / "safety.yaml").exists()


@pytest.mark.skipif(os.name == "nt", reason="Symlink tests for POSIX")
def test_sync_profile_settings_skips_symlink_escaping_real_gemini(tmp_path: Path):
    real_gemini = tmp_path / "real_gemini"
    real_gemini.mkdir()

    external_dir = tmp_path / "outside_gemini"
    external_dir.mkdir()
    secret_file = external_dir / "external_settings.json"
    secret_file.write_text("OUTSIDE", encoding="utf-8")

    # Symlink inside real_gemini pointing outside
    link_in_gemini = real_gemini / "settings.json"
    link_in_gemini.symlink_to(secret_file)

    add_profile("symlink-sync-p", kind="managed")
    sync_profile_settings("symlink-sync-p", real_gemini_dir=real_gemini)

    target_gemini = get_profile_home_dir("symlink-sync-p") / ".gemini"
    # Escaping symlink must NOT be followed or copied
    assert not (target_gemini / "settings.json").exists()


def test_sync_profile_settings_skips_external_profiles(tmp_path: Path):
    ext_home = tmp_path / "ext_home"
    ext_home.mkdir()

    add_profile("ext-sync-p", kind="external", home_dir=ext_home)
    real_gemini = tmp_path / "real_gemini"
    real_gemini.mkdir()
    (real_gemini / "settings.json").write_text("REAL", encoding="utf-8")

    copied = sync_profile_settings("ext-sync-p", real_gemini_dir=real_gemini)
    assert len(copied) == 0
