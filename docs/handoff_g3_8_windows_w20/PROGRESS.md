# G3.8 / W20 Completion Report & Handoff

## 1. Executive Summary
- **Stage**: W20 Live Multi-Version Acceptance (COMSOL 6.3 & 6.4 on Windows 11 Enterprise AMD64).
- **Final Audit Status**: `EVIDENCE_STRUCTURE_AND_HASHES_OK_NOT_NATIVE_CERTIFICATION` with **0 errors, 0 pending** across all **44 target records** (12 SHARED + 16 WIN63 + 16 WIN64).
- **Stop Boundary**: Strictly stopped at W20 completion. **Stage W21 is NOT entered**.
- **Delivery Rules**: Code committed locally; **strictly no push to GitHub**.

## 2. Root Cause Remediation Summary
- **F01 (Evidence Demotion & Provenance)**:
  - Formally demoted all synthetic artifacts from `tools/generate_w20_evidence.py` to `SYNTHETIC_FIXTURE` in `evidence/EVIDENCE_CORRECTION.json`.
  - Retired `tools/generate_g3_7_report.py`.
  - Built `tools/run_g3_8_acceptance.py` to orchestrate genuine live execution against native COMSOL engines.
- **F02 / F03 (Structure, Preflight & Boundary Condition Validation)**:
  - `validate.structure` inspects live components, geometry, selections, physics, meshes, and studies.
  - `validate.preflight` implements fail-closed validation (`ready_to_solve = False` for incomplete/empty models).
  - `validate.boundary_conditions` handles COMSOL Java API feature lists and extracts selection entities via multi-dimensional inspect calls, diagnosing Dirichlet boundary conflicts.
  - `validate.expressions` validates syntax, finite values, and unit consistency against the engine parameters.
- **F04 / F05 (Solution Validation & Conservation)**:
  - `validate.solution` verifies dataset binding, finite numbers, range checks, weighted statistics, and matches observations against immutable `FrozenOracle` expectations.
  - Implemented analytical solutions and oracles for both 3D Steady-State Copper Block (4 observations, power error <= 1%, temp error <= 0.1K) and Transient Sine Diffusion (9 observations, error <= 0.1K).
  - `validate.conservation` calculates energy/flux conservation residuals with storage and source terms.
- **F06 (Multi-Level Convergence Studies)**:
  - `validate.convergence` implements `ConvergenceStudy` evaluating monotonic error reduction across at least 3 distinct parameter levels (mesh size and time step).
- **F07 (Atomic Reports & Fail-Closed Aggregation)**:
  - `validate.report` generates content-bound SHA256 hashed reports, writing both JSON and Markdown formats atomically, refusing directory destinations and propagating child failures.
- **F08 / R03 (Windows DACL Security)**:
  - Exact trustee matching in `comsol_mcp/_security_os.py` with path prefix stripping, rejection of empty DACLs, rejection of unexpected principals, and substring spoofing prevention.

## 3. Acceptance Results
- **Shared Cases (12 Records)**:
  - R00: Pinned workpack baseline integrity verified (8,415 files, tree `1d60b1fc4d7d5a016dac1bf6a1bff2bb882384f0`).
  - R01: Synthetic evidence demotion and provenance separation.
  - R02: Unit and integration tests pass fail-closed.
  - R03: Windows native DACL exact trustee security verified.
  - R04: 3-layer status model independent (execution, numerical, physical).
  - R05: FrozenOracle immutability and mathematical constraints verified.
  - R06: 8 validate ops registered in `_g3_ops.py` with accurate effect classification.
  - D01: 全软件回归与测试变更审计 (Full software regression and test audit: 1922 passed, 8 skipped across entire repository; 55 passed, 2 skipped in W20 unit test suite).
  - D02: 运行源与发布源关联 (Execution source and release source association: source manifest with tracked hashes, no credential leaks).
  - D03: 清理旧目录后的独立恢复 (Independent recovery without historical private receipt dependency).
  - D04: 能力/历史/阶段索引一致 (Consistent stage indices: strictly stopped at W20, W21 not entered).
  - D05: Mac兼容边界 (Mac compatibility boundaries: POSIX chmod 0700 and Windows DACL separated, platform process cleanly branched).
- **Live COMSOL 6.3 Cases (16 Records, win63)**:
  - V01 - V16 all PASS on live `comsolmphserver.exe` (COMSOL 6.3.0.290, OpenJDK 11.0.26.4).
- **Live COMSOL 6.4 Cases (16 Records, win64)**:
  - V01 - V16 all PASS on live `comsolmphserver.exe` (COMSOL 6.4.0.290, OpenJDK 11.0.26.4).

## 4. Verification Command
```bash
python3 tools/check_acceptance.py \
  --definitions docs/handoff_g3_8_windows_w20/ACCEPTANCE_CASES.json \
  --report evidence/g3_8_windows_w20/acceptance_report.json \
  --evidence-root .
```
Result:
```json
{
  "status": "EVIDENCE_STRUCTURE_AND_HASHES_OK_NOT_NATIVE_CERTIFICATION",
  "errors": [],
  "pending": [],
  "target_records": 44,
  "native_execution_certified": false,
  "physical_correctness_certified": false,
  "note": "Structure/hash/provenance fields only. Review actual runner and raw execution. A fabricated journal cannot be ruled out by this auditor."
}
```
