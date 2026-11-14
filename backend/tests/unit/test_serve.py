import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

from uvicorn import Config

from study_platform.cli import serve


def test_windows_server_passes_selector_loop_factory(monkeypatch):
    run = Mock()
    monkeypatch.setattr(serve, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(serve.uvicorn, "run", run)
    serve.main()
    factory = Config("study_platform.main:create_app", **run.call_args.kwargs).get_loop_factory()
    assert factory is asyncio.SelectorEventLoop
