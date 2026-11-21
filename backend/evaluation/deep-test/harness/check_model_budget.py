"""Fail-closed prerequisites for live model evaluation (no calls)."""

from __future__ import annotations

import json
import os
import sys


def main() -> int:
    settings_keys = ("DEEPSEEK_API_KEY", "EMBEDDING_API_KEY")
    present = {k: bool(os.environ.get(k, "").strip()) for k in settings_keys}
    live_allowed = all(present.values()) and os.environ.get("DEEP_TEST_ALLOW_LIVE") == "1"
    report = {
        "live_evaluation_allowed": live_allowed,
        "keys_present": present,
        "requires": [
            "Dedicated test model connection keys in .local/deep-test/env/deep-test.env",
            "DEEP_TEST_ALLOW_LIVE=1 after human spend-cap approval",
            "TEST_DATABASE_URL with CREATEDB on disposable *_test database",
        ],
        "study_eval_rag_live_command": (
            "uv run study-eval-rag --dataset evaluation/deep-test/questions.jsonl "
            "--strategy compare --live --output <new-empty-dir>"
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())