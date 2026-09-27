# COMSOL MCP full-project progress

Updated 2026-09-27 UTC. **Overall PARTIAL; execution continues through W26.** This is the sole progress entry. Resume from [RESUME.json](docs/full_project_execution/state/RESUME.json), [TASKS.json](docs/full_project_execution/state/TASKS.json) and the frozen [acceptance plan](docs/full_project_execution/MAIN_ACCEPTANCE_PLAN.md). Original scope remains 26 work packages / 272 actions / 60 tests across six targets; the additional v2 operation makes the catalog 273 entries, not a replacement scope.

## Current stage

**W24 setup campaign: PASS for scoped software; W24 and project overall PARTIAL.** Fourteen unsolved model slots now have managed save/reopen, dependency preflight, bounded resource admission and conservative cleanup. Process identity failures cannot produce a false quiescent inventory.

**Actually executed:** isolated published `a365420` plus two-file overlay, **53 PASS / 0 FAIL / 0 ERROR / 0 SKIP**, Python compilation and four-class Java offline compilation PASS. Main review checked two overlays, 107 published sources and receipt/freeze/JUnit hashes. Integration uses real ControlDaemon/SQLite with stub Worker/Popen; **native and scientific acceptance NOT_RUN**. [Compact evidence and replay](docs/full_project_execution/w24/SETUP_CAMPAIGN_CHECKPOINT.json).

**Preserved W23 resources remain pending authorization.** Earlier setup failed before Worker/solver dispatch. Automatic approval rejected SIGTERM; cleanup has not run, and the explicit user question is still pending. No relaunch or workaround. Current process inventory has not been refreshed.

F04 recovery candidate is under main review (141 isolated software tests passed); W23 independent single-BMA producer software is in progress. These do not establish native acceptance.

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

- **F04:** connect/disconnect/reconnect software and recovery readback accepted. Owned Server software is accepted; next review full evidence-backed recovery, then managed native validation.
- **W24:** atomic admission (78 tests), sensitivity (96 tests), and setup campaign (53 tests) are scoped software checkpoints; full native science remains incomplete and no old solve budget is borrowed.
- **W23:** Port/BMA metadata software is accepted above; actual selected-solution producer and field mapping remain next. Two basis vectors are distinct eigensolutions of one receiver BMA, not two physical Ports. Managed native optics and tolerance/convergence acceptance remain open.
- **W24 historical attempts:** 0841Z freeze failed before engine birth (0 Workers/solves). Earlier ExternalStrain failure remains FAIL/UNKNOWN with safe_retry=false. Preserve these records; do not replay old freezes or reset budgets.
- **W01–W22:** existing implementation/evidence is not a blanket completed acceptance. **W25/W26:** Desktop/platform/release matrix acceptance remains incomplete. Final independent overall Reviewer has **not started**.
- **Platform scope:** macOS COMSOL 6.3 arm64 and x86_64 are USER_REQUESTED_SKIP, with acceptance NOT_RUN. Windows 6.3/6.4 and both Mac 6.4 targets remain required; Intel Mac 6.4 availability remains unconfirmed.

## Next action and recovery

Continue the three Luna Max work streams: implement remaining session lifecycle routes; implement W23 actual mode provenance; implement W24 new-epoch model reload and complete science orchestration. Publish each accepted software stage immediately. Then freeze isolated managed native candidates, execute their reviewed scope, complete all remaining W01–W26 work, and run a fresh independent overall review with repair/retest until accepted.

The latest focused replay is in [SETUP_CAMPAIGN_CHECKPOINT.json](docs/full_project_execution/w24/SETUP_CAMPAIGN_CHECKPOINT.json). Recover from the GitHub checkout and existing state. Last previously verified GitHub sync: `3a7622fd4f27b6f27441de9d3b368254038baa8a`; this stage is recorded as synchronized only after post-push fetch and HEAD/origin-main equality.
