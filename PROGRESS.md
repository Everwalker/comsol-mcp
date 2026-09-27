# COMSOL MCP full-project progress

Updated 2026-09-27 UTC. **Overall PARTIAL; execution continues through W26.** This is the sole progress entry. Resume from [RESUME.json](docs/full_project_execution/state/RESUME.json), [TASKS.json](docs/full_project_execution/state/TASKS.json) and the frozen [acceptance plan](docs/full_project_execution/MAIN_ACCEPTANCE_PLAN.md). Original scope remains 26 work packages / 272 actions / 60 tests across six targets; the additional v2 operation makes the catalog 273 entries, not a replacement scope.

## Current stage

**W23 single-BMA producer: PASS for scoped software; W23/project overall PARTIAL.** A dedicated receiver BMA study preserves the original std3d baseline. Producer validation binds the actual bounded request and canonical hash through dispatch, durable operation/job and terminal result; successful writes require exact revision +1. Cleanup preserves observed results and failed BMA execution returns nonzero.

**Actually executed:** isolated published `3a7622f` plus six-file overlay, **134 PASS / 0 FAIL / 0 ERROR / 0 SKIP**, Java API compilation and javap PASS. Main review verified 105 source files (99 unchanged published), receipt/JUnit/compiled-class hashes and independently confirmed the previously accepted foreign request/key is now rejected. [Compact evidence and replay](docs/full_project_execution/w23_overlap/BMA_PRODUCER_SOFTWARE_CHECKPOINT.json).

**Native BMA and full optical validation NOT_RUN; field-to-basis mapping UNVERIFIED.** The 2700-second candidate budget is preparation only. Original failed setup and UNKNOWN evidence remain unchanged. Automatic approval rejected cleanup SIGTERM; explicit authorization remains pending, and no cleanup or relaunch has occurred.

F04 model-bound recovery was published as `7c91f8c` (143 isolated tests); no-ModelRef lifecycle recovery continues. W24 setup software was published as `21ff75c` (53 tests); new-epoch reload and the discovered model.inspect response-contract fix are being isolated and retested.

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

- **F04:** connect/disconnect/reconnect software and recovery readback accepted. Owned Server and model-bound quiescence recovery software are accepted; next implement no-ModelRef lifecycle recovery, then managed native validation.
- **W24:** atomic admission (78 tests), sensitivity (96 tests), and setup campaign (53 tests) are scoped software checkpoints; full native science remains incomplete and no old solve budget is borrowed.
- **W23:** single-BMA producer software is accepted; actual native producer execution and field mapping remain next. Two basis vectors are distinct eigensolutions of one receiver BMA, not two physical Ports. Managed native optics and tolerance/convergence acceptance remain open.
- **W24 historical attempts:** 0841Z freeze failed before engine birth (0 Workers/solves). Earlier ExternalStrain failure remains FAIL/UNKNOWN with safe_retry=false. Preserve these records; do not replay old freezes or reset budgets.
- **W01–W22:** existing implementation/evidence is not a blanket completed acceptance. **W25/W26:** Desktop/platform/release matrix acceptance remains incomplete. Final independent overall Reviewer has **not started**.
- **Platform scope:** macOS COMSOL 6.3 arm64 and x86_64 are USER_REQUESTED_SKIP, with acceptance NOT_RUN. Windows 6.3/6.4 and both Mac 6.4 targets remain required; Intel Mac 6.4 availability remains unconfirmed.

## Next action and recovery

Continue the three Luna Max work streams: implement remaining session lifecycle routes; implement W23 actual mode provenance; implement W24 new-epoch model reload and complete science orchestration. Publish each accepted software stage immediately. Then freeze isolated managed native candidates, execute their reviewed scope, complete all remaining W01–W26 work, and run a fresh independent overall review with repair/retest until accepted.

The latest focused replay is in [BMA_PRODUCER_SOFTWARE_CHECKPOINT.json](docs/full_project_execution/w23_overlap/BMA_PRODUCER_SOFTWARE_CHECKPOINT.json). Recover from GitHub and existing state. Last previously verified GitHub sync: `7c91f8ccb38660f962bfd8c45704eab49fea3200`; this stage is recorded as synchronized only after post-push fetch and HEAD/origin-main equality.
