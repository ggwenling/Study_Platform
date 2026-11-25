"""Evidence validation for DEEP-SEC-01: classify search hits by ownership, not item count.

Isolated synthetic context only — not a production breach claim.
Recreates study_platform_test, seeds disjoint MOCK ingest per iteration, inspects API + DB.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import sqlalchemy as sa
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
BACKEND = ROOT / "backend"
API = "/api/v1"
PASSWORD = "deep-test-safe-password-42"
HARNESS_DIR = Path(__file__).resolve().parent


def _load_env() -> None:
    env = ROOT / ".local" / "deep-test" / "env" / "deep-test.env"
    if not env.exists():
        raise SystemExit(f"Missing {env}")
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.strip() and "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ[k.strip()] = v.strip()
    os.environ["RUN_DB_TESTS"] = "1"


def _recreate_db() -> None:
    from db_reset_guard import assert_safe_database

    assert_safe_database()
    subprocess.check_call(
        ["uv", "run", "python", "tests/ci_bootstrap_postgres.py"],
        cwd=BACKEND,
        env=os.environ,
    )


def _app():
    from study_platform.core.config import Settings, get_settings
    from study_platform.main import create_app
    from study_platform.persistence.database import get_db, session_factory

    settings = Settings(
        _env_file=None,
        auth_jwt_secret=os.environ["AUTH_JWT_SECRET"],
        storage_dir=Path(os.environ["STORAGE_DIR"]),
        deepseek_api_key="",
        embedding_api_key="",
    )
    get_settings.cache_clear()
    app = create_app(settings)

    def override_db():
        db = session_factory()()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    return app, session_factory


def _login(client: TestClient, email: str) -> dict:
    r = client.post(f"{API}/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    me = client.get(f"{API}/me")
    assert me.status_code == 200, me.text
    return me.json()


def _mock_ingest_pair(
    client: TestClient, session_factory, run_tag: str
) -> dict:
    from study_platform.persistence.models.identity import Material
    from study_platform.persistence.models.materials import IndexVersion, MaterialChunk
    from study_platform.services import ingestion

    canary_u1 = f"HYPERION-RED-4411-{run_tag}"
    canary_u2 = f"NYX-VIOLET-9920-{run_tag}"
    def fake_embed(texts, **_):
        return [[0.1] * 1024 for _ in texts]

    users: dict = {}

    with patch.object(ingestion, "embed_texts", fake_embed), patch(
        "study_platform.services.models.embed_texts", fake_embed
    ):
        for label, email_suffix, canary in (
            ("u1", "one", canary_u1),
            ("u2", "two", canary_u2),
        ):
            email = f"sec01-{email_suffix}-{run_tag}@example.test"
            client.post(
                f"{API}/auth/register",
                json={"email": email, "password": PASSWORD, "display_name": label},
            )
            _login(client, email)
            kb = client.post(f"{API}/knowledge-bases", json={"name": f"SecProbe-{label}-{run_tag}"}).json()[
                "id"
            ]
            upload = client.post(
                f"{API}/knowledge-bases/{kb}/materials",
                files={"file": (f"{label}-{run_tag}.txt", canary.encode(), "text/plain")},
            )
            assert upload.status_code == 202, upload.text
            material_id = upload.json()["material_id"]
            deadline = time.time() + 60
            while time.time() < deadline:
                ingestion.run_once()
                mat = client.get(f"{API}/materials/{material_id}").json()
                if mat.get("state") == "ready" or mat.get("status") == "ready":
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError(f"ingest timeout {label}")
            me = client.get(f"{API}/me").json()
            users[label] = {
                "email": email,
                "user_id": me["id"],
                "knowledge_base_id": kb,
                "material_id": material_id,
                "canary": canary,
            }
            client.headers.pop("Authorization", None)

    with session_factory()() as db:
        for label, info in users.items():
            mat = db.get(Material, info["material_id"])
            assert mat and str(mat.owner_user_id) == info["user_id"]
            chunks = db.scalars(
                sa.select(MaterialChunk)
                .join(IndexVersion)
                .where(
                    IndexVersion.is_active.is_(True),
                    MaterialChunk.text.contains(info["canary"]),
                )
            ).all()
            info["chunk_ids"] = [str(c.id) for c in chunks]
            assert len(info["chunk_ids"]) >= 1, label

    return {"users": users, "canaries_disjoint": canary_u1 not in canary_u2}


def _classify_items(
    db,
    items: list[dict],
    u1: dict,
    u2: dict,
    peer_canary: str,
) -> list[dict]:
    from study_platform.persistence.models.conversations import Conversation, Note
    from study_platform.persistence.models.identity import Material
    from study_platform.persistence.models.knowledge import KnowledgeBase
    from study_platform.persistence.models.materials import MaterialChunk, MaterialVersion

    u1_uid = sa.UUID(u1["user_id"])
    u2_uid = sa.UUID(u2["user_id"])
    u2_kb = sa.UUID(u2["knowledge_base_id"])
    u2_mat = sa.UUID(u2["material_id"])
    classified = []

    for it in items:
        row = {
            "api_type": it.get("type"),
            "api_id": it.get("id"),
            "title": it.get("title"),
            "knowledge_base_id": it.get("knowledge_base_id"),
            "excerpt_sanitized": (it.get("excerpt") or "")[:120],
            "matched_terms": it.get("matched_terms"),
        }
        owner_uid = None
        material_id = None
        kb_id = it.get("knowledge_base_id")

        if it.get("type") == "fragment":
            ch = db.scalar(sa.select(MaterialChunk).where(MaterialChunk.id == it["id"]))
            if ch:
                ver = db.get(MaterialVersion, ch.material_version_id)
                if ver:
                    mat = db.get(Material, ver.material_id)
                    if mat:
                        material_id = str(mat.id)
                        kb_id = str(mat.knowledge_base_id)
                        owner_uid = mat.owner_user_id
        elif it.get("type") == "material":
            mat = db.get(Material, it["id"])
            if mat:
                material_id = str(mat.id)
                owner_uid = mat.owner_user_id
        elif it.get("type") == "knowledge_base":
            kb = db.get(KnowledgeBase, it["id"])
            if kb:
                owner_uid = kb.owner_user_id
        elif it.get("type") == "note":
            note = db.get(Note, it["id"])
            if note:
                owner_uid = note.owner_user_id
        elif it.get("type") == "conversation":
            conv = db.get(Conversation, it["id"])
            if conv:
                owner_uid = conv.owner_user_id

        row["db_owner_user_id"] = str(owner_uid) if owner_uid else None
        row["db_material_id"] = material_id
        text_blob = (it.get("title") or "") + " " + (it.get("excerpt") or "")
        row["contains_peer_exact_canary"] = peer_canary in text_blob
        row["foreign_kb"] = kb_id is not None and str(kb_id) == str(u2_kb)
        row["foreign_material"] = material_id is not None and str(material_id) == str(u2_mat)
        row["is_u2_owned"] = owner_uid == u2_uid
        row["is_u1_owned"] = owner_uid == u1_uid
        classified.append(row)
    return classified


def _run_iteration(iter_no: int, out_dir: Path) -> dict:
    tag = uuid4().hex[:8]
    started = time.monotonic()
    _recreate_db()
    _load_env()
    app, session_factory = _app()
    origin = {"Origin": "http://127.0.0.1:5173"}
    seed = _mock_ingest_pair(TestClient(app, headers=origin), session_factory, tag)
    u1, u2 = seed["users"]["u1"], seed["users"]["u2"]

    scenarios: list[dict] = []
    with TestClient(app, headers={"Origin": "http://127.0.0.1:5173"}) as client:
        unauth = client.get(f"{API}/search", params={"q": u2["canary"], "include_content": "true"})
        scenarios.append(
            {
                "name": "unauth_search",
                "status_code": unauth.status_code,
                "pass": unauth.status_code in (401, 403),
            }
        )

        _login(client, u1["email"])
        me_u1 = client.get(f"{API}/me").json()
        pos = client.get(f"{API}/search", params={"q": u1["canary"], "include_content": "true"})
        client.headers.pop("Authorization", None)

        _login(client, u1["email"])
        cross = client.get(f"{API}/search", params={"q": u2["canary"], "include_content": "true"})
        client.headers.pop("Authorization", None)

        _login(client, u2["email"])
        pos_u2 = client.get(f"{API}/search", params={"q": u2["canary"], "include_content": "true"})
        client.headers.pop("Authorization", None)

        # Legacy shared-prefix probe (documents false-positive mechanism on fresh DB)
        legacy_q = "MOCK-INDEX-CANARY-U2-BRAVO"
        _login(client, u1["email"])
        legacy = client.get(f"{API}/search", params={"q": legacy_q, "include_content": "true"})
        client.headers.pop("Authorization", None)

    with session_factory()() as db:
        cross_items = cross.json().get("items", []) if cross.status_code == 200 else []
        cross_class = _classify_items(db, cross_items, u1, u2, u2["canary"])
        leaks = [
            c
            for c in cross_class
            if c["is_u2_owned"] or c["foreign_kb"] or c["foreign_material"] or c["contains_peer_exact_canary"]
        ]

    duration_s = round(time.monotonic() - started, 2)
    verdict = {
        "iteration": iter_no,
        "run_tag": tag,
        "duration_seconds": duration_s,
        "jwt_u1_matches_me": str(me_u1.get("id")) == u1["user_id"],
        "disjoint_canaries": seed["canaries_disjoint"],
        "positive_u1": {"status": pos.status_code, "count": len(pos.json().get("items", []))},
        "positive_u2": {"status": pos_u2.status_code, "count": len(pos_u2.json().get("items", []))},
        "cross_u1_queries_u2_canary": {
            "query": u2["canary"],
            "status_code": cross.status_code,
            "item_count": len(cross_items),
            "unauthorized_leaks": len(leaks),
            "leak_details": leaks,
            "classified_items": cross_class,
            "pass": len(leaks) == 0,
        },
        "legacy_shared_prefix_query": {
            "query": legacy_q,
            "item_count": len(legacy.json().get("items", [])) if legacy.status_code == 200 else 0,
            "note": "Expected 0 on fresh DB with disjoint corpus; non-zero on polluted DB implies own-doc OR-term noise",
        },
        "scenarios": scenarios,
        "seed": {
            "u1": {k: v for k, v in u1.items() if k != "chunk_ids"},
            "u2": {k: v for k, v in u2.items() if k != "chunk_ids"},
        },
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"iteration_{iter_no}.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return verdict


def main() -> int:
    sys.path.insert(0, str(HARNESS_DIR))
    _load_env()
    run_id = os.environ.get("SEC01_RUN_ID") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = ROOT / "artifacts" / "deep-test" / f"sec01-validation-{run_id}"
    iterations = []
    for i in (1, 2):
        iterations.append(_run_iteration(i, out_dir))

    conclusion = {
        "verdict": "FALSE_POSITIVE" if all(i["cross_u1_queries_u2_canary"]["pass"] for i in iterations) else "CONFIRMED_LEAK",
        "iterations": 2,
        "all_pass": all(i["cross_u1_queries_u2_canary"]["pass"] for i in iterations),
        "code_path": "backend/src/study_platform/api/v1/resources.py search() filters owner_user_id == user.id",
        "prior_fail_cause": "Assertion used item_count==0 while query shares OR terms (MOCK/INDEX/CANARY) with own chunks",
        "limitations": [
            "MOCK embeddings only",
            "TestClient in-process API",
            "Isolated study_platform_test",
            "Not production RAG quality",
        ],
        "artifact_dir": str(out_dir),
    }
    (out_dir / "summary.json").write_text(json.dumps(conclusion, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(conclusion, ensure_ascii=False, indent=2))
    return 0 if conclusion["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())