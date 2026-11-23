"""Offline Evaluation V2 report.

Reads a saved report, frozen questions, and optional chunk manifest and review
annotations. It does not open a database, a network connection, or a model.
Missing inputs stay unknown.
"""

import csv
import hashlib
import json
from pathlib import Path

from study_platform.services.rag_evaluation import (
    expected_documents,
    normalized,
    refusal_marker,
    support_hit,
    supporting_quotes,
)
from study_platform.services.retrieval_metrics import evaluate_ranking

SCHEMA_VERSION = "evaluation-v2.1"
EXACT_ITEM_LIMIT = 16
QUALITY_LABELS = {"answered", "partial", "refused", "unsupported", "contradictory", "unknown"}

LIMITATIONS = [
    "Legacy support_hit, document IR, and the refusal substring keep their old definitions.",
    "Evidence coverage is a normalized document-constrained quote match, not semantic support.",
    "A document hit is not semantic support, and a legal citation id is not a faithful citation.",
    "Satisfiability uses the supplied chunk universe. Saved top-k evidence is an observed scope.",
    "Rows without a document-bound identity are dropped, and a complete flag does not make the rest a full universe.",
    "Span macro and micro use answerable questions that have support. Empty support and unanswerable rows stay outside those denominators.",
    "Latency and cost are recorded only from stored measurements. A missing live run stays NOT_VERIFIED.",
    "An exact minimum is reported only when the bounds meet or the set cover is within the exact limit.",
    "A greedy cover size is an upper bound. It is never reported as a lower bound.",
    "Necessary versus supplementary is unknown unless an annotation states that role and its source.",
    "Answer class stays unknown without one confirmed human annotation. An AI label is not that class.",
    "This report does not open the unreviewed quality gate.",
]


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _valid_index(value):
    """Return a non-negative index. Zero is valid. Bool, float, blank, and negative are not."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    return None


def _index_field(row, key):
    if key not in row:
        return None
    return _valid_index(row.get(key))


def _nonempty_id(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def chunk_identity(row):
    """Return a document-bound chunk key, or None when the row cannot be identified.

    A bare local index is rejected. ``local_index=None`` falls back to a valid
    ``ordinal``, including 0. The same local index in two documents is two chunks,
    and identical text does not collapse them.
    """
    if not isinstance(row, dict):
        return None
    document = row.get("document")
    if document is None or (isinstance(document, str) and document.strip() == ""):
        return None
    chunk_id = _nonempty_id(row.get("chunk_id"))
    if chunk_id is not None:
        return ("id", chunk_id, str(document))
    local = _index_field(row, "local_index")
    if local is None:
        local = _index_field(row, "ordinal")
    if local is not None:
        return ("loc", str(document), local)
    evidence_id = _nonempty_id(row.get("id"))
    if evidence_id is not None:
        return ("id", evidence_id, str(document))
    return None


def packing_lower(item_sets):
    """Size of one disjoint packing. Any packing is a proven lower bound."""
    if any(not item for item in item_sets):
        return None
    used = set()
    count = 0
    for item in sorted(item_sets, key=len):
        if item.isdisjoint(used):
            used |= set(item)
            count += 1
    return count


def greedy_upper(item_sets):
    """Classic greedy set-cover size. This is an upper bound, never a lower bound."""
    if any(not item for item in item_sets):
        return None
    uncovered = set(range(len(item_sets)))
    covers = {}
    for index, item in enumerate(item_sets):
        for chunk in item:
            covers.setdefault(chunk, set()).add(index)
    chosen = 0
    covered = set()
    while uncovered:
        best_gain = 0
        best = None
        for chunk, items in covers.items():
            gain = len(items - covered)
            if gain > best_gain:
                best_gain = gain
                best = chunk
        if best is None:
            return None
        covered |= covers[best]
        uncovered -= covers[best]
        chosen += 1
    return chosen


def exact_cover_size(item_sets):
    """Minimum cover when the item count is within the exact limit, else None."""
    if any(not item for item in item_sets):
        return None
    count = len(item_sets)
    if count > EXACT_ITEM_LIMIT:
        return None
    universe = []
    seen = set()
    for item in item_sets:
        for chunk in item:
            if chunk not in seen:
                seen.add(chunk)
                universe.append(chunk)
    index = {chunk: bit for bit, chunk in enumerate(universe)}
    cover_of = [0] * len(universe)
    for item_index, item in enumerate(item_sets):
        for chunk in item:
            cover_of[index[chunk]] |= 1 << item_index
    need = (1 << count) - 1
    unreachable = count + 1
    distance = [unreachable] * (need + 1)
    distance[0] = 0
    for mask in range(need + 1):
        current = distance[mask]
        if current >= unreachable:
            continue
        for covered_bits in cover_of:
            nxt = mask | covered_bits
            if current + 1 < distance[nxt]:
                distance[nxt] = current + 1
    return None if distance[need] >= unreachable else distance[need]


def cover_bounds(item_sets, *, allow_exact=True):
    """Return exact minimum, or proven bounds when the exact cover is not run."""
    lower = packing_lower(item_sets)
    upper = greedy_upper(item_sets)
    exact = exact_cover_size(item_sets) if allow_exact else None
    if exact is not None:
        return {
            "algorithm": "exact",
            "minimum": exact,
            "lower": exact,
            "upper": exact,
            "item_count": len(item_sets),
        }
    if lower is None or upper is None:
        return {
            "algorithm": "impossible",
            "minimum": None,
            "lower": None,
            "upper": None,
            "item_count": len(item_sets),
        }
    if lower == upper:
        return {
            "algorithm": "exact",
            "minimum": lower,
            "lower": lower,
            "upper": upper,
            "item_count": len(item_sets),
        }
    return {
        "algorithm": "bounded",
        "minimum": None,
        "lower": lower,
        "upper": upper,
        "item_count": len(item_sets),
    }


def _read_json_or_jsonl(path):
    text = Path(path).read_text(encoding="utf-8")
    stripped = text.lstrip()
    if Path(path).suffix == ".jsonl" or (stripped and stripped[0] != "{" and stripped[0] != "["):
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    data = json.loads(text)
    if isinstance(data, list):
        return data
    return data


def load_questions(path):
    rows = _read_json_or_jsonl(path)
    if isinstance(rows, dict):
        rows = rows.get("cases") or rows.get("questions") or []
    return {row["id"]: row for row in rows}


def load_annotations(path):
    if path is None:
        return []
    rows = _read_json_or_jsonl(path)
    if isinstance(rows, dict):
        rows = rows.get("annotations") or []
    return list(rows)


def load_manifest(path):
    if path is None:
        return None
    data = _read_json_or_jsonl(path)
    if isinstance(data, list):
        return {"complete": False, "chunks": data}
    return data


def load_report(path):
    data = _read_json_or_jsonl(path)
    if isinstance(data, list):
        return {"cases": [_normalize_case(row) for row in data]}, [_normalize_case(row) for row in data]
    cases = [_normalize_case(row) for row in data.get("cases") or []]
    return data, cases


def _normalize_case(row):
    out = dict(row)
    out["id"] = row.get("id") or row.get("question_id")
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    for key in (
        "retrieval_support_hit",
        "refusal_marker",
        "retrieval_ir",
        "fact_aliases_found",
        "citation_valid",
        "reciprocal_rank",
    ):
        if key not in out and key in metrics:
            out[key] = metrics[key]
    out["evidence"] = list(row.get("evidence") or [])
    out["citations"] = list(row.get("citations") or [])
    return out


def _legacy_inputs(evidence):
    documents = {}
    adapted = []
    for row in evidence:
        document = row.get("document")
        material = row.get("material_version_id") or document
        if document is None or material is None:
            return None
        documents[str(material)] = document
        adapted.append({**row, "material_version_id": material, "text": row.get("text") or ""})
    return adapted, documents


def _quote_hit(support, row):
    if row.get("document") != support.get("document"):
        return False
    blob = normalized(row.get("text") or "")
    return any(phrase and phrase in blob for phrase in map(normalized, supporting_quotes(support)))


def _item_locations(support, rows):
    matches = []
    for rank, row in enumerate(rows, start=1):
        if _quote_hit(support, row):
            matches.append({"rank": rank, "chunk": chunk_identity(row)})
    return matches


def _coverage(question, evidence):
    if question is None:
        return {
            "status": "unknown",
            "matched": None,
            "total": None,
            "and_hit": None,
            "items": [],
            "reason": "question_missing",
        }
    if not question.get("answerable", True):
        return {
            "status": "not_applicable",
            "matched": None,
            "total": 0,
            "and_hit": None,
            "items": [],
            "reason": "unanswerable",
        }
    support = question.get("support") or []
    if not support:
        return {
            "status": "empty_gold",
            "matched": 0,
            "total": 0,
            "and_hit": None,
            "items": [],
            "reason": "answerable_without_support",
        }
    items = []
    for index, item in enumerate(support):
        locations = _item_locations(item, evidence)
        items.append(
            {
                "support_index": index,
                "document": item.get("document"),
                "matched": bool(locations),
                "locations": locations,
                "role": "unknown",
                "role_source": None,
            }
        )
    matched = sum(item["matched"] for item in items)
    return {
        "status": "scored",
        "matched": matched,
        "total": len(items),
        "rate": matched / len(items),
        "and_hit": matched == len(items),
        "items": items,
        "reason": None,
    }


def _hit_sets(question, chunks):
    sets = []
    for support in question.get("support") or []:
        found = set()
        for row in chunks:
            identity = chunk_identity(row)
            if identity is not None and _quote_hit(support, row):
                found.add(identity)
        sets.append(found)
    return sets


def _satisfiability(question, evidence, manifest, top_k):
    base = {
        "scope": "observed",
        "universe": "saved_evidence",
        "k": top_k,
        "candidate_complete": False,
        "algorithm": "unknown",
        "minimum": None,
        "lower": None,
        "upper": None,
        "structural_at_k": "unknown",
        "observed_covered": None,
        "item_count": None,
    }
    if question is None or not question.get("answerable", True):
        base["algorithm"] = "not_applicable"
        base["structural_at_k"] = "not_applicable"
        return base
    if not (question.get("support") or []):
        base["algorithm"] = "empty_gold"
        return base
    support = question.get("support") or []
    base["observed_covered"] = all(
        any(_quote_hit(item, row) for row in evidence) for item in support
    )
    complete = bool(manifest and manifest.get("complete") is True)
    authorised = complete and (manifest.get("candidate_complete", True) is True)
    if not authorised:
        base["note"] = "Saved evidence is the observed scope. Missing quotes are not structural."
        return base
    chunks = list(manifest.get("chunks") or [])
    unidentified = sum(chunk_identity(row) is None for row in chunks)
    if unidentified:
        base["candidate_complete"] = False
        base["universe"] = "incomplete_identity"
        base["algorithm"] = "unknown"
        base["structural_at_k"] = "unknown"
        base["validation_error"] = (
            f"{unidentified} manifest rows have no document-bound identity. "
            "Those rows are not a proven absence, and complete=true does not "
            "make the remaining rows a full universe."
        )
        base["note"] = base["validation_error"]
        return base
    bounds = cover_bounds(_hit_sets(question, chunks))
    structural = "unknown"
    if bounds["algorithm"] == "impossible":
        structural = False
    elif top_k is None:
        structural = "unknown"
    elif bounds["minimum"] is not None:
        structural = bounds["minimum"] <= top_k
    elif bounds["lower"] is not None and bounds["lower"] > top_k:
        structural = False
    elif bounds["upper"] is not None and bounds["upper"] <= top_k:
        structural = True
    return {
        "scope": "corpus",
        "universe": "complete_manifest",
        "k": top_k,
        "candidate_complete": manifest.get("candidate_complete", True) is True,
        "algorithm": bounds["algorithm"],
        "minimum": bounds["minimum"],
        "lower": bounds["lower"],
        "upper": bounds["upper"],
        "structural_at_k": structural,
        "observed_covered": base["observed_covered"],
        "item_count": bounds["item_count"],
        "note": "Corpus bounds use document-bound chunk identities.",
    }


def _annotation_key(row, case):
    question_id = row.get("question_id") or row.get("id")
    return question_id == case.get("id") and row.get("strategy") == case.get("strategy")


def _valid_annotation(row):
    if not isinstance(row, dict):
        return False
    if row.get("source") not in {"ai", "human"}:
        return False
    if row.get("status") != "confirmed":
        return False
    if not row.get("provenance"):
        return False
    label = row.get("label")
    return label in QUALITY_LABELS


def _apply_roles(coverage, annotations):
    items = coverage.get("items") or []
    for item in items:
        chosen = []
        for row in annotations:
            if not _valid_annotation(row):
                continue
            for role in row.get("support_roles") or []:
                if role.get("support_index") != item["support_index"]:
                    continue
                if role.get("role") not in {"necessary", "supplementary"}:
                    continue
                chosen.append((row.get("source"), role.get("role"), row.get("provenance")))
        human = {role for source, role, _ in chosen if source == "human"}
        if len(human) == 1:
            item["role"] = next(iter(human))
            item["role_source"] = "human"
        elif len(human) > 1:
            item["role"] = "unknown"
            item["role_source"] = "conflict"
        else:
            item["role"] = "unknown"
            item["role_source"] = None


def _quality(case, annotations):
    relevant = [row for row in annotations if _annotation_key(row, case)]
    valid = [row for row in relevant if _valid_annotation(row)]
    ignored = len(relevant) - len(valid)
    human = [row["label"] for row in valid if row["source"] == "human"]
    ai = [row["label"] for row in valid if row["source"] == "ai"]
    human_unique = set(human)
    if len(human_unique) > 1:
        label = "unknown"
        method = "annotation_conflict"
        reviewed = False
    elif len(human_unique) == 1:
        label = next(iter(human_unique))
        method = "annotation_human"
        reviewed = True
    else:
        label = "unknown"
        method = "none"
        reviewed = False
    return {
        "class": label,
        "method": method,
        "human_reviewed": reviewed,
        "ground_truth": False,
        "ai_annotation_label": ai[0] if ai and len(set(ai)) == 1 else ("conflict" if len(set(ai)) > 1 else None),
        "ai_is_human": False,
        "ignored_annotations": ignored,
        "refusal_marker": refusal_marker(case.get("answer")),
    }


def _legacy(case, question, k_values):
    evidence = case.get("evidence") or []
    prepared = _legacy_inputs(evidence)
    result = {
        "retrieval_support_hit": None,
        "refusal_marker": refusal_marker(case.get("answer")),
        "retrieval_ir": None,
        "inputs": "incomplete" if question is None or prepared is None else "recomputed",
    }
    if question is None or prepared is None or "answerable" not in question or "support" not in question:
        result["inputs"] = "incomplete"
        return result
    adapted, documents = prepared
    if question.get("answerable", True):
        result["retrieval_support_hit"] = bool(support_hit(question, adapted, documents))
    expected = expected_documents(question)
    ranked = [row.get("document") for row in evidence if row.get("document") is not None]
    result["retrieval_ir"] = evaluate_ranking(ranked, expected, k_values)
    return result


def _values_equal(stored, recomputed):
    if isinstance(stored, float) or isinstance(recomputed, float):
        return stored == recomputed or (
            isinstance(stored, int | float) and isinstance(recomputed, int | float)
            and abs(float(stored) - float(recomputed)) <= 1e-9
        )
    return stored == recomputed


def _legacy_diff(case, legacy):
    drifts = []
    for key in ("retrieval_support_hit", "refusal_marker"):
        if key not in case:
            continue
        if not _values_equal(case.get(key), legacy.get(key)):
            drifts.append({"field": key, "stored": case.get(key), "recomputed": legacy.get(key)})
    stored_ir = case.get("retrieval_ir")
    if isinstance(stored_ir, dict) and isinstance(legacy.get("retrieval_ir"), dict):
        for key, value in legacy["retrieval_ir"].items():
            if key in stored_ir and not _values_equal(stored_ir.get(key), value):
                drifts.append(
                    {"field": f"retrieval_ir.{key}", "stored": stored_ir.get(key), "recomputed": value}
                )
    return drifts


def _citation(case):
    evidence_ids = {str(row["id"]) for row in case.get("evidence") or [] if row.get("id") is not None}
    citations = [str(item) for item in case.get("citations") or []]
    subset = None
    if evidence_ids or not citations:
        subset = set(citations) <= evidence_ids
    return {
        "ids_subset_of_evidence": subset,
        "faithful": "unknown",
        "note": "An evidence-id subset is not a faithful citation.",
    }


def build_record(case, question, *, manifest, annotations, k_values, top_k):
    coverage = _coverage(question, case.get("evidence") or [])
    relevant = [row for row in annotations if _annotation_key(row, case)]
    _apply_roles(coverage, relevant)
    legacy = _legacy(case, question, k_values)
    return {
        "question_id": case.get("id"),
        "strategy": case.get("strategy"),
        "answerable": None if question is None else bool(question.get("answerable", True)),
        "legacy": legacy,
        "legacy_drift": _legacy_diff(case, legacy),
        "coverage": coverage,
        "satisfiability": _satisfiability(question, case.get("evidence") or [], manifest, top_k),
        "quality": _quality(case, annotations),
        "citation": _citation(case),
    }


def _summary(records):
    strategies = {}
    for row in records:
        bucket = strategies.setdefault(
            row["strategy"],
            {
                "records": 0,
                "answerable": 0,
                "unanswerable": 0,
                "question_missing": 0,
                "legacy_support_hit": 0,
                "legacy_support_denominator": 0,
                "coverage_and_hit": 0,
                "coverage_denominator": 0,
                "span_micro_matched": 0,
                "span_micro_total": 0,
                "span_macro_sum": 0.0,
                "span_macro_denominator": 0,
                "empty_support": 0,
                "refusal_marker": 0,
                "legacy_drift": 0,
                "quality_human": {},
                "quality_unknown": 0,
                "structural_false": 0,
                "structural_unknown": 0,
            },
        )
        bucket["records"] += 1
        if row["answerable"] is True:
            bucket["answerable"] += 1
        elif row["answerable"] is False:
            bucket["unanswerable"] += 1
        else:
            bucket["question_missing"] += 1
        legacy_hit = row["legacy"]["retrieval_support_hit"]
        if row["answerable"] is True and legacy_hit is not None:
            bucket["legacy_support_denominator"] += 1
            bucket["legacy_support_hit"] += int(bool(legacy_hit))
        coverage = row["coverage"]
        if coverage["status"] == "scored" and coverage["total"]:
            bucket["coverage_denominator"] += 1
            bucket["coverage_and_hit"] += int(bool(coverage["and_hit"]))
            bucket["span_micro_matched"] += coverage["matched"]
            bucket["span_micro_total"] += coverage["total"]
            bucket["span_macro_sum"] += coverage["matched"] / coverage["total"]
            bucket["span_macro_denominator"] += 1
        elif coverage["status"] == "empty_gold":
            bucket["empty_support"] += 1
        if row["quality"]["refusal_marker"]:
            bucket["refusal_marker"] += 1
        if row["legacy_drift"]:
            bucket["legacy_drift"] += 1
        if row["quality"]["method"] == "annotation_human":
            label = row["quality"]["class"]
            bucket["quality_human"][label] = bucket["quality_human"].get(label, 0) + 1
        else:
            bucket["quality_unknown"] += 1
        structural = row["satisfiability"]["structural_at_k"]
        if structural is False:
            bucket["structural_false"] += 1
        elif structural == "unknown":
            bucket["structural_unknown"] += 1
    for bucket in strategies.values():
        micro_total = bucket["span_micro_total"]
        macro_count = bucket["span_macro_denominator"]
        macro_sum = bucket.pop("span_macro_sum")
        bucket["span_micro"] = (bucket["span_micro_matched"] / micro_total) if micro_total else None
        bucket["span_macro"] = (macro_sum / macro_count) if macro_count else None
    return strategies


def _numeric(value):
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _stored_values(cases, reader):
    found = []
    for case in cases:
        value = _numeric(reader(case))
        if value is not None:
            found.append(value)
    count = len(cases)
    if not found:
        return {"status": "NOT_VERIFIED", "value": None, "n_measured": 0, "n_records": count, "source": None}
    status = "measured" if len(found) == count else "partial"
    return {
        "status": status,
        "value": sum(found) / len(found),
        "n_measured": len(found),
        "n_records": count,
        "source": "stored_report",
    }


def _answer_elapsed(case):
    metrics = case.get("answer_metrics")
    if not isinstance(metrics, dict):
        return None
    return metrics.get("elapsed_ms")


def _answer_tokens(case):
    metrics = case.get("answer_metrics")
    if not isinstance(metrics, dict):
        return None
    usage = metrics.get("usage")
    if not isinstance(usage, dict):
        return None
    return usage.get("total_tokens")


def _integration_baseline(cases, summary):
    """Offline baseline for a later retrieval/answer integration.

    Quality comes from this recompute. Latency and token counts are copied only
    when the saved report already has them. Cost is never inferred from tokens.
    """
    return {
        "role": "offline_baseline",
        "live_comparison": "NOT_VERIFIED",
        "quality": {
            "status": "measured",
            "source": "saved_report_recompute",
            "by_strategy": summary,
        },
        "retrieval_latency_ms": _stored_values(cases, lambda case: case.get("retrieval_ms")),
        "answer_latency_ms": _stored_values(cases, _answer_elapsed),
        "answer_tokens": _stored_values(cases, _answer_tokens),
        "cost": _stored_values(cases, lambda case: case.get("cost")),
        "note": "No paid Live call is made here. Stored figures are not a model before/after gain.",
    }


def build_report(report, cases, questions, *, manifest, annotations, inputs):
    k_values = tuple(report.get("metric_k") or (1, 3, 6, 10))
    top_k = report.get("top_k")
    records = [
        build_record(
            case,
            questions.get(case.get("id")),
            manifest=manifest,
            annotations=annotations,
            k_values=k_values,
            top_k=top_k,
        )
        for case in cases
    ]
    summary = _summary(records)
    baseline = _integration_baseline(cases, summary)
    report_cost = _numeric(report.get("cost"))
    if baseline["cost"]["status"] == "NOT_VERIFIED" and report_cost is not None:
        baseline["cost"] = {
            "status": "measured",
            "value": report_cost,
            "n_measured": 1,
            "n_records": 1,
            "source": "stored_report",
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "provenance": {
            "inputs": inputs,
            "offline": True,
            "model_calls": 0,
            "network": False,
        },
        "limitations": LIMITATIONS,
        "gate": {
            "eligible_for_default": False,
            "manual_review_complete": False,
            "pending_review": True,
            "reason": "V2 records annotations and does not release an unreviewed gate.",
        },
        "integration_baseline": baseline,
        "summary": summary,
        "records": records,
    }


def _input_record(role, path):
    if path is None:
        return {"role": role, "path": None, "sha256": None, "present": False}
    file = Path(path)
    return {
        "role": role,
        "path": file.as_posix(),
        "sha256": file_sha256(file),
        "present": True,
    }


def evaluate_paths(report_path, questions_path, *, manifest_path=None, annotations_path=None):
    report, cases = load_report(report_path)
    questions = load_questions(questions_path)
    manifest = load_manifest(manifest_path)
    annotations = load_annotations(annotations_path)
    inputs = [
        _input_record("report", report_path),
        _input_record("questions", questions_path),
        _input_record("manifest", manifest_path),
        _input_record("annotations", annotations_path),
    ]
    return build_report(report, cases, questions, manifest=manifest, annotations=annotations, inputs=inputs)


def write_outputs(report, output_dir):
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_csv(report, destination / "records.csv")
    (destination / "summary.md").write_text(_markdown(report), encoding="utf-8")
    return destination


def _write_csv(report, path):
    fields = [
        "question_id",
        "strategy",
        "answerable",
        "legacy_support_hit",
        "coverage_matched",
        "coverage_total",
        "coverage_and_hit",
        "coverage_status",
        "satisfiability_scope",
        "satisfiability_algorithm",
        "satisfiability_lower",
        "satisfiability_upper",
        "satisfiability_minimum",
        "structural_at_k",
        "quality_class",
        "quality_method",
        "ai_annotation_label",
        "refusal_marker",
        "legacy_drift",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in report["records"]:
            writer.writerow(
                {
                    "question_id": row["question_id"],
                    "strategy": row["strategy"],
                    "answerable": row["answerable"],
                    "legacy_support_hit": row["legacy"]["retrieval_support_hit"],
                    "coverage_matched": row["coverage"]["matched"],
                    "coverage_total": row["coverage"]["total"],
                    "coverage_and_hit": row["coverage"]["and_hit"],
                    "coverage_status": row["coverage"]["status"],
                    "satisfiability_scope": row["satisfiability"]["scope"],
                    "satisfiability_algorithm": row["satisfiability"]["algorithm"],
                    "satisfiability_lower": row["satisfiability"]["lower"],
                    "satisfiability_upper": row["satisfiability"]["upper"],
                    "satisfiability_minimum": row["satisfiability"]["minimum"],
                    "structural_at_k": row["satisfiability"]["structural_at_k"],
                    "quality_class": row["quality"]["class"],
                    "quality_method": row["quality"]["method"],
                    "ai_annotation_label": row["quality"]["ai_annotation_label"],
                    "refusal_marker": row["quality"]["refusal_marker"],
                    "legacy_drift": len(row["legacy_drift"]),
                }
            )


def _markdown(report):
    lines = [
        "# Evaluation V2",
        "",
        f"Schema `{report['schema_version']}`. Offline, no model calls.",
        "",
        "The unreviewed gate stays closed: `eligible_for_default` is false.",
        "",
        "| strategy | records | answerable | legacy support | coverage AND | refusal substring | drift | quality unknown |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for strategy, row in report["summary"].items():
        lines.append(
            "| {strategy} | {records} | {answerable} | {hit}/{den} | {cover}/{cden} | {refusal} | {drift} | {unknown} |".format(
                strategy=strategy,
                records=row["records"],
                answerable=row["answerable"],
                hit=row["legacy_support_hit"],
                den=row["legacy_support_denominator"],
                cover=row["coverage_and_hit"],
                cden=row["coverage_denominator"],
                refusal=row["refusal_marker"],
                drift=row["legacy_drift"],
                unknown=row["quality_unknown"],
            )
        )
    lines.extend(
        [
            "",
            "Span coverage uses answerable questions with support. "
            "Empty support and unanswerable rows are outside both denominators.",
            "",
            "| strategy | span micro | span macro | macro questions | empty support | unanswerable excluded |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for strategy, row in report["summary"].items():
        micro = (
            f"{row['span_micro_matched']}/{row['span_micro_total']}"
            if row["span_micro_total"]
            else "-"
        )
        macro = "-" if row["span_macro"] is None else f"{row['span_macro']:.4f}"
        lines.append(
            "| {strategy} | {micro} | {macro} | {macro_n} | {empty} | {unanswerable} |".format(
                strategy=strategy,
                micro=micro,
                macro=macro,
                macro_n=row["span_macro_denominator"],
                empty=row["empty_support"],
                unanswerable=row["unanswerable"],
            )
        )
    baseline = report["integration_baseline"]
    lines.extend(
        [
            "",
            "## Integration baseline",
            "",
            f"Live comparison: {baseline['live_comparison']}.",
            f"Retrieval latency: {baseline['retrieval_latency_ms']['status']}.",
            f"Answer latency: {baseline['answer_latency_ms']['status']}.",
            f"Cost: {baseline['cost']['status']}.",
            "",
        ]
    )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    lines.append("")
    return "\n".join(lines)
