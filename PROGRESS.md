# COMSOL MCP full-project progress

**Current priority:** pause capability expansion for this round and complete only the canonical Windows 6.4 → 6.3 solve/readback chain. **Overall PARTIAL.** The master goal still covers 26 work packages, 272 actions, 60 validation targets and 6 target environments; the final independent Reviewer has not started. The acceptance map and task ledger remain in [MAIN_ACCEPTANCE_PLAN](docs/full_project_execution/MAIN_ACCEPTANCE_PLAN.md), [TASKS.json](docs/full_project_execution/state/TASKS.json) and [RESUME.json](docs/full_project_execution/state/RESUME.json).

## Current Windows 6.4 state

**PARTIAL:** latest native run `w21-64-20260929T130508Z-abd555f3`, source `30a3f10`, freeze `1251c40a…5df79d`, exited 2. Native fixture construction, one study/solver execution and solution-index read succeeded. A read-only durable lookup confirms the original temperature-field job also **SUCCEEDED**: T in K, shape `[1,1,5,70874]`, exact session/ModelRef at revision 4, temporary Eval node verified removed. The persisted result is 45,447,416 bytes.

Public field delivery timed out, so runner state remains **UNKNOWN** and owned lifecycle cleanup was not dispatched. No result reread or replay was issued. A subsequent read-only check verified all 69 Worker requests terminal with matching IDs/hashes; complete process and 56-row listener inventories had zero task/canonical/unresolved matches. This establishes current-host quiet, not historical owned-exit or full-chain PASS. The original runner UNKNOWN is preserved. Exact evidence: [latest native checkpoint](docs/full_project_execution/project_session/WINDOWS64_V2_SESSION_START_UNKNOWN_CHECKPOINT.json#native_attempt_20260929T130508Z). Windows 6.3 remains **NOT_RUN**.

The auxiliary structural probe is explicitly RAW_UNINTERPRETED/PARTIAL (`TABLE_ROW_LIMIT_EXCEEDED`); its limits and complete metadata-only acceptance remain unchanged. Native field production is now demonstrated, while public delivery and canonical cleanup remain incomplete.

The preceding `a3c2af8` artifact job remains UNKNOWN: its public ENGINE_UNRESPONSIVE reply omitted the ambiguity flag, leading the runner to complete cleanup. Later read-only complete process/listener inventories confirmed current-host quiet; the original UNKNOWN remains unchanged. The missing-session and deterministic no-service refusal repairs passed 125 software tests and are included in the latest candidate.

Earlier failures remain preserved. Source `3133136` created native `mcp1` but its callback failed before returning a ModelRef; that `model_create` remains UNKNOWN. An exact-bound read-only check found 11/11 Worker requests terminal and complete process/listener inventories quiet; do not reuse or clean that session. The still-earlier source `3160730` stop UNKNOWN received one successful `session.recover` proving current-host quiescence only; original result retained, source job RECONCILING, lifecycle STOPPED revision 9. Neither historical UNKNOWN is relabeled PASS. Their evidence remains in the same checkpoint.

## Accepted software evidence

The session model-selection repair and UNKNOWN-code handling passed 177 backend/session/store tests and 110 runner tests. The additional authoritative project-binding repair passed 21 daemon/project/backend tests and now passes native model creation and inspection; solve/readback is still unexecuted. Earlier scoped software evidence: localized version parsing 109 tests, Windows listener enumeration 25, stop-only recovery 149, probe integration 98. Counts overlap and must not be added as a unique total. These results do not establish field, physical or overall acceptance. Reproduction commands and hashes: [software checkpoint](docs/full_project_execution/project_session/PRODUCTION_RUNNER_SOFTWARE_CHECKPOINT.json).

## Next action

1. Public wait is 45s; the field job persisted success in ~20.2s. Public result.evaluate cannot filter outer/inner tuples. The new smoke candidate changes only mesh hauto 2 → 9, retaining geometry, physics, materials, time grid and complete field read; no timeout/limit increase or convergence claim. Native after this fixture change is NOT_RUN.
2. The bounded-observation repair passed 118 runner tests and now reached native solve/field production. The fixture parameter received main diff inspection; its next native run must validate public readback and owned cleanup.
3. After a complete verified 6.4 canonical receipt, execute Windows 6.3. Unrelated capability expansion remains paused; overall project acceptance stays PARTIAL.

## Remaining project scope

W23 remains PARTIAL: native geometry/PML compatibility, Qabs production and optics are open. W24 remains PARTIAL: native physical-control solves and physical validation are open. W25 platform/Desktop acceptance and W26 Windows delivery/release acceptance remain PARTIAL. Both Mac COMSOL 6.3 targets (`macos-arm64` and `macos-x86_64`) are `USER_REQUESTED_SKIP` with acceptance cells `NOT_RUN`, per the [scope ledger](docs/full_project_execution/state/TASKS.json); this does not waive Windows 6.3/6.4 or either Mac 6.4 target. Intel Mac 6.4 availability is unconfirmed.

The master goal is not blanket-accepted by the software history. The final independent Reviewer remains unstarted. Storage migration preserved 619 inactive paths and 7 occupied roots remain retained; see [TMP migration summary](docs/full_project_execution/state/TMP_MIGRATION_SUMMARY.json). Detailed historical test receipts, failed attempts and recovery evidence remain in the linked canonical checkpoints and task ledger rather than being duplicated here.
