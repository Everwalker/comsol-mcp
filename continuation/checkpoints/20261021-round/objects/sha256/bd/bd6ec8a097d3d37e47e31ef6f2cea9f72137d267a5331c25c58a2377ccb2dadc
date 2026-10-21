#!/usr/bin/env python3
"""Prepare and execute one bounded, zero-solve W23 saved-MPH read-only audit.

The R21 direction-discovery candidate reopens only the exact Activity009
provisional MPH in a fresh task-owned project, enumerates actual Port/BMA and
solver/dataset links through public node reads, and recomputes prior R20 signed
observables offline. It never evaluates fields, submits Study/solver work, or
modifies/resaves the source model.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import json
import math
import re
import stat
import os
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID, uuid4

from tools import native_runtime_binding as fresh_runtime
from tools.w23_original_owner_hold import W23OriginalOwnerHold
from tools import w23_direction_discovery as direction
from tools import run_native_w23_te_science as science
from tools import w23_te_science_protocol as protocol

REPO = Path(__file__).resolve().parents[1]
EXPECTED_PYTHON = Path("/private/tmp/comsol-v2-runtime-20261011-new/comsol-mcp-recovery-runtime-20261004-001/python312/bin/python")
FIXTURE = REPO / "tools/java/NativeW23TEScienceFixture.java"
PLAN = REPO / "docs/full_project_execution/w23_overlap/W23_TE_FIXTURE_PROPOSAL.md"
TEST = REPO / "tests/test_native_w23_saved_mph_diagnostic.py"
EVIDENCE_ROOT = REPO / "docs/full_project_execution/w23_overlap/evidence"
ACTIVITY_ID = "w23-saved-mph-direction-discovery-20261015-001"
CANDIDATE_ID = "W23-TE-PORT-BMA-DIRECTION-DISCOVERY-READONLY-23"
CANDIDATE_REVISION = "candidate23-direction-r1"
PROJECT_LABEL = f"W23 Activity009 saved-MPH Port/BMA direction discovery {ACTIVITY_ID} {CANDIDATE_REVISION}"
SOURCE_MODEL = Path("/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository/docs/full_project_continuation_v2/evidence/w23_native_run_20261012_009_candidate17_r2/private-control-store/activity009-r2/saved-models/w23_coarse-phase0_provisional.mph")
SOURCE_MODEL_SHA256 = "cba0c1d404003c4e60a20b04316cb1456c38cc2b45596dc2dc29bbed62674a8c"
SOURCE_MODEL_BYTES = 13_795_478
R20_DIRECTION_REPORT = Path("/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V3/repository/_state/w23-zero8-r20/MODE_DIRECTION_SIGNED_POWER_REPORT_R1.json")
R20_DIRECTION_REPORT_SHA256 = "d6e00907e77b4a6d331a54e6b8ccb84d1eb7cbc19377b440231624cd157e9b38"
SOURCE_RECONCILIATION = Path("/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository/docs/full_project_continuation_v2/evidence/w23_native_run_20261012_009_candidate17_r2/ACTIVITY009_R2_RECONCILIATION.json")
SOURCE_RECONCILIATION_SHA256 = "9e2fe93acbc4a67bc0f87c8c93b2a02e695f03ea3ffcda9512a2eab99dcca7a3"
ROOT_PRESERVATION_CHECK = Path("/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository/docs/full_project_continuation_v2/evidence/w23_native_run_20261012_009_candidate17_r2/ROOT_PRESERVATION_CLEANUP_CHECK009_R2.json")
ROOT_PRESERVATION_CHECK_SHA256 = "27f6ad34cb2a82a8388f23f8c1e82f86d0616d5e8b3aa17a0c486c9c80bc3aa4"
ACTIVITY010_PRESERVATION_CHECK = Path("/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository/docs/full_project_continuation_v2/evidence/w23_native_run_20261012_010_saved_mph_diagnostic/ROOT_PRESERVATION_CLEANUP_CHECK010.json")
ACTIVITY010_PRESERVATION_CHECK_SHA256 = "871bc390e64136c5c2b0caaf7a07a6ddc4d8a3169b41cef899718a2446b04492"
MAX_BIRTH_SECONDS = 1800.0
CLEANUP_RESERVE_SECONDS = 120.0
MAX_RESULT_AT_POINTS_CALLS = 8
MAX_R21_RESULT_AT_POINTS_CALLS = 0
ALLOWED_MANAGED_OPERATIONS = frozenset({
    "server_connect", "model_load", "model.inspect", "dataset.list",
    "dataset.solution_indices",
    "operation_call",
})
ALLOWED_OPERATION_CALL_IDS = frozenset({"code.execute_java", "result.at_points"})
STUDY_SEQUENCE_TYPES = (
    "All", "None", "SolverSequence", "CopySolution",
    "ParametricStore", "Stored", "Parametric",
)
def fresh_budget() -> dict[str, Any]:
    return {"max_owned_server_processes": 1, "max_worker_sessions": 1,
            "max_gui_processes": 0, "max_study_run_calls": 0, "max_solver_run_calls": 0,
            "max_result_at_points_calls": MAX_R21_RESULT_AT_POINTS_CALLS,
            "max_seconds_from_server_birth": int(MAX_BIRTH_SECONDS),
            "cleanup_reserve_seconds": int(CLEANUP_RESERVE_SECONDS), "diagnostic_only": True}


def direction_graph_is_resolved(graph: Mapping[str, Any], *,
                                expected_prefix: str = direction.EXPECTED_W23_FIELD_PREFIX) -> bool:
    """Return whether the public-read graph clears the diagnostic completeness gate."""
    if not isinstance(graph, Mapping):
        return False
    waves = graph.get("electromagnetic_waves")
    namespace = waves.get("namespace_correlation") if isinstance(waves, Mapping) else None
    ports = graph.get("port_name_binding_status")
    bindings = graph.get("bma_solution_bindings")
    return (
        isinstance(namespace, Mapping)
        and direction.physics_namespace_is_resolved(namespace, expected_prefix=expected_prefix)
        and isinstance(ports, Mapping) and bool(ports)
        and all(value == "UNIQUE_EXACT_PORT_NAME" for value in ports.values())
        and isinstance(bindings, list) and bool(bindings)
        and all(row.get("status") == "UNIQUE_EXACT_LINK"
                for row in bindings if isinstance(row, Mapping))
        and all(isinstance(row, Mapping) for row in bindings)
    )

def _activity(freeze: Mapping[str, Any]) -> str:
    binding = freeze.get("fresh_runtime_binding")
    return ("w23-saved-mph-direction-discovery-" + binding["activity_id"]) if binding else ACTIVITY_ID


def _fresh_candidate_identity(binding: Mapping[str, Any]) -> dict[str, str]:
    activity_token = binding.get("activity_id")
    if not isinstance(activity_token, str):
        raise RuntimeError("fresh candidate identity requires the validated canonical activity UUID")
    try:
        canonical_activity_token = str(UUID(activity_token))
    except ValueError as exc:
        raise RuntimeError("fresh candidate identity requires the validated canonical activity UUID") from exc
    if canonical_activity_token != activity_token:
        raise RuntimeError("fresh candidate identity requires the validated canonical activity UUID")
    activity_id = "w23-saved-mph-direction-discovery-" + activity_token
    candidate_id = "W23-TE-PORT-BMA-DIRECTION-DISCOVERY-" + activity_token
    candidate_revision = "candidate-" + activity_token + "-direction-r1"
    return {
        "activity_id": activity_id,
        "candidate_id": candidate_id,
        "candidate_revision": candidate_revision,
        "project_label": f"W23 Activity009 saved-MPH Port/BMA direction discovery {activity_id} {candidate_revision}",
        "activity_token": activity_token,
    }


def _bind_fresh_candidate_identity(binding: Mapping[str, Any]) -> dict[str, str]:
    identity = _fresh_candidate_identity(binding)
    global ACTIVITY_ID, CANDIDATE_ID, CANDIDATE_REVISION, PROJECT_LABEL
    ACTIVITY_ID = identity["activity_id"]
    CANDIDATE_ID = identity["candidate_id"]
    CANDIDATE_REVISION = identity["candidate_revision"]
    PROJECT_LABEL = identity["project_label"]
    return identity


def plane_field_expressions(port: str) -> tuple[str, ...]:
    if port not in {"1", "2"}:raise ValueError("field plane must bind port1 orport2")
    return tuple([f"down(emw.{kind}{axis})" for kind in ("E", "H") for axis in "xyz"]
        + [f"emw.{kind}{axis}_{port}" for kind in ("Emode", "Hmode") for axis in "xyz"] + ["emw.nx", "emw.ny"])

RAW_FIELD_EXPRESSIONS = plane_field_expressions("2")
MODE_POINT_EXPRESSIONS = protocol.bma_point_expressions("1", field_scheme="emw_down")
FREQUENCY_EXPRESSIONS = ("freq",)
FREQUENCY_UNITS = {"freq": "Hz"}
FIELD_UNITS = {
    **{f"down(emw.E{axis})": "V/m" for axis in "xyz"},
    **{f"down(emw.H{axis})": "A/m" for axis in "xyz"},
    **{f"emw.Emode{axis}_{suffix}": "V/m" for axis in "xyz" for suffix in ("1", "2")},
    **{f"emw.Hmode{axis}_{suffix}": "A/m" for axis in "xyz" for suffix in ("1", "2")},
    # The consumer accepts either the historical explicit unit "1" or the
    # exact native-default empty string for these normal expressions. The
    # requested-unit witness binds which path was used; it does not equate them.
    "emw.nx": ("1", ""), "emw.ny": ("1", ""),
}
def plane_field_units(port: str) -> tuple[str, ...]:
    return tuple("" if e in {"emw.nx", "emw.ny"} else FIELD_UNITS[e]
                 for e in plane_field_expressions(port))
def plane_field_legacy_units(port: str) -> tuple[str, ...]:
    """Retain the old explicit normal-unit request for stored consumers."""
    return tuple("1" if e in {"emw.nx", "emw.ny"} else FIELD_UNITS[e]
                 for e in plane_field_expressions(port))
FIELD_REQUESTED_UNITS = plane_field_units("2")
def plane_selection(port: str) -> dict[str, Any]:
    return {"kind":"named","component":"comp1","geometry":"geom1","entity_dimension":1,
            "tag":"selInputPort" if port=="1" else "selReceiverX8"}


def mode_units(port_name: str) -> dict[str, str]:
    beta, neff, k0 = protocol.bma_point_expressions(port_name, field_scheme="emw_down")
    return {beta: "1/m", neff: "1", k0: "1/m"}
MODE_DATASETS = {"dset2": ("sol2", "bmaInput", "1", -10.0),
                 "dset3": ("sol3", "bmaOutput", "2", 8.0)}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                    sort_keys=True, default=str, allow_nan=False) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)


def _source_files() -> dict[str, dict[str, Any]]:
    """Extend the exact, already-frozen W23 runtime closure with this diagnostic."""
    rows = science._source_files()
    parent_path = REPO / "PARENT_MERGED_SOURCE_CLOSURE_R11_R7.json"
    if not parent_path.is_file():
        parent_path = REPO / "continuation/candidates/w23-r15/PARENT_MERGED_SOURCE_CLOSURE_R11_R7.json"
    parent = json.loads(parent_path.read_text())
    for relative in parent["source_files"]:
        path = REPO / relative
        rows[relative] = {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": sha256_file(path)}
    for path in (REPO / "tests/test_saved_mph_activity017_public_stack.py", parent_path):
        rows[path.relative_to(REPO).as_posix()] = {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": sha256_file(path)}
    additional = (
        Path(__file__).resolve(),
        (REPO / "tools/w23_direction_discovery.py").resolve(),
        (REPO / "tools/native_runtime_binding.py").resolve(),
        (REPO / "tests/test_native_w23_saved_mph_diagnostic.py").resolve(),
        (REPO / "tests/test_w23_r21_direction_discovery.py").resolve(),
        (REPO / "tests/test_w23_fresh_import_home.py").resolve(),
        (REPO / "tools/w23_original_owner_hold.py").resolve(),
        (REPO / "tests/test_w23_original_owner_hold.py").resolve(),
        (REPO / "tests/test_native_w23_te_science_protocol.py").resolve(),
        (REPO / "tests/test_native_w23_te_science_runner.py").resolve(),
        (REPO / "tests/test_unit_a_node_public_api.py").resolve(),
        (REPO / "tests/test_g2_registry_packaging.py").resolve(),
        (REPO / "tests/test_g2_security_guards.py").resolve(),
        (REPO / "docs/comsol_mcp_design_v1/02_ACTION_CATALOG.json").resolve(),
        (REPO / "docs/full_project_execution/contract/02_ACTION_CATALOG.json").resolve(),
        (REPO / "tests/test_g3_results.py").resolve(),
        (REPO / "tests/test_interp_native_default_units.py").resolve(),
        (REPO / "docs/comsol_mcp_design_v1/common.schema.json").resolve(),
        PLAN.resolve(),
        (REPO / "tests/test_saved_mph_offline_materialization.py").resolve(),
        (REPO / "tests/test_activity018_no_outer_transcript.py").resolve(),
        (REPO / "tests/test_activity019_private_singleton_ticket.py").resolve(),
        (REPO / "tests/test_activity021_definition_boundary_metadata.py").resolve(),
        (REPO / "tests/test_activity022_boundary_selection_ticket.py").resolve(),
        (REPO / "tests/test_activity022_r15_measure_all_and_literal_entry.py").resolve(),
        (REPO / "tests/fixtures/w23_activity022/ACTUAL_R14_WRONG_LITERAL_ENTRY.zsh").resolve(),
        (REPO / "tests/fixtures/w23_activity021/ACTUAL021_DEFINITION_BOUNDARY_RAW.json").resolve(),
        (REPO / "tests/fixtures/w23_activity020/ALL_THREE_ACTUAL020_POINT_JOBS_ORIGINAL_TYPED_EVENTS.json").resolve(),
        *((REPO / "tests/fixtures/w23_activity019" / name).resolve() for name in (
            "ACTUAL_019_HZ_TYPED_TICKET_PROJECTION.json", "ACTUAL018_019_PORT_DATASET_TYPED_PROJECTIONS.json")),
        (REPO / "tests/fixtures/w23_activity018/ACTUAL_DSET1_TYPED_TRANSCRIPT_AND_INVENTORY.json").resolve(),
        (REPO / "tests/fixtures/w23_activity011_normalized_saved_mph_inventory.json").resolve(),
        *((REPO / "tests/fixtures/w23_activity015" / name).resolve() for name in (
            "physical_frequency_Hz_terminal_response.json", "managed_solution_indices_dset1_terminal_response.json",
            "ACTUAL_HZ_JOB_ALL_WORKER_EVENTS.json", "ROOT_ACTIVITY015_COUNT_ONLY_PUBLICATION_DECISION.json", "PROVENANCE.json")),
        *((REPO / "tests/fixtures/w23_activity014" / name).resolve() for name in (
            "physical_frequency_Hz_terminal_response.json", "ACTUAL_OPERATIONSTORE_HZ_REQUEST.json",
            "ROOT_SINGLETON_COUNT_NATIVE_LABEL_DECISION.json", "PROVENANCE.json")),
        *((REPO / "docs/full_project_execution/w23_overlap/references" / name).resolve() for name in (
            "PRIMARY_INTERP_GETDATA_17293.json", "PRIMARY_INTERP_UNIT_17294.json", "PRIMARY_NUMERICAL_GETDATA_22986.json")),
    )
    additional += tuple(sorted((REPO / "docs/full_project_execution/w23_overlap/references/activity022").glob("*")))
    for path in additional:
        if not path.is_file():
            raise FileNotFoundError(path)
        rows[path.relative_to(REPO).as_posix()] = {
            "path": str(path), "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    return dict(sorted(rows.items()))


def _source_digest(rows: Mapping[str, Any]) -> str:
    return science._source_manifest_sha256(rows)


def _validate_diagnostic_scope(freeze: Mapping[str, Any]) -> None:
    limits = freeze.get("limits")
    plan = freeze.get("read_only_call_plan")
    if not isinstance(limits, Mapping) or not isinstance(plan, Mapping):
        raise RuntimeError("R21 freeze lacks closed direction-discovery limits or read-only call plan")
    if plan.get("mode") == "direction_discovery_only":
        try:
            direction.validate_direction_limits(plan)
        except direction.DirectionDiscoveryError as exc:
            raise RuntimeError(str(exc)) from exc
        if (limits.get("max_result_at_points_calls") != MAX_R21_RESULT_AT_POINTS_CALLS
                or limits.get("max_study_run_calls") != 0
                or limits.get("max_solver_run_calls") != 0
                or plan.get("native_operation_budget") != {
                    "Study.run": 0, "solver.run": 0, "result.at_points": 0,
                    "node.property_set": 0, "code.execute_java": 0, "model.save": 0}
                or plan.get("legacy_r20_point_call_limit") != MAX_RESULT_AT_POINTS_CALLS
                or plan.get("offline_quadrature") != {
                    "method": "composite Simpson", "coarse_points": 641,
                    "fine_points": 1281, "relative_tolerance": 1e-3}
                or protocol.INTEGRAL_RELATIVE_TOLERANCE != 1e-3):
            raise RuntimeError("R21 must be zero-point/zero-Study/zero-solver with the R20 641/1281 Simpson 1e-3 protocol retained offline")
        return
    # The prior R20 saved-MPH candidate keeps its exact eight result-point
    # contract. R21's stricter budget is selected only by its explicit mode.
    expected_slots = ["freq_Hz", "freq_GHz", "fields_output_x8_641", "fields_output_x8_1281",
                      "fields_input_xminus10_641", "fields_input_xminus10_1281",
                      "mode_point_bmaInput", "mode_point_bmaOutput"]
    expected_quadrature = {"method": "composite Simpson", "coarse_points": 641,
                           "fine_points": 1281, "relative_tolerance": 1e-3}
    if (limits.get("max_result_at_points_calls") != MAX_RESULT_AT_POINTS_CALLS
            or limits.get("max_study_run_calls") != 0 or limits.get("max_solver_run_calls") != 0
            or plan.get("ordered_point_slots") != expected_slots
            or plan.get("quadrature_refinement") != expected_quadrature
            or protocol.INTEGRAL_RELATIVE_TOLERANCE != 1e-3):
        raise RuntimeError("legacy saved-MPH readback must preserve its eight 641/1281 point slots and Simpson 1e-3 tolerance")


def _assert_source_inputs() -> dict[str, Any]:
    references = {
        "source_model": (SOURCE_MODEL, SOURCE_MODEL_BYTES, SOURCE_MODEL_SHA256),
        "source_reconciliation": (SOURCE_RECONCILIATION, None, SOURCE_RECONCILIATION_SHA256),
        "root_preservation_check": (ROOT_PRESERVATION_CHECK, None, ROOT_PRESERVATION_CHECK_SHA256),
        "activity010_preservation_check": (ACTIVITY010_PRESERVATION_CHECK, None, ACTIVITY010_PRESERVATION_CHECK_SHA256),
        "r20_direction_report": (R20_DIRECTION_REPORT, None, R20_DIRECTION_REPORT_SHA256),
    }
    prior_direction = json.loads(R20_DIRECTION_REPORT.read_text(encoding="utf-8"))
    field_rows = prior_direction.get("raw_EH_mode_and_signal_Poynting", {}).get("field_files")
    if not isinstance(field_rows, list) or len(field_rows) != 4:
        raise RuntimeError("frozen R20 direction report must identify exactly four raw field files")
    for row in field_rows:
        if not isinstance(row, Mapping) or row.get("plane") not in {"input", "output"}:
            raise RuntimeError("frozen R20 direction report has a malformed field-file row")
        count = row.get("sample_count")
        if type(count) is not int or count not in protocol.SAVED_MPH_QUADRATURE_SAMPLE_COUNTS:
            raise RuntimeError("frozen R20 direction report field grid differs from 641/1281")
        label = f"r20_{row['plane']}_{count}"
        if label in references:
            raise RuntimeError("frozen R20 direction report contains a duplicate field row")
        references[label] = (Path(row["field_file"]), None, row.get("field_file_sha256"))
    result: dict[str, Any] = {}
    for label, (path, expected_bytes, expected_sha) in references.items():
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError(f"{label} is absent or is not a regular preserved file: {path}")
        observed_sha = sha256_file(path)
        observed_bytes = path.stat().st_size
        if observed_sha != expected_sha or (expected_bytes is not None and observed_bytes != expected_bytes):
            raise RuntimeError(f"{label} differs from the exact preserved input: bytes={observed_bytes}, sha256={observed_sha}")
        result[label] = {"path": str(path), "bytes": observed_bytes,
                         "sha256": observed_sha, "status": "PASS_EXACT_INPUT_IDENTITY"}
    return result


def _offline_r20_direction_report() -> dict[str, Any]:
    """Recompute signed direction observables from exact preserved R20 arrays only."""
    prior = json.loads(R20_DIRECTION_REPORT.read_text(encoding="utf-8"))
    rows = prior["raw_EH_mode_and_signal_Poynting"]["field_files"]
    raw_fields: dict[int, dict[str, Path]] = {}
    expected_hashes: dict[str, str] = {}
    for row in rows:
        count = int(row["sample_count"])
        plane = "input" if row["plane"] == "input" else "output"
        if count not in raw_fields:
            raw_fields[count] = {}
        if plane in raw_fields[count]:
            raise RuntimeError("frozen R20 direction report has a duplicate plane/grid raw field")
        path = Path(row["field_file"])
        raw_fields[count][plane] = path
        expected_hashes[str(path)] = str(row["field_file_sha256"])
    return direction.offline_signed_poynting_report(
        raw_fields=raw_fields, expected_hashes=expected_hashes,
        prior_report=R20_DIRECTION_REPORT,
        expected_prior_sha256=R20_DIRECTION_REPORT_SHA256)


def _native_argv(freeze_path: Path, freeze_sha256: str, approval_path: Path,
                 input_path: Path, work: Path, evidence: Path,
                 binding: Mapping[str, Any] | None = None) -> list[str]:
    return [
        "env", "-u", "CODEX_NETWORK_GUARD_LOG",
        "-u", "CODEX_NETWORK_GUARD_LABEL",
        "-u", "CODEX_NETWORK_GUARD_RUN_ID",
        "TMPDIR=/private/tmp", f"PYTHONPATH={REPO}",
        *([f"COMSOL_SERVER_MCP_HOME={Path(binding['namespace']) / 'work/mcp-home'}"] if binding else []),
        str(binding["python"]["entry"] if binding else EXPECTED_PYTHON), "-m", "tools.run_native_w23_saved_mph_diagnostic", "execute",
        "--freeze", str(freeze_path), "--freeze-sha256", freeze_sha256,
        "--approval", str(approval_path), "--saved-model", str(input_path),
        "--work", str(work), "--evidence", str(evidence),
    ]


def _native_environment(binding: Mapping[str, Any] | None = None) -> dict[str, Any]:
    environment = {"TMPDIR": "/private/tmp", "PYTHONPATH": str(REPO),
                   "CODEX_NETWORK_GUARD_LOG": None, "CODEX_NETWORK_GUARD_LABEL": None,
                   "CODEX_NETWORK_GUARD_RUN_ID": None}
    if binding:
        environment["COMSOL_SERVER_MCP_HOME"] = str(Path(binding["namespace"]) / "work/mcp-home")
    return environment


def _validate_fresh_import_home(binding: Mapping[str, Any], work: Path) -> dict[str, Any]:
    """Inspect actual legacy import roots before any native birth; never rebind globals."""
    namespace = Path(binding["namespace"])
    fresh_runtime.namespace(namespace)
    expected = namespace / "work/mcp-home"
    if work != namespace / "work" or os.environ.get("COMSOL_SERVER_MCP_HOME") != str(expected):
        raise RuntimeError("fresh import home must be exact owned namespace/work/mcp-home before Python import")
    if namespace.stat().st_dev != Path("/private/tmp").stat().st_dev:
        raise RuntimeError("fresh import home must remain on the private task filesystem")
    if sys.platform != "darwin":
        raise RuntimeError("fresh import home APFS verification requires macOS")
    mounted = subprocess.run(["/sbin/mount"], capture_output=True, text=True, timeout=10, check=True)
    apfs_mounts = []
    for line in mounted.stdout.splitlines():
        if " on " not in line or " (apfs," not in line:
            continue
        mountpoint = Path(line.split(" on ", 1)[1].rsplit(" (", 1)[0])
        try:
            if mountpoint.stat().st_dev == namespace.stat().st_dev:
                apfs_mounts.append(str(mountpoint))
        except OSError:
            continue
    if not apfs_mounts:
        raise RuntimeError("fresh import home filesystem is not observed as APFS")
    for path in (work, expected, expected / "logs", expected / "outputs"):
        if path.is_symlink() or path.resolve(strict=False) != path:
            raise RuntimeError("fresh import home ancestry must not contain symlinks")
        if path.exists():
            st = path.stat()
            if not path.is_dir() or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077:
                raise RuntimeError("fresh import home directories must be owner-private")
    server = importlib.import_module("comsol_mcp._server")
    if server.active_runtime_context() is not None:
        raise RuntimeError("fresh prebirth legacy import must not inherit a selected runtime context")
    roots = {"COMSOL_SERVER_MCP_HOME": expected, "LOGS_DIR": expected / "logs",
             "OUTPUTS_DIR": expected / "outputs", "STATUS_FILE": expected / "status.json",
             "WORKFLOW_FILE": expected / "workflow_state.json", "OPERATIONS_FILE": expected / "logs/operations.jsonl",
             "SERVER_LOG": expected / "logs/server.log"}
    actual = {name: str(server.runtime_setting(name)) for name in roots}
    if any(actual[name] != str(path) for name, path in roots.items()):
        raise RuntimeError("actual imported legacy runtime write roots differ from fresh owned home (import occurred too early)")
    return {"status": "PASS_ACTUAL_IMPORTED_LEGACY_WRITE_ROOTS_BEFOREBIRTH", "paths": actual,
            "filesystem": {"type": "apfs", "device": namespace.stat().st_dev, "observed_mountpoints": apfs_mounts}}


def _validate_literal_capture_entry(script_path: Path, metadata: Mapping[str, Any], approval: Mapping[str, Any], freeze: Mapping[str, Any], freeze_sha256: str) -> dict[str, Any]:
    """Read actual fixed shell bytes, then run the original four-argument guard."""
    import shlex
    text = script_path.read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    exec_lines = [line for line in lines if line.startswith("exec ")]
    if len(exec_lines)!=1 or any(token in exec_lines[0] for token in ("$", "`", "\\", ";", "&&", "||")):
        raise RuntimeError("fixed capture must contain one literal exec argv")
    tokens = shlex.split(exec_lines[0])[1:]
    try:cut=tokens.index(">")
    except ValueError as exc:raise RuntimeError("fixed capture lacks stdout capture redirection") from exc
    actual = tokens[:cut]
    declared = metadata["exact_native_invocation"]
    if actual != declared["argv"]:
        raise RuntimeError("literal script argv differs from fixed metadata argv")
    path = str(freeze["approval_path_expected"])
    if (actual[actual.index("--approval")+1]!=path or [shlex.split(l)[2] for l in lines if l.startswith("test -f ")]!=[path]
        or [shlex.split(l)[3] for l in lines if l.startswith("test ! -L ") and "ROOT_APPROVAL_ACTIVITY" in l]!=[path]
        or [shlex.split(l)[1] for l in lines if l.startswith("cd ")]!=[declared["cwd"]]):
        raise RuntimeError("actual approval guards or cwd differ from frozen invocation")
    _validate_approval(approval, freeze, freeze_sha256, actual)
    return {"status":"PASS_ACTUAL_LITERAL_SCRIPT_ARGV_AND_APPROVAL_GUARDS_FOUR_ARG_VALIDATOR", "actual_argv":actual,
        "script_sha256":sha256_file(script_path),"actual_approval_path":path,"native":"NOT_RUN","Rootapproval":"REFERENCE_ONLY"}


def _strict_test_environment(evidence: Path, *, activity_id: str | None = None) -> dict[str, str]:
    guard = Path("/Volumes/SSD/Comsol-MCP/execution-scratch/temp-loss-runtime-recovery-20261004-001/builder-preparation022/network-guard")
    if not guard.is_dir():
        raise FileNotFoundError(f"required strict offline test guard is unavailable: {guard}")
    env = dict(os.environ)
    env["TMPDIR"] = "/private/tmp"
    env["PYTHONPATH"] = os.pathsep.join((str(guard), str(REPO)))
    env["CODEX_NETWORK_GUARD_LOG"] = str(evidence / "diagnostic-network-guard.jsonl")
    activity_id = activity_id or ACTIVITY_ID
    env["CODEX_NETWORK_GUARD_LABEL"] = f"w23_{activity_id}_{CANDIDATE_REVISION}_focused"
    env["CODEX_NETWORK_GUARD_RUN_ID"] = f"{activity_id}-{CANDIDATE_REVISION}-focused-001"
    return env


def _r13_inherited_base_record() -> dict[str, Any]:
    base_root = science.W23_R13_BASE_ROOT
    manifest_path = base_root / "prepare/source_manifest.json"
    junit_path = base_root / "prepare/offline_gates/activity024_focused_junit.xml"
    compile_path = base_root / "prepare/offline_gates/compile.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    junit_counts = science._junit_counts(junit_path)
    if (sha256_file(manifest_path) != science.W23_R13_BASE_SOURCE_MANIFEST_SHA256
            or sha256_file(junit_path) != science.W23_R13_BASE_JUNIT_SHA256
            or sha256_file(compile_path) != science.W23_R13_BASE_COMPILE_SHA256
            or manifest.get("source_manifest_sha256") != science.W23_R13_BASE_SOURCE_MAP_SHA256
            or manifest.get("source_count") != science.W23_R13_BASE_SOURCE_COUNT
            or junit_counts != {"tests": science.W23_R13_BASE_TEST_COUNT,
                                "failures": 0, "errors": 0, "skipped": 0}):
        raise RuntimeError("pinned R13 baseline source/JUnit/compile identity changed")
    return {
        "candidate_id": science.W23_R13_BASE_CANDIDATE_ID,
        "candidate_revision": science.W23_R13_BASE_CANDIDATE_REVISION,
        "activity_id": science.W23_R13_BASE_ACTIVITY_ID,
        "source_manifest_path": str(manifest_path),
        "source_manifest_sha256": science.W23_R13_BASE_SOURCE_MANIFEST_SHA256,
        "source_map_sha256": science.W23_R13_BASE_SOURCE_MAP_SHA256,
        "source_count": science.W23_R13_BASE_SOURCE_COUNT,
        "junit_path": str(junit_path),
        "junit_sha256": science.W23_R13_BASE_JUNIT_SHA256,
        "junit_counts": junit_counts,
        "compile_receipt_sha256": science.W23_R13_BASE_COMPILE_SHA256,
    }


def _source_delta_from_r13(current_files: Mapping[str, Any]) -> dict[str, Any]:
    base_path = science.W23_R13_BASE_ROOT / "prepare/source_manifest.json"
    base = json.loads(base_path.read_text(encoding="utf-8"))["source_files"]
    common_changes = []
    for name in sorted(set(base) & set(current_files)):
        before = science._source_content_row(base[name], label=f"R13 {name}")
        after = science._source_content_row(current_files[name], label=f"candidate {name}")
        if before != after:
            common_changes.append({"path": name, "base_bytes": before[0],
                                  "base_sha256": before[1], "candidate_bytes": after[0],
                                  "candidate_sha256": after[1]})
    additions = []
    for name in sorted(set(current_files) - set(base)):
        size, digest = science._source_content_row(current_files[name], label=f"candidate {name}")
        additions.append({"path": name, "candidate_bytes": size, "candidate_sha256": digest})
    return {"common_path_changes": common_changes, "added_paths": additions,
            "removed_paths": sorted(set(base) - set(current_files))}


def _run_focused_delta_tests(offline: Path, *, binding: Mapping[str, Any] | None,
                             current_full: bool = False,
                             expected_source_manifest_sha256: str | None = None) -> dict[str, Any]:
    if current_full:
        test_paths = [
            REPO / "tests/test_interp_native_default_units.py",
            REPO / "tests/test_w23_r21_direction_discovery.py",
            REPO / "tests/test_unit_a_node_public_api.py::test_named_public_property_schema_reads_has_property_before_typed_metadata_and_get",
            REPO / "tests/test_unit_a_node_public_api.py::test_named_public_property_schema_reports_absent_or_unknown_before_metadata",
            REPO / "tests/test_native_w23_te_science_protocol.py::test_requested_native_default_allowlist_includes_only_exact_neff_and_xy_normals",
            REPO / "tests/test_native_w23_te_science_protocol.py::test_bma_neff_native_default_is_exact_and_legacy_dimensionless_request_stays_valid",
            REPO / "tests/test_native_w23_te_science_protocol.py::test_saved_mph_641_to_1281_quadrature_refinement_is_a_separate_explicit_gate",
            REPO / "tests/test_native_w23_te_science_protocol.py::test_inherited_offline_delta_validator_positive_and_negative_controls",
            REPO / "tests/test_native_w23_te_science_protocol.py::test_current_full_offline_receipt_positive_and_negative_controls",
            REPO / "tests/test_native_w23_te_science_protocol.py::test_offline_preflight_current_full_binds_gate_hashes_same_map",
            REPO / "tests/test_native_w23_saved_mph_diagnostic.py::test_saved_field_and_bma_point_reads_allow_only_the_frozen_dataset_grid_and_expressions",
            REPO / "tests/test_native_w23_saved_mph_diagnostic.py::test_direction_freeze_binds_zero_reads_and_preserved_offline_quadrature",
            REPO / "tests/test_native_w23_saved_mph_diagnostic.py::test_saved_mph_freeze_binds_641_1281_eight_reads_zero_solve_and_fixed_tolerance",
            REPO / "tests/test_native_w23_saved_mph_diagnostic.py::test_direction_discovery_mode_never_opens_or_stages_java_fixture",
            REPO / "tests/test_native_w23_saved_mph_diagnostic.py::test_current_full_runner_packages_source_bound_gate_receipt",
            REPO / "tests/test_native_w23_saved_mph_diagnostic.py::test_fresh_direction_identity_uses_binding_uuid_instead_of_old_static_ids",
            REPO / "tests/test_native_w23_saved_mph_diagnostic.py::test_legacy_fixture_stage_restores_exact_private_copy_for_legacy_mode",
            REPO / "tests/test_native_w23_saved_mph_diagnostic.py::test_legacy_fixture_stage_refuses_existing_target_and_wrong_frozen_hash",
            REPO / "tests/test_native_w23_saved_mph_diagnostic.py::test_completed_point_projection_keeps_verified_calls_separate_from_unknown_attempts",
            REPO / "tests/test_native_w23_saved_mph_diagnostic.py::test_query_once_persists_unknown_point_attempt_without_completed_native_call",
            REPO / "tests/test_native_w23_saved_mph_diagnostic.py::test_outer_guard_retains_any_live_original_popen_for_blocked_or_unknown_cleanup",
            REPO / "tests/test_native_w23_saved_mph_diagnostic.py::test_outer_guard_does_not_hold_when_all_original_children_are_already_terminal",
            REPO / "tests/test_native_w23_saved_mph_diagnostic.py::test_outer_guard_treats_unobservable_original_children_as_active",
            REPO / "tests/test_native_w23_te_science_runner.py::test_socket_owner_gate_keeps_loopback_time_wait_inactive_and_not_a_worker_peer",
            REPO / "tests/test_native_w23_te_science_runner.py::test_socket_owner_gate_rejects_external_time_wait_even_when_state_is_inactive",
            REPO / "tests/test_native_w23_te_science_runner.py::test_direct_rpc_journal_separates_managed_requests_and_keeps_unknown_terminal_blocker",
            REPO / "tests/test_native_w23_te_science_runner.py::test_durable_unknown_cleanup_idle_requires_terminal_rpc_fresh_health_and_exact_endpoint",
            REPO / "tests/test_g3_results.py::test_w23_decoder_consumes_actual_requested_unit_native_adapter_witness",
            REPO / "tests/test_g3_results.py::test_real_only_port_scalars_require_actual_adapter_flag_and_exact_validated_binding",
            REPO / "tests/test_g2_registry_packaging.py",
            REPO / "tests/test_g2_security_guards.py",
        ]
    else:
        test_paths = [
        REPO / "tests/test_interp_native_default_units.py",
        REPO / "tests/test_w23_r21_direction_discovery.py",
        REPO / "tests/test_native_w23_te_science_protocol.py::test_requested_native_default_allowlist_includes_only_exact_neff_and_xy_normals",
        REPO / "tests/test_native_w23_te_science_protocol.py::test_bma_neff_native_default_is_exact_and_legacy_dimensionless_request_stays_valid",
        REPO / "tests/test_native_w23_te_science_protocol.py::test_saved_mph_641_to_1281_quadrature_refinement_is_a_separate_explicit_gate",
        REPO / "tests/test_native_w23_te_science_protocol.py::test_inherited_offline_delta_validator_positive_and_negative_controls",
        REPO / "tests/test_native_w23_saved_mph_diagnostic.py::test_saved_field_and_bma_point_reads_allow_only_the_frozen_dataset_grid_and_expressions",
        REPO / "tests/test_native_w23_saved_mph_diagnostic.py::test_direction_freeze_binds_zero_reads_and_preserved_offline_quadrature",
        REPO / "tests/test_native_w23_saved_mph_diagnostic.py::test_saved_mph_freeze_binds_641_1281_eight_reads_zero_solve_and_fixed_tolerance",
        REPO / "tests/test_native_w23_saved_mph_diagnostic.py::test_completed_point_projection_keeps_verified_calls_separate_from_unknown_attempts",
        REPO / "tests/test_native_w23_saved_mph_diagnostic.py::test_query_once_persists_unknown_point_attempt_without_completed_native_call",
        REPO / "tests/test_native_w23_saved_mph_diagnostic.py::test_outer_guard_retains_any_live_original_popen_for_blocked_or_unknown_cleanup",
        REPO / "tests/test_native_w23_saved_mph_diagnostic.py::test_outer_guard_does_not_hold_when_all_original_children_are_already_terminal",
        REPO / "tests/test_native_w23_saved_mph_diagnostic.py::test_outer_guard_treats_unobservable_original_children_as_active",
        REPO / "tests/test_native_w23_te_science_runner.py::test_socket_owner_gate_keeps_loopback_time_wait_inactive_and_not_a_worker_peer",
        REPO / "tests/test_native_w23_te_science_runner.py::test_socket_owner_gate_rejects_external_time_wait_even_when_state_is_inactive",
        REPO / "tests/test_native_w23_te_science_runner.py::test_direct_rpc_journal_separates_managed_requests_and_keeps_unknown_terminal_blocker",
        REPO / "tests/test_native_w23_te_science_runner.py::test_durable_unknown_cleanup_idle_requires_terminal_rpc_fresh_health_and_exact_endpoint",
        REPO / "tests/test_g3_results.py::test_w23_decoder_consumes_actual_requested_unit_native_adapter_witness",
        REPO / "tests/test_g3_results.py::test_real_only_port_scalars_require_actual_adapter_flag_and_exact_validated_binding",
    ]
    entry = binding["python"]["entry"] if binding else str(EXPECTED_PYTHON)
    junit_path = offline / "focused_delta_junit.xml"
    command = [entry, "-m", "pytest", "-q", "-p", "no:cacheprovider",
               *(str(path) for path in test_paths)]
    excluded_test_paths = []
    if current_full:
        # The registered Core loopback test is separately classified. A strict
        # network-guard EPERM is environmental, so isolation remains enabled.
        command.extend(["-k", "not test_science_dispatch_uses_actual_registered_session_runtime_after_public_model_load"])
        excluded_test_paths.append(
            "tests/test_w23_r21_direction_discovery.py::test_science_dispatch_uses_actual_registered_session_runtime_after_public_model_load")
    command.extend(["--junitxml", str(junit_path)])
    binding_activity = binding["activity_id"] if binding else ACTIVITY_ID
    env = _strict_test_environment(offline, activity_id=binding_activity)
    guard_log = Path(env["CODEX_NETWORK_GUARD_LOG"])
    guard_log.touch(exist_ok=False)
    result = subprocess.run(command, cwd=REPO, env=env, text=True,
                            capture_output=True, timeout=600, check=False)
    (offline / "focused-delta-pytest.stdout.txt").write_text(result.stdout, encoding="utf-8")
    (offline / "focused-delta-pytest.stderr.txt").write_text(result.stderr, encoding="utf-8")
    if not junit_path.is_file():
        raise RuntimeError("affected-control pytest run did not write its JUnit result")
    counts = science._junit_counts(junit_path)
    guard_rows = []
    for line in guard_log.read_text(encoding="utf-8").splitlines():
        if line.strip():
            guard_rows.append(json.loads(line))
    guard_sha = sha256_file(guard_log)
    guard_status = ("ACTIVE_NO_DENY_OR_SETUP_ERROR" if (
        guard_rows and guard_rows[0].get("event") == "GUARD_ACTIVE_BEFORE_CHILD_ENTRYPOINT"
        and not any(row.get("event") in {"DENIED_BEFORE_SOCKET_OPERATION", "GUARD_SETUP_ERROR"}
                    for row in guard_rows)) else "FAIL_NOT_ACTIVE_OR_DENIED")
    source_manifest_sha256 = _source_digest(_source_files())
    source_map_stable = (expected_source_manifest_sha256 is None
                         or source_manifest_sha256 == expected_source_manifest_sha256)
    focused = {
        "status": "PASS_FOCUSED_STRICT_GUARDED_JUNIT" if result.returncode == 0 else "FAIL_FOCUSED_STRICT_GUARDED_JUNIT",
        "command": command, "cwd": str(REPO), "strict_guard_enabled": True,
        "returncode": result.returncode,
        "test_count": counts["tests"], "failures": counts["failures"],
        "errors": counts["errors"], "skipped": counts["skipped"],
        "test_paths": [str(path) for path in test_paths],
        "excluded_test_paths": excluded_test_paths,
        "candidate_source_manifest_sha256": source_manifest_sha256,
        "junit_path": str(junit_path), "junit_sha256": sha256_file(junit_path),
        "network_guard": {
            "status": guard_status, "path": str(guard_log),
            "sha256": guard_sha, "event_count": len(guard_rows),
            "events": [row.get("event") for row in guard_rows],
        },
    }
    write_json(offline / "diagnostic_junit_receipt.json", focused)
    if (result.returncode != 0 or counts["tests"] < 25 or counts["failures"]
            or counts["errors"] or counts["skipped"]):
        raise RuntimeError(f"affected W23 control suite did not pass 25+ tests; see {offline}")
    if guard_status != "ACTIVE_NO_DENY_OR_SETUP_ERROR":
        raise RuntimeError(f"strict offline test guard did not prove active/no-deny state; see {guard_log}")
    if not source_map_stable:
        raise RuntimeError("current source map changed between offline preflight and focused tests")
    activity_id = ("w23-saved-mph-direction-discovery-" + binding_activity
                   if binding else ACTIVITY_ID)
    if current_full:
        delta_receipt = {
            "schema_version": 1,
            "status": "PASS_FOCUSED_DELTA_CURRENT_FULL_SOURCE_MAP",
            "candidate_id": CANDIDATE_ID,
            "candidate_revision": CANDIDATE_REVISION,
            "activity_id": activity_id,
            "candidate_source_manifest_sha256": source_manifest_sha256,
            "focused_tests": {
                "receipt_path": "offline_gates/diagnostic_junit_receipt.json",
                "receipt_sha256": sha256_file(offline / "diagnostic_junit_receipt.json"),
                "junit_path": "offline_gates/focused_delta_junit.xml",
                "junit_sha256": sha256_file(junit_path),
                "network_guard_path": "offline_gates/diagnostic-network-guard.jsonl",
                "network_guard_sha256": guard_sha,
            },
            "registered_core_loopback": {
                "status": "NOT_RUN_DEFERRED_POST_W24_TERMINAL",
                "test_path": excluded_test_paths[0],
                "strict_network_guard_enabled": False,
                "counted_in_guarded_junit": False,
            },
        }
    else:
        delta_receipt = {
            "schema_version": 1,
            "status": "PASS_OFFLINE_BASE_INHERITED_PLUS_FOCUSED_DELTA",
            "candidate_id": CANDIDATE_ID,
            "candidate_revision": CANDIDATE_REVISION,
            "activity_id": activity_id,
            "candidate_source_manifest_sha256": source_manifest_sha256,
            "inherited_base": _r13_inherited_base_record(),
            "source_delta": _source_delta_from_r13(_source_files()),
            "focused_tests": {
                "receipt_path": "offline_gates/diagnostic_junit_receipt.json",
                "receipt_sha256": sha256_file(offline / "diagnostic_junit_receipt.json"),
                "junit_path": "offline_gates/focused_delta_junit.xml",
                "junit_sha256": sha256_file(junit_path),
                "network_guard_path": "offline_gates/diagnostic-network-guard.jsonl",
                "network_guard_sha256": guard_sha,
            },
        }
    write_json(offline / "focused_delta_receipt.json", delta_receipt)
    if current_full:
        return {
            "status": "PASS_FOCUSED_DELTA_CURRENT_FULL_SOURCE_MAP",
            "focused_delta_receipt": {
                "path": "offline_gates/focused_delta_receipt.json",
                "sha256": sha256_file(offline / "focused_delta_receipt.json"),
            },
            "registered_core_loopback": delta_receipt["registered_core_loopback"],
        }
    return {
        "status": "PASS_OFFLINE_BASE_INHERITED_PLUS_FOCUSED_DELTA",
        "compile": json.loads((offline / "compile.json").read_text(encoding="utf-8")),
        "focused_delta_receipt": {
            "path": "offline_gates/focused_delta_receipt.json",
            "sha256": sha256_file(offline / "focused_delta_receipt.json"),
        },
    }


def _compile_and_test(evidence: Path, *, binding: Mapping[str, Any] | None = None,
                      current_source_files: Mapping[str, Any] | None = None) -> dict[str, Any]:
    offline = evidence / "offline_gates"
    offline.mkdir(parents=True, exist_ok=False)
    if current_source_files is not None:
        if binding is None:
            raise RuntimeError("current-full direction preparation requires a newly validated runtime binding")
        source_manifest_sha256 = _source_digest(current_source_files)
        current_full_root = offline / "current_full"
        full_gates = science.offline_preflight(
            current_full_root, current_source_files=current_source_files,
            runtime_binding=binding, runtime_budget=fresh_budget())
        if (full_gates.get("status") != "PASS_ALL_OFFLINE_GATES"
                or full_gates.get("source_manifest_sha256") != source_manifest_sha256):
            raise RuntimeError("complete current-full offline preflight did not bind the exact candidate source map")
        compile_receipt = full_gates.get("compile")
        if not isinstance(compile_receipt, Mapping):
            raise RuntimeError("current-full offline preflight did not compile the current Java sources")
        compile_path = current_full_root / "compile.json"
        write_json(compile_path, compile_receipt)
        focused = _run_focused_delta_tests(
            offline, binding=binding, current_full=True,
            expected_source_manifest_sha256=source_manifest_sha256)
        if _source_files() != current_source_files:
            raise RuntimeError("candidate source closure changed during current-full offline/focused gates")
        progress_path = current_full_root / "offline_preflight_progress.json"
        return {
            "status": "PASS_ALL_OFFLINE_GATES",
            "mode": science.CURRENT_FULL_OFFLINE_MODE,
            "source_manifest_sha256": source_manifest_sha256,
            "source_count": len(current_source_files),
            "current_full_gate_receipt": full_gates,
            "current_full_receipt": {
                "path": "offline_gates/current_full/offline_preflight_progress.json",
                "sha256": sha256_file(progress_path),
            },
            "compile": dict(compile_receipt),
            "runtime_imports": full_gates["runtime_imports"],
            "software_negative_controls": full_gates["software_negative_controls"],
            "pip_check": full_gates["pip_check"],
            "stdio": full_gates["stdio"],
            "compile_receipt": {
                "path": "offline_gates/current_full/compile.json",
                "sha256": sha256_file(compile_path),
            },
            "compile_artifact_root": "offline_gates/current_full/compile",
            "focused_delta_receipt": focused["focused_delta_receipt"],
            "registered_core_loopback": focused["registered_core_loopback"],
        }
    imports = science._runtime_helper_imports()
    import_receipt = {
        "status": "PASS_EXISTING_PRODUCTION_HELPER_IMPORTS",
        "resolved_helpers": sorted(imports),
        "python_executable": sys.executable,
        "python_version": sys.version,
    }
    write_json(offline / "runtime_imports.json", import_receipt)
    if binding:
        fresh_runtime.validate(binding, repo=REPO, budget=fresh_budget(), install=science.INSTALL_ROOT, jdk=science.JAVA11)
        command = [binding["python"]["entry"], "-m", "pip", "check"]
        result = subprocess.run(command, cwd=REPO, text=True, capture_output=True, timeout=60, check=False)
        dependency = {"status": "PASS_FRESH_DEV_RUNTIME_PIP_CHECK_NOT_W26_PRODUCT", "command": command,
                      "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
        if result.returncode:
            raise RuntimeError("fresh dev runtime dependency check failed")
    else:
        dependency = science._pip_dependency_check()
    write_json(offline / "pip_dependency_check.json", dependency)
    compile_output = offline / "compile"
    base_compile = science.W23_R13_BASE_ROOT / "prepare/offline_gates/compile.json"
    base_class_tree = science.W23_R13_BASE_ROOT / "prepare/offline_gates/compile"
    _r13_inherited_base_record()
    if compile_output.exists():
        raise FileExistsError(f"inherited class output target already exists: {compile_output}")
    shutil.copytree(base_class_tree, compile_output)
    shutil.copyfile(base_compile, offline / "compile.json")
    return {"runtime_imports": import_receipt,
            "pip_dependency_check": dependency,
            **_run_focused_delta_tests(offline, binding=binding)}


def prepare(evidence: Path, *, work: Path, execution_evidence: Path,
            runtime_authority: Path | None = None) -> dict[str, Any]:
    if runtime_authority is None:
        raise RuntimeError("current-full direction preparation requires a fresh runtime binding; old activity identities are not replayable")
    binding = fresh_runtime.load(runtime_authority, repo=REPO, budget=fresh_budget(),
                                 install=science.INSTALL_ROOT, jdk=science.JAVA11)
    identity = _bind_fresh_candidate_identity(binding)
    if binding["python"]["no_site"]:
        raise RuntimeError("W23 focused guard requires normal site startup")
    for path in (evidence, work, execution_evidence):
        fresh_runtime.scoped_path(binding, path, absent=True)
    if len({evidence,work,execution_evidence}) != 3 or work != Path(binding["namespace"])/"work":
        raise RuntimeError("fresh W23 paths must be distinct and work exactly namespace/work")
    activity_id = identity["activity_id"]
    if evidence.exists():
        raise FileExistsError(f"candidate evidence already exists: {evidence}")
    evidence.mkdir(parents=True, exist_ok=False)
    input_refs = _assert_source_inputs()
    direction_report_path = evidence / "offline_r20_signed_poynting_replay.json"
    direction_report = _offline_r20_direction_report()
    write_json(direction_report_path, direction_report)
    source_before = _source_files()
    gates = _compile_and_test(evidence, binding=binding,
                              current_source_files=source_before)
    source_after = _source_files()
    if source_before != source_after:
        raise RuntimeError("frozen W23 source closure changed during offline gates")
    manifest_artifact = science._write_source_manifest_artifact(evidence, source_after)
    freeze_path = evidence / "freeze.json"
    approval_path = evidence / f"ROOT_APPROVAL_ACTIVITY{identity['activity_token']}.json"
    freeze: dict[str, Any] = {
        "schema_version": 1,
        "activity_id": activity_id,
        "candidate_id": CANDIDATE_ID,
        "candidate_revision": CANDIDATE_REVISION,
        "status": "FROZEN_AWAITING_ROOT_APPROVAL",
        "created_at_utc": utc_now(),
        "scope": "Open the exact preserved Activity009 W23 provisional MPH once in a new registered project; discover the actual ElectromagneticWaves physics tag, Port feature properties, BoundaryModeAnalysis PortName/modeFreq properties, and explicit StudyStep/StoreSolution/Dataset.solution links through the public read-only node.children/node.inspect/node.property_schema/node.property_get APIs. Recompute only the preserved R20 641/1281 raw E/H direction observables offline. No result.at_points, Study.run, solver.run, setter, save, default, or sign/tolerance inference is dispatched.",
        "scope_limits": {
            "diagnostic_only": True,
            "saved_artifact_readback_only": True,
            "native_scientific_acceptance": "NOT_ACCEPTED",
            "field_and_mode_readback": "REUSE_FROZEN_R20_RAW_FIELDS_OFFLINE_ONLY",
            "study_or_solver_execution": "PROHIBITED",
            "explicit_study_run_dispatches": 0,
            "explicit_solver_run_dispatches": 0,
            "result_at_points_dispatches": 0,
            "internal_solver_execution_count": "UNKNOWN_NOT_INFERRED_FROM_READBACK",
            "no_role_guessing": True,
            "no_outer_inner_tuple_inference": True,
            "no_frequency_inference_from_config_or_tag": True,
            "no_saved_model_mutation_or_resave": True,
            "no_h_only_flip": True,
            "no_sign_convention_guess": True,
            "no_tolerance_change": True,
            "no_grid_densification": True,
            "no_companion_full_recovery_model": True,
        },
        "target": {
            "platform": "macOS", "comsol_root": str(science.INSTALL_ROOT),
            "required_engine_identity": "COMSOL Multiphysics 6.4.0.293",
            "jdk_home": str(science.JAVA11), "python_executable": sys.executable,
            "python_version": sys.version,
        },
        "input": {
            "path": str(SOURCE_MODEL), "bytes": SOURCE_MODEL_BYTES,
            "sha256": SOURCE_MODEL_SHA256,
            "role": "Activity009 provisional saved model; copied byte-for-byte into a new registered project before model_load",
            "prior_activity_reconciliation": {
                "path": str(SOURCE_RECONCILIATION), "sha256": SOURCE_RECONCILIATION_SHA256,
                "status": "TERMINAL_POSTPROCESS_FAIL_AFTER_ONE_SUCCEEDED_STUDY_RUN_CLEANUP_VERIFIED",
                "study_run_dispatch_count": 1, "study_run_job_status": "SUCCEEDED",
                "runner_exit_code": 2,
                "native_scientific_acceptance": "NOT_ACCEPTED",
                "actual_internal_solver_execution_count": "UNKNOWN_INTERNAL_SOLVER_STEPS_NOT_INFERRED",
            },
            "root_preservation_cleanup_check": {
                "path": str(ROOT_PRESERVATION_CHECK), "sha256": ROOT_PRESERVATION_CHECK_SHA256,
                "scope": "preservation and captured owned cleanup only",
            },
            "activity010_preservation_cleanup_check": {
                "path": str(ACTIVITY010_PRESERVATION_CHECK),
                "sha256": ACTIVITY010_PRESERVATION_CHECK_SHA256,
                "scope": "Activity010 artifact/ledger preservation and captured owned cleanup only; no scientific acceptance",
            },
        },
        "direction_discovery_inputs": input_refs,
        "offline_direction_report": {
            "path": str(direction_report_path),
            "sha256": sha256_file(direction_report_path),
            "status": direction_report.get("status"),
            "scope": "frozen R20 raw E/H arrays replayed offline only; no old R20 runtime/UNKNOWN calls",
        },
        "official_api_basis": {
            "solution_info": {
                "version": "COMSOL 6.4", "doc_id": 7709,
                "chunks": [23066, 23067],
                "sha256": "94d28f308be1be1b57d81c40932be6aebfd91e134c221b8e2f7e2235d51b9abb",
                "documented_contract": "getPNames/getPvals/getPvalsImag/getUnits accept one-based solnums where each solnums[n] is an (outer,inner) tuple; doc example maps (2,4) to return entry 5. getSolnum(outer,true) returns one-based inner solution numbers or an empty array when strict and the outer index is invalid. getOuterSolnum returns an empty array if there are no outer parameters/values. getSol and getSolverSequence accept a one-based outer solution number.",
            },
            "study_solver_sequence": {
                "version": "COMSOL 6.4", "doc_id": 7716,
                "chunk": 23089,
                "sha256": "7ab71c87a119f01583dbf0441ec9b032e257c4125aa9994b9d83f72571236546",
                "documented_query_types": list(STUDY_SEQUENCE_TYPES),
            },
            "solver_sequence_type_and_parameter_values": {
                "version": "COMSOL 6.4", "doc_id": 7714,
                "chunk": 23081,
                "sha256": "8fcefe8e2f2171858f49fe53ad6210d4231a6a28651766db67effc39c327a2c5",
                "documented_contract": "getSequenceType() classifies actual sequence types including SolverSequence, CopySolution, ParametricStore, and Stored. getPNames() returns the sequence parameter names; getPVals(solnum) and getPValsImag(solnum) return actual stored real/imaginary parameter values for the requested solution index. Preserve returned names/values without assigning a frequency, wavelength, time, or eigenvalue role.",
            },
            "solver_sequence_parameter_values": {
                "version": "COMSOL 6.4", "doc_id": 4604,
                "chunk": 17527,
                "sha256": "551d3f05317ab35fd2a6aeb37390c7d74979209137d25347b601602ba6b3ba45",
                "documented_contract": "getPVals(int solnum) exposes actual stored parameter tuples, time values, or real eigenvalues according to the solution sequence; getPValsImag(int solnum) exposes imaginary values. Activity010 records exact getter identity and raw return for the observed inner solnum but does not infer which semantic axis the values represent.",
                "installed_javap": "COMSOL 6.4.0.293 SolverSequence declares getPNames() -> String[], getPVals(int) -> double[], and getPValsImag(int) -> double[].",
            },
            "copy_solution": {
                "version": "COMSOL 6.4", "doc_id": 4617, "chunk": 17546,
                "sha256": "ceb3130ca7cf0085b539ade97d9c2f8c66b6c48e07ac1764769b0e91a494371b",
                "property_read": "sol",
            },
            "store_solution": {
                "version": "COMSOL 6.4", "doc_id": 4644, "chunk": 17597,
                "sha256": "5f98a43c69e60ccd3c8c8666beba64607b8e4f74e965322e95c6978ce01c809b",
                "property_read": "sol",
            },
            "boundary_mode_analysis": {
                "version": "COMSOL 6.4", "doc_id": 26574,
                "chunk": 112469, "pdf_page": 91,
                "sha256": "e3bb06e25ac37dcdf51d70f56b2ab47d8d5057b475cad321456e1d160c7154cc",
                "documented_contract": "Boundary Mode Analysis uses beta_i=j*lambda and neff_i=beta_i/k0; this is the BMA relation, not ordinary Mode Analysis beta=imag(alpha).",
            },
        },
        "limits": {
            "max_owned_server_processes": 1,
            "max_worker_sessions": 1,
            "max_result_at_points_calls": MAX_R21_RESULT_AT_POINTS_CALLS,
            "max_gui_processes": 0,
            "max_study_run_calls": 0,
            "max_solver_run_calls": 0,
            "max_seconds_from_server_birth": int(MAX_BIRTH_SECONDS),
            "cleanup_reserve_seconds": int(CLEANUP_RESERVE_SECONDS),
            "retry_unknown_or_nonterminal_worker_request": False,
            "stop_after_first_unknown_or_nonterminal_worker_request": True,
            "managed_operation_allowlist": ["server_connect", "model_load", "model.inspect(summary)", "operation_call/node.children", "operation_call/node.inspect", "operation_call/node.property_schema(named property only)", "operation_call/node.property_get(one allowlisted property only)"],
        },
        "read_only_call_plan": {
            "mode": "direction_discovery_only",
            "Study.run": 0,
            "solver.run": 0,
            "result.at_points": 0,
            "max_public_operation_calls": direction.MAX_DIRECTION_CALLS,
            "max_children_per_list": direction.MAX_CHILDREN_PER_LIST,
            "allowed_operation_call_ids": sorted(direction.READONLY_OPERATION_IDS),
            "root_model_path": {"segments": []},
            "component_path": {"segments": [{"collection": "component", "tag": "comp1"}]},
            "physics_interface_type": "ElectromagneticWaves",
            "field_variable_prefix": "emw",
            "physics_namespace_correlation": "capture Physics.tag/getType/identifier through public node children/inspect; keep tag separate, correlate the complete unique documented Physics Name/Scope identifier with frozen emw metadata, and fail closed for unavailable, wrong, malformed, or ambiguous identifiers; no field values evaluated",
            "port_properties": list(direction.PORT_PROPERTIES),
            "boundary_mode_analysis_properties": list(direction.BMA_PROPERTIES),
            "study_step_properties": list(direction.STUDY_STEP_PROPERTIES),
            "store_solution_properties": list(direction.STORE_SOLUTION_PROPERTIES),
            "dataset_properties": list(direction.DATASET_PROPERTIES),
            "property_sequence": "For each exact named property: node.property_schema must return has_property true, known getValueType/getter/kind/rank, and unique finite allowed string values if present before exactly one typed node.property_get. Absent/unknown/ambiguous properties are preserved and never read through a guessed getter.",
            "same_version_api_evidence": {
                "ModelEntityList": {"doc_id": 7644, "chunk_id": 22964, "sha256": "463ef0c4e8b2b7315e8950bd576f4b783718c75308307cef0a4d584d86b835ab", "finding": "tags() returns a string array with tags of all entities."},
                "PhysicsList": {"doc_id": 7669, "chunk_id": 23008, "sha256": "d1b397ef5d78f9447c6f6be572302f6ef5e291e769261d69d8ffdaf3a00fdb25", "finding": "PhysicsList extends ModelEntityList<Physics>."},
                "Physics": {"doc_id": 7757, "chunk_id": 23152, "sha256": "d0de70997a9ea928edd8f1d207f926ce82c3849e5846c20189d28d67cd9273fc", "finding": "Physics.getType() returns the type identifier; tag() is inherited from ModelEntity."},
                "PhysicsFeature": {"doc_id": 7758, "chunk_id": 23153, "sha256": "9eeb763b12eb4d5f6eaff7561381bb200aab2150165063410a4e9fa3592b5e7c", "finding": "PhysicsFeature exposes getType() and inherits ParameterEntity public property methods."},
                "PropFeature": {"doc_id": 7675, "chunk_id": 23018, "sha256": "befb8cc1c0e3df1ce74d2fa2cd1377e73d378e5c26170558e9f40dc255cc3bc3", "finding": "hasProperty(name) reports support; getValueType(name) names the typed getter; properties() returns property names."},
                "ParameterEntity": {"doc_id": 7668, "chunk_id": 23005, "sha256": "d48a6bc07e9862244a7fd496246477200d065a661ae69605de4ef3ae7e678539", "finding": "getAllowedPropertyValues(name) returns finite String[] values or null; typed getters expose declared Java types."},
                "Port_UI_guidance": {"doc_id": 24404, "chunks": [104652, 104653], "sha256": "aea018dcf88874ad63cb91d2b0a92c9d43b09fb3dadeb309888154be97fc4dc8", "finding": "Wave Excitation On is source/inport and Off is listener; this does not assign positive global-x to ForwardPort."},
                "Port_Mode_Field_Variables": {"doc_id": 24354, "chunk_id": 104589, "sha256": "d568ce649f47507fb6de28d2276d7a9774a9d0073a45d72ec107ec7b6f9c0bdb", "finding": "Emode/Hmode are numeric Port BMA fields; mode directions still require signed E/H witnesses."},
            },
            "feature_discovery": "Enumerate actual physics/study/solver/dataset tags through complete node.children pages; classify exact types through node.inspect; bind PortName to Port features, BMA features to StudyStep.study/studystep, StoreSolution.sol, and Dataset.solution by exact strings only. No positional/index inference.",
            "solver_tree_limits": {"maximum_solver_sequences": direction.MAX_ROOT_ENTITIES, "maximum_features_per_list": direction.MAX_FEATURES_PER_PARENT, "maximum_nodes": direction.MAX_SOLVER_TREE_NODES, "maximum_depth": direction.MAX_SOLVER_TREE_DEPTH},
            "project_create_before_birth_permissions": ["inspect", "project_write", "trusted_code"],
            "load_method": "one production managed model_load in the exact registered task-owned project; bind ModelRef/revision/project/session/server/worker epoch",
            "model_input_preparation": "copy exact preserved Activity009 MPH SHA/size into the task-owned project workspace; never write or resave the source model",
            "native_operation_budget": {"Study.run": 0, "solver.run": 0, "result.at_points": 0, "node.property_set": 0, "code.execute_java": 0, "model.save": 0},
            "legacy_r20_point_call_limit": MAX_RESULT_AT_POINTS_CALLS,
            "offline_quadrature": {"method": "composite Simpson", "coarse_points": protocol.SAVED_MPH_SAMPLE_COUNT,
                                   "fine_points": protocol.SAVED_MPH_REFINED_SAMPLE_COUNT,
                                   "relative_tolerance": protocol.INTEGRAL_RELATIVE_TOLERANCE},
            "frozen_r20_signed_power_report_sha256": R20_DIRECTION_REPORT_SHA256,
            "signed_direction_observables": "Offline replay of only the four exact R20 641/1281 decoded E/H arrays; report global Px and native nx*Px outward n.S separately. This does not infer excitation phase or accept science.",
            "nonchanges": ["no H-only flip", "no normal inversion", "no sign convention guess", "no tolerance relaxation", "no grid densification", "no formula change", "no scientific acceptance upgrade"],
            "retry_policy": "one dispatch per exact read slot; retain and stop on any failed/unknown/nonterminal operation; no replay or reconciliation of old R20 UNKNOWN",
        },
        "work_path": str(work),
        "execution_evidence_path": str(execution_evidence),
        "approval_path_expected": str(approval_path),
        "native_invocation_argv": _native_argv(freeze_path, "<freeze-sha256>", approval_path,
                                                SOURCE_MODEL, work, execution_evidence, binding),
        "source_files": source_after,
        "source_manifest_sha256": _source_digest(source_after),
        "source_manifest_artifact": manifest_artifact,
        "offline_preflight": gates,
        "native_acceptance": "NOT_RUN",
    }
    _validate_diagnostic_scope(freeze)
    if binding:
        freeze["fresh_runtime_binding"] = binding
    # A freeze is publishable only when its own relative compiler tree exists.
    material = science._check_offline_receipts(freeze_path, freeze)
    write_json(evidence / "executable_offline_material_check.json", material)
    write_json(freeze_path, freeze)
    freeze_sha = sha256_file(freeze_path)
    argv = _native_argv(freeze_path, freeze_sha, approval_path,
                        SOURCE_MODEL, work, execution_evidence, binding)
    packet = {
        "schema_version": 1,
        "activity_id": activity_id,
        "candidate_id": CANDIDATE_ID,
        "candidate_revision": CANDIDATE_REVISION,
        "status": "PREPARED_AWAITING_ROOT_REVIEW_AND_EXACT_ADMISSION",
        "source_root": str(REPO),
        "freeze_path": str(freeze_path), "freeze_sha256": freeze_sha,
        "source_manifest_path": str(evidence / "source_manifest.json"),
        "source_manifest_sha256": freeze["source_manifest_sha256"],
        "source_count": len(source_after),
        "source_bytes": sum(int(row["bytes"]) for row in source_after.values()),
        "source_changes_from_activity010_base": [
            "tools/w23_direction_discovery.py: exact bounded public node graph traversal, schema-before-get typing, no inference on absent/ambiguous fields, and exact Port/BMA/StudyStep/StoreSolution/Dataset link matching",
            "tools/run_native_w23_saved_mph_diagnostic.py: R21 fresh direction-only mode with zero result.at_points, Study.run, solver.run, setter, Java execution, and save dispatches; reuse the original-Popen cleanup owner",
            "tests/test_w23_r21_direction_discovery.py: allowlist, property-schema gate, typed-value, incomplete-child, tag-correlation, and zero-solve controls",
            "frozen R20 report and four R20 641/1281 raw E/H exports: exact offline replay only; prior runtime/UNKNOWN is never queried or reconciled",
        ],
        "previous_activity009_preservation_check": input_refs["root_preservation_check"],
        "previous_activity010_preservation_check": input_refs["activity010_preservation_check"],
        "source_reconciliation": input_refs["source_reconciliation"],
        "input_model": input_refs["source_model"],
        "offline_gates": gates,
        "native_invocation": {
            "argv": argv, "argv_sha256": sha256_json(argv),
            "cwd": str(REPO),
            "environment": _native_environment(binding),
            "strict_test_network_guard": "NOT_SET_FOR_NATIVE",
            "tool_sandbox_permissions": "require_escalated",
            "work_path_expected_absent": str(work),
            "evidence_path_expected_absent": str(execution_evidence),
            "root_approval_path_expected": str(approval_path),
        },
        "limits": freeze["limits"],
        "native_scientific_acceptance": "NOT_RUN; diagnostic readback only",
        "prior_activity009": "FAIL_OR_INCOMPLETE; one durable Study.run SUCCEEDED then postprocessing failed; no replay",
    }
    packet_path = evidence / f"ACTIVITY{identity['activity_token']}_REVIEW_PACKET.json"
    write_json(evidence / packet_path, packet)
    receipt = {
        "status": "PREPARED_AWAITING_ROOT_REVIEW_AND_EXACT_ADMISSION",
        "activity_id": activity_id, "candidate_id": CANDIDATE_ID,
        "candidate_revision": CANDIDATE_REVISION,
        "freeze_path": str(freeze_path), "freeze_sha256": freeze_sha,
        "packet_path": str(evidence / packet_path),
        "packet_sha256": sha256_file(evidence / packet_path),
        "source_count": len(source_after),
        "study_run_dispatches_allowed": 0, "solver_run_dispatches_allowed": 0,
        "native_process_started": False,
        "native_scientific_acceptance": "NOT_RUN",
    }
    write_json(evidence / "prepare_receipt.json", receipt)
    return {**receipt, "packet": packet}


def _validate_freeze(freeze_path: Path, expected_sha256: str) -> dict[str, Any]:
    if not freeze_path.is_file() or sha256_file(freeze_path) != expected_sha256:
        raise RuntimeError("freeze path is absent or its SHA-256 does not match the exact reviewed candidate")
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    binding = freeze.get("fresh_runtime_binding")
    if binding is not None:
        fresh_runtime.validate(binding, repo=REPO, budget=fresh_budget(), install=science.INSTALL_ROOT, jdk=science.JAVA11)
        fresh_runtime.scoped_path(binding, freeze_path.parent)
        _bind_fresh_candidate_identity(binding)
    if (freeze.get("status") != "FROZEN_AWAITING_ROOT_APPROVAL"
            or freeze.get("activity_id") != _activity(freeze)
            or freeze.get("candidate_id") != CANDIDATE_ID
            or freeze.get("candidate_revision") != CANDIDATE_REVISION
            or freeze.get("source_manifest_sha256") != _source_digest(freeze.get("source_files", {}))):
        raise RuntimeError("freeze identity/status/source-map digest is invalid")
    _validate_diagnostic_scope(freeze)
    if _source_files() != freeze.get("source_files"):
        raise RuntimeError("candidate source closure changed after freeze")
    if freeze.get("direction_discovery_inputs") != _assert_source_inputs():
        raise RuntimeError("R21 saved-MPH or frozen R20 direction inputs changed after freeze")
    offline_ref = freeze.get("offline_direction_report")
    expected_offline = freeze_path.parent / "offline_r20_signed_poynting_replay.json"
    if (not isinstance(offline_ref, Mapping) or Path(str(offline_ref.get("path"))) != expected_offline
            or not expected_offline.is_file() or expected_offline.is_symlink()
            or sha256_file(expected_offline) != offline_ref.get("sha256")):
        raise RuntimeError("R21 offline R20 direction report is absent or differs from its frozen exact hash")
    recomputed_offline = _offline_r20_direction_report()
    if sha256_json(recomputed_offline) != sha256_json(json.loads(expected_offline.read_text(encoding="utf-8"))):
        raise RuntimeError("offline replay of preserved R20 field arrays differs from the frozen direction report")
    science._validate_source_manifest_artifact(freeze_path, freeze)
    # Recheck the actual freeze parent, including metadata-only successors.
    science._check_offline_receipts(freeze_path, freeze)
    return freeze


def _validate_approval(approval: Mapping[str, Any], freeze: Mapping[str, Any],
                       freeze_sha256: str, argv: list[str]) -> None:
    expected = {
        "status": "ROOT_APPROVED_FOR_EXACT_FROZEN_CANDIDATE",
        "activity_id": _activity(freeze),
        "candidate_id": CANDIDATE_ID,
        "candidate_revision": CANDIDATE_REVISION,
        "freeze_sha256": freeze_sha256,
        "source_manifest_sha256": freeze["source_manifest_sha256"],
        "native_argv_sha256": sha256_json(argv),
        "max_owned_server_processes": 1, "max_worker_sessions": 1,
        "max_gui_processes": 0, "max_study_run_calls": 0,
        "max_solver_run_calls": 0,
        "max_result_at_points_calls": (MAX_R21_RESULT_AT_POINTS_CALLS
                                       if freeze.get("read_only_call_plan", {}).get("mode") == "direction_discovery_only"
                                       else MAX_RESULT_AT_POINTS_CALLS),
        "max_seconds_from_server_birth": int(MAX_BIRTH_SECONDS),
        "cleanup_reserve_seconds": int(CLEANUP_RESERVE_SECONDS),
        "retry_unknown_or_nonterminal_worker_request": False,
        "read_only_call_plan_sha256": sha256_json(freeze["read_only_call_plan"]),
    }
    if freeze.get("fresh_runtime_binding") is not None:
        expected["runtime_binding_sha256"] = freeze["fresh_runtime_binding"]["binding_sha256"]
    mismatches = {key: {"expected": value, "observed": approval.get(key)}
                  for key, value in expected.items() if approval.get(key) != value}
    invocation = approval.get("exact_native_invocation")
    if isinstance(invocation, Mapping):
        if invocation.get("argv") != argv:
            mismatches["exact_native_invocation.argv"] = {"expected": argv, "observed": invocation.get("argv")}
        if invocation.get("cwd") != str(REPO):
            mismatches["exact_native_invocation.cwd"] = {"expected": str(REPO), "observed": invocation.get("cwd")}
        expected_environment = _native_environment(freeze.get("fresh_runtime_binding"))
        if invocation.get("environment") != expected_environment:
            mismatches["exact_native_invocation.environment"] = {
                "expected": expected_environment,
                "observed": invocation.get("environment"),
            }
    else:
        mismatches["exact_native_invocation"] = {"expected": "exact argv/cwd/environment object", "observed": None}
    if mismatches:
        raise RuntimeError("exact frozen activity root approval mismatch: " + json.dumps(mismatches, ensure_ascii=False, sort_keys=True))


def _bind_project_create(response: Mapping[str, Any], helpers: Mapping[str, Any],
                         expected_workspace: Path, *, label: str = PROJECT_LABEL) -> dict[str, Any]:
    science.PROJECT_LABEL = label
    return science._bind_authoritative_project(response, helpers,
                                               expected_workspace=expected_workspace)


def _allowed_diagnostic_dispatch(request: Mapping[str, Any]) -> bool:
    operation = request.get("operation")
    if operation not in ALLOWED_MANAGED_OPERATIONS:
        return False
    if operation != "operation_call":
        return True
    arguments = request.get("arguments")
    if not isinstance(arguments, Mapping) or set(arguments) != {"operation_id", "arguments"}:
        return False
    operation_id = arguments.get("operation_id")
    operation_arguments = arguments.get("arguments")
    if operation_id == "result.at_points":
        return _allowed_saved_point_read(operation_arguments)
    if (operation_id != "code.execute_java" or not isinstance(operation_arguments, Mapping)
            or set(operation_arguments) != {"source_artifact", "entrypoint", "arguments", "mode"}):
        return False
    fixture_arguments = operation_arguments.get("arguments")
    return (operation_arguments.get("source_artifact") == FIXTURE.name
            and operation_arguments.get("entrypoint") == "NativeW23TEScienceFixture#run"
            and operation_arguments.get("mode") == "trusted"
            and isinstance(fixture_arguments, Mapping)
            and dict(fixture_arguments) == {"phase": "saved_solution_diagnostic"})


def _allowed_direction_discovery_dispatch(request: Mapping[str, Any], *,
                                         expected_model_path: Path | None = None,
                                         expected_project_id: str | None = None,
                                         expected_model_ref: Mapping[str, Any] | None = None,
                                         expected_revision: int | None = None) -> bool:
    if not isinstance(request, Mapping) or set(request) != {"operation", "arguments", "execution"}:
        return False
    operation = request.get("operation")
    arguments = request.get("arguments")
    execution = request.get("execution")
    if (not isinstance(arguments, Mapping) or not isinstance(execution, Mapping)
            or not isinstance(expected_project_id, str) or not expected_project_id):
        return False
    if operation in {"server_connect", "model_load"}:
        if expected_model_ref is not None or expected_revision is not None:
            return False
        if not direction.valid_science_execution_envelope(
                execution, expected_project_id=expected_project_id,
                require_model_context=False):
            return False
        timeout = 30.0 if operation == "server_connect" else 120.0
        if type(execution.get("rpc_timeout_s")) is not float or execution["rpc_timeout_s"] != timeout:
            return False
        if operation == "server_connect":
            return (set(arguments) == {"host", "port"}
                    and arguments.get("host") == "127.0.0.1"
                    and type(arguments.get("port")) is int and 1 <= arguments["port"] <= 65535)
        path = arguments.get("path") if set(arguments) == {"path"} else None
        return (expected_model_path is not None and isinstance(path, str)
                and Path(path) == expected_model_path
                and Path(path).name == "activity009-coarse-phase0-provisional.mph")
    if operation == "model.inspect":
        return (set(arguments) == {"detail"} and arguments.get("detail") == "summary"
                and type(execution.get("rpc_timeout_s")) is float
                and execution.get("rpc_timeout_s") == 60.0
                and direction.valid_science_execution_envelope(
                    execution, expected_project_id=expected_project_id,
                    expected_model_ref=expected_model_ref,
                    expected_revision=expected_revision, require_model_context=True))
    if operation == "operation_call":
        return direction.allowed_direction_dispatch(
            request, expected_project_id=expected_project_id,
            expected_model_ref=expected_model_ref,
            expected_revision=expected_revision)
    return False


def _bind_direction_dispatch_context(daemon: Any, project_id: str,
                                    model_ref: Mapping[str, Any], revision: int) -> None:
    context = getattr(daemon, "_w23_r21_direction_dispatch_context", None)
    if (not isinstance(context, dict) or context.get("project_id") != project_id
            or not isinstance(model_ref, Mapping) or type(revision) is not int or revision < 0):
        raise RuntimeError("R21 dispatch identity context is not bound to the exact registered project/model revision")
    context["model_ref"] = dict(model_ref)
    context["revision"] = revision


def _field_points(x_um: float, sample_count: int) -> list[list[float]]:
    if sample_count not in protocol.SAVED_MPH_QUADRATURE_SAMPLE_COUNTS:
        raise ValueError(f"field sample count is outside the frozen saved-MPH {protocol.SAVED_MPH_QUADRATURE_SAMPLE_COUNTS} grid")
    return [[x_um, -8.0 + 16.0 * index / (sample_count - 1)]
            for index in range(sample_count)]


def _public_points(points: list[list[float]]) -> list[dict[str, float]]:
    """Canonical public objects; preserve the frozen internal x/y values."""
    converted = []
    for point in points:
        if (not isinstance(point, (list, tuple)) or len(point) != 2
                or any(type(value) not in (int, float) or not math.isfinite(value) for value in point)):
            raise ValueError("saved diagnostic points must be finite exact 2D numeric coordinates")
        converted.append({"x": point[0], "y": point[1]})
    return converted


def _allowed_saved_point_read(raw_arguments: Any) -> bool:
    if not isinstance(raw_arguments, Mapping) or set(raw_arguments) != {
            "spec", "points", "coordinate_unit", "frame"}:
        return False
    spec = raw_arguments.get("spec")
    if (not isinstance(spec, Mapping)
            or set(spec) not in ({"expressions", "solution", "complex_mode"},
                                 {"expressions", "solution", "complex_mode", "units"},
                                 {"expressions", "solution", "complex_mode", "units", "selection", "entity_dim"})
            or spec.get("complex_mode") != "preserve"
            or raw_arguments.get("coordinate_unit") != "um"
            or raw_arguments.get("frame") != "spatial"):
        return False
    solution = spec.get("solution")
    if not isinstance(solution, Mapping) or set(solution) != {"dataset", "solution"}:
        return False
    dataset, solution_tag = solution.get("dataset"), solution.get("solution")
    expressions = spec.get("expressions")
    points = raw_arguments.get("points")
    if not isinstance(expressions, list) or not isinstance(points, list):
        return False
    if (not points or any(not isinstance(point, Mapping) or set(point) != {"x", "y"}
                         or any(type(value) not in (int, float) or not math.isfinite(value) for value in point.values())
                         for point in points)):
        return False
    if dataset=="dset1" and solution_tag=="sol1":
        for port,x in (("1",-10.0),("2",8.0)):
            if (expressions==list(plane_field_expressions(port)) and spec.get("units")==list(plane_field_units(port))
                and spec.get("selection")==plane_selection(port) and type(spec.get("entity_dim")) is int and spec["entity_dim"]==1):
                return any(points==_public_points(_field_points(x,count)) for count in protocol.SAVED_MPH_QUADRATURE_SAMPLE_COUNTS)
    if (dataset, solution_tag) == ("dset1", "sol1") and expressions == list(FREQUENCY_EXPRESSIONS):
        return points == _public_points([[-10.0, 0.0]]) and spec.get("units") in (["Hz"], ["GHz"])
    if (dataset, solution_tag) in {("dset2", "sol2"), ("dset3", "sol3")}:
        port = MODE_DATASETS[dataset][2]
        expected_expressions = protocol.bma_point_expressions(port, field_scheme="emw_down")
        x_um = MODE_DATASETS[dataset][3]
        return (expressions == list(expected_expressions) and points == _public_points([[x_um, 0.0]])
                and spec.get("units") == list(mode_units(port).values()))
    return False


def _set_dispatch_allowlist(daemon: Any, journal_path: Path, *, allow=None) -> None:
    original = daemon.dispatch

    def guarded(request: Mapping[str, Any]) -> dict[str, Any]:
        operation = request.get("operation")
        arguments = request.get("arguments")
        allowed = (allow or _allowed_diagnostic_dispatch)(request)
        if not allowed:
            row = {"at_utc": utc_now(), "status": "BLOCKED_NOT_IN_DIAGNOSTIC_ALLOWLIST",
                   "operation": operation, "arguments": arguments}
            with journal_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            raise RuntimeError("managed operation blocked by saved-MPH diagnostic allowlist")
        with journal_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"at_utc": utc_now(), "status": "DISPATCHED",
                                     "operation": operation,
                                     "operation_id": (arguments.get("operation_id")
                                                      if isinstance(arguments, Mapping) else None)},
                                    ensure_ascii=False, default=str) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return original(request)
    daemon.dispatch = guarded


def _query_once(daemon: Any, operation: str, arguments: dict[str, Any],
                ref: dict[str, Any], revision: int, *, project_id: str,
                deadline_epoch: float, evidence: Path, label: str,
                summary: dict[str, Any] | None = None,
                point_read_attempt: bool = False) -> tuple[dict[str, Any], dict[str, Any], int]:
    track_point = (point_read_attempt and summary is not None and operation == "operation_call"
                   and isinstance(arguments, Mapping)
                   and arguments.get("operation_id") == "result.at_points")
    attempt: dict[str, Any] | None = None
    if track_point:
        attempts = summary.get("point_read_attempts")
        if not isinstance(attempts, list) or any(
                isinstance(row, Mapping) and row.get("label") == label for row in attempts):
            raise RuntimeError(f"{label} attempted-call projection is missing or duplicated")
        attempt = {"ordinal": len(attempts) + 1, "label": label,
                   "operation_id": "result.at_points", "status": "DISPATCH_INVOKED",
                   "verified_completed": False,
                   "dispatch_response": None, "terminal_status": None,
                   "native_scientific_acceptance": "NOT_EVALUATED"}
        attempts.append(attempt)
        summary["result_at_points_attempt_count"] = len(attempts)
        summary["result_at_points_verified_completed_count"] = len(summary.get("native_calls", []))
        write_json(evidence / "summary.json", summary)
    try:
        response = science._dispatch(daemon, operation, arguments,
                                     project_id=project_id, ref=ref, revision=revision,
                                     timeout_s=min(120.0, max(1.0, deadline_epoch - time.time() - CLEANUP_RESERVE_SECONDS)))
    except BaseException as exc:
        if attempt is not None:
            attempt.update({"status": "DISPATCH_INVOKED_NO_RESPONSE",
                            "error": f"{type(exc).__name__}: {exc}"})
            write_json(evidence / "summary.json", summary)
        raise
    write_json(evidence / f"{label}_dispatch_response.json", response)
    job_id = science._job_id(response)
    if attempt is not None:
        dispatch_path = evidence / f"{label}_dispatch_response.json"
        attempt.update({"status": "DISPATCH_RESPONSE_RECEIVED",
                        "job_id": job_id,
                        "dispatch_success": response.get("success") is True,
                        "dispatch_response": {"path": str(dispatch_path),
                                              "bytes": dispatch_path.stat().st_size,
                                              "sha256": sha256_file(dispatch_path)}})
        write_json(evidence / "summary.json", summary)
    if job_id is not None:
        response = science._wait_job(daemon, response,
                                     deadline_epoch=deadline_epoch,
                                     cleanup_reserve_s=CLEANUP_RESERVE_SECONDS,
                                     label=label, evidence=evidence)
    write_json(evidence / f"{label}_terminal_response.json", response)
    if job_id is not None:
        job = daemon.store.job(job_id)
        if attempt is not None:
            terminal_path = evidence / f"{label}_job_terminal.json"
            attempt["terminal_status"] = job.get("status") if isinstance(job, Mapping) else None
            attempt["status"] = attempt["terminal_status"] or "TERMINAL_STATUS_UNAVAILABLE"
            if terminal_path.is_file():
                attempt["job_terminal_receipt"] = {"path": str(terminal_path),
                    "bytes": terminal_path.stat().st_size, "sha256": sha256_file(terminal_path)}
            summary["result_at_points_attempt_count"] = len(summary["point_read_attempts"])
            summary["result_at_points_verified_completed_count"] = len(summary.get("native_calls", []))
            write_json(evidence / "summary.json", summary)
        if not isinstance(job, Mapping) or job.get("status") not in {"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED", "UNKNOWN", "LOST"}:
            raise RuntimeError(f"{label} did not reach a durable terminal job state; do not issue another request")
        if job.get("status") in {"UNKNOWN", "QUEUED", "RUNNING"}:
            raise RuntimeError(f"{label} durable job is {job.get('status')}; stop issuing requests")
    elif attempt is not None:
        attempt["terminal_status"] = "SUCCEEDED_INLINE" if response.get("success") is True else "INLINE_ERROR"
        attempt["status"] = attempt["terminal_status"]
        summary["result_at_points_attempt_count"] = len(summary["point_read_attempts"])
        summary["result_at_points_verified_completed_count"] = len(summary.get("native_calls", []))
        write_json(evidence / "summary.json", summary)
    new_ref, new_revision = science._update_ref(response, daemon, ref, revision)
    return response, new_ref, new_revision


def _stage_legacy_fixture_once(project_workspace: Path, evidence: Path, *,
                               direction_only: bool,
                               expected_source_sha256: str | None) -> dict[str, Any]:
    """Stage the exact Java source only for the legacy saved-MPH diagnostic path."""
    if direction_only:
        return {"status": "SKIPPED_DIRECTION_DISCOVERY_ONLY", "staged": False,
                "source_opened": False, "code_execute_java": 0}
    source = FIXTURE
    if (not source.is_absolute() or source.resolve(strict=True) != source
            or source.is_symlink()):
        raise ValueError("legacy Java fixture source must be a canonical ordinary file")
    if (not isinstance(expected_source_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_source_sha256)):
        raise ValueError("legacy Java fixture lacks its exact frozen source SHA-256")
    target = project_workspace / source.name
    if not project_workspace.is_dir() or project_workspace.resolve(strict=True) != project_workspace:
        raise ValueError("registered project workspace must be canonical before fixture staging")
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"refusing to reuse preexisting Java fixture stage: {target}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    source_fd = os.open(source, flags)
    try:
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid != os.getuid():
            raise ValueError("legacy Java fixture source identity is not an owner-bound regular file")
        chunks = []
        while True:
            block = os.read(source_fd, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        raw = b"".join(chunks)
        after = os.fstat(source_fd)
        path_after = os.lstat(source)
        signature = lambda row: (row.st_dev, row.st_ino, row.st_mode, row.st_uid,
                                 row.st_nlink, row.st_size, row.st_mtime_ns, row.st_ctime_ns)
        if (signature(before) != signature(after) or signature(before) != signature(path_after)
                or len(raw) != before.st_size):
            raise RuntimeError("legacy Java fixture source changed while being read")
    finally:
        os.close(source_fd)
    source_sha = hashlib.sha256(raw).hexdigest()
    if source_sha != expected_source_sha256:
        raise RuntimeError("legacy Java fixture source differs from the frozen source manifest")
    target_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                        | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        view = memoryview(raw)
        while view:
            written = os.write(target_fd, view)
            if written <= 0:
                raise OSError("short write while staging the legacy Java fixture")
            view = view[written:]
        os.fsync(target_fd)
        target_state = os.fstat(target_fd)
    finally:
        os.close(target_fd)
    staged_raw = target.read_bytes()
    staged_sha = hashlib.sha256(staged_raw).hexdigest()
    if (len(staged_raw) != len(raw) or staged_sha != expected_source_sha256
            or not stat.S_ISREG(target_state.st_mode) or target_state.st_uid != os.getuid()
            or stat.S_IMODE(target_state.st_mode) != 0o600):
        raise RuntimeError("staged Java fixture failed exact frozen-byte or private-mode verification")
    workspace_fd = os.open(project_workspace, os.O_RDONLY
                           | getattr(os, "O_DIRECTORY", 0)
                           | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(workspace_fd)
    finally:
        os.close(workspace_fd)
    receipt = {
        "status": "PASS_EXACT_LEGACY_JAVA_FIXTURE_STAGED",
        "source": str(source), "source_bytes": len(raw), "source_sha256": source_sha,
        "staged": str(target), "staged_bytes": len(staged_raw),
        "staged_sha256": staged_sha, "staged_mode": stat.S_IMODE(target_state.st_mode),
        "exact_copy": True, "staged_for_mode": "legacy_saved_mph_diagnostic",
        "source_opened_read_only": True,
    }
    write_json(evidence / "fixture_stage.json", receipt)
    return receipt


def _copy_input_into_registered_workspace(source: Path, workspace: Path,
                                          evidence: Path) -> Path:
    target_dir = workspace / "inputs"
    target_dir.mkdir(parents=True, exist_ok=False)
    target = target_dir / "activity009-coarse-phase0-provisional.mph"
    if target.exists():
        raise FileExistsError(target)
    shutil.copyfile(source, target)
    with target.open("rb") as stream:
        os.fsync(stream.fileno())
    identity = {"source_path": str(source), "source_bytes": source.stat().st_size,
                "source_sha256": sha256_file(source), "project_copy_path": str(target),
                "project_copy_bytes": target.stat().st_size,
                "project_copy_sha256": sha256_file(target),
                "byte_exact_copy": sha256_file(source) == sha256_file(target)
                    and source.stat().st_size == target.stat().st_size,
                "source_opened_read_only": True, "model_mutation_or_resave": False}
    if identity["source_sha256"] != SOURCE_MODEL_SHA256 or not identity["byte_exact_copy"]:
        raise RuntimeError("registered-project MPH copy failed exact SHA/size verification")
    write_json(evidence / "saved_model_input_copy.json", identity)
    return target


def _load_saved_model_once(daemon: Any, model_path: Path, helpers: Mapping[str, Any],
                           evidence: Path, deadline_epoch: float) -> tuple[dict[str, Any], int, str]:
    project_id = science._active_project_id()
    response = science._dispatch(daemon, "model_load", {"path": str(model_path)},
                                 project_id=project_id, timeout_s=120.0)
    write_json(evidence / "model_load_dispatch_response.json", response)
    job_id = science._job_id(response)
    if job_id is not None:
        response = science._wait_job(daemon, response,
                                     deadline_epoch=deadline_epoch,
                                     cleanup_reserve_s=CLEANUP_RESERVE_SECONDS,
                                     label="model_load", evidence=evidence)
    write_json(evidence / "model_load_terminal_response.json", response)
    if response.get("success") is not True:
        raise RuntimeError("managed model_load failed: " + json.dumps(response, ensure_ascii=False, default=str)[:8000])
    execution = response.get("execution")
    data = response.get("data")
    ref = execution.get("model_ref") if isinstance(execution, Mapping) else None
    revision = execution.get("revision") if isinstance(execution, Mapping) else None
    if (not isinstance(ref, Mapping) or type(revision) is not int
            or not isinstance(ref.get("model_tag"), str)):
        raise RuntimeError("model_load response lacks exact managed ModelRef/revision")
    binding = daemon.backend.model_project_binding(ref)
    if (not isinstance(binding, Mapping) or binding.get("attribution") != "PROJECT_BOUND"
            or binding.get("project_id") != project_id):
        raise RuntimeError("model_load response is not bound to the exact registered diagnostic project")
    model_tag = str(data.get("model_tag") if isinstance(data, Mapping) else ref.get("model_tag"))
    if (isinstance(read_plan_mode := getattr(daemon, "_w23_r21_direction_dispatch_context", None), dict)
            and read_plan_mode.get("project_id") == project_id):
        _bind_direction_dispatch_context(daemon, project_id, ref, revision)
    inspect = science._dispatch(daemon, "model.inspect", {"detail": "summary"},
                                project_id=project_id, ref=dict(ref), revision=revision,
                                timeout_s=60.0)
    if inspect.get("success") is not True:
        raise RuntimeError("managed model.inspect after reopen failed")
    write_json(evidence / "model_project_binding.json", {
        "status": "PASS_SAVED_MODEL_LOADED_AND_PROJECT_BOUND",
        "source_sha256": SOURCE_MODEL_SHA256, "loaded_model_path": str(model_path),
        "project_id": project_id, "model_ref": dict(ref), "revision": revision,
        "model_tag": model_tag, "backend_binding": dict(binding),
        "model_inspect_response": inspect,
        "study_or_solver_invoked": False,
    })
    return dict(ref), revision, model_tag


def _response_body(response: Mapping[str, Any]) -> dict[str, Any]:
    if response.get("success") is not True:
        raise RuntimeError("managed readback failed: " + json.dumps(response, ensure_ascii=False, default=str)[:8000])
    return science._operation_body(response)


def _direction_public_read_once(daemon: Any, operation_id: str,
                                operation_arguments: Mapping[str, Any],
                                ref: dict[str, Any], revision: int, *,
                                project_id: str, deadline_epoch: float,
                                evidence: Path, summary: dict[str, Any]
                                ) -> tuple[dict[str, Any], dict[str, Any], int]:
    attempts = summary.setdefault("direction_discovery_calls", [])
    if not isinstance(attempts, list) or len(attempts) >= direction.MAX_DIRECTION_CALLS:
        raise RuntimeError("R21 public read call budget is exhausted; no further dispatch is allowed")
    if time.time() >= deadline_epoch - CLEANUP_RESERVE_SECONDS:
        raise TimeoutError("R21 public read reached the cleanup reserve; no further dispatch is allowed")
    ordinal = len(attempts) + 1
    label = f"direction_{ordinal:03d}_{operation_id.replace('.', '_')}"
    request = direction.operation_call(operation_id, operation_arguments)
    _bind_direction_dispatch_context(daemon, project_id, ref, revision)
    attempt = {"ordinal": ordinal, "label": label, "operation_id": operation_id,
               "arguments": dict(operation_arguments), "status": "DISPATCH_INVOKED",
               "before_model_ref": dict(ref), "before_revision": revision,
               "dispatch_response": None, "terminal_response": None}
    attempts.append(attempt)
    summary["direction_discovery_call_count"] = len(attempts)
    write_json(evidence / "summary.json", summary)
    try:
        response, new_ref, new_revision = _query_once(
            daemon, "operation_call", request, ref, revision,
            project_id=project_id, deadline_epoch=deadline_epoch,
            evidence=evidence, label=label)
    except BaseException as exc:
        attempt.update({"status": "DISPATCHED_NO_VERIFIED_TERMINAL",
                        "error": f"{type(exc).__name__}: {exc}"})
        write_json(evidence / "summary.json", summary)
        raise
    body = _response_body(response)
    body_path = evidence / f"{label}_body.json"
    write_json(body_path, body)
    dispatch_path = evidence / f"{label}_dispatch_response.json"
    terminal_path = evidence / f"{label}_terminal_response.json"
    attempt.update({"status": "SUCCEEDED_PUBLIC_READ",
                    "after_model_ref": dict(new_ref), "after_revision": new_revision,
                    "dispatch_response": {"path": str(dispatch_path), "sha256": sha256_file(dispatch_path)},
                    "terminal_response": {"path": str(terminal_path), "sha256": sha256_file(terminal_path)},
                    "body": {"path": str(body_path), "sha256": sha256_file(body_path)}})
    write_json(evidence / "summary.json", summary)
    return body, new_ref, new_revision


def _at_points_once(daemon: Any, *, dataset: str, solution: str,
                    expressions: tuple[str, ...], points: list[list[float]],
                    ref: dict[str, Any], revision: int, project_id: str,
                    deadline_epoch: float, evidence: Path, label: str,
                    requested_units: tuple[str, ...] | None = None, selection: Mapping[str, Any] | None = None,
                    summary: dict[str, Any] | None = None
                    ) -> tuple[dict[str, Any], dict[str, Any], int]:
    arguments = {
        "spec": {"expressions": list(expressions),
                 "solution": {"dataset": dataset, "solution": solution},
                 "complex_mode": "preserve"},
        "points": _public_points(points), "coordinate_unit": "um", "frame": "spatial",
    }
    if requested_units is not None:
        arguments["spec"]["units"] = list(requested_units)
    if selection is not None:
        arguments["spec"]["selection"] = dict(selection)
        arguments["spec"]["entity_dim"] = selection["entity_dimension"]
    request = {"operation_id": "result.at_points", "arguments": arguments}
    if not _allowed_saved_point_read(arguments):
        raise RuntimeError(f"{label} point readback is outside the exact saved-MPH allowlist")
    attempts_before = summary.get("point_read_attempts") if isinstance(summary, Mapping) else None
    if (not isinstance(attempts_before, list)
            or len(attempts_before) >= MAX_RESULT_AT_POINTS_CALLS):
        raise RuntimeError(f"{label} would exceed or cannot prove the frozen {MAX_RESULT_AT_POINTS_CALLS}-call result.at_points budget")
    before_ref, before_revision = dict(ref), revision
    before_worker_identity = dict(daemon.backend.worker_identity or {})
    response, ref, revision = _query_once(
        daemon, "operation_call", request, ref, revision,
        project_id=project_id, deadline_epoch=deadline_epoch,
        evidence=evidence, label=label, summary=summary, point_read_attempt=summary is not None)
    body = _response_body(response)
    job_id = science._job_id(response)
    durable_job_status = "SUCCEEDED_INLINE"
    if job_id is not None:
        job = daemon.store.job(job_id)
        durable_job_status = job.get("status") if isinstance(job, Mapping) else None
        if durable_job_status != "SUCCEEDED":
            raise RuntimeError(f"{label} result.at_points lacks a durable SUCCEEDED job receipt")
    write_json(evidence / f"{label}_native_result_raw.json", {
        "request": request, "response": response, "body": body,
        "durable_job_id": job_id, "durable_job_status": durable_job_status,
        "scope": "read-only saved solution result.at_points; no Study.run or solver.run",
        "native_scientific_acceptance": "NOT_EVALUATED",
    })
    context = {"model_ref_before":before_ref,"model_ref_after":dict(ref),"revision_before":before_revision,"revision_after":revision,
        "worker_identity_before":before_worker_identity,"worker_identity_after":dict(daemon.backend.worker_identity or {})}
    write_json(evidence / f"{label}_exact_context.json", context)
    if (before_ref != ref or before_revision != revision or before_worker_identity != context["worker_identity_after"]):
        raise RuntimeError("point readback ModelRef/revision/Worker epoch changed; no following dispatch")
    return body, ref, revision


def _append_completed_point_projection(summary: dict[str, Any], evidence: Path,
                                       label: str) -> dict[str, Any]:
    """Persist one exact successful raw+decoded point receipt before the next slot.

    This is an incremental evidence index only. It never upgrades a readback to
    scientific acceptance, and an UNKNOWN/nonterminal operation cannot satisfy
    the raw terminal-status checks below.
    """
    raw_path = evidence / f"{label}_native_result_raw.json"
    decoded_path = evidence / f"{label}_decoded.json"
    if not raw_path.is_file() or not decoded_path.is_file():
        raise RuntimeError(f"{label} lacks both durable raw and decoded result receipts")
    try:
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        decoded = json.loads(decoded_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} raw/decoded result receipt is unreadable") from exc
    request = raw.get("request") if isinstance(raw, Mapping) else None
    response = raw.get("response") if isinstance(raw, Mapping) else None
    body = raw.get("body") if isinstance(raw, Mapping) else None
    arguments = request.get("arguments") if isinstance(request, Mapping) else None
    spec = arguments.get("spec") if isinstance(arguments, Mapping) else None
    expressions = spec.get("expressions") if isinstance(spec, Mapping) else None
    solution = spec.get("solution") if isinstance(spec, Mapping) else None
    durable_status = raw.get("durable_job_status") if isinstance(raw, Mapping) else None
    if (not isinstance(request, Mapping) or request.get("operation_id") != "result.at_points"
            or not isinstance(arguments, Mapping) or not isinstance(spec, Mapping)
            or not isinstance(expressions, list) or not expressions
            or not isinstance(solution, Mapping)
            or not isinstance(response, Mapping) or response.get("success") is not True
            or not isinstance(body, Mapping)
            or durable_status not in {"SUCCEEDED", "SUCCEEDED_INLINE"}
            or science._job_id(response) != raw.get("durable_job_id")
            or raw.get("native_scientific_acceptance") != "NOT_EVALUATED"
            or not isinstance(decoded, Mapping)
            or decoded.get("status") != "PRODUCTION_NATIVE_COMPLEX_POINT_READBACK"
            or decoded.get("operation") != "result.at_points"
            or decoded.get("science_acceptance") != "NOT_EVALUATED"
            or decoded.get("dataset") != solution.get("dataset")
            or decoded.get("solution") != solution.get("solution")
            or [row.get("expression") for row in decoded.get("expressions", [])
                if isinstance(row, Mapping)] != expressions):
        raise RuntimeError(f"{label} cannot be projected as a succeeded, decoded readback-only call")
    requested_units = spec.get("units")
    if requested_units is not None:
        requested_application = decoded.get("requested_unit_application")
        if (not isinstance(requested_units, list)
                or not isinstance(requested_application, Mapping)
                or requested_application.get("requested_expressions") != expressions
                or requested_application.get("requested_units") != requested_units
                or decoded.get("expression_units") != dict(zip(expressions, requested_units))):
            raise RuntimeError(f"{label} decoded units differ from its exact public request")
    calls = summary.get("native_calls")
    if not isinstance(calls, list) or any(
            isinstance(row, Mapping) and row.get("label") == label for row in calls):
        raise RuntimeError(f"{label} projection list is missing or already contains this call")
    attempts = summary.get("point_read_attempts")
    attempt = next((row for row in attempts if isinstance(row, Mapping)
                    and row.get("label") == label), None) if isinstance(attempts, list) else None
    dispatch_receipt = attempt.get("dispatch_response") if isinstance(attempt, Mapping) else None
    dispatch_path = Path(dispatch_receipt.get("path", "")) if isinstance(dispatch_receipt, Mapping) else None
    dispatch_response: Mapping[str, Any] | None = None
    if (dispatch_path is not None and dispatch_path == evidence / f"{label}_dispatch_response.json"
            and dispatch_path.is_file()
            and dispatch_receipt.get("sha256") == sha256_file(dispatch_path)):
        try:
            observed_dispatch = json.loads(dispatch_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            observed_dispatch = None
        dispatch_response = observed_dispatch if isinstance(observed_dispatch, Mapping) else None
    if (not isinstance(attempt, dict)
            or attempt.get("status") not in {"SUCCEEDED", "SUCCEEDED_INLINE"}
            or attempt.get("terminal_status") not in {"SUCCEEDED", "SUCCEEDED_INLINE"}
            or attempt.get("job_id") != raw.get("durable_job_id")
            or not isinstance(dispatch_response, Mapping)
            or dispatch_response.get("success") is not True
            or science._job_id(dispatch_response) != attempt.get("job_id")):
        raise RuntimeError(f"{label} lacks a succeeded matching dispatch attempt receipt")
    projection = {
        "ordinal": len(calls) + 1,
        "label": label,
        "status": "SUCCEEDED_RAW_AND_DECODED_READBACK_ONLY",
        "operation_id": "result.at_points",
        "job_id": raw.get("durable_job_id"),
        "dataset": decoded["dataset"], "solution": decoded["solution"],
        "sample_count": decoded["sample_count"],
        "expression_units": dict(decoded.get("expression_units") or {}),
        "requested_units": list(requested_units) if requested_units is not None else None,
        "raw_native_result": {"path": str(raw_path), "bytes": raw_path.stat().st_size,
                              "sha256": sha256_file(raw_path)},
        "decoded_result": {"path": str(decoded_path), "bytes": decoded_path.stat().st_size,
                           "sha256": sha256_file(decoded_path)},
        "native_scientific_acceptance": "NOT_EVALUATED",
    }
    calls.append(projection)
    attempt["verified_completed"] = True
    attempt["verification_status"] = "SUCCEEDED_RAW_AND_DECODED_READBACK_ONLY"
    attempt["decoded_result"] = dict(projection["decoded_result"])
    summary["result_at_points_attempt_count"] = len(attempts)
    summary["result_at_points_verified_completed_count"] = len(calls)
    try:
        write_json(evidence / "summary.json", summary)
    except BaseException:
        calls.pop()
        attempt["verified_completed"] = False
        attempt.pop("verification_status", None)
        attempt.pop("decoded_result", None)
        raise
    return projection


def _frequency_readback_once(
    daemon: Any, server: Any, *, requested_unit: str, ref: dict[str, Any], revision: int,
    project_id: str, deadline_epoch: float, evidence: Path, count_witness: Mapping[str, Any],
    expected_context: Mapping[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], int]:
    if requested_unit not in {"Hz", "GHz"}:
        raise RuntimeError("frequency-control unit is outside the frozen Hz/GHz pair")
    if time.time() >= deadline_epoch - CLEANUP_RESERVE_SECONDS:
        raise TimeoutError("frequency-control readback reached cleanup reserve")
    label = "physical_frequency_" + requested_unit
    def epoch(stage: str) -> dict[str, Any]:
        health = server.worker.health(timeout_s=5.0)
        write_json(evidence / f"{label}_worker_health_{stage}.json", health)
        if (not isinstance(health, Mapping) or health.get("ok") is not True
                or health.get("connected") is not True or health.get("queued_or_running") != 0
                or not isinstance(health.get("instance_id"), str) or not health["instance_id"]
                or type(health.get("generation")) is not int or health["generation"] <= 0
                or not isinstance(health.get("server"), str) or not health["server"]):
            raise RuntimeError("frequency-control actual Worker health/epoch is not quiescent and bound")
        return {key: health[key] for key in ("instance_id", "generation", "server")}
    before_epoch = epoch("before")
    before_ref, before_revision = dict(ref), revision
    if expected_context is not None:
        if (before_ref != expected_context.get("model_ref_before")
                or before_revision != expected_context.get("revision_before")
                or before_epoch != expected_context.get("worker_epoch_before")):
            write_json(evidence / f"{label}_pre_dispatch_identity_refusal.json", {
                "status": "FAIL_CONTEXT_CHANGED_BEFORE_SECOND_UNIT_READ", "model_ref": before_ref,
                "revision": before_revision, "worker_epoch": before_epoch,
                "expected_context": dict(expected_context), "native_point_dispatch_issued": False})
            raise RuntimeError("frequency-control ModelRef/revision/Worker epoch changed before second unit dispatch")
    body, ref, revision = _at_points_once(
        daemon, dataset="dset1", solution="sol1", expressions=FREQUENCY_EXPRESSIONS,
        points=[[-10.0, 0.0]], ref=ref, revision=revision, project_id=project_id,
        deadline_epoch=deadline_epoch, evidence=evidence, label=label,
        requested_units=(requested_unit,), summary=summary)
    after_epoch = epoch("after")
    context = {"model_ref_before": before_ref, "model_ref_after": dict(ref),
               "revision_before": before_revision, "revision_after": revision,
               "worker_epoch_before": before_epoch, "worker_epoch_after": after_epoch}
    write_json(evidence / f"{label}_exact_context.json", context)
    if before_ref != ref or before_revision != revision or before_epoch != after_epoch:
        raise RuntimeError("frequency-control ModelRef/revision/Worker epoch changed during unit read; no further point dispatch")
    decoded = protocol.decode_production_complex_point_readback(
        body, dataset_tag="dset1", solution_tag="sol1", expressions=FREQUENCY_EXPRESSIONS,
        expected_points=[[-10.0, 0.0]], expected_solution_count=1, plane_id=label,
        expected_count_witness=count_witness, expected_expression_units={"freq": requested_unit},
        expected_requested_units=(requested_unit,), scalar_frequency=True)
    write_json(evidence / f"{label}_decoded.json", decoded)
    return decoded, context, ref, revision


def validate_fixture_readback(readback: Mapping[str, Any]) -> dict[str, Any]:
    if readback.get("diagnostic_phase") != "saved_solution_diagnostic":
        raise ValueError("Java readback is not the frozen diagnostic phase")
    if readback.get("explicit_study_or_solver_run_calls_in_this_phase") != 0:
        raise ValueError("fixture reports a forbidden Study/solver run call")
    if readback.get("native_scientific_acceptance") != "NOT_ACCEPTED_DIAGNOSTIC_READBACK_ONLY":
        raise ValueError("diagnostic readback must remain explicitly NOT_ACCEPTED")
    if readback.get("internal_solver_execution_count") != "UNKNOWN_NOT_INFERRED_FROM_READBACK":
        raise ValueError("readback cannot infer internal solver execution count")
    queries = readback.get("study_solver_sequence_queries")
    if not isinstance(queries, list) or [row.get("arguments", [None])[0] for row in queries] != list(STUDY_SEQUENCE_TYPES):
        raise ValueError("diagnostic did not preserve all seven exact Study.getSolverSequences query types")
    for row in queries:
        if row.get("status") not in {"CAPTURED", "ERROR"}:
            raise ValueError("Study sequence query lacks explicit terminal readback status")
    datasets = readback.get("datasets")
    solutions = readback.get("solver_sequences")
    dataset_info = readback.get("dataset_solution_info")
    if not isinstance(datasets, list) or not isinstance(solutions, list) or not isinstance(dataset_info, list):
        raise ValueError("diagnostic omitted datasets, solver trees, or exact dataset-bound SolutionInfo reads")
    matrix_calls = []
    direct_sequence_value_calls = 0
    solution_info_receivers = 0

    def require_raw_outcome(call: Mapping[str, Any], label: str) -> None:
        status = call.get("status")
        if status == "CAPTURED":
            if "actual_return_type" not in call or "return_value" not in call:
                raise ValueError(f"{label} lacks its raw captured return type/value")
        elif status == "ERROR":
            if not isinstance(call.get("error_type"), str) or "error" not in call:
                raise ValueError(f"{label} lacks its raw getter error")
        else:
            raise ValueError(f"{label} lacks a captured value or explicit error")

    for row in solutions:
        sequence_type = row.get("sequence_receiver_type")
        parameter_names = row.get("parameter_names_getter")
        if not isinstance(parameter_names, Mapping) or parameter_names.get("method") != "getPNames":
            raise ValueError("SolverSequence.getPNames() getter receipt is missing")
        if sequence_type is not None and parameter_names.get("receiver_type") != sequence_type:
            raise ValueError("SolverSequence.getPNames receiver differs from the observed sequence object")
        require_raw_outcome(parameter_names, "SolverSequence.getPNames")

    for row in list(solutions) + list(dataset_info):
        info = row.get("solution_info")
        if not isinstance(info, Mapping):
            continue
        receiver_type = info.get("solution_info_receiver_type")
        info_getter = info.get("solution_info_getter")
        if not isinstance(info_getter, Mapping):
            raise ValueError("SolutionInfo receiver getter was not preserved")
        require_raw_outcome(info_getter, "SolverSequence.getSolutioninfo")
        if receiver_type is not None:
            if not isinstance(receiver_type, str) or not receiver_type:
                raise ValueError("SolutionInfo actual receiver type is malformed")
            solution_info_receivers += 1
        calls = info.get("metadata_calls", [])
        if not isinstance(calls, list):
            raise ValueError("SolutionInfo metadata calls are not an ordered raw list")
        for call in calls if isinstance(calls, list) else []:
            if not isinstance(call, Mapping):
                raise ValueError("SolutionInfo metadata call is not an object")
            if receiver_type is not None and call.get("receiver_type") != receiver_type:
                raise ValueError("SolutionInfo getter receiver type differs from the exact getSolutioninfo receiver")
            require_raw_outcome(call, f"SolutionInfo.{call.get('method')}")
            if call.get("method") not in {"getPNames", "getPvals", "getPvalsImag", "getUnits"}:
                continue
            matrix_calls.append(call)
            args = call.get("arguments")
            if call.get("argument_types") != ["int[][]"]:
                raise ValueError("SolutionInfo matrix getter did not preserve declared int[][] argument type")
            if (not isinstance(args, list) or len(args) != 1
                    or not isinstance(args[0], list) or len(args[0]) != 1
                    or not isinstance(args[0][0], list) or len(args[0][0]) != 2
                    or any(type(value) is not int or value < 1 for value in args[0][0])):
                raise ValueError("SolutionInfo matrix getter argument is not one documented one-based (outer,inner) tuple")
        info_pairs = info.get("observed_one_based_outer_inner_pairs")
        sequence_reads = info.get("solver_sequence_parameter_reads")
        if not isinstance(info_pairs, list) or not isinstance(sequence_reads, list):
            raise ValueError("SolutionInfo to SolverSequence one-based pair observations are missing")
        normalized_pairs: set[tuple[int, int]] = set()
        for pair in info_pairs:
            if (not isinstance(pair, list) or len(pair) != 2
                    or any(type(value) is not int or value < 1 for value in pair)):
                raise ValueError("observed SolutionInfo tuple is not a positive one-based (outer,inner) pair")
            normalized_pairs.add((pair[0], pair[1]))
        seen_sequence_pairs: set[tuple[int, int]] = set()
        for pair_row in sequence_reads:
            if not isinstance(pair_row, Mapping):
                raise ValueError("SolverSequence parameter read is not an object")
            outer, inner = pair_row.get("outer"), pair_row.get("inner_solnum")
            if type(outer) is not int or type(inner) is not int or (outer, inner) not in normalized_pairs:
                raise ValueError("SolverSequence getPVals probe is not bound to an observed SolutionInfo tuple")
            seen_sequence_pairs.add((outer, inner))
            for key, method in (("real_parameter_values", "getPVals"),
                                ("imaginary_parameter_values", "getPValsImag")):
                call = pair_row.get(key)
                if not isinstance(call, Mapping) or call.get("method") != method:
                    raise ValueError(f"SolverSequence {method}(inner_solnum) call is missing")
                if call.get("argument_types") != ["int"] or call.get("arguments") != [inner]:
                    raise ValueError(f"SolverSequence {method} did not use the observed one-based inner solution number")
                sequence_type = row.get("sequence_receiver_type") or row.get("solver_sequence_type")
                if sequence_type is not None and call.get("receiver_type") != sequence_type:
                    raise ValueError(f"SolverSequence {method} receiver differs from the observed sequence object")
                require_raw_outcome(call, f"SolverSequence.{method}")
                direct_sequence_value_calls += 1
        if seen_sequence_pairs != normalized_pairs:
            raise ValueError("SolverSequence real/imaginary value reads do not cover the exact observed pair set")
    if direct_sequence_value_calls != 2 * sum(
            len(row.get("solution_info", {}).get("observed_one_based_outer_inner_pairs", []))
            for row in list(solutions) + list(dataset_info)
            if isinstance(row, Mapping) and isinstance(row.get("solution_info"), Mapping)):
        raise ValueError("direct SolverSequence real/imaginary parameter value reads are incomplete")
    return {
        "status": "PASS_DIAGNOSTIC_CAPTURE_SCHEMA_ONLY",
        "study_query_types": list(STUDY_SEQUENCE_TYPES),
        "dataset_count": len(datasets), "solver_sequence_count": len(solutions),
        "dataset_bound_solution_info_count": len(dataset_info),
        "typed_matrix_getter_calls": len(matrix_calls),
        "direct_sequence_parameter_value_calls": direct_sequence_value_calls,
        "solution_info_receiver_count": solution_info_receivers,
        "matrix_tuple_semantics": "one-based (outer,inner), documented by COMSOL 6.4 doc7709/chunk23066",
        "solution_roles_inferred": False,
        "scientific_acceptance": "NOT_ACCEPTED_DIAGNOSTIC_READBACK_ONLY",
        "internal_solver_execution_count": "UNKNOWN_NOT_INFERRED_FROM_READBACK",
    }


def _allowed_metadata_dispatch(request: Mapping[str, Any]) -> bool:
    operation = request.get("operation")
    if operation in {"server_connect", "model_load", "model.inspect"}:
        return True
    arguments = request.get("arguments")
    if operation != "operation_call" or not isinstance(arguments, Mapping) or set(arguments) != {"operation_id", "arguments"}:
        return False
    body = arguments.get("arguments")
    return (arguments.get("operation_id") == "code.execute_java" and isinstance(body, Mapping)
        and set(body) == {"source_artifact", "entrypoint", "arguments", "mode"}
        and body.get("source_artifact") == FIXTURE.name
        and body.get("entrypoint") == "NativeW23TEScienceFixture#run"
        and body.get("mode") == "trusted"
        and body.get("arguments") == {"phase": "definition_boundary_metadata"})


def validate_definition_boundary_metadata(readback: Mapping[str, Any]) -> dict[str, Any]:
    if (not isinstance(readback, Mapping) or readback.get("execution_state_unknown") is True
        or readback.get("status") == "READ_FAILED_STOPPED_NO_FURTHER_GETTERS" or readback.get("diagnostic_phase") != "definition_boundary_metadata"
        or readback.get("component_tag") != "comp1" or readback.get("geometry_tag") != "geom1"
        or readback.get("physics_tag") != "ewfd" or readback.get("geometry_dimension") != 2
        or readback.get("numerical_feature_dispatch_count") != 0
        or readback.get("study_or_solver_invoked") is not False
        or readback.get("physics_or_selection_mutated") is not False
        or readback.get("scientific_acceptance") != "NOT_EVALUATED"):
        raise RuntimeError("definition/boundary metadata exact identity/readonly envelope is invalid")
    for name in ("physics_name_raw", "physics_scope_raw", "physics_type", "polarization_components_raw", "electric_field_name_raw", "geometry_length_unit_raw"):
        if not isinstance(readback.get(name), str) or not readback[name]:
            raise RuntimeError("definition metadata native identity string is missing")
    for name in ("electric_field_components_raw", "electric_field_fieldname_raw"):
        values = readback.get(name)
        if not isinstance(values, list) or len(values) > 128 or any(not isinstance(v, str) for v in values):
            raise RuntimeError("definition metadata native field components are missing")
    box = readback.get("geometry_bounding_box_raw")
    if not isinstance(box, list) or len(box) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) for v in box):
        raise RuntimeError("definition metadata exact 2D geometry bounding box is missing")
    updown = readback.get("boundary_up_down_raw")
    if not isinstance(updown, list) or len(updown) != 2 or any(not isinstance(row, list) or len(row) > 512 or any(type(v) is not int for v in row) for row in updown):
        raise RuntimeError("definition metadata native up/down rows are missing")
    tables = readback.get("definition_tables")
    if not isinstance(tables, list) or len(tables) > 264:
        raise RuntimeError("definition metadata table list is missing or exceeds bound")
    total_rows = total_bytes = 0
    for table in tables:
        if not isinstance(table, Mapping) or table.get("table_type") != "Expression" or table.get("options") != ["recursive", "all"]:
            raise RuntimeError("definition metadata table is not exact all-column Expression request")
        rows = table.get("raw_rows")
        widths = table.get("row_widths")
        if not isinstance(rows, list) or not isinstance(widths, list) or len(rows) != len(widths):
            raise RuntimeError("definition metadata rows/widths are incomplete")
        total_rows += len(rows)
        for row, width in zip(rows, widths):
            if row is None:
                if width != -1:raise RuntimeError("native null metadata row was not retained")
                continue
            if not isinstance(row, list) or len(row) > 128 or width != len(row) or any(cell is not None and not isinstance(cell, str) for cell in row):
                raise RuntimeError("definition metadata native columns are malformed")
            total_bytes += sum(len(cell.encode("utf-8")) for cell in row if cell is not None)
    if total_rows > 16384 or total_bytes > 8388608:
        raise RuntimeError("definition metadata payload exceeds frozen row/UTF8 bound")
    selections = readback.get("named_selections")
    required = {"selReceiverX8", "selInputPort", "selCoreCaptureX8", "selCoreMaterial", "selPmlDomains", "selCladdingBottom", "selCladdingTop"}
    if not isinstance(selections, list) or len(selections) != 7 or {row.get("selection_tag") for row in selections if isinstance(row, Mapping)} != required:
        raise RuntimeError("definition metadata exact named selections are incomplete")
    for row in selections:
        entities = row.get("entities_raw")
        if (row.get("geometry_raw") != "geom1" or type(row.get("dimension_raw")) is not int or row.get("dimension_raw") not in (1, 2)
            or not isinstance(entities, list) or len(entities) > 512
            or any(type(value) is not int or value < 1 for value in entities)):
            raise RuntimeError("definition metadata selection geometry/dimension/entities are invalid")
    if len(json.dumps(readback, allow_nan=False).encode("utf-8")) > 16777216:
        raise RuntimeError("definition metadata JSON response exceeds frozen 16MiB publication bound")
    return {"status": "CAPTURED_RAW_METADATA_SCHEMA_ONLY_NOT_DEFINITION_OR_SCIENCE_ACCEPTANCE",
        "table_count": len(tables), "raw_rows": total_rows, "raw_UTF8_bytes": total_bytes,
        "definitions_interpreted": False, "boundary_side_chosen": False, "native_field_or_port_values": "NOT_RUN"}


def _set_native_dispatch_guard(daemon: Any, journal: Path, *, direction_only: bool = False,
                               expected_model_path: Path | None = None,
                               expected_project_id: str | None = None) -> None:
    if direction_only:
        if not isinstance(expected_project_id, str) or not expected_project_id:
            raise ValueError("direction-only guard requires the exact registered project ID")
        context = {"project_id": expected_project_id, "model_ref": None, "revision": None}
        daemon._w23_r21_direction_dispatch_context = context
        allow = lambda request: _allowed_direction_discovery_dispatch(
            request, expected_model_path=expected_model_path,
            expected_project_id=context["project_id"],
            expected_model_ref=context["model_ref"], expected_revision=context["revision"])
    else:
        allow = _allowed_diagnostic_dispatch
    _set_dispatch_allowlist(daemon, journal, allow=allow)


def _validate_private_read_input(path: Path, label: str, *, candidate_only: bool) -> None:
    if (not path.is_absolute() or path.resolve(strict=True) != path or path.is_symlink()
            or (candidate_only and REPO not in path.parents)):
        raise ValueError(label + " must be a canonical ordinary private-candidate path")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError(label + " must be an owner-bound ordinary file")


def _expected_work_path() -> Path:
    # This legacy no-binding path remains only for the explicitly preserved readback route.
    suffix = ACTIVITY_ID.removeprefix("w23-saved-mph-direction-discovery-")
    if suffix == ACTIVITY_ID or "/" in suffix or "/" in CANDIDATE_REVISION:
        raise RuntimeError("invalid frozen activity/revision identity")
    return Path("/private/tmp") / ("comsol-mcp-w23-direction-discovery-" + suffix + "-" + CANDIDATE_REVISION)


def _validate_fresh_execution_paths(freeze: Mapping[str, Any], work: Path, evidence: Path) -> None:
    binding = freeze.get("fresh_runtime_binding")
    expected_work = Path(binding["namespace"])/"work" if binding else _expected_work_path()
    expected_evidence = Path(freeze["execution_evidence_path"])
    if Path(freeze["work_path"]) != expected_work or work != expected_work:
        raise ValueError("--work must equal the exact frozen activity/revision canonical path")
    if binding:
        fresh_runtime.scoped_path(binding, work, absent=True)
        fresh_runtime.scoped_path(binding, evidence, absent=True)
    if evidence != expected_evidence or not evidence.is_absolute() or (binding is None and REPO not in evidence.parents):
        raise ValueError("--evidence must equal the exact frozen private-candidate path")
    for label,path in (("work",work),("evidence",evidence)):
        if (path.resolve(strict=False) != path or any(part in (".","..") for part in path.parts)
                or path.is_symlink()):
            raise ValueError(label + " path must be canonical without symlinks")
        if path.exists():
            raise FileExistsError(label + " path must be fresh")
        parent = path.parent
        if parent.resolve(strict=True) != parent or parent.is_symlink():
            raise ValueError(label + " parent must be canonical without symlinks")
        observed = parent.stat()
        allowed_owners = {0,os.getuid()} if label == "work" else {os.getuid()}
        if not stat.S_ISDIR(observed.st_mode) or observed.st_uid not in allowed_owners:
            raise ValueError(label + " parent ownership/type is not authorized")


def execute(freeze_path: Path, freeze_sha256: str, approval_path: Path,
            saved_model: Path, work: Path, evidence: Path) -> dict[str, Any]:
    _validate_private_read_input(freeze_path, "freeze", candidate_only=False)
    initial = json.loads(fresh_runtime.read_owned(freeze_path), object_pairs_hook=fresh_runtime._pairs)
    binding = initial.get("fresh_runtime_binding")
    if binding is not None:
        fresh_runtime.validate(binding, repo=REPO, budget=fresh_budget(), install=science.INSTALL_ROOT, jdk=science.JAVA11)
        fresh_runtime.scoped_path(binding, freeze_path.parent)
        if approval_path.parent != freeze_path.parent or approval_path != Path(initial["approval_path_expected"]):
            raise RuntimeError("fresh Root approval outside exact frozen candidate")
        fresh_runtime.read_owned(approval_path)
    else:
        _validate_private_read_input(freeze_path,"freeze",candidate_only=True)
        _validate_private_read_input(approval_path,"approval",candidate_only=True)
    _validate_private_read_input(saved_model,"saved model",candidate_only=False)
    if binding is not None:
        _validate_fresh_import_home(binding, work)
    helpers = science._load_helper_refs()
    freeze = _validate_freeze(freeze_path, freeze_sha256)
    approval = json.loads(fresh_runtime.read_owned(approval_path), object_pairs_hook=fresh_runtime._pairs) if binding else json.loads(approval_path.read_text(encoding="utf-8"))
    planned_argv = _native_argv(freeze_path, freeze_sha256, approval_path,
                                saved_model, work, evidence, binding)
    if not isinstance(approval, Mapping):
        raise RuntimeError("root approval receipt must be a JSON object")
    _validate_approval(approval, freeze, freeze_sha256, planned_argv)
    if binding is None and Path(sys.executable).resolve() != EXPECTED_PYTHON.resolve():
        raise RuntimeError("native diagnostic interpreter differs from frozen Python 3.12 runtime")
    if os.environ.get("PYTHONPATH") != str(REPO) or os.environ.get("TMPDIR") != "/private/tmp":
        raise RuntimeError("native environment must use candidate-only PYTHONPATH and TMPDIR=/private/tmp")
    if any(os.environ.get(name) is not None for name in (
            "CODEX_NETWORK_GUARD_LOG", "CODEX_NETWORK_GUARD_LABEL",
            "CODEX_NETWORK_GUARD_RUN_ID")):
        raise RuntimeError("native environment must not inherit offline-test network-guard variables")
    if sys.platform != "darwin":
        raise RuntimeError(f"native saved-MPH diagnostic requires macOS, observed {sys.platform}")
    if saved_model != SOURCE_MODEL or not saved_model.is_file() or saved_model.is_symlink():
        raise RuntimeError("saved-MPH path differs from the exact Activity009 source artifact")
    if saved_model.stat().st_size != SOURCE_MODEL_BYTES or sha256_file(saved_model) != SOURCE_MODEL_SHA256:
        raise RuntimeError("saved-MPH bytes/SHA differ from the frozen Activity009 input")
    _validate_fresh_execution_paths(freeze, work, evidence)
    _assert_source_inputs()
    if _source_files() != freeze["source_files"]:
        raise RuntimeError("frozen source closure changed immediately before prebirth inventory")
    if binding is not None:
        _validate_fresh_import_home(binding, work)
    return _execute_native_campaign(helpers, freeze_path, freeze_sha256, approval,
                                    saved_model, work, evidence, freeze)


def _execute_native_campaign(helpers: Mapping[str, Any], freeze_path: Path,
                             freeze_sha256: str, approval: Mapping[str, Any],
                             saved_model: Path, work: Path, evidence: Path,
                             freeze: Mapping[str, Any]) -> dict[str, Any]:
    """Keep original native owners alive even if cleanup diagnostics fail."""
    owner = W23OriginalOwnerHold(binding=freeze.get("fresh_runtime_binding"),
        freeze_sha256=freeze_sha256, source_sha256=freeze["source_manifest_sha256"],
        evidence=evidence, writer=write_json)
    failure = None
    try:
        result = _execute_native_campaign_body(helpers, freeze_path, freeze_sha256, approval,
                                               saved_model, work, evidence, freeze, owner)
    except BaseException as exc:
        owner.cause(exc)
        failure = exc
        result = owner.summary
    try:
        observed = owner.observe()
    except BaseException as exc:
        owner.cause(exc)
        owner.diagnostic("outer_original_child_observation", exc)
        observed = {"worker": {"created": None, "state": "OBSERVATION_UNKNOWN"},
                    "server": {"created": None, "state": "OBSERVATION_UNKNOWN"}}
    try:
        original_children_terminal = owner.terminal(observed)
    except BaseException as exc:
        owner.cause(exc)
        owner.diagnostic("outer_original_child_terminal_gate", exc)
        original_children_terminal = False
    cleanup_verified = (result.get("cleanup_status") ==
                        "CLEANUP_VERIFIED_EXACT_OWNED_PIDS_AND_PORTS_ABSENT")
    # The cleanup enum is produced by a separate body. Any new, missing, or
    # blocked status must still reach the original-Popen owner if a child is
    # live or unobservable. A positive cleanup receipt cannot override that
    # original-Popen observation.
    unsafe = not original_children_terminal
    if unsafe and cleanup_verified:
        owner.cause(RuntimeError("normal cleanup receipt contradicts original child Popen observation"))
        result["cleanup_status"] = "CLEANUP_UNVERIFIED"
        if isinstance(owner.summary, dict):
            owner.summary["cleanup_status"] = "CLEANUP_UNVERIFIED"
        unsafe = True
    if unsafe:
        result = owner.run()  # Never returns while an original child is alive/unobservable.
        try:
            finished = time.time()
            budget = result.get("birth_inclusive_budget", {})
            birth = budget.get("birth_epoch")
            if isinstance(birth, (int, float)):
                budget.update({"finished_epoch_includes_cleanup": None,
                    "observed_original_children_exit_epoch": finished,
                    "elapsed_seconds_until_children_observed_exited": max(0.0, finished-birth),
                    "budget_exceeded": finished-birth > MAX_BIRTH_SECONDS,
                    "standard_cleanup_complete": False, "child_lifetime_resolved": True})
            result["finished_at_utc"] = utc_now()
        except BaseException as exc:
            owner.diagnostic("terminal_budget_or_timestamp", exc)
            result["finished_at_utc"] = None
            result["terminal_time_diagnostic"] = "UNAVAILABLE_NOT_NATIVE_ACCEPTANCE"
        try: write_json(evidence / "summary.json", result)
        except BaseException as exc: owner.diagnostic("final_summary", exc)
        return result
    if failure is not None:
        raise failure
    return result


def _execute_native_campaign_body(helpers: Mapping[str, Any], freeze_path: Path,
                             freeze_sha256: str, approval: Mapping[str, Any],
                             saved_model: Path, work: Path, evidence: Path,
                             freeze: Mapping[str, Any], owner: W23OriginalOwnerHold) -> dict[str, Any]:
    """Native-effect handoff after immutable CLI guards, under original owner."""
    label = f"W23 fresh saved-MPH diagnostic {_activity(freeze)} {CANDIDATE_REVISION}" if freeze.get("fresh_runtime_binding") else PROJECT_LABEL
    evidence.mkdir(parents=True, exist_ok=False)
    write_json(evidence / "root_approval.json", dict(approval))
    summary: dict[str, Any] = {
        "activity_id": _activity(freeze), "candidate_id": CANDIDATE_ID,
        "candidate_revision": CANDIDATE_REVISION,
        "status": "PREBIRTH_NOT_STARTED",
        "native_scientific_acceptance": "NOT_ACCEPTED_DIAGNOSTIC_READBACK_ONLY",
        "input_model_sha256": SOURCE_MODEL_SHA256,
        "study_run_dispatch_count": 0, "solver_run_dispatch_count": 0,
        "actual_internal_solver_execution_count": "UNKNOWN_NOT_INFERRED_FROM_READBACK",
        "server_birth": None, "server_pid": None, "worker_pid": None,
        "cleanup_status": "NOT_STARTED", "native_calls": [], "point_read_attempts": [],
        "result_at_points_attempt_count": 0,
        "result_at_points_verified_completed_count": 0,
    }
    owner.summary = summary
    write_json(evidence / "summary.json", summary)
    direction_only = freeze.get("read_only_call_plan", {}).get("mode") == "direction_discovery_only"
    preflight = helpers["preflight_module"]
    server = None
    authority_daemon = None
    daemon = None
    project_receipt: dict[str, Any] | None = None
    project_id: str | None = None
    server_identity: dict[str, Any] = {}
    worker_identity: dict[str, Any] | None = None
    worker_port: int | None = None
    direct_journal = science.DirectRpcJournal(evidence / "direct_worker_rpcs.jsonl")
    managed_dispatch_journal = evidence / "managed_dispatches.jsonl"
    birth_epoch: float | None = None
    deadline_epoch: float | None = None
    fixture_terminal = False
    unknown_seen = False
    dispatch_guard_count = 0
    project_workspace: Path | None = None
    diagnostic_readback: Mapping[str, Any] | None = None
    production_dataset_rows: list[Mapping[str, Any]] = []
    solution_index_receipts: list[dict[str, Any]] = []
    try:
        compile_identity = science._check_offline_receipts(freeze_path, freeze)
        write_json(evidence / "frozen_compile_identity.json", compile_identity)
        inventory = preflight._process_inventory()
        write_json(evidence / "prelaunch_inventory.json", inventory)
        if not inventory.get("quiescent"):
            raise RuntimeError("fresh prelaunch inventory is not quiescent; no COMSOL server started")
        server = helpers["NativeLoopbackServer"](work, evidence,
                                                  event_log=evidence / "events.jsonl")
        owner.bind_server(server)
        shadow = server.prepare_shadow()
        write_json(evidence / "private_shadow_receipt.json", shadow)
        if _source_files() != freeze["source_files"]:
            raise RuntimeError("frozen source closure changed while staging private COMSOL tree")
        prebirth = preflight._process_inventory()
        write_json(evidence / "prebirth_inventory.json", prebirth)
        if not prebirth.get("quiescent"):
            raise RuntimeError("fresh pre-birth inventory is not quiescent")
        science.PROJECT_LABEL = label
        science._set_prebirth_project_authority_opt_in()
        authority_daemon = helpers["ControlDaemon"](work / "control", worker=None,
                                                     project_root=server.project)
        create_request = {
            "operation": "project.create",
            "arguments": {"label": label, "workspace": "science",
                          "policy": {"permissions": ["inspect", "project_write", "trusted_code"]}},
            "execution": {"request_id": f"w23-diagnostic-project-create-{uuid4()}",
                          "idempotency_key": f"w23-diagnostic-project-create-{uuid4()}",
                          "rpc_timeout_s": 30.0, "queue_timeout_s": 30.0},
        }
        project_response = authority_daemon.dispatch(create_request)
        write_json(evidence / "project_create_request.json", create_request)
        write_json(evidence / "project_create_response.json", project_response)
        project_receipt = _bind_project_create(project_response, helpers,
                                               server.project / "science", label=label)
        policy = project_receipt["project_record"].get("policy", {}).get("permissions", [])
        if sorted(policy) != ["inspect", "project_write", "trusted_code"]:
            raise RuntimeError("registered diagnostic project permissions exceed or differ from frozen read-only scope")
        project_id = str(project_receipt["project_id"])
        project_receipt.update({"request": create_request,
                                "response": project_response,
                                "permissions": policy,
                                "compute_permission_granted": False})
        write_json(evidence / "project_create_receipt.json", project_receipt)
        authority_daemon.close()
        authority_daemon = None
        after_create = preflight._process_inventory()
        write_json(evidence / "prebirth_after_project_create_inventory.json", after_create)
        if not after_create.get("quiescent"):
            raise RuntimeError("fresh process/listener inventory changed before server birth")
        if _source_files() != freeze["source_files"]:
            raise RuntimeError("frozen source closure changed before server birth")
        try:
            listener = server.start_and_verify_listener()
        finally:
            owner.capture_server()
        server_identity = dict(server.process_identity or {})
        birth_text = server_identity.get("birth")
        if not isinstance(birth_text, str):
            raise RuntimeError("owned COMSOL server birth identity was not captured")
        birth_epoch = preflight._mac_birth_epoch(birth_text)
        deadline_epoch = birth_epoch + MAX_BIRTH_SECONDS
        owner.deadline_epoch = deadline_epoch
        summary.update({"status": "NATIVE_DIAGNOSTIC_RUNNING",
                        "server_birth": birth_text,
                        "server_pid": server.proc.pid if server.proc else None,
                        "server_port": server.port,
                        "birth_epoch": birth_epoch,
                        "deadline_epoch": deadline_epoch})
        write_json(evidence / "server_birth.json", {
            "process_identity": server_identity, "listener": listener,
            "birth_epoch": birth_epoch, "deadline_epoch": deadline_epoch,
            "budget_seconds": MAX_BIRTH_SECONDS,
            "cleanup_reserve_seconds": CLEANUP_RESERVE_SECONDS,
            "source_model_sha256": SOURCE_MODEL_SHA256,
        })
        write_json(evidence / "summary.json", summary)
        if time.time() >= deadline_epoch - CLEANUP_RESERVE_SECONDS:
            raise TimeoutError("birth budget entered cleanup reserve before Worker startup")
        worker_identity_sink: dict[str, Any] = {}
        original_worker_type = server.worker_type
        def retained_worker_type(*args, **kwargs):
            kwargs["process_factory"] = owner.worker_process_factory
            worker = original_worker_type(*args, **kwargs)
            owner.bind_worker(worker)
            return worker
        server.worker_type = retained_worker_type
        try:
            worker_info = science._start_worker_with_journal(server, direct_journal,
                                                             helpers, worker_identity_sink)
        finally:
            server.worker_type = original_worker_type
            owner.capture_worker(worker_identity_sink.get("identity"))
        worker_identity = dict(worker_info.get("worker_process_identity", {}))
        owner.capture_worker(worker_identity)
        runtime = worker_info.get("worker_runtime", {})
        worker_port = runtime.get("port") if isinstance(runtime, Mapping) else None
        if type(worker_port) is not int:
            raise RuntimeError("exact Worker listener port unavailable")
        summary["worker_pid"] = worker_identity.get("pid")
        write_json(evidence / "worker_connection.json", worker_info)
        science._set_runtime_environment(server)
        daemon = helpers["ControlDaemon"](work / "control", worker=server.worker,
                                          project_root=server.project)
        owner.bind_daemon(daemon)
        project_workspace = science._project_science_workspace(project_receipt)
        expected_staged_model = project_workspace / "inputs" / "activity009-coarse-phase0-provisional.mph"
        _set_native_dispatch_guard(
            daemon, managed_dispatch_journal,
            direction_only=direction_only,
            expected_model_path=expected_staged_model,
            expected_project_id=project_id)
        connection = science._dispatch(daemon, "server_connect",
                                       {"host": "127.0.0.1", "port": server.port},
                                       project_id=project_id, timeout_s=30.0)
        write_json(evidence / "managed_connection.json", connection)
        if (connection.get("success") is not True
                or connection.get("data", {}).get("endpoint") != f"127.0.0.1:{server.port}"):
            raise RuntimeError("managed server_connect failed exact task-owned loopback binding")
        owner.capture_attach()
        fixture_manifest = freeze.get("source_files", {}).get("tools/java/NativeW23TEScienceFixture.java")
        expected_fixture_sha = (fixture_manifest.get("sha256")
                                if isinstance(fixture_manifest, Mapping) else None)
        _stage_legacy_fixture_once(project_workspace, evidence,
                                  direction_only=direction_only,
                                  expected_source_sha256=expected_fixture_sha)
        staged_model = _copy_input_into_registered_workspace(SOURCE_MODEL,
                                                             project_workspace, evidence)
        ref, revision, model_tag = _load_saved_model_once(daemon, staged_model,
                                                          helpers, evidence,
                                                          float(deadline_epoch))
        summary.update({"model_tag": model_tag,
                        "model_ref": ref, "model_revision": revision,
                        "project_id": project_id})
        write_json(evidence / "summary.json", summary)
        if freeze.get("read_only_call_plan", {}).get("mode") == "direction_discovery_only":
            def public_read(operation_id: str, arguments: Mapping[str, Any]):
                nonlocal ref, revision
                body, ref, revision = _direction_public_read_once(
                    daemon, operation_id, arguments, ref, revision,
                    project_id=project_id, deadline_epoch=float(deadline_epoch),
                    evidence=evidence, summary=summary)
                return body

            graph = direction.discover_direction_graph(public_read)
            graph_path = evidence / "public_direction_graph.json"
            write_json(graph_path, graph)
            actual_study_rows = science._actual_science_study_run_submissions(
                daemon, preflight)
            study_audit = {
                "rows": actual_study_rows,
                "observed_durable_study_run_submission_count": len(actual_study_rows),
                "required_count": 0,
                "status": "PASS_ZERO_EXPLICIT_STUDY_RUN_SUBMISSIONS"
                         if not actual_study_rows else "FAIL_FORBIDDEN_STUDY_RUN_SUBMISSION_OBSERVED",
                "solver_run_dispatch_count": 0,
                "result_at_points_dispatch_count": 0,
                "internal_solver_execution_count": "UNKNOWN_NOT_INFERRED_FROM_READBACK",
            }
            write_json(evidence / "study_run_submission_audit.json", study_audit)
            if actual_study_rows:
                raise RuntimeError("forbidden Study.run submission was persisted during R21 read-only discovery")
            expected_prefix = freeze.get("read_only_call_plan", {}).get("field_variable_prefix")
            resolved = direction_graph_is_resolved(graph, expected_prefix=expected_prefix)
            summary.update({
                "status": "DIRECTION_GRAPH_CAPTURED_NOT_SCIENCE_ACCEPTED" if resolved
                          else "DIRECTION_GRAPH_INCOMPLETE_OR_AMBIGUOUS_NOT_SCIENCE_ACCEPTED",
                "direction_discovery_status": graph["status"],
                "direction_graph_path": str(graph_path),
                "direction_graph_sha256": sha256_file(graph_path),
                "direction_namespace_status": graph["electromagnetic_waves"]["namespace_correlation"]["status"],
                "port_name_binding_status": graph["port_name_binding_status"],
                "bma_solution_binding_statuses": [row["status"] for row in graph["bma_solution_bindings"]],
                "offline_signed_poynting_report": freeze["offline_direction_report"],
                "direction_discovery_call_count": len(summary.get("direction_discovery_calls", [])),
                "study_run_dispatch_count": 0,
                "study_run_durable_submission_count": 0,
                "solver_run_dispatch_count": 0,
                "result_at_points_attempt_count": 0,
                "result_at_points_verified_completed_count": 0,
                "actual_internal_solver_execution_count": "UNKNOWN_NOT_INFERRED_FROM_READBACK",
                "native_scientific_acceptance": "NOT_ACCEPTED_DIAGNOSTIC_READBACK_ONLY",
            })
            write_json(evidence / "summary.json", summary)
            return summary
        if time.time() >= deadline_epoch - CLEANUP_RESERVE_SECONDS:
            raise TimeoutError("diagnostic work reached cleanup reserve before dataset inventory")
        dataset_response, ref, revision = _query_once(
            daemon, "dataset.list", {}, ref, revision, project_id=project_id,
            deadline_epoch=float(deadline_epoch), evidence=evidence,
            label="dataset_list")
        dataset_body = _response_body(dataset_response)
        dataset_rows = dataset_body.get("datasets")
        if not isinstance(dataset_rows, list):
            raise RuntimeError("dataset.list returned no datasets list")
        production_dataset_rows = [dict(row) for row in dataset_rows if isinstance(row, Mapping)]
        write_json(evidence / "dataset_list_raw.json", {
            "response": dataset_response, "datasets": dataset_rows,
            "source": "production managed dataset.list route",
        })
        for item in dataset_rows:
            if not isinstance(item, Mapping) or str(item.get("type_id", "")).lower() != "solution":
                continue
            tag = item.get("tag")
            if not isinstance(tag, str) or not tag:
                continue
            if time.time() >= deadline_epoch - CLEANUP_RESERVE_SECONDS:
                raise TimeoutError("diagnostic reached cleanup reserve; remaining metadata reads stopped")
            try:
                response, ref, revision = _query_once(
                    daemon, "dataset.solution_indices", {"path": tag}, ref, revision,
                    project_id=project_id, deadline_epoch=float(deadline_epoch),
                    evidence=evidence, label=f"managed_solution_indices_{tag}")
                index_body = _response_body(response)
                solution_index_receipts.append({"dataset": dict(item),
                                                "solution_indices": index_body})
                write_json(evidence / f"managed_solution_indices_{tag}_projection.json", {
                    "dataset_tag": tag, "response": response,
                    "body": index_body,
                    "status": "CAPTURED_RAW_RESPONSE_NOT_SCIENTIFIC_ACCEPTANCE",
                })
            except BaseException as exc:
                write_json(evidence / f"managed_solution_indices_{tag}_exception.json", {
                    "dataset_tag": tag, "status": "ERROR_NO_RETRY",
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                })
                if any(job.get("status") in {"UNKNOWN", "QUEUED", "RUNNING"}
                       for job in preflight._ledger_reconciliation(daemon).get("jobs", [])):
                    unknown_seen = True
                    raise
        if time.time() >= deadline_epoch - CLEANUP_RESERVE_SECONDS:
            raise TimeoutError("diagnostic reached cleanup reserve before Java readback")
        java_readback, ref, revision = science._fixture_call(
            daemon, ref, revision, FIXTURE.name,
            {"phase": "saved_solution_diagnostic"}, helpers,
            deadline_epoch=float(deadline_epoch), evidence=evidence,
            label="saved_solution_diagnostic")
        fixture_terminal = True
        diagnostic_readback = java_readback
        write_json(evidence / "saved_solution_diagnostic_raw.json", {
            "readback": java_readback, "model_ref": ref,
            "revision": revision, "model_tag": model_tag,
            "source_model_sha256": SOURCE_MODEL_SHA256,
            "raw_return_values_preserved": True,
            "scientific_acceptance": "NOT_ACCEPTED_DIAGNOSTIC_READBACK_ONLY",
        })
        validation = validate_fixture_readback(java_readback)
        write_json(evidence / "diagnostic_schema_check.json", validation)
        actual_study_rows = science._actual_science_study_run_submissions(
            daemon, preflight)
        write_json(evidence / "study_run_submission_audit.json", {
            "rows": actual_study_rows,
            "observed_durable_study_run_submission_count": len(actual_study_rows),
            "required_count": 0,
            "status": "PASS_ZERO_EXPLICIT_STUDY_RUN_SUBMISSIONS"
                     if not actual_study_rows else "FAIL_FORBIDDEN_STUDY_RUN_SUBMISSION_OBSERVED",
            "internal_solver_execution_count": "UNKNOWN_NOT_INFERRED_FROM_READBACK",
        })
        if actual_study_rows:
            raise RuntimeError("forbidden Study.run submission was persisted during metadata diagnostic")
        inventory = protocol.normalize_saved_mph_diagnostic_inventory(
            java_readback, production_dataset_rows, solution_index_receipts)
        write_json(evidence / "normalized_saved_mph_inventory.json", inventory)
        main_frequency = protocol.resolve_main_frequency_dataset(
            inventory["study_inventory"], inventory["dataset_inventory"])
        bma_bindings = protocol.resolve_bma_stored_dataset_bindings(
            inventory["study_inventory"], inventory["dataset_inventory"])
        write_json(evidence / "main_frequency_singleton_witness.json", main_frequency)
        write_json(evidence / "bma_study_store_dataset_bindings.json", bma_bindings)
        if ((main_frequency.get("dataset_tag"), main_frequency.get("dataset_solution_property"))
                != ("dset1", "sol1")):
            raise RuntimeError("captured main frequency dataset is not the exact Activity010-proven dset1/sol1")
        bma_by_step = {row["study_step_tag"]: row for row in bma_bindings["bindings"]}
        expected_bma = {
            "bmaInput": ("dset2", "sol2", "1", -10.0),
            "bmaOutput": ("dset3", "sol3", "2", 8.0),
        }
        for step, expected in expected_bma.items():
            row = bma_by_step.get(step)
            actual = ((row or {}).get("dataset_tag"), (row or {}).get("store_solution_reference"),
                      (row or {}).get("port_name"), expected[3])
            if actual != expected:
                raise RuntimeError(f"saved {step} StoreSolution/dataset/port differs from exact Activity010 evidence: {actual!r}")

        decoded_fields: dict[str, dict[int, Mapping[str, Any]]] = {
            "output_x8": {}, "input_xminus10": {}}
        frequency_hz, hz_context, ref, revision = _frequency_readback_once(
            daemon, server, requested_unit="Hz", ref=ref, revision=revision,
            project_id=project_id, deadline_epoch=float(deadline_epoch), evidence=evidence,
            count_witness=main_frequency, summary=summary)
        _append_completed_point_projection(summary, evidence, "physical_frequency_Hz")
        frequency_ghz, ghz_context, ref, revision = _frequency_readback_once(
            daemon, server, requested_unit="GHz", ref=ref, revision=revision,
            project_id=project_id, deadline_epoch=float(deadline_epoch), evidence=evidence,
            count_witness=main_frequency, expected_context=hz_context, summary=summary)
        _append_completed_point_projection(summary, evidence, "physical_frequency_GHz")
        frequency_scaling = protocol.verify_frequency_unit_scaling(
            frequency_hz, frequency_ghz, [hz_context, ghz_context])
        write_json(evidence / "physical_frequency_actual_native_unit_scaling.json", frequency_scaling)
        physical_frequency = protocol.resolve_physical_frequency_point_readback(frequency_hz, main_frequency)
        write_json(evidence / "physical_frequency_native_numerical_witness.json", physical_frequency)

        for plane_id, x_um in (("output_x8", 8.0), ("input_xminus10", -10.0)):
            for sample_count in protocol.SAVED_MPH_QUADRATURE_SAMPLE_COUNTS:
                if time.time() >= deadline_epoch - CLEANUP_RESERVE_SECONDS:
                    raise TimeoutError("saved-field readback reached cleanup reserve; remaining native reads stopped")
                port = "2" if plane_id == "output_x8" else "1"
                expressions = plane_field_expressions(port)
                requested_units = plane_field_units(port)
                label = f"fields_{plane_id}_{sample_count}"
                point_body, ref, revision = _at_points_once(
                    daemon, dataset="dset1", solution="sol1",
                    expressions=expressions,
                    points=_field_points(x_um, sample_count), ref=ref, revision=revision,
                    project_id=project_id, deadline_epoch=float(deadline_epoch),
                    evidence=evidence, label=label, requested_units=requested_units,
                    selection=plane_selection(port), summary=summary)
                decoded = protocol.decode_production_complex_point_readback(
                    point_body, dataset_tag="dset1", solution_tag="sol1",
                    expressions=expressions,
                    expected_points=_field_points(x_um, sample_count),
                    expected_solution_count=1, plane_id=plane_id,
                    expected_count_witness=main_frequency,
                    expected_expression_units=FIELD_UNITS,
                    expected_requested_units=requested_units,
                    require_x_plane_normal=True, expected_selection=plane_selection(port), field_scheme="emw_down")
                decoded_fields[plane_id][sample_count] = decoded
                write_json(evidence / f"{label}_decoded.json", decoded)
                _append_completed_point_projection(summary, evidence, label)

        mode_point_readbacks: dict[str, Mapping[str, Any]] = {}
        for step in ("bmaInput", "bmaOutput"):
            binding = bma_by_step[step]
            dataset_tag = str(binding["dataset_tag"])
            solution_tag = str(binding["store_solution_reference"])
            if time.time() >= deadline_epoch - CLEANUP_RESERVE_SECONDS:
                raise TimeoutError("BMA mode point readback reached cleanup reserve; no further native reads")
            port_name = str(binding["port_name"])
            mode_expressions = protocol.bma_point_expressions(port_name, field_scheme="emw_down")
            actual_mode_units = mode_units(port_name)
            label = f"mode_point_{step}"
            point_body, ref, revision = _at_points_once(
                daemon, dataset=dataset_tag, solution=solution_tag,
                expressions=mode_expressions,
                points=[[MODE_DATASETS[dataset_tag][3], 0.0]],
                ref=ref, revision=revision, project_id=project_id,
                deadline_epoch=float(deadline_epoch), evidence=evidence, label=label,
                requested_units=tuple(actual_mode_units.values()), summary=summary)
            decoded = protocol.decode_production_complex_point_readback(
                point_body, dataset_tag=dataset_tag, solution_tag=solution_tag,
                expressions=mode_expressions,
                expected_points=[[MODE_DATASETS[dataset_tag][3], 0.0]],
                expected_solution_count=1, plane_id=f"{step}_port_center",
                expected_expression_units=actual_mode_units,
                expected_requested_units=tuple(actual_mode_units.values()), scalar_bma_binding=binding, field_scheme="emw_down")
            mode_point_readbacks[step] = decoded
            write_json(evidence / f"{label}_decoded.json", decoded)
            _append_completed_point_projection(summary, evidence, label)
        bma_formula_check = protocol.reconcile_bma_mode_point_readbacks(
            bma_bindings, mode_point_readbacks, physical_frequency, field_scheme="emw_down")
        write_json(evidence / "bma_mode_formula_crosscheck.json", bma_formula_check)
        neff_checks = {}
        for step in ("bmaInput", "bmaOutput"):
            neff_checks[step] = science._validate_mode_index(
                mode_point_readbacks[step], protocol.bma_point_expressions(str(bma_by_step[step]["port_name"]), field_scheme="emw_down")[1], 2e-3)
        write_json(evidence / "saved_bma_neff_checks.json", neff_checks)

        independent = {}
        for sample_count in protocol.SAVED_MPH_QUADRATURE_SAMPLE_COUNTS:
            independent[sample_count] = protocol.independent_integrals(
                decoded_fields["output_x8"][sample_count],
                decoded_fields["input_xminus10"][sample_count], field_scheme="emw_down")
            write_json(evidence / f"saved_field_independent_integrals_{sample_count}.json",
                       independent[sample_count])
        refinement = protocol.compare_quadrature_refinement(
            independent[protocol.SAVED_MPH_SAMPLE_COUNT],
            independent[protocol.SAVED_MPH_REFINED_SAMPLE_COUNT],
            sample_counts=protocol.SAVED_MPH_QUADRATURE_SAMPLE_COUNTS)
        write_json(evidence / "saved_field_quadrature_refinement.json", refinement)
        summary.update({"status": "SAVED_MPH_READBACK_GATES_CAPTURED_NOT_FORMAL_SCIENCE_ACCEPTED",
                        "fixture_status": java_readback.get("diagnostic_phase"),
                        "diagnostic_schema_status": validation["status"],
                        "main_frequency_witness_status": main_frequency["status"],
                        "bma_binding_status": bma_bindings["status"],
                        "bma_formula_crosscheck_status": bma_formula_check["status"],
                        "result_at_points_attempt_count": len(summary["point_read_attempts"]),
                        "result_at_points_verified_completed_count": len(summary["native_calls"]),
                        "study_run_dispatch_count": 0,
                        "solver_run_dispatch_count": 0,
                        "study_run_durable_submission_count": 0,
                        "actual_internal_solver_execution_count": "UNKNOWN_NOT_INFERRED_FROM_READBACK",
                        "native_scientific_acceptance": "NOT_ACCEPTED_SAVED_ARTIFACT_READBACK_ONLY"})
    except BaseException as exc:
        owner.cause(exc)
        summary.update({"status": "FAIL_OR_INCOMPLETE",
                        "error": f"{type(exc).__name__}: {exc}",
                        "traceback": traceback.format_exc(),
                        "native_scientific_acceptance": "NOT_ACCEPTED_DIAGNOSTIC_READBACK_ONLY"})
        science.append_jsonl(evidence / "events.jsonl", {
            "event": "diagnostic_failed_or_incomplete", "error": summary["error"],
            "worker_request_terminal": fixture_terminal})
    finally:
        managed_reconciliation: dict[str, Any] | None = None
        direct_reconciliation: dict[str, Any] | None = None
        cleanup_gates: dict[str, Any] | None = None
        cleanup_authorization: dict[str, Any] | None = None
        idle: dict[str, Any] | None = None
        worker_health: Any = None
        disconnect_receipt: dict[str, Any] = {"status": "NOT_ATTEMPTED"}
        cleanup: dict[str, Any] = {"status": "NOT_STARTED"}
        if daemon is not None:
            try:
                endpoint_gate = preflight._task_endpoint_identity_gate(
                    server, server_identity=server_identity,
                    worker_identity=worker_identity, worker_port=worker_port,
                    process_snapshot=helpers["_process_snapshot"],
                    require_worker_peer=True)
                write_json(evidence / "cleanup_endpoint_identity_gate.json", endpoint_gate)
                if endpoint_gate.get("safe") is True:
                    worker_health = server.worker.health(timeout_s=5.0)
                write_json(evidence / "cleanup_worker_health_before_disconnect.json", {
                    "expected_endpoint": f"127.0.0.1:{server.port}",
                    "response": worker_health,
                    "read_only_health_rpc": True,
                })
                managed_reconciliation = preflight._ledger_reconciliation(daemon)
                managed_reconciliation["sqlite_snapshot"] = preflight._backup_operation_store(
                    daemon, evidence / "private-control-store" / "raw_operations.sqlite3")
                write_json(evidence / "managed_ledger_reconciliation.json", managed_reconciliation)
                direct_reconciliation = helpers["reconcile_direct_rpc_events"](
                    science.read_jsonl(direct_journal.path))
                write_json(evidence / "direct_rpc_reconciliation_before_disconnect.json", direct_reconciliation)
                cleanup_gates = helpers["combine_cleanup_gates"](managed_reconciliation,
                                                                  direct_reconciliation)
                idle = preflight._daemon_idle_proof(
                    daemon, worker_health=worker_health,
                    expected_endpoint=f"127.0.0.1:{server.port}" if server and server.port else None)
                cleanup_authorization = science._cleanup_authorization(
                    project_receipt, project_id, preflight,
                    managed_reconciliation, cleanup_gates, idle)
                write_json(evidence / "cleanup_gates_before_disconnect.json", {
                    "combined": cleanup_gates, "daemon_idle_proof": idle,
                    "unknown_preserved": True,
                })
                write_json(evidence / "cleanup_project_scope_before_disconnect.json", cleanup_authorization)
            except BaseException as exc:
                summary["cleanup_reconciliation_error"] = f"{type(exc).__name__}: {exc}"
                cleanup_authorization = {"safe_for_owned_cleanup": False}
        if server is not None and server.proc is not None:
            endpoint_gate = json.loads((evidence / "cleanup_endpoint_identity_gate.json").read_text()) if (evidence / "cleanup_endpoint_identity_gate.json").is_file() else {}
            safe = bool(cleanup_authorization
                        and cleanup_authorization.get("safe_for_owned_cleanup") is True
                        and endpoint_gate.get("safe") is True
                        and idle and idle.get("idle") is True
                        and idle.get("worker_health_quiescent") is True)
            if safe and server.worker is not None:
                try:
                    health_after: Any = None
                    request_id = f"w23-diagnostic-cleanup-disconnect-{uuid4()}"
                    disconnect = server.worker.client().disconnect(
                        request_id=request_id, rpc_timeout_s=10.0)
                    health_after = server.worker.health(timeout_s=5.0)
                    disconnect_ok = science._worker_disconnect_verified(
                        disconnect, worker_health, health_after)
                    disconnect_receipt = {
                        "status": "PASS_WORKER_DISCONNECTED_QUIESCENT" if disconnect_ok else "BLOCKED_WORKER_DISCONNECT_NOT_CONFIRMED",
                        "request_id": request_id,
                        "decoded_remote_client_result": disconnect,
                        "health_before": worker_health, "health_after": health_after,
                        "durable_domain_outcome_rewritten": False,
                    }
                    write_json(evidence / "cleanup_worker_disconnect.json", disconnect_receipt)
                    managed_reconciliation = preflight._ledger_reconciliation(daemon)
                    managed_reconciliation["sqlite_snapshot"] = preflight._backup_operation_store(
                        daemon, evidence / "private-control-store" / "raw_operations_post_disconnect.sqlite3")
                    direct_reconciliation = helpers["reconcile_direct_rpc_events"](
                        science.read_jsonl(direct_journal.path))
                    cleanup_gates = helpers["combine_cleanup_gates"](managed_reconciliation,
                                                                      direct_reconciliation)
                    cleanup_authorization = science._cleanup_authorization(
                        project_receipt, project_id, preflight,
                        managed_reconciliation, cleanup_gates, idle)
                    write_json(evidence / "direct_rpc_reconciliation.json", direct_reconciliation)
                    write_json(evidence / "cleanup_gates.json", {
                        "combined": cleanup_gates,
                        "daemon_idle_proof_before_disconnect": idle,
                        "worker_disconnect": disconnect_receipt,
                        "unknown_preserved": True,
                    })
                    write_json(evidence / "cleanup_project_scope.json", cleanup_authorization)
                    if disconnect_ok and cleanup_authorization.get("safe_for_owned_cleanup") is True:
                        cleanup = preflight._exact_owned_cleanup(
                            server, server_identity=server_identity,
                            worker_identity=worker_identity,
                            worker_port=worker_port,
                            process_snapshot=helpers["_process_snapshot"],
                            reconciliation=managed_reconciliation,
                            evidence=evidence, require_fixture_terminal=False)
                    else:
                        cleanup = {"status": "LEFT_RUNNING_ACTIVE_OR_UNKNOWN",
                                   "reason": "Worker disconnect/health/ledger proof did not pass",
                                   "durable_unknown_preserved": True}
                        owner.fence(cleanup)
                        write_json(evidence / "exact_owned_cleanup_receipt.json", cleanup)
                except BaseException as exc:
                    owner.cause(exc)
                    cleanup = {"status": "CLEANUP_UNVERIFIED",
                               "error": f"{type(exc).__name__}: {exc}",
                               "durable_unknown_preserved": True}
                    owner.fence(cleanup)
                    write_json(evidence / "exact_owned_cleanup_receipt.json", cleanup)
            else:
                cleanup = {"status": "LEFT_RUNNING_ACTIVE_OR_UNKNOWN",
                           "server_pid": server.proc.pid, "server_port": server.port,
                           "project_and_idle_gate": cleanup_authorization,
                           "endpoint_identity_gate": endpoint_gate,
                           "durable_unknown_preserved": True,
                           "action": "No signal; exact ownership, terminality, or idle proof is incomplete"}
                owner.fence(cleanup)
                write_json(evidence / "exact_owned_cleanup_receipt.json", cleanup)
            if cleanup.get("status") in {"LEFT_RUNNING_ACTIVE_OR_UNKNOWN", "CLEANUP_UNVERIFIED"}:
                owner.fence(cleanup)
            if not owner.fenced:
                try:
                    postinventory = preflight._process_inventory()
                    write_json(evidence / "postcleanup_inventory.json", postinventory)
                except BaseException as exc:
                    write_json(evidence / "postcleanup_inventory_error.json", {
                        "error": f"{type(exc).__name__}: {exc}"})
                    summary["status"] = "FAIL_OR_INCOMPLETE"
        if daemon is not None and (server is None or server.proc is None
                                   or cleanup.get("status") == "CLEANUP_VERIFIED_EXACT_OWNED_PIDS_AND_PORTS_ABSENT"):
            try:
                daemon.close()
            except BaseException as exc:
                summary["daemon_close_error"] = f"{type(exc).__name__}: {exc}"
                summary["status"] = "FAIL_OR_INCOMPLETE"
        if authority_daemon is not None and not owner.fenced:
            try:
                authority_daemon.close()
            except BaseException as exc:
                summary["project_create_daemon_close_error"] = f"{type(exc).__name__}: {exc}"
        summary["cleanup_status"] = cleanup.get("status")
        summary["managed_ledger_status"] = managed_reconciliation.get("status") if managed_reconciliation else None
        summary["managed_ledger_safe_for_owned_cleanup"] = managed_reconciliation.get("safe_for_owned_cleanup") if managed_reconciliation else None
        summary["direct_rpc_reconciliation_status"] = direct_reconciliation.get("status") if direct_reconciliation else None
        summary["cleanup_gates"] = cleanup_gates
        summary["cleanup_authorization"] = cleanup_authorization
        summary["worker_disconnect"] = disconnect_receipt
        attempts = summary.get("point_read_attempts")
        calls = summary.get("native_calls")
        attempts = attempts if isinstance(attempts, list) else []
        calls = calls if isinstance(calls, list) else []
        summary["result_at_points_attempt_count"] = len(attempts)
        summary["result_at_points_verified_completed_count"] = len(calls)
        summary["result_at_points_unknown_or_nonterminal_count"] = sum(
            1 for row in attempts if isinstance(row, Mapping) and row.get("status") in {
                "UNKNOWN", "QUEUED", "RUNNING", "DISPATCH_INVOKED",
                "DISPATCH_INVOKED_NO_RESPONSE", "DISPATCH_RESPONSE_RECEIVED",
                "TERMINAL_STATUS_UNAVAILABLE"})
        summary["observed_internal_solver_count"] = "UNKNOWN_NOT_INFERRED_FROM_READBACK"
        summary["explicit_study_or_solver_execution_allowed"] = False
        summary["native_scientific_acceptance"] = "NOT_ACCEPTED_DIAGNOSTIC_READBACK_ONLY"
        if birth_epoch is not None:
            finished = time.time()
            elapsed = max(0.0, finished - birth_epoch)
            summary["birth_inclusive_budget"] = {
                "birth_epoch": birth_epoch, "deadline_epoch": deadline_epoch,
                "finished_epoch_includes_cleanup": finished,
                "elapsed_seconds_includes_cleanup": elapsed,
                "max_seconds": MAX_BIRTH_SECONDS,
                "cleanup_reserve_seconds": CLEANUP_RESERVE_SECONDS,
                "budget_exceeded": elapsed > MAX_BIRTH_SECONDS,
                "deadline_was_not_reset": True,
            }
            if owner.fenced:
                summary["birth_inclusive_budget"].update({"finished_epoch_includes_cleanup": None,
                    "elapsed_seconds_includes_cleanup": None, "elapsed_seconds_at_hold": elapsed,
                    "standard_cleanup_complete": False, "child_lifetime_resolved": False,
                    "native_lifetime_within_budget": "UNRESOLVED_WHILE_ORIGINAL_CHILDREN_ALIVE"})
            if elapsed > MAX_BIRTH_SECONDS:
                summary["status"] = "FAIL_OR_INCOMPLETE_OVER_BUDGET"
        summary["finished_at_utc"] = None if owner.fenced else utc_now()
        write_json(evidence / "summary.json", summary)
    return summary


class _DurableCliTee:
    """Write CLI diagnostics to a durable raw text stream and original console."""
    def __init__(self, console: Any, durable: Any):
        self.console, self.durable = console, durable

    def write(self, text: str) -> int:
        self.durable.write(text)
        self.durable.flush()
        return self.console.write(text)

    def flush(self) -> None:
        self.durable.flush()
        self.console.flush()


def _execute_with_cli_capture(args: Any) -> int:
    # Keep CLI streams outside execute's newly-created evidence directory.
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    stdout_path = args.evidence.with_name(args.evidence.name + ".cli.stdout.bin")
    stderr_path = args.evidence.with_name(args.evidence.name + ".cli.stderr.bin")
    with stdout_path.open("x", encoding="utf-8") as stdout, stderr_path.open("x", encoding="utf-8") as stderr:
        with contextlib.redirect_stdout(_DurableCliTee(sys.stdout, stdout)), contextlib.redirect_stderr(_DurableCliTee(sys.stderr, stderr)):
            try:
                summary = execute(args.freeze, args.freeze_sha256, args.approval,
                                  args.saved_model, args.work, args.evidence)
                print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
                return 0 if summary.get("status") == "SAVED_MPH_READBACK_GATES_CAPTURED_NOT_FORMAL_SCIENCE_ACCEPTED" and summary.get("cleanup_status") == "CLEANUP_VERIFIED_EXACT_OWNED_PIDS_AND_PORTS_ABSENT" else 2
            except BaseException:
                traceback.print_exc()
                raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--evidence", type=Path, required=True)
    prepare_parser.add_argument("--runtime-authority", type=Path)
    prepare_parser.add_argument("--work", type=Path, required=True)
    prepare_parser.add_argument("--execution-evidence", type=Path, required=True)
    execute_parser = commands.add_parser("execute")
    execute_parser.add_argument("--freeze", type=Path, required=True)
    execute_parser.add_argument("--freeze-sha256", required=True)
    execute_parser.add_argument("--approval", type=Path, required=True)
    execute_parser.add_argument("--saved-model", type=Path, required=True)
    execute_parser.add_argument("--work", type=Path, required=True)
    execute_parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        receipt = prepare(args.evidence, work=args.work,
                          execution_evidence=args.execution_evidence, runtime_authority=args.runtime_authority)
        print(json.dumps(receipt, ensure_ascii=False, indent=2, default=str))
        return 0
    return _execute_with_cli_capture(args)


if __name__ == "__main__":
    raise SystemExit(main())
