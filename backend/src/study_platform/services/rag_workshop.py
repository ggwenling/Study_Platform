"""RAG workshop diagnostics: hybrid hit-testing and single-fragment editing & re-embedding."""

from __future__ import annotations

import hashlib
import math
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Session

from study_platform.api.v1.materials import missing, owned_kb
from study_platform.core.config import get_settings
from study_platform.core.errors import ApiError
from study_platform.persistence.models import (
    AiJob,
    ChunkEmbedding,
    IndexVersion,
    Material,
    MaterialChunk,
    MaterialVersion,
)
from study_platform.services import model_connections
from study_platform.services.chat import chunk_query, visible_indices
from study_platform.services.lexical import (
    lexical_column_values,
    lexical_query_terms,
    plain_tsquery,
)
from study_platform.services.material_reader import locator_label
from study_platform.services.model_usage import ModelUsageContext
from study_platform.services.models import embed_texts
from study_platform.services.rerank import rerank
from study_platform.services.retrieval import LEXICAL_IDF_POOL, dedup_documents
from study_platform.services.retrieval_settings import effective_settings, rerank_snapshot


def _public_locator(locator: dict | None) -> dict:
    if not isinstance(locator, dict):
        return {}
    return {k: v for k, v in locator.items() if k != "ocr"}


def _estimated_tokens(text: str) -> int:
    ascii_count = sum(char.isascii() for char in text)
    return len(text) - ascii_count + (ascii_count + 3) // 4


def run_hit_test(
    db: Session,
    *,
    user: Any,
    kb_id: UUID,
    query: str,
    top_k: int | None = None,
    lexical_weight: float | None = None,
    min_similarity: float | None = None,
    rerank_enabled: bool | None = None,
    rerank_connection_id: UUID | None = None,
    rerank_connection_override: bool = False,
    rerank_candidate_k: int | None = None,
    material_ids: list[UUID] | None = None,
) -> dict[str, Any]:
    cleaned_query = query.strip()
    if not cleaned_query:
        raise ApiError(422, "validation_error", "测试提问不能为空")

    kb = owned_kb(db, user, kb_id, lock=True)
    settings = get_settings()
    retrieval = effective_settings(kb.retrieval_settings, settings)
    for key, value in (
        ("top_k", top_k),
        ("lexical_weight", lexical_weight),
        ("min_similarity", min_similarity),
        ("rerank_enabled", rerank_enabled),
        ("rerank_candidate_k", rerank_candidate_k),
    ):
        if value is not None:
            retrieval[key] = value
    if rerank_connection_override:
        retrieval["rerank_connection_id"] = str(rerank_connection_id) if rerank_connection_id else None
    top_k = int(retrieval["top_k"])
    lexical_weight = float(retrieval["lexical_weight"])
    rerank_enabled = bool(retrieval["rerank_enabled"])
    if rerank_enabled and not settings.retrieval_hybrid_enabled:
        lexical_weight = 0.0
    use_ts_rank = rerank_enabled and settings.retrieval_lexical_scoring == "ts_rank"
    embedding_model = model_connections.library_snapshot(db, user.id, kb_id)
    rerank_model = rerank_snapshot(db, user.id, retrieval)
    index_ids = visible_indices(db, user.id, kb_id, material_ids)
    terms = lexical_query_terms(cleaned_query)
    min_sim = float(retrieval["min_similarity"])
    db.commit()

    if not index_ids:
        return {
            "query": cleaned_query,
            "query_terms": terms,
            "lexical_weight": lexical_weight,
            "min_similarity_threshold": min_sim,
            "rerank_enabled": rerank_enabled,
            "rerank_error": None,
            "items": [],
        }

    with model_connections.task(
        user.id, llm={"id": None, "kind": "llm"}, embedding=embedding_model,
        rerank=rerank_model,
    ):

        vectors = embed_texts(
            [cleaned_query],
            context=ModelUsageContext(user.id, "embedding"),
        )
        if not vectors or len(vectors[0]) != 1024:
            raise ApiError(502, "embedding_invalid", "向量模型返回维度或内容不正确")
        vector = vectors[0]

        indices = [UUID(x) for x in index_ids]
        profile = embedding_model["binding"]["profile"]
        candidate_k = (
            max(top_k, int(retrieval["rerank_candidate_k"]))
            if rerank_enabled
            else max(20, top_k * 3)
        )

        query = chunk_query(indices, user.id)
        base_query = query.add_columns(Material.id.label("material_id"))
        scoped = (
            query.with_only_columns(
                ChunkEmbedding.chunk_id, ChunkEmbedding.embedding, maintain_column_froms=True
            )
            .join(ChunkEmbedding, ChunkEmbedding.chunk_id == MaterialChunk.id)
            .where(ChunkEmbedding.model_profile_id == profile)
            .cte("hit_scoped")
            .prefix_with("MATERIALIZED")
        )
        distance_col = scoped.c.embedding.cosine_distance(vector).label("distance")
        vec_matches = (
            sa.select(scoped.c.chunk_id, distance_col)
            .order_by(distance_col, scoped.c.chunk_id)
            .limit(candidate_k)
            .subquery()
        )
        vec_rows = db.execute(
            base_query.add_columns(vec_matches.c.distance)
            .join(vec_matches, vec_matches.c.chunk_id == MaterialChunk.id)
            .order_by(vec_matches.c.distance, MaterialChunk.id)
        ).all()

        chunk_meta: dict[UUID, dict[str, Any]] = {}
        vector_info: dict[UUID, tuple[int, float, bool]] = {}
        for rank, (chunk, title, mat_id, dist) in enumerate(vec_rows, start=1):
            sim = round(max(0.0, min(1.0, 1.0 - float(dist))), 4)
            above = float(dist) <= 1.0 - min_sim
            vector_info[chunk.id] = (rank, sim, above)
            chunk_meta[chunk.id] = {
                "chunk": chunk,
                "title": title,
                "material_id": mat_id,
            }

        lexical_info: dict[UUID, tuple[int, float, list[str]]] = {}
        if terms:
            any_term = plain_tsquery(cleaned_query)
            term_queries = [sa.func.plainto_tsquery("simple", t) for t in terms]
            matched_cols = [
                MaterialChunk.lexical.op("@@")(tq).label(f"lex_t{i}")
                for i, tq in enumerate(term_queries)
            ]
            ts_rank_col = sa.func.ts_rank_cd(MaterialChunk.lexical, any_term).label("lex_rank")
            lex_rows = db.execute(
                base_query.add_columns(ts_rank_col, *matched_cols)
                .where(MaterialChunk.lexical.is_not(None), MaterialChunk.lexical.op("@@")(any_term))
                .order_by(sa.desc(ts_rank_col), MaterialChunk.id)
                .limit(candidate_k if use_ts_rank else LEXICAL_IDF_POOL)
            ).all()
            if use_ts_rank:
                for rank, (chunk, title, mat_id, ts_rank, *flags) in enumerate(lex_rows, start=1):
                    lexical_info[chunk.id] = (rank, round(float(ts_rank), 4), [t for t, flag in zip(terms, flags) if flag])
                    chunk_meta.setdefault(chunk.id, {"chunk": chunk, "title": title, "material_id": mat_id})
            elif lex_rows:
                total_pool = db.scalar(
                    sa.select(sa.func.count()).select_from(query.subquery())
                ) or len(lex_rows)
                total = max(int(total_pool), len(lex_rows))
                flags_list = [tuple(bool(f) for f in row[4:]) for row in lex_rows]
                df = [sum(1 for fl in flags_list if fl[i]) for i in range(len(terms))]
                idf = [math.log(1 + (total - d + 0.5) / (d + 0.5)) for d in df]
                scored_lex = []
                for idx, row in enumerate(lex_rows):
                    chunk, title, mat_id, ts_r = row[0], row[1], row[2], float(row[3] or 0.0)
                    flags = flags_list[idx]
                    idf_sum = sum(w for w, hit in zip(idf, flags) if hit)
                    matched_words = [t for t, hit in zip(terms, flags) if hit]
                    scored_lex.append(
                        (-idf_sum, -ts_r, idx, chunk, title, mat_id, idf_sum, matched_words)
                    )
                scored_lex.sort(key=lambda item: item[:3])
                for rank, (*_, chunk, title, mat_id, idf_sum, matched_words) in enumerate(
                    scored_lex[:candidate_k], start=1
                ):
                    lexical_info[chunk.id] = (rank, round(idf_sum, 4), matched_words)
                    chunk_meta.setdefault(
                        chunk.id,
                        {"chunk": chunk, "title": title, "material_id": mat_id},
                    )

        rrf_k = 60
        combined = []
        for cid in chunk_meta:
            v_entry = vector_info.get(cid)
            l_entry = lexical_info.get(cid)
            v_rank = v_entry[0] if v_entry else None
            v_above = v_entry[2] if v_entry else False
            l_rank = l_entry[0] if l_entry else None
            score = 0.0
            if v_rank is not None and v_above:
                score += 1.0 / (rrf_k + v_rank)
            if lexical_weight > 0 and l_rank is not None:
                score += lexical_weight / (rrf_k + l_rank)
            if score <= 0.0 and (rerank_enabled or v_rank is None):
                continue
            # Below-threshold vectors are useful diagnostics, but they carry no
            # vector rank in the live RRF ordering used before reranking.
            fusion_vector_rank = v_rank if v_rank is not None and (v_above or not rerank_enabled) else 10**9
            best_rank = min(
                fusion_vector_rank,
                l_rank if l_rank is not None else 10**9,
            )
            combined.append(
                (
                    -score,
                    best_rank,
                    fusion_vector_rank,
                    str(cid),
                    cid,
                    round(score, 6),
                )
            )
        combined.sort(key=lambda item: item[:4])

        out_items = []
        for rank, (*_, cid, rrf_score) in enumerate(combined[:candidate_k], start=1):
            meta = chunk_meta[cid]
            chunk: MaterialChunk = meta["chunk"]
            v_entry = vector_info.get(cid)
            l_entry = lexical_info.get(cid)
            pub_loc = _public_locator(chunk.locator)
            out_items.append(
                {
                    "chunk_id": str(chunk.id),
                    "material_id": str(meta["material_id"]),
                    "version_id": str(chunk.material_version_id),
                    "title": meta["title"],
                    "locator": pub_loc,
                    "location_label": locator_label(pub_loc),
                    "text": chunk.text,
                    "retrieval_enabled": bool(chunk.retrieval_enabled),
                    "vector_rank": v_entry[0] if v_entry else None,
                    "vector_similarity": v_entry[1] if v_entry else None,
                    "above_threshold": v_entry[2] if v_entry else False,
                    "lexical_rank": l_entry[0] if l_entry else None,
                    "lexical_score": l_entry[1] if l_entry else None,
                    "matched_terms": l_entry[2] if l_entry else [],
                    "rrf_score": rrf_score,
                    "rrf_rank": rank,
                    "rerank_rank": None,
                    "rerank_score": None,
                    "material_version_id": str(chunk.material_version_id),
                }
            )

        rerank_error = None
        if rerank_enabled and out_items:
            try:
                results = rerank(
                    user.id,
                    cleaned_query,
                    [f"{item['title']}\n{item['text']}" for item in out_items],
                    top_n=len(out_items),
                    saved=rerank_model,
                )
                ranked_indexes = [item["index"] for item in results or []]
                ranked_indexes.extend(
                    index for index in range(len(out_items)) if index not in set(ranked_indexes)
                )
                score_by_index = {item["index"]: item["relevance_score"] for item in results or []}
                out_items = [out_items[index] for index in ranked_indexes]
                for rank, item in enumerate(out_items, start=1):
                    original_index = ranked_indexes[rank - 1]
                    item["rerank_rank"] = rank
                    item["rerank_score"] = score_by_index.get(original_index)
            except ApiError as error:
                rerank_error = error.code

        out_items = dedup_documents(out_items, top_k)
        for item in out_items:
            item.pop("material_version_id", None)

        return {
            "query": cleaned_query,
            "query_terms": terms,
            "lexical_weight": lexical_weight,
            "min_similarity_threshold": min_sim,
            "rerank_enabled": rerank_enabled,
            "rerank_error": rerank_error,
            "items": out_items,
        }


def edit_fragment_text(
    db: Session,
    *,
    user: Any,
    kb_id: UUID,
    chunk_id: UUID,
    text: str,
) -> dict[str, Any]:
    cleaned = text.strip()
    if not cleaned:
        raise ApiError(422, "validation_error", "片段内容不能为空")
    if len(cleaned) > 8000:
        raise ApiError(422, "validation_error", "单个片段内容不能超过 8000 字符")

    # Verify ownership before calling embedding API, without holding DB locks across network I/O.
    owned_kb(db, user, kb_id)
    exists_in_kb = db.scalar(
        sa.select(MaterialChunk.id)
        .select_from(Material)
        .join(MaterialVersion, MaterialVersion.material_id == Material.id)
        .join(MaterialChunk, MaterialChunk.material_version_id == MaterialVersion.id)
        .where(
            Material.knowledge_base_id == kb_id,
            Material.owner_user_id == user.id,
            Material.deleted_at.is_(None),
            Material.status == "active",
            MaterialChunk.id == chunk_id,
        )
    )
    if not exists_in_kb:
        raise missing()

    with model_connections.task(user.id, llm={"id": None, "kind": "llm"}, kb_id=kb_id):
        expected_profile = model_connections.embedding_profile(user.id, kb_id, db)
        pre_idx_profile = db.scalar(
            sa.select(IndexVersion.embedding_profile)
            .select_from(Material)
            .join(
                MaterialVersion,
                sa.and_(
                    MaterialVersion.material_id == Material.id,
                    MaterialVersion.is_current.is_(True),
                ),
            )
            .join(
                IndexVersion,
                sa.and_(
                    IndexVersion.material_version_id == MaterialVersion.id,
                    IndexVersion.is_active.is_(True),
                    IndexVersion.status == "ready",
                ),
            )
            .join(MaterialChunk, MaterialChunk.index_version_id == IndexVersion.id)
            .where(
                Material.knowledge_base_id == kb_id,
                Material.owner_user_id == user.id,
                Material.deleted_at.is_(None),
                Material.status == "active",
                MaterialChunk.id == chunk_id,
            )
        )
        if pre_idx_profile is None:
            raise ApiError(409, "index_changed", "片段索引已更新，请刷新后重试。")
        if pre_idx_profile != expected_profile:
            raise ApiError(409, "embedding_profile_mismatch", "资料向量配置已变更，请重新索引")

        vectors = embed_texts(
            [cleaned],
            context=ModelUsageContext(user.id, "embedding"),
        )
        if len(vectors) != 1 or len(vectors[0]) != 1024:
            raise ApiError(502, "embedding_invalid", "向量模型返回维度或内容不正确")
        vector = vectors[0]

        kb = owned_kb(db, user, kb_id, lock=True)
        phase = (
            sa.select(AiJob.status)
            .where(AiJob.job_type == "ingest", AiJob.resource_id == MaterialVersion.id)
            .order_by(AiJob.created_at.desc(), AiJob.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        row = db.execute(
            sa.select(
                MaterialChunk,
                Material.id,
                Material.title,
                MaterialVersion.source_mime,
                IndexVersion.ready_at,
                IndexVersion.embedding_profile,
                sa.func.coalesce(phase, "succeeded"),
            )
            .select_from(Material)
            .join(
                MaterialVersion,
                sa.and_(
                    MaterialVersion.material_id == Material.id,
                    MaterialVersion.is_current.is_(True),
                ),
            )
            .join(
                IndexVersion,
                sa.and_(
                    IndexVersion.material_version_id == MaterialVersion.id,
                    IndexVersion.is_active.is_(True),
                    IndexVersion.status == "ready",
                ),
            )
            .join(MaterialChunk, MaterialChunk.index_version_id == IndexVersion.id)
            .where(
                Material.knowledge_base_id == kb.id,
                Material.owner_user_id == kb.owner_user_id,
                Material.deleted_at.is_(None),
                Material.status == "active",
                MaterialChunk.id == chunk_id,
            )
            .with_for_update(of=MaterialChunk)
        ).first()
        if row is None:
            raise ApiError(409, "index_changed", "片段索引已更新，请刷新后重试。")

        chunk, mat_id, title, mime, ready_at, idx_profile, proc_status = row
        profile = model_connections.embedding_profile(user.id, kb_id, db)
        if idx_profile != profile:
            raise ApiError(409, "embedding_profile_mismatch", "资料向量配置已变更，请重新索引")
        digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()
        db.execute(
            sa.update(MaterialChunk)
            .where(MaterialChunk.id == chunk.id)
            .values(
                text=cleaned,
                text_sha256=digest,
                **lexical_column_values(cleaned),
            )
        )
        updated_emb = db.execute(
            sa.update(ChunkEmbedding)
            .where(
                ChunkEmbedding.chunk_id == chunk.id,
                ChunkEmbedding.model_profile_id == profile,
            )
            .values(embedding=vector)
        )
        if updated_emb.rowcount == 0:
            db.add(ChunkEmbedding(chunk_id=chunk.id, model_profile_id=profile, embedding=vector))
        db.commit()

        pub_loc = _public_locator(chunk.locator)
        return {
            "id": chunk.id,
            "text": cleaned,
            "material_id": mat_id,
            "title": title,
            "mime_type": mime,
            "version_id": chunk.material_version_id,
            "locator": pub_loc,
            "estimated_tokens": _estimated_tokens(cleaned),
            "retrieval_enabled": chunk.retrieval_enabled,
            "status": proc_status,
            "ready_at": ready_at,
        }
