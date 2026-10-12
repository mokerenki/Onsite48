"""One structured provider-error contract for every outbound connector call.

Connectors used to talk to providers in three different dialects. Gmail and GitHub
went through ``provider_request`` and got a structured
:class:`ConnectorProviderError`. Slack, Atlassian and the Gmail send path used
``httpx`` directly and called ``response.raise_for_status()``, which raises
``HTTPStatusError`` -- an exception whose message embeds the request URL, the
status line, and a body prefix. That exception reached the executor as an opaque
tool failure, so the runtime could not distinguish "reconnect the account" from
"rate limited, try again in 30 seconds" from "you asked for a repository that does
not exist", and the user was told a retry might help for failures a retry cannot
fix. Gmail send also had a second defect: ``raise_for_status()`` on a *send* meant
a provider 500 after the message was accepted surfaced as a failure, inviting the
agent to send it again.

This module is the single place that turns a transport outcome into a decision:

* one exception type, carrying a machine code, a user-safe message, a
  ``retryable`` flag, a ``reconnect_required`` flag, an optional ``retry_after``,
  the provider status code, and bounded provider-specific context;
* every status class and every transport failure normalized into it;
* no raw token, header, secret-bearing URL, stack trace, or unbounded provider
  body can reach the message. :func:`safe_provider_detail` is the only place
  provider text enters, and it is redacted and length-bounded first.

``app.tools.connector_tools.ConnectorProviderError`` is an alias of
:class:`ProviderError` so existing imports and the executor contract keep working
while there is only one error class in the system.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Mapping, Optional

import httpx
import structlog

logger = structlog.get_logger("onsite48.services.provider_errors")


class ProviderErrorCode(StrEnum):
    """Machine codes the runtime branches on. Values are part of the contract."""

    AUTH_MISSING = "AUTH_MISSING"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    AUTH_REFRESH_FAILED = "AUTH_REFRESH_FAILED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_UNREACHABLE = "PROVIDER_UNREACHABLE"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    PROVIDER_ERROR = "PROVIDER_ERROR"


#: Codes where retrying the identical request later is a sensible recovery. The
#: executor and the planner's recovery prompt both branch on this instead of on
#: the exception class, which is why it lives on the contract.
RETRYABLE_CODES = frozenset({
    ProviderErrorCode.RATE_LIMITED,
    ProviderErrorCode.PROVIDER_UNAVAILABLE,
    ProviderErrorCode.PROVIDER_TIMEOUT,
    ProviderErrorCode.PROVIDER_UNREACHABLE,
})

#: Codes that cannot be fixed by retrying and need the user to do something.
RECONNECT_CODES = frozenset({
    ProviderErrorCode.AUTH_MISSING,
    ProviderErrorCode.AUTH_EXPIRED,
    ProviderErrorCode.AUTH_REFRESH_FAILED,
    ProviderErrorCode.CONFIGURATION_ERROR,
})

#: Hard bound on any provider text that reaches a user-facing message or a
#: structured payload. A provider response is not a log line.
MAX_DETAIL_CHARS = 240
#: Hard bound on how much of a raw body is even inspected before redaction.
MAX_BODY_INSPECTION_CHARS = 4000

_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Authorization / api-key headers, in any of the spellings providers use.
    (re.compile(r"(?i)\b(authorization|proxy-authorization|x-api-key|api[-_]key|apikey|token|secret|password|passwd|client_secret|refresh_token|access_token)\b(\s*[:=]\s*|\"\s*:\s*\"?)([^\s\"',}]+)"), r"\1\2[redacted]"),
    # Bearer / Basic / token credential shapes anywhere in free text.
    (re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._\-+/=]{8,}"), r"\1 [redacted]"),
    # JWT-shaped and long opaque credential shapes.
    (re.compile(r"\beyJ[A-Za-z0-9._\-]{10,}"), "[redacted]"),
    (re.compile(r"\b(?:gh[pousr]|github_pat|glpat|xox[baprs]|sk)-[A-Za-z0-9._\-]{8,}"), "[redacted]"),
    # URL query strings and userinfo: the part of a URL that carries secrets.
    (re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^\s?#]*\?[^\s]*"), r"\1[query redacted]"),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^/\s@]+@"), r"\1[redacted]@"),
    # Python traceback frames: the handler logs the exception, never the message.
    (re.compile(r"(?m)^\s*File \"[^\"]+\", line \d+.*$"), "[stack redacted]"),
    (re.compile(r"(?m)^\s*\^+\s*$"), ""),
    # Newlines and control characters would break a single-line user message.
    (re.compile(r"[\r\n\t]+"), " "),
    (re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"), ""),
)

#: Words providers put in an error body that make it a problem description
#: rather than a reason, and that leak internals when repeated back.
_NOISE_PREFIXES = (
    "traceback (most recent call last)",
    "error:",
    "whitelabel error page",
)


def redact_secrets(text: str) -> str:
    """Strip credentials, query strings, and stack frames from provider text."""
    if not text:
        return ""
    redacted = str(text)[:MAX_BODY_INSPECTION_CHARS]
    for pattern, replacement in _REDACTIONS:
        redacted = pattern.sub(replacement, redacted)
    redacted = re.sub(r"\s{2,}", " ", redacted).strip()
    lowered = redacted.lower()
    for prefix in _NOISE_PREFIXES:
        if lowered.startswith(prefix):
            redacted = redacted[len(prefix):].strip(" :")
            break
    return redacted


def safe_provider_detail(response: httpx.Response) -> str:
    """Read a provider's own error text, redacted and bounded.

    Providers disagree about where the human-readable reason lives. These are
    tried in order and the first non-empty one wins; if none matches, the raw
    text is still used, but only after redaction and truncation. A body that
    cannot be decoded at all yields the empty string rather than an exception,
    because a 500 with a binary body is still a 500.
    """
    try:
        payload: Any = response.json()
    except Exception:
        payload = None

    candidates: list[Any] = []
    if isinstance(payload, Mapping):
        error = payload.get("error")
        if isinstance(error, Mapping):
            candidates.extend([error.get("message"), error.get("description"), error.get("type")])
        elif isinstance(error, str):
            candidates.append(error)
        candidates.extend([
            payload.get("error_description"),
            payload.get("message"),
            payload.get("detail"),
            payload.get("title"),
            payload.get("reason"),
        ])
        errors = error.get("errors") if isinstance(error, Mapping) else None
        if isinstance(errors, list):
            for item in errors[:2]:
                if isinstance(item, Mapping):
                    candidates.append(item.get("message") or item.get("reason"))
    elif isinstance(payload, str):
        candidates.append(payload)

    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return redact_secrets(candidate)[:MAX_DETAIL_CHARS]

    try:
        return redact_secrets(response.text)[:MAX_DETAIL_CHARS]
    except Exception:
        return ""


def _context_phrase(context: Optional[Mapping[str, Any]]) -> str:
    """Render provider-specific context ("the repository acme/widgets") safely.

    The identifier comes from a plan argument, so it is treated as untrusted
    text: control characters are removed and it is length-bounded before it is
    ever concatenated into a message.
    """
    if not context:
        return ""
    kind = str(context.get("resource") or "").strip()
    identifier = context.get("identifier")
    identifier_text = redact_secrets(str(identifier))[:80] if identifier not in (None, "") else ""
    if kind and identifier_text:
        return f" {kind} {identifier_text}"
    if kind:
        return f" {kind}"
    return ""


def context_fields(context: Optional[Mapping[str, Any]]) -> dict[str, str]:
    """Bounded, safe structured context for the error payload.

    Only the two documented keys are carried. Everything else a caller might
    pass is dropped, so a provider response can never ride along inside it.
    """
    if not context:
        return {}
    fields: dict[str, str] = {}
    kind = str(context.get("resource") or "").strip()
    if kind:
        fields["resource"] = redact_secrets(kind)[:64]
    identifier = context.get("identifier")
    if identifier not in (None, ""):
        fields["identifier"] = redact_secrets(str(identifier))[:80]
    return fields


def _parse_retry_after(value: Any) -> int | None:
    if value is None:
        return None
    try:
        seconds = int(str(value).strip())
    except (TypeError, ValueError):
        # An HTTP-date Retry-After is legal; it is not worth a clock dependency
        # here, so it degrades to "no hint" and the contract stays truthful.
        return None
    if seconds < 0:
        return None
    return min(seconds, 3600)


class ProviderError(RuntimeError):
    """The single structured failure every connector HTTP call produces.

    Raised (and only raised) for an outcome the runtime must react to. The
    message is user-safe by construction: it is assembled here from a fixed
    sentence, the connector id, and redacted provider detail, never from an
    exception's own text.
    """

    def __init__(
        self,
        connector_id: str,
        code: str,
        message: str,
        status_code: int | None = None,
        retryable: bool = False,
        reconnect_required: bool = False,
        retry_after_seconds: int | None = None,
        context: Optional[Mapping[str, Any]] = None,
    ):
        super().__init__(message)
        self.connector_id = str(connector_id)
        self.code = str(code)
        self.message = message
        self.status_code = status_code
        self.retryable = bool(retryable)
        self.reconnect_required = bool(reconnect_required)
        self.retry_after_seconds = retry_after_seconds
        self.context = context_fields(context)

    def to_dict(self) -> dict[str, Any]:
        return {
            "connector_id": self.connector_id,
            "code": self.code,
            "message": self.message,
            "status_code": self.status_code,
            "retryable": self.retryable,
            "reconnect_required": self.reconnect_required,
            "retry_after_seconds": self.retry_after_seconds,
            **({"context": self.context} if self.context else {}),
        }

    def as_failure(self) -> dict[str, Any]:
        """The shape the executor stores on a step failure and the UI renders."""
        payload = self.to_dict()
        payload["user_message"] = self.message
        return payload


def provider_error_from_status(
    connector_id: str,
    response: httpx.Response,
    *,
    context: Optional[Mapping[str, Any]] = None,
) -> ProviderError:
    """Normalize any >=400 provider response into the structured contract.

    Every status class the runtime must distinguish gets its own code and its
    own user-facing sentence. A 403 additionally distinguishes "this scope is not
    granted" from a plain permission denial for Google connectors, because the
    first is fixed by reconnecting and the second is not.
    """
    connector = str(connector_id)
    status = int(response.status_code)
    detail = safe_provider_detail(response)
    where = _context_phrase(context)

    if status == 401:
        return ProviderError(
            connector,
            ProviderErrorCode.AUTH_EXPIRED,
            f"{connector} rejected this connection's authorization. Reconnect the account in Settings.",
            status_code=status,
            reconnect_required=True,
            context=context,
        )
    if status == 403:
        lowered = detail.lower()
        google_scope_stale = connector in {"gmail", "google_calendar"} and (
            "insufficientpermissions" in lowered
            or "forbidden" in lowered
            or "permission" in lowered
            or "scope" in lowered
        )
        message = f"{connector} denied permission for this operation{where}."
        if google_scope_stale:
            message += " Reconnect it in Settings so the updated Google scopes can be granted."
        return ProviderError(
            connector,
            ProviderErrorCode.PERMISSION_DENIED,
            message,
            status_code=status,
            reconnect_required=google_scope_stale,
            context=context,
        )
    if status == 404:
        return ProviderError(
            connector,
            ProviderErrorCode.NOT_FOUND,
            f"{connector} could not find{where or ' the requested resource'}.",
            status_code=status,
            context=context,
        )
    if status == 409:
        return ProviderError(
            connector,
            ProviderErrorCode.CONFLICT,
            (
                f"{connector} reported a conflict for{where or ' this request'}. "
                "It may already exist or have changed; re-read the current state before retrying."
            ),
            status_code=status,
            context=context,
        )
    if status == 422 or status == 400:
        return ProviderError(
            connector,
            ProviderErrorCode.INVALID_ARGUMENT,
            f"{connector} rejected the arguments for{where or ' this request'}"
            f"{f': {detail}' if detail else '.'}",
            status_code=status,
            context=context,
        )
    if status == 429:
        retry_after = _parse_retry_after(response.headers.get("Retry-After"))
        return ProviderError(
            connector,
            ProviderErrorCode.RATE_LIMITED,
            f"{connector} rate-limited the request. Please retry shortly.",
            status_code=status,
            retryable=True,
            retry_after_seconds=retry_after,
            context=context,
        )
    if status >= 500:
        return ProviderError(
            connector,
            ProviderErrorCode.PROVIDER_UNAVAILABLE,
            f"{connector} is temporarily unavailable. This is usually temporary.",
            status_code=status,
            retryable=True,
            context=context,
        )
    return ProviderError(
        connector,
        ProviderErrorCode.PROVIDER_ERROR,
        f"{connector} rejected the request{f': {detail}' if detail else '.'}",
        status_code=status,
        context=context,
    )


def provider_error_from_exception(
    connector_id: str,
    exc: BaseException,
    *,
    context: Optional[Mapping[str, Any]] = None,
) -> ProviderError:
    """Normalize a transport failure into the structured contract.

    A timeout and a DNS/connectivity failure are different user experiences --
    one is usually worth retrying now, the other means the provider's host is
    unreachable from this network -- and they were previously indistinguishable
    because both surfaced as ``httpx`` internals.
    """
    connector = str(connector_id)
    if isinstance(exc, httpx.TimeoutException):
        return ProviderError(
            connector,
            ProviderErrorCode.PROVIDER_TIMEOUT,
            f"{connector} did not respond in time. This is usually temporary.",
            retryable=True,
            context=context,
        )
    if isinstance(exc, (httpx.ConnectError, httpx.NetworkError, ConnectionError, OSError)):
        # A connect error can carry the resolved host and port in its text. The
        # class name is logged server-side; nothing derived from it is surfaced.
        return ProviderError(
            connector,
            ProviderErrorCode.PROVIDER_UNREACHABLE,
            f"Could not reach {connector}. The connection may be down or blocked.",
            retryable=True,
            context=context,
        )
    if isinstance(exc, ProviderError):
        return exc
    return ProviderError(
        connector,
        ProviderErrorCode.PROVIDER_ERROR,
        f"{connector} could not complete the request.",
        context=context,
    )


def provider_json(
    response: httpx.Response,
    connector_id: str,
    *,
    context: Optional[Mapping[str, Any]] = None,
) -> Any:
    """Decode a provider body, or fail with the structured contract.

    A 200 with a non-JSON body is a real provider failure -- a proxy error page,
    a truncated response -- and calling ``response.json()`` on it raised a bare
    ``JSONDecodeError`` that reached the executor as an unrelated error. The
    body itself is not surfaced: only the content type and a bounded preview
    after redaction.
    """
    try:
        return response.json()
    except Exception as exc:
        logger.warning(
            "provider.malformed_json",
            connector=str(connector_id),
            status=int(response.status_code),
            content_type=str(response.headers.get("content-type") or "")[:80],
            error=type(exc).__name__,
        )
        raise ProviderError(
            str(connector_id),
            ProviderErrorCode.MALFORMED_RESPONSE,
            f"{connector_id} returned a response this agent could not read. Please retry.",
            status_code=int(response.status_code),
            retryable=True,
            context=context,
        ) from None


async def request_json(
    connector_id: str,
    method: str,
    url: str,
    *,
    access_token: str | None = None,
    timeout: float = 20.0,
    context: Optional[Mapping[str, Any]] = None,
    **kwargs: Any,
) -> Any:
    """Perform an authenticated provider request and return decoded JSON.

    The one call site for every connector that does not need the stored-credential
    retry dance (``provider_request`` in ``connector_tools`` owns that, because it
    has to re-read credentials from the vault). It exists so no connector has a
    reason to reach for ``httpx`` and ``raise_for_status()`` directly:

    * transport failures become ``PROVIDER_TIMEOUT`` / ``PROVIDER_UNREACHABLE``;
    * any ``>=400`` becomes the status class for that response;
    * a ``>=400`` body is never parsed as a success payload;
    * a success body that is not JSON becomes ``MALFORMED_RESPONSE``.
    """
    headers = dict(kwargs.pop("headers", {}) or {})
    if access_token:
        headers.setdefault("Authorization", f"Bearer {access_token}")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(method, url, headers=headers, **kwargs)
    except Exception as exc:
        error = provider_error_from_exception(connector_id, exc, context=context)
        logger.warning(
            "provider.transport_failed",
            connector=str(connector_id),
            method=str(method).upper(),
            code=error.code,
            error=type(exc).__name__,
        )
        raise error from None

    if response.status_code >= 400:
        error = provider_error_from_status(connector_id, response, context=context)
        logger.info(
            "provider.http_error",
            connector=str(connector_id),
            method=str(method).upper(),
            status=error.status_code,
            code=error.code,
            retryable=error.retryable,
            reconnect_required=error.reconnect_required,
        )
        raise error
    return provider_json(response, connector_id, context=context)


def classify(
    connector_id: str,
    exc: BaseException,
    *,
    context: Optional[Mapping[str, Any]] = None,
) -> ProviderError:
    """Coerce any exception into a :class:`ProviderError`.

    Used by the tool boundary so an unexpected exception from a connector can
    never reach the executor as something it does not understand.
    """
    if isinstance(exc, ProviderError):
        return exc
    return provider_error_from_exception(connector_id, exc, context=context)


__all__ = [
    "MAX_BODY_INSPECTION_CHARS",
    "MAX_DETAIL_CHARS",
    "PROVIDER_ERROR_CODES",
    "RECONNECT_CODES",
    "RETRYABLE_CODES",
    "ProviderError",
    "ProviderErrorCode",
    "classify",
    "context_fields",
    "provider_error_from_exception",
    "provider_error_from_status",
    "provider_json",
    "redact_secrets",
    "request_json",
    "safe_provider_detail",
]

#: Exported as a plain tuple so callers can enumerate the contract without
#: importing the enum.
PROVIDER_ERROR_CODES = tuple(code.value for code in ProviderErrorCode)