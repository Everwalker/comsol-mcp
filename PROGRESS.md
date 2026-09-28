# COMSOL MCP full-project progress

Updated 2026-09-28 UTC. **Overall PARTIAL; execution resumed; W21 strict field-readback software accepted; actual stage backend next.** This is the sole progress entry. Resume from [RESUME.json](docs/full_project_execution/state/RESUME.json), [TASKS.json](docs/full_project_execution/state/TASKS.json) and the frozen [acceptance plan](docs/full_project_execution/MAIN_ACCEPTANCE_PLAN.md). Original scope remains 26 work packages / 272 actions / 60 tests across six targets; the additional v2 operation makes the catalog 273 entries, not a replacement scope.

## Current stage

**W21 strict source-field readback: PASS scoped software; W21 overall PARTIAL.** Exact manual tuple selection, source solution-object/mesh association, Worker-side complete payload limits and structured failure propagation are implemented. Actual **138 related + 1 registration = 139 unique tests PASS**; production Java Worker and payload harness compiled/executed offline. Main verified 108 source hashes and JUnit/artifact hashes. Source association is not target topology, intrinsic-unit or history acceptance; actual stage solve/check/save remains incomplete. [Evidence and replay](docs/full_project_execution/project_session/STRICT_FIELD_READBACK_SOFTWARE_CHECKPOINT.json).

**W23 Qabs/Frequency producer software: PASS scoped software; W23 overall PARTIAL.** Restored complete Java source, repaired null output association and added an executable Java-to-Python contract test. Actual **239 regression PASS**, including 2 focused cases; both Java fixtures and harness compile PASS. Main verified 108 source hashes, JUnit/artifact hashes and compiled-class hashes. Qabs production registry remains empty; native producer/calibration and physical optics acceptance NOT_RUN. [Evidence and replay](docs/full_project_execution/w23_overlap/QABS_FREQUENCY_SOFTWARE_CHECKPOINT.json).

**T058 assumption-report presentation: PASS scoped software; original T058 PARTIAL.** JSON/Markdown retain input assumptions, supplied sources/estimated/calibration labels, and optional sensitivity evidence. Caller metadata remains unauthenticated; fitted results do not become independent physical validation. Missing sensitivity is NOT_RUN, supplied evidence remains UNVERIFIED. Actual W20/W24 closure: **323 PASS**, including 3 focused cases. Main verified all manifest source and test-artifact hashes/JUnit. No sensitivity calculation or native execution was added. [Evidence and replay](docs/full_project_execution/w24/T058_ASSUMPTION_REPORT_CHECKPOINT.json).

**W24 physical-control orchestration: PASS scoped software; W24 overall PARTIAL.** Two-control preparation/execution, durable solve-slot intent, approval/source/runtime binding and capture verification are implemented. Main found and verified repair of non-finite `actfac` acceptance without changing tolerance. Actual regression **271 PASS**, including 14 focused cases; main independently checked 4 boundary cases and all 127 source hashes plus JUnit/artifact hashes. Native control solves **NOT_RUN**, physical validation **UNVERIFIED**. [Evidence and replay](docs/full_project_execution/w24/PHYSICAL_CONTROLS_SOFTWARE_CHECKPOINT.json).

**Test-efficiency tooling: PASS for scoped offline engineering checks.** Fixed baseline/configuration and 99-file source identity, APFS scratch preflight, bounded 2 KiB stdout, full local logs, strict JUnit/exit/source-drift checks, and explicit required-regression handling are implemented. Actual integrated run: 8 focused + 1 registration tests PASS; main independently rejected 3 contradictory JUnit cases and verified the source/artifact hashes. Native/Java compilation NOT_RUN; GPT quota savings are not yet quantified. [Compact evidence](docs/full_project_execution/state/TEST_EFFICIENCY_CHECKPOINT.json), [replay workflow](docs/full_project_execution/TEST_EXECUTION.md).

**Storage migration: PASS for 619 inactive paths; overall migration PARTIAL because 7 occupied roots remain.** Verified archives preserve 36.71 GB of source content on external SSD; original removal increased observed internal free space by 37.27 GB (other system activity can affect this delta). [Compact migration evidence and recovery](docs/full_project_execution/state/TMP_MIGRATION_SUMMARY.json). Raw archives and per-file hashes remain on SSD, not in Git.

**Frozen round integration:** W24 and W23 software candidates have now been reviewed, repaired and integrated as recorded above. W21 has no actual stage backend yet. All original native and scientific requirements remain open.

**W21 v2 plans, state-map selector configuration and durable stage attempts: PASS for scoped software; W21/project overall PARTIAL.** Canonical direct/fallback routes now configure an exact source tuple selector and verify the target solver attachment, or persist a no-dispatch attempt when native proof is absent. SQLite records bind the exact project, ModelRef, plan and revision; per-variable mapping and actual stage solves remain unimplemented.

**Actually executed:** 107 isolated and 107 main integration tests PASS, including SQLite migration/rollback, public routes, v1 compatibility and registration. Main independently retested and closed two findings: integrated-unit declarations and nonzero signed terms across different selections. Main verified 390 parent source files and the 16-file final overlay. [Compact evidence and replay](docs/full_project_execution/project_session/STATE_MAP_STAGE_ATTEMPT_SOFTWARE_CHECKPOINT.json). Tests use synthetic Worker/models; native COMSOL NOT_RUN.

**After the test-efficiency stage:** implement actual field/unit/mesh/frame/history verification and the single-Worker solve/check/save stage backend; review frozen W23 Qabs/producer and W24 physical-control candidates before continuing. Native resource authorization and required platform limitations remain unresolved. Final independent overall Reviewer has not started.

Previously synchronized checkpoint: W24 Equation View software `303198a`, 149 isolated and 149 main tests PASS; physical field/reference semantics remain UNVERIFIED.

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

**Current next action:** implement actual W21 supported stage solve/check/save backend with native target field/unit/topology/frame admission, persistent dispatch intent, UNKNOWN no-retry and exact saved-output continuity/conservation checks. Continue all remaining MASTER_GOAL requirements; T047/T058 native history and actual sensitivity remain open. Seven occupied old temporary roots remain untouched pending the separate shutdown choice.

Latest recovery/replay: [W21 strict-field checkpoint](docs/full_project_execution/project_session/STRICT_FIELD_READBACK_SOFTWARE_CHECKPOINT.json) and existing RESUME/TASKS. Last verified GitHub sync before this stage: `7b85a27ee8206886763ca442061c8e3f9deb0a3f`. This stage is synchronized only after post-push fetch confirms HEAD equals origin/main; the commit containing this entry identifies the stage.
