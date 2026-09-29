# COMSOL MCP full-project progress

**Current stage: W21 canonical Windows 6.4 → 6.3 native chain — scoped PASS. Overall project: PARTIAL.** This round paused capability expansion and completed the real production MCP chain on both Windows versions. The master plan remains 26 work packages, 272 actions, 60 validation targets and 6 environments; the final independent Reviewer has not started. The authoritative plan and task ledger remain [MAIN_ACCEPTANCE_PLAN](docs/full_project_execution/MAIN_ACCEPTANCE_PLAN.md), [TASKS.json](docs/full_project_execution/state/TASKS.json) and [RESUME.json](docs/full_project_execution/state/RESUME.json).

## Actually completed and verified

Same runtime source: `45399a5f7a5ff5a6ec6690d5f008a23faf54ae0c`. Execution order: Windows COMSOL 6.4.0 build 293, then 6.3.0 build 290 using the actual successful 6.4 receipt. Both public MCP chains completed start → connect → small model/geometry/mesh → one solve → exact solution-index read → full temperature field readback → owned cleanup. Both runners exited 0.

| Target | Actual run | Result |
| --- | --- | --- |
| Windows 6.4 | `w21-64-20260929T144015Z-95b9b5b5` | PASS, field `[1,1,5,637]`, unit K; selected values approximately 300–304.380 K |
| Windows 6.3 | `w21-63-20260929T144407Z-d0bfcdb8` | PASS, field `[1,1,5,637]`, unit K; selected values approximately 300–304.391 K |

Each selected tuple is `(outer=1, inner=5, solnum=5)`. Main Agent independently checked receipt/state/freeze and request identities, finite selected values and hashes, unchanged budgets, exact owned Server PID/birth exit and listener absence, Worker retirement and temporary Eval removal. The [compact two-version checkpoint](docs/full_project_execution/project_session/WINDOWS64_V2_SESSION_START_UNKNOWN_CHECKPOINT.json#canonical_native_chain_windows_round_20260929) records exact fingerprints, key result/cleanup evidence and reproduction entry.

Necessary repairs only: pass the existing 45s RPC wait to `session.start`; use a short internal state root for new Windows homes while preserving existing legacy homes and full session hashes; include COMSOL's nested recovery/solution filename in the existing 260-unit path guard. The new real Windows plan's worst path is 259 UTF-16 units including NUL. Real solve/readback success, not path preflight alone, establishes this round's result. The 900s total budget, 45s ordinary RPC wait and one Server/Worker/solve/read caps were unchanged.

## Software tests and retained failures

Latest affected regression: **298 PASS, 0 failed/error/skipped** across session context, Server, daemon sessions and runner. Main Agent verified the JUnit uniqueness, receipt and source hashes. The earlier start-wait repair passed 118 runner tests; counts overlap and are not additive. [Software checkpoint](docs/full_project_execution/project_session/PRODUCTION_RUNNER_SOFTWARE_CHECKPOINT.json#windows_native_solver_path_repair_20260929) contains exact commands/hashes. Failed long-path fixture runs remain on SSD; only the repaired final regression is PASS.

Two earlier Windows 6.3 failures remain preserved: interrupted start reached READY without a durable terminal result, then one public recovery proved current-host quiet and set lifecycle STOPPED revision 3 while retaining historical UNKNOWN; the following actual solve failed creating a 271-unit native file path, with 19/19 Worker requests terminal and complete current-host inventories quiet. Neither historical outcome is rewritten as successful. Earlier 6.4 failures and their scoped recovery evidence remain in the same checkpoint.

## Unfinished scope and next action

**PARTIAL / UNVERIFIED:** auxiliary structural probe completeness, actual spatial coordinates/intrinsic mesh mapping, stage-transfer admission, numerical convergence and physical validation. The coarse `hauto=9` smoke model establishes production-chain operation only; it does not establish these broader claims.

1. Resume the existing W21 fields/units/mesh mapping and actual stage-transfer-result acceptance work, using the verified Windows chains as platform baselines.
2. Continue the original W23–W26 plan: W23 native geometry/PML/Qabs/optics, W24 physical-control/scientific validation, W25 platform/Desktop and W26 delivery/release remain PARTIAL.
3. Run the unified independent Reviewer after the full project implementation, then repair/retest as planned. Do not add acceptance gates or parallel progress systems.

Latest resume entry: [RESUME.json](docs/full_project_execution/state/RESUME.json). Reproduce via `tools/run_w21_stage_native.py prepare/execute` with a fresh isolated plan, explicit matching COMSOL/JDK and the actual 6.4 prerequisite receipt for 6.3. Exact successful freezes, local receipt paths, manifest/archive hashes and key results are in the linked checkpoint; never replay historical freeze files. Full local round evidence: `/Volumes/SSD/Comsol-MCP/execution-scratch/w21-solver-path-w21r12`.

Both Mac COMSOL 6.3 targets remain `USER_REQUESTED_SKIP` / `NOT_RUN` per the scope ledger. Intel Mac 6.4 availability remains unconfirmed. Storage migration retained 619 inactive paths and 7 occupied roots; see [TMP migration summary](docs/full_project_execution/state/TMP_MIGRATION_SUMMARY.json). No full-project PASS is claimed.
