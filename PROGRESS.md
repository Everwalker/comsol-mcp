# COMSOL MCP full-project progress

**Current stage: W21 complete current-mesh capture helper — software PASS, native/integration NOT_RUN. Overall project: PARTIAL.** The Windows 6.4 → 6.3 canonical native round remains scoped PASS on its exact earlier runtime source. The master plan remains 26 work packages, 272 actions, 60 validation targets and 6 environments; the final independent Reviewer has not started. The authoritative plan and task ledger remain [MAIN_ACCEPTANCE_PLAN](docs/full_project_execution/MAIN_ACCEPTANCE_PLAN.md), [TASKS.json](docs/full_project_execution/state/TASKS.json) and [RESUME.json](docs/full_project_execution/state/RESUME.json).

## Current mesh evidence implementation

A read-only internal helper now streams every vertex coordinate, documented element corner index and geometric entity assignment through bounded MeshSequence block getters. Content hashes are independent of block size; observation identity and limited claim semantics have a separate evidence hash. Fixed element arities, full shape/index checks, a 65,536-scalar cap, metadata drift checks and strict snapshot validation reject incomplete evidence. Worker UNKNOWN/timeout exceptions propagate without subsequent reads.

**Software PASS: 31 unique focused tests, 0 failed/error/skipped. Native/integration NOT_RUN.** Main reviewed the diff and checked source/dependency/manifest/log/JUnit hashes. The initial 23 PASS / 7 FAIL and later reruns are retained. [Compact checkpoint](docs/full_project_execution/project_session/STAGE_BACKEND_SOFTWARE_CHECKPOINT.json#current_mesh_snapshot_20260930); full local evidence `/Volumes/SSD/Comsol-MCP/execution-scratch/w21-mesh-evidence-20260930`.

This is current mesh content capture only, not atomic/historical mesh, source-target mapping, frame, DOF or solver-history acceptance. Next: integrate snapshots under managed ownership around the initial-stage solve and persist source-attempt evidence; distinguish first-stage initialization prerequisites from predecessor transfer and post-solve acceptance. The existing admission stub is still UNVERIFIED. Continue original W21/W23–W26 and final independent review.

## Current W21 unit-controls stage

The frozen `auto-unit-controls` solve/readback profile requests exactly `T`, `T/1[K]`, `1` in one read, with no requested units and unchanged one-solve/one-read budgets. It preserves full arrays, exact tuple identities, units and hashes. Numeric checks require T/1[K] to match T within abs 1e-10 + rel 1e-12, constant 1, and zero imaginary components. Actual `{real, imag}` payloads are retained. Missing units remain UNVERIFIED; wrong units/numbers are FAIL. Windows 6.3 requires a successful matching-profile actual Windows 6.4 receipt.

**Software PASS: 138 unique runner tests, 0 failures/errors/skips. Native scoped PASS on source `40c9838`: Windows 6.4 then 6.3.** Actual runs `w21-64-20260929T165606Z-142016ea` and `w21-63-20260929T170038Z-0036f835` each exited 0; all 15 actions succeeded without UNKNOWN, one solve and one field read. Both returned `[3,1,5,637]`, units K/1/1, selected tuple outer=1/inner=5/solnum=5. Temperature-normalization and constant-one maximum errors were 0; all imaginary components were 0. Exact owned PID/birth exit, listener absence, Worker retirement and temporary Eval removal were verified. Main independently recomputed selected/full per-expression hashes and checked freeze/state/request binding. The 6.3 plan pins the actual successful same-profile 6.4 receipt.

[Compact native checkpoint](docs/full_project_execution/project_session/PRODUCTION_RUNNER_SOFTWARE_CHECKPOINT.json#auto_unit_controls_native_windows_20260930) includes fingerprints, results and reproducible entry. Full evidence stays on SSD at `/Volumes/SSD/Comsol-MCP/execution-scratch/w21-auto-unit-native-w21r14`; offline failures and tests remain at `/Volumes/SSD/Comsol-MCP/execution-scratch/w21-auto-unit-controls-7afde37`. Native stage admission, complete historical mesh/frame mapping and physical validation remain UNVERIFIED. Next: implement the actual stage field/unit/mesh evidence producer and stage-result binding under the original W21 requirements, then original W23–W26 and final independent Reviewer.

## Retained metadata finding

Runtime source `4c2cbc9`, Windows 6.4 build 293, run `w21-64-20260929T155143Z-06d67ff1`: exact `info / Shape` capture completed, but the native table is AVAILABLE with **0 rows**. Seven physics-field declarations include `temperature → T`; they do not establish active solver DOFs or hidden-state coverage. No intrinsic unit was read. **Transport/owned cleanup scoped PASS; unit/mesh/stage admission UNVERIFIED; capability PARTIAL.** Twelve actions returned success, no UNKNOWN; one Server/Worker, zero study/solver dispatches. Exact PID/birth exit, listener absence and Worker retirement are verified. Per the predeclared condition, Windows 6.3 duplicate empty-table capture was **NOT_RUN**; the earlier two-version solve/readback baseline remains valid.

[Compact native checkpoint](docs/full_project_execution/project_session/FIELD_IDENTITY_PROBE_SOFTWARE_CHECKPOINT.json#targeted_native_windows64_20260930) binds source/archive/freeze/request/model/revision/receipt/state hashes. Full evidence is on SSD at `/Volumes/SSD/Comsol-MCP/execution-scratch/w21-targeted-probe-w21r13`. Initial upload/extraction auto-review refusals were resolved through the same normal channels using existing user authorization; no alternate transfer or replay was used.

The automatic-unit controls above now provide real output evidence. The empty Shape table remains incomplete metadata and is not active-DOF or intrinsic-unit proof.

## Latest W21 software result

The existing probe now supports a frozen exact FeatureInfo tag/table or explicit fields/tag-only discovery. Overflow reports bounded location/counts; all output limits remain unchanged. Selected/discovery output is always PARTIAL, and native stage admission remains UNVERIFIED. Main review caught and closed a null-target fallback to full-table reading. Java offline compile/harness passed **58 assertions**; the affected runner module passed **124 unique tests**, 0 failed/error/skipped. Main verified source/log/JUnit hashes and 14 bounded JSON envelopes. Earlier sandbox and fixture-path failures remain preserved. [Compact checkpoint](docs/full_project_execution/project_session/FIELD_IDENTITY_PROBE_SOFTWARE_CHECKPOINT.json#scoped_selection_20260929).

The fresh Windows 6.4 run above exercised this exact software. The result remains scoped metadata capture and does not establish stage-transfer acceptance.

## Actually completed and verified

Same runtime source: `45399a5f7a5ff5a6ec6690d5f008a23faf54ae0c`. Execution order: Windows COMSOL 6.4.0 build 293, then 6.3.0 build 290 using the actual successful 6.4 receipt. Both public MCP chains completed start → connect → small model/geometry/mesh → one solve → exact solution-index read → full temperature field readback → owned cleanup. Both runners exited 0.

| Target | Actual run | Result |
| --- | --- | --- |
| Windows 6.4 | `w21-64-20260929T144015Z-95b9b5b5` | PASS, field `[1,1,5,637]`, unit K; selected values approximately 300–304.380 K |
| Windows 6.3 | `w21-63-20260929T144407Z-d0bfcdb8` | PASS, field `[1,1,5,637]`, unit K; selected values approximately 300–304.391 K |

Each selected tuple is `(outer=1, inner=5, solnum=5)`. Main Agent independently checked receipt/state/freeze and request identities, finite selected values and hashes, unchanged budgets, exact owned Server PID/birth exit and listener absence, Worker retirement and temporary Eval removal. The [compact two-version checkpoint](docs/full_project_execution/project_session/WINDOWS64_V2_SESSION_START_UNKNOWN_CHECKPOINT.json#canonical_native_chain_windows_round_20260929) records exact fingerprints, key result/cleanup evidence and reproduction entry.

Necessary repairs only: pass the existing 45s RPC wait to `session.start`; use a short internal state root for new Windows homes while preserving existing legacy homes and full session hashes; include COMSOL's nested recovery/solution filename in the existing 260-unit path guard. The new real Windows plan's worst path is 259 UTF-16 units including NUL. Real solve/readback success, not path preflight alone, establishes this round's result. The 900s total budget, 45s ordinary RPC wait and one Server/Worker/solve/read caps were unchanged.

## Software tests and retained failures

Canonical-chain repair regression: **298 PASS, 0 failed/error/skipped** across session context, Server, daemon sessions and runner. Main Agent verified the JUnit uniqueness, receipt and source hashes. The earlier start-wait repair passed 118 runner tests; counts overlap and are not additive. [Software checkpoint](docs/full_project_execution/project_session/PRODUCTION_RUNNER_SOFTWARE_CHECKPOINT.json#windows_native_solver_path_repair_20260929) contains exact commands/hashes. Failed long-path fixture runs remain on SSD; only the repaired final regression is PASS.

Two earlier Windows 6.3 failures remain preserved: interrupted start reached READY without a durable terminal result, then one public recovery proved current-host quiet and set lifecycle STOPPED revision 3 while retaining historical UNKNOWN; the following actual solve failed creating a 271-unit native file path, with 19/19 Worker requests terminal and complete current-host inventories quiet. Neither historical outcome is rewritten as successful. Earlier 6.4 failures and their scoped recovery evidence remain in the same checkpoint.

## Unfinished scope and next action

**PARTIAL / UNVERIFIED:** auxiliary structural probe completeness, actual spatial coordinates/intrinsic mesh mapping, stage-transfer admission, numerical convergence and physical validation. The coarse `hauto=9` smoke model establishes production-chain operation only; it does not establish these broader claims.

1. Resume the existing W21 fields/units/mesh mapping and actual stage-transfer-result acceptance work, using the verified Windows chains as platform baselines.
2. Continue the original W23–W26 plan: W23 native geometry/PML/Qabs/optics, W24 physical-control/scientific validation, W25 platform/Desktop and W26 delivery/release remain PARTIAL.
3. Run the unified independent Reviewer after the full project implementation, then repair/retest as planned. Do not add acceptance gates or parallel progress systems.

Latest resume entry: [RESUME.json](docs/full_project_execution/state/RESUME.json). Reproduce via `tools/run_w21_stage_native.py prepare/execute` with a fresh isolated plan, explicit matching COMSOL/JDK and the actual 6.4 prerequisite receipt for 6.3. Exact successful freezes, local receipt paths, manifest/archive hashes and key results are in the linked checkpoint; never replay historical freeze files. Full local round evidence: `/Volumes/SSD/Comsol-MCP/execution-scratch/w21-solver-path-w21r12`.

Both Mac COMSOL 6.3 targets remain `USER_REQUESTED_SKIP` / `NOT_RUN` per the scope ledger. Intel Mac 6.4 availability remains unconfirmed. Storage migration retained 619 inactive paths and 7 occupied roots; see [TMP migration summary](docs/full_project_execution/state/TMP_MIGRATION_SUMMARY.json). No full-project PASS is claimed.
