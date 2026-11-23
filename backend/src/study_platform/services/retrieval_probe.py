"""Offline retrieval probes that do not call a model API.

Line-chunk set cover is exact when every span sits in one chunk. Lexical figures
are token-overlap ranks, not production ``ts_rank`` / IDF / RRF. Vector rank,
rerank and rewrite quality stay ``NOT_VERIFIED`` here.
"""

import hashlib

from study_platform.services.chunking import semantic_chunks
from study_platform.services.lexical import lexical_index_text, lexical_query_terms
from study_platform.services.rag_evaluation import normalized

HIGH_AND_IDS = (
    "REAL-027",
    "REAL-028",
    "REAL-029",
    "REAL-031",
    "REAL-032",
    "REAL-034",
    "REAL-036",
    "REAL-040",
    "REAL-041",
)


def physical_lines(text):
    if text.startswith("\ufeff"):
        text = text[1:]
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped:
            yield stripped


def line_chunks(lines):
    chunks = []
    multi = 0
    for line in lines:
        parts = [part for part in semantic_chunks(line) if part]
        if len(parts) != 1:
            multi += 1
        chunks.extend(parts)
    return chunks, multi


def document_chunks(lines):
    text = "\n".join(lines)
    return [part for part in semantic_chunks(text) if part]


def cover_from_owners(owners):
    found = sum(bool(hits) for hits in owners)
    if not owners or found != len(owners):
        return {"in_chunk": found, "min_chunks": None, "exact": False}
    if all(len(hits) == 1 for hits in owners):
        return {"in_chunk": found, "min_chunks": len({hits[0] for hits in owners}), "exact": True}
    chunks = sorted({hit for hits in owners for hit in hits})
    best = None
    if len(chunks) <= 20:
        covers = [0] * len(chunks)
        index = {chunk: position for position, chunk in enumerate(chunks)}
        for span_index, hits in enumerate(owners):
            for hit in hits:
                covers[index[hit]] |= 1 << span_index
        best = _chunk_mask_cover(covers, (1 << len(owners)) - 1)
    elif len(owners) <= 24:
        best = _span_mask_cover(owners)
    if best is None:
        return {"in_chunk": found, "min_chunks": None, "exact": False}
    return {"in_chunk": found, "min_chunks": best, "exact": True}


def _chunk_mask_cover(covers, full):
    best = None
    width = len(covers)
    for mask in range(1, 1 << width):
        covered = 0
        count = 0
        for position in range(width):
            if mask & (1 << position):
                covered |= covers[position]
                count += 1
                if best is not None and count >= best:
                    break
        else:
            if covered == full and (best is None or count < best):
                best = count
    return best


def _span_mask_cover(owners):
    width = len(owners)
    seen = set()
    masks = []
    for span_index, hits in enumerate(owners):
        for hit in hits:
            masks.append((hit, span_index))
    by_chunk = {}
    for hit, span_index in masks:
        by_chunk[hit] = by_chunk.get(hit, 0) | (1 << span_index)
    unique_masks = []
    for mask in by_chunk.values():
        if mask not in seen:
            seen.add(mask)
            unique_masks.append(mask)
    full = (1 << width) - 1
    dp = [width + 1] * (full + 1)
    dp[0] = 0
    for mask in unique_masks:
        for state in range(full, -1, -1):
            current = dp[state]
            if current > width:
                continue
            nxt = state | mask
            cost = current + 1
            if cost < dp[nxt]:
                dp[nxt] = cost
    if dp[full] > width:
        return None
    return dp[full]


def min_chunk_cover(spans, chunks):
    norms = [normalized(chunk) for chunk in chunks]
    owners = []
    for span in spans:
        needle = normalized(span)
        owners.append([index for index, norm in enumerate(norms) if needle and needle in norm])
    return cover_from_owners(owners)


def lexical_coverage(question, spans, chunks, ks):
    terms = set(lexical_query_terms(question))
    prepared = [
        {"norm": normalized(chunk), "tokens": set(lexical_index_text(chunk).split())} for chunk in chunks
    ]
    ranked = sorted(range(len(prepared)), key=lambda index: (-len(terms & prepared[index]["tokens"]), index))
    needles = [normalized(span) for span in spans]
    hits = {}
    for k in ks:
        top = ranked[:k]
        hits[k] = sum(
            1
            for needle in needles
            if needle and any(needle in prepared[index]["norm"] for index in top)
        )
    return hits


def synthetic_probe():
    """23 one-line spans, the same shape as the high-AND lower bound, with no standard text."""
    target = [f"widget-policy rule {i:02d} allows only listed field {i:02d}" for i in range(23)]
    noise = [f"unrelated catalog entry {i:02d} describes hardware" for i in range(40)]
    target_chunks, multi = line_chunks(target)
    noise_chunks, _noise_multi = line_chunks(noise)
    cover = min_chunk_cover(target, target_chunks)
    packed = document_chunks(target)
    packed_cover = min_chunk_cover(target, packed)
    lexical = lexical_coverage(
        "widget-policy listed field", target, target_chunks + noise_chunks, (10, 24)
    )
    minimum = cover["min_chunks"]
    return {
        "line_chunks": len(target_chunks),
        "line_multi": multi,
        "span_count": len(target),
        "min_chunks": minimum,
        "exact": cover["exact"],
        "and_possible_at_10": minimum is not None and minimum <= 10,
        "and_possible_at_24": minimum is not None and minimum <= 24,
        "packed_chunks": len(packed),
        "packed_min_chunks": packed_cover["min_chunks"],
        "lexical_hits_at_10": lexical[10],
        "lexical_hits_at_24": lexical[24],
        "lexical_method": "token_overlap",
        "vector_ranking": "NOT_VERIFIED",
        "rerank": "NOT_VERIFIED",
        "rewrite_quality": "NOT_VERIFIED",
        "decisions": {
            "chunking_default": "KEEP",
            "top_k_default": "KEEP",
            "lexical_weight_default": "KEEP",
            "hybrid_with_weight_zero": "INVALID_COMPARISON",
        },
    }


def prepare_corpus(documents):
    rows = []
    counts = {}
    multi = {}
    for doc_id in sorted(documents):
        chunks, extra = line_chunks(list(physical_lines(documents[doc_id])))
        counts[doc_id] = len(chunks)
        multi[doc_id] = extra
        for ordinal, chunk in enumerate(chunks):
            rows.append(
                {
                    "doc": doc_id,
                    "ordinal": ordinal,
                    "norm": normalized(chunk),
                    "tokens": set(lexical_index_text(chunk).split()),
                }
            )
    return {"rows": rows, "counts": counts, "multi": multi}


def probe_cases(cases, corpus, ks=(10, 24)):
    """Return per-question counts only. Question text and quotes are not copied out."""
    rows = corpus["rows"]
    results = []
    for case in cases:
        if not case.get("answerable"):
            continue
        spans = []
        for support in case.get("support") or []:
            quote = support.get("quote")
            texts = quote if isinstance(quote, list) else [quote]
            for text in texts:
                if isinstance(text, str):
                    spans.append({"doc": support.get("document"), "needle": normalized(text)})
        owners = []
        for span in spans:
            hits = [
                (row["doc"], row["ordinal"])
                for row in rows
                if row["doc"] == span["doc"] and span["needle"] and span["needle"] in row["norm"]
            ]
            owners.append(hits)
        cover = cover_from_owners(owners)
        in_chunk = cover["in_chunk"]
        exact = cover["exact"]
        minimum = cover["min_chunks"]
        terms = set(lexical_query_terms(case.get("question") or ""))
        ranked = sorted(rows, key=lambda row: (-len(terms & row["tokens"]), row["doc"], row["ordinal"]))
        gold_docs = {span["doc"] for span in spans}
        sharing = 0
        for span in spans:
            if terms and any(
                row["doc"] == span["doc"]
                and span["needle"]
                and span["needle"] in row["norm"]
                and (row["tokens"] & terms)
                for row in rows
            ):
                sharing += 1
        lexical = {}
        for k in ks:
            top = ranked[:k]
            lexical[k] = {
                "span_hits": sum(
                    1
                    for span in spans
                    if any(
                        span["needle"] and span["needle"] in row["norm"] and row["doc"] == span["doc"]
                        for row in top
                    )
                ),
                "doc_hit": any(row["doc"] in gold_docs for row in top),
            }
        results.append(
            {
                "id": case.get("id"),
                "spans": len(spans),
                "in_chunk": in_chunk,
                "min_chunks": minimum,
                "exact": exact,
                "lexical_hits_at_10": lexical[10]["span_hits"],
                "lexical_hits_at_24": lexical[24]["span_hits"],
                "lexical_doc_hit_at_10": lexical[10]["doc_hit"],
                "lexical_doc_hit_at_24": lexical[24]["doc_hit"],
                "spans_sharing_query_term": sharing,
            }
        )
    return results


def file_sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expected_sha256(item):
    return item.get("sha256") or item.get("sha256_expected_from_eval_report")


def load_source_corpus(manifest, sources_dir):
    """Load hash-matched UTF-8 sources. A mismatched file is recorded and excluded."""
    documents = {}
    files = []
    for item in manifest["files"]:
        expected = expected_sha256(item)
        path = sources_dir / item["filename"]
        record = {"id": item["id"], "used": False, "status": "NOT_RUN"}
        if not path.is_file():
            files.append(record)
            continue
        digest = file_sha256(path)
        record["sha256"] = digest
        if digest != expected:
            record["status"] = "FAIL"
            files.append(record)
            continue
        if not item["filename"].endswith(".txt"):
            record["status"] = "NOT_VERIFIED"
            files.append(record)
            continue
        documents[item["id"]] = path.read_text(encoding="utf-8")
        record["status"] = "PASS"
        record["used"] = True
        files.append(record)
    return documents, files
