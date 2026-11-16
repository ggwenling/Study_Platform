from uuid import uuid4

from sqlalchemy.dialects import postgresql

from study_platform.services import chat
from study_platform.services.lexical import lexical_query_terms, plain_tsquery
from study_platform.services.retrieval import dedup_documents, fuse_rankings, idf_rank


def test_dedup_keeps_first_chunk_per_document():
    doc_a, doc_b = uuid4(), uuid4()
    chunks = [
        {"id": 1, "material_version_id": doc_a, "text": "a1"},
        {"id": 2, "material_version_id": doc_a, "text": "a2"},
        {"id": 3, "material_version_id": doc_b, "text": "b1"},
        {"id": 4, "material_version_id": doc_b, "text": "b2"},
        {"id": 5, "material_version_id": uuid4(), "text": "c1"},
    ]
    kept = dedup_documents(chunks, 3)
    assert [row["id"] for row in kept] == [1, 3, 5]
    assert dedup_documents(chunks, 0) == []


def test_fuse_rankings_still_matches_existing_rrf_cases():
    def row(key):
        return {"id": key}

    assert fuse_rankings(
        [[row("a"), row("b"), row("c")], [row("b"), row("d"), row("a")]], 3
    ) == [row("b"), row("a"), row("d")]
    assert chat.fuse_rankings([[row("a"), row("b")], [row("x"), row("y")]], 4) == [
        row("a"),
        row("x"),
        row("b"),
        row("y"),
    ]


def test_lexical_query_terms_drop_particles_and_question_words():
    terms = lexical_query_terms("北坞冷库的冷冻区警戒温度是多少？")
    assert "北坞" in terms and "警戒" in terms and "温度" in terms
    assert not {"的", "是", "多少", "区"} & set(terms)
    assert "v2.4" in lexical_query_terms("巢云v2.4故障工单的前缀是什么？")
    assert lexical_query_terms("的是吗？") == []
    assert plain_tsquery("的是吗？") is None


def test_plain_tsquery_ors_terms_instead_of_anding():
    compiled = str(
        plain_tsquery("北坞冷库警戒温度").compile(dialect=postgresql.dialect())
    )
    assert compiled.count("plainto_tsquery") >= 2
    assert "||" in compiled


def test_dedup_backfills_when_few_documents():
    doc_a, doc_b = uuid4(), uuid4()
    chunks = [
        {"id": 1, "material_version_id": doc_a},
        {"id": 2, "material_version_id": doc_a},
        {"id": 3, "material_version_id": doc_b},
        {"id": 4, "material_version_id": doc_a},
    ]
    assert [row["id"] for row in dedup_documents(chunks, 3)] == [1, 3, 2]
    assert [row["id"] for row in dedup_documents(chunks, 10)] == [1, 3, 2, 4]


def test_idf_rank_prefers_rare_term_over_common_terms():
    # term 0 = "2020" (in every row), term 1 = "中国" (common), term 2 = rare entity
    rows = [
        ("common", "t", 0.5, (True, True, False)),
        ("rare", "t", 0.1, (True, False, True)),
        ("other", "t", 0.4, (True, True, False)),
    ]
    ranked = idf_rank(rows, total_chunks=50)
    assert ranked[0][0] == "rare"
    # ties on IDF fall back to ts_rank
    assert [chunk for chunk, _ in ranked[1:]] == ["common", "other"]
    assert idf_rank([], 10) == []


def test_hybrid_and_vector_dedup_to_top_k(monkeypatch):
    doc = uuid4()
    rows = [{"id": i, "material_version_id": doc if i < 3 else uuid4()} for i in range(8)]
    monkeypatch.setattr(chat, "_final_limit", lambda: 4)
    kept = chat._finalize_ranking(rows, chat._final_limit())
    assert [row["id"] for row in kept] == [0, 3, 4, 5]
