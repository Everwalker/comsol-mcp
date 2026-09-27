# COMSOL MCP full-workpack progress

Updated 2026-09-27 UTC. Overall delivery is **PARTIAL**; execution continues through W01–W26 without a phase stop. This is the concise GitHub handoff. Recovery authority: [RESUME.json](docs/full_project_execution/state/RESUME.json). Evidence and rebuild commands: [checkpoint summary](docs/full_project_execution/release/PARTIAL_CHECKPOINT_20260927.json). Raw evidence remains local; historical state paths are not bundled artifacts.

**Prior scoped software checkpoint: 236 tests PASS** from an index-only clean snapshot (0 failures/errors/skips). This covers project control, W23 mapping/full-3D/preflight, release controls, W24 preflight, and artifact geometry runners; it is not full-suite or native acceptance. The source checkpoint `1be74ff8d222d07548ca2e9d2fc61ca50525f1c3` was pushed non-force and verified equal to `origin/main`. Subsequent stages use this same progress entry and existing state files.

## Current checkpoint

- **W24 managed shape setup: 20 software tests PASS; Java API compilation PASS; native NOT_RUN.** Added exact registered-session build/adopt/inspect/save/reopen orchestration and native configuration readback. Fixed default-session routing, nonfinite parameter acceptance and unproven unsolved-save claims; solver data is checked without clearing it. The real daemon routing test uses a fake backend. See [compact managed-shape checkpoint](docs/full_project_execution/w24/MANAGED_SHAPE_CHECKPOINT.json). Raw field/time acquisition and an isolated setup candidate remain next.

- **W23 two-mode basis v2: 32 synthetic/envelope tests PASS; native NOT_RUN; route NOT_REGISTERED.** Added complex Gram projection, normalization and independent-field comparison contracts. Review-driven fixes cover overflow, valid low-power scaling, per-mode Hermitian residuals and nonfinite references. Source and evidence hashes verified. See [compact basis checkpoint](docs/full_project_execution/w23_overlap/TWO_MODE_BASIS_CHECKPOINT.json). Authenticated managed native integration remains next.

- **F04 dispatch/control software: 164 tests PASS; overall PARTIAL.** Unified default/registered scheduling; bound job controls to exact Worker/project/session; kept status/authorized stop outside the ordinary engine queue; fixed stop-fence races and required stored/live/caller process-birth agreement. Three session read routes expose cached metadata. Six session mutations and platform lifecycle/native termination remain unverified. See [compact F04 checkpoint](docs/full_project_execution/project_session/f04_dispatch_control_checkpoint_20260927.json).

- **W24 shape post-processing: 23 synthetic-data tests PASS; native NOT_RUN.** Added substrate-complete wet/dry classification, interface/contact-line extraction, requested-time checks and separate volume/speed/shape gates. The raised-start sampling negative control prevents unobserved liquid being labeled dry. See [compact metrics checkpoint](docs/full_project_execution/w24/SHAPE_METRICS_CHECKPOINT.json). Actual managed field acquisition, stable shapes and sensitivity results remain unverified.

- **W23 full-3D science/setup software: 55 tests PASS; native NOT_RUN.** Added the project-bound public-dispatch configuration chain, complex-vector quadrature comparison with fixed zero-channel tolerances, and a self-contained planarity negative fixture. Java compilation passed with an unchecked-operations warning. No native setup, solve or optical result is claimed. See [compact W23 checkpoint](docs/full_project_execution/w23_overlap/FULL3D_SCIENCE_CHECKPOINT.json). Versioned two-mode basis and real managed science execution remain next.

- **W24 static-shape baseline: PARTIAL; offline Java compilation PASS.** Equal-volume flat/step setup code now covers the full wetting substrate, uses an explicit pressure reference, and treats bulk energy as diagnostic only. Source/draft/class hashes and all 349 installed JAR identities were independently checked. No server, Worker, native model build or solve was run. See [compact compile checkpoint](docs/full_project_execution/w24/STATIC_SHAPE_OFFLINE_CHECKPOINT.json); managed readback, field extraction, stability and sensitivity work continue.

- **Latest full offline attempt on `ceac7a0`: FAIL / PARTIAL** — 2596 passed, 28 failed, 39 skipped, 11 errors. The source export omitted root historical evidence and Git metadata; these export limitations are separated from code/test defects. An explicitly permitted loopback subset rerun produced 53 passed, 2 failed, 1 skipped, 0 errors. Original failures remain preserved. See [compact regression summary](docs/full_project_execution/release/OFFLINE_REGRESSION_20260927.json) for hashes, replay commands and classifications. This is not a full-suite or native PASS.
- **Repairs pending:** Worker allowlist lacks the verified `GeomObjectSelection.init(int)` used by geometry measurement; older tests need real project/provenance prerequisites and self-contained fixtures. The published F04 checkpoint repairs the reproduced queue overlap and retirement-fence race; remaining session lifecycle/native paths stay incomplete.

- **W01–W22:** existing implementation and evidence baseline; overall acceptance remains open.
- **W23:** mapping/full-3D scoped software checks passed (59 tests); native acceptance is **NOT_RUN**.
- **W24:** the 0841Z setup attempt failed before engine birth at `project.inspect` (0 engine/Worker births and 0 solves). The public dispatcher envelope/domain-argument fix and W24 runner regression now pass offline; this is software evidence only. Earlier 0517Z ExternalStrain failure remains `UNKNOWN`/`FAIL_OR_INCOMPLETE` with `safe_retry=false`; preserve it.
- **F01/F03:** scoped software integration verified; their remaining native acceptance is **NOT_RUN**. **F04:** nine-route and multi-Worker lifecycle integration is incomplete. **W25/W26:** remaining acceptance is not complete.
- **W24 science/static shape:** **NOT_FROZEN/NOT_RUN**. Cure history, mechanics limits, and equal-volume stepped/unstepped phase-field or level-set shape remain required. Windows COMSOL 6.3 remains required and unassessed; only macOS COMSOL 6.3 arm64/x86_64 are `USER_REQUESTED_SKIP`.

## Next work

Repair the classified offline regressions with their owning executors while completing F04 unified scheduling and precise job/Worker routing. Continue W23 full-3D managed-chain and versioned two-mode basis work, plus W24 executor and phase-field fixture implementation. At a stable source handoff, refresh the complete closure and run focused offline gates; submit a new setup-only candidate for independent review before any native launch. Do not replay the failed 0841Z freeze. Continue remaining P0/W01–W26 gaps and independent overall review.

Focused offline runner check (software only):

```bash
/private/tmp/comsol-mcp-w25-py312-20260926T2155Z/bin/python -m py_compile \
  tools/run_native_w24_cure_preflight.py tests/test_w24_cure_preflight_runner.py
/private/tmp/comsol-mcp-w25-py312-20260926T2155Z/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_w24_cure_preflight_runner.py
```

Recovery instructions now use the existing GitHub checkout and preserve state. Root `AGENTS.md` records continuous Luna execution and per-stage synchronization. Documentation checks confirm 26 work packages, 272 actions, 60 acceptance definitions and tracked progress links; these checks add no native or scientific acceptance. See [START_HERE](docs/full_project_execution/START_HERE.md).
