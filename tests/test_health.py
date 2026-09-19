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

    # Provider-prefixed credentials (H4)
    raw5 = "Error: OPENAI_API_KEY=sk-proj-abc12345 failed verification"
    san5 = sanitize_reason(raw5)
    assert "sk-proj-abc12345" not in san5
    assert "OPENAI_API_KEY=[REDACTED]" in san5

    raw6 = "Request failed: GOOGLE_ACCESS_TOKEN=ya29.a0AfH6SMD_secret123456 expired"
    san6 = sanitize_reason(raw6)
    assert "ya29.a0AfH6SMD_secret123456" not in san6
    assert "GOOGLE_ACCESS_TOKEN=[REDACTED]" in san6

    raw7 = (
        "Auth error: AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        " invalid"
    )
    san7 = sanitize_reason(raw7)
    assert "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY" not in san7
    assert "AWS_SECRET_ACCESS_KEY=[REDACTED]" in san7

    raw8 = '{"OPENAI_API_KEY": "sk-12345", "ANTHROPIC_API_KEY": "sk-ant-999"}'
    san8 = sanitize_reason(raw8)
    assert "sk-12345" not in san8
    assert "sk-ant-999" not in san8
    assert '"OPENAI_API_KEY": "[REDACTED]"' in san8
    assert '"ANTHROPIC_API_KEY": "[REDACTED]"' in san8


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


def test_sanitize_reason_compound_and_oauth_credentials():
    """H5: Redact all compound OAuth tokens, Basic auth, JSON fields, and URLs."""
    # access_token and refresh_token
    s1 = sanitize_reason("Error: access_token=super-secret-value and more")
    assert "super-secret-value" not in s1
    assert "access_token=[REDACTED]" in s1

    s2 = sanitize_reason("Failed with refresh_token=refresh-secret-123")
    assert "refresh-secret-123" not in s2
    assert "refresh_token=[REDACTED]" in s2

    # client_secret, apiKey, x-api-key
    s3 = sanitize_reason("client_secret=top-secret-client apiKey=key-xyz-1234")
    assert "top-secret-client" not in s3
    assert "key-xyz-1234" not in s3
    assert "client_secret=[REDACTED]" in s3
    assert "apiKey=[REDACTED]" in s3

    s4 = sanitize_reason("Header x-api-key: secret-x-key rejected")
    assert "secret-x-key" not in s4
    assert "x-api-key=[REDACTED]" in s4

    # Basic authorization header
    s5 = sanitize_reason("Authorization: Basic dXNlcjpwYXNz")
    assert "dXNlcjpwYXNz" not in s5
    assert "Authorization: Basic [REDACTED]" in s5

    # Standalone Basic token
    s6 = sanitize_reason("Invalid credentials Basic dXNlcjpwYXNz")
    assert "dXNlcjpwYXNz" not in s6
    assert "Basic [REDACTED]" in s6

    # JSON quoted key/value
    s7 = sanitize_reason('Payload: {"access_token": "secret-oauth-payload"}')
    assert "secret-oauth-payload" not in s7
    assert '"access_token": "[REDACTED]"' in s7

    # URL credentials
    s8 = sanitize_reason(
        "Request failed: https://alice:supersecret@example.com/api?foo=bar"
    )
    assert "supersecret" not in s8
    assert "alice" not in s8
    assert "[REDACTED_USER]:[REDACTED_PASS]@" in s8
    assert "[REDACTED_QUERY]" in s8


def test_parse_retry_seconds_latest_match():
    """M2: When multiple retry signals exist, pick the latest/winning one."""
    text = "429 retry in 1 hour\nSubsequent retry: 429 retry in 30s"
    assert parse_retry_seconds(text) == 30.0


def test_classify_run_health_conservative_precedence():
    """M2: Conservative precedence across sources: auth > quota > rate > timeout."""
    # Cross-source: newer stdout auth error wins over older/log rate limit
    c1 = classify_run_health(
        1,
        stdout="401 Unauthorized: authentication required",
        log_content="429 retry in 30s",
    )
    assert c1.health == "auth-required"

    # Cross-source: log auth error still wins over stdout rate limit
    c1_rev = classify_run_health(
        1,
        stdout="429 retry in 30s",
        log_content="401 Unauthorized: authentication required",
    )
    assert c1_rev.health == "auth-required"

    # Quota overrides rate limit across sources
    c2 = classify_run_health(
        1,
        stdout="429 retry in 30s",
        log_content="429 Quota exhausted for model",
    )
    assert c2.health == "quota-exhausted"

    # Rate overrides timeout across sources
    c3 = classify_run_health(
        1,
        stdout="429 retry in 30s",
        log_content="operation timed out after 10s",
    )
    assert c3.health == "rate-limited"
    assert c3.cooldown_seconds == 30.0
