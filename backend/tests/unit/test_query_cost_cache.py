"""Offline cache-count aggregation. No provider, no keys, no campaign files."""

import json

from study_platform.services.eval_embedding_resume import query_costs
from study_platform.services.model_failure import allow_usage

SECRET = "sk-live-secret-value"
REQUEST_ID = "req-7b3c9a"


def _generation(case_id, strategy, usage, *, calls=1):
    return {
        "id": case_id,
        "strategy": strategy,
        "shared_embedding_metrics": {"calls": 1, "usage": {"input_tokens": 5, "total_tokens": 5}},
        "answer_metrics": {"calls": calls, "elapsed_ms": 1, "usage": usage},
        "rewrite_metrics": None,
    }


def test_nested_cache_read_is_kept_and_not_treated_as_a_miss():
    usage, status = allow_usage(
        {
            "input_tokens": 100,
            "output_tokens": 8,
            "total_tokens": 108,
            "input_token_details": {
                "cache_read": 40,
                "request_id": REQUEST_ID,
                "body": SECRET,
            },
            "api_key": SECRET,
        }
    )
    assert status == "known"
    assert usage["input_token_details"] == {"cache_read": 40}
    assert usage["cache_read_status"] == "known"
    assert usage["cache_miss_status"] == "estimate"
    assert usage["cache_miss_estimate"] == 60
    assert "cache_miss" not in usage
    rendered = json.dumps(usage)
    assert SECRET not in rendered and REQUEST_ID not in rendered


def test_missing_cache_field_stays_unknown():
    usage, status = allow_usage({"input_tokens": 10, "output_tokens": 2, "prompt_tokens": 10})
    assert status == "known"
    assert usage["cache_read_status"] == "unknown"
    assert usage["cache_miss_status"] == "unknown"
    assert "input_token_details" not in usage
    assert "cache_read" not in usage
    assert "cache_miss_estimate" not in usage


def test_explicit_zero_cache_read_is_known_and_labeled_estimate():
    usage, status = allow_usage(
        {"input_tokens": 10, "output_tokens": 1, "input_token_details": {"cache_read": 0}}
    )
    assert status == "known"
    assert usage["input_token_details"]["cache_read"] == 0
    assert usage["cache_read_status"] == "known"
    assert usage["cache_miss_status"] == "estimate"
    assert usage["cache_miss_estimate"] == 10


def test_wrong_types_are_not_coerced_or_copied():
    for bad in (True, 1.5, "40", -1, {"n": 40}, None):
        usage, status = allow_usage(
            {
                "input_tokens": 10,
                "input_token_details": {"cache_read": bad, "header": SECRET},
            }
        )
        assert status == "known"
        assert usage["cache_read_status"] == "unknown"
        assert "input_token_details" not in usage
        assert SECRET not in json.dumps(usage)
    assert allow_usage({"input_token_details": "not-an-object", "note": SECRET}) == (None, "unknown")


def test_disagreeing_cache_sources_are_inconsistent():
    usage, status = allow_usage(
        {
            "input_tokens": 100,
            "input_token_details": {"cache_read": 40},
            "prompt_cache_hit_tokens": 55,
            "prompt_tokens_details": {"cached_tokens": 40},
        }
    )
    assert status == "known"
    assert usage["cache_read_status"] == "inconsistent"
    assert usage["cache_read_observed"] == [40, 55]
    assert "input_token_details" not in usage
    assert usage["cache_miss_status"] == "unknown"
    assert "cache_miss_estimate" not in usage


def test_cache_read_above_input_is_inconsistent():
    usage, _status = allow_usage(
        {"input_tokens": 10, "input_token_details": {"cache_read": 11}}
    )
    assert usage["input_token_details"]["cache_read"] == 11
    assert usage["cache_read_status"] == "inconsistent"
    assert "cache_miss_estimate" not in usage


def test_provider_miss_is_kept_without_becoming_an_estimate():
    usage, _status = allow_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 4,
            "prompt_cache_hit_tokens": 40,
            "prompt_cache_miss_tokens": 60,
        }
    )
    assert usage["input_token_details"] == {"cache_read": 40}
    assert usage["cache_miss"] == 60
    assert usage["cache_read_status"] == "known"
    assert usage["cache_miss_status"] == "known"
    assert "cache_miss_estimate" not in usage


def test_provider_miss_that_does_not_add_up_is_inconsistent():
    usage, _status = allow_usage(
        {
            "input_tokens": 100,
            "prompt_cache_hit_tokens": 40,
            "prompt_cache_miss_tokens": 50,
        }
    )
    assert usage["cache_read_status"] == "inconsistent"
    assert usage["cache_miss_status"] == "inconsistent"
    assert "cache_miss_estimate" not in usage


def test_two_segments_count_shared_embedding_once_and_keep_cache():
    shared = {"calls": 1, "usage": {"input_tokens": 5895, "total_tokens": 5895}}
    first = {
        "id": "Q1",
        "strategy": "baseline",
        "shared_embedding_metrics": shared,
        "answer_metrics": {
            "calls": 1,
            "usage": {
                "input_tokens": 67766,
                "output_tokens": 16194,
                "input_token_details": {"cache_read": 34936},
            },
        },
    }
    second = {
        "id": "Q1",
        "strategy": "rewrite",
        "shared_embedding_metrics": dict(shared),
        "rewrite_metrics": {
            "usage": {
                "input_tokens": 6705,
                "output_tokens": 2483,
                "input_token_details": {"cache_read": 0},
            }
        },
        "answer_metrics": {
            "calls": 1,
            "usage": {
                "input_tokens": 68702,
                "output_tokens": 16538,
                "input_token_details": {"cache_read": 37880, "request_id": REQUEST_ID},
            },
        },
    }
    costs = query_costs([first] + [second])
    assert [row["id"] for row in costs["embedding"]] == ["Q1"]
    assert costs["embedding"][0]["calls"] == 1
    assert costs["embedding"][0]["cache_read_status"] == "unknown"
    assert len(costs["generation"]) == 2
    assert len(costs["rewrite"]) == 1
    recorded = costs["recorded_usage"]
    assert recorded["currency_total"] == "unknown"
    assert recorded["unrecorded_calls"] == "unknown"
    assert recorded["generation"]["input_tokens"] == 136468
    assert recorded["generation"]["output_tokens"] == 32732
    assert recorded["generation"]["cache_read"] == 72816
    assert recorded["generation"]["cache_read_status"] == "known"
    assert recorded["generation"]["cache_miss_status"] == "estimate"
    assert recorded["generation"]["cache_miss_estimate"] == 63652
    assert recorded["rewrite"]["cache_read"] == 0
    assert recorded["rewrite"]["cache_read_status"] == "known"
    assert recorded["rewrite"]["input_tokens"] == 6705
    assert recorded["embedding"]["input_tokens"] == 5895
    assert recorded["embedding"]["cache_read_status"] == "unknown"
    assert "cache_read" not in recorded["embedding"]
    rendered = json.dumps(costs)
    assert REQUEST_ID not in rendered
    assert "usd" not in rendered.lower() and "cny" not in rendered.lower()


def test_one_missing_cache_field_makes_the_group_unknown():
    known = _generation(
        "Q1",
        "baseline",
        {"input_tokens": 10, "output_tokens": 1, "input_token_details": {"cache_read": 4}},
    )
    missing = _generation("Q2", "baseline", {"input_tokens": 10, "output_tokens": 1})
    costs = query_costs([known, missing])
    group = costs["recorded_usage"]["generation"]
    assert group["input_tokens"] == 20
    assert group["cache_read_status"] == "unknown"
    assert "cache_read" not in group
    assert "cache_miss_estimate" not in group


def test_inconsistent_shared_embedding_is_not_double_counted():
    first = _generation("Q1", "baseline", {"input_tokens": 3, "output_tokens": 1})
    second = _generation("Q1", "rewrite", {"input_tokens": 3, "output_tokens": 1})
    second["shared_embedding_metrics"] = {"calls": 1, "usage": {"input_tokens": 9}}
    second["rewrite_metrics"] = {"usage": {"input_tokens": 2, "output_tokens": 1}}
    costs = query_costs([first, second])
    assert len(costs["embedding"]) == 1
    assert costs["embedding"][0]["usage_status"] == "inconsistent"
    assert costs["embedding"][0]["usage"] is None
    assert costs["recorded_usage"]["embedding"]["input_tokens_status"] == "unknown"
    assert "input_tokens" not in costs["recorded_usage"]["embedding"]
