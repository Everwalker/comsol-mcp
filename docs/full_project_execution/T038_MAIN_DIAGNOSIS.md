# T038 historical failure triage

Main-agent read-only inspection, 2026-09-26, restored source a3418bd4546ecc33e26448e7f2a1982727a37153. This is source/evidence triage, not current test acceptance.

The FAIL in docs/comsol_mcp_design_v1/PROGRESS.md W01/W04 is a historical legacy outer-isError defect. Current comsol_mcp/_mcp_gateway.py mcp_result returns structuredContent and isError based on business success and unknown/cleanup state; tests/test_mcp_gateway.py covers business failure, invalid envelopes, partial evidence and transport exception redaction.

Later evidence explicitly supersedes the early defect within defined scope: evidence/phase2_acceptance.json T038_error_propagation records PASS for expression failures; evidence/phase3_acceptance.json W09_T038_T039 records actual full/domain/expert publication, strict fallback errors, pagination, object schemas, structured content and unknown-operation error checks. These remain historical evidence tied to their snapshots.

Do not run tools/w01_stdio_protocol_probe.py unchanged as final T038 acceptance: its baseline acceptance intentionally requires 50 tools and the old defect to be present. Preserve the historical probe/evidence. Current protocol checks must assert current published schema and correct failure semantics.

Current targeted execution: NOT_RUN at this checkpoint. Default python3 lacks pytest. Await the executor-owned test environment rather than modifying global packages or duplicating environments.

## Current targeted rerun

After the executor installed the locked dev extra in the existing APFS Python3.12 environment, the main agent ran `python -m pytest tests/test_mcp_gateway.py -q` against the current recovered source: **6 passed in 15.68s**, exit 0. Raw results: evidence/main_t038/pytest-rerun.log and junit-rerun.xml. The initial no-pytest failure remains in pytest.log. These tests cover protocol/unit behavior including a real stdio client exchange; they do not certify COMSOL-native operation or the full six-target T038 matrix. No product code was changed by main-agent diagnosis.
