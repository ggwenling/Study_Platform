
import pytest
from starlette.requests import Request

from study_platform.core.errors import ApiError
from study_platform.services import auth_limits


@pytest.fixture(autouse=True)
def reset_buckets():
    auth_limits._buckets.clear()
    yield
    auth_limits._buckets.clear()


def make_request(ip: str = "203.0.113.1") -> Request:
    scope = {"type": "http", "client": (ip, 0), "headers": [], "method": "POST", "path": "/"}
    return Request(scope)


def test_rate_limit_blocks_after_threshold():
    limit = 3
    for _ in range(limit):
        auth_limits.rate_limit_auth(make_request(), limit=limit)
    with pytest.raises(ApiError) as error:
        auth_limits.rate_limit_auth(make_request(), limit=limit)
    assert error.value.status_code == 429
    assert error.value.code == "rate_limited"