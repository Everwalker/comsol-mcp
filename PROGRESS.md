# COMSOL MCP full-project progress

Updated 2026-09-28 UTC. **Overall PARTIAL; execution continues through W26.** This is the sole progress entry. Resume from [RESUME.json](docs/full_project_execution/state/RESUME.json), [TASKS.json](docs/full_project_execution/state/TASKS.json) and the frozen [acceptance plan](docs/full_project_execution/MAIN_ACCEPTANCE_PLAN.md). Original scope remains 26 work packages / 272 actions / 60 tests across six targets; the additional v2 operation makes the catalog 273 entries, not a replacement scope.

## Current stage

**W21 immutable stage-definition route: PASS for scoped software; W21/project overall PARTIAL.** `experiment.stage_define` now registers a complete immutable plan through public MCP, direct and fallback routes. Plans and unique stage indexes persist in one SQLite transaction, bound to project and full ModelRef; revision is separate. Multiple disjoint plans retry correctly after revision changes and database reopen. Registration invokes no Worker or solve and returns DECLARED_UNVERIFIED.

**Actually executed:** 267 isolated tests PASS with two documented exclusions; main verified nine source files and306 unchanged dependencies, reran25 critical tests PASS and reproduced the original multi-plan bug as fixed. [Compact evidence and replay](docs/full_project_execution/project_session/STAGE_DEFINITION_SOFTWARE_CHECKPOINT.json). The historical Git-object test passed separately; the `job.list` cursor assertion fails on both clean base and candidate and remains an explicit unresolved defect.

**Original stage_run/state_map and native variable/reference-state/continuity validation remain incomplete.** Platform/T021/T047 cells are unchanged. W24 v3 internal-state software is frozen for main review; W23 native CV power-integration evidence is being implemented. Native process cleanup authorization remains pending following automatic approval rejection.

Latest previously published milestones: W23 explicit maps `dd452a6` (9 isolated/9 main tests,798 offline expression checks); W24 saved producer `d181e61` (142 isolated/142 integration tests); F16 objective/case artifacts `6dba12d`. These do not establish whole-project or scientific acceptance.

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

- **F16 original action scope:** objective/constraint reporting and case-artifact binding passed scoped software checks. Authentic native case reload, weighted evidence, state-transfer/history/conservation and full original acceptance remain incomplete; action coverage is not blanket upgraded.
- **F04:** connect/disconnect/reconnect software and recovery readback accepted. Owned Server and model-bound quiescence recovery software are accepted; no-ModelRef lifecycle recovery software is now accepted; live-context/Server recovery and managed native validation remain open.
- **W24:** atomic admission (78 tests), sensitivity (96 tests), and setup campaign (53 tests) are scoped software checkpoints; full native science remains incomplete and no old solve budget is borrowed.
- **W23:** single-BMA producer software is accepted; actual native producer execution and field mapping remain next. Two basis vectors are distinct eigensolutions of one receiver BMA, not two physical Ports. Managed native optics and tolerance/convergence acceptance remain open.
- **W24 historical attempts:** 0841Z freeze failed before engine birth (0 Workers/solves). Earlier ExternalStrain failure remains FAIL/UNKNOWN with safe_retry=false. Preserve these records; do not replay old freezes or reset budgets.
- **W01–W22:** existing implementation/evidence is not a blanket completed acceptance. **W25/W26:** Desktop/platform/release matrix acceptance remains incomplete. Final independent overall Reviewer has **not started**.
- **Platform scope:** macOS COMSOL 6.3 arm64 and x86_64 are USER_REQUESTED_SKIP, with acceptance NOT_RUN. Windows 6.3/6.4 and both Mac 6.4 targets remain required; Intel Mac 6.4 availability remains unconfirmed.

## Next action and recovery

Continue the three Luna Max work streams: complete F16 weighted native evidence and original experiment reporting/model gaps; complete W23 radiation geometry, capture/quadrature/power-balance and original optics acceptance; complete W24 UV/gel/reference/viscoelastic implementation and remaining science. Publish each accepted software stage immediately. Then freeze isolated managed native candidates, execute their reviewed scope, complete all remaining W01–W26 work, and run a fresh independent overall review with repair/retest until accepted.

The latest focused replay is in [STAGE_DEFINITION_SOFTWARE_CHECKPOINT.json](docs/full_project_execution/project_session/STAGE_DEFINITION_SOFTWARE_CHECKPOINT.json). Recover from GitHub and existing state. Last previously verified GitHub sync: dd452a6c63bb37d7e763b91d2528b75e91f8a5e2; this stage is recorded as synchronized only after post-push fetch and HEAD/origin-main equality.
