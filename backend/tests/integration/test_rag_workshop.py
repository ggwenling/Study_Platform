"""PostgreSQL integration tests for RAG workshop hit-testing and fragment editing."""

from __future__ import annotations

import hashlib
import os
from contextlib import nullcontext
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from study_platform.api.v1.auth import get_current_user
from study_platform.core.config import Settings
from study_platform.core.config import get_settings as load_settings
from study_platform.main import create_app
from study_platform.persistence.database import get_db
from study_platform.persistence.models import (
    ChunkEmbedding,
    IndexVersion,
    KnowledgeBase,
    Material,
    MaterialChunk,
    MaterialVersion,
    User,
)
from study_platform.services import chat, rag_workshop
from study_platform.services.lexical import lexical_column_values

pytestmark = pytest.mark.integration


def _unit_vec(x: float, y: float) -> list[float]:
    return [x, y] + [0.0] * 1022


@pytest.fixture
def workshop_env(tmp_path, monkeypatch):
    if os.getenv("RUN_DB_TESTS") != "1" or not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("explicit PostgreSQL test database required")
    url = make_url(os.environ["TEST_DATABASE_URL"])
    if not (url.database or "").endswith("_test"):
        pytest.fail("isolated *_test database required")

    engine = sa.create_engine(url.set(drivername="postgresql+psycopg"))
    load_settings.cache_clear()
    settings = Settings(
        _env_file=None,
        auth_jwt_secret="workshop-test-key" * 3,
        storage_dir=tmp_path,
        deepseek_api_key="",
        embedding_model="qwen3.7-text-embedding-flash",
        retrieval_min_similarity=0.35,
        database_url=url.render_as_string(hide_password=False),
        auth_public_url="http://testserver",
        allowed_origins=["http://testserver"],
    )
    for target in (
        "study_platform.services.model_connections.get_settings",
        "study_platform.services.rag_workshop.get_settings",
        "study_platform.core.config.get_settings",
    ):
        monkeypatch.setattr(target, lambda: settings)

    monkeypatch.setattr(
        rag_workshop,
        "embed_texts",
        lambda texts, **_: [_unit_vec(1.0, 0.0) for _ in texts],
    )

    with engine.connect() as connection:
        tx = connection.begin()
        db = Session(connection, join_transaction_mode="create_savepoint")
        factory = sessionmaker(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        monkeypatch.setattr(
            "study_platform.services.model_connections.session_factory", lambda: factory
        )

        user = User(
            id=uuid4(),
            email=f"{uuid4().hex}@example.test",
            password_hash="x",
            display_name="WorkshopUser",
        )
        kb = KnowledgeBase(id=uuid4(), owner_user_id=user.id, name="RAG工坊测试库")
        material = Material(
            id=uuid4(),
            owner_user_id=user.id,
            knowledge_base_id=kb.id,
            title="凸优化讲义",
            kind="other",
            status="active",
        )
        version = MaterialVersion(
            id=uuid4(),
            material_id=material.id,
            version_no=1,
            is_current=True,
            source_mime="text/markdown",
            source_sha256=hashlib.sha256(b"body").hexdigest(),
            byte_size=4,
            object_key=str(uuid4()),
            parse_status="ready",
        )
        index = IndexVersion(
            id=uuid4(),
            material_version_id=version.id,
            generation=1,
            pipeline_profile="test",
            embedding_profile="qwen3.7-text-embedding-flash:1024",
            status="ready",
            is_active=True,
            chunk_count=3,
            ready_at=datetime.now(UTC),
        )
        for row in (user, kb, material, version, index):
            db.add(row)
            db.flush()

        specs = [
            ("泛函极值与对偶空间概述", {"page": 1}, _unit_vec(1.0, 0.0)),
            ("拉格朗日乘子法用于带约束极值求解", {"page": 2}, _unit_vec(0.45, 0.89302855)),
            ("OCR误识别旧文本待修正", {"page": 3}, _unit_vec(0.1, 0.99498744)),
        ]
        chunk_ids = []
        for ordinal, (txt, loc, vec) in enumerate(specs):
            c = MaterialChunk(
                id=uuid4(),
                index_version_id=index.id,
                material_version_id=version.id,
                ordinal=ordinal,
                text=txt,
                text_sha256=hashlib.sha256(txt.encode("utf-8")).hexdigest(),
                locator=loc,
                **lexical_column_values(txt),
            )
            db.add(c)
            db.flush()
            db.add(
                ChunkEmbedding(
                    chunk_id=c.id,
                    model_profile_id="qwen3.7-text-embedding-flash:1024",
                    embedding=vec,
                )
            )
            chunk_ids.append(c.id)
        db.commit()

        app = create_app(settings)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: user
        with TestClient(
            app,
            base_url="http://testserver/api/v1",
            headers={"Origin": "http://testserver"},
        ) as http:
            yield http, db, kb.id, chunk_ids
        db.close()
        tx.rollback()
    engine.dispose()


def test_hit_test_hybrid_weights_and_fragment_reembedding(workshop_env):
    http, _db, kb_id, chunk_ids = workshop_env
    c1, c2, c3 = [str(cid) for cid in chunk_ids]

    # 1. Pure vector mode (lexical_weight=0): c1 (#1), c2 (#2), c3 (below threshold, rrf=0)
    pure_res = http.post(
        f"/knowledge-bases/{kb_id}/hit-test",
        json={"query": "拉格朗日乘子", "top_k": 5, "lexical_weight": 0.0},
    )
    assert pure_res.status_code == 200
    pure_data = pure_res.json()
    assert pure_data["query_terms"]
    assert [it["chunk_id"] for it in pure_data["items"][:3]] == [c1, c2, c3]
    assert pure_data["items"][0]["above_threshold"] is True
    assert pure_data["items"][2]["above_threshold"] is False
    assert pure_data["items"][2]["rrf_score"] == 0.0

    # 2. Hybrid lexical boost (lexical_weight=1.5): c2 matches lexical terms and overtakes c1
    hybrid_res = http.post(
        f"/knowledge-bases/{kb_id}/hit-test",
        json={"query": "拉格朗日乘子", "top_k": 5, "lexical_weight": 1.5},
    )
    assert hybrid_res.status_code == 200
    hybrid_items = hybrid_res.json()["items"]
    assert hybrid_items[0]["chunk_id"] == c2
    assert hybrid_items[0]["lexical_rank"] == 1
    assert len(hybrid_items[0]["matched_terms"]) >= 1

    # 3. Edit fragment c3 text & re-embed -> updates text, tsvector, and vector embedding
    patch_res = http.patch(
        f"/knowledge-bases/{kb_id}/fragments/{c3}",
        json={"text": "贝尔曼最优方程是动态规划的核心递推关系。"},
    )
    assert patch_res.status_code == 200
    patched = patch_res.json()
    assert patched["text"] == "贝尔曼最优方程是动态规划的核心递推关系。"
    assert patched["estimated_tokens"] > 5

    # 4. Hit-test for newly edited keyword now recalls c3 with both vector=1.0 and lexical hit
    after_res = http.post(
        f"/knowledge-bases/{kb_id}/hit-test",
        json={"query": "贝尔曼最优方程", "top_k": 3, "lexical_weight": 0.4},
    )
    assert after_res.status_code == 200
    top_hit = after_res.json()["items"][0]
    assert top_hit["chunk_id"] == c3
    assert top_hit["above_threshold"] is True
    assert top_hit["vector_similarity"] == pytest.approx(1.0, abs=1e-3)
    assert top_hit["lexical_rank"] == 1
    assert len(top_hit["matched_terms"]) >= 1


def test_retrieval_settings_patch_and_hit_test_inheritance(workshop_env):
    http, db, kb_id, _ = workshop_env
    payload = {
        "top_k": 4,
        "lexical_weight": 0.75,
        "min_similarity": 0.6,
        "rerank_enabled": True,
        "rerank_candidate_k": 12,
    }
    saved = http.patch(f"/knowledge-bases/{kb_id}/retrieval-settings", json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["retrieval_settings"] == payload
    assert http.get(f"/knowledge-bases/{kb_id}").json()["retrieval_settings"] == payload

    result = http.post(
        f"/knowledge-bases/{kb_id}/hit-test", json={"query": "拉格朗日乘子"}
    )
    assert result.status_code == 200, result.text
    response = result.json()
    assert response["lexical_weight"] == 0.75
    assert response["min_similarity_threshold"] == 0.6
    assert response["rerank_enabled"] is True
    assert response["rerank_error"] == "rerank_not_configured"
    assert len(response["items"]) <= 4

    bad_range = http.patch(
        f"/knowledge-bases/{kb_id}/retrieval-settings", json={"top_k": 21}
    )
    assert bad_range.status_code == 422
    bad_connection = http.patch(
        f"/knowledge-bases/{kb_id}/retrieval-settings",
        json={"rerank_connection_id": str(uuid4())},
    )
    assert bad_connection.status_code == 422
    assert db.get(KnowledgeBase, kb_id).retrieval_settings == payload


def test_rerank_only_receives_candidates_that_contribute_to_retrieval(workshop_env, monkeypatch):
    http, db, kb_id, (strong, lexical_only, _) = workshop_env
    owner = db.get(KnowledgeBase, kb_id).owner_user_id
    index_id = str(db.get(MaterialChunk, strong).index_version_id)
    seen = []

    def rank(_owner, _query, documents, **_):
        seen.append(documents)
        return [{"index": i, "relevance_score": 1.0} for i in reversed(range(len(documents)))]

    monkeypatch.setattr(rag_workshop, "rerank", rank)
    monkeypatch.setattr(chat, "rerank", rank)
    monkeypatch.setattr(chat, "get_settings", rag_workshop.get_settings)
    monkeypatch.setattr(chat, "session_factory", lambda: lambda: nullcontext(db))
    for weight, expected in [(0, [strong]), (1, [lexical_only, strong])]:
        settings = {"rerank_enabled": True, "min_similarity": 0.8, "lexical_weight": weight}
        query = "拉格朗日乘子"
        response = http.post(f"/knowledge-bases/{kb_id}/hit-test", json={"query": query, **settings})
        assert response.status_code == 200, response.text
        assert [item["chunk_id"] for item in response.json()["items"]] == [str(cid) for cid in expected]
        chat.retrieve_hybrid({"index_version_ids": [index_id], "retrieval_settings": settings},
                             [query], [_unit_vec(1, 0)], owner)
        assert seen[-1] == seen[-2] and len(seen[-1]) == len(expected)
    monkeypatch.setattr(rag_workshop, "embed_texts", lambda *_args, **_kwargs: [[0, 0, 1] + [0] * 1021])
    response = http.post(f"/knowledge-bases/{kb_id}/hit-test", json={
        "query": "拉格朗日乘子", "rerank_enabled": True, "min_similarity": 0.8, "lexical_weight": 0,
    })
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    assert len(seen) == 4



def test_rag_workshop_decouples_untested_llm_and_enforces_embedding_profile(workshop_env):
    from study_platform.persistence.models import ModelConnection, ModelConnectionLease

    http, db, kb_id, chunk_ids = workshop_env
    kb = db.get(KnowledgeBase, kb_id)
    target_chunk_id = str(chunk_ids[0])

    # 1. User has a default LLM connection in status='draft' (untested)
    draft_llm = ModelConnection(
        id=uuid4(),
        owner_user_id=kb.owner_user_id,
        kind="llm",
        name="UntestedLLM",
        revision=1,
        versions={"1": {"base_url": "https://api.example.test/v1", "model": "m1", "key": "enc"}},
        is_default=True,
        status="draft",
    )
    db.add(draft_llm)
    db.commit()

    # Both hit-test and fragment edit are pure embedding operations -> must succeed (200), zero LLM leases
    hit_res = http.post(
        f"/knowledge-bases/{kb_id}/hit-test",
        json={"query": "拉格朗日乘子", "top_k": 3, "lexical_weight": 0.4},
    )
    assert hit_res.status_code == 200

    patch_ok = http.patch(
        f"/knowledge-bases/{kb_id}/fragments/{target_chunk_id}",
        json={"text": "泛函极值与对偶空间修正版"},
    )
    assert patch_ok.status_code == 200
    lease_count = db.scalar(
        sa.select(sa.func.count())
        .select_from(ModelConnectionLease)
        .where(ModelConnectionLease.connection_id == draft_llm.id)
    )
    assert lease_count == 0

    # 2. Switch KB embedding_binding profile without re-indexing -> fragment edit must reject with 409
    kb.embedding_binding = {
        "base_url": "https://api.example.test/v1",
        "model": "custom-embed-v2",
        "dimensions": 1024,
        "profile": "custom-embed-v2:1024",
    }
    db.commit()

    mismatch_res = http.patch(
        f"/knowledge-bases/{kb_id}/fragments/{target_chunk_id}",
        json={"text": "不应写入的配置冲突文本"},
    )
    assert mismatch_res.status_code == 409
    assert mismatch_res.json()["code"] == "embedding_profile_mismatch"
    assert db.get(MaterialChunk, chunk_ids[0]).text == "泛函极值与对偶空间修正版"
