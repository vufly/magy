from magy.agy import classify_run_health


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
