from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from psycopg import OperationalError as PsycopgOperationalError
from sqlalchemy.exc import InterfaceError, OperationalError

from study_platform.cli import worker
from study_platform.core.config import Settings
from study_platform.persistence.database import connection_failure
from study_platform.services import worker_runner


def failure(code=None):
    return OperationalError("private statement", {}, SimpleNamespace(sqlstate=code))


def test_worker_reconnects_with_capped_backoff_and_resets(monkeypatch, caplog):
    claim = Mock(side_effect=[*[failure("08006") for _ in range(7)], None, None, failure(), KeyboardInterrupt])
    stop = Mock()
    stop.is_set.return_value = False
    elapsed = 0

    def wait(seconds):
        nonlocal elapsed
        elapsed += seconds

    stop.wait.side_effect = wait
    monkeypatch.setattr(worker_runner, "_claim", claim)
    monkeypatch.setattr(worker_runner.time, "monotonic", lambda: elapsed)
    with pytest.raises(KeyboardInterrupt):
        worker_runner.run(stop, Settings(_env_file=None))
    assert [call.args[0] for call in stop.wait.call_args_list] == [1, 2, 4, 8, 16, 30, 30, 1, 1]
    assert "private statement" not in caplog.text


@pytest.mark.parametrize("code", ["28P01", "3D000", "42501"])
def test_worker_does_not_loop_on_configuration_errors(monkeypatch, code):
    claim = Mock(side_effect=failure(code))
    monkeypatch.setattr(worker_runner, "_claim", claim)
    with pytest.raises(SystemExit) as error:
        worker.main()
    assert error.value.code == 1
    claim.assert_called_once_with(worker_runner.HEAVY)


def test_worker_reports_unexpected_failure_without_private_exception(monkeypatch, caplog):
    monkeypatch.setattr(worker_runner, "_claim", Mock(side_effect=PermissionError("private path")))
    with pytest.raises(SystemExit):
        worker.main()
    assert "PermissionError" in caplog.text
    assert "private path" not in caplog.text


def test_worker_recovers_invalidated_connections_but_not_programming_errors():
    assert connection_failure(
        InterfaceError("", {}, Exception(), connection_invalidated=True)
    )
    assert not connection_failure(InterfaceError("", {}, Exception()))


def test_missing_password_is_not_mistaken_for_a_network_outage():
    assert not connection_failure(
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
    claim = Mock(side_effect=OperationalError(None, None, original))
    monkeypatch.setattr(worker_runner, "_claim", claim)
    with pytest.raises(SystemExit) as error:
        worker.main()
    assert error.value.code == 1
    claim.assert_called_once_with(worker_runner.HEAVY)
    assert '"missing"' not in caplog.text
