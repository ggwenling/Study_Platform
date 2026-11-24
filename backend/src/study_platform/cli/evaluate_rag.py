"""Explicit live evaluation in a newly created disposable database, never the application DB."""

import argparse
import asyncio
import hashlib
import json
import os
import shutil
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic.config import Config
from dotenv import load_dotenv
from sqlalchemy.engine import make_url

from alembic import command
from study_platform.core.config import get_settings
from study_platform.persistence.database import session_factory
from study_platform.persistence.models import (
    ChunkEmbedding,
    IndexVersion,
    KnowledgeBase,
    Material,
    MaterialChunk,
    MaterialVersion,
    User,
)
from study_platform.services import chunking, ingestion
from study_platform.services.eval_embedding_resume import (
    CacheError,
    CorpusEmbedError,
    embed_document_batches,
    prepare_embedding_cache,
    query_costs,
    summarize_corpus_usage,
)
from study_platform.services.eval_index_reuse import (
    IndexReuseError,
    assert_database_access,
    assert_dataset_matches,
    build_manifest,
    code_schema_revision,
    import_index,
    load_index_package,
    new_database_name,
    read_export_records,
    read_only_retrieval,
    read_snapshot,
    retained_database,
    scopes_by_name,
    should_drop_database,
    test_database_url,
    validate_records,
    verify_index_contents,
    verify_retained_identity,
    write_package,
)
from study_platform.services.lexical import lexical_column_values
from study_platform.services.model_failure import classify_local_exception, public_failure
from study_platform.services.models import describe_model_failure, embed_texts
from study_platform.services.rag_evaluation import (
    DEFAULT_METRIC_K,
    evaluate_case,
    format_retrieval_table,
    quality_gate,
    read_dataset,
    review_template,
    summarize,
    summarize_retrieval,
)
from study_platform.services.storage import source_path, version_dir

BACKEND = Path(__file__).resolve().parents[3]


@contextmanager
def isolated_database(output, *, retain=False):
    url = test_database_url(os.getenv("TEST_DATABASE_URL"))
    name = new_database_name()
    admin = sa.create_engine(
        url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )
    created, factory = False, None
    quoted = None
    succeeded = False
    previous = {
        key: os.environ.get(key)
        for key in ("DATABASE_URL", "MIGRATION_DATABASE_URL", "STORAGE_DIR")
    }
    try:
        with admin.connect() as connection:
            if connection.scalar(sa.text("SELECT current_database()")) != "postgres":
                raise ValueError("Administrative connection selected the wrong database")
            if not connection.scalar(
                sa.text("SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname=current_user")
            ):
                raise ValueError("Test database role requires CREATEDB for isolated evaluation")
            quoted = connection.dialect.identifier_preparer.quote(name)
            connection.exec_driver_sql(f"CREATE DATABASE {quoted}")
            created = True
        target = url.set(database=name).render_as_string(hide_password=False)
        os.environ.update(
            DATABASE_URL=target, MIGRATION_DATABASE_URL=target, STORAGE_DIR=str(output / "storage")
        )
        get_settings.cache_clear()
        command.upgrade(Config(str(BACKEND / "alembic.ini")), "head")
        factory = session_factory()
        with factory() as db:
            if db.scalar(sa.text("SELECT current_database()")) != name:
                raise ValueError("Evaluation connection selected the wrong database")
        yield factory
        succeeded = True
    finally:
        if factory:
            factory.kw["bind"].dispose()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()
        try:
            if should_drop_database(created=created, retain=retain, succeeded=succeeded) and quoted:
                with admin.connect() as connection:
                    connection.exec_driver_sql(f"DROP DATABASE {quoted}")
        finally:
            admin.dispose()


def render_source(material, target):
    if material.get("path"):
        path = Path(material["path"])
        if getattr(path.stat(), "st_file_attributes", 0) & 0x1000:
            raise ValueError("Cloud-only material must be made locally available explicitly")
        shutil.copyfile(path, target)
        return
    text = "\n".join(material["sections"])
    if material["format"] == "txt":
        target.write_text(text, encoding="utf-8")
    elif material["format"] == "docx":
        from docx import Document

        doc = Document()
        for section in material["sections"]:
            doc.add_paragraph(section)
        doc.save(target)
    else:
        from PIL import Image, ImageDraw, ImageFont

        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 34)
        image = Image.new("RGB", (1800, max(240, 90 * len(material["sections"]))), "white")
        draw = ImageDraw.Draw(image)
        for line, section in enumerate(material["sections"]):
            draw.text((30, 30 + line * 90), section, fill="black", font=font)
        formats = {"png": "PNG", "jpeg": "JPEG", "pdf": "PDF"}
        image.save(target, format=formats[material["format"]])


def seed_corpus(factory, materials, *, cache_dir=None):
    settings = get_settings()
    owner = uuid4()
    scopes, documents, hashes, embedding_metrics, indexed_documents, eval_owner = (
        {},
        {},
        {},
        [],
        [],
        owner,
    )
    with factory.begin() as db:
        db.add(
            User(
                id=owner,
                email=f"{owner}@example.test",
                password_hash="unused",
                display_name="RAG evaluation",
            )
        )
        db.flush()
        for key in sorted({item["knowledge_base"] for item in materials}):
            kb = KnowledgeBase(id=uuid4(), owner_user_id=owner, name=key)
            db.add(kb)
            db.flush()
            scopes[key] = {
                "knowledge_base_id": str(kb.id),
                "knowledge_base_name": key,
                "index_version_ids": [],
                "intent": "qa",
                "answer_mode": "knowledge_only",
            }
        for item in materials:
            material = Material(
                id=uuid4(),
                owner_user_id=owner,
                knowledge_base_id=scopes[item["knowledge_base"]]["knowledge_base_id"],
                title=item["title"],
                kind="notes",
            )
            version = MaterialVersion(
                id=uuid4(),
                material_id=material.id,
                version_no=1,
                is_current=True,
                source_mime={
                    "txt": "text/plain",
                    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "png": "image/png",
                    "jpeg": "image/jpeg",
                    "pdf": "application/pdf",
                }[item["format"]],
                source_sha256="0" * 64,
                byte_size=1,
                object_key=str(uuid4()),
                parse_status="ready",
                parser_profile="document-v1",
            )
            path = source_path(version.id)
            path.parent.mkdir(parents=True)
            render_source(item, path)
            if path.stat().st_size > settings.max_upload_bytes:
                raise ValueError("Evaluation material exceeds the existing upload limit")
            hashes[item["id"]] = hashlib.sha256(path.read_bytes()).hexdigest()
            version.source_sha256, version.byte_size = hashes[item["id"]], path.stat().st_size
            db.add(material)
            db.flush()
            db.add(version)
            db.flush()
            extracted = list(ingestion.extract(version, version_dir(version.id), lambda _: None))
            parts = [
                (part, locator)
                for text, locator in extracted
                for part in chunking.semantic_chunks(text)
            ]
            if not parts:
                raise ValueError("Evaluation source produced no text")
            try:
                vectors, batch_metrics = embed_document_batches(
                    [text for text, _ in parts],
                    source_sha256=hashes[item["id"]],
                    pipeline_profile=chunking.SEMANTIC_PROFILE,
                    document_id=item["id"],
                    cache_dir=cache_dir,
                    embedding_model=settings.embedding_model,
                    embedding_base_url=settings.embedding_base_url,
                    embedding_dim=1024,
                    embed=embed_texts,
                )
            except CorpusEmbedError as exc:
                embedding_metrics.extend(exc.metrics)
                exc.metrics = list(embedding_metrics)
                exc.indexed_documents = list(indexed_documents)
                raise
            embedding_metrics.extend(batch_metrics)
            index = IndexVersion(
                id=uuid4(),
                material_version_id=version.id,
                generation=1,
                pipeline_profile=chunking.SEMANTIC_PROFILE,
                embedding_profile=f"{settings.embedding_model}:1024",
                embedding_dim=1024,
                status="ready",
                is_active=True,
                chunk_count=len(parts),
                ready_at=datetime.now(timezone.utc),
            )
            db.add(index)
            db.flush()
            for ordinal, ((text, locator), vector) in enumerate(zip(parts, vectors, strict=True)):
                chunk = MaterialChunk(
                    id=uuid4(),
                    index_version_id=index.id,
                    material_version_id=version.id,
                    ordinal=ordinal,
                    text=text,
                    text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                    locator=locator,
                    **lexical_column_values(text),
                )
                db.add(chunk)
                db.flush()
                db.add(
                    ChunkEmbedding(
                        chunk_id=chunk.id,
                        model_profile_id=f"{settings.embedding_model}:1024",
                        embedding=vector,
                    )
                )
            scopes[item["knowledge_base"]]["index_version_ids"].append(str(index.id))
            documents[str(version.id)] = item["id"]
            indexed_documents.append(item["id"])
            print(f"Indexed evaluation document {item['id']}", flush=True)
    # One transaction: a later failure rolls every document back. The print is not
    # a retained index, and a copied source file is not an embedded batch.
    return scopes, documents, hashes, embedding_metrics, eval_owner, indexed_documents


def write_json(path, value):
    temporary = path.with_suffix(f".{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _strategy_ready(strategy):
    if strategy in {"hybrid", "hybrid_compare"} and get_settings().retrieval_lexical_weight <= 0:
        raise ValueError(
            "hybrid strategies require RETRIEVAL_LEXICAL_WEIGHT > 0; "
            "the default 0 does not add a lexical branch"
        )


def _application_database():
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        return None
    try:
        return make_url(raw).database
    except Exception:
        return None


def _shared_test_database():
    return test_database_url(os.getenv("TEST_DATABASE_URL")).database


def _base_report(args, cases):
    settings = get_settings()
    materials_path = args.dataset.with_name("materials.json")
    report = {
        "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        "expected_case_ids": [case["id"] for case in cases],
        "strategy": args.strategy,
        "status": "running",
        "retrieval_only": args.retrieval_only,
        "metric_k": list(args.metric_k),
        "chat_model": None if args.retrieval_only else settings.chat_model,
        "embedding_model": settings.embedding_model,
        "dimensions": 1024,
        "top_k": settings.retrieval_top_k,
        "candidate_k": settings.retrieval_candidate_k,
        "candidate_limit": max(settings.retrieval_candidate_k, settings.retrieval_top_k),
        "lexical_weight": settings.retrieval_lexical_weight,
        "min_similarity": settings.retrieval_min_similarity,
        "cases": [],
    }
    if materials_path.is_file():
        report["materials_sha256"] = hashlib.sha256(materials_path.read_bytes()).hexdigest()
    return report


async def _run_cases(args, cases, output, report, scopes, documents, owner):
    for number, case in enumerate(cases, 1):
        report["cases"].extend(
            await evaluate_case(
                case,
                scopes[case["knowledge_base"]],
                documents,
                args.strategy,
                owner,
                k_values=args.metric_k,
                retrieval_only=args.retrieval_only,
            )
        )
        write_json(output / "report.json", report)
        print(f"Evaluated {number}/{len(cases)}: {case['id']}", flush=True)


def evaluation_failure_text(failure):
    parts = ["Evaluation failed"]
    for key in (
        "stage",
        "exception_class",
        "failure_kind",
        "http_status",
        "error_code",
        "mapped_error_code",
        "provider_code",
        "request_id",
        "attempts",
    ):
        value = failure.get(key) if isinstance(failure, dict) else None
        if value is not None:
            parts.append(f"{key}={value}")
    return " ".join(parts)


class EvaluationFailed(Exception):
    def __init__(self, failure):
        self.failure = failure if isinstance(failure, dict) else {}
        super().__init__(evaluation_failure_text(self.failure))


_REUSE_REASONS = {
    "schema revision": "schema_revision",
    "dataset hash": "dataset_hash",
    "materials file hash": "materials_hash",
    "embedding model": "embedding_model",
    "embedding dimension": "embedding_dimension",
}


def _local_failure(exc):
    """Local and verification failures stay out of the provider error mapping."""
    if isinstance(exc, IndexReuseError):
        return public_failure(
            {
                "exception_class": "IndexReuseError",
                "error_code": _REUSE_REASONS.get(str(exc), "local_verification"),
                "failure_kind": "local_verification",
                "mapped_error_code": None,
                "usage_status": "unknown",
                "retryable": False,
            }
        )
    local = classify_local_exception(exc)
    if local is None:
        return None
    return public_failure(local)


def preflight_evaluation_inputs(args, materials):
    """Read local inputs that can fail before any corpus embedding is purchased."""
    dataset = Path(args.dataset)
    _read_local_file(dataset)
    if bool(getattr(args, "retain_index", False)) or getattr(args, "export_index", None):
        json.loads(_read_local_file(dataset.with_name("materials.json")).decode("utf-8"))
    export_path = getattr(args, "export_index", None)
    if export_path is not None:
        parent = Path(export_path).parent
        if not parent.is_dir():
            raise FileNotFoundError(parent)
        if not os.access(parent, os.W_OK):
            raise PermissionError(parent)
    for item in materials or []:
        raw = item.get("path") if isinstance(item, dict) else None
        if raw:
            _read_local_file(raw)


def _read_local_file(path):
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(target)
    return target.read_bytes()


def _fail_before_database(output, args, cases, exc, stage):
    output.mkdir(parents=True, exist_ok=True)
    try:
        report = _base_report(args, cases)
    except OSError:
        report = {
            "status": "running",
            "cases": [],
            "strategy": getattr(args, "strategy", None),
            "retrieval_only": bool(getattr(args, "retrieval_only", False)),
            "metric_k": list(getattr(args, "metric_k", [])),
        }
    report["corpus_embedding"] = []
    report["indexed_documents"] = []
    report["index_retained"] = False
    failure = _local_failure(exc) or describe_model_failure(exc)
    _mark_failed(output, report, failure, stage=stage)
    raise EvaluationFailed(report["failure"]) from None


def _mark_failed(output, report, failure, *, stage):
    body = dict(failure) if isinstance(failure, dict) else {}
    body["stage"] = stage
    report["status"] = "failed"
    report["index_retained"] = False
    report["failure"] = public_failure(body)
    report["corpus_usage"] = summarize_corpus_usage(report.get("corpus_embedding") or [])
    write_json(output / "report.json", report)
    return report["failure"]


def _finish(args, output, report, cleanup_message):
    report["status"] = "completed"
    write_json(output / "report.json", report)
    if not args.retrieval_only:
        write_json(output / "review.json", review_template(report))
        write_json(output / "gate.json", quality_gate(report))
    retrieval = summarize_retrieval(report, args.metric_k)
    if retrieval:
        write_json(output / "retrieval_metrics.json", retrieval)
    summary = summarize(report)
    write_json(output / "summary.json", summary)
    if retrieval:
        if args.metrics_format == "table":
            print(format_retrieval_table(retrieval), flush=True)
        else:
            print(json.dumps(retrieval, ensure_ascii=False, indent=2))
    print(cleanup_message, flush=True)


def _publish_index(
    factory, args, output, scopes, documents, hashes, *, retain, export_path, application_database, shared
):
    snapshot = read_snapshot(factory)
    if snapshot["schema_revision"] != code_schema_revision():
        raise IndexReuseError("schema revision")
    assert_database_access(
        snapshot, application_database=application_database, shared_test_database=shared
    )
    manifest = build_manifest(
        snapshot=snapshot,
        scopes=scopes,
        documents=documents,
        material_hashes=hashes,
        dataset_sha256=hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        materials_sha256=hashlib.sha256(args.dataset.with_name("materials.json").read_bytes()).hexdigest(),
        embedding_model=get_settings().embedding_model,
    )
    if export_path:
        records = read_export_records(factory, manifest)
        exported = dict(manifest, binding="export")
        validate_records(exported, records)
        write_package(export_path, exported, records)
    if retain:
        write_json(output / "index_manifest.json", dict(manifest, binding="database"))
    return snapshot["database_name"]


async def _reuse_live(args, cases, output, reuse_path, retain, export_path):
    try:
        preflight_evaluation_inputs(args, [])
        manifest, records = load_index_package(reuse_path)
        assert_dataset_matches(manifest, args.dataset)
        if manifest["schema_revision"] != code_schema_revision():
            raise IndexReuseError("schema revision")
        if manifest["embedding_model"] != get_settings().embedding_model:
            raise IndexReuseError("embedding model")
        if manifest["embedding_dim"] != 1024:
            raise IndexReuseError("embedding dimension")
    except EvaluationFailed:
        raise
    except Exception as exc:
        _fail_before_database(output, args, cases, exc, "local_input")
    settings = get_settings()
    application_database = _application_database()
    shared = _shared_test_database()
    report = _base_report(args, cases)
    report["corpus_embedding"] = []
    report["indexed_documents"] = []
    report["index_reuse"] = {"corpus_embedding_calls": 0, "binding": manifest["binding"]}
    write_json(output / "report.json", report)
    try:
        if records is None:
            with retained_database(
                manifest, application_database=application_database, shared_test_database=shared
            ) as factory:
                snapshot = read_snapshot(factory)
                assert_database_access(
                    snapshot, application_database=application_database, shared_test_database=shared
                )
                verify_retained_identity(manifest, snapshot)
                verify_index_contents(
                    manifest,
                    snapshot,
                    embedding_model=settings.embedding_model,
                    schema_revision=code_schema_revision(),
                )
                await _run_cases(
                    args,
                    cases,
                    output,
                    report,
                    scopes_by_name(manifest["scopes"]),
                    manifest["documents"],
                    UUID(manifest["eval_owner_id"]),
                )
                if export_path:
                    exported_records = read_export_records(factory, manifest)
                    exported = dict(manifest, binding="export")
                    validate_records(exported, exported_records)
                    write_package(export_path, exported, exported_records)
                report["index_reuse"]["database_name"] = snapshot["database_name"]
            message = (
                f"Evaluation complete; reused retained index {manifest['database_name']}. "
                f"Corpus embedding skipped. Review report: {output}"
            )
        else:
            with isolated_database(output, retain=retain) as factory:
                import_index(factory, manifest, records)
                snapshot = read_snapshot(factory)
                if snapshot["schema_revision"] != code_schema_revision():
                    raise IndexReuseError("schema revision")
                assert_database_access(
                    snapshot, application_database=application_database, shared_test_database=shared
                )
                verify_index_contents(
                    manifest,
                    snapshot,
                    embedding_model=settings.embedding_model,
                    schema_revision=code_schema_revision(),
                )
                with read_only_retrieval(os.environ["DATABASE_URL"]):
                    await _run_cases(
                        args,
                        cases,
                        output,
                        report,
                        scopes_by_name(manifest["scopes"]),
                        manifest["documents"],
                        UUID(manifest["eval_owner_id"]),
                    )
                if retain or export_path:
                    _publish_index(
                        factory,
                        args,
                        output,
                        scopes_by_name(manifest["scopes"]),
                        manifest["documents"],
                        manifest["material_hashes"],
                        retain=retain,
                        export_path=export_path,
                        application_database=application_database,
                        shared=shared,
                    )
                report["index_reuse"]["database_name"] = snapshot["database_name"]
            message = (
                "Evaluation complete; imported index without corpus embedding"
                + (" and retained it." if retain else "; imported database removed.")
                + f" Review report: {output}"
            )
        report["indexed_documents"] = []
        report["index_retained"] = False
        report["query_costs"] = query_costs(report["cases"])
        report["corpus_usage"] = summarize_corpus_usage(report.get("corpus_embedding") or [])
        _finish(args, output, report, message)
    except EvaluationFailed:
        raise
    except Exception as exc:
        local = _local_failure(exc)
        failure = local or (exc.failure if isinstance(exc, CorpusEmbedError) else describe_model_failure(exc))
        report["indexed_documents"] = []
        report["index_retained"] = False
        stage = "local_input" if local else "reuse"
        _mark_failed(output, report, failure, stage=stage)
        raise EvaluationFailed(report["failure"]) from None


async def live_evaluation(args, cases, materials, output):
    reuse_path = getattr(args, "reuse_index", None)
    retain = bool(getattr(args, "retain_index", False))
    export_path = getattr(args, "export_index", None)
    cache_dir = getattr(args, "embedding_cache", None)
    _strategy_ready(args.strategy)
    if reuse_path:
        await _reuse_live(args, cases, output, reuse_path, retain, export_path)
        return
    try:
        preflight_evaluation_inputs(args, materials)
    except Exception as exc:
        _fail_before_database(output, args, cases, exc, "local_input")
    if cache_dir is not None:
        try:
            prepare_embedding_cache(cache_dir)
        except CacheError as exc:
            report = _base_report(args, cases)
            report["corpus_embedding"] = []
            report["indexed_documents"] = []
            report["index_retained"] = False
            _mark_failed(output, report, exc.failure, stage="embedding_cache")
            raise EvaluationFailed(report["failure"]) from None
    application_database = _application_database() if retain or export_path else None
    shared = _shared_test_database() if retain or export_path else None
    with isolated_database(output, retain=retain) as factory:
        report = _base_report(args, cases)
        write_json(output / "report.json", report)
        try:
            scopes, documents, hashes, corpus_embedding, eval_owner, indexed = seed_corpus(
                factory, materials, cache_dir=cache_dir
            )
            report.update(
                source_hashes=hashes,
                corpus_embedding=corpus_embedding,
                indexed_documents=indexed,
                corpus_usage=summarize_corpus_usage(corpus_embedding),
            )
            await _run_cases(args, cases, output, report, scopes, documents, eval_owner)
            report["query_costs"] = query_costs(report["cases"])
            if retain or export_path:
                _publish_index(
                    factory,
                    args,
                    output,
                    scopes,
                    documents,
                    hashes,
                    retain=retain,
                    export_path=export_path,
                    application_database=application_database,
                    shared=shared,
                )
        except EvaluationFailed:
            raise
        except CorpusEmbedError as exc:
            report["corpus_embedding"] = list(exc.metrics)
            # The seed transaction rolls back, so these documents are not a retained index.
            report["indexed_documents"] = []
            report["index_retained"] = False
            _mark_failed(output, report, exc.failure, stage="corpus_embedding")
            raise EvaluationFailed(report["failure"]) from None
        except Exception as exc:
            local = _local_failure(exc)
            report["indexed_documents"] = []
            report["index_retained"] = False
            if local:
                stage = "index_publish" if report.get("corpus_embedding") else "local_input"
                failure = local
            else:
                stage = "evaluation"
                failure = describe_model_failure(exc)
            _mark_failed(output, report, failure, stage=stage)
            raise EvaluationFailed(report["failure"]) from None
    report["index_retained"] = bool(retain)
    report["query_costs"] = query_costs(report["cases"])
    report["corpus_usage"] = summarize_corpus_usage(report.get("corpus_embedding") or [])
    cleanup = (
        f"Evaluation complete; isolated database retained. Review report: {output}"
        if retain
        else f"Evaluation complete; isolated database removed. Review report: {output}"
    )
    _finish(args, output, report, cleanup)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=BACKEND / "evaluation/facts.jsonl")
    parser.add_argument(
        "--strategy",
        choices=("baseline", "rewrite", "compare", "hybrid", "hybrid_compare"),
        default="compare",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--live", action="store_true", help="explicitly allow paid provider calls in a new test DB"
    )
    parser.add_argument(
        "--retrieval-only",
        action="store_true",
        help="retrieve and score IR metrics only; skip chat-model answers",
    )
    parser.add_argument(
        "--metric-k",
        type=int,
        nargs="+",
        default=list(DEFAULT_METRIC_K),
        metavar="K",
        help="cutoff list for Recall@k, nDCG@k and Hit@k (default: 1 3 6 10)",
    )
    parser.add_argument(
        "--metrics-format",
        choices=("json", "table"),
        default="table",
        help="stdout format for aggregated retrieval metrics after a live run",
    )
    parser.add_argument(
        "--review",
        type=Path,
        help="recompute the gate from an existing report and human review; no model calls",
    )
    parser.add_argument(
        "--retain-index",
        action="store_true",
        help="after a successful --live build, keep the study_eval_*_test database and write index_manifest.json",
    )
    parser.add_argument(
        "--export-index",
        type=Path,
        help="write a portable index package so a later run can import it without embedding",
    )
    parser.add_argument(
        "--reuse-index",
        type=Path,
        help=(
            "reuse a retained manifest or an export package; skips corpus embedding and does not "
            "write that index. Omit --retrieval-only to generate answers"
        ),
    )
    parser.add_argument(
        "--embedding-cache",
        type=Path,
        help="opt-in private directory for resumable corpus batches; vectors are sensitive and local",
    )
    args = parser.parse_args()
    try:
        if args.review:
            if not args.output:
                raise ValueError(
                    "--review requires --output pointing to the existing report directory"
                )
            report = json.loads((args.output / "report.json").read_text(encoding="utf-8"))
            review = json.loads(args.review.read_text(encoding="utf-8"))
            gate = quality_gate(report, review)
            write_json(args.output / "gate.json", gate)
            write_json(args.output / "summary.json", summarize(report))
            print(json.dumps(gate, ensure_ascii=False))
            return
        if args.reuse_index and not args.live:
            manifest, _records = load_index_package(args.reuse_index)
            chunks = sum(row["chunk_count"] for row in manifest["indexes"])
            print(
                f"Validated index package {manifest['binding']} / {manifest['embedding_profile']} "
                f"/ {chunks} chunks. No database or provider calls."
            )
            return
        if (args.retain_index or args.export_index or args.embedding_cache) and not args.live:
            raise ValueError("--retain-index, --export-index and --embedding-cache require --live")
        if any(k <= 0 for k in args.metric_k):
            raise ValueError("--metric-k values must be positive integers")
        args.metric_k = sorted(set(args.metric_k))
        cases, materials = read_dataset(args.dataset)
        if not args.live:
            print(
                f"Validated {len(cases)} questions / {len({c['fact_group'] for c in cases})} fact groups. No database or provider calls; use --live to evaluate."
            )
            return
        load_dotenv(get_settings().model_config["env_file"])
        _strategy_ready(args.strategy)
        output = (args.output or BACKEND.parent / ".local/rag-evaluation" / uuid4().hex).resolve()
        output.mkdir(parents=True, exist_ok=False)
        asyncio.run(live_evaluation(args, cases, materials, output))
    except ValueError as error:
        raise SystemExit(str(error)) from None
    except FileExistsError:
        raise SystemExit(
            "Choose a new --output directory; existing evaluation runs are never overwritten"
        ) from None
    except EvaluationFailed as error:
        raise SystemExit(evaluation_failure_text(error.failure)) from None
    except Exception as error:
        # Do not print SQL parameters, credentials, prompts or private document text.
        raise SystemExit(
            f"Evaluation failed ({type(error).__name__}); check test DB, model and source configuration"
        ) from None


if __name__ == "__main__":
    main()
