from unittest.mock import MagicMock, Mock

import pytest
from sqlalchemy.engine import make_url

from study_platform.cli import bootstrap_database


def test_bootstrap_writes_auth_secret_without_printing_credentials(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        bootstrap_database, "__file__", str(tmp_path / "backend/src/study_platform/cli/script.py")
    )
    monkeypatch.setattr("sys.argv", ["bootstrap_database"])
    monkeypatch.setattr(bootstrap_database.getpass, "getpass", lambda _: "admin-password")
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.execute.return_value.fetchone.return_value = (True,)
    connection.execute.return_value.fetchall.return_value = []
    monkeypatch.setattr(bootstrap_database.psycopg, "connect", Mock(return_value=connection))

    bootstrap_database.main()

    values = dict(
        line.split("=", 1) for line in (tmp_path / ".env").read_text().splitlines()
    )
    assert len(values["AUTH_JWT_SECRET"].encode()) >= 32
    output = capsys.readouterr().out
    assert values["AUTH_JWT_SECRET"] not in output
    for key, value in values.items():
        if key.endswith("DATABASE_URL"):
            assert make_url(value).password not in output


def test_bootstrap_does_not_overwrite_existing_env(monkeypatch, tmp_path):
    monkeypatch.setattr(
        bootstrap_database, "__file__", str(tmp_path / "backend/src/study_platform/cli/script.py")
    )
    monkeypatch.setattr("sys.argv", ["bootstrap_database"])
    connect = Mock()
    monkeypatch.setattr(bootstrap_database.psycopg, "connect", connect)
    env_path = tmp_path / ".env"
    env_path.write_text("existing configuration\n")

    with pytest.raises(SystemExit, match="already exists"):
        bootstrap_database.main()

    assert env_path.read_text() == "existing configuration\n"
    connect.assert_not_called()
