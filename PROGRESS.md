# COMSOL MCP full-project progress

Updated 2026-09-27 UTC. **Overall PARTIAL; execution continues through W26.** This is the sole progress entry. Resume from [RESUME.json](docs/full_project_execution/state/RESUME.json), [TASKS.json](docs/full_project_execution/state/TASKS.json) and the frozen [acceptance plan](docs/full_project_execution/MAIN_ACCEPTANCE_PLAN.md). Original scope remains 26 work packages / 272 actions / 60 tests across six targets; the additional v2 operation makes the catalog 273 entries, not a replacement scope.

## Current stage

**F16 weighted evidence adapter: PASS for scoped software; weighted capability/F16/project overall PARTIAL.** The adapter records explicit minimum/integral sampling configuration, selected solution tuples, transient ROI membership, positive denominators and cleanup. Global parameter unit readback is only a scoped diagnostic. General weighted `metric.evaluate` still refuses unverified ROI units or actual sample coverage and publishes no observation.

**Actually executed:** isolated published `ae4e817` plus four-file overlay: **182 PASS / 0 FAIL / 0 ERROR / 0 SKIP**. Main verified 307 unchanged baseline files, all four overlay hashes and JUnit evidence, then independently ran **9 critical tests PASS**. [Compact evidence and replay](docs/full_project_execution/project_session/WEIGHTED_EVIDENCE_SOFTWARE_CHECKPOINT.json).

The first isolation omitted schema/driver dependencies (176 PASS / 1 FAIL / 5 SKIP); the final same-base archive resolves all six. Main also rejected the original parameter-context unit promotion; the repaired candidate retains it as diagnostic only. Both failure records remain preserved.

**COMSOL/native NOT_RUN; general weighted acceptance remains OPEN.** Matching integration method/order does not prove actual Gauss-point identity. Native ROI-unit resolution, experiment-case metric associations, full experiment reporting and exact recoverable per-case model artifacts remain incomplete. W24 public capture/full-history software was published as `c6bd0d1` (85 isolated and 33 main tests); its native/reference gates remain open. W23 radiation/PML geometry continues.

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

Continue the three Luna Max work streams: complete F16 weighted native evidence, experiment-case metric associations and original experiment reporting/model gaps; complete W23 radiation geometry, capture/quadrature/power-balance and original optics acceptance; complete W24 UV/gel/reference/viscoelastic implementation and remaining science. Publish each accepted software stage immediately. Then freeze isolated managed native candidates, execute their reviewed scope, complete all remaining W01–W26 work, and run a fresh independent overall review with repair/retest until accepted.

The latest focused replay is in [WEIGHTED_EVIDENCE_SOFTWARE_CHECKPOINT.json](docs/full_project_execution/project_session/WEIGHTED_EVIDENCE_SOFTWARE_CHECKPOINT.json). Recover from GitHub and existing state. Last previously verified GitHub sync: `c6bd0d191bf1c5006eba15f9134a37af31d96313`; this stage is recorded as synchronized only after post-push fetch and HEAD/origin-main equality.
