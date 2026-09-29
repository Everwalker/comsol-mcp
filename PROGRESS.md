# COMSOL MCP full-project progress

**Current priority:** pause capability expansion for this round and complete only the canonical Windows 6.4 → 6.3 solve/readback chain. **Overall PARTIAL.** The master goal still covers 26 work packages, 272 actions, 60 validation targets and 6 target environments; the final independent Reviewer has not started. The acceptance map and task ledger remain in [MAIN_ACCEPTANCE_PLAN](docs/full_project_execution/MAIN_ACCEPTANCE_PLAN.md), [TASKS.json](docs/full_project_execution/state/TASKS.json) and [RESUME.json](docs/full_project_execution/state/RESUME.json).

## Current Windows 6.4 state

**PASS — scoped canonical Windows 6.4 native chain.** Run `w21-64-20260929T132007Z-820b0f09`, runtime source `2bdcc75`, COMSOL 6.4.0 build 293, exited 0. Public MCP start/connect/model construction → one solve → exact solution-index read → full temperature field readback → owned cleanup all completed. Field shape is `[1,1,5,637]`, unit readback K, selected tuple `(outer=1, inner=5, solnum=5)`; selected values are approximately 300–304.380 K. The complete field response was 375,596 bytes under the unchanged 45s RPC wait and native budgets. Returned-field hashes and request/model/revision bindings were checked by the main Agent.

Cleanup is proven for the exact owned Server PID 2796/birth: child reaped, exit confirmed, listener absent; Worker retired and temporary Eval node verified removed. [Canonical 6.4 checkpoint](docs/full_project_execution/project_session/WINDOWS64_V2_SESSION_START_UNKNOWN_CHECKPOINT.json#canonical_native_chain_windows64) contains the compact result, exact fingerprints, reproduction entry and local receipt identities. **Windows 6.3 was attempted and startup remains UNKNOWN**; solve/readback is NOT_RUN.

This is minimal coarse-mesh (`hauto=9`) production-chain acceptance only. The auxiliary structural probe remains RAW_UNINTERPRETED/PARTIAL. Actual spatial coordinates, intrinsic mesh mapping, stage-transfer admission, numerical convergence and physical validation are not established. The preceding fine-mesh run produced a real 45MB field result but its public reply timed out; its runner UNKNOWN remains preserved and current-host quiescence was separately verified. It is not retroactively counted as a complete chain.

The preceding `a3c2af8` artifact job remains UNKNOWN: its public ENGINE_UNRESPONSIVE reply omitted the ambiguity flag, leading the runner to complete cleanup. Later read-only complete process/listener inventories confirmed current-host quiet; the original UNKNOWN remains unchanged. The missing-session and deterministic no-service refusal repairs passed 125 software tests and are included in the latest candidate.

Earlier failures remain preserved. Source `3133136` created native `mcp1` but its callback failed before returning a ModelRef; that `model_create` remains UNKNOWN. An exact-bound read-only check found 11/11 Worker requests terminal and complete process/listener inventories quiet; do not reuse or clean that session. The still-earlier source `3160730` stop UNKNOWN received one successful `session.recover` proving current-host quiescence only; original result retained, source job RECONCILING, lifecycle STOPPED revision 9. Neither historical UNKNOWN is relabeled PASS. Their evidence remains in the same checkpoint.

## Current Windows 6.3 state

**PARTIAL / startup UNKNOWN.** Same runtime source, COMSOL 6.3.0 build 290, run `w21-63-20260929T132702Z-8406c97b`, actual runner exit 2. Native PID 4316 reached READY after 33.382s, but the exact start job remains RUNNING with no terminal result and lifecycle STARTING revision 1. No connect, solve, readback or cleanup followed. Read-only complete inventories at 13:38:38Z found no canonical/task process or listener and no unresolved candidates (56 listener rows). This proves current-host quiet only; original startup outcome is not relabeled PASS. Exact identities, hashes and local reproduction inputs are in the [same checkpoint](docs/full_project_execution/project_session/WINDOWS64_V2_SESSION_START_UNKNOWN_CHECKPOINT.json#canonical_native_chain_windows63_attempt_20260929T132702Z).

## Accepted software evidence

The latest bounded-observation repair passed 118 runner tests, including 6 focused cases. The preceding path/Worker-envelope/revision repair passed 127 affected tests, including real production registration, result-construction and ledger paths. Earlier backend, session, project and recovery checks remain in the [software checkpoint](docs/full_project_execution/project_session/PRODUCTION_RUNNER_SOFTWARE_CHECKPOINT.json), with reproduction commands and hashes. Counts overlap and are not additive. Software tests alone do not establish native or physical acceptance; the scoped native result is recorded separately above.

## Next action

1. Read task daemon diagnostics and establish the READY-to-terminal failure mechanism for the original 6.3 attempt.
2. Repair the necessary startup coordination only; preserve original UNKNOWN and use a separately frozen attempt after safe disposition. Complete 6.3 canonical solve/readback/cleanup under the existing budgets.
3. Keep metadata, mapping and physical acceptance separate; unrelated capability expansion stays paused this round.

## Remaining project scope

W23 remains PARTIAL: native geometry/PML compatibility, Qabs production and optics are open. W24 remains PARTIAL: native physical-control solves and physical validation are open. W25 platform/Desktop acceptance and W26 Windows delivery/release acceptance remain PARTIAL. Both Mac COMSOL 6.3 targets (`macos-arm64` and `macos-x86_64`) are `USER_REQUESTED_SKIP` with acceptance cells `NOT_RUN`, per the [scope ledger](docs/full_project_execution/state/TASKS.json); this does not waive Windows 6.3/6.4 or either Mac 6.4 target. Intel Mac 6.4 availability is unconfirmed.

The master goal is not blanket-accepted by the software history. The final independent Reviewer remains unstarted. Storage migration preserved 619 inactive paths and 7 occupied roots remain retained; see [TMP migration summary](docs/full_project_execution/state/TMP_MIGRATION_SUMMARY.json). Detailed historical test receipts, failed attempts and recovery evidence remain in the linked canonical checkpoints and task ledger rather than being duplicated here.
