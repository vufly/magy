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


@pytest.mark.skipif(os.name == "nt", reason="Symlink tests for POSIX")
def test_sync_rejects_destination_symlink_to_token(tmp_path: Path):
    """H1: Destination symlink pointing to oauth token must NOT overwrite token."""
    add_profile("dest-symlink-p", kind="managed")
    target_gemini = get_profile_home_dir("dest-symlink-p") / ".gemini"

    # Profile has an existing OAuth token
    cli_dir = target_gemini / "antigravity-cli"
    cli_dir.mkdir(parents=True, exist_ok=True)
    token_file = cli_dir / "antigravity-oauth-token"
    token_file.write_text("CRITICAL_OAUTH_TOKEN", encoding="utf-8")

    # Attacker places a symlink at target settings.json pointing to token_file
    target_settings = target_gemini / "settings.json"
    target_settings.symlink_to(token_file)

    # Real gemini has a settings.json
    real_gemini = tmp_path / "real_gemini"
    real_gemini.mkdir()
    (real_gemini / "settings.json").write_text('{"theme": "light"}', encoding="utf-8")

    # Perform settings sync
    sync_profile_settings("dest-symlink-p", real_gemini_dir=real_gemini)

    # Token must NOT be overwritten!
    assert token_file.read_text(encoding="utf-8") == "CRITICAL_OAUTH_TOKEN"


def test_sync_prunes_denied_nested_directory_components(tmp_path: Path):
    """H1: Sensitive directory components anywhere in relative path must be pruned."""
    real_gemini = tmp_path / "real_gemini"
    real_gemini.mkdir()

    # Denied directory component inside allowlisted directory 'commands'
    oauth_dir = real_gemini / "commands" / "oauth"
    oauth_dir.mkdir(parents=True)
    (oauth_dir / "payload.bin").write_bytes(b"SENSITIVE_OAUTH_PAYLOAD")

    # Denied directory component inside allowlisted directory 'config'
    creds_dir = real_gemini / "config" / "credentials"
    creds_dir.mkdir(parents=True)
    (creds_dir / "data.json").write_text('{"key": "secret"}', encoding="utf-8")

    # Safe file in commands
    safe_file = real_gemini / "commands" / "safe_script.sh"
    safe_file.write_text("#!/bin/sh\n", encoding="utf-8")

    add_profile("nested-denied-p", kind="managed")
    sync_profile_settings("nested-denied-p", real_gemini_dir=real_gemini)

    target_gemini = get_profile_home_dir("nested-denied-p") / ".gemini"
    assert (target_gemini / "commands" / "safe_script.sh").exists()
    assert not (target_gemini / "commands" / "oauth").exists()
    assert not (target_gemini / "commands" / "oauth" / "payload.bin").exists()
    assert not (target_gemini / "config" / "credentials").exists()
    assert not (target_gemini / "config" / "credentials" / "data.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="Symlink tests for POSIX")
def test_sync_skips_in_root_source_symlinks(tmp_path: Path):
    """H1: In-root source symlinks must not be followed."""
    real_gemini = tmp_path / "real_gemini"
    real_gemini.mkdir()

    safe_target = real_gemini / "original_file.txt"
    safe_target.write_text("original", encoding="utf-8")

    cmds = real_gemini / "commands"
    cmds.mkdir()
    symlink_file = cmds / "linked_cmd.txt"
    symlink_file.symlink_to(safe_target)

    add_profile("src-symlink-p", kind="managed")
    sync_profile_settings("src-symlink-p", real_gemini_dir=real_gemini)

    target_gemini = get_profile_home_dir("src-symlink-p") / ".gemini"
    assert not (target_gemini / "commands" / "linked_cmd.txt").exists()


def test_concurrent_sync_under_lock(tmp_path: Path):
    """M2: Concurrent settings synchronization is serialized and safe."""
    import concurrent.futures

    real_gemini = tmp_path / "real_gemini"
    real_gemini.mkdir()
    (real_gemini / "settings.json").write_text('{"count": 0}', encoding="utf-8")

    add_profile("concurrent-sync-p", kind="managed")

    def _sync():
        return sync_profile_settings("concurrent-sync-p", real_gemini_dir=real_gemini)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(_sync) for _ in range(8)]
        results = [f.result() for f in futures]

    assert len(results) == 8
    target_settings = (
        get_profile_home_dir("concurrent-sync-p") / ".gemini" / "settings.json"
    )
    assert target_settings.read_text(encoding="utf-8") == '{"count": 0}'


def test_sync_profile_settings_copies_gemini_md(tmp_path: Path):
    real_gemini = tmp_path / "real_gemini"
    real_gemini.mkdir()
    (real_gemini / "GEMINI.md").write_text("# Gemini Rules", encoding="utf-8")

    add_profile("gemini-md-p", kind="managed")
    copied = sync_profile_settings("gemini-md-p", real_gemini_dir=real_gemini)
    assert len(copied) == 1

    target_gemini = get_profile_home_dir("gemini-md-p") / ".gemini"
    assert (target_gemini / "GEMINI.md").exists()
    assert (target_gemini / "GEMINI.md").read_text(encoding="utf-8") == "# Gemini Rules"


@pytest.mark.skipif(os.name == "nt", reason="Symlink tests for POSIX")
def test_sync_profile_settings_symlinks_skills_directory(tmp_path: Path):
    real_gemini = tmp_path / "real_gemini"
    real_gemini.mkdir()
    cfg_dir = real_gemini / "config"
    cfg_dir.mkdir()

    # Create external skills directory and symlink inside config/skills
    external_skills = tmp_path / "external_skills"
    external_skills.mkdir()
    skill_a = external_skills / "skill_a"
    skill_a.mkdir()
    (skill_a / "SKILL.md").write_text("name: skill_a", encoding="utf-8")

    (cfg_dir / "skills").symlink_to(external_skills)

    add_profile("skills-sync-p", kind="managed")
    sync_profile_settings("skills-sync-p", real_gemini_dir=real_gemini)

    target_gemini = get_profile_home_dir("skills-sync-p") / ".gemini"
    target_skills = target_gemini / "config" / "skills"

    assert target_skills.is_symlink()
    assert os.readlink(str(target_skills)) == str(cfg_dir / "skills")
    assert (target_skills / "skill_a" / "SKILL.md").exists()
    assert (target_skills / "skill_a" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "name: skill_a"


@pytest.mark.skipif(os.name == "nt", reason="Symlink tests for POSIX")
def test_sync_profile_settings_rejects_symlinked_destination_gemini(tmp_path: Path):
    """H1: Destination .gemini symlink must be rejected, preventing overwrite."""
    real_gemini = tmp_path / "real_gemini"
    real_gemini.mkdir()
    (real_gemini / "settings.json").write_text('{"safe": true}', encoding="utf-8")

    victim_dir = tmp_path / "external_victim"
    victim_dir.mkdir()
    victim_settings = victim_dir / "settings.json"
    victim_settings.write_text("VICTIM_TOKEN", encoding="utf-8")

    add_profile("dest-sym-p", kind="managed")
    p_home = get_profile_home_dir("dest-sym-p")
    target_gemini = p_home / ".gemini"

    # Replace .gemini with symlink to victim directory
    if target_gemini.exists():
        import shutil

        shutil.rmtree(target_gemini)
    target_gemini.symlink_to(victim_dir)

    with pytest.raises(ValueError, match="cannot be a symlink"):
        sync_profile_settings("dest-sym-p", real_gemini_dir=real_gemini)

    # Assert external victim was NOT touched
    assert victim_settings.read_text(encoding="utf-8") == "VICTIM_TOKEN"


@pytest.mark.skipif(os.name == "nt", reason="Symlink tests for POSIX")
def test_sync_profile_settings_rejects_symlinked_source_gemini(tmp_path: Path):
    """H1: Source .gemini root as a symlink must be rejected."""
    real_source = tmp_path / "real_source_dir"
    real_source.mkdir()
    (real_source / "settings.json").write_text('{"safe": true}', encoding="utf-8")

    symlink_source = tmp_path / "symlink_source_gemini"
    symlink_source.symlink_to(real_source)

    add_profile("src-root-sym-p", kind="managed")
    with pytest.raises(
        ValueError, match="Source .gemini directory cannot be a symlink"
    ):
        sync_profile_settings("src-root-sym-p", real_gemini_dir=symlink_source)
