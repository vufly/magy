"""Tests for agy subcommand dispatch in magy CLI.

Validates that magy correctly recognises known agy subcommands, strips
-p/--profile from any position in argv, and routes to _run_agy_subcommand
(or the passthrough path for unknown tokens).
"""

from unittest.mock import MagicMock, patch

import pytest

from magy.cli import (
    _AGY_SUBCOMMANDS,
    _extract_profile_flag,
    _first_non_flag,
    main,
)


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------


class TestFirstNonFlag:
    def test_simple_subcommand(self):
        assert _first_non_flag(["models"]) == "models"

    def test_profile_before_sub(self):
        assert _first_non_flag(["-p", "work", "models"]) == "models"

    def test_profile_long_before_sub(self):
        assert _first_non_flag(["--profile", "work", "models"]) == "models"

    def test_profile_equals_before_sub(self):
        assert _first_non_flag(["--profile=work", "models"]) == "models"

    def test_profile_compact_before_sub(self):
        assert _first_non_flag(["-pwork", "models"]) == "models"

    def test_other_flags_skipped(self):
        assert _first_non_flag(["--version", "models"]) == "models"

    def test_none_when_all_flags(self):
        assert _first_non_flag(["-p", "work", "--version"]) is None

    def test_empty(self):
        assert _first_non_flag([]) is None

    def test_sub_then_profile(self):
        assert _first_non_flag(["models", "-p", "work"]) == "models"

    def test_mcp_with_subarg(self):
        assert _first_non_flag(["mcp", "list"]) == "mcp"


class TestExtractProfileFlag:
    def test_no_profile(self):
        profile, rest = _extract_profile_flag(["models"])
        assert profile is None
        assert rest == ["models"]

    def test_short_flag_before(self):
        profile, rest = _extract_profile_flag(["-p", "work", "models"])
        assert profile == "work"
        assert rest == ["models"]

    def test_long_flag_before(self):
        profile, rest = _extract_profile_flag(["--profile", "inviwater", "models"])
        assert profile == "inviwater"
        assert rest == ["models"]

    def test_equals_form(self):
        profile, rest = _extract_profile_flag(["--profile=inviwater", "models"])
        assert profile == "inviwater"
        assert rest == ["models"]

    def test_compact_form(self):
        profile, rest = _extract_profile_flag(["-pinviwater", "models"])
        assert profile == "inviwater"
        assert rest == ["models"]

    def test_profile_after_sub(self):
        profile, rest = _extract_profile_flag(["models", "-p", "work"])
        assert profile == "work"
        assert rest == ["models"]

    def test_profile_middle(self):
        profile, rest = _extract_profile_flag(["mcp", "-p", "work", "list"])
        assert profile == "work"
        assert rest == ["mcp", "list"]

    def test_mcp_list_profile_after(self):
        profile, rest = _extract_profile_flag(["mcp", "list", "--profile", "personal"])
        assert profile == "personal"
        assert rest == ["mcp", "list"]

    def test_no_value_after_flag(self):
        # Malformed: -p at end — leave flag in rest so caller can error.
        profile, rest = _extract_profile_flag(["-p"])
        assert profile is None
        assert "-p" in rest


# ---------------------------------------------------------------------------
# Known agy subcommands set sanity check
# ---------------------------------------------------------------------------


def test_agy_subcommands_set():
    expected = {
        "agent", "agents", "changelog", "install", "mcp",
        "mic-serve", "models", "plugin", "plugins", "remote-control", "update",
    }
    assert _AGY_SUBCOMMANDS == frozenset(expected)
    # help must NOT be in the set (magy wins)
    assert "help" not in _AGY_SUBCOMMANDS


# ---------------------------------------------------------------------------
# Integration tests for main() agy subcommand dispatch
# ---------------------------------------------------------------------------


def _make_profile(name="test-profile"):
    p = MagicMock()
    p.name = name
    p.incarnation_id = "abc"
    return p


@pytest.fixture()
def mock_dispatch():
    """Patch select_profile and run_in_profile to avoid real I/O."""
    selected = _make_profile("test-profile")
    with (
        patch("magy.routing.select_profile", return_value=selected) as sel,
        patch("magy.profiles.run_in_profile", return_value=0) as run,
    ):
        yield sel, run


# Patch sites used inside _run_agy_subcommand (imported lazily there too)
@pytest.fixture()
def mock_run_agy_sub():
    """Patch _run_agy_subcommand directly for cleaner assertions."""
    with patch("magy.cli._run_agy_subcommand", return_value=0) as m:
        yield m


class TestMainAgySubcommandDispatch:
    def test_models_no_profile(self, mock_run_agy_sub):
        ret = main(["models"])
        assert ret == 0
        mock_run_agy_sub.assert_called_once_with(["models"], profile=None)

    def test_models_profile_after(self, mock_run_agy_sub):
        ret = main(["models", "-p", "inviwater"])
        assert ret == 0
        mock_run_agy_sub.assert_called_once_with(["models"], profile="inviwater")

    def test_models_profile_before(self, mock_run_agy_sub):
        ret = main(["-p", "inviwater", "models"])
        assert ret == 0
        mock_run_agy_sub.assert_called_once_with(["models"], profile="inviwater")

    def test_models_long_profile_after(self, mock_run_agy_sub):
        ret = main(["models", "--profile", "work"])
        assert ret == 0
        mock_run_agy_sub.assert_called_once_with(["models"], profile="work")

    def test_models_equals_form(self, mock_run_agy_sub):
        ret = main(["models", "--profile=work"])
        assert ret == 0
        mock_run_agy_sub.assert_called_once_with(["models"], profile="work")

    def test_mcp_list_profile_after(self, mock_run_agy_sub):
        ret = main(["mcp", "list", "-p", "work"])
        assert ret == 0
        mock_run_agy_sub.assert_called_once_with(["mcp", "list"], profile="work")

    def test_mcp_profile_middle(self, mock_run_agy_sub):
        ret = main(["mcp", "-p", "work", "list"])
        assert ret == 0
        mock_run_agy_sub.assert_called_once_with(["mcp", "list"], profile="work")

    def test_changelog_no_profile(self, mock_run_agy_sub):
        ret = main(["changelog"])
        assert ret == 0
        mock_run_agy_sub.assert_called_once_with(["changelog"], profile=None)

    def test_plugin_install(self, mock_run_agy_sub):
        ret = main(["plugin", "install", "some-plugin", "-p", "personal"])
        assert ret == 0
        mock_run_agy_sub.assert_called_once_with(
            ["plugin", "install", "some-plugin"], profile="personal"
        )

    def test_agents_alias(self, mock_run_agy_sub):
        ret = main(["agents"])
        assert ret == 0
        mock_run_agy_sub.assert_called_once_with(["agents"], profile=None)

    def test_dashdash_bypass(self, mock_dispatch):
        """magy -- models should go through raw passthrough, NOT agy-sub mode."""
        sel, run = mock_dispatch
        ret = main(["--", "models"])
        assert ret == 0
        # run_in_profile called directly (passthrough path), not via _run_agy_subcommand
        run.assert_called_once()
        call_args = run.call_args
        assert call_args[0][1] == ["models"]  # agy_args forwarded as-is


# ---------------------------------------------------------------------------
# Help command behaviour
# ---------------------------------------------------------------------------


class TestHelpWithAgySubcommands:
    def test_help_agy_sub_delegates(self, mock_run_agy_sub):
        ret = main(["help", "models"])
        assert ret == 0
        mock_run_agy_sub.assert_called_once_with(["help", "models"])

    def test_help_mcp_delegates(self, mock_run_agy_sub):
        ret = main(["help", "mcp"])
        assert ret == 0
        mock_run_agy_sub.assert_called_once_with(["help", "mcp"])

    def test_help_unknown_topic_error(self, capsys):
        ret = main(["help", "totally-unknown-command"])
        assert ret == 2
        captured = capsys.readouterr()
        assert "unknown help topic" in captured.err

    def test_help_magy_sub_unchanged(self, capsys):
        # magy help doctor should still show magy's doctor help
        ret = main(["help", "doctor"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "doctor" in captured.out.lower() or "doctor" in captured.err.lower()
