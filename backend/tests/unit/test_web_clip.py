from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from study_platform.core.errors import ApiError
from study_platform.services import web_clip


def _public_dns(monkeypatch, calls=None):
    def getaddrinfo(host, port, type=None):
        if calls is not None:
            calls.append((host, port))
        return [(2, 1, 6, "", ("8.8.8.8", port))]

    monkeypatch.setattr(web_clip.socket, "getaddrinfo", getaddrinfo)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/article",
        "http://user:pass@example.com/article",
        "http://127.0.0.1/article",
        "http://[::1]/article",
        "http://[::ffff:127.0.0.1]/article",
        "http://[64:ff9b::7f00:1]/article",
        "http://169.254.169.254/latest/meta-data",
    ],
)
def test_rejects_invalid_or_nonpublic_web_clip_urls(url):
    with pytest.raises(ApiError) as error:
        web_clip.public_target(url)
    assert error.value.status_code == 422


def test_rejects_hostname_resolving_to_private_address(monkeypatch):
    monkeypatch.setattr(
        web_clip.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("10.0.0.8", 443))],
    )
    with pytest.raises(ApiError, match="公网地址") as error:
        web_clip.public_target("https://private.example/article")
    assert error.value.code == "web_clip_url_blocked"


def test_rejects_benchmark_fake_ip_with_actionable_hint(monkeypatch):
    monkeypatch.setattr(
        web_clip.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("198.18.0.207", 443))],
    )
    with pytest.raises(ApiError, match="公网地址") as error:
        web_clip.public_target("https://developer.mozilla.org/en-US/")
    assert error.value.status_code == 422
    assert error.value.code == "web_clip_url_blocked"
    assert "198.18.0.0/15" in error.value.message


def test_dns_resolution_obeys_deadline(monkeypatch):
    import time

    monkeypatch.setattr(
        web_clip.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: time.sleep(0.05),
    )
    with pytest.raises(ApiError) as error:
        web_clip.public_target("https://slow.example/", deadline=time.monotonic() + 0.005)
    assert error.value.code == "web_clip_timeout"


def test_connect_uses_resolved_public_ip_and_original_hostname(monkeypatch):
    _public_dns(monkeypatch)
    target = web_clip.public_target("https://public.example/article")
    connected = []
    from httpcore._backends.sync import SyncBackend

    monkeypatch.setattr(
        SyncBackend,
        "connect_tcp",
        lambda _self, host, _port, *_args, **_kwargs: connected.append(host) or host,
    )
    backend = web_clip._PinnedSyncBackend(target)
    assert backend.connect_tcp("public.example", 443) == "8.8.8.8"
    assert connected == ["8.8.8.8"]
    with pytest.raises(ApiError):
        backend.connect_tcp("other.example", 443)


def test_fetch_rechecks_redirect_destination_before_request(monkeypatch):
    calls = []
    _public_dns(monkeypatch, calls)
    settings = SimpleNamespace(web_clip_timeout_seconds=2, max_web_clip_bytes=1024)
    monkeypatch.setattr(web_clip, "get_settings", lambda: settings)
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    monkeypatch.setattr(
        web_clip,
        "_client_for_target",
        lambda _target: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(ApiError) as error:
        web_clip.fetch_public_webpage("https://public.example/article")
    assert error.value.code == "web_clip_url_blocked"
    assert len(requests) == 1
    assert calls == [("public.example", 443)]


def test_fetches_public_redirect_with_mocked_http(monkeypatch):
    calls = []
    _public_dns(monkeypatch, calls)
    settings = SimpleNamespace(web_clip_timeout_seconds=2, max_web_clip_bytes=1024)
    monkeypatch.setattr(web_clip, "get_settings", lambda: settings)
    requests = []
    body = "A public article with enough useful text to pass the minimum length threshold."

    def handler(request):
        requests.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/article"})
        return httpx.Response(
            200,
            headers={"content-type": "text/markdown; charset=utf-8"},
                stream=httpx.ByteStream(body.encode()),
        )

    monkeypatch.setattr(
        web_clip,
        "_client_for_target",
        lambda _target: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = web_clip.fetch_public_webpage("https://public.example/start")
    assert result.final_url == "https://public.example/article"
    assert result.title == "public.example"
    assert result.markdown == body
    assert requests == ["https://public.example/start", "https://public.example/article"]
    assert calls == [("public.example", 443), ("public.example", 443)]


def test_rejects_oversized_body_before_extracting(monkeypatch):
    _public_dns(monkeypatch)
    settings = SimpleNamespace(web_clip_timeout_seconds=2, max_web_clip_bytes=16)
    monkeypatch.setattr(web_clip, "get_settings", lambda: settings)
    monkeypatch.setattr(
        web_clip,
        "_client_for_target",
        lambda _target: httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    headers={"content-type": "text/plain"},
                    stream=httpx.ByteStream(b"x" * 17),
                )
            )
        ),
    )
    with pytest.raises(ApiError) as error:
        web_clip.fetch_public_webpage("https://public.example/article")
    assert error.value.code == "web_clip_too_large"


def test_rejects_compressed_response_without_decompressing(monkeypatch):
    _public_dns(monkeypatch)
    settings = SimpleNamespace(web_clip_timeout_seconds=2, max_web_clip_bytes=1024)
    monkeypatch.setattr(web_clip, "get_settings", lambda: settings)
    monkeypatch.setattr(
        web_clip,
        "_client_for_target",
        lambda _target: httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    headers={"content-type": "text/plain", "content-encoding": "gzip"},
                    stream=httpx.ByteStream(b"compressed bytes"),
                )
            )
        ),
    )
    with pytest.raises(ApiError) as error:
        web_clip.fetch_public_webpage("https://public.example/article")
    assert error.value.code == "web_clip_unsupported_encoding"


def test_short_html_extraction_returns_login_wall_error():
    with pytest.raises(ApiError) as error:
        web_clip._extract(
            b"<html><title>Sign in</title><body>Please log in</body></html>",
            "text/html",
            "utf-8",
            "https://public.example/login",
        )
    assert error.value.code == "web_clip_empty"


def test_web_clip_route_persists_markdown_source_and_queues_ingest(tmp_path, monkeypatch):
    from study_platform.api.v1 import materials
    from study_platform.persistence.models.identity import Material
    from study_platform.persistence.models.materials import MaterialVersion

    kb_id, owner_id = uuid4(), uuid4()
    user = SimpleNamespace(id=owner_id)
    path = tmp_path / str(uuid4()) / "source"
    clip = web_clip.WebClip(
        title="Extracted title",
        markdown="# Article\n\nThe extracted article contains enough text to index.",
        final_url="https://public.example/final",
    )

    class Database:
        def __init__(self):
            self.rows = []
            self.did_rollback = False
            self.did_commit = False

        def rollback(self):
            self.did_rollback = True

        def add(self, row):
            self.rows.append(row)

        def flush(self):
            pass

        def commit(self):
            self.did_commit = True

    db = Database()
    monkeypatch.setattr(materials, "owned_kb", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(materials, "fetch_public_webpage", lambda _url: clip)
    monkeypatch.setattr(materials, "source_path", lambda _version_id: path)
    monkeypatch.setattr(
        materials,
        "create_job",
        lambda *_args: SimpleNamespace(id=uuid4()),
    )
    response = materials.web_clip(
        kb_id,
        materials.WebClipRequest(url="https://public.example/start", title="  "),
        db,
        user,
    )
    material = next(row for row in db.rows if isinstance(row, Material))
    version = next(row for row in db.rows if isinstance(row, MaterialVersion))
    assert response["status"] == "queued"
    assert material.title == "Extracted title"
    assert material.source_url == clip.final_url
    assert version.source_mime == "text/markdown"
    assert version.byte_size == path.stat().st_size
    assert path.read_text(encoding="utf-8") == clip.markdown
    assert db.did_rollback and db.did_commit
