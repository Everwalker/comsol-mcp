# COMSOL MCP full-project progress

Updated 2026-09-28 UTC. **Overall PARTIAL; execution continues through W26.** This is the sole progress entry. Resume from [RESUME.json](docs/full_project_execution/state/RESUME.json), [TASKS.json](docs/full_project_execution/state/TASKS.json) and the frozen [acceptance plan](docs/full_project_execution/MAIN_ACCEPTANCE_PLAN.md). Original scope remains 26 work packages / 272 actions / 60 tests across six targets; the additional v2 operation makes the catalog 273 entries, not a replacement scope.

## Current stage

**W24 Equation View capture and state association: PASS for scoped software; W24/project overall PARTIAL.** Read-only private capture retains actual physics/feature paths, raw table cells and shape metadata, then authenticates the operation, source, artifact, model revision and exact V3 layout. Exact-cell field associations retain all ambiguous/unmatched candidates. API column labels and Maxwell/reference semantics remain explicitly unavailable or unverified.

**Actually executed:** 149 isolated and 149 main integration tests PASS; main verified 114 runtime-source hashes, four overlays and the Java compile class hashes. [Compact evidence and replay](docs/full_project_execution/w24/EQUATION_VIEW_SOFTWARE_CHECKPOINT.json). Tests use actual ControlDaemon/SQLite with a synthetic Worker; no COMSOL model was started or solved. Earlier failures and their fixes remain recorded, not counted as native evidence.

**Next:** complete W24 physical fixtures/numerical controls and native semantic evidence; continue W21 durable stage execution and actual mapping, plus W23 Qabs and BMA→Frequency producer binding. Native cleanup authorization/platform limitations remain unresolved. Final independent overall Reviewer has not started.

Latest published stage: W23 partial CV power terms `aa1ae31`, with Qabs generic interface NOT_IMPLEMENTED and native/science NOT_RUN. W24 V3 `b4ffbc0` and prior stages retain their scoped acceptance only.

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

The latest focused replay is in [EQUATION_VIEW_SOFTWARE_CHECKPOINT.json](docs/full_project_execution/w24/EQUATION_VIEW_SOFTWARE_CHECKPOINT.json). Recover from GitHub and existing state. Last previously verified GitHub sync: aa1ae3168444c4f302eaa95c32f29447fd78b2b0; this stage is recorded as synchronized only after post-push fetch and HEAD/origin-main equality.
