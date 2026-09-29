# COMSOL MCP full-project progress

**Current priority:** pause capability expansion for this round and complete only the canonical Windows 6.4 → 6.3 solve/readback chain. **Overall PARTIAL.** The master goal still covers 26 work packages, 272 actions, 60 validation targets and 6 target environments; the final independent Reviewer has not started. The acceptance map and task ledger remain in [MAIN_ACCEPTANCE_PLAN](docs/full_project_execution/MAIN_ACCEPTANCE_PLAN.md), [TASKS.json](docs/full_project_execution/state/TASKS.json) and [RESUME.json](docs/full_project_execution/state/RESUME.json).

## Current Windows 6.4 state

**PASS — scoped canonical Windows 6.4 native chain, including the shared path repair.** Latest run `w21-64-20260929T144015Z-95b9b5b5`, source `45399a5`, COMSOL 6.4.0 build 293, exited 0. Public MCP start/connect/model construction → one solve → exact solution-index read → full temperature field readback → owned cleanup all completed. Field shape `[1,1,5,637]`, unit K, selected tuple `(outer=1, inner=5, solnum=5)`, approximately 300–304.380 K. Main Agent checked receipt/state/freeze/request identities, finite values and hashes, unchanged budget, and exact owned Server exit/listener absence plus Worker retirement and temporary Eval removal. [Latest compact checkpoint](docs/full_project_execution/project_session/WINDOWS64_V2_SESSION_START_UNKNOWN_CHECKPOINT.json#canonical_native_chain_windows64_shared_path_candidate).

The revised Windows path preflight includes native recovery files (worst path 259 UTF-16 units including NUL); the real solve and readback passed. Earlier 6.4 success on source `2bdcc75` remains retained separately. Windows 6.3 verification of this candidate is next; its prior native failure remains below.

This is minimal coarse-mesh (`hauto=9`) production-chain acceptance only. The auxiliary structural probe remains RAW_UNINTERPRETED/PARTIAL. Actual spatial coordinates, intrinsic mesh mapping, stage-transfer admission, numerical convergence and physical validation are not established. The preceding fine-mesh run produced a real 45MB field result but its public reply timed out; its runner UNKNOWN remains preserved and current-host quiescence was separately verified. It is not retroactively counted as a complete chain.

The preceding `a3c2af8` artifact job remains UNKNOWN: its public ENGINE_UNRESPONSIVE reply omitted the ambiguity flag, leading the runner to complete cleanup. Later read-only complete process/listener inventories confirmed current-host quiet; the original UNKNOWN remains unchanged. The missing-session and deterministic no-service refusal repairs passed 125 software tests and are included in the latest candidate.

Earlier failures remain preserved. Source `3133136` created native `mcp1` but its callback failed before returning a ModelRef; that `model_create` remains UNKNOWN. An exact-bound read-only check found 11/11 Worker requests terminal and complete process/listener inventories quiet; do not reuse or clean that session. The still-earlier source `3160730` stop UNKNOWN received one successful `session.recover` proving current-host quiescence only; original result retained, source job RECONCILING, lifecycle STOPPED revision 9. Neither historical UNKNOWN is relabeled PASS. Their evidence remains in the same checkpoint.

## Current Windows 6.3 state

**PARTIAL — startup/connect/model/mesh passed; solve UNKNOWN, readback NOT_RUN.** New run `w21-63-20260929T140237Z-f4004a44`, source `18154c6`, COMSOL 6.3.0 build 290, exited 2 after an actual transient-solver failure: it could not create a native solution file. The measured path is 271 UTF-16 units before NUL; the existing 260-unit preflight missed COMSOL's nested recovery directory and solution filename. One solve was dispatched; no result read or cleanup followed. Read-only evidence confirms 19/19 Worker requests terminal and complete process/listener inventories quiet at 14:07:38Z. Preserve the original UNKNOWN; this does not establish owned cleanup or scientific acceptance. [Exact failure and resume evidence](docs/full_project_execution/project_session/WINDOWS64_V2_SESSION_START_UNKNOWN_CHECKPOINT.json#canonical_native_chain_windows63_attempt_20260929T140237Z).

The earlier 6.3 interrupted start was safely reconciled by one public `session.recover`: current lifecycle STOPPED revision 3, historical job UNKNOWN, zero replay and no new Worker. The subsequent run confirms real start/connect success after the missing start RPC wait parameter was supplied; it does not prove every detail of the prior transport failure's cause.

## Accepted software evidence

The latest shared Windows path repair passed 298 tests across session context, Server, daemon sessions and runner (0 failures/errors/skips). It preserves legacy roots and checks the native recovery-file path under the unchanged 260-unit limit. The preceding start RPC wait-parameter repair passed 118 runner tests (0 failures/errors/skips), including an assertion on the actual start request. Prior longer-path test attempts failed fixture path budgets and remain on SSD; the final short-path run passed. The preceding bounded-observation repair also passed 118 runner tests, including 6 focused cases. The preceding path/Worker-envelope/revision repair passed 127 affected tests, including real production registration, result-construction and ledger paths. Earlier backend, session, project and recovery checks remain in the [software checkpoint](docs/full_project_execution/project_session/PRODUCTION_RUNNER_SOFTWARE_CHECKPOINT.json), with reproduction commands and hashes. Counts overlap and are not additive. Software tests alone do not establish native or physical acceptance; the scoped native result is recorded separately above.

## Next action

1. Shared path candidate `45399a5` passed 298 software tests and the full Windows 6.4 native chain.
2. Bind the new actual 6.4 receipt and complete Windows 6.3 solve/readback/cleanup with the same source and unchanged budgets.
3. Preserve both historical UNKNOWN attempts and separate metadata/mapping/physical gaps; unrelated capability expansion stays paused this round.

## Remaining project scope

W23 remains PARTIAL: native geometry/PML compatibility, Qabs production and optics are open. W24 remains PARTIAL: native physical-control solves and physical validation are open. W25 platform/Desktop acceptance and W26 Windows delivery/release acceptance remain PARTIAL. Both Mac COMSOL 6.3 targets (`macos-arm64` and `macos-x86_64`) are `USER_REQUESTED_SKIP` with acceptance cells `NOT_RUN`, per the [scope ledger](docs/full_project_execution/state/TASKS.json); this does not waive Windows 6.3/6.4 or either Mac 6.4 target. Intel Mac 6.4 availability is unconfirmed.

The master goal is not blanket-accepted by the software history. The final independent Reviewer remains unstarted. Storage migration preserved 619 inactive paths and 7 occupied roots remain retained; see [TMP migration summary](docs/full_project_execution/state/TMP_MIGRATION_SUMMARY.json). Detailed historical test receipts, failed attempts and recovery evidence remain in the linked canonical checkpoints and task ledger rather than being duplicated here.
