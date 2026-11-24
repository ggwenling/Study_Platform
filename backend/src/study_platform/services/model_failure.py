"""Safe metadata for a model failure. Never store provider text, headers, or bodies."""

import json
import math
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from openai import APIConnectionError, APITimeoutError

from study_platform.core.errors import ApiError

_CODE = re.compile(r"[A-Za-z][A-Za-z0-9._]{0,63}")
_REQUEST_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z",
    re.IGNORECASE,
)
_DELAY_DIGITS = re.compile(r"[0-9]{1,10}\Z")
_CREDENTIAL = re.compile(
    r"(?i)(sk-|akia[0-9a-z]{16}|asia[0-9a-z]{16}|aiza[0-9a-z\-_]{10,}|"
    r"ghp_|github_pat_|xox[baprs]-|bearer\s|-----begin )"
)
_CLASS_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,80}")
_SECRET_WORD = ("secret", "token", "bearer", "apikey", "api_key", "password")
_MAX_RETRY_AFTER_SECONDS = 30
_USAGE_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "input_tokens",
    "output_tokens",
)
_KINDS = {
    "timeout",
    "connection",
    "http_200_incomplete",
    "http_429",
    "http_5xx",
    "http_status",
    "invalid_dimension",
    "invalid_count",
    "invalid_value",
    "invalid_request",
    "auth",
    "cache_permissions",
    "cache_not_directory",
    "local_missing",
    "local_permission",
    "local_io",
    "local_parse",
    "local_verification",
    "api_error",
    "unknown",
}
_RETRYABLE_KINDS = {"timeout", "connection", "http_5xx", "http_429"}
_REQUEST_ID_HEADERS = ("x-request-id", "x-dashscope-request-id", "x-dashscope-requestid")


def _credential_shaped(value):
    lowered = value.lower()
    if any(word in lowered for word in _SECRET_WORD):
        return True
    return _CREDENTIAL.search(value) is not None


def safe_code(value):
    if not isinstance(value, str) or _CODE.fullmatch(value) is None:
        return None
    if _credential_shaped(value):
        return None
    return value


def safe_request_id(value):
    """Keep a UUID request id. Anything else, including credential-shaped text, is omitted."""
    if not isinstance(value, str) or _REQUEST_ID.fullmatch(value) is None:
        return None
    if _credential_shaped(value):
        return None
    return value.lower()


def allow_usage(usage):
    """Token counts only. Missing or unusable usage is unknown, not zero."""
    raw = usage.model_dump() if hasattr(usage, "model_dump") else usage
    if not isinstance(raw, dict):
        return None, "unknown"
    kept = {}
    for key in _USAGE_KEYS:
        value = raw.get(key)
        if type(value) is int and value >= 0:
            kept[key] = value
    if not kept:
        return None, "unknown"
    return kept, "known"


def _http_status(exc):
    response = getattr(exc, "response", None)
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = getattr(response, "status_code", None)
    if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
        return None
    return status


def _provider_code(exc):
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        found = safe_code(body.get("code"))
        if found:
            return found
        error = body.get("error")
        if isinstance(error, dict):
            found = safe_code(error.get("code"))
            if found:
                return found
    return safe_code(getattr(exc, "code", None))


def _request_id(exc):
    found = safe_request_id(getattr(exc, "request_id", None))
    if found:
        return found
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        for name in _REQUEST_ID_HEADERS:
            found = safe_request_id(headers.get(name))
            if found:
                return found
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        found = safe_request_id(body.get("request_id"))
        if found:
            return found
        error = body.get("error")
        if isinstance(error, dict):
            return safe_request_id(error.get("request_id"))
    return None


def interpret_retry_after(raw, *, now=None):
    """Decide from the raw header before any stored field is shortened.

    Absent, a 0–30 second wait, and stop are different. A present header that is
    too large, non-finite, or unreadable is a stop, not a missing header.
    """
    now = now or datetime.now(timezone.utc)
    if raw is None or isinstance(raw, bool):
        return _retry_decision(present=True, state="stop", unparsed=True)
    if isinstance(raw, float) and not math.isfinite(raw):
        return _retry_decision(present=True, state="stop", unparsed=True)
    text = raw.strip() if isinstance(raw, str) else str(int(raw)) if type(raw) is int else None
    if text is None:
        return _retry_decision(present=True, state="stop", unparsed=True)
    if text.isdigit() and len(text) > 10:
        return _retry_decision(present=True, state="stop", exceeds=True)
    if _DELAY_DIGITS.fullmatch(text):
        value = int(text)
        if value > _MAX_RETRY_AFTER_SECONDS:
            return _retry_decision(present=True, state="stop", exceeds=True)
        return _retry_decision(present=True, state="wait", seconds=value)
    if text.lower() in {"nan", "inf", "+inf", "-inf", "infinity", "+infinity"}:
        return _retry_decision(present=True, state="stop", unparsed=True)
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        parsed = None
    if parsed is None:
        return _retry_decision(present=True, state="stop", unparsed=True)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    delay = (parsed - now).total_seconds()
    if not math.isfinite(delay) or delay > _MAX_RETRY_AFTER_SECONDS:
        return _retry_decision(present=True, state="stop", exceeds=math.isfinite(delay))
    seconds = 0 if delay <= 0 else math.ceil(delay)
    if seconds > _MAX_RETRY_AFTER_SECONDS:
        return _retry_decision(present=True, state="stop", exceeds=True)
    return _retry_decision(present=True, state="wait", seconds=int(seconds))


def _retry_decision(*, present, state, seconds=None, exceeds=False, unparsed=False):
    return {
        "present": present,
        "state": state,
        "seconds": seconds,
        "exceeds": exceeds,
        "unparsed": unparsed,
    }


def _retry_after(exc, *, now=None):
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None or "retry-after" not in headers:
        return _retry_decision(present=False, state="absent")
    return interpret_retry_after(headers.get("retry-after"), now=now)


def _kind(exc, status):
    if isinstance(exc, APITimeoutError) or isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, APIConnectionError):
        return "connection"
    if status == 200:
        return "http_200_incomplete"
    if status == 429:
        return "http_429"
    if status is not None and status >= 500:
        return "http_5xx"
    if status in (401, 403):
        return "auth"
    if status in (400, 413, 422):
        return "invalid_request"
    if isinstance(exc, ApiError):
        return {
            "embedding_dimension_invalid": "invalid_dimension",
            "model_timeout": "timeout",
            "model_auth_failed": "auth",
        }.get(exc.code, "api_error")
    if status is not None:
        return "http_status"
    return "unknown"


def retryable(failure):
    """Bounded retries fit transient network, 5xx, and rate throttling only.

    The retry decision uses the header state from before sanitization. A stop
    is not the same as a missing header.
    """
    if failure.get("retry_after_state") == "stop":
        return False
    if failure.get("retry_after_unparsed") or failure.get("retry_after_exceeds"):
        return False
    seconds = failure.get("retry_after_seconds")
    if type(seconds) is int and not isinstance(seconds, bool) and seconds > _MAX_RETRY_AFTER_SECONDS:
        return False
    if failure.get("provider_code") == "Throttling.AllocationQuota":
        return False
    return failure.get("failure_kind") in _RETRYABLE_KINDS


def classify_model_exception(exc):
    status = _http_status(exc)
    kind = _kind(exc, status)
    decision = _retry_after(exc)
    failure = {
        "exception_class": type(exc).__name__ if _CLASS_NAME.fullmatch(type(exc).__name__) else "Exception",
        "error_code": safe_code(exc.code) if isinstance(exc, ApiError) else None,
        "mapped_error_code": None,
        "provider_code": _provider_code(exc),
        "http_status": status,
        "failure_kind": kind if kind in _KINDS else "unknown",
        "usage_status": "unknown",
        "retryable": False,
        "retry_after_present": decision["present"],
        "retry_after_state": decision["state"],
        "retry_after_seconds": decision["seconds"],
        "retry_after_exceeds": decision["exceeds"],
        "retry_after_unparsed": decision["unparsed"],
        "request_id": _request_id(exc),
        "attempts": None,
        "stage": None,
    }
    failure["retryable"] = retryable(failure)
    return failure


def classify_local_exception(exc):
    """File, permission, IO, and parse failures are not provider model errors."""
    if isinstance(exc, FileNotFoundError):
        kind = "local_missing"
    elif isinstance(exc, PermissionError):
        kind = "local_permission"
    elif isinstance(exc, (json.JSONDecodeError, UnicodeError)):
        kind = "local_parse"
    elif isinstance(exc, OSError):
        kind = "local_io"
    else:
        return None
    return {
        "exception_class": type(exc).__name__ if _CLASS_NAME.fullmatch(type(exc).__name__) else "Exception",
        "error_code": kind,
        "mapped_error_code": None,
        "provider_code": None,
        "http_status": None,
        "failure_kind": kind,
        "usage_status": "unknown",
        "retryable": False,
        "retry_after_present": False,
        "retry_after_state": "absent",
        "retry_after_seconds": None,
        "retry_after_exceeds": False,
        "retry_after_unparsed": False,
        "request_id": None,
        "attempts": None,
        "stage": None,
    }


def _bounded_retry_fields(failure):
    """Apply an over-budget delay before the stored seconds are cleared."""
    seconds = failure.get("retry_after_seconds")
    present = bool(failure.get("retry_after_present"))
    exceeds = bool(failure.get("retry_after_exceeds"))
    unparsed = bool(failure.get("retry_after_unparsed"))
    state = failure.get("retry_after_state")
    if type(seconds) is int and not isinstance(seconds, bool) and seconds > _MAX_RETRY_AFTER_SECONDS:
        present, exceeds, state, seconds = True, True, "stop", None
    elif type(seconds) is int and not isinstance(seconds, bool) and seconds < 0:
        present, unparsed, state, seconds = True, True, "stop", None
    if state not in {"absent", "wait", "stop"}:
        state = "stop" if present or exceeds or unparsed else "absent"
    if state == "wait" and not (type(seconds) is int and not isinstance(seconds, bool) and 0 <= seconds <= _MAX_RETRY_AFTER_SECONDS):
        state, unparsed, seconds = "stop", True, None
    if state != "wait":
        seconds = None
    return present, state, seconds, exceeds, unparsed


def public_failure(failure):
    """Copy only known scalar fields. Drop messages, headers, and nested provider objects."""
    if not isinstance(failure, dict):
        failure = {}
    kind = failure.get("failure_kind")
    status = failure.get("http_status")
    attempts = failure.get("attempts")
    present, state, seconds, exceeds, unparsed = _bounded_retry_fields(failure)
    request_id = safe_request_id(failure.get("request_id")) or "unknown"
    cleaned = {
        "stage": failure.get("stage") if isinstance(failure.get("stage"), str) else None,
        "exception_class": failure.get("exception_class")
        if isinstance(failure.get("exception_class"), str) and _CLASS_NAME.fullmatch(failure.get("exception_class"))
        else "Exception",
        "error_code": safe_code(failure.get("error_code")),
        "mapped_error_code": safe_code(failure.get("mapped_error_code")),
        "provider_code": safe_code(failure.get("provider_code")),
        "http_status": status if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599 else None,
        "failure_kind": kind if kind in _KINDS else "unknown",
        "usage_status": failure.get("usage_status") if failure.get("usage_status") in {"known", "unknown"} else "unknown",
        "retryable": False,
        "retry_after_present": present,
        "retry_after_state": state,
        "retry_after_seconds": seconds,
        "retry_after_exceeds": exceeds,
        "retry_after_unparsed": unparsed,
        "request_id": request_id,
        "attempts": attempts if type(attempts) is int and not isinstance(attempts, bool) and attempts >= 0 else None,
    }
    cleaned["retryable"] = retryable(cleaned)
    return cleaned
