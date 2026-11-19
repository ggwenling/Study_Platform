import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from study_platform.services.lexical import lexical_index_text


def test_lexical_index_drops_noise_and_segment_chinese():
    indexed = lexical_index_text("项目编号 QZ-731 位于“雪松”仓库")
    assert "731" in indexed
    assert "雪松" in indexed


def test_lexical_index_returns_empty_string_for_symbol_only_text():
    assert lexical_index_text("---") == ""
    assert lexical_index_text("... *** !!!") == ""
    assert lexical_index_text("   \n\t  ") == ""


def test_0008_lexical_backfill_updates_symbol_only_chunks_without_looping(monkeypatch):
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "0008_lexical_backfill.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0008", migration_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    rows = {
        uuid4(): {"text": "---", "lexical": None},
        uuid4(): {"text": "   ", "lexical": None},
        uuid4(): {"text": "雪松仓库 QZ-731", "lexical": None},
    }
    select_calls = 0
    updates: list[tuple[object, str]] = []
    committed = 0
    closed = False

    class FakeResult:
        def __init__(self, items):
            self._items = items

        def all(self):
            return self._items

    class FakeSession:
        def __init__(self, bind):
            self.bind = bind

        def execute(self, statement, params):
            nonlocal select_calls
            sql = str(statement)
            if "SELECT id, text" in sql:
                select_calls += 1
                assert select_calls <= 3, "lexical backfill migration entered an infinite loop"
                pending = [
                    (chunk_id, row["text"])
                    for chunk_id, row in rows.items()
                    if row["lexical"] is None
                ][: params["limit"]]
                return FakeResult(pending)
            if "UPDATE study.material_chunks" in sql:
                chunk_id = params["id"]
                indexed = params["indexed"]
                updates.append((chunk_id, indexed))
                # PostgreSQL to_tsvector('simple', '') yields non-NULL ''::tsvector
                rows[chunk_id]["lexical"] = indexed
                return FakeResult([])
            raise AssertionError(f"unexpected SQL: {sql}")

        def commit(self):
            nonlocal committed
            committed += 1

        def close(self):
            nonlocal closed
            closed = True

    monkeypatch.setattr(module.op, "get_context", lambda: SimpleNamespace(opts={}))
    monkeypatch.setattr(module.op, "get_bind", lambda: object())
    monkeypatch.setattr(module, "Session", FakeSession)

    module.upgrade()

    assert select_calls == 2
    assert committed == 1
    assert closed is True
    assert len(updates) == 3
    assert all(row["lexical"] is not None for row in rows.values())
    indexed_by_text = {rows[chunk_id]["text"]: indexed for chunk_id, indexed in updates}
    assert indexed_by_text["---"] == ""
    assert indexed_by_text["   "] == ""
    assert "雪松" in indexed_by_text["雪松仓库 QZ-731"]
