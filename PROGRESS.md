# COMSOL MCP full-project progress

Updated 2026-09-27 UTC. **Overall PARTIAL; execution continues through W26.** This is the sole progress entry. Resume from [RESUME.json](docs/full_project_execution/state/RESUME.json), [TASKS.json](docs/full_project_execution/state/TASKS.json) and the frozen [acceptance plan](docs/full_project_execution/MAIN_ACCEPTANCE_PLAN.md). Original scope remains 26 work packages / 272 actions / 60 tests across six targets; the additional v2 operation makes the catalog 273 entries, not a replacement scope.

## Current stage

**F16 durable experiment reads: PASS for scoped software; W21/project overall PARTIAL.** Canonical `experiment.inspect` and `experiment.case_result` read a project-authorized consistent SQLite snapshot without a live ModelRef or engine queue. Exact case IDs bind validated plan order and parameters; RUNNING, UNKNOWN and partial results remain explicit.

**Actually executed:** isolated published `ebb30ce` plus eight-file overlay: **139 PASS / 1 SKIP** across selected tests. The historical Git-object test initially failed in the source-only archive, then passed separately with read-only repository objects; its failure is retained. The skip is the opt-in loopback fixture. Main verified all 11,043 unchanged archive files, overlay/import/JUnit identities and reran **22 durable-read tests PASS**. Existing design/run callbacks were exercised with synthetic case execution and actual SQLite. [Compact evidence and replay](docs/full_project_execution/project_session/EXPERIMENT_READS_SOFTWARE_CHECKPOINT.json).

**COMSOL/native and scientific acceptance NOT_RUN/NOT_ESTABLISHED.** The first candidate returned mismatched case identity; main rejected it. The repaired candidate refuses the same counterexample with `EXPERIMENT_STATE_UNKNOWN`. No real solver result is certified by this software stage.

W24 lifecycle orchestration was published and post-push verified as `a4684f8` (158 tests plus 10 main retests); UV/gel/reference/viscoelastic work continues. W23 paired mapping was published as `dc25955` (171 tests plus 7 main retests); convergence/cohort work continues. Automatic approval rejected cleanup of the original failed native resources; explicit local termination authorization remains pending, with no cleanup/relaunch.

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

Continue the three Luna Max work streams: implement F16 five versioned metric actions and authorized historical comparisons; complete W23 optical overlap/convergence orchestration; complete W24 UV/gel/reference/viscoelastic implementation and remaining science. Publish each accepted software stage immediately. Then freeze isolated managed native candidates, execute their reviewed scope, complete all remaining W01–W26 work, and run a fresh independent overall review with repair/retest until accepted.

The latest focused replay is in [EXPERIMENT_READS_SOFTWARE_CHECKPOINT.json](docs/full_project_execution/project_session/EXPERIMENT_READS_SOFTWARE_CHECKPOINT.json). Recover from GitHub and existing state. Last previously verified GitHub sync: `a4684f8b9a024e13fa620ae8c726c7b5a5391e13`; this stage is recorded as synchronized only after post-push fetch and HEAD/origin-main equality.
