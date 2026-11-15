from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from psycopg import OperationalError as PsycopgOperationalError
from sqlalchemy.exc import InterfaceError, OperationalError

from study_platform.cli import worker


def failure(code=None):
    return OperationalError("private statement", {}, SimpleNamespace(sqlstate=code))


def test_worker_reconnects_with_capped_backoff_and_resets(monkeypatch, caplog):
    run = Mock(
        side_effect=[*[failure("08006") for _ in range(7)], True, failure(), KeyboardInterrupt]
    )
    sleep = Mock()
    monkeypatch.setattr(worker, "run_once", run)
    monkeypatch.setattr(worker.time, "sleep", sleep)
    with pytest.raises(KeyboardInterrupt):
        worker.main()
    assert [call.args[0] for call in sleep.call_args_list] == [1, 2, 4, 8, 16, 30, 30, 1]
    assert "private statement" not in caplog.text


@pytest.mark.parametrize("code", ["28P01", "3D000", "42501"])
def test_worker_does_not_loop_on_configuration_errors(monkeypatch, code):
    monkeypatch.setattr(worker, "run_once", Mock(side_effect=failure(code)))
    sleep = Mock()
    monkeypatch.setattr(worker.time, "sleep", sleep)
    with pytest.raises(SystemExit) as error:
        worker.main()
    assert error.value.code == 1
    sleep.assert_not_called()


def test_worker_reports_unexpected_failure_without_private_exception(monkeypatch, caplog):
    monkeypatch.setattr(worker, "run_once", Mock(side_effect=PermissionError("private path")))
    with pytest.raises(SystemExit):
        worker.main()
    assert "PermissionError" in caplog.text
    assert "private path" not in caplog.text


def test_worker_recovers_invalidated_connections_but_not_programming_errors():
    assert worker.connection_failure(
        InterfaceError("", {}, Exception(), connection_invalidated=True)
    )
    assert not worker.connection_failure(InterfaceError("", {}, Exception()))


def test_missing_password_is_not_mistaken_for_a_network_outage():
    assert not worker.connection_failure(
        OperationalError("", {}, Exception("no password supplied"))
    )


@pytest.mark.parametrize(
    "diagnostic",
    [
        b'FATAL:  database "missing" does not exist\n',
        '致命错误:  数据库 "missing" 不存在\n'.encode("gb18030"),
    ],
)
def test_worker_exits_on_missing_database_startup_without_sqlstate(monkeypatch, caplog, diagnostic):
    original = PsycopgOperationalError(
        diagnostic.decode("utf-8", errors="replace"),
        pgconn=SimpleNamespace(error_message=diagnostic),
    )
    assert original.sqlstate is None
    monkeypatch.setattr(
        worker, "run_once", Mock(side_effect=OperationalError(None, None, original))
    )
    sleep = Mock()
    monkeypatch.setattr(worker.time, "sleep", sleep)
    with pytest.raises(SystemExit) as error:
        worker.main()
    assert error.value.code == 1
    sleep.assert_not_called()
    assert '"missing"' not in caplog.text
