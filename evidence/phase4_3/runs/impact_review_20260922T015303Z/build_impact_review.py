#!/usr/bin/env python3
"""Write a read-only W17 cross-layer impact review for this evidence run.

The report deliberately records static observations and rerun requirements.  It
does not call COMSOL, start a worker, or turn source inspection into a runtime
acceptance claim.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path.cwd().resolve()
RUN = ROOT / "evidence/phase4_3/runs/impact_review_20260922T015303Z"
REPORT = RUN / "impact_review.json"
TARGETED_RUN = ROOT / "evidence/phase4_3/runs/artifact_regression_20260922T014543Z"
TARGETED_MANIFEST = TARGETED_RUN / "manifest.json"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def command_record(argv: list[str]) -> dict[str, Any]:
    completed = subprocess.run(argv, cwd=ROOT, text=True, capture_output=True, check=False)
    return {
        "command": " ".join(argv),
        "cwd": str(ROOT),
        "interpreter": argv[0],
        "exit_code": completed.returncode,
        "stdout_sha256": sha256_bytes(completed.stdout.encode()),
        "stderr_sha256": sha256_bytes(completed.stderr.encode()),
        "stdout": completed.stdout if argv[:2] in (["git", "rev-parse"], ["git", "branch"]) else "bounded-hash-only",
    }


def source_entry(relative: str, observations: list[str], risks: list[str], tests: list[str], anchors: list[str]) -> dict[str, Any]:
    path = ROOT / relative
    status = subprocess.run(["git", "status", "--porcelain", "--", relative], cwd=ROOT, text=True,
                            capture_output=True, check=False).stdout.strip()
    return {
        "path": relative,
        "absolute_path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "modified_since_HEAD": bool(status),
        "git_status_code": status[:2] if status else "",
        "anchors": anchors,
        "observations": observations,
        "risks_or_limits": risks,
        "tests_to_rerun": tests,
        "semantic_status": "STATIC_REVIEW_ONLY",
    }


def main() -> int:
    head = command_record(["git", "rev-parse", "HEAD"])
    branch = command_record(["git", "branch", "--show-current"])
    status = command_record(["git", "status", "--short"])
    now = datetime.now(timezone.utc).isoformat()

    module_specs: list[tuple[str, list[str], list[str], list[str], list[str]]] = [
        (
            "comsol_mcp/_g3_ops.py",
            [
                "Imports the W13-W17 operation modules and records missing-module ImportError entries in SKIPPED_MODULES.",
                "EFFECTS prefer the catalog, then the explicit fallback table, then fail-closed WRITE; every non-READ effect is in REQUIRES_ISOLATION.",
                "dispatch only calls a registered callable; schema validation and witness/final-state decisions are owned by the registry/backend/service layers.",
            ],
            [
                "A broad ImportError catch can hide a broken module as a skipped workstream; the published inventory must be checked for skipped modules and exact implementation coverage.",
                "Changing the catalog or a domain module changes effect and isolation routing even when this file is unchanged.",
            ],
            [
                "tests/test_g3_wiring.py::test_every_published_operation_is_implemented_and_classified",
                "tests/test_g3_wiring.py::test_effects_come_from_the_catalogue_and_translate_everywhere",
                "tests/test_g3_wiring.py::test_requires_isolation_is_exactly_the_non_read_surface",
                "tests/test_g3_wiring.py::test_probe_operations_are_reachable_from_host_dispatch",
                "tests/test_g3_w17.py::test_w17_operations_catalog_effects_and_dispatch",
            ],
            ["_load_modules:105-134", "_effect_sources:141-167", "dispatch:182-188", "describe:191-202"],
        ),
        (
            "comsol_mcp/_g2_registry.py",
            [
                "The packaged action catalog is authoritative when present; checkout use compares it byte-for-byte with the reviewed docs catalog and fails closed on drift.",
                "validate_call applies the catalog schema plus effective managed-wire additions such as object ModelRef and W17 export overwrite.",
                "registry_manifest exposes catalog path/hash and explicitly does not imply engine acceptance.",
            ],
            [
                "is_implemented catches all exceptions while importing _g3_ops and returns false, which can conceal a real import regression unless SKIPPED_MODULES and wiring are inspected.",
                "Every catalog $ref to common.schema.json must resolve in an installed wheel with the checkout absent.",
            ],
            [
                "tests/test_g2_registry_packaging.py::test_packaged_catalog_matches_reviewed_source_and_is_authoritative",
                "tests/test_g2_registry_packaging.py::test_catalog_selector_rejects_drift_and_prefers_package",
                "tests/test_g2_registry_packaging.py::test_catalog_selector_has_only_legacy_workspace_fallback",
                "tests/test_g2_registry_packaging.py::test_pyproject_declares_catalog_as_package_data",
                "tests/test_g2_registry_packaging.py::test_registry_imports_from_isolated_package_tree_without_checkout",
            ],
            ["_select_catalog_path:25-57", "_effective_input_schema:303-363", "_catalog_entries:366-413", "registry_manifest:504-530", "validate_call:532-572"],
        ),
        (
            "comsol_mcp/_managed_backend.py",
            [
                "invoke routes G2 and G3 through the bound ModelRef, ExecutionService, operation effect, and per-job request/session identity.",
                "_dispatch_with_witness returns a clean validation refusal only when the stage is explicitly validation and the witness saw no mutation; an unproven exception becomes EXECUTION_STATE_UNKNOWN.",
                "The backend persists SessionLedger/runtime state and exposes job/status/log/result/reconcile paths through the control plane.",
            ],
            [
                "A live failure after any engine call must preserve the witness, cause, job id, dirty/revision state, and no-replay rule across persistence and MCP job queries.",
                "The G3 operation import fallback can make the surface appear absent; static success cannot establish a live worker route.",
            ],
            [
                "tests/test_managed_backend.py::test_explicit_reconnect_starts_existing_worker_and_invalidates_old_epoch",
                "tests/test_managed_backend.py::test_visible_workflow_start_uses_managed_connection_not_legacy_reconnect",
                "tests/test_g3_phase4_request_chain.py::test_an_unfinished_job_is_resolved_through_its_own_query_first",
                "tests/test_g3_phase4_request_chain.py::test_exactly_one_recorded_new_plan_and_only_for_a_proved_not_executed_request",
                "tests/test_g3_domain_outcome.py::test_an_unknown_domain_operation_blocks_later_work_and_is_never_replayed",
            ],
            ["_refusal_envelope:68-95", "connect/persist/invoke:159-434", "_dispatch_with_witness:796-865", "_invoke_g3_model:867-900"],
        ),
        (
            "comsol_mcp/_execution_service.py",
            [
                "execute_legacy performs identity/permission/path preflight, takes a pre-dispatch snapshot, opens a write ticket for non-inspect effects, and performs a required post-dispatch snapshot.",
                "Callback exceptions after the ticket finish as unknown/changed unless they already carry the structured unknown contract; final_state and classify_envelope decide the published outcome.",
                "Unknown or partial read outcomes freeze the managed model state rather than publishing a surface success.",
            ],
            [
                "The contract depends on the live adapter fingerprint and external-event counter; offline adapters cannot prove COMSOL state or fresh-worker reopen.",
                "Ledger finish/state emission failures must remain visible as unresolved state and must not be overwritten by secondary bookkeeping errors.",
            ],
            [
                "tests/test_execution_service.py::test_service_conflict_prevents_callback_before_write",
                "tests/test_execution_service.py::test_service_reconcile_invalidates_old_revision_then_executes",
                "tests/test_execution_service.py::test_post_snapshot_failure_releases_ticket_and_marks_unknown",
                "tests/test_execution_state_propagation.py::test_execution_service_marks_wrapped_unknown_dirty_and_blocks_retry",
                "tests/test_g3_domain_outcome.py::test_a_failed_domain_operation_reaches_the_job_as_a_failure",
            ],
            ["execute_legacy:85-234", "_freeze_after_unknown_read:256-275", "_snapshot/_metadata:278-307"],
        ),
        (
            "comsol_mcp/_domain_outcome.py",
            [
                "DispatchWitness records engine method calls and classifies mutation using explicit setter/prefix/known-read rules; the W17 native metadata accessors are in the read allow-list.",
                "classify gives precedence to unknown state or cleanup_failed, then explicit failure/partial evidence, and only then a clean refusal or success; verification is a separate axis.",
                "domain_envelope/classify_envelope/final_state merge nested evidence so an outer success cannot clear a true unknown or cleanup signal.",
            ],
            [
                "The read allow-list is a security boundary: an accessor misclassified as read can undermine prewrite refusal evidence, while a mutator misclassified as unknown/read changes freeze behavior. Native live traces are required.",
                "A cleanup flag, worker failure, or unrecognised status must survive all envelope layers and job/ledger serialization.",
            ],
            [
                "tests/test_g3_domain_outcome.py::test_a_plain_domain_failure_dictionary_is_not_a_success",
                "tests/test_g3_domain_outcome.py::test_02_a_nested_unknown_beats_a_surface_success",
                "tests/test_g3_domain_outcome.py::test_05_a_proven_prewrite_refusal_keeps_its_code_and_leaves_the_revision_clean",
                "tests/test_g3_domain_outcome.py::test_06_a_post_write_readback_failure_is_partial_and_dirty",
                "tests/test_g3_domain_outcome.py::test_the_worker_funnel_feeds_the_witness_with_real_method_names",
                "tests/test_g3_domain_outcome.py::test_an_unknown_domain_operation_blocks_later_work_and_is_never_replayed",
            ],
            ["KNOWN_READ_METHODS/is_mutation_call:194-264", "DispatchWitness:270-337", "classify:551-714", "domain_envelope:767-793", "final_state:910-930"],
        ),
        (
            "comsol_mcp/_java_worker.py",
            [
                "PersistentJavaWorker uses a private authenticated loopback endpoint, records request events, and keeps the original request id queryable after RPC timeout.",
                "_request marks a timed-out request UNKNOWN; status/reconcile must use the same request id. RemoteJava records the real engine method into the active witness before submit.",
                "_decode_reply preserves structured worker failure and execution_state_unknown instead of converting it to a normal local exception.",
            ],
            [
                "A timeout does not terminate the child and may leave an engine operation running; only same-key status/reconciliation can establish its terminal state.",
                "Endpoint files and request event output must never expose authentication tokens or private server identity in public acceptance evidence.",
            ],
            [
                "tests/test_java_worker.py::test_java_worker_is_loopback_authenticated_and_reports_health",
                "tests/test_java_worker.py::test_java_worker_keeps_original_request_queryable_after_nonblocking_submit",
                "tests/test_java_worker.py::test_same_request_id_with_different_body_is_rejected_without_replay",
                "tests/test_java_worker.py::test_operation_context_publishes_request_linkage_and_redacts_credentials",
                "tests/test_java_worker.py::test_two_workers_cannot_own_the_same_global_endpoint_lock",
                "tests/test_execution_state_propagation.py::test_decode_reply_preserves_structured_worker_failure_and_message",
            ],
            ["PersistentJavaWorker._request:273-299", "submit/status:342-380", "RemoteJava._call:474-491", "_decode_reply/_redact:581-611"],
        ),
        (
            "comsol_mcp/worker_java/PersistentComsolWorker.java",
            [
                "The Java server is a serial loopback worker with a method allow-list, generation checks, authenticated endpoint, and request state QUEUED/RUNNING/SUCCEEDED/FAILED.",
                "Same request_id with the same body returns the prior snapshot; a different semantic body returns IDEMPOTENCY_KEY_CONFLICT. The request hash excludes request_id and queue_timeout_ms.",
                "Queue timeout can return an active snapshot; generic engine Throwable is encoded with execution_state_unknown=true. W17 native metadata methods and genResult are now allow-listed.",
            ],
            [
                "Java source inspection and fixture reflection do not prove compilation against the installed COMSOL API or execution on a fresh worker.",
                "The exact request, timeout, status, failure, generation, and cleanup traces need one real public-MCP serial run after source freeze; retain endpoint/token details privately.",
            ],
            [
                "tests/test_java_worker.py::test_java_worker_is_loopback_authenticated_and_reports_health",
                "tests/test_java_worker.py::test_java_worker_keeps_original_request_queryable_after_nonblocking_submit",
                "tests/test_java_worker.py::test_same_request_id_with_different_body_is_rejected_without_replay",
                "tests/test_java_worker.py::test_java_worker_reconnects_existing_endpoint_without_spawning_or_replaying",
                "fresh javac plus fresh-worker public MCP protocol run (NOT_RUN in this audit)",
            ],
            ["server/endpoint:124-270", "call/allow-list:498-505", "reflection selftests:709-745", "hashRequest/RequestState:926-936"],
        ),
        (
            "comsol_mcp/_g3_results.py",
            [
                "W17 dataset/result operations resolve typed NodePath values and native solution axes/complex metadata; field export routes through the artifact contract.",
                "Current worktree source hash drifted after the latest targeted artifact run, so that 28-pass result is valid only for its manifest-matched _g3_results snapshot.",
            ],
            [
                "The current file is being changed by another agent; full W17 semantics, especially NodePath shape and M1 standard-deviation behavior, must be rerun after root freeze.",
                "Static fixture tables or finite values do not prove a real Chain-A solved model or fresh-worker result metadata.",
            ],
            [
                "tests/test_g3_w17.py (all W17 tests after freeze)",
                "tests/test_g3_w17.py::test_field_export_large_data_and_chunk_verification",
                "tests/test_g33_export_cases.py",
                "tests/test_g3_artifact_hardening.py",
                "tests/test_g3_d17_artifact_service.py",
            ],
            ["dataset path/solution indices around 1500-1830", "result evaluation/selection around 2000-2350", "field export and OPERATION_ARGUMENTS near 2500+"],
        ),
        (
            "comsol_mcp/_artifact_store.py",
            [
                "Artifact publication requires an explicit trusted project root, rejects symlink/escape paths, defaults to no overwrite, writes atomically, and retains whole-file/chunk hashes.",
                "Publish rejects failed/UNKNOWN/cleanup_failed/empty or nonfinite output; artifact.read is root-bound and bounded by offset/length with expected whole hash.",
            ],
            [
                "Targeted offline contract coverage passed for the manifest-matched source, but no live COMSOL result was published or read in this audit.",
                "The caller must continue passing the worker-bound project root and the final DomainOutcome; a class name or successful serializer call is not proof that evaluation was safe.",
            ],
            [
                "tests/test_g3_artifact_hardening.py",
                "tests/test_g3_d17_artifact_service.py",
                "tests/test_g33_export_cases.py",
                "tests/test_g3_w17.py::test_field_export_large_data_and_chunk_verification",
                "public MCP C12/C13/F07-F09 run via tools/g33_export_cases.py (NOT_RUN here)",
            ],
            ["ArtifactStore root/path validation and publication methods", "chunk/hash/read methods", "field-export adapter contract"],
        ),
    ]
    modules = [source_entry(*spec) for spec in module_specs]

    catalog = ROOT / "comsol_mcp/data/g2/02_ACTION_CATALOG.json"
    reviewed_catalog = ROOT / "docs/comsol_mcp_design_v1/02_ACTION_CATALOG.json"
    common_schema = ROOT / "comsol_mcp/data/g2/common.schema.json"
    reviewed_schema = ROOT / "docs/comsol_mcp_design_v1/common.schema.json"
    catalog_equal = catalog.read_bytes() == reviewed_catalog.read_bytes()
    schema_equal = common_schema.read_bytes() == reviewed_schema.read_bytes()

    targeted_manifest: dict[str, Any] = {}
    if TARGETED_MANIFEST.is_file():
        targeted_manifest = json.loads(TARGETED_MANIFEST.read_text(encoding="utf-8"))
    targeted_drift: list[dict[str, Any]] = []
    for relative, old in targeted_manifest.get("sources", {}).items():
        path = ROOT / relative
        current = {"sha256": sha256_file(path), "bytes": path.stat().st_size} if path.is_file() else {"missing": True}
        if current != {k: old.get(k) for k in ("sha256", "bytes") if k in old}:
            targeted_drift.append({"path": relative, "manifest": old, "current": current})

    report: dict[str, Any] = {
        "schema": "comsol-mcp.g3.3.impact-review.v1",
        "captured_at_utc": now,
        "repository": str(ROOT),
        "head": head.get("stdout", "").strip(),
        "branch": branch.get("stdout", "").strip(),
        "scope": "W17 cross-layer static impact review and rerun plan during source-freeze wait",
        "authorization_boundary": {
            "source_changes": "none",
            "comsol_started": False,
            "shared_processes_touched": False,
            "old_pass_claims": "not promoted; prior runs are only identified as rerun inputs",
        },
        "semantic_status": "UNVERIFIED_RUNTIME",
        "commands": [
            head,
            branch,
            status,
            {
                "command": f"{sys.executable} {RUN.relative_to(ROOT)}/build_impact_review.py",
                "cwd": str(ROOT),
                "interpreter": sys.executable,
                "exit_code": 0,
                "stdout": "impact_review.json written",
            },
        ],
        "source_manifest": modules + [
            source_entry("comsol_mcp/data/g2/02_ACTION_CATALOG.json", ["272 catalog operations; W17 result.field_export includes optional overwrite=false; artifact.read is published as READ."], ["Catalog drift blocks import and changes wire validation."], ["tests/test_g2_registry_packaging.py", "tests/test_g3_wiring.py"], ["catalog operations list"]),
            source_entry("comsol_mcp/data/g2/common.schema.json", ["Defines NodePath, EvaluationSpec, SolutionSpec, and typed result inputs referenced by catalog $refs."], ["Wheel must carry the referenced resource; hash equality alone does not validate every ref in an installed environment."], ["tests/test_g2_registry_packaging.py::test_registry_imports_from_isolated_package_tree_without_checkout", "schema/resource validation in post-freeze wheel run"], ["$defs:NodePath/EvaluationSpec/SolutionSpec"]),
            source_entry("docs/comsol_mcp_design_v1/02_ACTION_CATALOG.json", ["Reviewed catalog copy currently byte-equal to package catalog."], ["Any later edit must update package and reviewed copies together."], ["tests/test_g2_registry_packaging.py::test_packaged_catalog_matches_reviewed_source_and_is_authoritative"], ["reviewed catalog"]),
            source_entry("docs/comsol_mcp_design_v1/common.schema.json", ["Reviewed schema copy currently byte-equal to package schema."], ["Keep package/docs schema copies synchronized if either changes."], ["package resource/schema validation"], ["reviewed schema"]),
            source_entry("pyproject.toml", ["setuptools package-data includes worker_java/*.java and comsol_mcp/data/g2/*.json; wheel source does not include the docs tree."], ["Wheel validation must run from an extracted external wheel with checkout absent and verify all runtime resource references."], ["tests/test_g2_registry_packaging.py::test_pyproject_declares_catalog_as_package_data", "post-freeze external wheel install"], ["tool.setuptools.package-data:50"]),
        ],
        "resource_sync": {
            "catalog_package_sha256": sha256_file(catalog),
            "catalog_reviewed_sha256": sha256_file(reviewed_catalog),
            "catalog_byte_equal": catalog_equal,
            "common_schema_package_sha256": sha256_file(common_schema),
            "common_schema_reviewed_sha256": sha256_file(reviewed_schema),
            "common_schema_byte_equal": schema_equal,
            "catalog_operation_count": 272,
            "selected_operations": ["dataset.list", "dataset.create", "dataset.update", "dataset.remove", "dataset.solution_indices", "result.evaluate", "result.at_points", "result.numerical_manage", "result.field_export", "artifact.read"],
        },
        "latest_targeted_artifact_run": {
            "path": str(TARGETED_RUN),
            "command_file": str(TARGETED_RUN / "command"),
            "exit_code_file": str(TARGETED_RUN / "exit_code"),
            "observed_result": "28 passed in 0.76s, rc=0",
            "source_manifest_path": str(TARGETED_MANIFEST),
            "current_source_drift": targeted_drift,
            "acceptance_use": "reference only until every manifest source hash matches; any drift invalidates that run for the changed module",
        },
        "latest_public_export_observation": {
            "path": str(ROOT / "evidence/phase4_3/runs/public_export_20260922T0200Z"),
            "ledger": str(ROOT / "evidence/phase4_3/runs/public_export_20260922T0200Z/g33_export_cases.json"),
            "correction": str(ROOT / "evidence/phase4_3/runs/public_export_20260922T0200Z/correction.json"),
            "run_status": str(ROOT / "evidence/phase4_3/runs/public_export_20260922T0200Z/run_status.json"),
            "status": "FAIL",
            "case": "json-success",
            "error_code": "FIELD_ARRAY_SHAPE_MISMATCH",
            "observed_message": "coordinate count for point does not match axis length",
            "published_result": "no artifact; response was wrapped as EXECUTION_STATE_UNKNOWN because the callback did not prove pre-dispatch",
            "runner_exit_code": 0,
            "runner_exit_code_interpretation": "defect: helper summary had 0 PASS and 1 BLOCKED, but the runner did not assert the summary; correction.json preserves FAIL",
            "required_followup": [
                "fix the core FieldArray axis/coordinate shape defect before rerunning",
                "make the helper query job_status, job_result, and job_reconcile for any UNKNOWN response before returning",
                "make the runner exit nonzero or write FAIL when overall is not PASS",
                "keep the preserved original run and correction ledger append-only",
            ],
            "acceptance_eligible": False,
        },
        "helper_regression_after_unknown_query_fix": {
            "command": str(RUN / "helper_regression.command"),
            "cwd": str(RUN / "helper_regression.cwd"),
            "interpreter": str(RUN / "helper_regression.interpreter"),
            "exit_code": 0,
            "stdout": str(RUN / "helper_regression.stdout"),
            "observed_result": "2 passed in 0.19s",
            "scope": "offline helper contract only; no COMSOL/live acceptance",
            "source_hashes": {
                "tools/g33_export_cases.py": sha256_file(ROOT / "tools/g33_export_cases.py"),
                "tests/test_g33_export_cases.py": sha256_file(ROOT / "tests/test_g33_export_cases.py"),
            },
        },
        "propagation_checkpoints": [
            {
                "sequence": 1,
                "checkpoint": "MCP request and catalog/schema",
                "flow": "public tool -> _g2_registry.validate_call -> effective schema -> operation/effect/identity",
                "assertions": ["result.field_export accepts overwrite as optional boolean default false", "artifact.read remains bounded READ", "NodePath/model_ref wire shape is validated before dispatch", "package and reviewed catalog bytes match"],
                "tests": ["tests/test_g2_registry_packaging.py", "tests/test_g3_wiring.py", "tests/test_g3_phase4_wire_replay.py::test_no_string_literal_in_a_nodepath_argument"],
            },
            {
                "sequence": 2,
                "checkpoint": "operation implementation and isolation",
                "flow": "_g3_ops catalog effect -> dispatch -> ManagedBackend._invoke_g3_model -> effect/permission/isolation",
                "assertions": ["all published operation ids resolve to exactly one callable", "non-READ effects require isolation", "missing/import-broken modules cannot be silently treated as a passing implementation", "witness scope wraps the actual domain call"],
                "tests": ["tests/test_g3_wiring.py", "tests/test_g3_w17.py::test_w17_operations_catalog_effects_and_dispatch", "tests/test_g3_phase4_isolation_and_causes.py"],
            },
            {
                "sequence": 3,
                "checkpoint": "preflight, ledger ticket, snapshots",
                "flow": "ManagedBackend -> ExecutionService.execute_legacy -> SessionLedger begin/finish -> revision/dirty state",
                "assertions": ["validation refusal is returned only with explicit validation stage and no mutation witness", "post-dispatch exception or missing snapshot is UNKNOWN/dirty", "successful evaluation cannot clear nested unknown or cleanup_failed", "job record and state events retain request/job/model identity"],
                "tests": ["tests/test_execution_service.py", "tests/test_execution_state_propagation.py", "tests/test_g3_domain_outcome.py::test_a_failed_domain_operation_reaches_the_job_as_a_failure", "tests/test_g3_domain_outcome.py::test_an_unknown_domain_operation_blocks_later_work_and_is_never_replayed"],
            },
            {
                "sequence": 4,
                "checkpoint": "Java request and reconciliation",
                "flow": "RemoteJava witness -> PersistentJavaWorker submit/_request -> Java RequestState -> status/reconcile",
                "assertions": ["same request id plus identical body is replay-safe", "different body is IDEMPOTENCY_KEY_CONFLICT", "RPC timeout leaves same request queryable and never auto-replays", "worker failure carries execution_state_unknown", "generation and endpoint ownership are verified"],
                "tests": ["tests/test_java_worker.py", "tests/test_g3_phase4_request_chain.py::test_a_retry_reuses_its_key_only_with_an_identical_body", "tests/test_g3_phase4_request_chain.py::test_an_unfinished_job_is_resolved_through_its_own_query_first"],
            },
            {
                "sequence": 5,
                "checkpoint": "domain outcome to public job/ledger",
                "flow": "DomainOutcome/domain_envelope -> ExecutionService.final_state -> SessionLedger -> MCP job_status/job_log/job_result/job_reconcile",
                "assertions": ["FAILED, UNKNOWN, PARTIAL, cleanup_failed, and NOT_RUN remain distinct", "UNKNOWN freezes later writes and is not published as success", "prewrite refusal remains retryable only when proven no dispatch", "public job and ledger payloads retain witness/cause/verification status"],
                "tests": ["tests/test_g3_domain_outcome.py", "tests/test_g3_phase4_reconcile_and_probes.py", "tests/test_g3_phase4_request_chain.py", "tests/test_g3_phase4_isolation_and_causes.py"],
            },
            {
                "sequence": 6,
                "checkpoint": "field evaluation to artifact",
                "flow": "result.field_export -> evaluate -> DomainOutcome -> ArtifactStore.publish -> artifact.read bounded hash",
                "assertions": ["format/path/overwrite are validated before evaluate", "failed/UNKNOWN/cleanup_failed/empty/nonfinite field cannot publish", "JSON keeps FieldArray metadata and CSV has explicit axis/expr/outer/inner/point/real/imag/unit/coords columns", "atomic file hash and bounded read hash are immutable and tamper detectable"],
                "tests": ["tests/test_g33_export_cases.py", "tests/test_g3_artifact_hardening.py", "tests/test_g3_d17_artifact_service.py", "tests/test_g3_w17.py::test_field_export_large_data_and_chunk_verification", "public MCP C12/C13/F07-F09 run after freeze (NOT_RUN here)"],
            },
        ],
        "required_reruns_after_root_freeze": {
            "g2_registry_and_resources": {
                "status": "REQUIRED",
                "tests": ["tests/test_g2_registry_packaging.py", "tests/test_g3_wiring.py", "tests/test_g3_phase4_wire_replay.py", "tests/test_g3_phase4_request_chain.py"],
                "reason": "catalog/schema/package resource and NodePath/effect/request identity inputs feed every downstream route",
            },
            "g3_w17_offline": {
                "status": "REQUIRED_AFTER_CURRENT_SOURCE_STABILIZES",
                "tests": ["tests/test_g3_w17.py", "tests/test_g33_export_cases.py", "tests/test_g3_artifact_hardening.py", "tests/test_g3_d17_artifact_service.py"],
                "reason": "current _g3_results hash changed after the latest targeted run; the old 28-pass result cannot be generalized to the current file",
            },
            "error_unknown_cleanup": {
                "status": "REQUIRED",
                "tests": ["tests/test_g3_domain_outcome.py", "tests/test_execution_service.py", "tests/test_execution_state_propagation.py", "tests/test_g3_phase4_reconcile_and_probes.py", "tests/test_g3_phase4_isolation_and_causes.py", "tests/test_g3_phase4_request_chain.py"],
                "reason": "verify structured failure/UNKNOWN/cleanup signals survive witness, service, ledger, job, and reconcile layers",
            },
            "worker_and_live_public_mcp": {
                "status": "NOT_RUN",
                "tests": ["fresh javac against the actual COMSOL classpath if supplied", "fresh authenticated loopback worker health/status/idempotency run", "tools/g33_export_cases.run_export_cases(client, run_dir) over Chain-A public MCP"],
                "reason": "this audit did not start COMSOL or touch shared processes; source compile and offline fixtures are not live acceptance",
            },
        },
        "wheel_and_resource_sync_checklist": [
            "Freeze source, then build the wheel from the frozen tree (not the earlier git-archive wheel); record command, cwd, interpreter, exit code, wheel SHA-256, and source manifest.",
            "Confirm pyproject package-data includes comsol_mcp/data/g2/*.json and worker_java/*.java; verify every catalog common.schema.json $ref resolves inside the extracted wheel.",
            "Install the wheel in a fresh external venv with the exact pinned constraints; run pip check and capture pip freeze.",
            "Import _g2_registry with the checkout absent; assert package catalog authority, reviewed/package byte equality, operation_count=272, and W17 overwrite/artifact.read schemas.",
            "Run schema/resource validation and package tests from outside the source checkout; do not count editable install or source-tree imports as wheel proof.",
            "Inspect wheel RECORD and compare included file list against the frozen source manifest; preserve no endpoint token, server identity, model receipt, or private credential in public evidence.",
            "Re-run the full software baseline only after source freeze; retain sandbox loopback errors separately from escalated host results and keep live COMSOL acceptance distinct.",
        ],
        "observed_worktree_failures_to_reproduce": [
            {
                "status": "REPORTED_NEEDS_REPRODUCTION",
                "test": "tests/test_g3_w17.py::test_dataset_crud_lifecycle",
                "observed": "expected NODE_NOT_FOUND, received INVALID_NODE_PATH for the old string-shaped fixture after NodePath changes",
            },
            {
                "status": "REPORTED_NEEDS_REPRODUCTION",
                "test": "tests/test_g3_w17.py::test_pinned_selection_without_entities_is_refused",
                "observed": "expected SELECTION_MATCHED_NO_ENTITIES, received SELECTION_APPLY_FAILED",
            },
            {
                "status": "REPORTED_NEEDS_REPRODUCTION",
                "test": "tests/test_g3_w17.py::test_result_numerical_and_table_management",
                "observed": "old string num1 path no longer reaches the intended typed path contract",
            },
            {
                "status": "REPORTED_NEEDS_REPRODUCTION",
                "test": "tests/test_g3_w17.py::test_unimplemented_solution_selections_are_refused_not_ignored",
                "observed": "expected API_UNSUPPORTED, received SOLUTION_AXIS_METADATA_UNAVAILABLE",
            },
        ],
        "evidence_publication": {
            "included": ["this JSON report", "command/cwd/interpreter/exit metadata", "source hashes and resource equality", "bounded test-result references and rerun checklist"],
            "excluded": ["credentials/tokens/private keys/certificates/env files", "COMSOL endpoint files and server identities", "private model paths/receipts", "temporary partial files and reconstructed artifacts"],
            "source_secret_scan": "not performed by this report builder; do not publish raw logs or endpoint files",
        },
    }

    RUN.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(str(REPORT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
