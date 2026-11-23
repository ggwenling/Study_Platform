"""Build an offline Evaluation V2 report from saved records.

No database, network, or model calls. Example:

    study-eval-report-v2 --report report.json --questions questions.jsonl --output-dir v2
"""

import argparse
import json
from pathlib import Path

from study_platform.services.evaluation_v2 import evaluate_paths, write_outputs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True, help="saved report JSON or JSONL records")
    parser.add_argument("--questions", type=Path, required=True, help="frozen questions JSONL")
    parser.add_argument(
        "--manifest",
        type=Path,
        help="optional chunk manifest; only source=corpus_manifest with matching chunk_count and documents is a corpus proof",
    )
    parser.add_argument("--annotations", type=Path, help="optional AI or human review annotations")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    report = evaluate_paths(
        args.report,
        args.questions,
        manifest_path=args.manifest,
        annotations_path=args.annotations,
    )
    write_outputs(report, args.output_dir)
    print(json.dumps({"schema_version": report["schema_version"], "summary": report["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
