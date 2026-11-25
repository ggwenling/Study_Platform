"""Opt-in private cache and bounded retries for evaluation corpus embeddings.

Copying a source file is not an embedded batch. A batch counts only after the
provider returns a complete, valid vector list. Cached hits are not a new charge.
"""

import hashlib
import json
import math
import os
import stat
import time
from pathlib import Path
from uuid import uuid4

from study_platform.services.model_failure import allow_usage, public_failure
from study_platform.services.models import describe_model_failure, embed_texts

if os.name == "nt":
    from study_platform.services import _windows_cache_security

BATCH_LIMIT = 20
MAX_EXTRA_ATTEMPTS = 2
MAX_RETRY_AFTER_SECONDS = 30
CACHE_SCHEMA = 1


class CorpusEmbedError(Exception):
    def __init__(self, metrics, failure):
        self.metrics = metrics
        self.failure = public_failure(failure)
        self.indexed_documents = []
        super().__init__(self.failure.get("failure_kind") or "corpus_embedding")


class CacheError(Exception):
    def __init__(self, kind):
        self.failure = public_failure(
            {
                "failure_kind": kind if kind in {"cache_permissions", "cache_not_directory"} else "unknown",
                "exception_class": "CacheError",
                "error_code": kind,
                "usage_status": "unknown",
                "retryable": False,
            }
        )
        super().__init__(kind)


def provider_fingerprint(base_url):
    return hashlib.sha256(str(base_url).encode()).hexdigest()


def batch_identity(*, source_sha256, pipeline_profile, embedding_model, embedding_base_url, embedding_dim, texts):
    identity = {
        "schema": CACHE_SCHEMA,
        "source_sha256": source_sha256,
        "pipeline_profile": pipeline_profile,
        "embedding_model": embedding_model,
        "provider_fingerprint": provider_fingerprint(embedding_base_url),
        "embedding_profile": f"{embedding_model}:{embedding_dim}",
        "embedding_dim": embedding_dim,
        "text_sha256s": [hashlib.sha256(text.encode()).hexdigest() for text in texts],
    }
    key = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return key, identity


def vectors_ok(vectors, count, dim):
    if not isinstance(vectors, list) or len(vectors) != count:
        return False
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != dim or not any(vector):
            return False
        for item in vector:
            if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
                return False
    return True


def prepare_embedding_cache(directory):
    directory = Path(directory)
    if os.name == "nt":
        directory.parent.mkdir(parents=True, exist_ok=True)
        error = _windows_cache_security.directory_error(directory)
        if error:
            raise CacheError(error)
        return directory
    if directory.exists() or directory.is_symlink():
        _require_private_dir(directory)
        return directory
    directory.mkdir(parents=True, mode=0o700)
    os.chmod(directory, 0o700)
    _require_private_dir(directory)
    return directory


def _require_private_dir(directory):
    info = os.lstat(directory)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise CacheError("cache_not_directory")
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise CacheError("cache_permissions")


def _batch_dir(directory):
    path = Path(directory) / "batches"
    if os.name == "nt":
        error = _windows_cache_security.directory_error(path)
        if error:
            raise CacheError(error)
        return path
    if not path.exists():
        path.mkdir(mode=0o700)
        os.chmod(path, 0o700)
    _require_private_dir(path)
    return path


def _read_private(path):
    if os.name == "nt":
        return _windows_cache_security.read_private(path)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            return None
        chunks = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def load_cached_batch(directory, key, identity, count):
    raw = _read_private(_batch_dir(directory) / f"{key}.json")
    if raw is None:
        return None
    try:
        payload = json.loads(raw.decode())
    except (UnicodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("complete") is not True
        or payload.get("schema") != CACHE_SCHEMA
        or payload.get("identity") != identity
    ):
        return None
    if not vectors_ok(payload.get("vectors"), count, identity["embedding_dim"]):
        return None
    usage, status = allow_usage(payload.get("usage"))
    if payload.get("usage_status") == "known" and status != "known":
        return None
    elapsed = payload.get("elapsed_ms")
    if elapsed is not None and (
        isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed < 0
    ):
        return None
    return {
        "vectors": payload["vectors"],
        "usage": usage,
        "usage_status": status,
        "elapsed_ms": elapsed,
    }


def store_cached_batch(directory, key, identity, vectors, usage, usage_status, elapsed_ms):
    if not vectors_ok(vectors, len(identity["text_sha256s"]), identity["embedding_dim"]):
        raise CacheError("cache_not_directory")
    raw = json.dumps(
        {
            "schema": CACHE_SCHEMA,
            "complete": True,
            "identity": identity,
            "vectors": vectors,
            "usage": usage,
            "usage_status": usage_status,
            "elapsed_ms": elapsed_ms,
        },
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode() + b"\n"
    batches = _batch_dir(directory)
    destination = batches / f"{key}.json"
    temporary = batches / f".{key}.{uuid4().hex}.tmp"
    if os.name == "nt":
        descriptor = _windows_cache_security.open_new(temporary)
    else:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0), 0o600)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        os.write(descriptor, raw)
        os.fsync(descriptor)
    except Exception:
        os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise
    os.close(descriptor)
    try:
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    except PermissionError as exc:
        # A concurrent reader can prevent replacement on Windows. Keep an
        # already validated batch for this identity in that case.
        if (
            os.name != "nt" or getattr(exc, "winerror", None) not in (5, 32)
            or load_cached_batch(directory, key, identity, len(vectors)) is None
        ):
            raise
    finally:
        temporary.unlink(missing_ok=True)
    if os.name != "nt":
        os.chmod(destination, 0o600)


def _delay(failure, retry_index):
    """Wait only for a header that was already accepted as a 0–30 second delay."""
    if (
        failure.get("retry_after_state") == "stop"
        or failure.get("retry_after_exceeds")
        or failure.get("retry_after_unparsed")
    ):
        return None
    if failure.get("retry_after_state") == "wait":
        seconds = failure.get("retry_after_seconds")
        if type(seconds) is int and not isinstance(seconds, bool) and 0 <= seconds <= MAX_RETRY_AFTER_SECONDS:
            return seconds
        return None
    return min(2 ** (retry_index - 1), 4)


def _failure_from_call(exc, diagnostics, attempts):
    failure = diagnostics.get("failure") if isinstance(diagnostics, dict) else None
    if not isinstance(failure, dict):
        failure = describe_model_failure(exc)
    else:
        failure = dict(failure)
        if not failure.get("mapped_error_code"):
            failure["mapped_error_code"] = describe_model_failure(exc).get("mapped_error_code")
    failure["attempts"] = attempts
    if diagnostics.get("usage_status") == "known":
        failure["usage_status"] = "known"
    return public_failure(failure)


def _embed_once(texts, embed, pause):
    attempts = 0
    while attempts < 1 + MAX_EXTRA_ATTEMPTS:
        attempts += 1
        diagnostics = {"calls": 1, "input_count": len(texts), "usage": None, "usage_status": "unknown"}
        try:
            vectors = embed(texts, diagnostics=diagnostics)
        except Exception as exc:
            failure = _failure_from_call(exc, diagnostics, attempts)
            if attempts >= 1 + MAX_EXTRA_ATTEMPTS or not failure.get("retryable"):
                return None, failure, diagnostics, attempts
            delay = _delay(failure, attempts - 1)
            if delay is None:
                failure["retryable"] = False
                return None, public_failure(failure), diagnostics, attempts
            pause(delay)
            continue
        if not vectors_ok(vectors, len(texts), 1024):
            failure = describe_model_failure(
                ValueError("invalid embedding"), kind="invalid_value", http_status=None
            )
            failure["attempts"] = attempts
            failure["usage_status"] = diagnostics.get("usage_status") or "unknown"
            return None, public_failure(failure), diagnostics, attempts
        usage, status = allow_usage(diagnostics.get("usage"))
        diagnostics["usage"] = usage
        diagnostics["usage_status"] = status
        return vectors, None, diagnostics, attempts
    raise AssertionError("embedding retry loop exited without a result")


def embed_document_batches(
    texts,
    *,
    source_sha256,
    pipeline_profile,
    document_id,
    cache_dir,
    embedding_model,
    embedding_base_url,
    embedding_dim,
    pause=time.sleep,
    embed=None,
):
    """Embed texts in groups of 20. Only a validated provider result is stored."""
    embed = embed_texts if embed is None else embed
    if cache_dir is not None:
        prepare_embedding_cache(cache_dir)
    if embedding_dim != 1024:
        failure = describe_model_failure(ValueError("dimension"), kind="invalid_dimension")
        raise CorpusEmbedError([], failure)
    metrics = []
    vectors = []
    for offset in range(0, len(texts), BATCH_LIMIT):
        batch = texts[offset : offset + BATCH_LIMIT]
        key, identity = batch_identity(
            source_sha256=source_sha256,
            pipeline_profile=pipeline_profile,
            embedding_model=embedding_model,
            embedding_base_url=embedding_base_url,
            embedding_dim=embedding_dim,
            texts=batch,
        )
        cached = load_cached_batch(cache_dir, key, identity, len(batch)) if cache_dir is not None else None
        if cached is not None:
            metrics.append(
                _journal_row(
                    document_id,
                    offset,
                    batch,
                    outcome="cached",
                    calls=0,
                    usage=cached["usage"],
                    usage_status=cached["usage_status"],
                    origin="cache",
                    elapsed_ms=None,
                    cached_elapsed_ms=cached["elapsed_ms"],
                )
            )
            vectors.extend(cached["vectors"])
            continue
        embedded, failure, diagnostics, attempts = _embed_once(batch, embed, pause)
        if failure is not None:
            usage, status = allow_usage(diagnostics.get("usage"))
            metrics.append(
                _journal_row(
                    document_id,
                    offset,
                    batch,
                    outcome="failed",
                    calls=failure.get("attempts") or 1,
                    usage=usage,
                    usage_status=failure.get("usage_status") or status,
                    origin=None,
                    elapsed_ms=_elapsed(diagnostics),
                    failure=failure,
                )
            )
            raise CorpusEmbedError(metrics, failure)
        usage, status = allow_usage(diagnostics.get("usage"))
        elapsed = _elapsed(diagnostics)
        if cache_dir is not None:
            store_cached_batch(cache_dir, key, identity, embedded, usage, status, elapsed)
        metrics.append(
            _journal_row(
                document_id,
                offset,
                batch,
                outcome="embedded",
                calls=attempts,
                usage=usage,
                usage_status=status,
                origin="provider",
                elapsed_ms=elapsed,
                prior_attempt_cost="unknown" if attempts > 1 else "none",
            )
        )
        vectors.extend(embedded)
    return vectors, metrics


def _elapsed(diagnostics):
    value = diagnostics.get("elapsed_ms") if isinstance(diagnostics, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return value


def _journal_row(
    document_id,
    offset,
    batch,
    *,
    outcome,
    calls,
    usage,
    usage_status,
    origin,
    elapsed_ms,
    cached_elapsed_ms=None,
    failure=None,
    prior_attempt_cost=None,
):
    charge = {"embedded": True, "cached": False}.get(outcome)
    row = {
        "document": document_id,
        "batch_offset": offset,
        "input_count": len(batch),
        "batch_size_limit": BATCH_LIMIT,
        "outcome": outcome,
        "calls": calls,
        "new_corpus_charge": charge,
        "usage": usage,
        "usage_status": usage_status if usage_status in {"known", "unknown"} else "unknown",
        "usage_origin": origin,
        "elapsed_ms": elapsed_ms,
    }
    if outcome == "cached":
        row["cached_elapsed_ms"] = cached_elapsed_ms
    if prior_attempt_cost is not None:
        row["prior_attempt_cost"] = prior_attempt_cost
    if failure is not None:
        row["failure"] = failure
    return row


def _token_total(rows):
    if any(row.get("usage_status") != "known" for row in rows):
        return None
    return sum((row.get("usage") or {}).get("total_tokens", 0) for row in rows)


def summarize_corpus_usage(rows):
    """Currency stays unknown. Failed usage is not filled in as zero."""
    accepted = [row for row in rows if row.get("outcome") == "embedded"]
    cached = [row for row in rows if row.get("outcome") == "cached"]
    failed = [row for row in rows if row.get("outcome") == "failed"]
    failed_unknown = any(row.get("usage_status") != "known" for row in failed)
    retry_unknown = any(row.get("prior_attempt_cost") == "unknown" for row in accepted)
    return {
        "accepted_batches": len(accepted),
        "accepted_new_tokens": _token_total(accepted),
        "accepted_new_tokens_excludes_failures": True,
        "cached_batches": len(cached),
        "cached_new_corpus_charge": False,
        "cached_tokens_not_charged_again": _token_total(cached),
        "failed_batches": len(failed),
        "failed_cost": "unknown" if failed and failed_unknown else ("usage_returned" if failed else "none"),
        "retry_failure_cost": "unknown" if retry_unknown else "none",
        "partial": bool(
            failed_unknown or retry_unknown or any(row.get("usage_status") != "known" for row in accepted)
        ),
        "currency_total": "unknown",
    }


def _usage_row(usage, status):
    row_status = (usage or {}).get("cache_read_status", "unknown")
    miss_status = (usage or {}).get("cache_miss_status", "unknown")
    if status != "known":
        row_status = "unknown" if status != "not_called" else "not_called"
        miss_status = row_status
    return row_status, miss_status


def _rollup_numbers(rows, number_key, status_key, estimate_key=None):
    active = [row for row in rows if row.get("usage_status") != "not_called"]
    if not active:
        return {status_key: "not_called"}
    if status_key in {"cache_read_status", "cache_miss_status"} and any(
        (row.get("usage") or {}).get("input_status") == "inconsistent" for row in active
    ):
        return {status_key: "inconsistent"}
    statuses = {row.get(status_key, "unknown") for row in active}
    if statuses != {"known"} and not (estimate_key and statuses == {"estimate"}):
        if "inconsistent" in statuses:
            return {status_key: "inconsistent"}
        return {status_key: "unknown"}
    values = []
    source = estimate_key if estimate_key else number_key
    for row in active:
        usage = row.get("usage") or {}
        if source == "cache_read":
            details = usage.get("input_token_details")
            value = details.get("cache_read") if isinstance(details, dict) else None
        else:
            value = usage.get(source)
        if type(value) is not int:
            return {status_key: "unknown"}
        values.append(value)
    label = "estimate" if estimate_key else "known"
    reported_key = estimate_key or number_key
    return {status_key: label, reported_key: sum(values)}


def _token_status(rows, key):
    active = [row for row in rows if row.get("usage_status") != "not_called"]
    if not active:
        return "not_called", None
    if key == "input" and any((row.get("usage") or {}).get("input_status") == "inconsistent" for row in active):
        return "inconsistent", None
    if any(row.get("usage_status") != "known" for row in active):
        return "unknown", None
    values = []
    for row in active:
        usage = row.get("usage") or {}
        if key == "input":
            candidates = [usage[name] for name in ("input_tokens", "prompt_tokens") if name in usage]
        else:
            candidates = [usage[name] for name in ("output_tokens", "completion_tokens") if name in usage]
        if len(set(candidates)) != 1:
            return "unknown", None
        values.append(candidates[0])
    return "known", sum(values)


def recorded_usage(embedding, rewrite, generation):
    """Sum safe counts already on the rows. Unrecorded calls stay unknown, not zero."""
    groups = {}
    for name, rows in (("embedding", embedding), ("rewrite", rewrite), ("generation", generation)):
        item = {
            "rows": sum(row.get("usage_status") != "not_called" for row in rows),
            "currency_total": "unknown",
            "unrecorded_calls": "unknown",
        }
        for label, key in (("input_tokens", "input"), ("output_tokens", "output")):
            status, total = _token_status(rows, key)
            item[f"{label}_status"] = status
            if status == "known":
                item[label] = total
        item.update(_rollup_numbers(rows, "cache_read", "cache_read_status"))
        miss = _rollup_numbers(rows, "cache_miss", "cache_miss_status")
        if miss.get("cache_miss_status") == "unknown":
            estimated = _rollup_numbers(
                rows, "cache_miss", "cache_miss_status", estimate_key="cache_miss_estimate"
            )
            if estimated.get("cache_miss_status") == "estimate":
                miss = estimated
        item.update(miss)
        groups[name] = item
    return {
        "embedding": groups["embedding"],
        "rewrite": groups["rewrite"],
        "generation": groups["generation"],
        "currency_total": "unknown",
        "unrecorded_calls": "unknown",
    }


def query_costs(cases):
    embedding = []
    supplemental = []
    seen = {}
    rewrite = []
    generation = []
    for row in cases:
        usage, status = allow_usage((row.get("shared_embedding_metrics") or {}).get("usage"))
        calls = (row.get("shared_embedding_metrics") or {}).get("calls") or 0
        cache_status, miss_status = _usage_row(usage, status if calls else "not_called")
        item = {
            "id": row["id"],
            "calls": calls,
            "usage": usage,
            "usage_status": status if calls else "not_called",
            "cache_read_status": cache_status,
            "cache_miss_status": miss_status,
        }
        prior = seen.get(row["id"])
        if prior is None:
            seen[row["id"]] = item
            embedding.append(item)
        elif (prior["calls"], prior["usage"], prior["usage_status"]) != (calls, usage, item["usage_status"]):
            prior["usage"] = None
            prior["usage_status"] = "inconsistent"
            prior["cache_read_status"] = "inconsistent"
            prior["cache_miss_status"] = "inconsistent"
        extra = row.get("supplemental_embedding_metrics") or {}
        if extra.get("calls"):
            usage, status = allow_usage(extra.get("usage"))
            cache_status, miss_status = _usage_row(usage, status)
            supplemental.append({
                "id": row["id"], "strategy": row.get("strategy"), "calls": extra["calls"],
                "usage": usage, "usage_status": status, "cache_read_status": cache_status,
                "cache_miss_status": miss_status,
            })
        rewrite_metrics = row.get("rewrite_metrics") or None
        if row.get("strategy") == "rewrite" and rewrite_metrics:
            usage, status = allow_usage(rewrite_metrics.get("usage"))
            usage_status = status if rewrite_metrics.get("usage") is not None or usage else (
                "unknown" if rewrite_metrics.get("reason") else "not_called"
            )
            cache_status, miss_status = _usage_row(usage, usage_status)
            rewrite.append(
                {
                    "id": row["id"],
                    "elapsed_ms": rewrite_metrics.get("elapsed_ms"),
                    "usage": usage,
                    "usage_status": usage_status,
                    "cache_read_status": cache_status,
                    "cache_miss_status": miss_status,
                }
            )
        answer = row.get("answer_metrics") or {}
        calls = answer.get("calls") or 0
        usage, status = allow_usage(answer.get("usage"))
        usage_status = status if calls else "not_called"
        cache_status, miss_status = _usage_row(usage, usage_status)
        generation.append(
            {
                "id": row["id"],
                "strategy": row.get("strategy"),
                "calls": calls,
                "elapsed_ms": answer.get("elapsed_ms"),
                "usage": usage,
                "usage_status": usage_status,
                "cache_read_status": cache_status,
                "cache_miss_status": miss_status,
            }
        )
    return {
        "embedding": embedding,
        "supplemental_embedding": supplemental,
        "rewrite": rewrite,
        "generation": generation,
        "recorded_usage": recorded_usage([*embedding, *supplemental], rewrite, generation),
        "currency_total": "unknown",
    }
