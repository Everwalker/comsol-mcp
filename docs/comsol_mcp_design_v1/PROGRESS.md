# COMSOL MCP implementation progress

## W01 — frozen baseline and legacy-tool mapping

**Status: COMPLETE (inventory/protocol scope only).** This work package did not start, connect to, or change COMSOL. It does not certify any platform/version combination and does not implement W09's new registry or typed protocol. **T038 full acceptance: FAIL** because a registered business failure returns outer `isError=false`; its remaining W09 items are `NOT_RUN`.

### Baseline and preservation

- Initial workspace contents were frozen in `INITIAL_WORKSPACE_MANIFEST.sha256` before import. The list includes the supplied design files and `.DS_Store`, but excludes the manifest itself.
- Imported the exact upstream tree `ccca65aa8277d1205c5de5fb6221e460aca5997a` through `git archive`; the local development branch is `w01-baseline-audit`, import commit `ddcdc15c68203e1d6b96ee0ee1584941cf4bce4c`.
- No supplied design file was overwritten. Initial-manifest verification is captured in `evidence/w01/runs/20260918T174200Z/initial_manifest_check.txt`.

### Audited deliverables

- `tools/w01_generate_registry_audit.py` imports the unmodified baseline registry, asserts its 50 tools, captures each callable's Python signature plus FastMCP JSON input/output schemas, and maps every legacy name to catalog-defined proposed logical actions. Its generated artifact is `evidence/w01/runs/20260918T174200Z/legacy_tool_registry_and_mapping.json`.
- `tools/w01_static_findings.py` produces source-line evidence for F01–F12 in `evidence/w01/runs/20260918T174200Z/F01_F12_static_evidence.{json,md}`. Every finding is `STATIC_CONFIRMED_NOT_ENGINE_REPRODUCED`; W04/W05/W06/W09 own the fixes.
- `tools/w01_stdio_protocol_probe.py` performs `initialize`, `tools/list`, a successful `tools/call(server_info)`, an unconnected registered-tool failure, and an unknown-tool negative call over real stdio. Its five-part evidence case is `evidence/w01/runs/20260918T174200Z/cases/T038_stdio_registry/`.
- Baseline unit tests are preserved separately at `evidence/w01/runs/20260918T174200Z/cases/pytest_baseline/`.

### Commands and results

| Command | Result |
|---|---|
| historical venv `python -m pytest -q` | PASS — 40 passed in 0.27s |
| `python tools/w01_generate_registry_audit.py` | PASS — 50 legacy tools and 50 object input schemas captured |
| `python tools/w01_static_findings.py` | PASS — F01–F12 static evidence written |
| `python tools/w01_stdio_protocol_probe.py` | PASS (legacy basic transport); **T038 full acceptance FAIL** — MCP `2025-11-25`, 50 Draft 2020-12-valid object schemas, success call `isError=false`, unknown call `isError=true`; registered `get_parameters` fails internally but outer `isError=false` |
| `shasum -a 256 -c docs/comsol_mcp_design_v1/INITIAL_WORKSPACE_MANIFEST.sha256` | PASS — all frozen pre-import files still match |

### Compatibility gaps intentionally recorded, not repaired

- Tool results currently place legacy JSON inside a single `structuredContent.result` string. A registered tool's internal failure also returns outer `isError=false`. They do not yet implement the proposed typed `ActionResult`, output-schema publication, generated registry documentation, paging, or partial-success semantics. Therefore T038 is **not accepted as the W09/G2 protocol gate**; the W01 stdio test only proves baseline transport behavior and records the defect.
- The client cannot directly observe raw server stdout after framing. The probe proves no decoding failure and keeps server logs on stderr, but does not claim byte-level stdout isolation certification.
- No engine log exists because W01 is `ALL_NO_ENGINE`; every case contains `engine.log` with `NOT_RUN` and the stated boundary.

### Next dependencies

W02 may use the pinned baseline and the separate protocol evidence directory. W03 depends on W02 runtime/server evidence. W04 should implement the F01/F02/F03/F06 repairs using the exact static locations recorded here; all changes require new runtime tests and must not be inferred from this audit.
