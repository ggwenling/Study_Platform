"""Fail-closed guard: only disposable *_test databases may be reset or destroyed."""

from __future__ import annotations

import os
import re
import sys

from sqlalchemy.engine import make_url

_ALLOWED = re.compile(r"^[a-z0-9_]+_test$|^study_eval_[a-f0-9]+_test$")


def assert_safe_database(env_key: str = "DATABASE_URL") -> str:
    raw = os.environ.get(env_key)
    if not raw:
        raise SystemExit(f"{env_key} missing — refusing destructive operations")
    url = make_url(raw)
    name = url.database or ""
    if url.get_backend_name() != "postgresql":
        raise SystemExit(f"{env_key} must be PostgreSQL, got {url.get_backend_name()}")
    if {"dbname", "service", "servicefile"} & set(url.query):
        raise SystemExit(f"{env_key} must not override connection target")
    if not _ALLOWED.match(name):
        raise SystemExit(
            f"Refusing operation on non-test database {name!r}; "
            "only *_test or study_eval_<hex>_test allowed"
        )
    return name


def main() -> int:
    for key in ("DATABASE_URL", "TEST_DATABASE_URL", "MIGRATION_DATABASE_URL"):
        if os.environ.get(key):
            assert_safe_database(key)
    print("OK: database reset guard passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())