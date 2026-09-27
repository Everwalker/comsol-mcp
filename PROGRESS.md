# COMSOL MCP full-project progress

Updated 2026-09-27 UTC. **Overall PARTIAL; execution continues through W26.** This is the sole progress entry. Resume from [RESUME.json](docs/full_project_execution/state/RESUME.json), [TASKS.json](docs/full_project_execution/state/TASKS.json) and the frozen [acceptance plan](docs/full_project_execution/MAIN_ACCEPTANCE_PLAN.md). Original scope remains 26 work packages / 272 actions / 60 tests across six targets; the additional v2 operation makes the catalog 273 entries, not a replacement scope.

## Current stage

**W23 setup runner repair: PASS for scoped software; W23 overall PARTIAL.** The runner now handles the actual helper process snapshot and binds numeric birth identity while its exact Popen is alive. Public job.list uses project-scoped, catalog-compatible 500-row pages with complete ledger checks.

**Actually executed:** isolated published `a365420` plus exact overlay, **50 PASS / 0 FAIL / 0 ERROR / 0 SKIP**. Tests use the actual process-snapshot helper on a harmless child and real ControlDaemon/SQLite dispatch with 1007 jobs across three pages, foreign-project filtering, UNKNOWN detection and invalid-limit refusal. Main review verified 102 source files, 99 unchanged published files, candidate/receipt and JUnit hashes. [Compact evidence and replay](docs/full_project_execution/w23_overlap/SETUP_RUNNER_REPAIR_CHECKPOINT.json).

**Native rerun/science NOT_RUN.** The earlier setup failed after creating a Server/control daemon, with no Worker or solver dispatched. Historical UNKNOWN and raw evidence remain intact. Cleanup has not run: automatic approval rejected SIGTERM for lack of explicit user authorization. The pending question scopes only those two task-owned processes; no workaround or relaunch is permitted.

F04 [owned Server software](docs/full_project_execution/project_session/OWNED_SERVER_CHECKPOINT.json) remains accepted at 212 PASS / 2 SKIP; full recovery is in progress. W24 setup candidate is being repaired after main review found a missing late-import cleanup dependency despite 40 tests passing; no native W24 execution.

## Latest accepted software baseline

| Area | Actual result and evidence | Remaining scope |
| --- | --- | --- |
| Full offline suite, frozen `6474bf4` | [2818 PASS / 9 SKIP / 0 FAIL / 0 ERROR](docs/full_project_execution/release/FULL_OFFLINE_6474BF4_CHECKPOINT.json); separately enabled loopback 1 PASS | Does not certify later changes; skips remain explicit |
| W23 registered v2 | [88 software tests and isolated wheel build/install smoke PASS](docs/full_project_execution/w23_overlap/REGISTERED_V2_CHECKPOINT.json) | Actual mode/field identity and optics |
| W23 aggregation | [95 combined software tests PASS](docs/full_project_execution/w23_overlap/AGGREGATION_CHECKPOINT.json), superseded by current 99-test normal suite | Supplied envelopes are not authenticated native evidence |
| F04 dispatch/control | [164 scoped software tests PASS](docs/full_project_execution/project_session/f04_dispatch_control_checkpoint_20260927.json) | Session mutations, process lifecycle and platform acceptance |
| W24 setup/history | [20 managed-setup tests](docs/full_project_execution/w24/MANAGED_SHAPE_CHECKPOINT.json), [12 capture tests plus Java compile](docs/full_project_execution/w24/HISTORY_CAPTURE_CHECKPOINT.json), [23 shape-metric tests](docs/full_project_execution/w24/SHAPE_METRICS_CHECKPOINT.json) PASS | Native setup/solve/field history, sensitivity, gel/cure/mechanics |

Earlier source checkpoints and failures remain in the existing state and linked summaries. Raw local evidence paths/hashes and rebuilding instructions are recorded there; runtime directories, compiled files and duplicate logs are not delivery artifacts.

## In progress and real limitations

- **F04:** connect/disconnect/reconnect software and recovery readback accepted. Next implement exact owned process lifecycle and full evidence-backed recovery, then managed native validation.
- **W24:** preliminary 52-test result remains historically rejected; the repaired scoped software now passes78 isolated tests above. Variant-aware sensitivity software is accepted above; full native science remains incomplete and no old solve budget is borrowed.
- **W23:** Port/BMA metadata software is accepted above; actual selected-solution producer and field mapping remain next. Two basis vectors are distinct eigensolutions of one receiver BMA, not two physical Ports. Managed native optics and tolerance/convergence acceptance remain open.
- **W24 historical attempts:** 0841Z freeze failed before engine birth (0 Workers/solves). Earlier ExternalStrain failure remains FAIL/UNKNOWN with safe_retry=false. Preserve these records; do not replay old freezes or reset budgets.
- **W01–W22:** existing implementation/evidence is not a blanket completed acceptance. **W25/W26:** Desktop/platform/release matrix acceptance remains incomplete. Final independent overall Reviewer has **not started**.
- **Platform scope:** macOS COMSOL 6.3 arm64 and x86_64 are USER_REQUESTED_SKIP, with acceptance NOT_RUN. Windows 6.3/6.4 and both Mac 6.4 targets remain required; Intel Mac 6.4 availability remains unconfirmed.

## Next action and recovery

Continue the three Luna Max work streams: implement remaining session lifecycle routes; implement W23 actual mode provenance; implement W24 sensitivity variants and complete science orchestration. Publish each accepted software stage immediately. Then freeze isolated managed native candidates, execute their reviewed scope, complete all remaining W01–W26 work, and run a fresh independent overall review with repair/retest until accepted.

The latest focused replay is in [SETUP_RUNNER_REPAIR_CHECKPOINT.json](docs/full_project_execution/w23_overlap/SETUP_RUNNER_REPAIR_CHECKPOINT.json). Recover from the GitHub checkout and existing state; raw local paths are supporting artifacts, not prerequisite hidden source. Usage interruption was resolved and three replacement executors resumed without restarting native jobs. Last previously verified GitHub sync: `e520e7c04ae0427c7553aa85de271bcbc664d5d9`; current-stage synchronization is recorded only after post-push fetch and HEAD/origin-main equality verification.
