#!/usr/bin/env python3
"""Assemble a fail-closed G3.3 acceptance ledger from explicit evidence refs.

This helper deliberately lives beside the resumption evidence instead of in
``tools/``.  It never runs COMSOL, the MCP server, pytest, git, or a remote
sync.  The caller supplies a JSON mapping containing every final path and
expected SHA-256.  The helper reads those files, verifies nested assertions,
and writes a new ledger in this run directory.

The input is intentionally verbose.  A successful process exit, a declared
``PASS``, or a hash comparison by itself is not acceptance.  Every required
case and proof must have an existing, hash-matched artifact, an actual nested
assertion, source identity, and execution metadata.  Native ``NOT_RUN`` and
``PARTIAL`` observations are preserved verbatim; the only accepted
cross-evidence resolutions are the explicit policies for C02, C10, C11 and
C14 in :func:`_validate_special_case`.

Usage::

    python3 assemble_acceptance_ledger.py --write-template input.json
    python3 assemble_acceptance_ledger.py --mapping input.json \
        --output assembled_acceptance_ledger.json

The template is a contract skeleton.  It contains no guessed run paths or
hashes and therefore cannot accidentally certify the current worktree.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA = "comsol-mcp-g3/phase4_3-acceptance-assembly/1"
INPUT_SCHEMA = "comsol-mcp-g3/phase4_3-acceptance-assembly-input/1"
CASE_IDS = tuple(f"C{i:02d}" for i in range(18))
REQUIRED_PROOFS = ("software", "wheel", "recovery", "audit", "remote_sync")
HEX64 = set("0123456789abcdefABCDEF")
# The final source freeze was advanced after the earlier 4c32d75 run family.
# These defaults are deliberately explicit; a completed mapping may override
# them only by changing the input and recording a new, hash-matched manifest.
DEFAULT_EXPECTED_COMMIT = "a3f39b3022b417e16c3087b8b35f1daa06f30b8a"
DEFAULT_EXPECTED_TREE = "5a3e1b72e95a9df8892def2ca0994a8737508612"
# C00/C01 intentionally bind to the recovered PIN and protected-history
# evidence.  Those cases establish historical restoration/provenance; they do
# not claim that the historical bytes are the current runtime source.
PIN_EXPECTED_COMMIT = "2cb4627924d1a3240818ea7cd00453d4bd2d2da8"
PIN_EXPECTED_TREE = "dd3095e89c640c19aeb151cd0e8efe4af3c55802"
DEFAULT_SEMANTIC_SOURCE_HASHES = {
    "comsol_mcp/_domain_outcome.py": "d89cd4ef977b2899519425b5cabeb1c0d82afc448021d9e65832b9dddbea80f2",
    "tests/test_g3_domain_outcome.py": "25943d8028200e38c5b68c88281aa98207e13c2901b6753a16176059cda17362",
}


class Validation:
    """Collect all evidence defects so the output remains inspectable."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, where: str, message: str) -> None:
        self.errors.append(f"{where}: {message}")

    def warning(self, where: str, message: str) -> None:
        self.warnings.append(f"{where}: {message}")


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_load(path: Path, validation: Validation, where: str) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        validation.error(where, f"JSON read failed: {exc}")
        return None


def _resolve_path(value: Any, repo_root: Path, validation: Validation, where: str) -> tuple[Path | None, str | None]:
    if not _nonempty_string(value):
        validation.error(where, "path is required and must be a non-empty string")
        return None, None
    raw = Path(str(value)).expanduser()
    candidate = raw if raw.is_absolute() else repo_root / raw
    try:
        resolved = candidate.resolve(strict=False)
        relative = resolved.relative_to(repo_root).as_posix()
    except (OSError, ValueError) as exc:
        validation.error(where, f"path is outside repository root or cannot resolve: {exc}")
        return None, None
    return resolved, relative


def _hash_ref(spec: Any, repo_root: Path, validation: Validation, where: str, *, load_json: bool = True) -> tuple[dict[str, Any], Any | None]:
    """Validate one path/hash reference and optionally parse its JSON."""
    result: dict[str, Any] = {"label": where, "path": None, "sha256": None, "expected_sha256": None}
    if not isinstance(spec, Mapping):
        validation.error(where, "reference must be an object with path and sha256")
        return result, None
    path, relative = _resolve_path(spec.get("path"), repo_root, validation, where)
    result["path"] = relative
    expected = spec.get("sha256")
    result["expected_sha256"] = expected if isinstance(expected, str) else None
    if not isinstance(expected, str) or len(expected) != 64 or any(ch not in HEX64 for ch in expected):
        validation.error(where, "an expected 64-character sha256 is required; hashes are never inferred")
    if path is None or not path.is_file():
        validation.error(where, "referenced file does not exist")
        return result, None
    try:
        actual = _sha256(path)
    except OSError as exc:
        validation.error(where, f"cannot hash referenced file: {exc}")
        return result, None
    result["sha256"] = actual
    if isinstance(expected, str) and actual.lower() != expected.lower():
        validation.error(where, f"sha256 mismatch (expected {expected}, got {actual})")
    document: Any | None = None
    if load_json:
        document = _json_load(path, validation, where)
    result["assertions"] = []
    return result, document


def _json_pointer(document: Any, pointer: str) -> tuple[bool, Any]:
    if pointer == "":
        return True, document
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        return False, None
    current = document
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if token not in current:
                return False, None
            current = current[token]
        elif isinstance(current, list):
            try:
                index = int(token)
            except ValueError:
                return False, None
            if index < 0 or index >= len(current):
                return False, None
            current = current[index]
        else:
            return False, None
    return True, current


def _equal(actual: Any, expected: Any) -> bool:
    # bool is an int subclass; JSON assertions must keep those distinct.
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    return actual == expected


def _subset(actual: Any, expected: Any) -> bool:
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            return False
        return all(key in actual and _subset(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        return _equal(actual, expected)
    return _equal(actual, expected)


def _walk_mappings(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk_mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_mappings(child)


def _selector_matches(document: Any, where: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [row for row in _walk_mappings(document)
            if all(key in row and _equal(row[key], expected) for key, expected in where.items())]


def _assertion(document: Any, spec: Mapping[str, Any], validation: Validation, where: str) -> dict[str, Any]:
    """Evaluate one explicit JSON assertion, returning observed values."""
    record: dict[str, Any] = {"label": where}
    if "status_pointer" in spec:
        pointer = spec.get("status_pointer")
        if not isinstance(pointer, str):
            validation.error(where, "status_pointer must be a JSON pointer")
            return record
        ok, actual = _json_pointer(document, pointer)
        record.update({"json_pointer": pointer, "found": ok, "actual": actual if ok else None})
        if not ok:
            validation.error(where, f"JSON pointer {pointer!r} was not found")
            return record
        if "expected_status" in spec and not _equal(actual, spec.get("expected_status")):
            validation.error(where, f"status mismatch (expected {spec.get('expected_status')!r}, got {actual!r})")
        return record
    if "json_pointer" in spec:
        pointer = spec.get("json_pointer")
        if not isinstance(pointer, str):
            validation.error(where, "json_pointer must be a JSON pointer")
            return record
        ok, actual = _json_pointer(document, pointer)
        record.update({"json_pointer": pointer, "found": ok, "actual": actual if ok else None})
        if not ok:
            validation.error(where, f"JSON pointer {pointer!r} was not found")
            return record
        if "equals" in spec and not _equal(actual, spec.get("equals")):
            validation.error(where, f"value mismatch (expected {spec.get('equals')!r}, got {actual!r})")
        if "expect" in spec and not _subset(actual, spec.get("expect")):
            validation.error(where, "nested expected value did not match")
        if spec.get("non_empty") is True and (actual is None or actual == "" or actual == [] or actual == {}):
            validation.error(where, "expected a non-empty value")
        return record
    if "selectors" in spec:
        selectors = spec.get("selectors")
        if not isinstance(selectors, list) or not selectors:
            validation.error(where, "selectors must be a non-empty list")
            return record
        selected_records: list[dict[str, Any]] = []
        for index, selector in enumerate(selectors):
            selector_where = _mapping(selector)
            if selector_where is None or not isinstance(selector_where.get("where"), Mapping):
                validation.error(f"{where}[{index}]", "selector requires a where mapping")
                continue
            matches = _selector_matches(document, selector_where["where"])
            expected_count = selector_where.get("count", 1)
            if len(matches) != expected_count:
                validation.error(f"{where}[{index}]", f"expected {expected_count} selector matches, got {len(matches)}")
                continue
            expected = selector_where.get("expect", {})
            if not isinstance(expected, Mapping):
                validation.error(f"{where}[{index}]", "selector expect must be an object")
                continue
            for match_index, match in enumerate(matches):
                if not _subset(match, expected):
                    validation.error(f"{where}[{index}]/{match_index}", "selected record did not match expected fields")
                selected_records.append(dict(match))
        record["selected"] = selected_records
        return record
    if "expect" in spec:
        expected = spec.get("expect")
        record["actual"] = document
        if not _subset(document, expected):
            validation.error(where, "root document did not match expected fields")
        return record
    validation.error(where, "assertion requires status_pointer, json_pointer, selectors, or expect")
    return record


def _apply_assertions(document: Any, spec: Mapping[str, Any], validation: Validation, where: str) -> list[dict[str, Any]]:
    assertions: list[dict[str, Any]] = []
    if "status_pointer" in spec or "json_pointer" in spec or "selectors" in spec or "expect" in spec:
        assertions.append(_assertion(document, spec, validation, where))
    listed = spec.get("assertions", [])
    if listed is not None:
        if not isinstance(listed, list):
            validation.error(where, "assertions must be a list")
        else:
            for index, assertion in enumerate(listed):
                if not isinstance(assertion, Mapping):
                    validation.error(f"{where}.assertions[{index}]", "assertion must be an object")
                    continue
                assertions.append(_assertion(document, assertion, validation, f"{where}.assertions[{index}]"))
    return assertions


def _status_from(document: Any, ref: Mapping[str, Any], validation: Validation, where: str) -> Any:
    pointer = ref.get("status_pointer")
    if isinstance(pointer, str):
        ok, value = _json_pointer(document, pointer)
        if not ok:
            validation.error(where, f"status_pointer {pointer!r} was not found")
            return None
        return value
    if isinstance(document, Mapping):
        for key in ("status", "overall", "state"):
            value = document.get(key)
            if isinstance(value, str):
                return value
    validation.error(where, "status_ref needs status_pointer or a root status/overall/state")
    return None


def _source_identity(document: Any) -> dict[str, Any]:
    if not isinstance(document, Mapping):
        return {"commit": None, "tree": None, "dirty": None}
    commit = (document.get("commit") or document.get("head") or
              document.get("source_head") or document.get("source_commit"))
    tree = document.get("tree") or document.get("source_tree")
    dirty = document.get("dirty")
    if dirty is None:
        dirty = document.get("dirty_paths")
    if dirty is None:
        dirty = document.get("tracked_dirty_paths")
    return {"commit": commit, "tree": tree, "dirty": dirty}


def _metadata(run_status: Any, source_manifest: Any, case_spec: Mapping[str, Any], validation: Validation, where: str) -> dict[str, Any]:
    explicit = _mapping(case_spec.get("metadata")) or {}
    status = _mapping(run_status) or {}
    source = _mapping(source_manifest) or {}

    def first(*names: str) -> Any:
        for name in names:
            if name in explicit and explicit[name] is not None:
                return explicit[name]
            if name in status and status[name] is not None:
                return status[name]
            if name in source and source[name] is not None:
                return source[name]
        return None

    command = first("command", "runner_command")
    cwd = first("cwd")
    interpreter = first("interpreter")
    exit_code = first("exit_code")
    runtime = first("runtime")
    if runtime is None:
        runtime = {key: first(key) for key in ("started_at", "started_at_utc", "finished_at", "finished_at_utc", "elapsed_seconds")
                   if first(key) is not None}
    for key, value in (("command", command), ("cwd", cwd), ("interpreter", interpreter), ("runtime", runtime), ("exit_code", exit_code)):
        if value is None or value == "" or value == {}:
            validation.error(f"{where}.metadata", f"{key} is missing; supply it explicitly or in run/source metadata")
    if isinstance(exit_code, bool) or (exit_code is not None and not isinstance(exit_code, int)):
        validation.error(f"{where}.metadata", "exit_code must be an integer")
    return {
        "command": command,
        "cwd": cwd,
        "interpreter": interpreter,
        "runtime": runtime,
        "exit_code": exit_code,
    }


def _ref_with_assertions(spec: Any, repo_root: Path, validation: Validation, where: str, *, require_json: bool = True) -> tuple[dict[str, Any], Any | None]:
    record, document = _hash_ref(spec, repo_root, validation, where, load_json=require_json)
    if document is not None:
        record["assertions"] = _apply_assertions(document, spec if isinstance(spec, Mapping) else {}, validation, where)
    return record, document


def _validate_source_binding(mapping: Mapping[str, Any], repo_root: Path, validation: Validation) -> dict[str, Any]:
    spec = _mapping(mapping.get("source_binding")) or {}
    expected_commit = spec.get("expected_commit")
    expected_tree = spec.get("expected_tree")
    if not _nonempty_string(expected_commit) or not _nonempty_string(expected_tree):
        validation.error("source_binding", "expected_commit and expected_tree are required")
    start_ref, start_doc = _ref_with_assertions(spec.get("start_manifest"), repo_root, validation, "source_binding.start_manifest")
    end_ref, end_doc = _ref_with_assertions(spec.get("end_manifest"), repo_root, validation, "source_binding.end_manifest")
    diff_ref, diff_doc = _ref_with_assertions(spec.get("diff"), repo_root, validation, "source_binding.diff")
    start = _source_identity(start_doc)
    end = _source_identity(end_doc)
    zero_drift = False
    if start["commit"] != end["commit"] or start["tree"] != end["tree"]:
        validation.error("source_binding", f"start/end source identity differs: {start} vs {end}")
    if _nonempty_string(expected_commit):
        if start["commit"] != expected_commit or end["commit"] != expected_commit:
            validation.error("source_binding", "source commit does not match expected_commit")
    if _nonempty_string(expected_tree):
        if start["tree"] != expected_tree or end["tree"] != expected_tree:
            validation.error("source_binding", "source tree does not match expected_tree")
    if isinstance(start_doc, Mapping) and isinstance(end_doc, Mapping):
        start_files = start_doc.get("files")
        end_files = end_doc.get("files")
        if isinstance(start_files, Mapping) and isinstance(end_files, Mapping) and start_files != end_files:
            validation.error("source_binding", "start/end manifest file maps differ")
    if not isinstance(diff_doc, Mapping):
        validation.error("source_binding.diff", "diff document is not an object")
    else:
        for key in ("added", "deleted", "changed"):
            value = diff_doc.get(key)
            if value != []:
                validation.error("source_binding.diff", f"{key} is not empty: {value!r}")
        if diff_doc.get("clean_scope") is not True:
            validation.error("source_binding.diff", "clean_scope is not true")
        if diff_doc.get("start_head") != start["commit"] or diff_doc.get("end_head") != end["commit"]:
            validation.error("source_binding.diff", "diff head does not match start/end manifest")
        if diff_doc.get("start_tree") != start["tree"] or diff_doc.get("end_tree") != end["tree"]:
            validation.error("source_binding.diff", "diff tree does not match start/end manifest")
        zero_drift = (
            diff_doc.get("clean_scope") is True
            and diff_doc.get("added") == []
            and diff_doc.get("deleted") == []
            and diff_doc.get("changed") == []
            and start["commit"] == end["commit"] == expected_commit
            and start["tree"] == end["tree"] == expected_tree
        )
    return {
        "expected_commit": expected_commit,
        "expected_tree": expected_tree,
        "start_manifest": start_ref,
        "end_manifest": end_ref,
        "diff": diff_ref,
        "start_identity": start,
        "end_identity": end,
        "zero_drift": zero_drift,
    }


def _validate_semantic_source_hashes(mapping: Mapping[str, Any], repo_root: Path, validation: Validation) -> dict[str, str]:
    """Bind the semantic domain/test report to the current frozen bytes.

    The report itself is evidence, not source identity.  Requiring these two
    direct file hashes prevents a stale semantic report (for example one
    generated before the final domain fix) from being silently reused.
    """
    expected = mapping.get("semantic_source_hashes")
    if not isinstance(expected, Mapping):
        validation.error("semantic_source_hashes", "mapping is required for the domain/test semantic bind")
        return {}
    required = ("comsol_mcp/_domain_outcome.py", "tests/test_g3_domain_outcome.py")
    for path_name in required:
        if path_name not in expected:
            validation.error("semantic_source_hashes", f"required hash is missing for {path_name}")
    observed: dict[str, str] = {}
    for raw_path, expected_hash in expected.items():
        where = f"semantic_source_hashes[{raw_path!r}]"
        path, relative = _resolve_path(raw_path, repo_root, validation, where)
        if relative is None:
            continue
        if not isinstance(expected_hash, str) or len(expected_hash) != 64 or any(ch not in HEX64 for ch in expected_hash):
            validation.error(where, "expected a 64-character sha256")
            continue
        if path is None or not path.is_file():
            validation.error(where, "semantic source file does not exist")
            continue
        actual = _sha256(path)
        observed[relative] = actual
        if actual.lower() != expected_hash.lower():
            validation.error(where, f"source hash mismatch (expected {expected_hash}, got {actual})")
    return observed


def _validate_proof(name: str, spec: Any, repo_root: Path, validation: Validation, source: Mapping[str, Any]) -> list[dict[str, Any]]:
    specs = spec if isinstance(spec, list) else [spec]
    records: list[dict[str, Any]] = []
    if not specs or all(item is None for item in specs):
        validation.error(f"proofs.{name}", "at least one explicit proof reference is required")
        return records
    for index, item in enumerate(specs):
        item_map = _mapping(item) or {}
        ref_spec = item_map.get("ref", item)
        record, document = _ref_with_assertions(ref_spec, repo_root, validation, f"proofs.{name}[{index}]")
        if document is None:
            records.append(record)
            continue
        assertion_specs = item_map.get("assertions", [])
        if assertion_specs:
            if not isinstance(assertion_specs, list):
                validation.error(f"proofs.{name}[{index}]", "assertions must be a list")
            else:
                record["assertions"].extend(
                    _assertion(document, assertion, validation, f"proofs.{name}[{index}].assertions[{j}]")
                    for j, assertion in enumerate(assertion_specs) if isinstance(assertion, Mapping)
                )
        elif not record.get("assertions"):
            validation.error(f"proofs.{name}[{index}]", "proof must include at least one nested assertion")
        if name == "audit":
            identity = _mapping(item_map.get("source_identity"))
            if identity is None:
                validation.error(f"proofs.{name}[{index}]", "source_identity pointers are required for current audit binding")
            else:
                identity_values: dict[str, Any] = {}
                for key, expected_key in (("commit_pointer", "expected_commit"), ("tree_pointer", "expected_tree")):
                    pointer = identity.get(key)
                    if not isinstance(pointer, str):
                        validation.error(f"proofs.{name}[{index}]", f"{key} is required")
                        continue
                    ok, value = _json_pointer(document, pointer)
                    if not ok:
                        validation.error(f"proofs.{name}[{index}]", f"audit source pointer {pointer!r} was not found")
                    identity_values[key] = value if ok else None
                    expected = source.get(expected_key)
                    if ok and value != expected:
                        validation.error(f"proofs.{name}[{index}]", f"audit source does not match {expected_key}")
                record["source_identity"] = identity_values
        if name == "remote_sync":
            sync = _mapping(item_map.get("sync_equality"))
            if sync is None:
                validation.error(f"proofs.{name}[{index}]", "sync_equality pointers are required")
            else:
                values: dict[str, Any] = {}
                for key in ("local_pointer", "remote_pointer", "verified_pointer"):
                    pointer = sync.get(key)
                    if not isinstance(pointer, str):
                        validation.error(f"proofs.{name}[{index}]", f"{key} is required")
                        continue
                    ok, value = _json_pointer(document, pointer)
                    if not ok:
                        validation.error(f"proofs.{name}[{index}]", f"sync pointer {pointer!r} was not found")
                    values[key] = value if ok else None
                if not values.get("verified_pointer") is True:
                    validation.error(f"proofs.{name}[{index}]", "verified_pointer is not true")
                if not _nonempty_string(values.get("local_pointer")) or not _nonempty_string(values.get("remote_pointer")):
                    validation.error(f"proofs.{name}[{index}]", "local/remote sync identities are missing")
                elif values["local_pointer"] != values["remote_pointer"]:
                    validation.error(f"proofs.{name}[{index}]", "local and remote sync identities differ")
                record["sync_equality"] = values
        record["proof_name"] = item_map.get("name", name)
        records.append(record)
    return records


def _validate_special_case(case_id: str, spec: Mapping[str, Any], repo_root: Path, validation: Validation, where: str) -> dict[str, Any]:
    special = _mapping(spec.get("special")) or {}
    required: dict[str, tuple[str, str]] = {
        "C02": {
            "native_cleanup_fault": "NOT_RUN",
            "control_cleanup_fault": "PASS",
        },
        "C10": {
            "native": "PARTIAL",
            "control_mapping": "PASS",
        },
        "C11": {
            "nodes_not_run": "NOT_RUN",
            "cutplane_coverage": "PASS",
        },
        "C14": {
            "nodes_not_run": "NOT_RUN",
            "transient_coverage": "PASS",
        },
    }.get(case_id, {})
    output: dict[str, Any] = {}
    for name, expected in required.items():
        item = _mapping(special.get(name)) or {}
        ref_spec = item.get("ref", item)
        ref_record, document = _ref_with_assertions(ref_spec, repo_root, validation, f"{where}.special.{name}")
        actual = _status_from(document, ref_spec, validation, f"{where}.special.{name}") if document is not None else None
        if actual != expected:
            validation.error(f"{where}.special.{name}", f"observed status must remain {expected!r}, got {actual!r}")
        output[name] = {
            "observed_status": actual,
            "expected_status": expected,
            "reference": ref_record,
        }
        if case_id in ("C11", "C14") and name.endswith("coverage"):
            node_ref = output.get("nodes_not_run", {}).get("reference", {}).get("path")
            coverage_path = ref_record.get("path")
            if node_ref and coverage_path and node_ref == coverage_path:
                validation.error(f"{where}.special.{name}", "coverage must come from a separate run/file")
        if case_id == "C10" and name == "control_mapping":
            mapping_verified = item.get("mapping_verified")
            if mapping_verified is not True:
                validation.error(f"{where}.special.{name}", "mapping_verified must be explicit true")
            mapping_assertion = item.get("mapping_assertion")
            if not isinstance(mapping_assertion, Mapping):
                validation.error(f"{where}.special.{name}", "mapping_assertion must read an actual nested match")
            else:
                mapping_record = _assertion(
                    document,
                    mapping_assertion,
                    validation,
                    f"{where}.special.{name}.mapping_assertion",
                )
                output[name]["mapping_assertion"] = mapping_record
            output[name]["mapping_verified"] = mapping_verified
    if case_id == "C02":
        output["resolution"] = "control_pass_retains_native_not_run"
    elif case_id == "C10":
        output["resolution"] = "control_mapping_covers_native_partial"
    elif case_id in ("C11", "C14"):
        output["resolution"] = "separate_native_coverage_preserves_not_run"
    return output


def _validate_case(case_id: str, spec: Any, repo_root: Path, source: Mapping[str, Any], validation: Validation) -> dict[str, Any]:
    where = f"cases.{case_id}"
    case = _mapping(spec) or {}
    for key in ("scope", "evidence_level", "run_dir", "run_status_ref", "source_manifest_ref", "status_ref", "artifact_refs"):
        if key not in case:
            validation.error(where, f"{key} is required")
    run_dir, run_dir_relative = _resolve_path(case.get("run_dir"), repo_root, validation, f"{where}.run_dir")
    if run_dir is not None and not run_dir.is_dir():
        validation.error(f"{where}.run_dir", "run_dir is not an existing directory")
    run_status_ref, run_status = _ref_with_assertions(case.get("run_status_ref"), repo_root, validation, f"{where}.run_status_ref")
    source_ref, source_doc = _ref_with_assertions(case.get("source_manifest_ref"), repo_root, validation, f"{where}.source_manifest_ref")
    status_ref, status_doc = _ref_with_assertions(case.get("status_ref"), repo_root, validation, f"{where}.status_ref")
    observed_status = _status_from(status_doc, _mapping(case.get("status_ref")) or {}, validation, f"{where}.status_ref") if status_doc is not None else None
    required_status = case.get("required_status", "PASS")
    if observed_status != required_status:
        validation.error(where, f"observed case status must be {required_status!r}, got {observed_status!r}")
    source_identity = _source_identity(source_doc)
    if not _nonempty_string(source_identity.get("commit")) or not _nonempty_string(source_identity.get("tree")):
        validation.error(f"{where}.source_manifest_ref", "source commit/tree are missing")
    source_policy = case.get("source_binding_policy", "current")
    if source_policy == "historical_pin":
        if case_id not in ("C00", "C01"):
            validation.error(where, "historical_pin source policy is reserved for C00/C01")
        expected_case_commit = case.get("expected_source_commit", PIN_EXPECTED_COMMIT)
        expected_case_tree = case.get("expected_source_tree", PIN_EXPECTED_TREE)
        if source_identity.get("commit") != expected_case_commit:
            validation.error(where, "historical source commit differs from the recovered PIN")
        if source_identity.get("tree") != expected_case_tree:
            validation.error(where, "historical source tree differs from the recovered PIN")
    elif source_policy == "current":
        if source_identity.get("commit") != source.get("expected_commit"):
            validation.error(where, "case source commit differs from assembled expected commit")
        if source_identity.get("tree") != source.get("expected_tree"):
            validation.error(where, "case source tree differs from assembled expected tree")
    else:
        validation.error(where, f"unknown source_binding_policy {source_policy!r}")

    assertion_records: list[dict[str, Any]] = []
    for index, item in enumerate(case.get("assertion_refs", [])):
        record, _document = _ref_with_assertions(item, repo_root, validation, f"{where}.assertion_refs[{index}]")
        assertion_records.append(record)
    request_records: list[dict[str, Any]] = []
    for index, item in enumerate(case.get("request_reply_assertion_refs", [])):
        record, _document = _ref_with_assertions(item, repo_root, validation, f"{where}.request_reply_assertion_refs[{index}]")
        request_records.append(record)
    artifact_records: list[dict[str, Any]] = []
    for index, item in enumerate(case.get("artifact_refs", [])):
        record, _document = _ref_with_assertions(item, repo_root, validation, f"{where}.artifact_refs[{index}]", require_json=False)
        artifact_records.append(record)

    metadata = _metadata(run_status, source_doc, case, validation, where)
    special = _validate_special_case(case_id, case, repo_root, validation, where)
    accepted_status = "PASS"
    if case_id == "C10":
        accepted_status = "PASS_WITH_CONTROL_MAPPING"
    return {
        "case_id": case_id,
        "scope": case.get("scope"),
        "evidence_level": case.get("evidence_level"),
        "run_dir": run_dir_relative,
        "observed_status": observed_status,
        "accepted_status": accepted_status,
        "source": source_identity,
        "source_binding_policy": source_policy,
        "run_status": run_status_ref,
        "source_manifest": source_ref,
        "status_assertion": status_ref,
        "command": metadata["command"],
        "cwd": metadata["cwd"],
        "interpreter": metadata["interpreter"],
        "runtime": metadata["runtime"],
        "exit_code": metadata["exit_code"],
        "assertion_refs": assertion_records,
        "request_reply_assertion_refs": request_records,
        "artifact_hashes": artifact_records,
        "special": special,
    }


def assemble(mapping: Mapping[str, Any], mapping_path: Path, repo_root: Path) -> dict[str, Any]:
    validation = Validation()
    if mapping.get("schema") != INPUT_SCHEMA:
        validation.error("mapping.schema", f"expected {INPUT_SCHEMA!r}")
    source = _validate_source_binding(mapping, repo_root, validation)
    semantic_source_hashes = _validate_semantic_source_hashes(mapping, repo_root, validation)
    proofs_spec = _mapping(mapping.get("proofs")) or {}
    proofs: dict[str, list[dict[str, Any]]] = {}
    for proof_name in REQUIRED_PROOFS:
        if proof_name not in proofs_spec:
            validation.error("proofs", f"required proof {proof_name!r} is missing")
            proofs[proof_name] = []
        else:
            proofs[proof_name] = _validate_proof(proof_name, proofs_spec[proof_name], repo_root, validation, source)

    cases_spec = _mapping(mapping.get("cases")) or {}
    missing_cases = [case_id for case_id in CASE_IDS if case_id not in cases_spec]
    extra_cases = sorted(set(cases_spec) - set(CASE_IDS))
    for case_id in missing_cases:
        validation.error("cases", f"required case {case_id} is missing")
    for case_id in extra_cases:
        validation.error("cases", f"unexpected case {case_id!r}")
    cases: dict[str, Any] = {}
    for case_id in CASE_IDS:
        cases[case_id] = _validate_case(case_id, cases_spec.get(case_id), repo_root, source, validation)

    # The policy is intentionally explicit.  No case becomes PASS merely from
    # exit_code=0; the special records retain their native status in output.
    proofs_valid = bool(proofs) and all(bool(records) for records in proofs.values()) and not any(
        error.startswith("proofs") for error in validation.errors
    )
    cases_valid = not any(error.startswith("cases.") for error in validation.errors)
    all_accepted = cases_valid and all(case.get("accepted_status") in ("PASS", "PASS_WITH_CONTROL_MAPPING") for case in cases.values())
    sync_valid = proofs_valid and bool(proofs.get("remote_sync")) and not any(
        error.startswith("proofs.remote_sync") for error in validation.errors
    )
    overall = bool(source.get("zero_drift") and proofs_valid and all_accepted and sync_valid and not validation.errors)
    try:
        mapping_hash = _sha256(mapping_path)
    except OSError:
        mapping_hash = None
    return {
        "schema": SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_mapping": mapping_path.name,
        "input_mapping_sha256": mapping_hash,
        "repository_root": str(repo_root),
        "policy": {
            "required_cases": list(CASE_IDS),
            "required_proofs": list(REQUIRED_PROOFS),
            "source_manifest_start_end_must_be_identical": True,
            "native_not_run_is_retained": True,
            "native_partial_is_retained": True,
            "exit_code_alone_never_proves_pass": True,
            "overall_requires_all_gates_and_remote_sync": True,
        },
        "source_binding": source,
        "semantic_source_hashes": semantic_source_hashes,
        "proofs": proofs,
        "cases": cases,
        "gate_summary": {
            "source_zero_drift": bool(source.get("zero_drift")),
            "proofs_valid": proofs_valid,
            "cases_valid": cases_valid,
            "all_cases_accepted": all_accepted,
            "remote_sync_verified": sync_valid,
        },
        "overall_acceptance": overall,
        "validation": {
            "errors": validation.errors,
            "warnings": validation.warnings,
            "error_count": len(validation.errors),
        },
    }


def _placeholder_ref() -> dict[str, Any]:
    return {"path": None, "sha256": None}


def template() -> dict[str, Any]:
    def status_ref(expected: str) -> dict[str, Any]:
        ref = _placeholder_ref()
        ref.update({"status_pointer": "/status", "expected_status": expected})
        return ref

    cases: dict[str, Any] = {}
    for case_id in CASE_IDS:
        required_status = "PARTIAL" if case_id == "C10" else "PASS"
        case: dict[str, Any] = {
            "scope": "<fill: live/control/static/software scope>",
            "evidence_level": "<fill: protocol/numerical/static/software/control>",
            "run_dir": None,
            "run_status_ref": _placeholder_ref(),
            "source_manifest_ref": _placeholder_ref(),
            "status_ref": status_ref(required_status),
            "required_status": required_status,
            "metadata": {
                "command": None,
                "cwd": None,
                "interpreter": None,
                "runtime": None,
                "exit_code": None,
            },
            "assertion_refs": [],
            "request_reply_assertion_refs": [],
            "artifact_refs": [],
        }
        cases[case_id] = case
    cases["C02"]["special"] = {
        "native_cleanup_fault": {"ref": status_ref("NOT_RUN")},
        "control_cleanup_fault": {"ref": status_ref("PASS")},
    }
    cases["C10"]["special"] = {
        "native": {"ref": status_ref("PARTIAL")},
        "control_mapping": {
            "ref": status_ref("PASS"),
            "mapping_verified": True,
            "mapping_assertion": {"json_pointer": None, "equals": True},
        },
    }
    cases["C11"]["special"] = {
        "nodes_not_run": {"ref": status_ref("NOT_RUN")},
        "cutplane_coverage": {"ref": status_ref("PASS")},
    }
    cases["C14"]["special"] = {
        "nodes_not_run": {"ref": status_ref("NOT_RUN")},
        "transient_coverage": {"ref": status_ref("PASS")},
    }
    for historical_case in ("C00", "C01"):
        cases[historical_case].update({
            "source_binding_policy": "historical_pin",
            "expected_source_commit": PIN_EXPECTED_COMMIT,
            "expected_source_tree": PIN_EXPECTED_TREE,
        })
    return {
        "schema": INPUT_SCHEMA,
        "repository_root": "<fill: repository root or omit for CLI --repo-root>",
        "source_binding": {
            "expected_commit": DEFAULT_EXPECTED_COMMIT,
            "expected_tree": DEFAULT_EXPECTED_TREE,
            "start_manifest": _placeholder_ref(),
            "end_manifest": _placeholder_ref(),
            "diff": _placeholder_ref(),
        },
        "semantic_source_hashes": copy.deepcopy(DEFAULT_SEMANTIC_SOURCE_HASHES),
        "proofs": {
            "software": {"ref": _placeholder_ref(), "assertions": []},
            "wheel": {"ref": _placeholder_ref(), "assertions": []},
            "recovery": {"ref": _placeholder_ref(), "assertions": []},
            "audit": [{
                "name": "final_audit",
                "ref": _placeholder_ref(),
                "assertions": [],
                "source_identity": {
                    "commit_pointer": None,
                    "tree_pointer": None,
                },
            }],
            "remote_sync": {
                "ref": _placeholder_ref(),
                "assertions": [],
                "sync_equality": {
                    "local_pointer": None,
                    "remote_pointer": None,
                    "verified_pointer": None,
                },
            },
        },
        "cases": cases,
    }


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    # ``script_dir`` is .../repository/evidence/phase4_3/runs/<this-run>;
    # the recovered source root is therefore parents[3].  Keeping this
    # default correct lets a completed mapping omit a redundant absolute path
    # while still resolving all refs inside the repository boundary.
    parser.add_argument("--repo-root", type=Path, default=script_dir.parents[3], help="recovered repository root")
    parser.add_argument("--mapping", type=Path, help="completed acceptance assembly input JSON")
    parser.add_argument("--output", type=Path, default=script_dir / "assembled_acceptance_ledger.json")
    parser.add_argument("--write-template", type=Path, metavar="PATH", help="write a blank input skeleton and exit")
    args = parser.parse_args(argv)
    if args.write_template is not None:
        _write_json(args.write_template, template())
        print(f"wrote template: {args.write_template}")
        return 0
    if args.mapping is None:
        parser.error("--mapping is required unless --write-template is used")
    mapping_path = args.mapping.expanduser().resolve()
    repo_root = args.repo_root.expanduser().resolve()
    if not mapping_path.is_file():
        print(f"mapping does not exist: {mapping_path}", file=sys.stderr)
        return 2
    try:
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"cannot read mapping: {exc}", file=sys.stderr)
        return 2
    if not isinstance(mapping, Mapping):
        print("mapping root must be an object", file=sys.stderr)
        return 2
    ledger = assemble(mapping, mapping_path, repo_root)
    output = args.output.expanduser().resolve()
    _write_json(output, ledger)
    print(json.dumps({
        "output": str(output),
        "overall_acceptance": ledger["overall_acceptance"],
        "error_count": ledger["validation"]["error_count"],
    }, sort_keys=True))
    return 0 if ledger["overall_acceptance"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
