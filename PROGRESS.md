# COMSOL MCP full-project progress

**Current priority:** pause capability expansion for this round and complete only the canonical Windows 6.4 → 6.3 solve/readback chain. **Overall PARTIAL.** The master goal still covers 26 work packages, 272 actions, 60 validation targets and 6 target environments; the final independent Reviewer has not started. The acceptance map and task ledger remain in [MAIN_ACCEPTANCE_PLAN](docs/full_project_execution/MAIN_ACCEPTANCE_PLAN.md), [TASKS.json](docs/full_project_execution/state/TASKS.json) and [RESUME.json](docs/full_project_execution/state/RESUME.json).

## Current Windows 6.4 state

**PARTIAL:** latest native run `w21-64-20260929T114231Z-5715139c`, source `a4dfc98`, freeze `9a45250f…fc945`, completed with exit 2. Public project creation, start, connect and model creation **succeeded**. The returned ModelRef binds model `mcp1` to the expected session and Server, revision 0. The runner then refused the missing `execution.project_id` before model inspection, fixture, solve or readback. Study, solver, tuple and field counts remain zero; Windows 6.3 is **NOT_RUN**.

Failure cleanup **succeeded**: Worker retired; exact owned Server PID 32744 was reaped, exit confirmed and listener absent. The durable stop job succeeded with return code 1 recorded as process termination evidence; lifecycle is STOPPED at revision 8. No UNKNOWN operation arose in this attempt. This also supplies the first successful native owned-stop check after the Windows empty-listener repair. Details, exact request/result identities, hashes and local reproduction evidence: [latest native checkpoint](docs/full_project_execution/project_session/WINDOWS64_V2_SESSION_START_UNKNOWN_CHECKPOINT.json#native_attempt_20260929T114231Z).

Earlier failures remain preserved. Source `3133136` created native `mcp1` but its callback failed before returning a ModelRef; that `model_create` remains UNKNOWN. An exact-bound read-only check found 11/11 Worker requests terminal and complete process/listener inventories quiet; do not reuse or clean that session. The still-earlier source `3160730` stop UNKNOWN received one successful `session.recover` proving current-host quiescence only; original result retained, source job RECONCILING, lifecycle STOPPED revision 9. Neither historical UNKNOWN is relabeled PASS. Their evidence remains in the same checkpoint.

## Accepted software evidence

The session model-selection repair and UNKNOWN-code handling passed 177 backend/session/store tests and 110 runner tests. The latest native run now confirms the returned ModelRef works through model creation; solve/readback is still unexecuted. Earlier scoped software evidence: localized version parsing 109 tests, Windows listener enumeration 25, stop-only recovery 149, probe integration 98. Counts overlap and must not be added as a unique total. These results do not establish field, physical or overall acceptance. Reproduction commands and hashes: [software checkpoint](docs/full_project_execution/project_session/PRODUCTION_RUNNER_SOFTWARE_CHECKPOINT.json).

## Next action

1. The authoritative project-binding repair is main-reviewed scoped software PASS: 21 unique daemon/project/backend tests passed, including actual dispatch create/load/inspect and a foreign-mapping negative case. Native execution after this fix is NOT_RUN.
2. Package the reviewed change and run a separate Windows 6.4 start → connect → small-model solve → result readback → owned cleanup candidate. Preserve every earlier attempt.
3. After a verified successful 6.4 receipt, execute the equivalent Windows 6.3 chain. Pause unrelated capability expansion this round.

## Remaining project scope

W23 remains PARTIAL: native geometry/PML compatibility, Qabs production and optics are open. W24 remains PARTIAL: native physical-control solves and physical validation are open. W25 platform/Desktop acceptance and W26 Windows delivery/release acceptance remain PARTIAL. Both Mac COMSOL 6.3 targets (`macos-arm64` and `macos-x86_64`) are `USER_REQUESTED_SKIP` with acceptance cells `NOT_RUN`, per the [scope ledger](docs/full_project_execution/state/TASKS.json); this does not waive Windows 6.3/6.4 or either Mac 6.4 target. Intel Mac 6.4 availability is unconfirmed.

The master goal is not blanket-accepted by the software history. The final independent Reviewer remains unstarted. Storage migration preserved 619 inactive paths and 7 occupied roots remain retained; see [TMP migration summary](docs/full_project_execution/state/TMP_MIGRATION_SUMMARY.json). Detailed historical test receipts, failed attempts and recovery evidence remain in the linked canonical checkpoints and task ledger rather than being duplicated here.
