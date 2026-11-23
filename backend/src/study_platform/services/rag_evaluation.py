"""Fact-group evaluation and review gates; no LLM-as-judge."""

import hashlib
import json
import re
import time
import unicodedata
from dataclasses import asdict
from pathlib import Path

from study_platform.services import chat
from study_platform.services.models import chat_model, embed_texts
from study_platform.services.query_rewrite import rewrite_query
from study_platform.services.retrieval_metrics import (
    aggregate_by_knowledge_base,
    evaluate_ranking,
)

DEFAULT_METRIC_K = (1, 3, 6, 10)


def normalized(text):
    return re.sub(r"[^\w]", "", unicodedata.normalize("NFKC", text).casefold())


def supporting_quotes(support):
    value = support["quote"]
    return value if isinstance(value, list) else [value]


def proof_text(text):
    """Keep comparison operators in versioned evidence."""
    return re.sub(r"[^\w<>=≤≥≠+−%±×÷]", "", unicodedata.normalize("NFKC", text).casefold())


def alternatives(requirement):
    return requirement.get("alternatives", (requirement,))


def validate_support(support, material_ids, question):
    for span in alternatives(support):
        if span.get("document") not in material_ids:
            raise ValueError("Gold evidence must belong to the requested knowledge base")
        if "parts" in span:
            parts = span["parts"]
            if not isinstance(parts, list) or not parts:
                raise ValueError("Evidence alternative requires at least one part")
            for part in parts:
                loc = part.get("locator", {})
                if not isinstance(loc, dict) or set(loc) not in (
                    {"page"}, {"start_line", "end_line"}
                ) or any(type(n) is not int or n < 1 for n in loc.values()) or (
                    "end_line" in loc and loc["start_line"] > loc["end_line"]
                ):
                    raise ValueError("Evidence part requires a physical page or valid line range")
            quotes = [part.get("quote") for part in parts]
            norm = proof_text
        else:
            if "alternatives" in support:
                raise ValueError("An alternative needs anchored evidence parts")
            quotes = supporting_quotes(span)
            norm = normalized
        if not quotes or any(
            not isinstance(quote, str) or not norm(quote) or norm(quote) == norm(question)
            for quote in quotes
        ):
            raise ValueError("Use natural questions and separately labelled supporting passages")
    if not alternatives(support):
        raise ValueError("Evidence requirement needs at least one alternative")


def read_dataset(path):
    cases = [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    materials = json.loads(Path(path).with_name("materials.json").read_text(encoding="utf-8"))
    if len({item["id"] for item in materials}) != len(materials):
        raise ValueError("Material IDs must be unique")
    if any(item["format"] not in {"txt", "docx", "png", "jpeg", "pdf"} for item in materials):
        raise ValueError("Unsupported evaluation material format")
    libraries = {item["knowledge_base"] for item in materials}
    if not cases or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Dataset must contain unique case IDs")
    groups = {}
    for case in cases:
        if case["knowledge_base"] not in libraries:
            raise ValueError("Requested knowledge base has no evaluation materials")
        if case["split"] not in ("calibration", "heldout"):
            raise ValueError("Unknown split")
        group = case["fact_group"]
        if group in groups and groups[group] != case["split"]:
            raise ValueError("Paraphrases of one fact cannot cross evaluation splits")
        groups[group] = case["split"]
        if not isinstance(case["answerable"], bool) or not case["question"].strip():
            raise ValueError("Invalid answerability or question")
        if case["answerable"] and (not case["expected_facts"] or not case["support"]):
            raise ValueError("Answerable cases require facts and supporting passages")
        if any(not isinstance(aliases, list) or not aliases or any(
            not isinstance(alias, str) or not normalized(alias) for alias in aliases
        ) for aliases in case["expected_facts"]):
            raise ValueError("Each fact needs a nonempty normalized alias")
        if not case["answerable"] and (case["expected_facts"] or case["support"]):
            raise ValueError("Unanswerable cases cannot declare answer facts or mandatory support")
        material_ids = {row["id"] for row in materials if row["knowledge_base"] == case["knowledge_base"]}
        for support in case["support"]:
            validate_support(support, material_ids, case["question"])
    return cases, materials


def expected_documents(case):
    if not case["answerable"]:
        return set()
    return {alternatives(item)[0]["document"] for item in case["support"]}


def retrieved_documents(evidence, documents):
    return [documents[str(row["material_version_id"])] for row in evidence]


def same_locator(actual, expected):
    if "page" in expected:
        return actual.get("page") == expected["page"]
    start = actual.get("start_line", actual.get("line"))
    end = actual.get("end_line", actual.get("line"))
    return (
        isinstance(start, int) and isinstance(end, int)
        and start <= expected["end_line"] and end >= expected["start_line"]
    )


def support_witness(case, evidence, documents):
    """Return one sufficient set of positions per proposition, or None if absent."""
    by_document = {}
    for index, row in enumerate(evidence):
        document = documents[str(row["material_version_id"])]
        by_document.setdefault(document, []).append((index, row))
    witnessed = []
    for requirement in case["support"]:
        choices = []
        for span in alternatives(requirement):
            candidates = by_document.get(span["document"], ())
            if "parts" not in span:
                quotes = tuple(map(normalized, supporting_quotes(span)))
                match = next((i for i, row in candidates if any(
                    quote and quote in normalized(row["text"]) for quote in quotes
                )), None)
                if match is not None:
                    choices.append({match})
                continue
            positions = []
            for part in span["parts"]:
                quote = proof_text(part["quote"])
                match = next((i for i, row in candidates if (
                    same_locator(row.get("locator", {}), part["locator"]) and quote in proof_text(row["text"])
                )), None)
                if match is None:
                    break
                positions.append(match)
            else:
                choices.append(set(positions))
        witnessed.append(min(choices, key=len) if choices else None)
    return witnessed


def support_hit(case, evidence, documents):
    return all(part is not None for part in support_witness(case, evidence, documents))


async def evaluate_case(
    case,
    scope,
    documents,
    strategy,
    owner,
    k_values=DEFAULT_METRIC_K,
    retrieval_only=False,
):
    """Strategies share the same original query vector and index snapshot."""
    rewritten = None
    if strategy in ("rewrite", "compare", "three_way") and scope["index_version_ids"]:
        rewritten = await rewrite_query(case["question"])
    queries = [case["question"]]
    if rewritten and rewritten.query:
        queries.append(rewritten.query)
    embedding = {"calls": 0, "input_count": 0, "usage": None}
    vectors = embed_texts(queries, diagnostics=embedding) if scope["index_version_ids"] else []
    results = []
    has_anchored_support = any("alternatives" in item for item in case["support"])
    if strategy == "compare":
        strategy_names = ["baseline", "rewrite"]
    elif strategy == "three_way":
        strategy_names = ["baseline", "hybrid", "rewrite"]
    elif strategy == "hybrid_compare":
        strategy_names = ["baseline", "hybrid"]
    else:
        strategy_names = [strategy]
    for name in strategy_names:
        started = time.monotonic()
        if name == "hybrid":
            hybrid_scope = scope
            if strategy == "three_way":
                hybrid_scope = {**scope, "retrieval_settings": {
                    "lexical_weight": 0.4, "top_k": 10, "min_similarity": 0.35, "rerank_enabled": False,
                }}
            evidence = chat.retrieve_hybrid(hybrid_scope, [case["question"]], vectors[:1], owner)
        elif name == "baseline":
            evidence = chat.retrieve_vectors(scope, vectors[:1], owner)
        else:
            evidence = chat.retrieve_vectors(scope, vectors, owner)
        retrieval_ms = (time.monotonic() - started) * 1000
        witnessed = support_witness(case, evidence, documents) if case["answerable"] else []
        supported = all(part is not None for part in witnessed) if case["answerable"] else None
        reciprocal_rank = 0
        for rank in range(1, len(evidence) + 1):
            if supported and support_hit(case, evidence[:rank], documents):
                reciprocal_rank = 1 / rank
                break
        retrieved = retrieved_documents(evidence, documents)
        retrieval_ir = evaluate_ranking(retrieved, expected_documents(case), k_values)
        usage, answer_error = None, None
        calls = 0
        answer_ms = 0.0
        answer = ""
        citations, citation_valid = [], True
        if not retrieval_only:
            started = time.monotonic()
            if not evidence:
                answer = "当前资料不足，无法根据所选知识库回答。请补充相关资料或调整提问。"
            else:
                calls = 1
                try:
                    reply = await chat_model().ainvoke(
                        chat.prompt(
                            {**scope, "content": case["question"]},
                            evidence,
                            [],
                        )
                    )
                    answer, usage = reply.content, reply.usage_metadata
                    if not isinstance(answer, str):
                        raise ValueError("Non-text answer")
                except Exception as error:
                    answer, answer_error = "", type(error).__name__
            answer_ms = (time.monotonic() - started) * 1000
            try:
                citations = chat.cited_chunks(answer, evidence) if not answer_error else []
                citation_valid = not answer_error
            except Exception:
                citations, citation_valid = [], False
        results.append(
            {
                "id": case["id"],
                "strategy": name,
                "fact_group": case["fact_group"],
                "split": case["split"],
                "question": case["question"],
                "knowledge_base": case["knowledge_base"],
                "answerable": case["answerable"],
                "expected_facts": case["expected_facts"],
                "support": case["support"],
                "retrieval_support_hit": supported,
                "evidence_coverage": (
                    sum(part is not None for part in witnessed) / len(witnessed) if witnessed else None
                ),
                "document_role_coverage": (
                    sum(any(span["document"] in retrieved for span in alternatives(item))
                        for item in case["support"]) / len(case["support"]) if case["answerable"] else None
                ),
                "reciprocal_rank": reciprocal_rank,
                "retrieval_ir": retrieval_ir,
                "retrieved_documents": retrieved,
                "scope_valid": all(
                    str(row["index_version_id"]) in scope["index_version_ids"] for row in evidence
                ),
                "retrieval_ms": retrieval_ms,
                "evidence_characters": sum(len(c["text"]) for c in evidence),
                "evidence": [
                    {**row, "document": documents[str(row["material_version_id"])]}
                    for row in evidence
                ],
                "answer": answer,
                "answer_error": answer_error,
                "citation_valid": citation_valid,
                "citations": [str(c["id"]) for c in citations],
                "fact_aliases_found": all(
                    any(normalized(alias) in normalized(answer) for alias in aliases)
                    for aliases in case["expected_facts"]
                ) if case["answerable"] and not has_anchored_support and not retrieval_only and not answer_error else None,
                "refusal_marker": None if retrieval_only or answer_error else "资料不足" in answer,
                "answer_metrics": {"calls": calls, "elapsed_ms": answer_ms, "usage": usage},
                "rewrite_metrics": asdict(rewritten) if rewritten and name == "rewrite" else None,
                "shared_embedding_metrics": embedding,
            }
        )
    return results


def report_digest(report):
    return hashlib.sha256(
        json.dumps(report, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def review_template(report):
    return {
        "report_sha256": report_digest(report),
        "reviewer": "",
        "human_confirmed": False,
        "cases": [
            {
                "id": row["id"],
                "strategy": row["strategy"],
                "fact_correct": None,
                "citations_supported": None,
            }
            for row in report["cases"]
        ],
    }


def quality_gate(report, review=None):
    rows = {(row["id"], row["strategy"]): row for row in report["cases"]}
    expected_ids = report.get("expected_case_ids", [])
    expected_pairs = {(case_id, strategy) for case_id in expected_ids
                      for strategy in ("baseline", "rewrite")}
    complete_comparison = (
        report.get("status") == "completed" and report.get("strategy") == "compare"
        and bool(expected_ids) and len(expected_pairs) == 2 * len(expected_ids)
        and len(rows) == len(report["cases"]) and set(rows) == expected_pairs
    )
    verdicts = {}
    if review:
        if review.get("report_sha256") != report_digest(report):
            raise ValueError("Review belongs to another report")
        verdicts = {(row["id"], row["strategy"]): row for row in review["cases"]}
        if len(verdicts) != len(review["cases"]) or set(verdicts) != set(rows):
            raise ValueError("Review must cover exactly the evaluated cases")
    reviewed = (
        bool(verdicts)
        and review.get("human_confirmed") is True
        and bool(str(review.get("reviewer", "")).strip())
        and all(
            type(item.get(field)) is bool
            for item in verdicts.values()
            for field in ("fact_correct", "citations_supported")
        )
    )
    # Automatic checks are evidence for review, not a claim of semantic correctness.
    passed = {
        key: bool(
            reviewed
            and verdicts[key]["fact_correct"]
            and verdicts[key]["citations_supported"]
            and not row["answer_error"]
            and row["citation_valid"]
        )
        for key, row in rows.items()
    }
    groups = {}
    for key, row in rows.items():
        if row["split"] == "heldout" and row["answerable"]:
            groups.setdefault((row["fact_group"], row["strategy"]), []).append(
                bool(row["retrieval_support_hit"])
            )
    hits = {key: all(values) for key, values in groups.items()}
    additions = sorted(
        group
        for group, strategy in hits
        if strategy == "rewrite"
        and hits[(group, strategy)]
        and not hits.get((group, "baseline"), True)
    )
    regressions = [
        key[0]
        for key in rows
        if key[1] == "baseline" and passed[key] and not passed.get((key[0], "rewrite"), False)
    ]
    lost_groups = sorted(
        group
        for group, strategy in hits
        if strategy == "baseline"
        and hits[(group, strategy)]
        and not hits.get((group, "rewrite"), False)
    )
    safety_failures = [
        key[0]
        for key, row in rows.items()
        if key[1] == "rewrite"
        and (
            not row["scope_valid"]
            or not row["citation_valid"]
            or row["answer_error"]
            or (reviewed and not verdicts[key]["citations_supported"])
            or (reviewed and not row["answerable"] and not verdicts[key]["fact_correct"])
        )
    ]
    safe = (reviewed and complete_comparison and not regressions and not lost_groups
            and not safety_failures)
    return {
        "complete_comparison": complete_comparison,
        "manual_review_complete": reviewed,
        "pending_review": not reviewed,
        "non_regression_passed": safe,
        "heldout_added_groups": additions,
        "heldout_lost_groups": lost_groups,
        "answer_regressions": regressions,
        "safety_failures": safety_failures,
        "eligible_for_default": bool(safe and additions),
    }


def summarize_retrieval(report, k_values=DEFAULT_METRIC_K):
    rows = []
    for row in report.get("cases", []):
        metrics = row.get("retrieval_ir")
        if metrics is None:
            continue
        rows.append({**metrics, "knowledge_base": row["knowledge_base"]})
    if not rows:
        return None
    return {"k_values": list(k_values), "by_scope": aggregate_by_knowledge_base(rows, k_values)}


def format_retrieval_table(retrieval_summary):
    lines = ["scope | n_scored | mrr | " + " | ".join(
        f"R@{k} nDCG@{k} H@{k}" for k in retrieval_summary["k_values"]
    )]
    for scope, values in retrieval_summary["by_scope"].items():
        parts = [scope, str(values["n_scored"]), _format_float(values.get("mrr"))]
        for k in retrieval_summary["k_values"]:
            parts.extend(
                [
                    _format_float(values.get(f"recall@{k}")),
                    _format_float(values.get(f"ndcg@{k}")),
                    _format_float(values.get(f"hit@{k}")),
                ]
            )
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def _format_float(value):
    return "-" if value is None else f"{value:.4f}"


def summarize(report):
    k_values = tuple(report.get("metric_k") or DEFAULT_METRIC_K)
    summary = {"retrieval": summarize_retrieval(report, k_values)}
    for strategy in sorted({row["strategy"] for row in report["cases"]}):
        rows = [row for row in report["cases"] if row["strategy"] == strategy]
        groups = {}
        for row in rows:
            if row["answerable"]:
                groups.setdefault((row["split"], row["fact_group"]), []).append(row)
        splits = {}
        for split in ("calibration", "heldout"):
            subset = [values for (name, _), values in groups.items() if name == split]
            splits[split] = {
                "answerable_groups": len(subset),
                "all_paraphrases_hit_groups": sum(
                    all(row["retrieval_support_hit"] for row in values) for values in subset
                ),
                "group_mean_reciprocal_rank": (
                    sum(
                        sum(row["reciprocal_rank"] for row in values) / len(values)
                        for values in subset
                    )
                    / len(subset)
                )
                if subset
                else None,
            }
        rewrite = [row["rewrite_metrics"] for row in rows if row["rewrite_metrics"]]
        evidence_coverage = [row["evidence_coverage"] for row in rows if row.get("evidence_coverage") is not None]
        role_coverage = [row["document_role_coverage"] for row in rows if row.get("document_role_coverage") is not None]
        summary[strategy] = {
            "splits": splits,
            "questions": len(rows),
            "answer_calls": sum(row["answer_metrics"]["calls"] for row in rows),
            "rewrite_calls": len(rewrite),
            "rewrite_fallbacks": sum(item["query"] is None for item in rewrite),
            "model_errors": sum(bool(row["answer_error"]) for row in rows),
            "invalid_citations": sum(not row["citation_valid"] for row in rows),
            "scope_violations": sum(not row["scope_valid"] for row in rows),
            "mean_evidence_coverage": sum(evidence_coverage) / len(evidence_coverage) if evidence_coverage else None,
            "mean_document_role_coverage": sum(role_coverage) / len(role_coverage) if role_coverage else None,
            "unanswerable_cases": sum(not row["answerable"] for row in rows),
            "refusal_markers_on_unanswerable": sum(
                row.get("refusal_marker") is True for row in rows if not row["answerable"]
            ),
            "mean_evidence_characters": sum(row["evidence_characters"] for row in rows) / len(rows),
            "known_answer_tokens": sum(
                (row["answer_metrics"]["usage"] or {}).get("total_tokens", 0) for row in rows
            ),
            "answer_usage_unknown": sum(
                row["answer_metrics"]["calls"] and row["answer_metrics"]["usage"] is None
                for row in rows
            ),
            "known_rewrite_tokens": sum(
                (item["usage"] or {}).get("total_tokens", 0) for item in rewrite
            ),
            "rewrite_usage_unknown": sum(item["usage"] is None for item in rewrite),
            "max_rewrite_ms": max((item["elapsed_ms"] for item in rewrite), default=None),
        }
    by_case = {(row["id"], row["strategy"]): row for row in report["cases"]}
    summary["empty_to_nonempty"] = sum(
        not row["evidence"] and bool(by_case.get((key, "rewrite"), {}).get("evidence"))
        for (key, strategy), row in by_case.items()
        if strategy == "baseline"
    )
    shared = {row["id"]: row["shared_embedding_metrics"] for row in report["cases"]}
    summary["shared_embedding"] = {
        "calls": sum(item["calls"] for item in shared.values()),
        "input_count": sum(item["input_count"] for item in shared.values()),
        "known_tokens": sum(
            (item["usage"] or {}).get("total_tokens", 0) for item in shared.values()
        ),
        "usage_unknown": sum(item["calls"] and item["usage"] is None for item in shared.values()),
    }
    return summary
