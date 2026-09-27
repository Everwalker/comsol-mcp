# COMSOL MCP full-workpack progress

Updated 2026-09-27 UTC. Overall delivery is **PARTIAL**; execution continues through W01–W26 without a phase stop. This is the concise GitHub handoff. Recovery authority: [RESUME.json](docs/full_project_execution/state/RESUME.json). Evidence and rebuild commands: [checkpoint summary](docs/full_project_execution/release/PARTIAL_CHECKPOINT_20260927.json). Raw evidence remains local; historical state paths are not bundled artifacts.

**Current software checkpoint: 236 tests PASS** from an index-only clean snapshot (0 failures/errors/skips). This covers project control, W23 mapping/full-3D/preflight, release controls, W24 preflight, and artifact geometry runners; it is not full-suite or native acceptance. Stage publication follows non-force push and remote hash verification. Subsequent stages use this same progress entry and existing state files.

## Current checkpoint

- **W01–W22:** existing implementation and evidence baseline; overall acceptance remains open.
- **W23:** mapping/full-3D scoped software checks passed (59 tests); native acceptance is **NOT_RUN**.
- **W24:** the 0841Z setup attempt failed before engine birth at `project.inspect` (0 engine/Worker births and 0 solves). The public dispatcher envelope/domain-argument fix and W24 runner regression now pass offline; this is software evidence only. Earlier 0517Z ExternalStrain failure remains `UNKNOWN`/`FAIL_OR_INCOMPLETE` with `safe_retry=false`; preserve it.
- **F01/F03:** scoped software integration verified; their remaining native acceptance is **NOT_RUN**. **F04:** nine-route and multi-Worker lifecycle integration is incomplete. **W25/W26:** remaining acceptance is not complete.
- **W24 science/static shape:** **NOT_FROZEN/NOT_RUN**. Cure history, mechanics limits, and equal-volume stepped/unstepped phase-field or level-set shape remain required. Windows COMSOL 6.3 remains required and unassessed; only macOS COMSOL 6.3 arm64/x86_64 are `USER_REQUESTED_SKIP`.

## Next work

Continue W24 executor and phase-field fixture implementation offline while release completes F04. At a stable source handoff, refresh the complete closure and run focused offline gates; submit a new setup-only candidate for independent review before any native launch. Do not replay the failed 0841Z freeze. Continue remaining P0/W01–W26 gaps and independent overall review.

Focused offline runner check (software only):

```bash
/private/tmp/comsol-mcp-w25-py312-20260926T2155Z/bin/python -m py_compile \
  tools/run_native_w24_cure_preflight.py tests/test_w24_cure_preflight_runner.py
/private/tmp/comsol-mcp-w25-py312-20260926T2155Z/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_w24_cure_preflight_runner.py
```
