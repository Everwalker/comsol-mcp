#!/usr/bin/env python3
"""Generate the substantive, static semantic review for the three key runners.

This is deliberately a small report generator rather than an executable test.  It
reads the complete current source text, records source identity and emits findings
that are limited to what can be established without starting COMSOL.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "audit" / "semantic_key_tests_tools_review.json"
FILES = [
    ROOT / "tools" / "g33_numeric_cases.py",
    ROOT / "tools" / "g3_3_protocol_acceptance.py",
    ROOT / "tests" / "run_g3_3_live_acceptance.py",
]


def file_identity(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    return {
        "path": str(path.relative_to(ROOT)),
        "bytes": len(data),
        "line_count": len(data.decode("utf-8").splitlines()),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def evidence(lines: str, observation: str, conclusion: str) -> dict[str, str]:
    return {"lines": lines, "observation": observation, "conclusion": conclusion}


def main() -> None:
    identities = {str(path.relative_to(ROOT)): file_identity(path) for path in FILES}
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--short", "--untracked-files=all"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()

    numeric = {
        **identities["tools/g33_numeric_cases.py"],
        "reviewed_here": True,
        "semantic_reviewed": True,
        "review_status": "STATIC_SEMANTIC_REVIEW_ONLY",
        "runtime_status": "NOT_RUN",
        "engine_started": False,
        "responsibility": "Public MCP numerical cases C04-C10 and Chain-B multidimensional axis checks.",
        "oracle_sources": [
            "ORACLES constants fixed in source before run (lines 8-15).",
            "Analytic comments for polynomial, weighted, centered-variance, axisymmetric and parameter-time fixtures (lines 99-109, 136-142, 190-240).",
            "Engine-reported dataset solution_indices and parameter metadata are used for Chain-B axis binding (lines 216-240); this is an engine metadata oracle, not an independent physical oracle.",
        ],
        "cases": [
            {"id": "C04", "lines": "82-89", "meaning": "Builds a fresh constant-field fixture and checks integral, average, std and RMS against [6,9], [2,3], [0,0] and [2,3]."},
            {"id": "C05", "lines": "91-98,111-116,174-188", "meaning": "Checks 2-D all/subset, 3-D volume, 1-D endpoint/line dimensions and non-default component/geometry; scalar values are flattened."},
            {"id": "C06", "lines": "99-109", "meaning": "Checks x+2y integral/average/std/RMS, a positive nonconstant weight and a large-offset centered standard deviation."},
            {"id": "C09", "lines": "117-145", "meaning": "Checks real/imag/abs/phase and preserve modes for constant and varying complex expressions, including transform-before-statistics."},
            {"id": "C10", "lines": "148-172", "meaning": "Checks m versus mm point reads, coordinate readback and unsupported frame/dimension refusals; records PARTIAL because two controls remain."},
            {"id": "C07", "lines": "190-198", "meaning": "Checks axisymmetric volume, mean radius and outer side integral against the cylinder oracle."},
            {"id": "C08", "lines": "199-269", "meaning": "Runs a real parametric/time study, checks engine axes and parameter pairs, FieldArray shape [2,4,5,3], aggregates, selection, slices and invalid bindings."},
        ],
        "line_evidence": [
            evidence("18-29", "close() recursively flattens lists and compares only count, scalar type, finiteness and value tolerance.", "A wrong axis order or nesting can pass if the flattened sequence happens to match; this helper is not a shape/order oracle."),
            evidence("32-75", "action() dispatches with reconcile=False and handles only error.code EXECUTION_STATE_UNKNOWN; it returns data after checking success boolean.", "A success envelope carrying data.status RUNNING/QUEUED or rpc_wait_expired can be treated as completed, allowing a later mutation before quiescence is proven."),
            evidence("46-66", "UNKNOWN reconciliation checks three observations and model_inspect, then adopts the readback.", "This is useful evidence for an actual UNKNOWN error, but it does not cover non-error running envelopes and terminal status handling is narrower than the C15 helper."),
            evidence("148-172", "C10 is explicitly recorded as status PARTIAL after m/mm and geometry checks.", "A caller that only asserts no exception can report a suite PASS while this case is incomplete; PARTIAL must remain a non-PASS acceptance state."),
            evidence("216-240", "Chain-B expected parameter pairs and t=.5*inner are derived from engine metadata and hard-coded sweep construction.", "The axis mapping is exercised, but this does not independently validate the physical solve beyond the constructed analytic expression."),
            evidence("270-271", "Final numeric_cases.json is written only after the preceding cases complete.", "An exception before this write can leave per-case assertion files but no consolidated numeric ledger; the outer runner must classify that as incomplete."),
        ],
        "false_pass_risks": [
            {"id": "NUM-FP-01", "severity": "high", "lines": "18-29", "finding": "Flatten-only scalar comparison omits FieldArray shape/axis identity for most cases."},
            {"id": "NUM-FP-02", "severity": "high", "lines": "43-75", "finding": "RUNNING/QUEUED/rpc_wait_expired success data is not reconciled before the next mutation."},
            {"id": "NUM-FP-03", "severity": "medium", "lines": "148-172", "finding": "C10 records PARTIAL; an outer caller that does not inspect records can promote incomplete coverage."},
        ],
        "cleanup_risks": [
            {"lines": "32-41,270-271", "finding": "The helper writes JSON checkpoints but does not own model/worker cleanup; correctness depends on the caller's OwnedHost/finally path."},
            {"lines": "43-75", "finding": "If an unresolved non-error running envelope is accepted, the caller may close or mutate while work remains active."},
        ],
    }

    protocol = {
        **identities["tools/g3_3_protocol_acceptance.py"],
        "reviewed_here": True,
        "semantic_reviewed": True,
        "review_status": "STATIC_SEMANTIC_REVIEW_ONLY",
        "runtime_status": "NOT_RUN",
        "engine_started": False,
        "responsibility": "Fresh stdio acceptance orchestration, owned server/worker teardown, Gate-A reopen, negative controls and numerical/export sub-suite delegation.",
        "phases": [
            {"name": "isolated_server", "lines": "30-56", "meaning": "Starts one new mphserver in run-private prefs/tmp/recovery and terminates only the process it started."},
            {"name": "owned_host_teardown", "lines": "63-159", "meaning": "Observes pending jobs, retains runtime on unresolved work, closes transport and terminates freshly identified private processes."},
            {"name": "trusted_java", "lines": "243-276", "meaning": "Hashes/stores source artifacts and dispatches code.execute_java; checks worker success and attempts UNKNOWN observation/retry."},
            {"name": "gate_a_exercise", "lines": "340-425", "meaning": "Builds/saves/reopens Chain A/B/C, checks fresh server identity and stale references, then invokes verify_reopen."},
            {"name": "negative_controls", "lines": "427-524", "meaning": "Checks hash/dataset/solution/derived/value failures and modified/cleared copies, plus an intentional Java fault."},
            {"name": "numerical_suite", "lines": "527-580", "meaning": "Delegates live numeric/control/node/export cases and tears down through OwnedHost."},
            {"name": "exploratory_probes", "lines": "583-795", "meaning": "Native API observations explicitly described as exploratory, not a passing numerical gate."},
        ],
        "oracle_sources": [
            "Public MCP response status and job logs for lifecycle/idempotency checks.",
            "Freshly built source strings extracted from current tests/run_g3_3_live_acceptance.py (lines 161-168).",
            "Chain-A point values have analytic checks in points() (lines 284-301); Chain-B/C reopen expectations are captured from pre-save reads (lines 375-382).",
            "verify_reopen model identity, artifact hash, dataset/solution and derived-value checks (lines 414-419 and negatives 427-524).",
        ],
        "line_evidence": [
            evidence("63-100", "OwnedHost marks active/rpc_wait_expired/UNKNOWN jobs pending and polls job_status/job_reconcile until terminal or metadata.reconciled_quiescent.", "This is the intended no-kill boundary for active jobs, but only terminal/quiescent observations release the pending set."),
            evidence("121-158", "Cleanup iterates endpoint files and asserts all recorded results are EXITED; missing endpoints or absent process snapshots are skipped.", "all([]) can make cleanup look successful without proving that no owned child existed; a missing/invalid endpoint is not recorded as an explicit verification state."),
            evidence("243-273", "On Java failure the code observes job_status/result/reconcile, then for UNKNOWN replays the exact request and compares bounded job-log events.", "The replay is attempted before an explicit terminal/quiescent proof. Equal logs and same job id prove idempotency response shape, not that the original work was no longer active."),
            evidence("274-276", "A successful reply is required to contain worker.ok=True and worker.status=SUCCEEDED.", "A success=True envelope with data.status RUNNING/rpc_wait_expired is not classified here and can raise or bypass intended active-job handling."),
            evidence("375-417", "Receipts record point values from the builder; after reopen read values are flattened and passed into verify_reopen as the evaluator.", "For B/C this verifies persistence/shape against the reopened read, but the value oracle is not independent of that same read; a systematic post-reopen mapping error could self-consistently pass."),
            evidence("569-578", "export_only asserts no FAIL/BLOCKED and PASS>0; normal numerical_suite discards the run_numeric_cases return value.", "It does not assert an overall report status or enforce a budget witness, and C10 PARTIAL from the helper is not promoted by this caller."),
            evidence("583-595", "axis_probe docstring says native API observations are not a passing numerical gate.", "These probes must remain observations in acceptance accounting."),
        ],
        "false_pass_risks": [
            {"id": "PROTO-FP-01", "severity": "high", "lines": "243-273", "finding": "UNKNOWN Java request is retried before quiescence is proven."},
            {"id": "PROTO-FP-02", "severity": "high", "lines": "375-417", "finding": "Gate-A B/C stored-value evaluator is coupled to the reopened read rather than an independent analytic oracle."},
            {"id": "PROTO-FP-03", "severity": "medium", "lines": "569-578", "finding": "Delegated numerical/export statuses are incompletely aggregated; explicit PARTIAL or missing overall status can be lost."},
        ],
        "cleanup_risks": [
            {"id": "PROTO-CLEAN-01", "severity": "high", "lines": "121-158", "finding": "Empty cleanup result is vacuously accepted; endpoint absence/invalid PID is not an explicit cleanup failure."},
            {"id": "PROTO-CLEAN-02", "severity": "high", "lines": "243-273", "finding": "Unknown replay can create a second mutation window while the original job remains active."},
            {"id": "PROTO-CLEAN-03", "severity": "medium", "lines": "98-119", "finding": "On quiescence observation failure runtime is retained, but the caller must preserve and later reconcile that retained process; this branch is not a PASS."},
        ],
    }

    live = {
        **identities["tests/run_g3_3_live_acceptance.py"],
        "reviewed_here": True,
        "semantic_reviewed": True,
        "review_status": "STATIC_SEMANTIC_REVIEW_ONLY",
        "runtime_status": "NOT_RUN",
        "engine_started": False,
        "responsibility": "Unified local COMSOL C00-C17 runner; combines static package checks, live worker cases, numerical oracles, export/chunk controls and teardown ledger.",
        "oracle_sources": [
            "Analytic constants and tolerance records are source-defined in each numerical case (C04-C10).",
            "Fresh engine reads for point/aggregate/table/probe checks where stated in the case code.",
            "Gate-A artifact hash, dataset/solution/derived identity and saved-solution reads from reopened workers.",
            "Static lock/catalog/schema/wheel inspection for C00/C01/C16; these are delivery checks, not COMSOL scientific validation.",
        ],
        "cases": [
            {"id": "C00", "lines": "699-823", "scope": "clean recovery, source binding, wheel build and out-of-tree wheel import; delivery/static plus packaging."},
            {"id": "C01", "lines": "828-868", "scope": "hashes fixed historical evidence and correction ledger; static integrity only."},
            {"id": "C02", "lines": "873-911", "scope": "cleanup_failed classification and fail-closed export refusal; no live engine read."},
            {"id": "C03/C03R", "lines": "916-1713", "scope": "live Chain A/B/C save/reopen, negative copies, independent re-solve; Gate-A and numerical."},
            {"id": "C04", "lines": "1717-1797", "scope": "constant field integral/average/std/RMS and denominator oracle."},
            {"id": "C05", "lines": "1800-2026", "scope": "2-D partition plus fresh 1-D wire and 3-D block dimensions, temperature and empty-selection refusal."},
            {"id": "C06", "lines": "2028-2119", "scope": "analytic x+2y integral/average/std/RMS and denominator."},
            {"id": "C07", "lines": "2121-2196", "scope": "axisymmetric cylinder volume, mean radius and cross-section/revolved measures."},
            {"id": "C08", "lines": "2198-2307", "scope": "transient solution axis, point shape, slices, expression relation and range refusals."},
            {"id": "C09", "lines": "2309-2394", "scope": "complex preserve/real/imag/abs/phase and missing-imaginary rejection."},
            {"id": "C10", "lines": "2396-2482", "scope": "point coordinate units, nonfinite/dimension/frame refusals."},
            {"id": "C11", "lines": "2484-2544", "scope": "CutPoint2D read and dataset-chain cycle detection, including a mock-only cycle control."},
            {"id": "C12", "lines": "2546-2743", "scope": "path traversal, export refusal, atomic rollback/publish; calls production export directly."},
            {"id": "C13", "lines": "2746-2910", "scope": "artifact.read chunk reconstruction, digest/refusal checks and client tracemalloc comparison."},
            {"id": "C14", "lines": "2912-3036", "scope": "live probe/table separation, dispatch reachability and readback."},
            {"id": "C15", "lines": "3038-3219", "scope": "same/different request hashes, live control responsiveness, solve completion and stale reference."},
            {"id": "C16", "lines": "3221-3361", "scope": "dependency lock/source identity, wheel resource presence and schema/catalog parse."},
            {"id": "C17", "lines": "3363-3428", "scope": "owned worker/server teardown, locks and derived run status."},
        ],
        "line_evidence": [
            evidence("69-74", "Java fixture helper requires ok=True and status=SUCCEEDED.", "Engine fixture failures are surfaced before reading an unchanged model."),
            evidence("130-148", "_flatten_scalars removes nesting and maps all leaves to floats.", "This convenience comparison is shape-insensitive unless the case separately checks shape."),
            evidence("212-223", "_reopen_evaluator explicitly indexes the reopened value list; comments document the former x==x defect.", "Current Gate-A evaluator is improved for actual reopened reads, but the value list remains a same-read oracle unless analytic expectations are separately supplied."),
            evidence("828-868", "C01 checks exact historical hashes and correction levels.", "PASS means static ledger integrity; it cannot establish fresh engine behavior."),
            evidence("873-911", "C02 classifies cleanup_failed and checks a failed export does not create a file.", "This is a production contract test; no live COMSOL result is exercised."),
            evidence("1419-1425,1616-1648", "Chain-B/C expectations are pre-save reads, while Chain-A has an analytic profile and negative copies.", "B/C demonstrate stored-value preservation and identity, not an independent physical solution oracle."),
            evidence("1490-1553", "Cleared copy is read through engine and then passed to verify_reopen; a returned value is treated as a defect.", "This is a meaningful stale/cleared negative control because it does not replay captured numbers."),
            evidence("1555-1608", "Mutated copy is re-solved, hashed after mutation and read through engine before mismatch assertion.", "This negative control binds both digest and changed numerical value."),
            evidence("1719-1797", "C04 verifies analytic constant-field values and reported denominator.", "Numerical oracle is concrete and engine-reported measure is checked."),
            evidence("1802-2026", "C05 builds/solves 1-D and 3-D fixtures, checks measures, temperatures and empty selections.", "Dimension coverage is materially stronger than a 2-D-only test; worker close is attempted in finally at 2005-2010."),
            evidence("2031-2119", "C06 checks x+2y analytic integrals and all derived statistics with tolerance records.", "Independent polynomial oracle is explicit."),
            evidence("2124-2196", "C07 checks cylinder formulas plus axisymmetric metadata/measure fields.", "Axisymmetric weighting is checked as a semantic result, subject to the stated 1e-6 tolerance."),
            evidence("2201-2307", "C08 checks engine time values [0..5], shape [expressions,solutions,points], slices and invalid ranges.", "This is a concrete axis/shape oracle with relation x*T, not only scalar flattening."),
            evidence("2312-2394", "C09 checks complex modes and explicitly rejects missing imaginary data.", "The negative control is no longer swallowed by a broad except in this source."),
            evidence("2487-2544", "C11 catches any exception while attempting CutPoint2D creation, then evaluates cpt1.", "A pre-existing or partially created cpt1 could make this control ambiguous; creation failure should be recorded explicitly."),
            evidence("2549-2743", "C12 checks traversal, failed export preservation, staged writer rollback, success ZIP/hash, absolute destination refusal and direct _export_to_artifact publish.", "The atomic controls are meaningful; the direct export call must match the current project-root/overwrite contract before live reuse."),
            evidence("2748-2910", "C13 reconstructs via host dispatch and validates chunk/whole hashes; tracemalloc compares stream and full-read client allocations.", "This proves bounded client transport behavior for these artifacts, not COMSOL engine internal memory or raw getData peak."),
            evidence("2915-3036", "C14 creates/removes a live probe, exercises dispatch, refuses unimplemented operations and writes/reads/removes a table.", "Probe/table separation is live; table removal has no post-remove readback assertion."),
            evidence("3041-3219", "C15 uses a fixed 2 s health budget during an actual >1 s Java solve, waits for completion, checks post-solve read and stale reference.", "This is a substantive active-control test; worker close is in finally, so a solve-thread failure needs separate retained-runtime handling."),
            evidence("3224-3361", "C16 checks current lock/source identity and wheel resources, building a wheel with pip wheel --no-deps if C00 did not.", "Resource presence and source binding are checked, but this branch does not fresh-install the fallback wheel or run pip check itself."),
            evidence("3366-3428", "C17 closes verifier worker, stops the isolated server, checks locks and derives status from every prior case.", "Teardown verdict is only meaningful when every earlier case recorded an explicit result and process ownership is verified."),
            evidence("3433-3506", "run_case_guarded converts escaping exceptions into FAIL records and continues through C17.", "Failure recording is safer than aborting, but a later case can still depend on missing C03 artifacts and must remain a visible cascade failure."),
        ],
        "false_pass_risks": [
            {"id": "LIVE-FP-01", "severity": "high", "lines": "1419-1425,1616-1636", "finding": "Gate-A Chain-B/C value preservation uses captured pre-save values as the expected source; it is not a physical analytic oracle."},
            {"id": "LIVE-FP-02", "severity": "medium", "lines": "2491-2497", "finding": "C11 swallows every CutPoint2D construction error before evaluating the tag."},
            {"id": "LIVE-FP-03", "severity": "medium", "lines": "3288-3301", "finding": "C16 fallback wheel path builds but does not install and import that fallback wheel in a clean environment."},
            {"id": "LIVE-FP-04", "severity": "medium", "lines": "2829-2890", "finding": "C13 memory assertions cover Python tracemalloc for artifact reads, not engine-side or JSON/Python field-array allocation during evaluation."},
        ],
        "cleanup_risks": [
            {"id": "LIVE-CLEAN-01", "severity": "medium", "lines": "2638-2647", "finding": "Failed atomic-save candidates are intentionally retained as incident evidence; C12 therefore does not claim a clean artifact directory after failure."},
            {"id": "LIVE-CLEAN-02", "severity": "medium", "lines": "2005-2010,3142-3147", "finding": "Worker close is attempted in finally, but no explicit retained-runtime path is recorded if a background solve thread remains alive after an exception."},
            {"id": "LIVE-CLEAN-03", "severity": "low", "lines": "3005-3013", "finding": "Probe removal is verified, while table removal is issued without a post-removal readback."},
        ],
    }

    report = {
        "schema": "comsol-mcp.g3.3.semantic-key-tests-tools-review.v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "SEMANTIC_REVIEW_STATIC_ONLY",
        "semantic_reviewed": True,
        "review_method": "Complete source text read for exactly the three files below; line evidence is tied to the current bytes and hash. No imports, COMSOL startup or test execution.",
        "runtime": {
            "engine_started": False,
            "server_started": False,
            "fresh_worker_reopen": False,
            "live_acceptance_claim": False,
            "claim_limit": "Findings are static semantic observations and required follow-up points, never runtime PASS evidence.",
        },
        "repository": {
            "root": str(ROOT),
            "head": head,
            "branch": branch,
            "dirty": bool(status),
            "dirty_entry_count": len(status),
            "source_identity": identities,
        },
        "automated_inventory_pointer": {
            "path": "audit/semantic_tests_tools_review.json",
            "required_interpretation": "AUTOMATED_INVENTORY_ONLY; semantic_reviewed=false; do not treat its per-file inventory as a semantic PASS.",
        },
        "files": [numeric, protocol, live],
        "summary": {
            "files_substantively_reviewed": 3,
            "files_runtime_verified": 0,
            "case_semantics_recorded": 32,
            "finding_ids": [
                "NUM-FP-01", "NUM-FP-02", "NUM-FP-03", "PROTO-FP-01", "PROTO-FP-02", "PROTO-FP-03",
                "PROTO-CLEAN-01", "PROTO-CLEAN-02", "PROTO-CLEAN-03", "LIVE-FP-01", "LIVE-FP-02",
                "LIVE-FP-03", "LIVE-FP-04", "LIVE-CLEAN-01", "LIVE-CLEAN-02", "LIVE-CLEAN-03",
            ],
            "required_follow_up": [
                "Keep the automated 80-file inventory explicitly non-semantic.",
                "Reconcile running/rpc_wait_expired envelopes before any subsequent mutation in numeric helpers.",
                "Require an independent budget witness before raw getData/export and aggregate delegated status without dropping PARTIAL.",
                "Do not replay UNKNOWN Java requests until a terminal/quiescent observation is proven.",
                "Distinguish missing endpoint/process evidence from an empty successful cleanup result.",
                "Rerun all live cases after source freeze; this report itself is not a replacement for that run.",
            ],
        },
    }
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUT), "files": identities, "status": report["status"]}, indent=2))


if __name__ == "__main__":
    main()
