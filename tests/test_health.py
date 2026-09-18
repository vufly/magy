from pathlib import Path

from magy.agy import (
    classify_run_health,
    parse_retry_seconds,
    read_bounded_log_tail,
    sanitize_reason,
)


def test_classify_success():
    res = classify_run_health(0, stdout="output", stderr="")
    assert res.health == "healthy"
    assert res.is_success is True
    assert res.cooldown_seconds is None


def test_classify_auth_required():
    res1 = classify_run_health(
        1, stderr="Error: Please sign in to view available models."
    )
    assert res1.health == "auth-required"
    assert res1.is_success is False
    assert res1.cooldown_seconds == 86400.0

    res2 = classify_run_health(1, stderr="Authentication failed. Run 'agy auth login'.")
    assert res2.health == "auth-required"


def test_classify_auth_in_stdout():
    # stdout inspection when stderr is empty
    res = classify_run_health(
        1, stdout="Please sign in to access this project.", stderr=""
    )
    assert res.health == "auth-required"
    assert res.cooldown_seconds == 86400.0


def test_classify_rate_limited():
    res1 = classify_run_health(
        1, stderr="429 Resource exhausted: Rate limit reached. Try again in 45s."
    )
    assert res1.health == "rate-limited"
    assert res1.cooldown_seconds == 45.0
    assert "retry in 45s" in (res1.reason or "")

    res2 = classify_run_health(1, stderr="Error: 429 Too Many Requests")
    assert res2.health == "rate-limited"
    assert res2.cooldown_seconds == 60.0  # default cooldown


def test_classify_quota_exhausted():
    msg = (
        "Error: You have exceeded your current quota. "
        "Please check your plan and billing details."
    )
    res = classify_run_health(1, stderr=msg)
    assert res.health == "quota-exhausted"
    assert res.cooldown_seconds == 3600.0


def test_classify_timeout():
    res = classify_run_health(1, stderr="Error: Request timed out after 30.0s.")
    assert res.health == "timeout"
    assert res.cooldown_seconds == 30.0


def test_classify_unknown_failure():
    res = classify_run_health(1, stderr="Some unexpected internal error occurred.")
    assert res.health == "unknown-failure"
    assert res.cooldown_seconds == 15.0
    assert "Some unexpected internal error" in (res.reason or "")


def test_latest_signal_precedence():
    # 1. Older auth message, newer rate limit message -> rate-limited must win!
    log_content_rate_wins = (
        "2026-01-01 10:00:00 Authentication failed for key\n"
        "2026-01-01 10:00:05 Retrying...\n"
        "2026-01-01 10:00:10 429 Rate limit reached (retry in 30s)\n"
    )
    res1 = classify_run_health(1, log_content=log_content_rate_wins)
    assert res1.health == "rate-limited"
    assert res1.cooldown_seconds == 30.0

    # 2. Older rate limit message, newer auth failure -> auth-required must win!
    log_content_auth_wins = (
        "2026-01-01 10:00:00 429 Resource exhausted: Rate limit reached\n"
        "2026-01-01 10:00:05 Switching key...\n"
        "2026-01-01 10:00:10 Please sign in to authenticate.\n"
    )
    res2 = classify_run_health(1, log_content=log_content_auth_wins)
    assert res2.health == "auth-required"
    assert res2.cooldown_seconds == 86400.0


def test_parse_retry_seconds_multi_unit():
    assert parse_retry_seconds("retry in 45s") == 45.0
    assert parse_retry_seconds("retry after 10 seconds") == 10.0
    assert parse_retry_seconds("try again in 2 minutes") == 120.0
    assert parse_retry_seconds("retry in 1.5 min") == 90.0
    assert parse_retry_seconds("retry in 1 hour") == 3600.0
    assert parse_retry_seconds("try again after 2 hrs") == 7200.0
    assert parse_retry_seconds("retry in 30") == 30.0
    assert parse_retry_seconds("random text without retry") is None


def test_sanitize_reason_redaction():
    # Bearer tokens
    raw1 = "Authorization: Bearer ya29.a0AfH6SMD_secret123456789"
    san1 = sanitize_reason(raw1)
    assert "ya29" not in san1
    assert "Bearer [REDACTED]" in san1

    # Raw tokens and passwords
    raw2 = "Failed to connect: token='secret_token_val' password=admin123"
    san2 = sanitize_reason(raw2)
    assert "secret_token_val" not in san2
    assert "admin123" not in san2
    assert "token=[REDACTED]" in san2
    assert "password=[REDACTED]" in san2

    # Emails
    raw3 = "User admin.account@example.corp failed authentication"
    san3 = sanitize_reason(raw3)
    assert "admin.account@example.corp" not in san3
    assert "[REDACTED_EMAIL]" in san3

    # Home paths
    raw4 = "/home/vudinhn/.gemini/antigravity-cli/token: access denied"
    san4 = sanitize_reason(raw4)
    assert "/home/vudinhn" not in san4
    assert "~/.gemini" in san4


def test_read_bounded_log_tail(tmp_path: Path):
    log_file = tmp_path / "test.log"

    # Write 100KB of data
    content = ("line " * 20 + "\n") * 1000
    log_file.write_text(content, encoding="utf-8")

    tail = read_bounded_log_tail(log_file, max_bytes=1024)
    assert len(tail.encode("utf-8")) <= 1024 + 10  # approximate boundary
    assert tail.endswith("\n")

    # Nonexistent file returns empty string
    assert read_bounded_log_tail(tmp_path / "nonexistent.log") == ""

    # Invalid UTF-8 bytes decoded safely with errors='replace'
    binary_file = tmp_path / "corrupt.log"
    binary_file.write_bytes(b"Valid line\n\xff\xfe Invalid bytes\nEnd line\n")
    decoded = read_bounded_log_tail(binary_file)
    assert "Valid line" in decoded
    assert "End line" in decoded
