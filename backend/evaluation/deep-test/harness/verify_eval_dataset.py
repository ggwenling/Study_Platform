"""Beyond study-eval-rag dry-run: schema via read_dataset + quote-in-corpus checks."""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3]
DATASET = BACKEND / "evaluation" / "deep-test" / "questions.jsonl"


def main() -> int:
    sys.path.insert(0, str(BACKEND / "src"))
    from study_platform.services.rag_evaluation import read_dataset, supporting_quotes

    cases, materials = read_dataset(DATASET)
    by_id = {m["id"]: m for m in materials}
    errors: list[str] = []
    for case in cases:
        if not case.get("answerable"):
            continue
        for support in case["support"]:
            doc = by_id.get(support["document"])
            if not doc:
                continue
            blob = "\n".join(doc.get("sections") or [])
            for quote in supporting_quotes(support):
                if quote not in blob:
                    errors.append(f"{case['id']}: quote not in material {doc['id']} sections")
    extra = [k for case in cases for k in case if k not in {
        "id", "fact_group", "split", "knowledge_base", "question", "answerable",
        "expected_facts", "support", "category", "severity", "allowed_boundary", "review_status",
    }]
    report = {
        "ok": not errors,
        "questions": len(cases),
        "materials": len(materials),
        "read_dataset": "pass",
        "support_quotes_in_materials_sections": len(errors) == 0,
        "errors": errors[:20],
        "extra_fields_present": bool(extra),
        "note": (
            "read_dataset validates evaluator schema; "
            "quote-in-sections checks deep-test synthetic alignment only (not live RAG)"
        ),
        "review_status": "synthetic_not_human_curated",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())