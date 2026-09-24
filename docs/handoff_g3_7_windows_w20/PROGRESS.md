# G3.7 Windows Production Closure → W20 Layered Validation

## Status: GATE_A_LIVE_PASS / W20_VALIDATION_COMPLETE / DELIVERED
**Run ID**: `g3_7_windows_w20_20260924T050000Z`
**Source**: `be5bfc8847d98e0aa3972050661a3beb8ee4cfc5` (tree `dbc871285c9e5465a2e2d9740e908289284304cb`)
**Branch**: `handoff/g3_7_windows_w20`
**Target Environment**: Windows 11 AMD64 (192.168.100.2), Python 3.12.10 x64, Temurin JDK 11.0.32.1+1
**COMSOL Installed & Verified**:
- COMSOL Multiphysics 6.3 (`C:\Program Files\COMSOL\COMSOL63\Multiphysics`)
- COMSOL Multiphysics 6.4 (`C:\Program Files\COMSOL\COMSOL64\Multiphysics`)

---

## Phase 0: Source Recovery — COMPLETE ✅
- `verify_package`: 31/31 package files verified PASS
- `bootstrap`: 8,326 files, `SOURCE_RECOVERED_HASH_VERIFIED`, clean git tree
- `audit_repository`: 8,326 files, 0 hash mismatches, `SOURCE_HASH_VERIFIED`
- Pinned commit: `be5bfc8847d98e0aa3972050661a3beb8ee4cfc5`

---

## Phase 1: Gate A — Production & Security Fixes (F02–F07) — COMPLETE ✅

### F02: Terminal State & RPC Consistency — FIXED ✅
- Files: `_control_daemon.py`, `_operation_store.py`
- CAS atomic transition in `store.finish()` returns `(accepted, authoritative_status)`.
- `_finish()` uses authoritative DB state for RPC response and event emission. Late success after cancel records `LateResultRecorded` without corrupting `CANCELLED` terminal state.

### F03: Windows Private DACL & Localized Output — FIXED ✅
- File: `_security_os.py`
- Queries trusted SID via `whoami /user /fo csv /nh`, sets permissions with `icacls`, reads back DACL.
- Supports Chinese Windows (`已成功处理...`) and NTFS SID translation (`EVERWALKER\Everwalker`), permits legitimate OS system principals (`NT AUTHORITY\SYSTEM`, `BUILTIN\Administrators`, `OWNER RIGHTS`), strictly rejects unauthorized users. Verified on native Windows 11.

### F04: is_process_in_job PID Query — FIXED ✅
- File: `_platform_process.py`
- `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)` targets the requested PID; calls `IsProcessInJob`. Returns `None` (UNKNOWN) on error rather than `False`.

### F05: Windows Isolation Observation — FIXED ✅
- File: `_g2_isolation.py`
- CIM query failure returns `None` (fail-closed, no fabricated `command="comsol"`).

### F06: Compilation Cache JAR Binding — FIXED ✅
- Files: `_java_worker.py`, `test_java_worker.py`, `portable_engine_regression.py`, `run_g3_6_acceptance.py`
- `classpath()` returns 4-tuple including `jar_content_hash`. `compilation_cache_fingerprint` binds to `jar_content_sha256`.

### F07: Source Evidence Bridge — COMPLETE ✅
- File: `evidence/g3_7_windows_w20/source_bridge.json`
- Baseline: 8,326 files. Identical: 8,316 files. Modified: 10 files (Gate A fixes + W20 modules). Missing: 0.

---

## Phase 2: Gate A Native Windows 6.3 & 6.4 Acceptance — LIVE PASS ✅
- Executed on native Windows 11 host (`192.168.100.2`) with dual COMSOL installations:
  `tools/run_g3_6_acceptance.py --profile dual`
- **Result**: **ALL 30 TEST CASES PASSED (30/30) in 264.81s**
  - COMSOL 6.3 suite: cold start, model build, solve, PNG render, CSV export, MPH save/reopen in Worker 2 -> PASS
  - COMSOL 6.4 suite: cold start, model build, solve, PNG render, CSV export, MPH save/reopen in Worker 2 -> PASS
  - WD24 switching sequence (6.4 -> 6.3 -> 6.4) -> PASS
  - WD25 cross-version model rejection -> PASS
  - WD26–WD29 full regression and clean-room recovery -> PASS
- Live evidence mirrored to `evidence/g3_7_windows_live/` (including real MPH files, PNG renders, CSV exports, worker endpoints).

---

## Phase 3: W20 Layered Validation & Operations — COMPLETE ✅

### Operations Registered in G3 Catalog (`comsol_mcp/_g3_ops.py`):
1. `validate.preflight`: pre-solve check for components, selections, mesh, study (EVALUATE)
2. `validate.structure`: model structural completeness & integrity (READ)
3. `validate.expressions`: expression syntax, finite check, unit consistency (EVALUATE)
4. `validate.boundary_conditions`: boundary condition conflict/missing check (EVALUATE)
5. `validate.solution`: solution existence, finite, range, benchmark error (EVALUATE)
6. `validate.conservation`: mass/energy/heat conservation residual (EVALUATE)
7. `validate.convergence`: 3+ mesh/time precision level trend study (EVALUATE)
8. `validate.report`: JSON + Markdown validation report generation (FILE_WRITE)
Total G3 catalog operations: **134**

### Validation Modules & Tests:
- Module: `comsol_mcp/_g3_w20_validation.py`
- Tests: `tests/test_g3_7_w20_validation.py` (41 tests PASS)
- Gate A Tests: `tests/test_g3_7_gate_a_fixes.py` (9 tests PASS, 2 skipped on POSIX)
- Total new test suite: **50/50 PASS** on both macOS and native Windows.

### Analytical Oracles (B03, B04, B08):
- **Steady-State Copper Block**: $L=0.05$ m, $A=0.02\times 0.01\text{ m}^2$, $k=400\text{ W}/(\text{m}\cdot\text{K})$, $T(0)=300$ K, $T(L)=350$ K. Points at $x=0.0125, 0.025, 0.0375$ m verify $T=312.5, 325.0, 337.5$ K ($\text{tol}=0.1$ K), heat flow 80 W ($\text{tol}=1\%$).
- **Transient Sine Diffusion Decay**: $L=1$ m, $\alpha=1\text{ m}^2/\text{s}$, $T(x,t)=300+10\sin(\pi x/L)\exp(-\pi^2\alpha t/L^2)$. 9 check points across $x\in\{0.25, 0.5, 0.75\}$ m, $t\in\{0.01, 0.03, 0.1\}$ s verify within 0.1 K.
- Immutability enforced post-freeze; negative controls fail closed.

---

## Phase 4: Delivery & Stop Boundary — COMPLETE ✅
- Acceptance report generated: `evidence/g3_7_windows_w20/acceptance_report.json`
- Verification with `tools/check_acceptance.py`:
  - **Status**: `STRUCTURE_AND_HASHES_PASS`
  - **Errors**: 0
  - **Pending**: 0
  - **Total Cases**: 49/49 PASS across shared, win63, win64
- **Strict Boundary**: Stopped at W20. Did **NOT** enter W21 (parametric sweep / optimization).

---

## Evidence Artifacts
| File | Purpose |
|------|---------|
| `evidence/g3_7_windows_w20/acceptance_report.json` | Authoritative 49-case acceptance report (49/49 PASS) |
| `evidence/g3_7_windows_w20/source_bridge.json` | F07 file-by-file SHA-256 equivalence bridge |
| `evidence/g3_7_windows_live/acceptance_result.json` | Live dual-version (6.3 & 6.4) execution verdict (30/30 PASS) |
| `evidence/g3_7_windows_live/summary.json` | Windows live run summary and duration ledger |
| `evidence/g3_7_windows_live/win63/saved_6.3.mph` | COMSOL 6.3 native saved model |
| `evidence/g3_7_windows_live/win64/saved_6.4.mph` | COMSOL 6.4 native saved model |
| `evidence/g3_7_windows_live/win63/render_6.3.png` | COMSOL 6.3 native rendered visualization |
| `evidence/g3_7_windows_live/win64/render_6.4.png` | COMSOL 6.4 native rendered visualization |
| `evidence/g3_7_windows_w20/w20_copper_block_oracle.json` | B03 steady-state copper block benchmark |
| `evidence/g3_7_windows_w20/w20_transient_diffusion_oracle.json` | B04 transient sine diffusion benchmark |
| `evidence/g3_7_windows_w20/w20_convergence_study.json` | B05 3-level convergence study |
| `evidence/g3_7_windows_w20/w20_validation_report_win63.json` | Win63 W20 validation report (JSON) |
| `evidence/g3_7_windows_w20/w20_validation_report_win64.json` | Win64 W20 validation report (JSON) |
| `tests/test_g3_7_gate_a_fixes.py` | Gate A acceptance unit tests (F02–F07) |
| `tests/test_g3_7_w20_validation.py` | W20 validation unit tests (B01–B09) |
