"""Deterministic checks for RAG grounding prompts and citation publication (no models)."""

import json
from types import SimpleNamespace
from uuid import UUID

import pytest

from study_platform.core.errors import ApiError
from study_platform.services import chat


def evidence(count: int = 2) -> list[dict]:
    return [
        {
            "id": f"chunk-{index}",
            "title": f"Doc {index}",
            "text": f"Supporting fact {index}.",
        }
        for index in range(1, count + 1)
    ]


def _system_message(scope, chunks):
    return chat.prompt(scope, chunks, [])[0].content


def test_prompt_states_material_only_grounding_rules():
    scope = {"answer_mode": "knowledge_only", "intent": "qa", "content": "问题？"}
    system = _system_message(scope, evidence())
    assert "只使用本轮提供的资料作为资料依据" in system
    assert "不编造引用" in system
    assert "资料不足" in system
    assert "不要误判为冲突" in system


def test_prompt_knowledge_only_forbids_general_knowledge_supplement():
    scope = {"answer_mode": "knowledge_only", "intent": "qa", "content": "问题？"}
    system = _system_message(scope, evidence())
    assert "禁止用自身知识补充资料外的事实" in system
    assert "【通用补充】" not in system


def test_prompt_with_general_requires_section_markers():
    scope = {"answer_mode": "with_general", "intent": "qa", "content": "问题？"}
    system = _system_message(scope, evidence())
    assert "【知识库依据】" in system and "【通用补充】" in system


def test_prompt_serializes_one_source_block_per_evidence_chunk():
    scope = {"answer_mode": "knowledge_only", "intent": "qa", "content": "问题？"}
    system = _system_message(scope, evidence(3))
    assert '"source": "S1"' in system
    assert '"source": "S3"' in system
    assert '"source": "S4"' not in system
    assert "Supporting fact 2." in system


def test_prompt_exposes_adjacent_focus_table_rows_in_retrieval_order():
    scope = {
        "answer_mode": "knowledge_only",
        "intent": "qa",
        "content": "公开指南里的 Tab 导航做法，与本地 FC-0304、FC-8540 测试顺序如何对应？",
    }
    chunks = [
        {
            "id": "focus-row-three",
            "title": "focus-matrix.docx",
            "material_version_id": "focus-matrix-version",
            "text": "检查项：FC-8540 | 期望结果：下一步 Tab 到筛选按钮",
            "locator": {"table_path": [{"table": 1, "row": 3}]},
        },
        {
            "id": "wai-apg",
            "title": "wai-apg",
            "text": "Tab and Shift+Tab move focus between components.",
        },
        {
            "id": "focus-row-two",
            "title": "focus-matrix.docx",
            "material_version_id": "focus-matrix-version",
            "text": "检查项：FC-0304 | 期望结果：焦点先到搜索框",
            "locator": {"table_path": [{"table": 1, "row": 2}]},
        },
    ]

    documents = json.loads(_system_message(scope, chunks).split("\n本轮资料JSON：\n", 1)[1])
    focus_rows = {
        doc["source"]: doc.get("locator", {}).get("table_path")
        for doc in documents
        if doc["title"] == "focus-matrix.docx"
    }

    assert focus_rows == {"S1": [{"table": 1, "row": 3}], "S3": [{"table": 1, "row": 2}]}
    assert documents[0]["document"] == documents[2]["document"]
    assert "locator" not in documents[1]


def test_prompt_ignores_invalid_table_positions_and_does_not_expose_storage_ids():
    chunks = evidence(2)
    chunks[0].update(material_version_id="storage-id", locator={"table_path": [{"table": True, "row": 2}]})
    chunks[1].update(material_version_id="storage-id", locator={"table_path": [
        {"table": 1, "row": 2, "column": 3, "other": "untrusted"},
    ]})
    scope = {"answer_mode": "knowledge_only", "intent": "qa", "content": "问题？"}
    message = _system_message(scope, chunks)
    documents = json.loads(message.split("\n本轮资料JSON：\n", 1)[1])
    assert "locator" not in documents[0]
    assert documents[1]["locator"] == {"table_path": [{"table": 1, "row": 2, "column": 3}]}
    assert "storage-id" not in message and "untrusted" not in message


def test_local_evidence_adds_same_row_cells_and_never_crosses_index_or_table():
    def chunk(name, ordinal, row, index="i", table=1):
        return {
            "id": name, "index_version_id": index, "ordinal": ordinal, "section_id": name,
            "text": name, "locator": {"table_path": [{"table": table, "row": row}]},
        }

    anchor = chunk("KB-2920", 10, 3)
    other = chunk("WAI navigation", 2, 1, index="public")
    weak = chunk("Existing lower-ranked result", 22, 2)
    value = chunk("弹层关闭后回到触发按钮", 11, 3)
    wrong_index = chunk("跨资料不正确", 11, 3, index="another")
    wrong_table = chunk("同名表表项不正确", 12, 3, table=2)
    result = chat._select_local_evidence(
        [anchor, other, weak], [value, wrong_index, wrong_table], "KB-2920 的期望是什么？", 1,
    )
    assert result == [anchor, other, weak, value]


def test_local_evidence_respects_headings_while_supplementing_a_limit():
    def line(number, text):
        return {"id": number, "index_version_id": "i", "ordinal": number, "section_id": number,
                "text": text, "locator": {"line": number}}

    anchor = line(167, "stale-if-error = delta-seconds")
    valid = line(183, "Its value indicates the upper limit to staleness")
    boundary = line(184, "4.1.  Example")
    foreign = line(185, "The example must not exceed the upper limit")
    assert chat._select_local_evidence(
        [anchor], [valid, boundary, foreign], "stale-if-error 的窗口限制是什么？", 4
    ) == [anchor, valid]


def test_local_evidence_keeps_original_hits_when_adding_a_continuation():
    def row(number, text, locator):
        return {"id": number, "index_version_id": "i", "ordinal": number, "section_id": number,
                "text": text, "locator": locator}

    hit = row(1, "The protocol explains this, but", {"line": 1})
    plain = row(30, "Other original text", {"line": 30})
    anchor = row(50, "CT-8402", {"table_path": [{"table": 1, "row": 3, "column": 1}]})
    value = row(51, "Sample validation status 304", {"table_path": [{"table": 1, "row": 3, "column": 2}]})
    next_line = row(2, "only if there is a matching response", {"line": 2})
    result = chat._select_local_evidence([hit, anchor, plain, value], [next_line], "CT-8402 的值？", 1)
    assert result == [hit, anchor, plain, value, next_line]


def test_extra_queries_require_a_missing_compound_clause_and_leave_optional_rerank_budget(monkeypatch):
    monkeypatch.setattr(chat, "get_settings", lambda: SimpleNamespace(rag_query_rewrite_enabled=False))
    monkeypatch.setattr(chat, "effective_settings", lambda settings: settings or {"rerank_enabled": False})
    scope = {"index_version_ids": ["index"]}
    evidence = [
        {"text": "The stale-while-revalidate extension permits asynchronous cache validation."},
        {"text": "With stale-while-revalidate enabled, a cache may serve a stale response."},
    ]
    question = "stale-while-revalidate 有何作用？stale-if-error 的限制是什么？"
    assert chat.missing_qa_queries(scope, question, evidence) == ["stale-if-error 的限制是什么"]
    assert chat.missing_qa_queries(
        scope, "stale-if-error 和 stale-while-revalidate 的基本触发场景分别是什么？", evidence,
    ) == ["stale-if-error"]
    for conjunction in (" 和 ", " 与 ", "和", "与"):
        question = f"stale-if-error{conjunction}stale-while-revalidate 的基本触发场景是什么？"
        assert chat.missing_qa_queries(scope, question, evidence) == ["stale-if-error"]
    assert not chat.missing_qa_queries(scope, "stale-if-error 的限制是什么？", evidence)
    assert not chat.missing_qa_queries(scope, "前端工程师如何参与审核流程？", evidence)
    assert not chat.missing_qa_queries(
        {**scope, "retrieval_settings": {"rerank_enabled": True}}, question, evidence,
    )


@pytest.mark.parametrize("second_has_distinct_hit", [True, False])
def test_supplement_reserves_a_hit_per_query_and_uses_one_embedding_batch(monkeypatch, second_has_distinct_hit):
    original = evidence(10)
    first = {"id": "fresh", "text": "A new distinct fact"}
    extra = {"id": "extra", "text": "Another hit for the first query"}
    second = {"id": "second", "text": "Only the second query found this"}
    queries = ["first clause", "second clause"]
    vectors = [[0.1], [0.2]]
    embedding_calls = []
    retrieval_calls = []
    scope, owner = {"index_version_ids": ["i"]}, UUID(int=1)

    def embed(queries, **_kwargs):
        embedding_calls.append(queries)
        return vectors

    def retrieve(scope, queries, vectors, owner):
        retrieval_calls.append((scope, queries, vectors, owner))
        if queries == ["first clause"]:
            return [original[0], first, extra]
        return [first, second] if second_has_distinct_hit else [first]

    monkeypatch.setattr(chat, "embed_texts", embed)
    monkeypatch.setattr(chat, "effective_settings", lambda _: {"top_k": 10})
    monkeypatch.setattr(chat, "retrieve_hybrid", retrieve)
    monkeypatch.setattr(chat, "_local_qa_evidence", lambda _scope, _question, _owner, rows, _budget: rows)
    assert chat.supplement_qa(scope, "compound question", owner, original, queries) == [
        *original, first, second if second_has_distinct_hit else extra,
    ]
    assert embedding_calls == [queries]
    assert retrieval_calls == [(scope, [query], [vector], owner) for query, vector in zip(queries, vectors, strict=True)]


@pytest.mark.parametrize("conjunction", ["和", "与"])
@pytest.mark.parametrize("marker", ["的", ""])
def test_entity_comparison_preserves_shared_predicate(monkeypatch, conjunction, marker):
    monkeypatch.setattr(chat, "get_settings", lambda: SimpleNamespace(rag_query_rewrite_enabled=False))
    monkeypatch.setattr(chat, "effective_settings", lambda _: {"rerank_enabled": False})
    scope = {"index_version_ids": ["i"]}
    question = f"苏州{conjunction}上海{marker}公共交通有什么区别？"
    queries = [f"{entity}{marker}公共交通有什么区别" for entity in ("苏州", "上海")]
    assert chat.missing_qa_queries(scope, question, []) == queries
    assert chat.missing_qa_queries(scope, question, [
        {"text": "苏州和上海都是中国城市，两地有很多区别。"},
        {"text": "公共交通包括地铁和公交。"},
    ]) == queries
    assert chat.missing_qa_queries(scope, question, [{"text": "上海的公共交通以轨道交通为主。"}]) == queries[:1]
    assert not chat.missing_qa_queries(scope, question, [
        {"text": "苏州的公共交通包含公交和轨道交通。"},
        {"text": "上海的公共交通以轨道交通为主。"},
    ])


@pytest.mark.parametrize("top_k", [2, 3])
def test_supplement_queries_only_as_many_clauses_as_fit(monkeypatch, top_k):
    original = evidence(top_k)
    extra = {"id": "extra", "text": "Complementary evidence"}
    embedded, retrieved = [], []

    def embed(queries, **_kwargs):
        embedded.append(queries)
        return [[0.1] for _ in queries]

    def retrieve(_scope, queries, _vectors, _owner):
        retrieved.append(queries)
        return [extra]

    monkeypatch.setattr(chat, "effective_settings", lambda _: {"top_k": top_k})
    monkeypatch.setattr(chat, "embed_texts", embed)
    monkeypatch.setattr(chat, "retrieve_hybrid", retrieve)
    assert chat.supplement_qa({}, "compound", UUID(int=1), original, ["first", "second"]) == [*original, extra]
    assert embedded == retrieved == [["first"]]


def test_cited_chunks_preserves_citation_order_and_indexes_evidence():
    rows = chat.cited_chunks("依据 [S2] 与 [S1]", evidence(2))
    assert [row["source_number"] for row in rows] == [2, 1]
    assert rows[0]["id"] == "chunk-2"


def test_cited_chunks_rejects_invalid_source_numbers():
    with pytest.raises(ApiError) as error:
        chat.cited_chunks("引用 [S9]", evidence(1))
    assert error.value.code == "citation_invalid"


@pytest.mark.parametrize(
    "answer",
    [
        "没有标注的回答",
        "General knowledge answer without markers",
    ],
)
def test_cited_chunks_requires_markers_when_evidence_exists(answer):
    with pytest.raises(ApiError) as error:
        chat.cited_chunks(answer, evidence(1))
    assert error.value.code == "citation_missing"
    assert error.value.retryable is True


def test_cited_chunks_allows_explicit_insufficient_material_without_markers():
    assert chat.cited_chunks("当前资料不足，无法根据所选知识库回答。", evidence(1)) == []


def test_cited_chunks_allows_empty_evidence_without_markers():
    assert chat.cited_chunks("简短回答", []) == []
