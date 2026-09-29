# COMSOL MCP full-project progress

**Current stage: W21 automatic expression-unit output producer — software PASS; native NOT_RUN. Overall project: PARTIAL.** The original scope remains 26 work packages, 272 actions and 60 tests across six targets, subject to the recorded user skips. The final independent Reviewer has not started.

## Latest completed software unit

The stage output producer now makes one managed field read for the original variable, its unit-normalized expression and constant one, with no requested unit override. It checks automatic units and complete complex numeric controls under the frozen tolerance, retains the full three-expression capture, and labels the comparator slice explicitly. Existing tuple/selection, payload limits, UNKNOWN no-replay and successor checks remain enforced.

**195 unique tests PASS; 0 failures/errors/skips.** The 32 focused tests are included in that total. Main reviewed the final diff and independently verified both changed source/test hashes, 14 report hashes and unique JUnit counts. Review caught an expression-axis error shared by the initial implementation and fake: the corrected producer enforces `[expression, outer, inner, point]`, uses the actual `FieldArray` builder in tests and rejects the former layout. Earlier 31/194 passing runs are retained as superseded evidence.

[Compact checkpoint and rebuild entry](docs/full_project_execution/project_session/STAGE_BACKEND_SOFTWARE_CHECKPOINT.json#automatic_unit_controls_producer_20260930). Full evidence: `/Volumes/SSD/Comsol-MCP/execution-scratch/w21-stage-unit-controls-20260930`. The missing historical test venv was replaced with an isolated SSD environment using locked dependencies; ArtifactStore tests use a fresh directory on the existing APFS test image because exFAT lacks required hard links. Failures and prior evidence remain preserved.

**Limits:** these are real ManagedBackend/ExecutionService software tests with a fake Worker. This producer has not run against COMSOL. Automatic expression-unit dimensionality does not establish active DOFs, physics intrinsic units, coordinate frames, history, stage transfer or scientific acceptance. The earlier managed current-mesh bridge remains software PASS (194 unique tests and 24 offline Java argument controls), native NOT_RUN; current mesh content alone is not historical-mesh proof.

## Verified Windows native baseline

The canonical public MCP chain actually completed **Windows 6.4 build 293 → Windows 6.3 build 290** on runtime source `45399a5`: start → connect → small model/geometry/mesh → one solve → exact solution tuple → complete temperature field readback → owned cleanup. Both runners exited 0, returning `[1,1,5,637]`, unit K and selected tuple outer=1/inner=5/solnum=5. Selected temperature maxima were approximately 304.380 K and 304.391 K. The coarse `hauto=9` model proves this runtime chain, not convergence or physical correctness.

Subsequent native unit controls on source `40c9838` also passed in that order: runs `w21-64-20260929T165606Z-142016ea` and `w21-63-20260929T170038Z-0036f835`. One field read requested `T`, `T/1[K]`, `1` without requested units; both returned `[3,1,5,637]` with K/1/1. Normalization and constant-one maximum errors were 0, all imaginary values were 0, all 15 actions succeeded with no UNKNOWN, and exact owned PID/birth exit, listener absence, Worker retirement and temporary Eval removal were verified. The 6.3 run pins the actual successful same-profile 6.4 receipt.

[Canonical two-version checkpoint](docs/full_project_execution/project_session/WINDOWS64_V2_SESSION_START_UNKNOWN_CHECKPOINT.json#canonical_native_chain_windows_round_20260929) and [native unit-control checkpoint](docs/full_project_execution/project_session/PRODUCTION_RUNNER_SOFTWARE_CHECKPOINT.json#auto_unit_controls_native_windows_20260930) retain source/archive/freeze/result fingerprints and rebuild entries. Full evidence stays on SSD under `execution-scratch/w21-solver-path-w21r12` and `execution-scratch/w21-auto-unit-native-w21r14`.

Necessary Windows repairs were verified by real solve/readback: explicit existing RPC wait, short state roots for new homes and the nested COMSOL solver-file path guard. Budgets were unchanged. Historical UNKNOWN/start/path failures remain preserved, with their scoped quiescence/recovery evidence; they are not rewritten as PASS or replayed. Targeted `info/Shape` metadata was actually empty, so it still provides no active-DOF/unit proof.

## Next work and recovery entry

1. Integrate the executable initial-stage path under the existing W21 route. Separate first-stage prerequisites from predecessor transfer; capture every mapped target field, automatic unit, actual dataset/frame and solved-for field identity, exact solution tuple, owned mesh association/observations and saved artifact before acceptance. Correct the existing contradiction between initial-state empty source checks and the unconditional output-check requirement. Preserve successor mapping/frame/history and frozen continuity/conservation checks.
2. Run the resulting native stage chain on Windows 6.4, then 6.3. Do not inherit native PASS from the earlier canonical source or replay historical freezes.
3. Continue original W23 optics/PML/Qabs, W24 shape/UV–thermal–cure–cooling/history, W25 platform/Desktop and W26 delivery requirements. Then freeze the complete candidate for the independent Reviewer and repair/retest loop.

Authoritative recovery: [RESUME.json](docs/full_project_execution/state/RESUME.json), [TASKS.json](docs/full_project_execution/state/TASKS.json), [MAIN_ACCEPTANCE_PLAN.md](docs/full_project_execution/MAIN_ACCEPTANCE_PLAN.md) and [MASTER_GOAL.md](docs/full_project_execution/MASTER_GOAL.md). Do not bootstrap/reset existing state. Native reproduction uses `tools/run_w21_stage_native.py prepare/execute` with a fresh isolated short home, explicit installed COMSOL/JDK and the actual 6.4 prerequisite receipt for 6.3.

Both Mac COMSOL 6.3 targets remain USER_REQUESTED_SKIP / NOT_RUN. Intel Mac 6.4 availability is unconfirmed. The [TMP migration summary](docs/full_project_execution/state/TMP_MIGRATION_SUMMARY.json) records retained inactive paths and occupied roots; new scratch is on SSD. No full-project completion is claimed.
