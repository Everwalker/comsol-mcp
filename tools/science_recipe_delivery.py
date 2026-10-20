#!/usr/bin/env python3
"""Read-only T060 artifact coverage audit for W22/W23/W24 science deliveries.

This tool verifies a package manifest's file closure and claimed target-replay
records. It never upgrades T060 acceptance: scientific content, module/license
scope, embedded paths in opaque files, and actual clean-target replay still need
independent review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


SCHEMA = "comsol-mcp-science-delivery/1"
CASE_DOMAINS = {
    "W22": "W22",
    "W23": "W23",
    "W24_SHAPE": "W24",
    "W24_CURE": "W24",
}
REQUIRED_CATEGORIES = (
    "mph",
    "dependency_hashes",
    "recipe",
    "raw_numerical",
    "plots",
    "logs",
    "version",
    "validation",
)
TEXT_SUFFIXES = {
    ".csv", ".json", ".log", ".md", ".txt", ".xml", ".yaml", ".yml",
    ".java", ".py", ".tsv",
}
MAX_TEXT_SCAN_BYTES = 16 * 1024 * 1024
KNOWN_ABSOLUTE_PATH = re.compile(
    rb"(?i)(?:"
    rb"(?<![A-Za-z0-9_])(?:[A-Z]:\\(?:Users|Temp|Windows|Program Files|ProgramData|COMSOL)\\)"
    rb"|\\\\[^\\\s]+\\[^\\\s]+"
    rb"|(?<![A-Za-z0-9_])/(?:Users|Volumes|private|tmp|home|var|opt|Applications|Library|System)/"
    rb")"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _json_read(path: Path) -> Any:
    return _strict_json_loads(path.read_text(encoding="utf-8"))


def _strict_json_loads(text: str) -> Any:
    """Decode finite JSON while rejecting duplicate object keys at every depth."""

    def object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("non-finite JSON number")
        return parsed

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON constant: {value}")

    return json.loads(
        text,
        object_pairs_hook=object_without_duplicates,
        parse_float=finite_float,
        parse_constant=reject_constant,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _target_matrix(matrix: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(matrix, dict) or not isinstance(matrix.get("tests"), list):
        raise ValueError("acceptance matrix must contain a tests list")
    rows = [row for row in matrix["tests"] if isinstance(row, dict) and row.get("id") == "T060"]
    if len(rows) != 1:
        raise ValueError("acceptance matrix must contain exactly one T060 row")
    targets = rows[0].get("targets")
    if not isinstance(targets, dict) or not targets:
        raise ValueError("T060 row has no target matrix")
    return targets


def _manifest_path(value: Any) -> tuple[PurePosixPath | None, str | None]:
    if not isinstance(value, str) or not value or "\x00" in value:
        return None, "path must be a non-empty string"
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if "\\" in value or posix.is_absolute() or windows.is_absolute() or windows.drive:
        return None, "absolute or non-portable path"
    if value != posix.as_posix() or any(part in ("", ".", "..") for part in value.split("/")):
        return None, "path is not a normalized relative POSIX path"
    return posix, None


def _resolve_regular_file(root: Path, rel: PurePosixPath) -> tuple[Path | None, str | None]:
    candidate = root.joinpath(*rel.parts)
    current = root
    try:
        for part in rel.parts:
            current = current / part
            if current.is_symlink():
                return None, "symlink is not allowed"
        if not candidate.is_file():
            return None, "file is missing or is not a regular file"
        root_real = root.resolve(strict=True)
        candidate_real = candidate.resolve(strict=True)
        if not candidate_real.is_relative_to(root_real):
            return None, "resolved file escapes the package root"
    except OSError as exc:
        return None, f"file inspection failed: {exc.__class__.__name__}"
    return candidate, None


def _audit_ref(root: Path, ref: Any, *, scan_text: bool) -> dict[str, Any]:
    if not isinstance(ref, dict):
        return {"status": "INVALID", "error": "file reference must be an object"}
    rel, path_error = _manifest_path(ref.get("path"))
    if path_error:
        return {"status": "INVALID", "path": ref.get("path"), "error": path_error}
    expected = ref.get("sha256")
    if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
        return {"status": "INVALID", "path": str(rel), "error": "sha256 must be 64 lowercase hex characters"}
    file_path, file_error = _resolve_regular_file(root, rel)
    if file_error:
        return {"status": "INVALID", "path": str(rel), "error": file_error}
    actual = _sha256(file_path)
    result: dict[str, Any] = {
        "status": "PASS" if actual == expected else "HASH_MISMATCH",
        "path": str(rel),
        "expected_sha256": expected,
        "actual_sha256": actual,
        "size_bytes": file_path.stat().st_size,
    }
    if actual != expected:
        result["error"] = "file hash differs from manifest"
    suffix = file_path.suffix.lower()
    if scan_text and suffix in TEXT_SUFFIXES:
        if file_path.stat().st_size > MAX_TEXT_SCAN_BYTES:
            result["text_path_scan"] = "TEXT_TOO_LARGE_NOT_SCANNED"
            return result
        try:
            payload = file_path.read_bytes()
        except OSError as exc:
            result["text_path_scan"] = "READ_ERROR"
            result["error"] = f"text payload read failed: {exc.__class__.__name__}"
        else:
            found = bool(KNOWN_ABSOLUTE_PATH.search(payload))
            result["text_path_scan"] = "KNOWN_ABSOLUTE_PATH_FOUND" if found else "NO_KNOWN_ABSOLUTE_PATH_PATTERN"
    else:
        result["text_path_scan"] = "OPAQUE_NOT_SCANNED"
    return result


def _audit_case(root: Path, case: Any) -> dict[str, Any]:
    if not isinstance(case, dict):
        return {"case_id": None, "status": "INVALID", "error": "case must be an object"}
    case_id = case.get("case_id")
    result: dict[str, Any] = {"case_id": case_id, "status": "INCOMPLETE", "categories": {}}
    if case_id not in CASE_DOMAINS:
        return {"case_id": case_id, "status": "INVALID", "error": "unexpected case_id"}
    if case.get("domain") != CASE_DOMAINS[case_id]:
        result["status"] = "INVALID"
        result["error"] = "domain does not match the fixed W22/W23/W24 case mapping"
        return result
    artifacts = case.get("artifacts")
    if not isinstance(artifacts, dict):
        result["status"] = "INVALID"
        result["error"] = "artifacts must be an object"
        return result
    unexpected_categories = sorted(set(artifacts) - set(REQUIRED_CATEGORIES))
    if unexpected_categories:
        result["status"] = "INVALID"
        result["error"] = "unexpected artifact categories: " + ", ".join(unexpected_categories)
        return result
    for category in REQUIRED_CATEGORIES:
        refs = artifacts.get(category)
        if not isinstance(refs, list) or not refs:
            result["categories"][category] = {"status": "MISSING", "files": []}
            continue
        audited = [_audit_ref(root, ref, scan_text=True) for ref in refs]
        paths = [entry.get("path") for entry in audited if entry.get("path")]
        if len(paths) != len(set(paths)):
            for entry in audited:
                if entry.get("path") in paths:
                    entry.setdefault("warnings", []).append("duplicate path within case")
            category_status = "INVALID"
        elif all(entry.get("status") == "PASS" for entry in audited):
            category_status = "PASS"
        elif any(entry.get("status") == "HASH_MISMATCH" for entry in audited):
            category_status = "HASH_MISMATCH"
        else:
            category_status = "INVALID"
        result["categories"][category] = {"status": category_status, "files": audited}
    if all(result["categories"][name]["status"] == "PASS" for name in REQUIRED_CATEGORIES):
        result["status"] = "PASS"
    return result


def _manifest_absolute_path_fields(value: Any, pointer: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, str) and KNOWN_ABSOLUTE_PATH.search(value.encode("utf-8", errors="replace")):
        found.append(pointer)
    elif isinstance(value, dict):
        for key, child in value.items():
            found.extend(_manifest_absolute_path_fields(child, f"{pointer}/{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_manifest_absolute_path_fields(child, f"{pointer}/{index}"))
    return found


def _declared_artifact_paths(manifest: Any) -> set[str]:
    declared: set[str] = set()
    if not isinstance(manifest, dict):
        return declared
    cases = manifest.get("cases", [])
    if isinstance(cases, list):
        for case in cases:
            if not isinstance(case, dict) or not isinstance(case.get("artifacts"), dict):
                continue
            for category in REQUIRED_CATEGORIES:
                refs = case["artifacts"].get(category)
                if not isinstance(refs, list):
                    continue
                for ref in refs:
                    if isinstance(ref, dict):
                        rel, error = _manifest_path(ref.get("path"))
                        if rel is not None and error is None:
                            declared.add(rel.as_posix())
    replays = manifest.get("target_replays", [])
    if isinstance(replays, list):
        for record in replays:
            if isinstance(record, dict) and record.get("status") == "PASS" and isinstance(record.get("log"), dict):
                rel, error = _manifest_path(record["log"].get("path"))
                if rel is not None and error is None:
                    declared.add(rel.as_posix())
    return declared


def _audit_package_file_set(root: Path, declared: set[str], manifest_file: Path | None) -> dict[str, Any]:
    excluded_manifest: str | None = None
    if manifest_file is not None:
        try:
            if manifest_file.resolve(strict=True).is_relative_to(root):
                excluded_manifest = manifest_file.resolve(strict=True).relative_to(root).as_posix()
        except OSError:
            pass
    unlisted: list[str] = []
    symlinks: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        directory = Path(dirpath)
        for name in list(dirnames):
            child = directory / name
            if child.is_symlink():
                symlinks.append(child.relative_to(root).as_posix())
                dirnames.remove(name)
        for name in filenames:
            child = directory / name
            rel = child.relative_to(root).as_posix()
            if child.is_symlink():
                symlinks.append(rel)
            elif rel != excluded_manifest and rel not in declared:
                unlisted.append(rel)
    status = "PASS" if not unlisted and not symlinks else "INCOMPLETE"
    return {
        "status": status,
        "declared_artifact_count": len(declared),
        "manifest_file_excluded_from_member_set": excluded_manifest,
        "unlisted_files": sorted(unlisted),
        "symlinks": sorted(symlinks),
    }


def _audit_replays(root: Path, manifest: Any, target_matrix: dict[str, dict[str, Any]]) -> dict[str, Any]:
    records = manifest.get("target_replays") if isinstance(manifest, dict) else None
    if not isinstance(records, list):
        return {"status": "INVALID", "targets": {}, "errors": ["target_replays must be a list"]}
    by_id: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("target_id"), str):
            errors.append("target replay row missing target_id")
            continue
        target_id = record["target_id"]
        if target_id in by_id:
            errors.append(f"duplicate target replay row: {target_id}")
        by_id[target_id] = record
    if set(by_id) != set(target_matrix):
        errors.append("target replay rows do not exactly match the original T060 target matrix")
    audited: dict[str, Any] = {}
    for target_id, baseline in target_matrix.items():
        record = by_id.get(target_id)
        if record is None:
            audited[target_id] = {"status": "MISSING"}
            continue
        status = record.get("status")
        waiver_id = baseline.get("scope_waiver_id")
        skipped = baseline.get("effective_scope_disposition") == "USER_REQUESTED_SKIP"
        item: dict[str, Any] = {"status": status, "claim_review": "UNREVIEWED"}
        if skipped:
            if (
                status != "USER_REQUESTED_SKIP"
                or record.get("waiver_id") != waiver_id
                or record.get("waiver_basis") != baseline.get("scope_waiver_basis")
                or record.get("compatibility_claim") != "NOT_VERIFIED"
                or baseline.get("compatibility_claim") != "NOT_VERIFIED"
                or baseline.get("original_required_scope_preserved") is not True
                or record.get("original_required_scope_preserved") is not True
            ):
                item["status"] = "INVALID_SKIP_CLAIM"
                errors.append(f"target {target_id} does not preserve its exact skip/compatibility waiver")
            else:
                item["waiver_id"] = waiver_id
                item["waiver_basis"] = baseline.get("scope_waiver_basis")
                item["compatibility_claim"] = "NOT_VERIFIED"
        elif status == "PASS":
            required_version = target_id.rsplit("/", 1)[-1]
            engine_version = record.get("engine_version")
            if (
                record.get("clean_project_directory") is not True
                or not isinstance(engine_version, str)
                or required_version.replace("comsol", "") not in engine_version.lower()
            ):
                item["status"] = "INVALID_PASS_CLAIM"
                errors.append(f"target {target_id} PASS requires a clean directory and a matching engine_version")
            else:
                item["log"] = _audit_ref(root, record.get("log"), scan_text=True)
                if item["log"].get("status") != "PASS":
                    item["status"] = "INVALID_PASS_CLAIM"
                    errors.append(f"target {target_id} replay log is missing or fails hash verification")
        elif status not in {"NOT_RUN", "BLOCKED_LICENSE"}:
            item["status"] = "INVALID_STATUS"
            errors.append(f"target {target_id} has unsupported replay status")
        if status == "BLOCKED_LICENSE":
            modules = record.get("missing_modules")
            if not isinstance(modules, list) or not modules:
                item["status"] = "INVALID_LICENSE_BLOCK"
                errors.append(f"target {target_id} BLOCKED_LICENSE must name unavailable modules")
        audited[target_id] = item
    effective_pass = all(
        audited.get(target_id, {}).get("status") in {"PASS", "USER_REQUESTED_SKIP"}
        for target_id in target_matrix
    )
    return {
        "status": "ALL_APPLICABLE_REPLAY_CLAIMS_PRESENT" if effective_pass and not errors else "NOT_RUN_OR_INCOMPLETE",
        "targets": audited,
        "errors": errors,
    }


def audit_bundle(
    package_root: Path,
    manifest: Any,
    acceptance_matrix: Any,
    *,
    manifest_file: Path | None = None,
) -> dict[str, Any]:
    """Audit relative artifact paths and hashes without asserting T060 acceptance."""
    root = package_root
    if not root.is_dir() or root.is_symlink():
        raise ValueError("package_root must be an existing non-symlink directory")
    root = root.resolve(strict=True)
    matrix_targets = _target_matrix(acceptance_matrix)
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        raise ValueError(f"manifest schema must be {SCHEMA}")
    cases = manifest.get("cases")
    if not isinstance(cases, list):
        raise ValueError("manifest cases must be a list")
    seen: set[str] = set()
    case_reports: list[dict[str, Any]] = []
    for case in cases:
        case_id = case.get("case_id") if isinstance(case, dict) else None
        if case_id in seen:
            case_reports.append({"case_id": case_id, "status": "INVALID", "error": "duplicate case_id"})
        seen.add(case_id)
        case_reports.append(_audit_case(root, case))
    if seen != set(CASE_DOMAINS):
        case_reports.append({
            "case_id": "__case_set__",
            "status": "INVALID",
            "error": "manifest must contain exactly W22, W23, W24_SHAPE, and W24_CURE",
        })
    replay_report = _audit_replays(root, manifest, matrix_targets)
    errors = list(replay_report["errors"])
    file_set_report = _audit_package_file_set(root, _declared_artifact_paths(manifest), manifest_file)
    text_path_findings = []
    manifest_path_findings = _manifest_absolute_path_fields(manifest)
    opaque_count = 0
    for case_report in case_reports:
        for category in case_report.get("categories", {}).values():
            for file_report in category.get("files", []):
                if file_report.get("text_path_scan") == "KNOWN_ABSOLUTE_PATH_FOUND":
                    text_path_findings.append(file_report.get("path"))
                if file_report.get("text_path_scan") in {"OPAQUE_NOT_SCANNED", "TEXT_TOO_LARGE_NOT_SCANNED"}:
                    opaque_count += 1
        if case_report.get("status") == "INVALID":
            errors.append(f"case {case_report.get('case_id')} is invalid")
    for target_report in replay_report["targets"].values():
        log_report = target_report.get("log") if isinstance(target_report, dict) else None
        if isinstance(log_report, dict) and log_report.get("text_path_scan") == "KNOWN_ABSOLUTE_PATH_FOUND":
            text_path_findings.append(log_report.get("path"))
    artifact_complete = len(case_reports) == len(CASE_DOMAINS) and all(
        report.get("case_id") in CASE_DOMAINS and report.get("status") == "PASS"
        for report in case_reports
    )
    if text_path_findings or manifest_path_findings:
        package_status = "KNOWN_ABSOLUTE_PATHS_FOUND"
    elif file_set_report["status"] != "PASS":
        package_status = "UNLISTED_FILES_OR_SYMLINKS_PRESENT"
    elif artifact_complete and replay_report["status"] == "ALL_APPLICABLE_REPLAY_CLAIMS_PRESENT":
        package_status = "ARTIFACT_HASH_CLOSURE_COMPLETE_PENDING_INDEPENDENT_REVIEW"
    elif artifact_complete:
        package_status = "ARTIFACT_HASH_CLOSURE_COMPLETE_TARGET_REPLAY_INCOMPLETE"
    else:
        package_status = "ARTIFACT_CLOSURE_INCOMPLETE"
    artifact_complete = artifact_complete and file_set_report["status"] == "PASS"
    return {
        "schema": "comsol-mcp-science-delivery-audit/1",
        "package_status": package_status,
        "artifact_hash_closure": "PASS" if artifact_complete else "INCOMPLETE",
        "package_file_set": file_set_report,
        "target_replay_claim_status": replay_report["status"],
        "target_replays": replay_report["targets"],
        "embedded_path_scan": {
            "status": "KNOWN_ABSOLUTE_PATHS_FOUND" if text_path_findings or manifest_path_findings else "PARTIAL_TEXT_ONLY_OPAQUE_FILES_UNSCANNED",
            "findings": sorted(set(text_path_findings)),
            "manifest_fields_with_known_absolute_paths": manifest_path_findings,
            "opaque_files_not_scanned": opaque_count,
        },
        "cases": case_reports,
        "errors": errors,
        "t060_acceptance": "NOT_RUN",
        "acceptance_limit": (
            "This read-only checker verifies declared file/hash closure and replay claims only. "
            "It does not validate scientific values, licensed module scope, hidden paths inside opaque files, "
            "target execution, or the independent reviewer decision."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--acceptance-matrix",
        type=Path,
        default=Path("docs/full_project_execution/state/ACCEPTANCE_MATRIX.json"),
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        report = audit_bundle(
            args.package_root,
            _json_read(args.manifest),
            _json_read(args.acceptance_matrix),
            manifest_file=args.manifest,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "INPUT_ERROR", "error": str(exc)}, indent=2), file=sys.stderr)
        return 2
    rendered = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0 if report["artifact_hash_closure"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
