"""Prepare and verify an evaluation corpus without a database or model calls."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from study_platform.services import chunking, ingestion
from study_platform.services.rag_evaluation import support_witness

MIME_TYPES = {
    "txt": "text/plain", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "png": "image/png", "jpeg": "image/jpeg", "pdf": "application/pdf",
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package_path(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Evaluation path escapes the declared corpus root")
    return path


def load_profile(manifest_file, dataset, cases, materials, profile_name):
    package = manifest_file.resolve().parent
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 2 or profile_name not in manifest.get("profiles", {}):
        raise ValueError("Unknown evaluation manifest or profile")
    if manifest.get("dataset_sha256") != digest(dataset) or manifest.get("materials_sha256") != digest(
        dataset.with_name("materials.json")
    ):
        raise ValueError("Dataset or material manifest does not match the freeze")
    profile = manifest["profiles"][profile_name]
    root = package_path(package, manifest["corpus_root"])
    if not root.is_dir():
        raise ValueError("Corpus root is not a directory")
    ids = {case["id"] for case in cases}
    selected = set(profile.get("case_ids", ids))
    forced = set(profile.get("force_pdf_ocr", ()))
    if not selected or not selected <= ids or not forced <= {
        material["id"] for material in materials if material["format"] == "pdf"
    }:
        raise ValueError("Invalid case or forced OCR selection")
    if type(profile.get("max_pdf_pages")) is not int or not 1 <= profile["max_pdf_pages"] <= 150:
        raise ValueError("Invalid evaluation-specific PDF page limit")
    material_paths = []
    for material in materials:
        path = package_path(root, material["path"])
        if not path.is_file():
            raise ValueError(f"Missing evaluation material {material['id']}")
        material_paths.append({**material, "path": str(path)})
    reviewed = valid_ai_review(package, manifest, ids)
    return [case for case in cases if case["id"] in selected], material_paths, profile, reviewed


def valid_ai_review(package, manifest, case_ids):
    if not manifest.get("reviews"):
        return False
    if len(manifest["reviews"]) != 2:
        return False
    answer_key = package_path(package, manifest["answer_key"])
    if digest(answer_key) != manifest.get("answer_key_sha256"):
        return False
    identities = set()
    for entry in manifest["reviews"]:
        path = package_path(package, entry["path"])
        if not path.is_file() or digest(path) != entry.get("sha256"):
            return False
        review = json.loads(path.read_text(encoding="utf-8"))
        verdicts = review.get("verdicts", {})
        if (
            review.get("review_kind") != "ai" or not review.get("reviewer")
            or review.get("dataset_sha256") != manifest["dataset_sha256"]
            or review.get("answer_key_sha256") != manifest["answer_key_sha256"]
            or set(verdicts) != case_ids
            or any(row.get("status") != "PASS" or not row.get("reason") for row in verdicts.values())
        ):
            return False
        identities.add(review["reviewer"])
    return len(identities) == 2


@dataclass
class PreparedMaterial:
    source: Path
    sha256: str
    page_count: int | None
    chunks: list


def prepare_corpus(materials, cases, settings, directory, render_source, profile, pipeline_profile=None):
    """Stage each original once, then use these same chunks for live indexing."""
    selected_profile = chunking.resolve_pipeline_profile(
        pipeline_profile if pipeline_profile is not None else (profile.get("pipeline_profile") if profile else None)
    )
    prepared = {}
    failures = []
    evidence = []
    forced = set(profile.get("force_pdf_ocr", ())) if profile else set()
    for ordinal, material in enumerate(materials):
        material_id = material["id"]
        folder = directory / str(ordinal)
        folder.mkdir()
        source = folder / f"source.{material['format']}"
        version = SimpleNamespace(id=material_id, source_mime=MIME_TYPES[material["format"]])
        try:
            render_source(material, source)
            if source.stat().st_size > settings.upload_limit_for_mime(version.source_mime):
                raise ValueError("Source exceeds upload size limit")
            fingerprint = digest(source)
            if (profile or material.get("sha256")) and fingerprint != material.get("sha256"):
                raise ValueError("Source SHA-256 mismatch")
            parts = list(ingestion.extract(
                version, folder, lambda _: None, selected_profile, path=source, settings=settings,
                force_pdf_ocr=material_id in forced,
            ))
            chunks = [
                (text, narrowed)
                for part, loc in parts
                for text, narrowed in chunking.segment_unit(part, loc, selected_profile)
            ]
            if not chunks:
                raise ValueError("No indexable text")
        except (OSError, RuntimeError, ValueError) as error:
            failures.append({"material": material_id, "error": type(error).__name__, "detail": str(error)})
            continue
        prepared[material_id] = PreparedMaterial(
            source, fingerprint, getattr(version, "page_count", None), chunks,
        )
        evidence.extend(
            {"material_version_id": material_id, "text": text, "locator": loc}
            for text, loc in chunks if material_id not in forced or loc.get("ocr")
        )
    identity = {material_id: material_id for material_id in prepared}
    case_results = []
    for case in cases:
        if not case["answerable"]:
            continue
        witness = support_witness(case, evidence, identity)
        missing = [i + 1 for i, match in enumerate(witness) if match is None]
        count = len(set().union(*(match for match in witness if match is not None)))
        case_results.append({"id": case["id"], "missing_requirements": missing, "witness_chunks": count})
    summary = {
        "failures": failures,
        "materials": [
            {"id": key, "sha256": doc.sha256, "pages": doc.page_count, "chunks": len(doc.chunks),
             "ocr_pages": sorted({loc["page"] for _, loc in doc.chunks if loc.get("ocr") and "page" in loc})}
            for key, doc in prepared.items()
        ],
        "evidence": case_results,
        "preflight_passed": not failures and all(
            not row["missing_requirements"] and row["witness_chunks"] <= settings.retrieval_top_k
            for row in case_results
        ),
    }
    return prepared, summary
