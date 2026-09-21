# G3.3 Execution Plan: Clean-room Recovery, Evidence Correction, and W17 Live Verification

## 1. Context & Scope

Following `NEXT_GOAL.md` and `PIN.json`, G3.3 targets the clean-room continuation of `Everwalker/comsol-mcp` pinned at commit `2cb4627924d1a3240818ea7cd00453d4bd2d2da8` (tree `dd3095e89c640c19aeb151cd0e8efe4af3c55802`).

### Operating Boundaries
- **Workstream Scope:** W17 results system and Gate A model reopen acceptance.
- **Stop Boundary:** Stop at W17; do NOT advance to W18–W26.
- **Platform Scope:** macOS aarch64 (Apple Silicon) commercial COMSOL 6.4 (Build 293) and Amazon Corretto JDK 11.
- **Unverified Platforms:** Windows x64, Linux, Intel Mac, COMSOL 6.3, and GUI Desktop remain explicitly `UNVERIFIED`.
- **Server Safety:** External shared server PID 5014 is untouched and never killed. Clean-room tests run on dedicated isolated ephemeral-port servers.
- **Ledger Integrity:** Historical ledgers (`phase4_1_acceptance.json`, `phase4_2_acceptance.json`, `w17_acceptance.json`) are preserved byte-for-byte. All corrections live in separate correction ledgers.

---

## 2. Milestones & Work Breakdown

### M0: Baseline Recovery, Audit & Ledger Correction
- Recover repository using network bootstrap (`tools/bootstrap.py`) verifying commit, tree, and tracked file inventory (3,580 files).
- Conduct AST and ledger audits of historical G3.2 claims.
- Create `evidence/w17_correction.json` and `evidence/phase4_3/BASELINE_REVIEW.json`.
- Downgrade `test_g3_gate_a2_f02_reopen.py` to `CONTROL_UNIT` and `test_g3_w17.py` to `UNIT_CONTRACT`.

### M1: Modular Remediation of W17 Defects (F01–F12)
- Modularize results architecture:
  - `comsol_mcp/_measure_spec.py`: Dimension-aware numerical feature mapping (`IntLine`, `IntSurface`, `IntVolume`), explicit denominator measures ($M = \int w\,d\mu$), population variance, standard deviation, and RMS.
  - `comsol_mcp/_solution_binding.py` & `FieldArray`: Rigorous 3D indexing `[expr][solnum][vertex]` along solution axis (Axis 1), dataset dependency chain cycle detection.
  - `comsol_mcp/_complex_transform.py`: Fail-closed imaginary extraction (no silent zero-padding).
  - `comsol_mcp/_artifact_store.py`: Path containment and traversal protection, pre-validation before disk creation, atomic replace (`os.replace`), bounded-memory chunk streaming reader.
  - `comsol_mcp/_probe_manage.py`: Strict separation of Model Definitions probes (`model.probe()`) from Results Derived Values (`model.result().numerical()`).
  - `comsol_mcp/_gate_a_reopen.py`: Production Gate A checker shared across positive chains and negative controls.

### M2: Unit & Counterexample Verification
- Run `tools/reproduce_w17_findings.py`: Reduce reproduced defects from 5 to 0.
- Execute unit test suites (`test_g3_3_remediation.py`, `test_g3_gate_a2_f02_reopen.py`, `test_g3_results.py`, `test_g3_w17.py`, `test_java_worker.py`): 166 passed, 1 skipped, 0 failed.

### M3: Live COMSOL Acceptance (C00–C17)
- Deploy unified acceptance test runner `tests/run_g3_3_live_acceptance.py`.
- Execute all 18 cases against isolated local COMSOL 6.4 mphserver.
- Verify Gate A saved model reopen in fresh worker without solving (Chain A steady gradient, Chain B transient decaying profile, Chain C modified continuation).
- Execute 4 negative controls through the same `verify_reopen` service.
- Verify dangerous state refusal, export security, chunk streaming, probe CRUD, packaging, and teardown.

### M4: Final Evidence Generation, Packaging & Closure
- Produce `evidence/phase4_3_acceptance.json` with status `G3_3_MAC_W17_VERIFIED_SCOPED`.
- Capture run artifacts in `evidence/phase4_3/runs/<run_id>/` with SHA256 sums.
- Build clean wheel `comsol_mcp-0.1.9-py3-none-any.whl` and verify out-of-tree installation in a fresh venv.
- Package source bundle archive `release/COMSOL_MCP_SOURCE_WORKPACK_<sha>.zip`.
- Finalize documentation and stop at W17.
