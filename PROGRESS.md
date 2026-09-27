# COMSOL MCP full-project progress

Updated 2026-09-27 UTC. **Overall PARTIAL; execution continues through W26.** This is the sole progress entry. Resume from [RESUME.json](docs/full_project_execution/state/RESUME.json), [TASKS.json](docs/full_project_execution/state/TASKS.json) and the frozen [acceptance plan](docs/full_project_execution/MAIN_ACCEPTANCE_PLAN.md). Original scope remains 26 work packages / 272 actions / 60 tests across six targets; the additional v2 operation makes the catalog 273 entries, not a replacement scope.

## Current stage

**F04 session.connect: PASS for scoped software; lifecycle remains PARTIAL.** The public route attaches a private, project-scoped Worker to an already-listening endpoint using trusted local runtime configuration, independently observed peer and actual connect reply. Concurrent duplicate requests share durable admission. Startup/connect uncertainty, failed child cleanup and post-connect persistence/registration errors retain the original Worker as UNKNOWN and prevent replacement. No process-global runtime/environment mutation is used.

**Actually executed:** 108 tests PASS / 0 FAIL / 0 ERROR / 0 SKIP in an isolated archive of published `2c1770a` plus exactly five changed files. Main review verified all five overlay hashes, 84 unchanged runtime sources, archive hashes and JUnit. [Compact evidence and reproduction recipe](docs/full_project_execution/project_session/SESSION_CONNECT_CHECKPOINT.json).

Only synthetic/injected Workers were used: **native COMSOL and Windows 6.3/6.4 validation NOT_RUN**. A missing remote engine build is reported as NOT_REPORTED. Reconnect/disconnect/recover and owned start/stop are still incomplete.

W23 surface-normal stage remains software PASS: [99 isolated tests](docs/full_project_execution/w23_overlap/NORMAL_READBACK_CHECKPOINT.json). Frame/Port propagation and optical acceptance remain UNVERIFIED/NOT_RUN.

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

- **F04:** session.connect software stage accepted above. Next implement reconnect/disconnect/recover and exact owned process lifecycle, then run managed native validation.
- **W24 science admission:** preliminary 52-test result was **not accepted**. Cross-process atomic dispatch, UNKNOWN re-entry and original-revision receipt binding are being repaired and retested. A 14-solve sensitivity plan has no current native authorization; do not borrow the old ten-solve budget.
- **W23:** actual Port → BMA → solver/dataset → SolutionInfo provenance is next. Two basis vectors are distinct eigensolutions of one receiver BMA, not two physical Ports. Managed native optics and tolerance/convergence acceptance remain open.
- **W24 historical attempts:** 0841Z freeze failed before engine birth (0 Workers/solves). Earlier ExternalStrain failure remains FAIL/UNKNOWN with safe_retry=false. Preserve these records; do not replay old freezes or reset budgets.
- **W01–W22:** existing implementation/evidence is not a blanket completed acceptance. **W25/W26:** Desktop/platform/release matrix acceptance remains incomplete. Final independent overall Reviewer has **not started**.
- **Platform scope:** macOS COMSOL 6.3 arm64 and x86_64 are USER_REQUESTED_SKIP, with acceptance NOT_RUN. Windows 6.3/6.4 and both Mac 6.4 targets remain required; Intel Mac 6.4 availability remains unconfirmed.

## Next action and recovery

Continue the three Luna Max work streams: implement remaining session lifecycle routes; implement W23 actual mode provenance; finish W24 atomic admission and original-revision validation. Publish each accepted software stage immediately. Then freeze isolated managed native candidates, execute their reviewed scope, complete all remaining W01–W26 work, and run a fresh independent overall review with repair/retest until accepted.

The latest focused replay is in [NORMAL_READBACK_CHECKPOINT.json](docs/full_project_execution/w23_overlap/NORMAL_READBACK_CHECKPOINT.json). Recover from the GitHub checkout and existing state; raw local paths are supporting artifacts, not prerequisite hidden source. Usage interruption was resolved and three replacement executors resumed without restarting native jobs. Last previously verified GitHub sync: `2c1770a91250f7d1f4dcde0e6f4e9f177b45e50a`; current-stage synchronization is recorded only after post-push fetch and HEAD/origin-main equality verification.
