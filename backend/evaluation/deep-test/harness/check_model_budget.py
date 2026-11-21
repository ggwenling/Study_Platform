"""Fail-closed prerequisites for live model evaluation (metadata only; no provider calls)."""

from __future__ import annotations

import json
import os


def main() -> int:
    spend_approved = os.environ.get("DEEP_TEST_SPEND_APPROVED") == "unlimited_finite"
    allow_live_flag = os.environ.get("DEEP_TEST_ALLOW_LIVE") == "1"
    settings_keys = ("DEEPSEEK_API_KEY", "EMBEDDING_API_KEY")
    present = {k: bool(os.environ.get(k, "").strip()) for k in settings_keys}
    live_allowed = spend_approved and allow_live_flag and all(present.values())
    report = {
        "live_evaluation_allowed": live_allowed,
        "spend_cap": {
            "user_authorized": spend_approved,
            "label": "unlimited for finite deep-test evaluation only (2026-11-21)",
            "note": "Budget approval does not inject credentials",
        },
        "keys_present": present,
        "env_flags": {
            "DEEP_TEST_ALLOW_LIVE": allow_live_flag,
            "DEEP_TEST_ISOLATION": os.environ.get("DEEP_TEST_ISOLATION"),
        },
        "platform_defaults": {
            "chat_provider": "OpenAI-compatible (deepseek_base_url default)",
            "chat_model_default": "deepseek-flash",
            "embedding_provider": "OpenAI-compatible (embedding_base_url default)",
            "embedding_model_default": "qwen3.7-text-embedding-flash",
            "embedding_dimensions": 1024,
            "deepseek_mandatory": False,
        },
        "live_eval_cli_strategies": ["baseline", "rewrite", "compare", "hybrid", "hybrid_compare"],
        "retrieval_only_flag": "--retrieval-only (skips chat; still needs embed)",
        "requires_to_run_live": [
            "DEEP_TEST_SPEND_APPROVED=unlimited_finite (harness marker after human cap)",
            "DEEP_TEST_ALLOW_LIVE=1",
            "DEEPSEEK_API_KEY and EMBEDDING_API_KEY in isolated deep-test.env (or env secrets)",
            "TEST_DATABASE_URL with CREATEDB on disposable *_test",
            "New empty --output directory",
        ],
        "study_eval_rag_live_command": (
            "uv run study-eval-rag --dataset evaluation/deep-test/questions.jsonl "
            "--strategy compare --live --output <new-empty-dir>"
        ),
        "bounded_protocol_when_ready": [
            "Dry 100Q (already pass)",
            "Live --retrieval-only on 5-10 question slice (ledger)",
            "Live compare on same slice",
            "Optional full 100Q single pass with cost-manifest.json",
        ],
        "scoring_disclaimer": "Automated metrics are not independent human label review",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())