"""Fetch public static webpages without following unvalidated network targets."""

import ipaddress
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from threading import Lock
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpcore
import httpx
from httpcore._backends.sync import SyncBackend
from lxml import etree
from lxml import html as lxml_html

from study_platform.core.config import get_settings
from study_platform.core.errors import ApiError

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAX_REDIRECTS = 5
_ALLOWED_CONTENT_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "text/markdown",
}
_BENCHMARK_NET = ipaddress.ip_network("198.18.0.0/15")
_DNS_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="web-clip-dns")
_FETCH_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="web-clip-fetch")


@dataclass(frozen=True)
class PublicTarget:
    url: str
    scheme: str
    host: str
    port: int
    address: str


@dataclass(frozen=True)
class WebClip:
    title: str
    markdown: str
    final_url: str


class _FetchState:
    def __init__(self):
        self._lock = Lock()
        self._clients = set()

    def add(self, client):
        with self._lock:
            self._clients.add(client)

    def remove(self, client):
        with self._lock:
            self._clients.discard(client)

    def close(self):
        with self._lock:
            clients = tuple(self._clients)
        for client in clients:
            client.close()


def _bad_url() -> ApiError:
    return ApiError(422, "web_clip_url_invalid", "请填写有效的 HTTP 或 HTTPS 网页地址。")


def _resolve_addresses(host: str, port: int, deadline: float | None):
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        future = None
        try:
            if deadline is None:
                records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise FutureTimeout
                future = _DNS_POOL.submit(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
                records = future.result(timeout=remaining)
        except FutureTimeout:
            if future:
                future.cancel()
            raise ApiError(504, "web_clip_timeout", "网页抓取超时，请稍后重试。") from None
        except OSError:
            raise ApiError(502, "web_clip_dns_failed", "网页地址无法解析。") from None
        addresses = []
        for record in records:
            try:
                address = ipaddress.ip_address(record[4][0])
            except ValueError:
                raise ApiError(422, "web_clip_url_blocked", "该网页地址解析到不安全的网络。") from None
            addresses.append(address)
    else:
        addresses = [literal]
    return addresses


def _is_public_address(address):
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            return False
        special_networks = ("64:ff9b::/96", "64:ff9b:1::/48", "2002::/16")
        if any(address in ipaddress.ip_network(network) for network in special_networks):
            return False
    return address.is_global and not (
        address.is_multicast
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
    )


def public_target(value: str, *, deadline: float | None = None) -> PublicTarget:
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise _bad_url()
        if parsed.username is not None or parsed.password is not None or "%" in parsed.hostname:
            raise _bad_url()
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
    except (AttributeError, UnicodeError, ValueError):
        raise _bad_url() from None
    if port < 1 or port > 65535 or any(ord(char) < 33 for char in value.strip()):
        raise _bad_url()

    addresses = _resolve_addresses(host, port, deadline)
    if deadline is not None and time.monotonic() >= deadline:
        raise ApiError(504, "web_clip_timeout", "网页抓取超时，请稍后重试。")
    if not addresses or any(not _is_public_address(address) for address in addresses):
        if addresses and all(
            isinstance(address, ipaddress.IPv4Address) and address in _BENCHMARK_NET
            for address in addresses
        ):
            raise ApiError(
                422,
                "web_clip_url_blocked",
                "仅允许访问解析到公网地址的网页（当前域名被解析到保留网段 198.18.0.0/15，若本机开启了代理 TUN/Fake-IP，请关闭 Fake-IP 或对该域名配置直连后重试）。",
            )
        raise ApiError(422, "web_clip_url_blocked", "仅允许访问解析到公网地址的网页。")

    authority_host = f"[{host}]" if ":" in host else host
    authority = authority_host + (f":{port}" if parsed.port is not None else "")
    url = urlunsplit((parsed.scheme, authority, parsed.path or "/", parsed.query, parsed.fragment))
    return PublicTarget(url, parsed.scheme, host, port, str(addresses[0]))


class _PinnedSyncBackend(SyncBackend):
    def __init__(self, target: PublicTarget):
        self.target = target

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if isinstance(host, bytes):
            host = host.decode("ascii")
        if host.lower() != self.target.host or port != self.target.port:
            raise ApiError(422, "web_clip_url_blocked", "网页连接目标与已验证地址不一致。")
        return super().connect_tcp(
            self.target.address, port, timeout, local_address, socket_options
        )


def _client_for_target(target: PublicTarget) -> httpx.Client:
    transport = httpx.HTTPTransport()
    transport._pool = httpcore.ConnectionPool(network_backend=_PinnedSyncBackend(target))
    return httpx.Client(
        transport=transport,
        timeout=get_settings().web_clip_timeout_seconds,
        follow_redirects=False,
        trust_env=False,
    )


def _restore_main_paragraphs(html: str, markdown: str) -> str:
    """Recover paragraphs dropped by the extractor from an unambiguous main region."""
    try:
        document = lxml_html.fromstring(html)
    except (etree.ParserError, ValueError):
        return markdown
    main = document.xpath("//main | //*[@role='main']")
    if len(main) != 1:
        return markdown
    root = main[0]
    for element in root.xpath(
        ".//nav | .//header | .//footer | .//aside | .//script | .//style | .//template | "
        ".//form | .//noscript | .//*[@hidden or @aria-hidden='true' or @role='navigation']"
    ):
        element.drop_tree()

    def normalize(value):
        return " ".join(re.findall(r"\w+", value.casefold()))

    extracted = normalize(markdown)
    additions = []
    heading = None
    for element in root.xpath(".//h1 | .//h2 | .//h3 | .//h4 | .//h5 | .//h6 | .//p"):
        text = " ".join(element.text_content().split())
        if element.tag.startswith("h"):
            heading = f"{'#' * int(element.tag[1])} {text}" if text else None
            continue
        normalized = normalize(text)
        if len(normalized) < 40 or normalized in extracted:
            continue
        if heading and (not additions or additions[-1][0] != heading):
            additions.append((heading, []))
        elif not additions:
            additions.append(("", []))
        additions[-1][1].append(text)
        extracted += " " + normalized
    if not additions:
        return markdown
    supplemental = "\n\n".join(block for title, paragraphs in additions for block in (title, *paragraphs) if block)
    return (markdown + "\n\n" + supplemental).strip()


def _extract(markup: bytes, content_type: str, encoding: str | None, final_url: str):
    if content_type in ("text/plain", "text/markdown"):
        try:
            text = markup.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ApiError(422, "web_clip_encoding_invalid", "网页文本必须使用 UTF-8 编码。") from None
        title = None
    else:
        try:
            html = markup.decode(encoding or "utf-8-sig", errors="replace")
        except LookupError:
            html = markup.decode("utf-8", errors="replace")
        import trafilatura

        metadata = trafilatura.extract_metadata(html)
        title = metadata.title if metadata else None
        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            output_format="markdown",
        ) or ""
        text = _restore_main_paragraphs(html, text)
    markdown = text.strip()
    if sum(char.isalnum() for char in markdown) < 40:
        raise ApiError(
            422,
            "web_clip_empty",
            "未能从该网页提取到有效正文，可能是需要登录或动态渲染页面，请改用「随手贴文本」导入。",
        )
    if not title:
        parsed = urlsplit(final_url)
        title = parsed.hostname or "网页剪藏"
    return title.strip()[:512], markdown


def _fetch_public_webpage(url: str, deadline: float, state: _FetchState) -> WebClip:
    settings = get_settings()
    max_bytes = settings.max_web_clip_bytes
    current_url = url
    for redirect_count in range(_MAX_REDIRECTS + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ApiError(504, "web_clip_timeout", "网页抓取超时，请稍后重试。")
        target = public_target(current_url, deadline=deadline)
        client = _client_for_target(target)
        state.add(client)
        try:
            with client:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ApiError(504, "web_clip_timeout", "网页抓取超时，请稍后重试。")
                with client.stream(
                    "GET",
                    target.url,
                    headers={
                        "Accept": "text/html,application/xhtml+xml,text/plain,text/markdown",
                        "Accept-Encoding": "identity",
                        "User-Agent": "Synapse-WebClip/1.0",
                    },
                    timeout=httpx.Timeout(remaining),
                ) as response:
                    if response.status_code in _REDIRECT_STATUSES:
                        if redirect_count >= _MAX_REDIRECTS:
                            raise ApiError(
                                422, "web_clip_redirect_limit", "网页重定向次数超过安全上限。"
                            )
                        location = response.headers.get("location")
                        if not location:
                            raise ApiError(
                                422, "web_clip_redirect_invalid", "网页重定向地址无效。"
                            )
                        current_url = urljoin(target.url, location)
                        continue
                    if response.status_code < 200 or response.status_code >= 300:
                        raise ApiError(502, "web_clip_remote_error", "网页服务器未能提供内容。")
                    content_encoding = response.headers.get("content-encoding", "identity").strip().lower()
                    if content_encoding not in ("", "identity"):
                        raise ApiError(
                            415, "web_clip_unsupported_encoding", "网页服务器返回了不支持的压缩内容。"
                        )
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if content_type not in _ALLOWED_CONTENT_TYPES:
                        raise ApiError(
                            415, "web_clip_unsupported_type", "仅支持公开网页和纯文本内容。"
                        )
                    content_length = response.headers.get("content-length")
                    if content_length:
                        try:
                            if int(content_length) > max_bytes:
                                raise ApiError(
                                    413, "web_clip_too_large", "网页内容超过 5 MB 上限。"
                                )
                        except ValueError:
                            pass
                    body = bytearray()
                    for block in response.iter_raw():
                        if time.monotonic() >= deadline:
                            raise ApiError(504, "web_clip_timeout", "网页抓取超时，请稍后重试。")
                        if len(body) + len(block) > max_bytes:
                            raise ApiError(413, "web_clip_too_large", "网页内容超过 5 MB 上限。")
                        body.extend(block)
                    if time.monotonic() >= deadline:
                        raise ApiError(504, "web_clip_timeout", "网页抓取超时，请稍后重试。")
                    title, markdown = _extract(
                        bytes(body), content_type, response.encoding, target.url
                    )
                    return WebClip(title, markdown, target.url)
        except ApiError:
            raise
        except httpx.TimeoutException:
            raise ApiError(504, "web_clip_timeout", "网页抓取超时，请稍后重试。") from None
        except httpx.HTTPError:
            raise ApiError(502, "web_clip_fetch_failed", "无法读取该网页，请检查网址后重试。") from None
        finally:
            state.remove(client)
    raise ApiError(422, "web_clip_redirect_limit", "网页重定向次数超过安全上限。")


def fetch_public_webpage(url: str) -> WebClip:
    timeout = get_settings().web_clip_timeout_seconds
    deadline = time.monotonic() + timeout
    state = _FetchState()
    future = _FETCH_POOL.submit(_fetch_public_webpage, url, deadline, state)
    try:
        return future.result(timeout=timeout)
    except FutureTimeout:
        state.close()
        future.cancel()
        raise ApiError(504, "web_clip_timeout", "网页抓取超时，请稍后重试。") from None
