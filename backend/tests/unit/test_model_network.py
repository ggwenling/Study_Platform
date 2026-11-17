import socket

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from study_platform.core.config import Settings
from study_platform.core.errors import ApiError
from study_platform.services import model_connections, model_network


@pytest.fixture
def settings(monkeypatch):
    value = Settings(_env_file=None, model_connections_key=Fernet.generate_key().decode())
    monkeypatch.setattr(model_network, "get_settings", lambda: value)
    monkeypatch.setattr(model_connections, "get_settings", lambda: value)
    return value


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example/v1",
        "https://user:key@api.example/v1",
        "https://api.example/v1?key=secret",
        "https://api.example/#fragment",
        "file:///tmp/model",
        "https://[broken/v1",
        "https://example:bad/v1",
    ],
)
def test_unsafe_base_urls_are_rejected(settings, url):
    with pytest.raises(ApiError):
        model_network.normalize_url(url)


@pytest.mark.parametrize(
    "ip", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "::1", "::ffff:127.0.0.1", "0.0.0.0"]
)
def test_public_dns_cannot_resolve_to_internal_targets(settings, monkeypatch, ip):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *_args, **_kwargs: [(None, None, None, None, (ip, 443))]
    )
    with pytest.raises(ApiError) as error:
        model_network.approved_ip("api.example", 443, "https://api.example/v1")
    assert error.value.code == "model_address_blocked"


def test_internal_allowlist_exact_origin_and_revocation(settings, monkeypatch):
    settings.model_allowed_origins = ["http://model.internal:11434"]
    assert (
        model_network.normalize_url("http://model.internal:11434/v1/")
        == "http://model.internal:11434/v1"
    )
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("10.1.1.1", 11434))],
    )
    assert (
        model_network.approved_ip("model.internal", 11434, "http://model.internal:11434/v1")
        == "10.1.1.1"
    )
    with pytest.raises(ApiError):
        model_network.approved_ip("model.internal", 80, "http://model.internal:11434/v1")
    with pytest.raises(ApiError):
        model_network.normalize_url("http://model.internal:11435/v1")
    settings.model_allowed_origins = []
    with pytest.raises(ApiError):
        model_network.sync_client("http://model.internal:11434/v1", 1)


def test_dns_rebinding_and_mixed_answers_are_rechecked(settings, monkeypatch):
    responses = iter([["8.8.8.8"], ["8.8.8.8", "127.0.0.1"]])
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, (ip, 443)) for ip in next(responses)],
    )
    assert model_network.approved_ip("api.example", 443, "https://api.example/v1") == "8.8.8.8"
    with pytest.raises(ApiError):
        model_network.approved_ip("api.example", 443, "https://api.example/v1")


def test_authenticated_encryption_detects_tampering_and_missing_key(settings):
    encrypted = model_connections.encrypt("never-return-full-key")
    assert "never-return-full-key" not in encrypted
    assert model_connections.decrypt(encrypted) == "never-return-full-key"
    with pytest.raises(ApiError) as error:
        model_connections.decrypt(encrypted[:-8] + "tampered")
    assert error.value.code == "model_decryption_failed"
    settings.model_connections_key = SecretStr("")
    with pytest.raises(ApiError) as error:
        model_connections.encrypt("secret")
    assert error.value.code == "model_encryption_unavailable"
