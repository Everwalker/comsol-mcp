# COMSOL MCP full-project progress

Updated 2026-09-27 UTC. **Overall PARTIAL; execution continues through W26.** This is the sole progress entry. Resume from [RESUME.json](docs/full_project_execution/state/RESUME.json), [TASKS.json](docs/full_project_execution/state/TASKS.json) and the frozen [acceptance plan](docs/full_project_execution/MAIN_ACCEPTANCE_PLAN.md). Original scope remains 26 work packages / 272 actions / 60 tests across six targets; the additional v2 operation makes the catalog 273 entries, not a replacement scope.

## Current stage

**W24 cure-law v2: PASS for scoped software; W24/project overall PARTIAL.** Additive fixtures implement relative spatial UV dose, first-gel Activation and a full one-branch Maxwell material. Independent analytical controls check Maxwell absolute amplitudes, all six gel stress components and complete captured history continuity. Historical v1 build/readback remains available.

**Actually executed:** isolated published `a4684f8` plus five-file overlay: **45 PASS / 0 FAIL / 0 ERROR / 0 SKIP**, including v1 coupon/science-runner regressions. Main verified 103 baseline source hashes and all overlay/artifact identities, then reran **12 new tests PASS** in that isolated directory. Both Java fixtures compiled against the installed COMSOL 6.4 API with JDK 11. [Compact evidence and replay](docs/full_project_execution/w24/CURE_LAW_V2_SOFTWARE_CHECKPOINT.json).

The first isolated test run was **42 PASS / 3 FAIL** due to a missing published proposal dependency; its evidence remains preserved. Main independently reproduced an incomplete stress-component acceptance bug; the repaired candidate rejects it and retains full six-component control behavior.

**COMSOL/native NOT_RUN; scientific acceptance NOT_ESTABLISHED.** Parser flags do not authenticate a native source. Live Activation/reference, Maxwell history, staged transfer/reopen and the revised solve budget remain unverified. Automatic approval previously rejected cleanup of the failed owned native resources; explicit local termination authorization remains pending, with no cleanup/relaunch.

F16 five-action metrics and W23 route-bound mesh/cohort work continue. Their current working changes are not covered by this W24 checkpoint.

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

Continue the three Luna Max work streams: implement F16 five versioned metric actions and authorized historical comparisons; complete W23 optical overlap/convergence orchestration; complete W24 UV/gel/reference/viscoelastic implementation and remaining science. Publish each accepted software stage immediately. Then freeze isolated managed native candidates, execute their reviewed scope, complete all remaining W01–W26 work, and run a fresh independent overall review with repair/retest until accepted.

The latest focused replay is in [CURE_LAW_V2_SOFTWARE_CHECKPOINT.json](docs/full_project_execution/w24/CURE_LAW_V2_SOFTWARE_CHECKPOINT.json). Recover from GitHub and existing state. Last previously verified GitHub sync: `785c7513951ab08bc1312adfb1deda653a83710b`; this stage is recorded as synchronized only after post-push fetch and HEAD/origin-main equality.
