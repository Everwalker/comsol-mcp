# COMSOL MCP full-project progress

Updated 2026-09-27 UTC. **Overall PARTIAL; execution continues through W26.** This is the sole progress entry. Resume from [RESUME.json](docs/full_project_execution/state/RESUME.json), [TASKS.json](docs/full_project_execution/state/TASKS.json) and the frozen [acceptance plan](docs/full_project_execution/MAIN_ACCEPTANCE_PLAN.md). Original scope remains 26 work packages / 272 actions / 60 tests across six targets; the additional v2 operation makes the catalog 273 entries, not a replacement scope.

## Current stage

**F04 no-ModelRef lifecycle recovery: PASS for scoped software; F04/project overall PARTIAL.** Recovery now requires exact original process/Worker/request identity and consumes existing close/reap evidence. SQLite atomically records lifecycle state and proof while preserving original UNKNOWN results. Missing live context remains explicit.

**Actually executed:** isolated published `b484a735` plus three-file overlay: **164 PASS / 0 FAIL / 0 ERROR / 0 SKIP**, plus the separately run historical Git-blob test **1 PASS**. Compilation passed. Main review verified 249 unchanged source files and all three overlay files, receipt/JUnit hashes, and independently reran four identity/retirement controls. [Compact evidence and replay](docs/full_project_execution/project_session/LIFECYCLE_RECOVERY_CHECKPOINT.json).

**COMSOL/native NOT_RUN.** Live context reconstruction and Server-lifecycle UNKNOWN recovery remain incomplete. Main review rejected two earlier candidates and verified their repairs; this scoped review is not the final independent overall review.

W23 single-BMA producer software was published as `b484a735` (134 tests); paired-field mapping continues. W24 setup was published as `21ff75c` (53 tests); new-epoch readmission/budget identity repair is awaiting main review. Automatic approval rejected local cleanup SIGTERM; explicit authorization remains pending, with no cleanup or relaunch.

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

- **F04:** connect/disconnect/reconnect software and recovery readback accepted. Owned Server and model-bound quiescence recovery software are accepted; no-ModelRef lifecycle recovery software is now accepted; live-context/Server recovery and managed native validation remain open.
- **W24:** atomic admission (78 tests), sensitivity (96 tests), and setup campaign (53 tests) are scoped software checkpoints; full native science remains incomplete and no old solve budget is borrowed.
- **W23:** single-BMA producer software is accepted; actual native producer execution and field mapping remain next. Two basis vectors are distinct eigensolutions of one receiver BMA, not two physical Ports. Managed native optics and tolerance/convergence acceptance remain open.
- **W24 historical attempts:** 0841Z freeze failed before engine birth (0 Workers/solves). Earlier ExternalStrain failure remains FAIL/UNKNOWN with safe_retry=false. Preserve these records; do not replay old freezes or reset budgets.
- **W01–W22:** existing implementation/evidence is not a blanket completed acceptance. **W25/W26:** Desktop/platform/release matrix acceptance remains incomplete. Final independent overall Reviewer has **not started**.
- **Platform scope:** macOS COMSOL 6.3 arm64 and x86_64 are USER_REQUESTED_SKIP, with acceptance NOT_RUN. Windows 6.3/6.4 and both Mac 6.4 targets remain required; Intel Mac 6.4 availability remains unconfirmed.

## Next action and recovery

Continue the three Luna Max work streams: implement F16 canonical durable experiment reads; implement W23 paired-field mode mapping; review W24 new-epoch readmission budget identity and complete science orchestration. Publish each accepted software stage immediately. Then freeze isolated managed native candidates, execute their reviewed scope, complete all remaining W01–W26 work, and run a fresh independent overall review with repair/retest until accepted.

The latest focused replay is in [LIFECYCLE_RECOVERY_CHECKPOINT.json](docs/full_project_execution/project_session/LIFECYCLE_RECOVERY_CHECKPOINT.json). Recover from GitHub and existing state. Last previously verified GitHub sync: `b484a735b7460c3780ff6a02329d6781a1faadce`; this stage is recorded as synchronized only after post-push fetch and HEAD/origin-main equality.
