# COMSOL MCP full-project progress

Updated 2026-09-27 UTC. **Overall PARTIAL; execution continues through W26.** This is the sole progress entry. Resume from [RESUME.json](docs/full_project_execution/state/RESUME.json), [TASKS.json](docs/full_project_execution/state/TASKS.json) and the frozen [acceptance plan](docs/full_project_execution/MAIN_ACCEPTANCE_PLAN.md). Original scope remains 26 work packages / 272 actions / 60 tests across six targets; the additional v2 operation makes the catalog 273 entries, not a replacement scope.

## Current stage

**W24 science admission/revision: PASS for scoped software; W24 remains PARTIAL.** The baseline flat/step runner binds each solve to the original approved project, store, model epoch/revision and case. SQLite admission prevents duplicate dispatch; Java holds a persistent file lock through validation, submission intent, solve and save. Setup readback evidence retains its original revision; pre-solve model.inspect does not advance it. Flat/step comparison remains descriptive.

**Actually executed:** isolated published `2c1770a` plus W24 overlay, **78 PASS / 0 FAIL / 0 ERROR / 0 SKIP**, including the original 20 setup regressions. Real ControlDaemon/SQLite tests use two independent store connections and a stub Worker to test concurrency, UNKNOWN reentry and changed-payload refusal. Six Java source classes compiled with Corretto11.0.31 against349 COMSOL6.4 JARs. Main review checked source, runtime, import, JUnit, class and JAR hashes. [Compact evidence and replay](docs/full_project_execution/w24/SCIENCE_ADMISSION_CHECKPOINT.json).

COMSOL process/solver submissions: **0**; native and scientific acceptance **NOT_RUN**. The fourteen-solve sensitivity matrix is **PLAN ONLY**, not an executable candidate. Variant fixture/readback/capture/runner implementation is next; the old ten-solve budget remains separate.

F04 session.connect is now software PASS with [108 isolated tests](docs/full_project_execution/project_session/SESSION_CONNECT_CHECKPOINT.json); other lifecycle routes and native connection remain open. W23 normal readback has [99 isolated tests](docs/full_project_execution/w23_overlap/NORMAL_READBACK_CHECKPOINT.json); actual Port/mode/frame provenance remains in progress.

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
- **W24:** preliminary 52-test result remains historically rejected; the repaired scoped software now passes78 isolated tests above. Variant-aware sensitivity execution and full native science remain incomplete; no old solve budget is borrowed.
- **W23:** actual Port → BMA → solver/dataset → SolutionInfo provenance is next. Two basis vectors are distinct eigensolutions of one receiver BMA, not two physical Ports. Managed native optics and tolerance/convergence acceptance remain open.
- **W24 historical attempts:** 0841Z freeze failed before engine birth (0 Workers/solves). Earlier ExternalStrain failure remains FAIL/UNKNOWN with safe_retry=false. Preserve these records; do not replay old freezes or reset budgets.
- **W01–W22:** existing implementation/evidence is not a blanket completed acceptance. **W25/W26:** Desktop/platform/release matrix acceptance remains incomplete. Final independent overall Reviewer has **not started**.
- **Platform scope:** macOS COMSOL 6.3 arm64 and x86_64 are USER_REQUESTED_SKIP, with acceptance NOT_RUN. Windows 6.3/6.4 and both Mac 6.4 targets remain required; Intel Mac 6.4 availability remains unconfirmed.

## Next action and recovery

Continue the three Luna Max work streams: implement remaining session lifecycle routes; implement W23 actual mode provenance; implement W24 sensitivity variants and complete science orchestration. Publish each accepted software stage immediately. Then freeze isolated managed native candidates, execute their reviewed scope, complete all remaining W01–W26 work, and run a fresh independent overall review with repair/retest until accepted.

The latest focused replay is in [NORMAL_READBACK_CHECKPOINT.json](docs/full_project_execution/w23_overlap/NORMAL_READBACK_CHECKPOINT.json). Recover from the GitHub checkout and existing state; raw local paths are supporting artifacts, not prerequisite hidden source. Usage interruption was resolved and three replacement executors resumed without restarting native jobs. Last previously verified GitHub sync: `24ce3606e3e7bb43bcf880e1f8322f2410ab0e68`; current-stage synchronization is recorded only after post-push fetch and HEAD/origin-main equality verification.
