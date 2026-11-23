"""Reproducible offline retrieval probe. It does not call a model or open a database.

Pass ``--questions`` and ``--sources`` to score hash-checked local texts. The
written JSON contains counts and statuses only.
"""

import argparse
import json
from pathlib import Path

from study_platform.services.retrieval_probe import (
    load_source_corpus,
    probe_cases,
    synthetic_probe,
)

COUNT_KEYS = {
    "id",
    "spans",
    "in_chunk",
    "min_chunks",
    "exact",
    "lexical_hits_at_10",
    "lexical_hits_at_24",
    "lexical_doc_hit_at_10",
    "lexical_doc_hit_at_24",
    "spans_sharing_query_term",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path)
    parser.add_argument("--sources", type=Path)
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = {"synthetic": synthetic_probe(), "vector_ranking": "NOT_VERIFIED", "rerank": "NOT_VERIFIED"}
    if args.questions or args.sources or args.source_manifest:
        if not (args.questions and args.sources and args.source_manifest):
            raise SystemExit("External probe requires --questions, --sources and --source-manifest")
        manifest = json.loads(args.source_manifest.read_text(encoding="utf-8"))
        documents, files = load_source_corpus(manifest, args.sources)
        cases = [
            json.loads(line)
            for line in args.questions.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        corpus_rows = None
        if documents:
            from study_platform.services.retrieval_probe import prepare_corpus

            prepared = prepare_corpus(documents)
            corpus_rows = probe_cases(cases, prepared)
            report["replay_counts"] = prepared["counts"]
            report["replay_multi"] = prepared["multi"]
        report["sources"] = files
        report["questions"] = corpus_rows or []
        report["lexical_method"] = "token_overlap"
        for row in report["questions"]:
            if set(row) - COUNT_KEYS:
                raise SystemExit("Probe output tried to keep a non-count field")
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
