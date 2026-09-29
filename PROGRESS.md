# COMSOL MCP full-project progress

**Current priority:** pause capability expansion for this round and complete only the canonical Windows 6.4 → 6.3 solve/readback chain. **Overall PARTIAL.** The master goal still covers 26 work packages, 272 actions, 60 validation targets and 6 target environments; the final independent Reviewer has not started. The acceptance map and task ledger remain in [MAIN_ACCEPTANCE_PLAN](docs/full_project_execution/MAIN_ACCEPTANCE_PLAN.md), [TASKS.json](docs/full_project_execution/state/TASKS.json) and [RESUME.json](docs/full_project_execution/state/RESUME.json).

## Current Windows 6.4 state

**PARTIAL:** latest native run `w21-64-20260929T121941Z-f2ce9cb2`, source `bc0563e`, freeze `01b19371…ffcdb3`, exited 2. Project creation, start, connect, model creation and inspection succeeded. Fixture registration then deterministically **FAILED**: runner supplied an absolute path, while production requires a project-relative path without traversal. The session binding repair passed its runtime guard. No fixture execution, study, solver, tuple or field read occurred; Windows 6.3 remains **NOT_RUN**.

Owned cleanup completed: exact Server child reaped, exit confirmed, listener absent, Worker retired, durable lifecycle STOPPED. No UNKNOWN occurred in this attempt. Exact request/result identities and evidence: [latest native checkpoint](docs/full_project_execution/project_session/WINDOWS64_V2_SESSION_START_UNKNOWN_CHECKPOINT.json#native_attempt_20260929T121941Z).

The preceding `a3c2af8` artifact job remains UNKNOWN: its public ENGINE_UNRESPONSIVE reply omitted the ambiguity flag, leading the runner to complete cleanup. Later read-only complete process/listener inventories confirmed current-host quiet; the original UNKNOWN remains unchanged. The missing-session and deterministic no-service refusal repairs passed 125 software tests and are included in the latest candidate.

Earlier failures remain preserved. Source `3133136` created native `mcp1` but its callback failed before returning a ModelRef; that `model_create` remains UNKNOWN. An exact-bound read-only check found 11/11 Worker requests terminal and complete process/listener inventories quiet; do not reuse or clean that session. The still-earlier source `3160730` stop UNKNOWN received one successful `session.recover` proving current-host quiescence only; original result retained, source job RECONCILING, lifecycle STOPPED revision 9. Neither historical UNKNOWN is relabeled PASS. Their evidence remains in the same checkpoint.

## Accepted software evidence

The session model-selection repair and UNKNOWN-code handling passed 177 backend/session/store tests and 110 runner tests. The additional authoritative project-binding repair passed 21 daemon/project/backend tests and now passes native model creation and inspection; solve/readback is still unexecuted. Earlier scoped software evidence: localized version parsing 109 tests, Windows listener enumeration 25, stop-only recovery 149, probe integration 98. Counts overlap and must not be added as a unique total. These results do not establish field, physical or overall acceptance. Reproduction commands and hashes: [software checkpoint](docs/full_project_execution/project_session/PRODUCTION_RUNNER_SOFTWARE_CHECKPOINT.json).

## Next action

1. Canonical runner contract repair is scoped software PASS: project-relative paths, actual Worker envelope decoding and trusted-code revision progression; 127 affected tests passed, including real production route/service constructors. Native after this fix is NOT_RUN.
2. After targeted regression and main review, sync the stage and execute a separate Windows 6.4 canonical solve/readback/cleanup candidate.
3. After a verified successful 6.4 receipt, execute Windows 6.3. Unrelated capability expansion stays paused this round.

## Remaining project scope

W23 remains PARTIAL: native geometry/PML compatibility, Qabs production and optics are open. W24 remains PARTIAL: native physical-control solves and physical validation are open. W25 platform/Desktop acceptance and W26 Windows delivery/release acceptance remain PARTIAL. Both Mac COMSOL 6.3 targets (`macos-arm64` and `macos-x86_64`) are `USER_REQUESTED_SKIP` with acceptance cells `NOT_RUN`, per the [scope ledger](docs/full_project_execution/state/TASKS.json); this does not waive Windows 6.3/6.4 or either Mac 6.4 target. Intel Mac 6.4 availability is unconfirmed.

The master goal is not blanket-accepted by the software history. The final independent Reviewer remains unstarted. Storage migration preserved 619 inactive paths and 7 occupied roots remain retained; see [TMP migration summary](docs/full_project_execution/state/TMP_MIGRATION_SUMMARY.json). Detailed historical test receipts, failed attempts and recovery evidence remain in the linked canonical checkpoints and task ledger rather than being duplicated here.
