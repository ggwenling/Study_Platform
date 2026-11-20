from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest

from study_platform.core.errors import ApiError
from study_platform.services import model_calls, model_connections, model_usage
from study_platform.services.model_usage import ModelUsageContext


def test_invoke_vision_sends_image_uri_and_records_tokens(monkeypatch):
    owner = uuid4()
    connection_id = uuid4()
    saved = {"id": str(connection_id), "revision": 3, "kind": "vision"}
    connection = {
        "id": str(connection_id),
        "revision": 3,
        "kind": "vision",
        "api_key": "secret",
        "base_url": "https://vision.example/v1",
        "model": "vision-model",
        "source": f"connection:{connection_id}:3",
    }
    request = {}
    recorded = []

    class Completions:
        def create(self, **kwargs):
            request.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="Read the chart."))],
                usage=SimpleNamespace(model_dump=lambda: {"prompt_tokens": 4, "total_tokens": 6}),
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            request["client_config"] = kwargs
            self.chat = SimpleNamespace(completions=Completions())

        def close(self):
            request["closed"] = True

    monkeypatch.setattr(model_calls, "get_settings", lambda: SimpleNamespace(
        model_timeout_seconds=12, model_max_output_tokens=300
    ))
    monkeypatch.setattr(model_calls, "resolved", lambda *_args: connection)
    monkeypatch.setattr(model_calls, "sync_client", lambda *_args: "guarded-client")
    monkeypatch.setattr(model_calls, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(
        model_calls,
        "record_model_call",
        lambda context, **kwargs: recorded.append((context, kwargs)),
    )

    result = model_calls.invoke_vision(
        "Summarize the chart.",
        b"image-bytes",
        "image/png",
        context=ModelUsageContext(owner, "vision_ingest"),
        saved=saved,
    )

    image_uri = request["messages"][0]["content"][1]["image_url"]["url"]
    assert image_uri == "data:image/png;base64,aW1hZ2UtYnl0ZXM="
    assert request["messages"][0]["content"][0]["text"] == "Summarize the chart."
    assert request["model"] == "vision-model" and request["max_tokens"] == 300
    assert request["client_config"]["http_client"] == "guarded-client" and request["closed"]
    assert result == "Read the chart."
    context, usage = recorded[0]
    assert context.owner_user_id == owner and context.purpose == "vision_ingest"
    assert context.connection_source == connection["source"]
    assert usage["success"] and usage["usage"] == {"prompt_tokens": 4, "total_tokens": 6}


def test_invoke_vision_records_failure_code_without_content(monkeypatch):
    owner = uuid4()
    recorded = []

    class Completions:
        def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=" "))], usage=None
            )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=Completions())

        def close(self):
            pass

    monkeypatch.setattr(model_calls, "get_settings", lambda: SimpleNamespace(
        model_timeout_seconds=12, model_max_output_tokens=300
    ))
    monkeypatch.setattr(model_calls, "resolved", lambda *_args: {
        "id": "connection-id", "source": "connection:connection-id:1", "model": "vision-model",
        "api_key": "secret", "base_url": "https://vision.example/v1",
    })
    monkeypatch.setattr(model_calls, "sync_client", lambda *_args: object())
    monkeypatch.setattr(model_calls, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(
        model_calls,
        "record_model_call",
        lambda context, **kwargs: recorded.append((context, kwargs)),
    )

    with pytest.raises(ApiError) as error:
        model_calls.invoke_vision(
            "Read this image.",
            b"image-bytes",
            "image/jpeg",
            context=ModelUsageContext(owner, "vision_grade"),
            saved={"id": "connection-id", "revision": 1, "kind": "vision"},
        )
    assert error.value.code == "vision_response_invalid"
    assert recorded[0][1]["success"] is False
    assert recorded[0][1]["usage"] == {"error_code": "vision_response_invalid"}


@pytest.mark.parametrize("mime", ["image/gif", "text/plain"])
def test_invoke_vision_rejects_unsupported_image_types(monkeypatch, mime):
    owner = uuid4()
    recorded = []
    monkeypatch.setattr(model_calls, "get_settings", lambda: SimpleNamespace(
        model_timeout_seconds=12, model_max_output_tokens=300
    ))
    monkeypatch.setattr(
        model_calls,
        "record_model_call",
        lambda context, **kwargs: recorded.append(kwargs),
    )
    with pytest.raises(ApiError) as error:
        model_calls.invoke_vision(
            "Read this image.",
            b"bytes",
            mime,
            context=ModelUsageContext(owner, "vision_ingest"),
        )
    assert error.value.code == "vision_image_invalid"
    assert recorded[0]["usage"]["error_code"] == "vision_image_invalid"


def test_vision_snapshot_requires_ready_default(monkeypatch):
    monkeypatch.setattr(model_connections, "default_snapshot", lambda *_args: {
        "id": None, "kind": "vision"
    })
    with pytest.raises(ApiError) as error:
        model_connections.vision_snapshot(object(), uuid4())
    assert error.value.status_code == 409 and error.value.code == "vision_not_configured"


def test_usage_normalization_preserves_failure_code():
    assert model_usage._normalize_usage({"error_code": "vision_model_unsupported"}) == {
        "error_code": "vision_model_unsupported"
    }


def test_vision_probe_uses_small_image_and_saved_snapshot(monkeypatch):
    from PIL import Image

    from study_platform.services import model_probe

    owner = uuid4()
    saved = {"id": str(uuid4()), "revision": 4, "kind": "vision"}
    calls = []

    def invoke(prompt, image_bytes, mime_type, *, context, saved):
        with Image.open(BytesIO(image_bytes)) as image:
            assert image.size == (1, 1)
        calls.append((prompt, mime_type, context.purpose, saved))
        return "white"

    monkeypatch.setattr(model_calls, "invoke_vision", invoke)
    assert model_probe.probe_vision(owner, saved) == "white"
    assert calls[0][1:] == ("image/png", "connection_test", saved)
