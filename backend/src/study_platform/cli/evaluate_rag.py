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
from uuid import uuid4

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
from study_platform.services import ingestion
from study_platform.services.lexical import lexical_column_values
from study_platform.services.models import embed_texts
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


def test_database_url(raw):
    if not raw:
        raise ValueError("Configure TEST_DATABASE_URL for a disposable PostgreSQL test database")
    url = make_url(raw)
    if (
        url.get_backend_name() != "postgresql"
        or not (url.database or "").endswith("_test")
        or {"dbname", "service", "servicefile"} & set(url.query)
    ):
        raise ValueError(
            "TEST_DATABASE_URL must name a PostgreSQL *_test database without target overrides"
        )
    return url.set(drivername="postgresql+psycopg")


@contextmanager
def isolated_database(output):
    url = test_database_url(os.getenv("TEST_DATABASE_URL"))
    name = f"study_eval_{uuid4().hex}_test"
    admin = sa.create_engine(
        url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )
    created, factory = False, None
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
            if created:
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


def seed_corpus(factory, materials):
    settings = get_settings()
    owner = uuid4()
    scopes, documents, hashes, embedding_metrics, eval_owner = {}, {}, {}, [], owner
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
                (part, locator) for text, locator in extracted for part in ingestion.chunks(text)
            ]
            if not parts:
                raise ValueError("Evaluation source produced no text")
            vectors = []
            for offset in range(0, len(parts), 20):
                metrics = {"document": item["id"]}
                vectors.extend(
                    embed_texts(
                        [text for text, _ in parts[offset : offset + 20]], diagnostics=metrics
                    )
                )
                embedding_metrics.append(metrics)
            index = IndexVersion(
                id=uuid4(),
                material_version_id=version.id,
                generation=1,
                pipeline_profile="document-v1",
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
            print(f"Indexed evaluation document {item['id']}", flush=True)
    return scopes, documents, hashes, embedding_metrics, eval_owner


def write_json(path, value):
    temporary = path.with_suffix(f".{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


async def live_evaluation(args, cases, materials, output):
    with isolated_database(output) as factory:
        settings = get_settings()
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
            "min_similarity": settings.retrieval_min_similarity,
            "cases": [],
        }
        write_json(output / "report.json", report)
        scopes, documents, hashes, corpus_embedding, eval_owner = seed_corpus(factory, materials)
        report.update(source_hashes=hashes, corpus_embedding=corpus_embedding)
        for number, case in enumerate(cases, 1):
            report["cases"].extend(
                await evaluate_case(
                    case,
                    scopes[case["knowledge_base"]],
                    documents,
                    args.strategy,
                    eval_owner,
                    k_values=args.metric_k,
                    retrieval_only=args.retrieval_only,
                )
            )
            write_json(output / "report.json", report)
            print(f"Evaluated {number}/{len(cases)}: {case['id']}", flush=True)
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
    print(f"Evaluation complete; isolated database removed. Review report: {output}")


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
        output = (args.output or BACKEND.parent / ".local/rag-evaluation" / uuid4().hex).resolve()
        output.mkdir(parents=True, exist_ok=False)
        asyncio.run(live_evaluation(args, cases, materials, output))
    except ValueError as error:
        raise SystemExit(str(error)) from None
    except FileExistsError:
        raise SystemExit(
            "Choose a new --output directory; existing evaluation runs are never overwritten"
        ) from None
    except Exception as error:
        # Do not print SQL parameters, credentials, prompts or private document text.
        raise SystemExit(
            f"Evaluation failed ({type(error).__name__}); check test DB, model and source configuration"
        ) from None


if __name__ == "__main__":
    main()
