# COMSOL MCP full-project progress

Updated 2026-09-28 UTC. **Overall PARTIAL; execution continues through W26.** This is the sole progress entry. Resume from [RESUME.json](docs/full_project_execution/state/RESUME.json), [TASKS.json](docs/full_project_execution/state/TASKS.json) and the frozen [acceptance plan](docs/full_project_execution/MAIN_ACCEPTANCE_PLAN.md). Original scope remains 26 work packages / 272 actions / 60 tests across six targets; the additional v2 operation makes the catalog 273 entries, not a replacement scope.

## Current stage

**W23 full-aperture conflict detection and control-volume topology adapter: PASS for scoped software; W23/project overall PARTIAL.** Shared vertex/edge geometry, connected and annular face partitions, Java flat-row normalization and closed-edge sampling are implemented. Full air apertures remain intact; incompatible tilted backing/PML sweeps are rejected.

**Actually executed:** published eb31e7f dependency plus three-file overlay: **56 isolated tests PASS and 56 main retests PASS**. Main verified source/dependency/evidence hashes and repeated both original counterexamples: disconnected faces rejected, annulus plus inner disk accepted. Java compilation and a synthetic proxy exercising production curve sampling passed. [Compact evidence and replay](docs/full_project_execution/w23_overlap/CV_TOPOLOGY_SOFTWARE_CHECKPOINT.json).

Earlier disconnected-closure, annular-regression and producer-alignment findings remain preserved. **Native geometry/solve NOT_RUN; topology and PML compatibility UNVERIFIED; physical area integral and power balance NOT_RUN.** Four required nonzero-tilt cases still fail full-aperture sweep clearance. A new backing/cap envelope and revised PML maps are next; rejecting these cases is not completion.

F16 case-metric association was published at 0f4b58c (352 isolated / 16 main tests PASS). W24 production capture remains CHANGES_REQUIRED: early-stage Worker2 reopen incorrectly shares the terminal save producer identity; Luna is repairing the three-stage chain.

Automatic approval previously rejected cleanup of failed owned native resources; explicit local termination authorization remains pending, with no cleanup/relaunch.

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

- **F16 original action scope:** durable reads passed scoped software checks, but `inspect` still needs failure/cache/best-feasible reporting from frozen objectives and constraints; `case_result` still needs an exact recoverable case-model artifact, not only a historical ModelRef. Both actions remain PARTIAL in the original coverage table.
- **F04:** connect/disconnect/reconnect software and recovery readback accepted. Owned Server and model-bound quiescence recovery software are accepted; no-ModelRef lifecycle recovery software is now accepted; live-context/Server recovery and managed native validation remain open.
- **W24:** atomic admission (78 tests), sensitivity (96 tests), and setup campaign (53 tests) are scoped software checkpoints; full native science remains incomplete and no old solve budget is borrowed.
- **W23:** single-BMA producer software is accepted; actual native producer execution and field mapping remain next. Two basis vectors are distinct eigensolutions of one receiver BMA, not two physical Ports. Managed native optics and tolerance/convergence acceptance remain open.
- **W24 historical attempts:** 0841Z freeze failed before engine birth (0 Workers/solves). Earlier ExternalStrain failure remains FAIL/UNKNOWN with safe_retry=false. Preserve these records; do not replay old freezes or reset budgets.
- **W01–W22:** existing implementation/evidence is not a blanket completed acceptance. **W25/W26:** Desktop/platform/release matrix acceptance remains incomplete. Final independent overall Reviewer has **not started**.
- **Platform scope:** macOS COMSOL 6.3 arm64 and x86_64 are USER_REQUESTED_SKIP, with acceptance NOT_RUN. Windows 6.3/6.4 and both Mac 6.4 targets remain required; Intel Mac 6.4 availability remains unconfirmed.

## Next action and recovery

Continue the three Luna Max work streams: complete F16 weighted native evidence and original experiment reporting/model gaps; complete W23 radiation geometry, capture/quadrature/power-balance and original optics acceptance; complete W24 UV/gel/reference/viscoelastic implementation and remaining science. Publish each accepted software stage immediately. Then freeze isolated managed native candidates, execute their reviewed scope, complete all remaining W01–W26 work, and run a fresh independent overall review with repair/retest until accepted.

The latest focused replay is in [CV_TOPOLOGY_SOFTWARE_CHECKPOINT.json](docs/full_project_execution/w23_overlap/CV_TOPOLOGY_SOFTWARE_CHECKPOINT.json). Recover from GitHub and existing state. Last previously verified GitHub sync: 0f4b58c410ecc997715d99d58c2814411f9f3595; this stage is recorded as synchronized only after post-push fetch and HEAD/origin-main equality.
