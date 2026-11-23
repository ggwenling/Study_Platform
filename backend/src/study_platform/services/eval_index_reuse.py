"""Opt-in retention, export and reuse for isolated evaluation indexes.

The default evaluation database is still dropped. Retention and export happen
only when the caller asks. Reuse refuses any database that is not a
``study_eval_<32 hex>_test`` index owned by the current role, and it refuses a
snapshot whose owner, knowledge base, source hash, index version, schema
revision, embedding model or dimension disagrees with the manifest.
"""

import hashlib
import json
import os
import re
import shutil
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from study_platform.core.config import get_settings
from study_platform.persistence.models import (
    ChunkEmbedding,
    IndexVersion,
    KnowledgeBase,
    Material,
    MaterialChunk,
    MaterialVersion,
    User,
)
from study_platform.services.chunking import SEMANTIC_PROFILE
from study_platform.services.lexical import lexical_column_values

MANIFEST_VERSION = 1
MANIFEST_KIND = "study_eval_index"
EMBEDDING_DIM = 1024
DB_NAME_RE = re.compile(r"^study_eval_[0-9a-f]{32}_test$")
ROLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,62}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
READ_ONLY_OPTIONS = "-c timezone=UTC -c default_transaction_read_only=on"
FORBIDDEN_KEYS = frozenset(
    {"password", "api_key", "database_url", "authorization", "secret", "token", "access_key"}
)
BACKEND = Path(__file__).resolve().parents[3]


class IndexReuseError(ValueError):
    """A reuse request failed a safety check. The message is a short reason code."""


def new_database_name():
    return f"study_eval_{uuid4().hex}_test"


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


def assert_database_name(name):
    if not isinstance(name, str) or DB_NAME_RE.fullmatch(name) is None:
        raise IndexReuseError("database name")


def should_drop_database(*, created, retain, succeeded):
    """Drop unless this process created the database, the run succeeded, and retention was requested."""
    return bool(created) and not (retain and succeeded)


def code_schema_revision():
    script = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))
    heads = script.get_heads()
    if len(heads) != 1:
        raise IndexReuseError("schema revision")
    return heads[0]


def reuse_target_url(raw_test_url, database_name):
    """Build a connection URL for one allowlisted evaluation database. Manifests never carry a DSN."""
    assert_database_name(database_name)
    return test_database_url(raw_test_url).set(database=database_name)


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def assert_dataset_matches(manifest, dataset):
    dataset = Path(dataset)
    if file_sha256(dataset) != manifest["dataset_sha256"]:
        raise IndexReuseError("dataset hash")
    if file_sha256(dataset.with_name("materials.json")) != manifest["materials_sha256"]:
        raise IndexReuseError("materials file hash")


def _uuid(value, reason):
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError):
        raise IndexReuseError(reason) from None


def _sha256(value, reason):
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise IndexReuseError(reason)
    return value


def _role(value):
    if not isinstance(value, str) or ROLE_RE.fullmatch(value) is None:
        raise IndexReuseError("database owner")
    return value


def reject_secret_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in FORBIDDEN_KEYS:
                raise IndexReuseError("forbidden credential field")
            reject_secret_keys(item)
    elif isinstance(value, list):
        for item in value:
            reject_secret_keys(item)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def vector_to_list(value):
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            raise IndexReuseError("embedding dimension") from None
    if not isinstance(value, (list, tuple)):
        raise IndexReuseError("embedding dimension")
    if len(value) != EMBEDDING_DIM or any(not _is_number(item) for item in value):
        raise IndexReuseError("embedding dimension")
    try:
        numbers = [float(item) for item in value]
    except (TypeError, ValueError):
        raise IndexReuseError("embedding dimension") from None
    if any(number != number or number in (float("inf"), float("-inf")) for number in numbers):
        raise IndexReuseError("embedding dimension")
    return numbers


def assert_database_access(snapshot, *, application_database, shared_test_database):
    name = snapshot.get("database_name")
    assert_database_name(name)
    if application_database and name == application_database:
        raise IndexReuseError("application database")
    if shared_test_database and name == shared_test_database:
        raise IndexReuseError("shared test database")
    _role(snapshot.get("database_owner_role"))
    if snapshot.get("current_user") != snapshot.get("database_owner_role"):
        raise IndexReuseError("database owner")


def verify_retained_identity(manifest, snapshot):
    if manifest.get("binding") != "database":
        raise IndexReuseError("database binding")
    if snapshot.get("database_name") != manifest.get("database_name"):
        raise IndexReuseError("database name")
    if snapshot.get("database_owner_role") != manifest.get("database_owner_role"):
        raise IndexReuseError("database owner")


def _derived_scopes(snapshot):
    names = {row["id"]: row["name"] for row in snapshot["knowledge_bases"]}
    scopes = {}
    versions = {row["version_id"]: row for row in snapshot["materials"]}
    for index in snapshot["indexes"]:
        material = versions[index["material_version_id"]]
        name = names[material["knowledge_base_id"]]
        scope = scopes.setdefault(
            name,
            {
                "knowledge_base_name": name,
                "knowledge_base_id": material["knowledge_base_id"],
                "index_version_ids": [],
                "intent": "qa",
                "answer_mode": "knowledge_only",
            },
        )
        scope["index_version_ids"].append(index["id"])
    for scope in scopes.values():
        scope["index_version_ids"] = sorted(scope["index_version_ids"])
    return [scopes[name] for name in sorted(scopes)]


def _scope_rows(scopes):
    if isinstance(scopes, dict):
        rows = []
        for name, scope in scopes.items():
            rows.append(
                {
                    "knowledge_base_name": name,
                    "knowledge_base_id": _uuid(scope["knowledge_base_id"], "knowledge base"),
                    "index_version_ids": sorted(
                        _uuid(item, "index version") for item in scope["index_version_ids"]
                    ),
                    "intent": "qa",
                    "answer_mode": "knowledge_only",
                }
            )
        return sorted(rows, key=lambda row: row["knowledge_base_name"])
    return scopes


def build_manifest(
    *,
    snapshot,
    scopes,
    documents,
    material_hashes,
    dataset_sha256,
    materials_sha256,
    embedding_model,
    created_at=None,
):
    """Build a secret-free manifest from a database snapshot and the in-memory seed map."""
    profile = f"{embedding_model}:{EMBEDDING_DIM}"
    if len(snapshot.get("user_ids") or []) != 1:
        raise IndexReuseError("owner")
    if snapshot["embedding_profile"] != profile:
        raise IndexReuseError("embedding model")
    derived = _derived_scopes(snapshot)
    provided = _scope_rows(scopes)
    if provided != derived:
        raise IndexReuseError("knowledge base")
    materials = []
    for row in sorted(snapshot["materials"], key=lambda item: item["version_id"]):
        document_id = documents.get(row["version_id"])
        if not document_id or material_hashes.get(document_id) != row["source_sha256"]:
            raise IndexReuseError("material hash")
        materials.append(
            {
                "document_id": document_id,
                "material_id": row["id"],
                "version_id": row["version_id"],
                "knowledge_base_id": row["knowledge_base_id"],
                "knowledge_base_name": next(
                    scope["knowledge_base_name"]
                    for scope in derived
                    if scope["knowledge_base_id"] == row["knowledge_base_id"]
                ),
                "title": row["title"],
                "source_sha256": row["source_sha256"],
                "source_mime": row["source_mime"],
                "byte_size": row["byte_size"],
            }
        )
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "kind": MANIFEST_KIND,
        "binding": "database",
        "database_name": snapshot["database_name"],
        "database_owner_role": snapshot["database_owner_role"],
        "schema_revision": snapshot["schema_revision"],
        "eval_owner_id": snapshot["user_ids"][0],
        "embedding_model": embedding_model,
        "embedding_dim": EMBEDDING_DIM,
        "embedding_profile": profile,
        "pipeline_profile": SEMANTIC_PROFILE,
        "dataset_sha256": _sha256(dataset_sha256, "dataset hash"),
        "materials_sha256": _sha256(materials_sha256, "materials file hash"),
        "material_hashes": {row["document_id"]: row["source_sha256"] for row in materials},
        "documents": {row["version_id"]: row["document_id"] for row in materials},
        "materials": materials,
        "knowledge_bases": [
            {"id": row["id"], "name": row["name"], "owner_user_id": row["owner_user_id"]}
            for row in sorted(snapshot["knowledge_bases"], key=lambda item: item["id"])
        ],
        "scopes": derived,
        "indexes": [
            {
                "id": row["id"],
                "material_version_id": row["material_version_id"],
                "pipeline_profile": row["pipeline_profile"],
                "embedding_profile": row["embedding_profile"],
                "embedding_dim": row["embedding_dim"],
                "chunk_count": row["chunk_count"],
                "status": row["status"],
                "is_active": row["is_active"],
                "generation": row["generation"],
            }
            for row in sorted(snapshot["indexes"], key=lambda item: item["id"])
        ],
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
    }
    validate_manifest_shape(manifest)
    verify_index_contents(
        manifest,
        snapshot,
        embedding_model=embedding_model,
        schema_revision=snapshot["schema_revision"],
    )
    reject_secret_keys(manifest)
    return manifest


def validate_manifest_shape(manifest):
    if not isinstance(manifest, dict):
        raise IndexReuseError("manifest version")
    reject_secret_keys(manifest)
    if manifest.get("manifest_version") != MANIFEST_VERSION or manifest.get("kind") != MANIFEST_KIND:
        raise IndexReuseError("manifest version")
    if manifest.get("binding") not in {"database", "export"}:
        raise IndexReuseError("database binding")
    assert_database_name(manifest.get("database_name"))
    _role(manifest.get("database_owner_role"))
    if not isinstance(manifest.get("schema_revision"), str) or not manifest["schema_revision"]:
        raise IndexReuseError("schema revision")
    _uuid(manifest.get("eval_owner_id"), "owner")
    model = manifest.get("embedding_model")
    if not isinstance(model, str) or not model.strip() or any(char.isspace() for char in model):
        raise IndexReuseError("embedding model")
    if manifest.get("embedding_dim") != EMBEDDING_DIM:
        raise IndexReuseError("embedding dimension")
    if manifest.get("embedding_profile") != f"{model}:{EMBEDDING_DIM}":
        raise IndexReuseError("embedding model")
    if manifest.get("pipeline_profile") != SEMANTIC_PROFILE:
        raise IndexReuseError("index version")
    _sha256(manifest.get("dataset_sha256"), "dataset hash")
    _sha256(manifest.get("materials_sha256"), "materials file hash")
    hashes = manifest.get("material_hashes")
    documents = manifest.get("documents")
    if not isinstance(hashes, dict) or not hashes or not isinstance(documents, dict):
        raise IndexReuseError("material hash")
    if set(documents.values()) != set(hashes):
        raise IndexReuseError("material hash")
    for document_id, digest in hashes.items():
        if not isinstance(document_id, str) or not document_id:
            raise IndexReuseError("material hash")
        _sha256(digest, "material hash")
    for version_id in documents:
        _uuid(version_id, "material hash")
    materials = manifest.get("materials")
    if not isinstance(materials, list) or len(materials) != len(documents):
        raise IndexReuseError("material hash")
    for row in materials:
        version_id = _uuid(row.get("version_id"), "material hash")
        document_id = row.get("document_id")
        if documents.get(version_id) != document_id or hashes.get(document_id) != row.get("source_sha256"):
            raise IndexReuseError("material hash")
        _uuid(row.get("material_id"), "material hash")
        _uuid(row.get("knowledge_base_id"), "knowledge base")
        if not isinstance(row.get("title"), str) or not row["title"]:
            raise IndexReuseError("material hash")
        if not isinstance(row.get("source_mime"), str) or isinstance(row.get("byte_size"), bool):
            raise IndexReuseError("material hash")
        if not isinstance(row.get("byte_size"), int) or row["byte_size"] < 1:
            raise IndexReuseError("material hash")
    if not isinstance(manifest.get("knowledge_bases"), list) or not manifest["knowledge_bases"]:
        raise IndexReuseError("knowledge base")
    base_names = {}
    for row in manifest["knowledge_bases"]:
        base_id = _uuid(row.get("id"), "knowledge base")
        if row.get("owner_user_id") != manifest["eval_owner_id"] or not isinstance(row.get("name"), str):
            raise IndexReuseError("knowledge base")
        base_names[base_id] = row["name"]
    for row in materials:
        base_id = _uuid(row["knowledge_base_id"], "knowledge base")
        if row.get("knowledge_base_name") != base_names.get(base_id):
            raise IndexReuseError("knowledge base")
    if not isinstance(manifest.get("scopes"), list) or not manifest["scopes"]:
        raise IndexReuseError("knowledge base")
    if not isinstance(manifest.get("indexes"), list) or not manifest["indexes"]:
        raise IndexReuseError("index version")
    index_ids = []
    for row in manifest["indexes"]:
        index_ids.append(_uuid(row.get("id"), "index version"))
        version_id = _uuid(row.get("material_version_id"), "index version")
        if version_id not in documents:
            raise IndexReuseError("index version")
        if (
            row.get("pipeline_profile") != SEMANTIC_PROFILE
            or row.get("embedding_profile") != manifest["embedding_profile"]
            or row.get("embedding_dim") != EMBEDDING_DIM
            or row.get("status") != "ready"
            or row.get("is_active") is not True
        ):
            raise IndexReuseError("index version")
        chunk_count = row.get("chunk_count")
        generation = row.get("generation")
        if isinstance(chunk_count, bool) or not isinstance(chunk_count, int) or chunk_count < 1:
            raise IndexReuseError("chunk count")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            raise IndexReuseError("index version")
    if len(set(index_ids)) != len(index_ids):
        raise IndexReuseError("index version")
    if not isinstance(manifest.get("created_at"), str):
        raise IndexReuseError("manifest version")


def verify_index_contents(manifest, snapshot, *, embedding_model, schema_revision):
    validate_manifest_shape(manifest)
    if manifest["schema_revision"] != schema_revision or snapshot.get("schema_revision") != schema_revision:
        raise IndexReuseError("schema revision")
    if manifest["embedding_model"] != embedding_model:
        raise IndexReuseError("embedding model")
    if snapshot.get("embedding_profile") != manifest["embedding_profile"]:
        raise IndexReuseError("embedding model")
    if snapshot.get("user_ids") != [manifest["eval_owner_id"]]:
        raise IndexReuseError("owner")
    expected_bases = {row["id"]: row for row in manifest["knowledge_bases"]}
    found_bases = {row["id"]: row for row in snapshot.get("knowledge_bases", [])}
    if set(expected_bases) != set(found_bases):
        raise IndexReuseError("knowledge base")
    for base_id, row in found_bases.items():
        expected = expected_bases[base_id]
        if row.get("deleted_at") or row["owner_user_id"] != manifest["eval_owner_id"]:
            raise IndexReuseError("knowledge base")
        if row["name"] != expected["name"] or expected["owner_user_id"] != manifest["eval_owner_id"]:
            raise IndexReuseError("knowledge base")
    if _derived_scopes(snapshot) != manifest["scopes"]:
        raise IndexReuseError("knowledge base")
    materials = snapshot.get("materials", [])
    if any(not row["is_current"] or row["owner_user_id"] != manifest["eval_owner_id"] for row in materials):
        raise IndexReuseError("material hash")
    found_versions = {row["version_id"]: row for row in materials}
    if set(found_versions) != set(manifest["documents"]):
        raise IndexReuseError("material hash")
    for version_id, document_id in manifest["documents"].items():
        row = found_versions[version_id]
        if row["source_sha256"] != manifest["material_hashes"][document_id]:
            raise IndexReuseError("material hash")
        if document_id != manifest["documents"][version_id]:
            raise IndexReuseError("material hash")
    expected_indexes = {row["id"]: row for row in manifest["indexes"]}
    found_indexes = {row["id"]: row for row in snapshot.get("indexes", [])}
    if set(expected_indexes) != set(found_indexes):
        raise IndexReuseError("index version")
    for index_id, row in found_indexes.items():
        expected = expected_indexes[index_id]
        if (
            not row["is_active"]
            or row["status"] != "ready"
            or row["pipeline_profile"] != SEMANTIC_PROFILE
            or row["embedding_profile"] != manifest["embedding_profile"]
            or row["embedding_dim"] != EMBEDDING_DIM
            or row["vector_dim_min"] != EMBEDDING_DIM
            or row["vector_dim_max"] != EMBEDDING_DIM
            or row["model_profiles"] != [manifest["embedding_profile"]]
            or row["chunk_count"] != row["actual_chunks"]
            or row["actual_chunks"] != row["embedding_rows"]
            or row["chunk_count"] != expected["chunk_count"]
            or row["material_version_id"] != expected["material_version_id"]
            or row["generation"] != expected["generation"]
        ):
            raise IndexReuseError("index version")
        if row["chunk_count"] < 1:
            raise IndexReuseError("chunk count")


def _text_sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


def validate_records(manifest, records):
    validate_manifest_shape(manifest)
    if not isinstance(records, list) or not records:
        raise IndexReuseError("material hash")
    reject_secret_keys(records)
    by_version = {row["version_id"]: row for row in manifest["materials"]}
    seen = set()
    for record in records:
        version_id = _uuid(record.get("material_version_id"), "material hash")
        expected = by_version.get(version_id)
        if expected is None or version_id in seen:
            raise IndexReuseError("material hash")
        seen.add(version_id)
        if (
            record.get("document_id") != expected["document_id"]
            or _uuid(record.get("material_id"), "material hash") != expected["material_id"]
            or record.get("source_sha256") != expected["source_sha256"]
            or record.get("knowledge_base_id") != expected["knowledge_base_id"]
            or record.get("title") != expected["title"]
            or record.get("source_mime") != expected["source_mime"]
            or record.get("byte_size") != expected["byte_size"]
        ):
            raise IndexReuseError("material hash")
        index = next(row for row in manifest["indexes"] if row["material_version_id"] == version_id)
        if record.get("index_version_id") != index["id"] or record.get("chunk_count") != index["chunk_count"]:
            raise IndexReuseError("index version")
        chunks = record.get("chunks")
        if not isinstance(chunks, list) or len(chunks) != index["chunk_count"]:
            raise IndexReuseError("chunk count")
        ordinals = []
        for chunk in chunks:
            if not isinstance(chunk, dict) or not isinstance(chunk.get("text"), str):
                raise IndexReuseError("chunk count")
            if chunk.get("text_sha256") != _text_sha256(chunk["text"]):
                raise IndexReuseError("material hash")
            if not isinstance(chunk.get("locator"), dict):
                raise IndexReuseError("index version")
            ordinals.append(chunk.get("ordinal"))
            vector_to_list(chunk.get("embedding"))
        if ordinals != list(range(len(chunks))):
            raise IndexReuseError("chunk count")
    if seen != set(by_version):
        raise IndexReuseError("material hash")


def canonical_manifest(manifest):
    return json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def package_digest(manifest, records_bytes):
    payload = canonical_manifest(manifest).encode() + b"\n" + records_bytes
    return hashlib.sha256(payload).hexdigest()


def write_package(directory, manifest, records):
    directory = Path(directory)
    if directory.exists():
        raise ValueError("Refusing to overwrite an existing index package")
    validate_records(manifest, records)
    raw = "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records).encode()
    temporary = directory.parent / f".{directory.name}.{uuid4().hex}.tmp"
    temporary.mkdir(parents=True)
    try:
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (temporary / "records.jsonl").write_bytes(raw)
        (temporary / "package.sha256").write_text(package_digest(manifest, raw) + "\n", encoding="utf-8")
        temporary.rename(directory)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return directory


def load_index_package(path):
    path = Path(path)
    if path.is_dir():
        manifest_path = path / "manifest.json"
        records_path = path / "records.jsonl"
        digest_path = path / "package.sha256"
    else:
        manifest_path = path
        records_path = None
        digest_path = None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_manifest_shape(manifest)
    records = None
    if path.is_dir() and manifest["binding"] == "export":
        if records_path is None or digest_path is None or not records_path.is_file() or not digest_path.is_file():
            raise IndexReuseError("material hash")
        raw = records_path.read_bytes()
        expected = digest_path.read_text(encoding="utf-8").strip()
        if expected != package_digest(manifest, raw):
            raise IndexReuseError("package hash")
        records = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
        validate_records(manifest, records)
    elif manifest["binding"] == "export":
        raise IndexReuseError("material hash")
    return manifest, records


def scopes_by_name(scopes):
    return {row["knowledge_base_name"]: row for row in scopes}


def read_access(connection):
    database_name = connection.scalar(sa.text("SELECT current_database()"))
    owner_role, current_user = connection.execute(
        sa.text(
            """
            SELECT r.rolname, current_user
            FROM pg_database AS d
            JOIN pg_roles AS r ON r.oid = d.datdba
            WHERE d.datname = current_database()
            """
        )
    ).one()
    return {
        "database_name": database_name,
        "database_owner_role": owner_role,
        "current_user": current_user,
    }


def _text(value):
    return None if value is None else str(value).strip()


def read_snapshot(factory):
    with factory() as session:
        access = read_access(session)
        schema_revision = session.scalar(sa.text("SELECT version_num FROM study.alembic_version"))
        user_ids = [_uuid(row, "owner") for row in session.scalars(sa.select(User.id)).all()]
        knowledge_bases = [
            {
                "id": _uuid(row.id, "knowledge base"),
                "name": row.name,
                "owner_user_id": _uuid(row.owner_user_id, "knowledge base"),
                "deleted_at": row.deleted_at.isoformat() if row.deleted_at else None,
            }
            for row in session.execute(
                sa.select(
                    KnowledgeBase.id,
                    KnowledgeBase.name,
                    KnowledgeBase.owner_user_id,
                    KnowledgeBase.deleted_at,
                )
            ).all()
        ]
        materials = [
            {
                "id": _uuid(row.id, "material hash"),
                "owner_user_id": _uuid(row.owner_user_id, "material hash"),
                "knowledge_base_id": _uuid(row.knowledge_base_id, "knowledge base"),
                "title": row.title,
                "version_id": _uuid(row.version_id, "material hash"),
                "source_sha256": _text(row.source_sha256),
                "is_current": bool(row.is_current),
                "source_mime": row.source_mime,
                "byte_size": int(row.byte_size),
            }
            for row in session.execute(
                sa.select(
                    Material.id,
                    Material.owner_user_id,
                    Material.knowledge_base_id,
                    Material.title,
                    MaterialVersion.id.label("version_id"),
                    MaterialVersion.source_sha256,
                    MaterialVersion.is_current,
                    MaterialVersion.source_mime,
                    MaterialVersion.byte_size,
                ).join(MaterialVersion, MaterialVersion.material_id == Material.id)
            ).all()
        ]
        count_rows = dict(
            session.execute(
                sa.select(MaterialChunk.index_version_id, sa.func.count()).group_by(
                    MaterialChunk.index_version_id
                )
            ).all()
        )
        embedding_rows = dict(
            session.execute(
                sa.select(MaterialChunk.index_version_id, sa.func.count())
                .join(ChunkEmbedding, ChunkEmbedding.chunk_id == MaterialChunk.id)
                .group_by(MaterialChunk.index_version_id)
            ).all()
        )
        profiles = {}
        for index_id, profile in session.execute(
            sa.select(MaterialChunk.index_version_id, ChunkEmbedding.model_profile_id)
            .join(ChunkEmbedding, ChunkEmbedding.chunk_id == MaterialChunk.id)
            .distinct()
        ).all():
            profiles.setdefault(index_id, set()).add(profile)
        dims = {
            UUID(str(index_id)): (int(low), int(high))
            for index_id, low, high in session.execute(
                sa.text(
                    """
                    SELECT c.index_version_id::text, MIN(vector_dims(e.embedding)),
                           MAX(vector_dims(e.embedding))
                    FROM study.material_chunks AS c
                    JOIN study.chunk_embeddings AS e ON e.chunk_id = c.id
                    GROUP BY c.index_version_id
                    """
                )
            ).all()
        }
        indexes = []
        for row in session.execute(
            sa.select(
                IndexVersion.id,
                IndexVersion.material_version_id,
                IndexVersion.pipeline_profile,
                IndexVersion.embedding_profile,
                IndexVersion.embedding_dim,
                IndexVersion.chunk_count,
                IndexVersion.status,
                IndexVersion.is_active,
                IndexVersion.generation,
            )
        ).all():
            index_id = row.id
            low, high = dims.get(index_id, (None, None))
            indexes.append(
                {
                    "id": _uuid(index_id, "index version"),
                    "material_version_id": _uuid(row.material_version_id, "index version"),
                    "pipeline_profile": row.pipeline_profile,
                    "embedding_profile": row.embedding_profile,
                    "embedding_dim": int(row.embedding_dim),
                    "chunk_count": int(row.chunk_count),
                    "status": row.status,
                    "is_active": bool(row.is_active),
                    "generation": int(row.generation),
                    "actual_chunks": int(count_rows.get(index_id, 0)),
                    "embedding_rows": int(embedding_rows.get(index_id, 0)),
                    "model_profiles": sorted(profiles.get(index_id, ())),
                    "vector_dim_min": low,
                    "vector_dim_max": high,
                }
            )
        profile_values = {row["embedding_profile"] for row in indexes}
        snapshot = {
            **access,
            "schema_revision": schema_revision,
            "user_ids": user_ids,
            "knowledge_bases": knowledge_bases,
            "materials": materials,
            "indexes": indexes,
            "embedding_profile": next(iter(profile_values)) if len(profile_values) == 1 else None,
        }
        return snapshot


def read_export_records(factory, manifest):
    materials = {row["version_id"]: row for row in manifest["materials"]}
    with factory() as session:
        rows = session.execute(
            sa.select(
                MaterialChunk.index_version_id,
                MaterialChunk.material_version_id,
                MaterialChunk.ordinal,
                MaterialChunk.text,
                MaterialChunk.text_sha256,
                MaterialChunk.locator,
                ChunkEmbedding.embedding,
                ChunkEmbedding.model_profile_id,
            )
            .join(ChunkEmbedding, ChunkEmbedding.chunk_id == MaterialChunk.id)
            .order_by(MaterialChunk.material_version_id, MaterialChunk.ordinal)
        ).all()
    grouped = {}
    for row in rows:
        grouped.setdefault(str(row.material_version_id), []).append(row)
    records = []
    for version_id in sorted(materials):
        material = materials[version_id]
        chunks = []
        for row in grouped.get(version_id, []):
            if row.model_profile_id != manifest["embedding_profile"]:
                raise IndexReuseError("embedding model")
            chunks.append(
                {
                    "ordinal": int(row.ordinal),
                    "text": row.text,
                    "text_sha256": _text(row.text_sha256),
                    "locator": row.locator,
                    "embedding": vector_to_list(row.embedding),
                }
            )
        index = next(item for item in manifest["indexes"] if item["material_version_id"] == version_id)
        records.append(
            {
                "document_id": material["document_id"],
                "material_id": material["material_id"],
                "material_version_id": version_id,
                "knowledge_base_id": material["knowledge_base_id"],
                "knowledge_base_name": material["knowledge_base_name"],
                "title": material["title"],
                "source_sha256": material["source_sha256"],
                "source_mime": material["source_mime"],
                "byte_size": material["byte_size"],
                "index_version_id": index["id"],
                "chunk_count": index["chunk_count"],
                "chunks": chunks,
            }
        )
    exported = dict(manifest, binding="export")
    validate_records(exported, records)
    return records


def import_index(factory, manifest, records):
    """Insert a previously embedded package. This function does not call an embedding API."""
    validate_records(manifest, records)
    owner = UUID(manifest["eval_owner_id"])
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
        bases = {}
        for row in manifest["knowledge_bases"]:
            base = KnowledgeBase(
                id=UUID(row["id"]), owner_user_id=owner, name=row["name"]
            )
            db.add(base)
            db.flush()
            bases[row["id"]] = base
        for record in records:
            material = Material(
                id=UUID(record["material_id"]),
                owner_user_id=owner,
                knowledge_base_id=UUID(record["knowledge_base_id"]),
                title=record["title"],
                kind="notes",
            )
            version = MaterialVersion(
                id=UUID(record["material_version_id"]),
                material_id=material.id,
                version_no=1,
                is_current=True,
                source_mime=record["source_mime"],
                source_sha256=record["source_sha256"],
                byte_size=record["byte_size"],
                object_key=str(record["material_version_id"]),
                parse_status="ready",
                parser_profile="document-v1",
            )
            index_row = next(
                row for row in manifest["indexes"] if row["id"] == record["index_version_id"]
            )
            index = IndexVersion(
                id=UUID(record["index_version_id"]),
                material_version_id=version.id,
                generation=index_row["generation"],
                pipeline_profile=manifest["pipeline_profile"],
                embedding_profile=manifest["embedding_profile"],
                embedding_dim=EMBEDDING_DIM,
                status="ready",
                is_active=True,
                chunk_count=record["chunk_count"],
                ready_at=datetime.now(timezone.utc),
            )
            db.add(material)
            db.flush()
            db.add(version)
            db.flush()
            db.add(index)
            db.flush()
            for chunk in record["chunks"]:
                stored = MaterialChunk(
                    id=uuid4(),
                    index_version_id=index.id,
                    material_version_id=version.id,
                    ordinal=chunk["ordinal"],
                    text=chunk["text"],
                    text_sha256=chunk["text_sha256"],
                    locator=chunk["locator"],
                    **lexical_column_values(chunk["text"]),
                )
                db.add(stored)
                db.flush()
                db.add(
                    ChunkEmbedding(
                        chunk_id=stored.id,
                        model_profile_id=manifest["embedding_profile"],
                        embedding=vector_to_list(chunk["embedding"]),
                    )
                )


@contextmanager
def read_only_retrieval(database_url):
    engine = sa.create_engine(
        database_url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"connect_timeout": 5, "options": READ_ONLY_OPTIONS},
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    from study_platform.services import chat

    previous = chat.session_factory
    chat.session_factory = lambda: factory
    try:
        yield factory
    finally:
        chat.session_factory = previous
        engine.dispose()


@contextmanager
def retained_database(manifest, *, application_database, shared_test_database, test_database_url_raw=None):
    """Reconnect to one retained evaluation database. This context never drops it."""
    validate_manifest_shape(manifest)
    if manifest["binding"] != "database":
        raise IndexReuseError("database binding")
    if manifest["database_name"] == application_database:
        raise IndexReuseError("application database")
    if shared_test_database and manifest["database_name"] == shared_test_database:
        raise IndexReuseError("shared test database")
    url = reuse_target_url(test_database_url_raw if test_database_url_raw is not None else _env_test_url(),
                           manifest["database_name"])
    rendered = url.render_as_string(hide_password=False)
    engine = sa.create_engine(
        rendered,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"connect_timeout": 5, "options": READ_ONLY_OPTIONS},
    )
    try:
        with engine.connect() as connection:
            access = read_access(connection)
        assert_database_access(
            access,
            application_database=application_database,
            shared_test_database=shared_test_database,
        )
        verify_retained_identity(manifest, access)
    except Exception:
        engine.dispose()
        raise
    previous_env = {
        key: os.environ.get(key) for key in ("DATABASE_URL", "MIGRATION_DATABASE_URL")
    }
    os.environ["DATABASE_URL"] = rendered
    os.environ["MIGRATION_DATABASE_URL"] = rendered
    get_settings.cache_clear()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    from study_platform.services import chat

    previous_factory = chat.session_factory
    chat.session_factory = lambda: factory
    try:
        yield factory
    finally:
        chat.session_factory = previous_factory
        engine.dispose()
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()


def _env_test_url():
    return os.getenv("TEST_DATABASE_URL")
