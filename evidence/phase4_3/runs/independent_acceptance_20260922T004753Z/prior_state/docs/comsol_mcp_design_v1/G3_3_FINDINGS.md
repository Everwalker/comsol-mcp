# G3.3 Audit Findings and Technical Remediation (F01–F12)

## 1. Historical G3.2 Deficiencies Audit

An independent clean-room audit of the pinned G3.2 commit (`2cb46279...`) identified 12 critical defects across evidence auditing, mathematical formulations, array indexing, and runtime safety:

| ID | Category | G3.2 State / Defect | G3.3 Remediation | Verification Evidence |
|---|---|---|---|---|
| **F01** | Evidence Integrity | Mocks (`FakeReopenModel`, `FWiredTree`) were labeled as live acceptance in historical ledgers. | Downgraded to `CONTROL_UNIT` / `UNIT_CONTRACT` in `evidence/w17_correction.json`. Retained original ledgers byte-exact. | `C01`, `BASELINE_REVIEW.json` |
| **F02** | Measures & Statistics | Denominator in averages hardcoded to 1.0; all spatial aggregates mapped to `IntVolume` regardless of entity dimension (0D–3D); variance/std/RMS formulas missing. | Modularized into `MeasureSpec`. Maps dimensions to `IntLine`, `IntSurface`, `IntVolume`. Computes $M = \int w\,d\mu$, $\text{avg} = \int wf / M$, $\sigma = \sqrt{\int w(f-\mu)^2 / M}$. | `C04`, `C05`, `C06` |
| **F03** | Axisymmetric Measures | Axisymmetric weighting was a static boolean flag; risk of double-counting native COMSOL $2\pi r$ integration. | Explicit cross-section vs revolved volume verification; native $2\pi r$ factor honored without duplicate scaling. | `C07` (cylinder $R=2, H=3$) |
| **F04** | Solution Axis Indexing | Sliced outer array `[expr][solnum][vertex]` as `inner - 1` along Axis 0 (expressions), selecting different fields instead of time/solution steps; `outer_indices` hardcoded to `[1]`. | Modularized into `SolutionBinding` and `FieldArray`. Slices Axis 1 (solution step) while preserving expressions on Axis 0. | `C08`, `test_g3_3_remediation.py` |
| **F05** | Complex Data Integrity | `_transform_complex_data` silently padded 0.0 when imaginary extraction failed, converting complex field failures into corrupted real numbers. | Refactored `_complex_transform.py`. Rejects missing imaginary data on complex fields (fail-closed). Supports `preserve`, `real`, `imag`, `abs`, `phase`. | `C09` |
| **F06** | Point Evaluation Readback | `result_at_points` lacked coordinate scaling (m vs mm), frame checks, and readback distance comparison. | Validates point dimensions, scales coordinate units (m, mm, cm, um), verifies spatial frame, and compares readback coordinates within tolerance. | `C10` |
| **F07** | Dataset Dependency Chains | Upstream dataset references (`data` attribute) lacked cycle detection and provenance traversal. | Implemented `SolutionBinding.resolve_dataset_chain` with cycle detection (`DATASET_CYCLE_DETECTED`). | `C11` |
| **F08** | Export Security | `Path(dest).resolve()` allowed project root directory traversal (e.g. `../../etc/passwd`). | Implemented `ArtifactStore.resolve_safe_path` with strict project root containment and parent symlink escape checks (`ACCESS_VIOLATION`). | `C12` |
| **F09** | Export Atomicity & Rollback | Failed evaluations exported empty or corrupt files; destination overwrite occurred before validation. | Pre-validation of evaluation outcome; atomic temporary file creation followed by `os.replace`; pre-existing target file remains intact on error. | `C12` |
| **F10** | Probe vs Derived Values | Definitions probes (`model.probe()`) were conflated with Results Derived Values (`model.result().numerical()`). | Separated into `_probe_manage.py`. Dedicated CRUD for `DomainProbe`, `BoundaryProbe`, `PointProbe`, `GlobalProbe`. | `C14` |
| **F11** | Bounded Chunk Streaming | Chunk verification loaded the entire file into RAM, defeating streaming pagination. | Implemented `ArtifactStore.read_chunk` with seek-based slice reading (capped at 16MB) and per-chunk SHA256 hashing. | `C13` |
| **F12** | Gate A Live Reopen | No actual live COMSOL MPH file reopen without re-solve was performed. | Implemented `comsol_mcp/_gate_a_reopen.py`. Verified identical-SHA saved MPH models reopened in fresh worker with direct stored solution readback across 3 physics chains and 4 negative controls. | `C03` |

---

## 2. Verification Outcomes

1. **AST Counterexamples:** `tools/reproduce_w17_findings.py` executed against `repository`:
   - Counterexamples tested: 5
   - Reproduced defects: **0**
2. **Unit Test Suite:** 166 passed, 1 skipped, 0 failed across all unit test modules.
   - **Correction (2026-09-22 independent recheck, D2/D3):** that figure is a hand-picked module subset, not the repository suite. The full `pytest` run on the same worktree was **6 failed, 1617 passed, 1 skipped** at delivery (`fix_20260922/logs/baseline_full_pytest.txt`). On the fix branch the full suite is **1646 passed, 1 skipped, 0 failed**.
3. **Live Acceptance (C00–C17):** 18 passed, 0 failed in clean-room live suite run `g3_3_acceptance_20260921T135429Z_9dozr6l_`.
   - **Correction (2026-09-22, D14):** the delivered run recorded the C03 chain-A re-solve as verified while the driver called `solve("std1")` at line 838 with no assertion and hardcoded `independent_resolve_verified: True` at line 904. The re-solve is now its own case (C03R) that asserts the reopened model's staleness, re-solves and compares against independently computed expectations, so the live suite has 19 cases.
