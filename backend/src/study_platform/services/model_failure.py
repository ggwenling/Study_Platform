"""Safe metadata for a model failure. Never store provider text, headers, or bodies."""

import json
import math
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from openai import APIConnectionError, APITimeoutError

from study_platform.core.errors import ApiError

_CODE = re.compile(r"[A-Za-z][A-Za-z0-9._]{0,63}")
_DELAY_DIGITS = re.compile(r"[0-9]{1,10}\Z")
# Exact official quota codes only. A short key or a UUID is not stored because it
# happens to match a loose pattern. Comparison with a live credential is not done.
_SAFE_PROVIDER_CODES = frozenset({"Throttling.RateQuota", "Throttling.AllocationQuota"})
_CREDENTIAL = re.compile(
    r"(?i)(sk-|akia[0-9a-z]{16}|asia[0-9a-z]{16}|aiza[0-9a-z\-_]{10,}|"
    r"ghp_|github_pat_|xox[baprs]-|bearer\s|-----begin )"
)
_CLASS_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,80}")
_SECRET_WORD = ("secret", "token", "bearer", "apikey", "api_key", "password")
_MAX_RETRY_AFTER_SECONDS = 30
_REQUEST_ID_OMITTED = "unknown"
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


def safe_provider_code(value):
    """Keep an official quota code. Every other provider code is unknown."""
    if value in _SAFE_PROVIDER_CODES:
        return value
    if value is None:
        return None
    return "unknown"


def _nonnegative_int(value):
    if type(value) is int and value >= 0:
        return value
    return None


def _named_int(container, key):
    """Return absent, invalid, or a nonnegative int. Wrong types are not coerced."""
    if not isinstance(container, dict) or key not in container:
        return "absent", None
    value = _nonnegative_int(container.get(key))
    if value is None:
        return "invalid", None
    return "value", value


def _collect_ints(readings):
    present = False
    invalid = False
    values = []
    for state, value in readings:
        if state == "absent":
            continue
        present = True
        if state == "invalid":
            invalid = True
        else:
            values.append(value)
    return present, invalid, values


def _one_input(kept, left, right):
    values = [kept[key] for key in (left, right) if key in kept]
    if len(set(values)) > 1:
        return "inconsistent"
    if not values:
        return None
    return values[0]


def _cache_fields(raw, kept):
    """Keep allowlisted cache counts. A missing count is unknown, not a miss of zero."""
    details = raw.get("input_token_details")
    prompt_details = raw.get("prompt_tokens_details")
    read_present, read_invalid, read_values = _collect_ints(
        (
            _named_int(details, "cache_read"),
            _named_int(prompt_details, "cached_tokens"),
            _named_int(raw, "prompt_cache_hit_tokens"),
        )
    )
    if "input_token_details" in raw and not isinstance(details, dict):
        read_present, read_invalid = True, True
    if "prompt_tokens_details" in raw and not isinstance(prompt_details, dict):
        read_present, read_invalid = True, True
    miss_present, miss_invalid, miss_values = _collect_ints(
        (
            _named_int(details, "cache_miss"),
            _named_int(raw, "prompt_cache_miss_tokens"),
        )
    )
    creation_state, creation = _named_int(details, "cache_creation")
    fields = {}
    details_out = {}
    if creation_state == "value":
        details_out["cache_creation"] = creation
    agreed_read = len(set(read_values)) == 1
    if not read_present:
        fields["cache_read_status"] = "unknown"
    elif read_invalid or not agreed_read:
        fields["cache_read_status"] = "inconsistent" if read_values else "unknown"
        if read_values:
            fields["cache_read_observed"] = sorted(set(read_values))
    else:
        details_out["cache_read"] = read_values[0]
        fields["cache_read_status"] = "known"
    if details_out:
        fields["input_token_details"] = details_out
    incoming = _one_input(kept, "input_tokens", "prompt_tokens")
    if (
        fields.get("cache_read_status") == "known"
        and type(incoming) is int
        and details_out["cache_read"] > incoming
    ):
        fields["cache_read_status"] = "inconsistent"
    agreed_miss = len(set(miss_values)) == 1
    if not miss_present:
        fields["cache_miss_status"] = "unknown"
    elif miss_invalid or not agreed_miss:
        fields["cache_miss_status"] = "inconsistent" if miss_values else "unknown"
    else:
        fields["cache_miss"] = miss_values[0]
        fields["cache_miss_status"] = "known"
    if (
        fields["cache_read_status"] == "known"
        and fields["cache_miss_status"] == "known"
        and type(incoming) is int
        and details_out["cache_read"] + fields["cache_miss"] != incoming
    ):
        fields["cache_read_status"] = "inconsistent"
        fields["cache_miss_status"] = "inconsistent"
    if (
        fields["cache_miss_status"] == "unknown"
        and fields["cache_read_status"] == "known"
        and type(incoming) is int
    ):
        fields["cache_miss_status"] = "estimate"
        fields["cache_miss_estimate"] = incoming - details_out["cache_read"]
    return fields


def allow_usage(usage):
    """Token counts and allowlisted cache counts. Missing usage is unknown, not zero.

    Nested ``input_token_details.cache_read`` is kept when it is a nonnegative int.
    DeepSeek ``prompt_cache_hit_tokens`` and OpenAI ``prompt_tokens_details.cached_tokens``
    are the same count. A missing cache field stays unknown and is not stored as a
    hit of zero. Text, headers, request ids, and secrets are not copied.
    """
    raw = usage.model_dump() if hasattr(usage, "model_dump") else usage
    if not isinstance(raw, dict):
        return None, "unknown"
    kept = {}
    for key in _USAGE_KEYS:
        value = _nonnegative_int(raw.get(key))
        if value is not None:
            kept[key] = value
    cache = _cache_fields(raw, kept)
    has_cache_number = "input_token_details" in cache or "cache_miss" in cache
    if not kept and not has_cache_number:
        return None, "unknown"
    kept.update(cache)
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
    """Read only the code slots. An official quota code wins; other text is not copied."""
    values = []
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        values.append(body.get("code"))
        error = body.get("error")
        if isinstance(error, dict):
            values.append(error.get("code"))
    if not isinstance(exc, ApiError):
        values.append(getattr(exc, "code", None))
    for value in values:
        if value in _SAFE_PROVIDER_CODES:
            return value
    if any(value is not None for value in values):
        return "unknown"
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
        "provider_code": safe_provider_code(_provider_code(exc)),
        "http_status": status,
        "failure_kind": kind if kind in _KINDS else "unknown",
        "usage_status": "unknown",
        "retryable": False,
        "retry_after_present": decision["present"],
        "retry_after_state": decision["state"],
        "retry_after_seconds": decision["seconds"],
        "retry_after_exceeds": decision["exceeds"],
        "retry_after_unparsed": decision["unparsed"],
        "request_id": _REQUEST_ID_OMITTED,
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
        "request_id": _REQUEST_ID_OMITTED,
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
    cleaned = {
        "stage": failure.get("stage") if isinstance(failure.get("stage"), str) else None,
        "exception_class": failure.get("exception_class")
        if isinstance(failure.get("exception_class"), str) and _CLASS_NAME.fullmatch(failure.get("exception_class"))
        else "Exception",
        "error_code": safe_code(failure.get("error_code")),
        "mapped_error_code": safe_code(failure.get("mapped_error_code")),
        "provider_code": safe_provider_code(failure.get("provider_code")),
        "http_status": status if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599 else None,
        "failure_kind": kind if kind in _KINDS else "unknown",
        "usage_status": failure.get("usage_status") if failure.get("usage_status") in {"known", "unknown"} else "unknown",
        "retryable": False,
        "retry_after_present": present,
        "retry_after_state": state,
        "retry_after_seconds": seconds,
        "retry_after_exceeds": exceeds,
        "retry_after_unparsed": unparsed,
        "request_id": _REQUEST_ID_OMITTED,
        "attempts": attempts if type(attempts) is int and not isinstance(attempts, bool) and attempts >= 0 else None,
    }
    cleaned["retryable"] = retryable(cleaned)
    return cleaned
