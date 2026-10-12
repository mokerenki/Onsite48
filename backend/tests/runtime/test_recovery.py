"""Unit tests for deterministic recovery classification."""
import pytest

from app.runtime.recovery import FailureClass, classify_failure, recovery_message


def test_transient_timeout():
    a = classify_failure({"error_code": "timeout", "message": "timed out", "retryable": True})
    assert a.failure_class == FailureClass.TRANSIENT
    assert a.retryable is True
    assert a.backoff_s > 0


def test_rate_limited():
    a = classify_failure({"error_code": "rate_limit", "message": "429 Too Many Requests", "retry_after": 10})
    assert a.failure_class == FailureClass.RATE_LIMITED
    assert a.retry_after_s == 10.0


def test_auth_expired():
    a = classify_failure({"error_code": "unauthorized", "message": "token expired"})
    assert a.failure_class == FailureClass.AUTH_EXPIRED
    assert a.max_retries == 1


def test_generic_agent_computer_error_never_enters_connector_auth_recovery():
    a = classify_failure(
        {
            "error_code": "unauthorized",
            "message": "The target page requires authentication before navigation can continue.",
            "retryable": False,
        },
        tool_name="computer_navigate",
    )
    assert a.failure_class == FailureClass.PERMANENT
    assert a.retryable is False


def test_reconnect_required_provider_error_does_not_schedule_refresh_retry():
    a = classify_failure(
        {
            "error_code": "AUTH_EXPIRED",
            "connector_id": "github",
            "message": "GitHub rejected this connection's authorization.",
            "reconnect_required": True,
        },
        tool_name="github_list_repositories",
    )
    assert a.failure_class == FailureClass.AUTH_EXPIRED
    assert a.retryable is False
    assert a.take_over is True


@pytest.mark.asyncio
async def test_auth_refresh_uses_scoped_connector_runtime(monkeypatch):
    from app.runtime.recovery import maybe_refresh_auth
    from app.tools import connector_tools

    calls = []

    async def fake_refresh(connector_id: str):
        calls.append(connector_id)

    monkeypatch.setattr(connector_tools, "refresh_connector_credentials", fake_refresh)

    result = await maybe_refresh_auth("github_list_repositories", org_id="ignored")

    assert result == {
        "refreshed": True,
        "detail": "credentials refreshed",
        "connector_id": "github",
    }
    assert calls == ["github"]


def test_provider_error_keeps_connector_metadata_at_the_runtime_boundary():
    from app.runtime.loop import _exception_result
    from app.services.provider_errors import ProviderError

    result = _exception_result(
        ProviderError(
            "github",
            "AUTH_EXPIRED",
            "GitHub rejected this connection's authorization.",
            status_code=401,
            reconnect_required=True,
        )
    )

    assert result["error_code"] == "AUTH_EXPIRED"
    assert result["connector_id"] == "github"
    assert result["reconnect_required"] is True


def test_invalid_arguments():
    a = classify_failure({"error_code": "bad_arguments", "message": "missing required field"})
    assert a.failure_class == FailureClass.INVALID_ARGUMENTS


def test_precondition():
    a = classify_failure({"error_code": "not_found", "message": "event does not exist (404)"})
    assert a.failure_class == FailureClass.PRECONDITION_FAILED


def test_permanent():
    a = classify_failure({"error_code": "not_executable", "message": "no executor", "retryable": False})
    assert a.failure_class == FailureClass.PERMANENT
    assert a.retryable is False


def test_loop_detected():
    a = classify_failure({"ok": True}, same_call_count=5, same_call_limit=3)
    assert a.failure_class == FailureClass.LOOP_DETECTED
    assert a.take_over is True


def test_recovery_message_shape():
    a = classify_failure({"error_code": "timeout", "retryable": True})
    msg = recovery_message(a)
    assert "recovery" in msg
    assert msg["recovery"]["class"] == "transient"
