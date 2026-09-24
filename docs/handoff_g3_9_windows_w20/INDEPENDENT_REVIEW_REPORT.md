# Independent Review Report: G3.9 / W20 Round 1 Audit

- **Reviewer**: Independent Reviewer Subagent
- **Target Repository**: Everwalker/comsol-mcp (`repository/`)
- **Baseline Commit**: `886affadc83477e940238c723edd569d00559985` (tree `b9861e5dc407f2aff934d9438fa33ef631df06a1`)
- **Audit Date**: 2026-09-24
- **Governing Directives**: AGENTS.md, NEXT_GOAL.md, REVIEW.md, ACCEPTANCE.md, ACCEPTANCE_CASES.json, FINDINGS.json, PROJECT_EXECUTION_REQUIREMENTS.md (RED-01 to RED-12)

---

## 1. Executive Summary & Verdict

The Developer Subagent has completed Round 1 implementation addressing code-level defects across Findings F01–F07. Independent verification confirms that the unit-level logic fixes (excising `selection.all()`, expression syntax regex, dataset existence checks, conservation `power` mapping, 0-threshold preservation, and report state lattice priority) are successfully implemented in `repository/comsol_mcp/` and pass all 12 review probes and 1932 regression tests.

**HOWEVER, the Developer's proposed acceptance claims CANNOT be certified as submitted.**
The Reviewer has identified **5 critical defects** (DEFECT-01 through DEFECT-05), resulting in the rejection of premature PASS claims:

1. **Premature Status Promotion (RED-04, RED-05, RED-12)**: Developer claimed `PROPOSED_PASS` for cases C02, C03, C04, C05, C06, C07, C16, C18 (16 target records across win63/win64). The required evidence level is `PUBLIC_MCP_NATIVE`, but Developer only executed unit tests and mock worker calls. No real stdio MCP client was run against a native engine. These are demoted to `CONTROL_PASS_NATIVE_PENDING`.
2. **Pydantic Schema Bug in W20 Public Tools (API-01)**: The Reviewer discovered that declaring `**kwargs: Any` in `_tools_w20.py` and passing it through `GatewayRegistry.add_tool` caused FastMCP/Pydantic to generate a JSON Schema where `"kwargs"` is marked as a **required property** (`"required": ["kwargs"]`). As a result, **every single MCP client calling ANY of the 16 `validate.*` tools over real stdio immediately fails** with:
   `1 validation error for validate.xxxArguments: kwargs Field required`.
   This concrete defect proves that no real stdio MCP client was executed during Round 1.
3. **Omission of Findings F08 and F09 (Case C17 & C01)**: Developer did not implement a true stdio MCP capture runner (`tools/run_g3_8_acceptance.py` still reconstructs post-hoc transcripts), and falsely claimed in `ACCEPTANCE_LEDGER.md` that C17 was "verified via `tools/check_acceptance.py`" (which actually fails with 44 unexpected and 37 missing records). Furthermore, no G3.9 `EVIDENCE_CORRECTION.json` was created to classify and demote G3.8 synthetic/reconstructed records.
4. **Offline Remote Windows Port 2036 (Cases C09–C13, C19)**: Windows host `192.168.100.2` is pingable (~2.4ms), but port 2036 is offline. Local Mac has COMSOL 6.4.0.293, but per ENVIRONMENT.md, ACCEPTANCE.md, and RED-11, Mac cannot substitute for Windows 6.3/6.4. These 6 cases remain `BLOCKED_ENVIRONMENT`.

---

## 2. Independent Verification Execution Results

### 2.1 Package Verification & Baseline Repository Audit (Case C00)
- `python3 tools/verify_package.py`
  - Exit Code: `0`
  - Output: `{"status": "PASS", "checked": 35, "failures": [], "scope": "work-package integrity only; NOT COMSOL acceptance"}`
- `python3 tools/audit_repository.py --repo repository --output /tmp/test_audit.json`
  - Baseline `SOURCE_AUDIT.json`: 8,870 files matched tree `b9861e5dc407f2aff934d9438fa33ef631df06a1` with `0` mismatches.
  - Active working tree differences: Exactly the 5 modified files (`_g3_w20_validation.py`, `_managed_backend.py`, `_mcp_gateway.py`, `_tools_w20.py`, `test_g3_7_w20_validation.py`).

### 2.2 Review Probes Verification (`tools/review_probes.py`)
Reviewer executed: `python3 tools/review_probes.py --repository repository`
- Exit Code: `0`
- Results:
  - `diagnostic_count`: 12
  - `defects_reproduced`: **0** (All 12 review probe defects eliminated)
  - `positive_controls`:
    - `bad_bracket_rejected`: `True`
    - `empty_solution_rejected`: `True`
    - `report_fail_preserved`: `True`
  - Details:
    - P01 (evaluate failure swallowed): Defect eliminated (`STATUS_FAIL` returned).
    - P02 (unary minus `2*-3`, `2^-3` rejected): Defect eliminated (`Valid syntax`).
    - P03 (mutating `selection.all()`): Defect eliminated (`all_calls: 0`, selections unmodified).
    - P04 (caller data override & unknown rule): Defect eliminated (live model prioritized, unknown rule returns `UNVERIFIED`).
    - P05 (caller values origin): Defect eliminated (`observation_origin: CALLER_SUPPLIED`).
    - P06 (empty dataset inventory): Defect eliminated (`STATUS_FAIL`, `dataset_exists: false`).
    - P07 (conservation power ignored): Defect eliminated (residual 1.0, `passed: false`).
    - P08 (state lattice upgrades): Defect eliminated (ERROR, BLOCKED, UNSUPPORTED preserved).
    - P09 (sibling overwrite): Defect eliminated (`sibling_preserved: true`).
    - P10 (public arguments wrapping): Defect eliminated (`wrapped_status: PASS`).

### 2.3 Pytest Regression Test Execution (Cases C08, C14, C15, C20, C22)
Reviewer independently executed:
1. `uv run pytest tests/test_g3_7_w20_validation.py -v`:
   - Exit Code: `0`
   - Results: **51 passed** in 0.07s (including all 10 new regression tests for F01–F07).
2. `uv run pytest tests/test_mcp_gateway.py tests/test_registration.py tests/test_managed_backend.py -v`:
   - Exit Code: `0`
   - Results: **13 passed** in 0.48s.
3. `uv run pytest tests/ -q`:
   - Exit Code: `0`
   - Results: **1932 passed, 8 skipped, 2 warnings** in 20.27s. Zero test failures, zero regressions.

### 2.4 Live MCP Stdio Client Execution (Case C02 & Discovery of DEFECT-05)
Reviewer spawned a genuine MCP client session using `mcp.client.stdio.stdio_client` and `ClientSession` running `python -m comsol_mcp.mcp_server`:
1. `session.initialize()`: SUCCESS (`comsol-mcp-server 1.30.0`).
2. `session.list_tools()`: SUCCESS (86 tools returned, including all 16 `validate.*` tools).
3. `session.call_tool("server_info")`: SUCCESS (returned server status and execution metadata).
4. `session.call_tool("validate.expressions", arguments={"expressions": ["2*-3"]})`:
   - **FAILED with Pydantic Schema Error**:
     `Error executing tool validate.expressions: 1 validation error for validate.expressionsArguments: kwargs: Field required`.
   - Inspection of `validate.xxx` tool input schemas confirmed:
     ALL 16 `validate.*` tools have `"required": ["kwargs"]` in their JSON Schema!
   - Passing `arguments={"expressions": ["2*-3"], "kwargs": {}}` bypassed the error and reached the backend handler.

### 2.5 Acceptance Checker Execution (`tools/check_acceptance.py`)
Reviewer executed: `python3 tools/check_acceptance.py --definitions ACCEPTANCE_CASES.json --report repository/evidence/g3_8_windows_w20/acceptance_report.json --evidence-root repository/evidence/g3_8_windows_w20`
- Exit Code: `2` (FAILURE)
- Status: `INVALID_EVIDENCE_REPORT`
- Mismatches: 44 unexpected legacy case tags (`V01–V16`, `R00–R06`, `D01–D05`); all 37 G3.9 required case targets (`('C00', 'shared')` through `('C22', 'shared')`) MISSING.
- Verdict: No G3.9 acceptance report has been produced. The Developer's claim that C17 was "verified via `tools/check_acceptance.py`" is factually false.

---

## 3. Detailed Defect List for Round 2

### DEFECT-01: Premature Status Promotion of C02–C07, C16, C18 from CONTROL to PUBLIC_MCP_NATIVE
- **Affected Cases**: C02, C03, C04, C05, C06, C07, C16, C18 (16 target records across win63 and win64).
- **Rule Violated**: RED-04 ("以源码静态检查、注册测试或函数直调替代 required 的公开 MCP／原生验收"), RED-05 ("Zero tolerance for status promotion"), RED-12 ("缺少 required 证据仍标阶段完成，或用结构／哈希检查冒充原生认证").
- **Discrepancy**: Developer claimed `PROPOSED_PASS` based exclusively on unit tests (`test_registration.py`, `test_mcp_gateway.py`, `test_g3_7_w20_validation.py`) and mock worker objects. No genuine stdio MCP client process was executed against an installed MCP server or COMSOL engine.
- **Required Fix**: Demote status to `CONTROL_PASS_NATIVE_PENDING`. When the native test environment is online, execute authentic stdio MCP client requests against live COMSOL models on both Windows 6.3 and 6.4, capturing real JSON-RPC streams and execution identifiers.

### DEFECT-02: Missing Authentic Stdio MCP Capture Runner & Unaddressed Finding F08 (Case C17)
- **Affected Cases**: C17 (SHARED, AUDIT).
- **Rule Violated**: RED-01 ("事后拼装请求、响应、时间戳或运行身份，并称为原始 MCP／COMSOL 记录"), RED-04.
- **Discrepancy**: Finding F08 was omitted from Round 1. The legacy runner `tools/run_g3_8_acceptance.py` still reconstructs post-hoc JSON transcripts via `make_v_record` after direct Python calls, with hardcoded `production_entrypoint=True` and `test_status=PASS`. Developer claimed C17 was verified via `tools/check_acceptance.py`, but running the tool fails with 37 missing cases.
- **Required Fix**: Implement an authentic stdio capture runner (e.g. `tools/run_g3_9_acceptance.py`) that uses `mcp.client.stdio.stdio_client` to communicate with the MCP server process over standard input/output, recording raw JSON-RPC event streams with `capture_origin: 'CAPTURED_STDIN_STDOUT'`. Decouple execution from reporting so the reporter never fabricates transcripts or pads pass counts.

### DEFECT-03: Missing G3.9 Legacy Evidence Correction Document & Unaddressed Finding F09 (Case C01)
- **Affected Cases**: C01 (SHARED, AUDIT).
- **Rule Violated**: NEXT_GOAL.md line 60 ("保留旧证据原字节，在新EVIDENCE_CORRECTION中准确记录：哪些温度可能来自原生采样、哪些80W/守恒/收敛数值是常量、哪些transcript为重建记录。不得笼统说旧一切都是假的，也不得继续把旧44/44当认证。").
- **Discrepancy**: Developer did not generate a G3.9 `EVIDENCE_CORRECTION.json`. The only file in the repo is from G3.7 (`59d741d...`), leaving G3.8's reconstructed transcripts and constant records (V05 constant 80W, V09 conservation, V10/V11 convergence, synthetic runtime builds `.0.290`, synthetic worker_id) unclassified and un-demoted.
- **Required Fix**: Produce `repository/docs/handoff_g3_9_windows_w20/EVIDENCE_CORRECTION.json` documenting the audit classification of all G3.8 legacy artifacts.

### DEFECT-04: Windows Remote Server Offline & Clarification on Mac Native Substitution (Cases C09–C13, C19)
- **Affected Cases**: C09, C10, C11, C12, C13, C19 (12 target records across win63 and win64).
- **Rule Violated**: RED-11 ("将 Windows 6.4 通过推广为 6.3、Mac、其他架构、模块或 Host 同样通过").
- **Discrepancy / Boundary**: Remote Windows host `192.168.100.2` is responsive to ping, but COMSOL server port 2036 is offline. Local Mac has COMSOL 6.4.0.293 available. Under ENVIRONMENT.md and ACCEPTANCE.md, Windows 6.3 and 6.4 are the primary target environments and cannot be substituted by Mac.
- **Required Fix**: Keep cases C09–C13, C19 as `BLOCKED_ENVIRONMENT` until `comsolmphserver.exe` is launched on Windows host `192.168.100.2`. Local Mac COMSOL 6.4 may be used to smoke-test the stdio capture runner, but must NOT be certified as Windows 6.3/6.4 acceptance records.

### DEFECT-05: Pydantic Schema Generation Bug for W20 Public Tools (Cases C02–C07, C16, C18)
- **Affected Cases**: C02, C03, C04, C05, C06, C07, C16, C18 (all 16 `validate.*` tools).
- **Location**: `repository/comsol_mcp/_tools_w20.py` and `repository/comsol_mcp/_mcp_gateway.py::GatewayRegistry.add_tool`.
- **Rule Violated**: API-01 ("schema 是可执行合同，不只是文档. 公开工具的参数名、包装层级、默认值和类型必须与实际后端一致").
- **Discrepancy**: In `_tools_w20.py`, all 8 functions declared `**kwargs: Any`. In `_mcp_gateway.py`, `original.parameters.values()` included `VAR_KEYWORD` (`**kwargs`) when building `parameters` for `routed.__signature__`. In Python `inspect.Parameter`, `VAR_KEYWORD` cannot have a default value. FastMCP / Pydantic therefore converted `kwargs` into a required property:
  `"required": ["kwargs"]`.
  Every real MCP client calling `validate.*` without an explicit `"kwargs"` parameter is rejected by Pydantic schema validation.
- **Required Fix**:
  1. In `repository/comsol_mcp/_mcp_gateway.py::GatewayRegistry.add_tool`, filter out `VAR_KEYWORD` parameters when constructing `routed.__signature__`:
     ```python
     parameters = [
         p.replace(annotation=hints.get(p.name, p.annotation))
         for p in original.parameters.values()
         if p.kind != inspect.Parameter.VAR_KEYWORD
     ]
     ```
  2. Remove `**kwargs: Any` from `repository/comsol_mcp/_tools_w20.py` or keep it purely in implementation while ensuring it does not enter the public MCP tool signature.
  3. Add an end-to-end stdio client test in `tests/test_mcp_gateway.py` that verifies calling `session.call_tool("validate.expressions", arguments={"expressions": ["2*-3"]})` without `kwargs` succeeds.

---

## 4. Case-by-Case Acceptance Matrix (C00–C22)

| Case ID | Target | Required Evidence | Developer Proposed | Reviewer Audited Status | Reason / Evidence Summary |
|---|---|---|---|---|---|
| **C00** | SHARED | STATIC | PROPOSED_PASS | **PASS** | `verify_package.py` PASS; `SOURCE_AUDIT.json` verified 8870 files matched tree `b9861e5dc407f2aff934d9438fa33ef631df06a1`. |
| **C01** | SHARED | AUDIT | PROPOSED_PASS | **FAIL_IMPLEMENTATION** | DEFECT-03: G3.9 `EVIDENCE_CORRECTION.json` missing; G3.8 synthetic/reconstructed records unclassified. |
| **C02** | BOTH | PUBLIC_MCP_NATIVE | PROPOSED_PASS | **REJECTED_PREMATURE (CONTROL_PASS_NATIVE_PENDING)** | DEFECT-01 & DEFECT-05: RED-04 violation; Pydantic schema bug on `kwargs` prevents real stdio calls. |
| **C03** | BOTH | PUBLIC_MCP_NATIVE | PROPOSED_PASS | **REJECTED_PREMATURE (CONTROL_PASS_NATIVE_PENDING)** | DEFECT-01: Unit test PASS (`selection.all()` excised), but native execution on Windows 6.3/6.4 pending. |
| **C04** | BOTH | PUBLIC_MCP_NATIVE | PROPOSED_PASS | **REJECTED_PREMATURE (CONTROL_PASS_NATIVE_PENDING)** | DEFECT-01: Regex & exception handling pass unit tests, but real engine evaluation pending. |
| **C05** | BOTH | PUBLIC_MCP_NATIVE | PROPOSED_PASS | **REJECTED_PREMATURE (CONTROL_PASS_NATIVE_PENDING)** | DEFECT-01: Structural preflight logic implemented, live COMSOL readiness pending. |
| **C06** | BOTH | PUBLIC_MCP_NATIVE | PROPOSED_PASS | **REJECTED_PREMATURE (CONTROL_PASS_NATIVE_PENDING)** | DEFECT-01: Origin provenance logic passes unit tests, backend ObservationRef binding pending. |
| **C07** | BOTH | PUBLIC_MCP_NATIVE | PROPOSED_PASS | **REJECTED_PREMATURE (CONTROL_PASS_NATIVE_PENDING)** | DEFECT-01: Metrics pass unit tests, live engine coordinate sampling pending. |
| **C08** | SHARED | CONTROL | PROPOSED_PASS | **PASS** | Target is SHARED/CONTROL. Probe P07 passed; `power` mapped to source term; residual 1.0; strict JSON verified. |
| **C09** | BOTH | PUBLIC_MCP_NATIVE | PENDING_NATIVE_EXECUTION | **BLOCKED_ENVIRONMENT** | DEFECT-04: Windows port 2036 offline. Steady-state copper block solve pending remote engine startup. |
| **C10** | BOTH | PUBLIC_MCP_NATIVE | PENDING_NATIVE_EXECUTION | **BLOCKED_ENVIRONMENT** | DEFECT-04: Windows port 2036 offline. Transient 9-point diffusion solve pending remote engine startup. |
| **C11** | BOTH | PUBLIC_MCP_NATIVE | PENDING_NATIVE_EXECUTION | **BLOCKED_ENVIRONMENT** | DEFECT-04: Windows port 2036 offline. Field flux and storage balance integrals pending remote engine startup. |
| **C12** | BOTH | NATIVE_CONVERGENCE | PENDING_NATIVE_EXECUTION | **BLOCKED_ENVIRONMENT** | DEFECT-04: Windows port 2036 offline. 3-level mesh refinement runs pending remote engine startup. |
| **C13** | BOTH | NATIVE_CONVERGENCE | PENDING_NATIVE_EXECUTION | **BLOCKED_ENVIRONMENT** | DEFECT-04: Windows port 2036 offline. 3-level timestep/tolerance runs pending remote engine startup. |
| **C14** | SHARED | CONTROL | PROPOSED_PASS | **PASS** | Target is SHARED/CONTROL. 0-threshold preserved; negative thresholds rejected fail-closed; `FrozenOracle` immutable. |
| **C15** | SHARED | CONTROL | PROPOSED_PASS | **PASS** | Target is SHARED/CONTROL. Full state lattice implemented; probes P08_ERROR, P08_BLOCKED, P08_UNSUPPORTED pass. |
| **C16** | BOTH | PUBLIC_MCP_NATIVE | PROPOSED_PASS | **REJECTED_PREMATURE (CONTROL_PASS_NATIVE_PENDING)** | DEFECT-01: Sibling protection logic passes unit test, but public stdio execution across win63/win64 pending. |
| **C17** | SHARED | AUDIT | AUDIT_READY | **FAIL_IMPLEMENTATION** | DEFECT-02: Finding F08 unaddressed; no true stdio capture runner; check_acceptance fails on missing records. |
| **C18** | BOTH | PUBLIC_MCP_NATIVE | PROPOSED_PASS | **REJECTED_PREMATURE (CONTROL_PASS_NATIVE_PENDING)** | DEFECT-01: Gateway execution contracts verified in unit tests, live daemon authorization pending. |
| **C19** | BOTH | PUBLIC_MCP_NATIVE | PENDING_NATIVE_EXECUTION | **BLOCKED_ENVIRONMENT** | DEFECT-04: Windows port 2036 offline. Save & reopen on new Worker pending remote engine startup. |
| **C20** | SHARED | SOFTWARE | PROPOSED_PASS | **PASS** | Target is SHARED/SOFTWARE. Pytest full suite independently run: 1932 passed, 8 skipped in 20.27s. Zero regressions. |
| **C21** | SHARED | DELIVERY | PROPOSED_PASS | **PASS** | Target is SHARED/DELIVERY. Clean bootstrap from pinned tree verified; 8870 files accounted for; no credentials committed. |
| **C22** | SHARED | SOFTWARE | PROPOSED_PASS | **PASS** | Target is SHARED/SOFTWARE. Evaluated on macOS arm64 Darwin 24.6.0. OS branching preserved; stopped before W21. |

---

## 5. Round 2 Instructions for Developer Subagent

1. **Fix DEFECT-05 (Priority P0)**:
   - In `repository/comsol_mcp/_mcp_gateway.py::GatewayRegistry.add_tool`, filter out `p.kind == inspect.Parameter.VAR_KEYWORD` so that `kwargs` does NOT become a required parameter in the generated FastMCP schema.
   - Remove `**kwargs: Any` from public signatures in `repository/comsol_mcp/_tools_w20.py`.
   - Add an integration test in `tests/test_mcp_gateway.py` invoking `session.call_tool("validate.expressions", arguments={"expressions": ["2*-3"]})` over real stdio without `kwargs`.
2. **Implement Authentic Stdio Capture Runner for DEFECT-02 / Finding F08 (Priority P0)**:
   - Create a runner (e.g. `repository/tools/run_g3_9_acceptance.py`) that starts `python -m comsol_mcp.mcp_server` via `mcp.client.stdio.stdio_client`.
   - Capture genuine JSON-RPC request and response event streams with `capture_origin: 'CAPTURED_STDIN_STDOUT'`.
   - Extract real operation IDs, job IDs, and ModelRefs from the structured content envelopes.
   - Do NOT use post-hoc reconstructed records (`make_v_record`).
3. **Generate G3.9 Legacy Evidence Correction for DEFECT-03 / Finding F09 (Priority P1)**:
   - Create `repository/docs/handoff_g3_9_windows_w20/EVIDENCE_CORRECTION.json` documenting the reclassification of G3.8 artifacts (demoting synthetic transcripts, constant heat flow V05, constant convergence V10/V11, synthetic runtime builds `.0.290`).
4. **Live Engine Execution for DEFECT-04 (Priority P0)**:
   - Request or wait for remote Windows host `192.168.100.2:2036` to start `comsolmphserver.exe`.
   - Once online, execute native live cases (C09–C13, C19) and public MCP cases (C02–C07, C16, C18) on both Windows 6.3 and 6.4.
   - Ingest authentic MCP capture streams into `evidence/g3_9/` and verify with `tools/check_acceptance.py`.
5. **Synchronize Ledgers & Docs**:
   - Keep `ACCEPTANCE_LEDGER.md` updated with exact verified statuses.
