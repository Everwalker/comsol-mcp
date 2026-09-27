# COMSOL MCP full-project progress

Updated 2026-09-27 UTC. **Overall PARTIAL; execution continues through W26.** This is the sole progress entry. Resume from [RESUME.json](docs/full_project_execution/state/RESUME.json), [TASKS.json](docs/full_project_execution/state/TASKS.json) and the frozen [acceptance plan](docs/full_project_execution/MAIN_ACCEPTANCE_PLAN.md). Original scope remains 26 work packages / 272 actions / 60 tests across six targets; the additional v2 operation makes the catalog 273 entries, not a replacement scope.

## Current stage

**W23 paired BMA field mapping probe: PASS for scoped software; W23/project overall PARTIAL.** Two distinct eigensolution tuples are bound through dataset/SolutionInfo metadata. Each tuple reads paired complex E/H and Port fields with native normals on the same grid; unadjusted E/H residuals are checked separately. Phase/subspace diagnostics do not establish a mapping.

**Actually executed:** isolated published `b484a735` plus six-file overlay: **171 PASS / 0 FAIL / 0 ERROR / 0 SKIP**; Java API compilation/javap PASS. A fresh no-COMSOL daemon smoke confirmed private control-home identity and reaped its owned child. Main review verified 105 source files (99 unchanged published), compiled-class/receipt/JUnit identities, and reran **7 negative controls PASS**. [Compact evidence and replay](docs/full_project_execution/w23_overlap/BMA_MAPPING_SOFTWARE_CHECKPOINT.json).

**Native BMA/field mapping/science NOT_RUN; mapping UNVERIFIED.** The 3240-second profile is prepared only. Synthetic field samples and the harmless daemon smoke are not COMSOL or optical acceptance. Cleanup SIGTERM for the original failed native setup was rejected by automatic approval; explicit authorization remains pending, with no cleanup/relaunch.

F04 lifecycle recovery was published as `ebb30ce` (164 + 1 tests); F16 durable experiment reads continue. W24 readmission/budget identity was published and post-push verified as `2a61e4c` (100 tests plus 9 main retests); its complete two-phase, single-runtime production CLI is being implemented.

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

Continue the three Luna Max work streams: implement F16 canonical durable experiment reads; complete W23 optical overlap/convergence orchestration; complete W24 production orchestration and remaining science. Publish each accepted software stage immediately. Then freeze isolated managed native candidates, execute their reviewed scope, complete all remaining W01–W26 work, and run a fresh independent overall review with repair/retest until accepted.

The latest focused replay is in [BMA_MAPPING_SOFTWARE_CHECKPOINT.json](docs/full_project_execution/w23_overlap/BMA_MAPPING_SOFTWARE_CHECKPOINT.json). Recover from GitHub and existing state. Last previously verified GitHub sync: `2a61e4c9c7954e0ce12dbdf9cae4792bebe65208`; this stage is recorded as synchronized only after post-push fetch and HEAD/origin-main equality.
