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

## W02 — runtime/Java PoC

**Status: COMPLETE for currently accessible execution; other targets BLOCKED/UNVERIFIED.** The accepted run is `evidence/w02/runs/20260918T110411819403Z/`, reached through actual stdio MCP `runtime_poc_v64`, not a standalone Java substitute. Only the fixed recipe on macOS arm64 / COMSOL 6.4.0.293 / Corretto 11.0.31 is VERIFIED. Full platform support remains UNVERIFIED.

- Changed files: `_phase1_runtime.py`, `_tools_phase1.py`, `phase1_java/*.java`, registration, package data, `tools/w02_run_mcp.py`, `tests/test_phase1_runtime.py`, registration test and private/evidence ignore rules. New plan/API/operation notes document permissions and replay.
- Command: historical Python 3.12 venv `python tools/w02_run_mcp.py --pid 84749 --port 56388 --prefs <repository>/.phase1-private/20260918T175300Z/prefs-server` — **PASS**. Real requests/responses, PID/start-time/listener inventory, classpath/source hashes and every child command are retained.
- External JDK 11 compiles against all 25 official `apiplugins` JARs. Actual server build is read back. Solve/save/fresh-Java-process reopen/PNG export pass. Both analytic maximum errors are `1.2856382625159313e-13 < 1e-8`; PNG is 800×600, CRC/raster validated and visually inspected. This is an analytic unit-square PDE benchmark, not a general multiphysics physical gate.
- **T001 BLOCKED overall**, local fixed runtime PoC PASS: the other five real environment combinations are unavailable. **T002 BLOCKED**: no COMSOL 6.3 installation/second-version process. No mock substitutes.
- **T042 BLOCKED overall**: documented `hasProduct("ACDC")` returned true without checkout, but no actually absent documented product was identified for a negative test. ACDC physics operation is not thereby VERIFIED.
- **T059 PASS** within this phase: API-only observations stay UNVERIFIED; capability upgrade cites the actual MCP test and unavailable cases remain BLOCKED. See authoritative `evidence/phase1_acceptance.json` after W04 delivery.
- Failures retained: direct-Java historical run `20260918T175300Z` is **not MCP acceptance** and its old capability label is superseded. MCP run `20260918T105911073875Z` timed out after successful reopen because an idle Java non-daemon pool retained the client; thread dump showed `DestroyJavaVM` and an idle queue. Only that completed dedicated client was sent SIGTERM after identity verification. The server was untouched. Run `20260918T110318486018Z` failed on missing Pillow; the implementation now uses standard-library PNG validation.
- Unit verification: final suite **68 PASS**, including real path-boundary/classpath/PNG control tests; see `evidence/w04/pytest_final.txt`. Unit tests do not certify any engine.
- Dependency: W03 may now attempt Desktop against the registered server. No W05 was started.

The Python interpreter used for all test commands is `/Users/everwalker/Documents/Codex/2026-09-05/https-github-com-wjc9011-comsol-multiphysics/comsol-mcp-server/.venv/bin/python`. It was reused read-only; no user client/service configuration was changed.

## W03 — Desktop and cancellation PoC

**Status: BLOCKED for GUI acceptance; currently available inventory completed.** Evidence: `evidence/w03/runs/20260918T111700Z/`.

- Actual Computer Use action: `cua.getApp('/Applications/COMSOL64/Multiphysics/COMSOL Multiphysics.app')`. Exact response: **“Computer Use was not approved to use COMSOL Multiphysics”**. No window/control was accessible; no GUI click, permission-setting change, or cancellation was attempted.
- **T004 BLOCKED**: Desktop shared binding and alternating parameter readback cannot be verified. Binding is `unknown`.
- **T023 BLOCKED**: no tested supported native shared-server cancel route was found; GUI access was denied. Cancellation is UNVERIFIED. A real running solve was not cancelled and no false stop success was reported.
- **T052 BLOCKED**: permission denial is observed, but revoke/restore and GUI-cancel subcases are not executed. No permission bypass was attempted.
- Command: external JDK `javap -classpath '<COMSOL>/apiplugins/*' com.comsol.model.util.ModelUtil com.comsol.model.SolverSequence` — interface inventory retained. No public general cancel/stop/interrupt method was found in these two interfaces; this is static evidence only. Batch cancellation has a separate operating mode and is not a substitute for this case.
- Changed files: per-case five-part evidence, GUI refusal record, API inventory and this progress entry. No GUI automation implementation is claimed. The phase-wide 68 passing unit tests cover software control flow; they do not cover Desktop permissions or cancellation.
- Dependency: none of the W04 safety repairs depends on GUI; continue W04. Resume GUI cases with an authorized accessible Desktop and identified test job. Stop before W05.
