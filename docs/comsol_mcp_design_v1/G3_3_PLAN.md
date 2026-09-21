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

---

## 3. Case ID Mapping: Historical Txxx Specification to G3.3 C00–C17

| Acceptance Case ID | Original Catalog / Spec ID | Description & Acceptance Requirement | Evidence Level | G3.3 Status |
|---|---|---|---|:---:|
| **C00** | T001 (Clean-Room Part) | Clean recovery from pinned commit/tree, no old path reuse, wheel build & out-of-tree venv installation. | protocol | **PASS** |
| **C01** | Historical Ledgers Audit | Preservation of phase4_1/4_2/w17 ledgers byte-for-byte; creation of correction ledgers (`w17_correction.json`). | static | **PASS** |
| **C02** | T033, T038 | Dangerous state transitions: cleanup failure mapped to `STATE_UNKNOWN`, export refused on evaluate failure. | protocol | **PASS** |
| **C03** | Gate A, T019, F02 | Saved MPH identical-SHA reload in fresh worker without re-solve across Chains A, B, C; 4 negative controls. | numerical | **PASS** |
| **C04** | T013 (Constant Stats) | Constant field $f=2, V=3$: integral=6, average=2, std=0, RMS=2 with verified denominator $M=3.0$ (not 1.0). | numerical | **PASS** |
| **C05** | T013 (Multi-dim/Sel) | Entity dimension mapping (`IntLine`, `IntSurface`, `IntVolume`); partial selection vs whole domain. | protocol | **PASS** |
| **C06** | T013 (Non-Uniform) | Analytical field $f=x+2y$ on $[0,2] \times [0,3]$: area=6, integral=24, avg=4, var=10/3, std=$\sqrt{10/3}$, RMS=$\sqrt{58/3}$. | numerical | **PASS** |
| **C07** | T013 (Axisymmetric) | Revolved cylinder $R=2, H=3$: volume=$12\pi$, avg $r=4/3$, lateral area=$12\pi$; native $2\pi r$ confirmed un-doubled. | numerical | **PASS** |
| **C08** | T021 (Solution Axis) | Slicing along solution axis (Axis 1) in 3D array `[expr][solnum][vertex]`; bounds checks & metadata binding. | protocol | **PASS** |
| **C09** | T014 (Complex Fields) | Complex field modes (`preserve`, `real`, `imag`, `abs`, `phase`); fail-closed rejection of missing imaginary data. | numerical | **PASS** |
| **C10** | T015 (Point Coordinates) | Coordinate scaling between meters and millimeters; coordinate distance readback verification. | protocol | **PASS** |
| **C11** | Dataset / Nodes | Dataset provenance chains (`Solution -> CutPlane/Point`); cyclic graph detection (`DATASET_CYCLE_DETECTED`). | protocol | **PASS** |
| **C12** | T035, T030 | Export directory traversal rejection (`../../etc/passwd`); failed export preserves pre-existing file intact. | protocol | **PASS** |
| **C13** | T049 (Chunk Stream) | Bounded memory chunk streaming (16KB payload in 8KB slices); seek-based reading without full-file buffer. | protocol | **PASS** |
| **C14** | Probe / Table CRUD | Separation of Model Definitions probes (`model.probe()`) from Results Derived Values (`model.result().numerical()`). | protocol | **PASS** |
| **C15** | T010, T012, T027 | Idempotent evaluate calls; control plane responsiveness during solver operations (health check $\le 2.0$s). | protocol | **PASS** |
| **C16** | Packaging & Schema | Dependencies lock, wheel file presence, out-of-tree import, and schema validation. | static | **PASS** |
| **C17** | Teardown & Scoping | Safe isolated server shutdown, lock release, evidence ledger finalization, and W17 stop boundary enforcement. | protocol | **PASS** |

---

## 4. Source File & Semantic Review Coverage

- **Total Tracked Files:** 3,580 files (verified against `review/source_inventory.json`).
- **Remediated Production Modules:**
  - `comsol_mcp/_g3_results.py`: Core results dispatch, evaluate, at_points, field_export.
  - `comsol_mcp/_measure_spec.py`: Dimension-aware integration and statistical aggregation.
  - `comsol_mcp/_complex_transform.py`: Fail-closed complex data transformations.
  - `comsol_mcp/_solution_binding.py`: Multidimensional array slicing along solution axis.
  - `comsol_mcp/_artifact_store.py`: Path traversal protection, atomic publication, bounded chunk streaming.
  - `comsol_mcp/_probe_manage.py`: Definitions probe CRUD segregated from derived values.
  - `comsol_mcp/_gate_a_reopen.py`: Production Gate A model reopen and stored solution verification service.
- **Audited Historical Evidence:**
  - `evidence/phase4_1_acceptance.json` (preserved byte-exact)
  - `evidence/phase4_2_acceptance.json` (preserved byte-exact)
  - `evidence/w17_acceptance.json` (preserved byte-exact)
  - `evidence/w17_correction.json` (corrections active)
  - `evidence/phase4_3/BASELINE_REVIEW.json` (audit ledger active)
  - `evidence/phase4_3_acceptance.json` (G3.3 verified ledger active)

---

## 5. Platform Support Matrix

| Platform / Environment Target | Status | Certification Basis |
|---|:---:|---|
| **macOS Apple Silicon (aarch64) + COMSOL 6.4.0.293 + JDK 11** | **VERIFIED** | Tested live against commercial installation; 18/18 acceptance cases pass. |
| **macOS Intel (x86_64)** | **UNVERIFIED** | No physical x86_64 hardware in test environment. |
| **Windows x64** | **UNVERIFIED** | Remote Windows testing disabled per NEXT_GOAL §0. |
| **Linux x86_64 / aarch64** | **UNVERIFIED** | No Linux commercial COMSOL environment available. |
| **COMSOL Multiphysics 6.3** | **UNVERIFIED** | Secondary COMSOL version not installed. |
| **COMSOL GUI / Desktop Interaction** | **UNVERIFIED** | Headless server mode only; GUI automation disabled. |

