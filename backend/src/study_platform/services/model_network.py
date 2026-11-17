"""Connect only to policy-approved resolved IPs, retaining original TLS SNI."""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

import httpcore
import httpx
from httpcore._backends.anyio import AnyIOBackend
from httpcore._backends.sync import SyncBackend

from study_platform.core.config import get_settings
from study_platform.core.errors import ApiError


def normalize_url(value):
    try:
        parsed = urlsplit(value.strip())
        hostname = parsed.hostname.encode("idna").decode("ascii").lower() if parsed.hostname else ""
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
    except (ValueError, UnicodeError):
        raise ApiError(422, "model_address_invalid", "模型地址或端口无效。") from None
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ApiError(422, "model_address_invalid", "请填写不含凭据、查询或片段的 API Base URL。")
    if port < 1 or "%" in hostname or any(ord(c) < 33 for c in value):
        raise ApiError(422, "model_address_invalid", "模型地址包含无效字符。")
    host = f"[{hostname}]" if ":" in hostname else hostname
    origin = origin_key(f"{parsed.scheme}://{host}:{port}")
    allowed = {origin_key(item) for item in get_settings().model_allowed_origins}
    if parsed.scheme != "https" and origin not in allowed:
        raise ApiError(
            422, "model_address_blocked", "默认只允许公网 HTTPS；内网地址须由管理员开放。"
        )
    return (
        f"{parsed.scheme}://{host}"
        + (f":{port}" if port != (443 if parsed.scheme == "https" else 80) else "")
        + parsed.path.rstrip("/")
    )


def origin_key(url):
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.hostname.lower() if parsed.hostname else ''}:{parsed.port or (443 if parsed.scheme == 'https' else 80)}"


def approved_ip(host, port, base_url):
    normalize_url(base_url)  # Re-evaluate administrator policy on every new socket.
    if isinstance(host, bytes):
        host = host.decode("ascii")
    target = urlsplit(base_url)
    if host.lower() != target.hostname.lower() or port != (
        target.port or (443 if target.scheme == "https" else 80)
    ):
        raise ApiError(422, "model_address_blocked", "模型连接目标与已配置地址不一致。")
    allowed = origin_key(base_url) in {
        origin_key(value) for value in get_settings().model_allowed_origins
    }
    try:
        addresses = [item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]
    except OSError:
        raise ApiError(502, "model_dns_failed", "模型地址无法解析，请检查 Base URL。") from None
    if not addresses or (
        not allowed
        and any(
            not ipaddress.ip_address(ip).is_global or ipaddress.ip_address(ip).is_multicast
            for ip in addresses
        )
    ):
        raise ApiError(422, "model_address_blocked", "该地址解析到未获管理员允许的网络。")
    return addresses[0]


class GuardedSyncBackend(SyncBackend):
    def __init__(self, base_url):
        self.base_url = base_url

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        return super().connect_tcp(
            approved_ip(host, port, self.base_url), port, timeout, local_address, socket_options
        )


class GuardedAsyncBackend(AnyIOBackend):
    def __init__(self, base_url):
        self.base_url = base_url

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        address = await asyncio.to_thread(approved_ip, host, port, self.base_url)
        return await super().connect_tcp(address, port, timeout, local_address, socket_options)


def sync_client(base_url, timeout):
    normalize_url(base_url)
    transport = httpx.HTTPTransport()
    transport._pool = httpcore.ConnectionPool(network_backend=GuardedSyncBackend(base_url))
    return httpx.Client(
        transport=transport, timeout=timeout, follow_redirects=False, trust_env=False
    )


def async_client(base_url, timeout):
    normalize_url(base_url)
    transport = httpx.AsyncHTTPTransport()
    transport._pool = httpcore.AsyncConnectionPool(network_backend=GuardedAsyncBackend(base_url))
    return httpx.AsyncClient(
        transport=transport, timeout=timeout, follow_redirects=False, trust_env=False
    )
