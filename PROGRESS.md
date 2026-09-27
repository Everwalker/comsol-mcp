# COMSOL MCP full-project progress

Updated 2026-09-27 UTC. **Overall PARTIAL; execution continues through W26.** This is the sole progress entry. Resume from [RESUME.json](docs/full_project_execution/state/RESUME.json), [TASKS.json](docs/full_project_execution/state/TASKS.json) and the frozen [acceptance plan](docs/full_project_execution/MAIN_ACCEPTANCE_PLAN.md). Original scope remains 26 work packages / 272 actions / 60 tests across six targets; the additional v2 operation makes the catalog 273 entries, not a replacement scope.

## Current stage

**F16 five metric actions: PASS for scoped software; F16/project overall PARTIAL.** Canonical define/list/evaluate/remove/compare routes now use project-scoped immutable definition versions, tombstones, and trusted observation artifacts. Comparison re-derives exact solution-tuple values from authorized artifact bytes, verifies producer/hash/unit/definition identity, and rejects nonfinite derived arithmetic.

**Actually executed:** isolated published `785c751` plus eleven-file overlay: **281 PASS**, with one history-dependent Git-blob test run separately **1 PASS**. Main verified 11,049 unchanged archive files, all overlay and evidence hashes, and reran **8 critical tests PASS**. Actual SQLite and persisted artifacts were exercised; the native adapter used synthetic API objects. [Compact evidence and replay](docs/full_project_execution/project_session/METRIC_ACTIONS_SOFTWARE_CHECKPOINT.json).

The initial source-only archive failed the historical-object test; the original failure is retained. Main found finite-input comparison overflow, and the repaired persisted-artifact route now refuses `COMPARISON_OVERFLOW`. Separate published-base/metric-overlay GUARD_T038 loopback tests both passed; ordinary sandbox bind EPERM prevented that probe from reaching registration. The broader interrupted test run is not a complete-suite PASS.

**COMSOL/native NOT_RUN; scientific acceptance NOT_ESTABLISHED.** Weighted metrics still refuse pending actual weight-unit/nonnegative evidence; experiment-case associations remain unverified. Original experiment failure/cache/best-feasible and recoverable case-model requirements are still open. W24 cure-law software was published as `81fcc73` with 45 isolated and 12 main tests; native capture/history work continues. W23 remains under review after a required test-helper overlay omission was found.

Automatic approval previously rejected cleanup of the failed owned native resources; explicit local termination authorization remains pending, with no cleanup/relaunch.

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

Continue the three Luna Max work streams: complete F16 weighted native evidence, experiment-case metric associations and original experiment reporting/model gaps; complete W23 optical overlap/convergence orchestration; complete W24 UV/gel/reference/viscoelastic implementation and remaining science. Publish each accepted software stage immediately. Then freeze isolated managed native candidates, execute their reviewed scope, complete all remaining W01–W26 work, and run a fresh independent overall review with repair/retest until accepted.

The latest focused replay is in [METRIC_ACTIONS_SOFTWARE_CHECKPOINT.json](docs/full_project_execution/project_session/METRIC_ACTIONS_SOFTWARE_CHECKPOINT.json). Recover from GitHub and existing state. Last previously verified GitHub sync: `81fcc73784e1a7e36197ae433c862fd1f4f9c621`; this stage is recorded as synchronized only after post-push fetch and HEAD/origin-main equality.
