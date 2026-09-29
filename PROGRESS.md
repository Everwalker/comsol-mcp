# COMSOL MCP full-project progress

**Current priority:** pause capability expansion for this round and complete only the canonical Windows 6.4 → 6.3 solve/readback chain. **Overall PARTIAL.** The master goal still covers 26 work packages, 272 actions, 60 validation targets and 6 target environments; the final independent Reviewer has not started. The acceptance map and task ledger remain in [MAIN_ACCEPTANCE_PLAN](docs/full_project_execution/MAIN_ACCEPTANCE_PLAN.md), [TASKS.json](docs/full_project_execution/state/TASKS.json) and [RESUME.json](docs/full_project_execution/state/RESUME.json).

## Current Windows 6.4 state

**PARTIAL:** latest native run `w21-64-20260929T124807Z-0c308f9c`, source `cda6c11`, freeze `1871a836…614579`, exited 2. Project/session setup, model creation/inspection, fixture registration and native Java construction all succeeded: geometry and mesh each ran once, and the heat-transfer fixture returned BUILT_NOT_SOLVED. Probe registration and execution succeeded, but its business payload reports `OUTPUT_LIMIT_EXCEEDED / TABLE_ROW_LIMIT_EXCEEDED`, `payload_complete=false`, `native_admission=UNVERIFIED`. The runner stopped on this incomplete metadata observation before study/solver/tuple/field dispatch; Windows 6.3 remains **NOT_RUN**.

Owned cleanup completed: exact Server child reaped, exit confirmed, listener absent, Worker retired. No UNKNOWN occurred in this attempt. Complete-probe/native admission is not established. Exact request/result identities, bounded payload and evidence: [latest native checkpoint](docs/full_project_execution/project_session/WINDOWS64_V2_SESSION_START_UNKNOWN_CHECKPOINT.json#native_attempt_20260929T124807Z).

The preceding `a3c2af8` artifact job remains UNKNOWN: its public ENGINE_UNRESPONSIVE reply omitted the ambiguity flag, leading the runner to complete cleanup. Later read-only complete process/listener inventories confirmed current-host quiet; the original UNKNOWN remains unchanged. The missing-session and deterministic no-service refusal repairs passed 125 software tests and are included in the latest candidate.

Earlier failures remain preserved. Source `3133136` created native `mcp1` but its callback failed before returning a ModelRef; that `model_create` remains UNKNOWN. An exact-bound read-only check found 11/11 Worker requests terminal and complete process/listener inventories quiet; do not reuse or clean that session. The still-earlier source `3160730` stop UNKNOWN received one successful `session.recover` proving current-host quiescence only; original result retained, source job RECONCILING, lifecycle STOPPED revision 9. Neither historical UNKNOWN is relabeled PASS. Their evidence remains in the same checkpoint.

## Accepted software evidence

The session model-selection repair and UNKNOWN-code handling passed 177 backend/session/store tests and 110 runner tests. The additional authoritative project-binding repair passed 21 daemon/project/backend tests and now passes native model creation and inspection; solve/readback is still unexecuted. Earlier scoped software evidence: localized version parsing 109 tests, Windows listener enumeration 25, stop-only recovery 149, probe integration 98. Counts overlap and must not be added as a unique total. These results do not establish field, physical or overall acceptance. Reproduction commands and hashes: [software checkpoint](docs/full_project_execution/project_session/PRODUCTION_RUNNER_SOFTWARE_CHECKPOINT.json).

## Next action

1. Bounded-observation handling is main-reviewed scoped software PASS: 118 runner tests passed (including 6 focused cases). Solve-readback preserves only the exact known table-row-limit envelope as RAW_UNINTERPRETED/PARTIAL; metadata-only and unknown errors still reject it. Java limits and 6.3 receipt criteria are unchanged. Native after this repair is NOT_RUN.
2. Canonical runner path/Worker-envelope/revision repairs passed 127 affected software tests and now passed native fixture/probe execution. Run a separate 6.4 solve/readback/cleanup candidate.
3. After a verified successful 6.4 receipt, execute Windows 6.3. Unrelated capability expansion stays paused this round.

## Remaining project scope

W23 remains PARTIAL: native geometry/PML compatibility, Qabs production and optics are open. W24 remains PARTIAL: native physical-control solves and physical validation are open. W25 platform/Desktop acceptance and W26 Windows delivery/release acceptance remain PARTIAL. Both Mac COMSOL 6.3 targets (`macos-arm64` and `macos-x86_64`) are `USER_REQUESTED_SKIP` with acceptance cells `NOT_RUN`, per the [scope ledger](docs/full_project_execution/state/TASKS.json); this does not waive Windows 6.3/6.4 or either Mac 6.4 target. Intel Mac 6.4 availability is unconfirmed.

The master goal is not blanket-accepted by the software history. The final independent Reviewer remains unstarted. Storage migration preserved 619 inactive paths and 7 occupied roots remain retained; see [TMP migration summary](docs/full_project_execution/state/TMP_MIGRATION_SUMMARY.json). Detailed historical test receipts, failed attempts and recovery evidence remain in the linked canonical checkpoints and task ledger rather than being duplicated here.
