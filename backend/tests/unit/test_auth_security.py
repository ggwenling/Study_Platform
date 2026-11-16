
import pytest
from pydantic import SecretStr

from study_platform.core.config import Settings
from study_platform.services.auth import validate_cookie_security


def test_validate_cookie_security_requires_secure_flag_for_https_public_url():
    settings = Settings(
        _env_file=None,
        auth_public_url="https://app.example.test",
        cookie_secure=False,
        auth_jwt_secret=SecretStr("x" * 32),
    )
    with pytest.raises(RuntimeError, match="COOKIE_SECURE"):
        validate_cookie_security(settings)


def test_validate_cookie_security_allows_http_local():
    settings = Settings(
        _env_file=None,
        auth_public_url="http://127.0.0.1:5173",
        cookie_secure=False,
        auth_jwt_secret=SecretStr("x" * 32),
    )
    validate_cookie_security(settings)