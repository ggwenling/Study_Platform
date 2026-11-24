"""Safe metadata for a model failure. Never store provider text, headers, or bodies."""

import re

from openai import APIConnectionError, APITimeoutError

from study_platform.core.errors import ApiError

_CODE = re.compile(r"[A-Za-z][A-Za-z0-9._]{0,63}")
_REQUEST_ID = re.compile(r"[A-Za-z0-9-]{8,64}")
_CLASS_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,80}")
_SECRET_WORD = ("secret", "token", "bearer", "apikey", "api_key", "password")
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
    "api_error",
    "unknown",
}
_RETRYABLE_KINDS = {"timeout", "connection", "http_5xx", "http_429"}
_REQUEST_ID_HEADERS = ("x-request-id", "x-dashscope-request-id", "x-dashscope-requestid")


def safe_code(value):
    if not isinstance(value, str) or _CODE.fullmatch(value) is None:
        return None
    lowered = value.lower()
    if lowered.startswith("sk-") or any(word in lowered for word in _SECRET_WORD):
        return None
    return value


def safe_request_id(value):
    """Keep a short request id. Reject secrets and free text instead of trimming them."""
    if not isinstance(value, str) or _REQUEST_ID.fullmatch(value) is None:
        return None
    lowered = value.lower()
    if lowered.startswith("sk-") or any(word in lowered for word in _SECRET_WORD):
        return None
    return value


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


def _retry_after(exc):
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None or "retry-after" not in headers:
        return None, False
    raw = headers.get("retry-after")
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip()), False
    return None, True


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
    """Bounded retries fit transient network, 5xx, and rate throttling only."""
    if failure.get("retry_after_unparsed"):
        return False
    seconds = failure.get("retry_after_seconds")
    if type(seconds) is int and seconds > 30:
        return False
    if failure.get("provider_code") == "Throttling.AllocationQuota":
        return False
    return failure.get("failure_kind") in _RETRYABLE_KINDS


def classify_model_exception(exc):
    status = _http_status(exc)
    kind = _kind(exc, status)
    seconds, unparsed = _retry_after(exc)
    failure = {
        "exception_class": type(exc).__name__ if _CLASS_NAME.fullmatch(type(exc).__name__) else "Exception",
        "error_code": safe_code(exc.code) if isinstance(exc, ApiError) else None,
        "mapped_error_code": None,
        "provider_code": _provider_code(exc),
        "http_status": status,
        "failure_kind": kind if kind in _KINDS else "unknown",
        "usage_status": "unknown",
        "retryable": False,
        "retry_after_seconds": seconds,
        "retry_after_unparsed": unparsed,
        "request_id": _request_id(exc),
        "attempts": None,
        "stage": None,
    }
    failure["retryable"] = retryable(failure)
    return failure


def public_failure(failure):
    """Copy only known scalar fields. Drop messages, headers, and nested provider objects."""
    if not isinstance(failure, dict):
        failure = {}
    kind = failure.get("failure_kind")
    status = failure.get("http_status")
    seconds = failure.get("retry_after_seconds")
    attempts = failure.get("attempts")
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
        "retryable": bool(failure.get("retryable")),
        "retry_after_seconds": seconds if type(seconds) is int and 0 <= seconds <= 86400 else None,
        "retry_after_unparsed": bool(failure.get("retry_after_unparsed")),
        "request_id": safe_request_id(failure.get("request_id")),
        "attempts": attempts if type(attempts) is int and attempts >= 0 else None,
    }
    cleaned["retryable"] = retryable(cleaned)
    return cleaned
