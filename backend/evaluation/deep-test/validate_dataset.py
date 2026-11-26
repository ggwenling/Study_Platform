"""Validators for deep-test corpus coverage, IDs, and evidence integrity."""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

REQUIRED_CATEGORIES = {
    "unanswerable",
    "misleading",
    "conflicting_scope",
    "versioned",
    "versioned_trap",
    "cross_document",
    "multilingual",
    "tables",
    "factual",
    "ocr",
    "scan",
    "web_clip",
}
MIN_QUESTIONS = 100
REVIEW_STATUS = "synthetic_not_human_curated"


def load() -> tuple[list[dict], list[dict], list[dict]]:
    root = Path(__file__).resolve().parent
    materials = json.loads(root.joinpath("materials.json").read_text(encoding="utf-8"))
    questions = [
        json.loads(line)
        for line in root.joinpath("questions.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    canaries = json.loads(root.joinpath("isolation_canaries.json").read_text(encoding="utf-8"))
    return materials, questions, canaries


def validate_ids(questions: list[dict]) -> list[str]:
    errors: list[str] = []
    ids = [row["id"] for row in questions]
    if len(ids) != len(set(ids)):
        errors.append("duplicate question IDs")
    if not re.match(r"^[A-Z0-9]+-\d+$", ids[0] if ids else ""):
        pass
    for row in questions:
        if not re.fullmatch(r"[A-Z]+\d+-\d+", row["id"]):
            errors.append(f"bad id format: {row['id']}")
    return errors


def validate_evidence(materials: list[dict], questions: list[dict]) -> list[str]:
    errors: list[str] = []
    by_id = {m["id"]: m for m in materials}
    libs = {m["knowledge_base"] for m in materials}
    groups: dict[str, str] = {}
    for row in questions:
        if row.get("review_status") != REVIEW_STATUS:
            errors.append(f"{row['id']}: review_status must be {REVIEW_STATUS}")
        if row["knowledge_base"] not in libs:
            errors.append(f"{row['id']}: unknown knowledge_base")
        grp = row["fact_group"]
        if grp in groups and groups[grp] != row["split"]:
            errors.append(f"{row['id']}: split mismatch within fact_group {grp}")
        groups[grp] = row["split"]
        if row["answerable"]:
            if not row.get("expected_facts") or not row.get("support"):
                errors.append(f"{row['id']}: answerable needs expected_facts and support")
            for support in row["support"]:
                doc = by_id.get(support["document"])
                if not doc:
                    errors.append(f"{row['id']}: missing document {support['document']}")
                    continue
                if doc["knowledge_base"] != row["knowledge_base"]:
                    errors.append(f"{row['id']}: support KB mismatch")
                quote = support.get("quote")
                if not quote or quote == row["question"]:
                    errors.append(f"{row['id']}: invalid quote")
        else:
            if row.get("expected_facts") or row.get("support"):
                errors.append(f"{row['id']}: unanswerable must not carry gold support")
    return errors


def validate_coverage(questions: list[dict]) -> list[str]:
    errors: list[str] = []
    if len(questions) < MIN_QUESTIONS:
        errors.append(f"need >={MIN_QUESTIONS} questions, got {len(questions)}")
    cats = Counter(row["category"] for row in questions)
    missing = REQUIRED_CATEGORIES - set(cats)
    if missing:
        errors.append(f"missing categories: {sorted(missing)}")
    if cats["unanswerable"] < 8:
        errors.append("need >=8 unanswerable cases (4 groups min)")
    cross = [r for r in questions if r["category"] == "cross_document" and r["answerable"]]
    multi_support = [r for r in cross if len(r.get("support") or []) >= 2]
    if not multi_support:
        errors.append("need cross-document case with >=2 support docs")
    return errors


def main() -> int:
    materials, questions, canaries = load()
    errors: list[str] = []
    errors.extend(validate_ids(questions))
    errors.extend(validate_evidence(materials, questions))
    errors.extend(validate_coverage(questions))
    if len({m["id"] for m in materials}) != len(materials):
        errors.append("duplicate material IDs")
    if errors:
        for item in errors:
            print(f"ERROR: {item}", file=sys.stderr)
        return 1
    print(
        f"OK: {len(questions)} questions, {len(materials)} materials, "
        f"{len(canaries)} isolation canaries"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())