"""In-process auth rate limiting (per client IP, rolling minute window)."""

from collections import defaultdict
from time import monotonic

from fastapi import Request

from study_platform.core.errors import ApiError

_buckets: dict[str, list[float]] = defaultdict(list)


def rate_limit_auth(request: Request, *, limit: int) -> None:
    ip = request.client.host if request.client else "unknown"
    rate_limit(ip, limit=limit)


def rate_limit(key: str, *, limit: int) -> None:
    if limit <= 0:
        return
    now = monotonic()
    window = _buckets[key]
    window[:] = [stamp for stamp in window if now - stamp < 60]
    if len(window) >= limit:
        raise ApiError(429, "rate_limited", "请求过于频繁，请稍后再试", retryable=True)
    window.append(now)
