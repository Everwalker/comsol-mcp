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

## W04 — nondestructive evaluation, variables and selections

**Status: PASS for current executable acceptance.** Final accepted run: `evidence/w04/runs/20260918T112244254506Z/`. All five cases and independent MCP-process MPH reopening passed on real macOS arm64 / COMSOL 6.4.0.293.

| Case | Result | Actual evidence |
|---|---|---|
| T003 | PASS | Production `load_visible_main_model` adopts the second preloaded model; the first model's numerical/plot/table structure and other-model sentinel remain unchanged. |
| T005 | PASS | max/min/avg/integral of `u` are 2 within 1e-8; an invalid symbol fails; user numerical expressions/table links/data and temporary-node cleanup are checked before/after. |
| T006 | PASS | Global and component groups each append two actual names, update one, re-solve and evaluate the dependent expression as 9. No `name`/`expr` variables are created. |
| T007 | PASS, bounded | Parent explicit/all/named and editable child selections read back correctly. The default PDE feature rejects edits; the custom flux feature rejects unsupported inheritance with the actual engine error, then remains editable. Successful inheritance on other physics is UNVERIFIED. |
| T033 | PASS | `pure_read` rejects; serialized ephemeral evaluation returns the real field and preserves user nodes. Invalid metric definitions reject before iteration writes/solve. Unit tests additionally inject cleanup failures and concurrent callbacks. |

- Independent fresh MCP process reopens `after.mph`; user numerical/plot/table definitions, data and variable groups are preserved; fresh evaluation confirms the variable is 9 and the PDE error remains below 1e-8. Full inventories are retained. COMSOL-generated `iexpr*` caches are not persisted user definitions and are compared separately, not mistaken for user data loss.
- Commands: historical venv `python tools/w04_run_mcp.py --pid 84749 --port 56388 --prefs <repository>/.phase1-private/20260918T175300Z/prefs-server` — **PASS**; `python -m pytest -q` — **68 passed**; `git diff --check` — PASS; final frozen-manifest check — 29/30 match; only `.DS_Store` differs. All supplied document files match. Current metadata is preserved without restoration; see `evidence/phase1_preservation_audit.json`.
- Changed code: `_model_ops.py` owns UUID result nodes, preserves array/complex structures and propagates cleanup failures; `_physics_ops.py` uses true variable setters/readback and correct selection target/API; `_tools_params.py` adds policy handling and explicit metrics; `_tools_snapshot.py` validates metrics before mutation; `_model.py`, model/connection/workflow tools and `_server.py` limit explicit prune and remove automatic prune. `tests/test_w04_safety.py` and the test-only real MCP fixture/driver provide repeatable regression entry points.
- Runtime failures were preserved, then fixed: loop-level setters failed for stationary/global results and were absent on field Eval; the implementation retrieves the full result and selects the solution axis. The fixture originally used a nonexistent boundary tag and reused one variable-group tag across scopes; these fixture errors were corrected. Stored-solution variables required re-solving. Earlier fresh-reopen comparison included regenerated internal expression caches; final acceptance explicitly compares user definitions and retains the raw diff.
- Migration/limits: see `PHASE1_OPERATIONS.md`. Scalar consumers must handle preserved arrays. Complex aggregation remains explicitly unsupported. Full dataset/outer-sweep typing (W17), model generations (W05), managed-worker lifecycle/cancellation (W06/W19), and typed MCP error semantics (W09) are not implemented. T038's legacy outer `isError` defect remains recorded FAIL; per-row numerical failures are inspected instead of mistaking outer transport success for acceptance.
- Final evidence ledger: `evidence/phase1_acceptance.json`; accepted runs include per-case environment/request/result/assertions/engine logs and SHA256SUMS. Failure attempts are retained. Private credentials remain ignored and are not delivered as evidence.

## Stop and handoff

W01 is complete; W02's accessible real tests and W03's available checks are complete with explicit blockers; W04's executable cases pass. **W05 and later remain NOT_RUN.** No user design file, service configuration, license file, or OS permission was overwritten.

The attach-only test server (PID 84749, loopback 56388) remains running for reproducible follow-up; no test solve is pending. Its historical identity must be rechecked before reuse or shutdown. The repository includes accepted MPH/PNG artifacts and can continue from the phase-one commits. No full six-combination platform certification is claimed.


## Fork delivery — 2026-09-18

Delivery target: `https://github.com/Everwalker/comsol-mcp`, branch `main`.
The fork initially pointed at the exact W01 upstream baseline
`ccca65aa8277d1205c5de5fb6221e460aca5997a`. Comparing it with the local import
commit `ddcdc15` showed only the added design package; all upstream files were
identical. The independent import history can therefore be joined to this
unchanged upstream history without replacing any remote user edits.

This delivery includes all W01–W04 stage commits, source, tests, evidence and
explicit blocked/unverified statuses. `AGENTS.md` records the user's standing
instruction to synchronize each completed phase to the fork with a non-force
push and verify the remote commit. It does not authorize W05 or a scheduled
background job. Private runtime preferences and credentials remain excluded.

## Phase two — active implementation (2026-09-18)

A subsequent explicit Goal authorizes legacy safety closure and W05 → W06 → W07,
stopping before W08. The phase-one stop statement above remains its historical
delivery record. Phase two started from clean `main` at
`bdf557d6e8a19489d6220fcedc01e7bc53fd5c25`; a fresh remote check returned the same
SHA. No phase-two completion or new platform certification is claimed here.

- **Legacy regressions: software PASS; new-backend engine regression NOT_RUN.**
  `evidence/phase2/legacy/pre_fix_regressions.txt` retains four failing regressions
  before the fixes; `post_fix_regressions.txt` records ten passing cases. The
  recorded full-suite run at that checkpoint has 97 passing tests. Fixes cover
  one solve per request, full static batch preflight, explicit partial writes,
  cleanup failure propagation, required metric failures and type conflicts.
  Successful metric evaluation alone is explicitly `acceptance_status=not_evaluated`.
- **W05: implementation and software validation in progress; engine NOT_RUN.**
  `_execution_contract.py` and `_execution_service.py` implement identity,
  generation, managed revisions, permission checks and canonical project paths.
  Review corrections reject user-declared dynamic effects and invalidate old
  revisions after external-change reconciliation. These are managed observations,
  not a COMSOL cross-client atomic CAS. Production registration now passes every
  legacy tool through `_mcp_gateway.py`; backend integration remains underway.
- **W06: implementation in progress; engine NOT_RUN.** The read-only inventory
  `evidence/phase2/inventory/w06_worker_api_inventory_20260918.json` rechecked the
  existing server PID/start time/listener and official JDK/client interfaces.
  It did not connect to or mutate COMSOL. A persistent Java worker and separate
  control service are being integrated; control transport tests establish that
  lost responses do not automatically resubmit operations.
- **W07: implementation in progress; engine NOT_RUN.** Atomic-save file-I/O tests
  and their limitations are in `evidence/phase2/w07/save/`. These tests do not
  satisfy a real COMSOL save, reopen, disk-full or process-restart acceptance.
  Durable operation/job storage and idempotency are being integrated after the
  execution contract and worker interfaces.

Current software test commands use the interpreter recorded in W02 above:
`python -m pytest -q tests/test_registration.py tests/test_mcp_gateway.py tests/test_control_client.py`
returned **10 passed**. All public MCP registrations carry execution metadata;
business failures propagate `isError=true`. No mock/unit result upgrades the
historically unavailable Windows, Intel Mac, COMSOL 6.3 or GUI combinations.
Their real-environment acceptance remains **BLOCKED / UNVERIFIED**.

Next dependency: finish W05 service integration, exercise the persistent-worker
main path and W06 queue/timeout behavior, then complete W07 durable recovery and
atomic-save acceptance. Record fresh evidence before phase completion and fork
synchronization. W08 remains **NOT_RUN**.

### Phase-two software checkpoint — implementation not yet accepted

- **Software regression PASS:** the final command using the W02 interpreter,
  `python -m pytest -q`, returned **156 passed in 5.10s**. Exact command,
  timestamp and output are in `evidence/phase2/software_checkpoint/`.
  The intermediate 153-pass/1-fail run is retained: its stale driver test
  incorrectly prohibited the real `run_study` submission required by T027.
- **Production transport PASS, health only:**
  `evidence/phase2/runs/20260918T220200Z/` records a fresh stdio MCP host reaching
  the existing control daemon in approximately 16 ms. The returned state was
  `worker_connected=false`. This does **not** verify COMSOL connection, model
  operations, long-solve responsiveness or a platform/version combination.
  Earlier startup failures remain in `20260918T131245049606Z/` and
  `20260918T131411143652Z/`; they are not silently replaced by this narrower test.
- **W05/W06/W07 real acceptance BLOCKED / UNVERIFIED:** automatic approval review
  rejected the live Phase-two workflow because it interpreted the visible user
  scope as W01–W04. The active Goal records W05–W07, but this discrepancy was
  not treated as permission to bypass the rejection. Explicit user confirmation
  was requested; see `evidence/phase2/authorization_checkpoint.json`. No live
  write/solve/save/reopen or fault-injection retry followed that rejection.
- Implemented production routing is stdio gateway → persistent control daemon
  → serialized execution service → persistent Java Worker. Software tests cover
  identities/revisions, private-path denial, actual queue-start deadlines,
  independent health/status/log reads, same-key reuse, UNKNOWN reconciliation,
  schema handling and atomic operation/job observations. A Worker-held global
  endpoint lock prevents a different control home from establishing a second
  queue for the same Server while the original Worker survives. The lock tests
  use isolated Java loopback workers and do not contact COMSOL.
- The legacy commit fix writes the configured artifact path explicitly while
  preserving the server model's observed source identity. The artifact path is
  not substituted for `getFilePath()` in the visible-main guard. Atomic save
  file-I/O tests preserve the previous complete file and failure candidates;
  **T030 real disk-full save remains NOT_RUN**. A separate 16 MB disposable image
  is prepared at `output/phase2-disk-test-volume`; it has not been filled or used
  for a COMSOL save. The image and runtime credentials remain ignored.
- Repeatable real test entries are `tools/phase2_run_mcp.py`,
  `tools/phase2_recovery_mcp.py` and `tools/phase2_disk_full_mcp.py`. T027 requires
  an actual pending Java `run` request before closing its submitting MCP host;
  a short fixture that misses that window cannot receive PASS. T028 verifies
  private PID, owner and command before any task-owned process signal. None of
  these entries authorizes terminating the COMSOL Server.
- Changed files and source hashes are recorded in
  `evidence/phase2/software_checkpoint/changed_files.json`. This remains an
  uncommitted Phase-two worktree, not a completed-phase delivery. The Phase-one
  fork commit remains `bdf557d6e8a19489d6220fcedc01e7bc53fd5c25`; Phase two has
  **not** been published or marked complete. After approval, finish all current
  executable real acceptance, update evidence, review, and synchronize the fork.
  Windows, Intel Mac, COMSOL 6.3 and GUI remain BLOCKED/UNVERIFIED. Do not enter W08.

The user subsequently answered **“批准”** to the explicit Phase-two real-engine
and task-owned process recovery approval request. The authorization blocker is
resolved in `evidence/phase2/authorization_checkpoint.json`; real acceptance
resumes without changing the W05–W07 scope or the prohibition on stopping the
COMSOL Server. The earlier rejection remains preserved as historical evidence.

### Phase-two approved live checkpoint — acceptance still in progress

- The approved production stdio MCP → control service → persistent Java Worker
  → COMSOL 6.4.0.293 main flow passed in
  `evidence/phase2/runs/20260918T141019Z/`: load, same-tag/type reuse and
  conflict rejection, managed revision conflict, request-key/hash conflict,
  real solve, analytic `max(abs(u-2)) < 1e-8`, atomic MPH save and reopen from
  a new MCP host. This checkpoint does not yet establish a new Java Worker
  reopen or T012 long-solve latency. Earlier failed attempts remain retained.
- Task-owned idle Worker replacement passed in
  `evidence/phase2/recovery/20260918T140715Z-worker-replace/`; the generation
  changed and old refs were rejected. Idle control-process replacement passed
  in `evidence/phase2/recovery/20260918T141150465969Z/`: same Worker, same
  original solve job, no replay. The COMSOL Server was not terminated.
- A subsequent full software run returned **166 passed in 5.68s**. This is an
  intermediate result; final source hashes and tests will follow the remaining
  driver/error-propagation changes.
- Long-solve, host-disconnect, private-path rejection, disk-full preservation,
  and independent Worker reopen evidence are being completed. A refined real
  fixture has 69,154 elements; its preparation is explicitly separated from
  MCP acceptance. Insufficient sampling windows remain BLOCKED.
- W05–W07 are not yet declared delivered; W08 remains NOT_RUN. Unavailable
  Windows/Intel Mac/6.3/GUI environments remain BLOCKED/UNVERIFIED.

### Phase-two boundary — W05/W06/W07 accessible acceptance

The current outcome is recorded in `evidence/phase2_acceptance.json`. It
separates successful scoped behavior from the external-event failure and
unavailable platform/GUI combinations. No W08 work was started.

**W05:** The production gateway now routes legacy calls through session/model
refs, Worker connection generations, managed revisions, request hashes,
permission/effect checks and canonical project paths. Same-key reuse and
different-body rejection, same-tag/type reuse and conflict, stale revisions,
and absolute/parent/private/symlink denial passed on the real current route.
The independent Java client parameter edit was observed by fingerprint, read
back as `2`, and the old revision write was rejected. However, the native
`ModelChangedHandler` counter did **not** advance: its supplemental assertion
remains **FAIL**, general property/event coverage **UNVERIFIED**, and full
Desktop T011 **BLOCKED**. Managed revisions do not provide a cross-client
atomic CAS. These limits are exposed in capability metadata; no full-model
external-edit guarantee is made. See
`evidence/phase2/followup/20260918T142300Z-external-api/`.

**W06:** One persistent Java Worker and one Server-wide engine queue are wired
into production stdio MCP. Separate cached health/status/log queries remained
responsive during an actual refined-model solve. T012 recorded 20 pending-run
samples per endpoint: p95 health/status/log was 4.75/4.11/6.11 ms. A model
inspection queued behind the solver and submitted no Java requests until the
solve finished. A 0.2-second execution deadline and 0.1-second no-progress
warning were observed while the job remained RUNNING; null execution timeout
also completed. Same-model queue expiry returned NOT_EXECUTED and the original
solve succeeded. T028 verified owned idle control and Worker replacement,
unchanged original job and rejected stale refs. No COMSOL Server was killed.
Evidence: `evidence/phase2/runtime-acceptance/`, `evidence/phase2/recovery/`,
and `evidence/phase2/followup/20260918T142510Z-same-model-queue/`.

**W07:** SQLite persists operations/jobs, identities/revisions, artifacts and
checkpoints with schema handling and atomic status updates. During real solve,
closing the submitting MCP host and reconnecting with the original key reused
one operation/job and retrieved its successful result (T027/T057). Actual
COMSOL save on the separate 16 MiB test volume failed for lack of space; the
last complete MPH retained its SHA-256 and ZIP integrity (T030). The filler
was removed and the zero-byte COMSOL scratch failure artifact preserved. A
normal eject returned resource busy; the image remains mounted, with no forced
eject or Server interruption. A newly started Java Worker independently loaded
the saved `after.mph` and passed the analytic metric. Evidence:
`evidence/phase2/disk-full/20260918T142204354278Z/` and
`evidence/phase2/followup/20260918T142450Z-fresh-worker-reopen/`.

**Legacy safety closure:** regression tests cover complete static batch
preflight, applied/failed/not-executed results, no second implicit solve, metric
failure blocking iteration success/snapshot, and cleanup/unknown state
propagation. Both required-metric and ordinary-expression failures were also
observed through real MCP with outer `isError=true`. Idempotent feature creation
now verifies type; legacy empty property values retain their prior semantics.

Run entry points (using the W02 Python interpreter and the approved private
COMSOL preferences/home):

- `python tools/phase2_run_mcp.py` — full production solve/metric/save chain;
  exact input and environment are retained per run.
- `python tools/phase2_recovery_mcp.py --mode host-disconnect` — real fixture
  loading, pending-solve control sampling, queueing and original-job recovery.
- `python tools/phase2_recovery_mcp.py --mode control-restart|worker-replace|path-security`
  — each mode has explicit arguments and private process identity guards.
- `python tools/phase2_disk_full_mcp.py` — requires the separately mounted
  bounded test volume and prior complete MPH; never fills an ordinary directory.
- `python tools/phase2_followup_mcp.py --mode fresh-worker-reopen|external-api|same-model-queue`
  — scoped supplementary cases; external API is not Desktop acceptance.

Final software output, changed-file hashes and evidence integrity audit are in
`evidence/phase2/final_software/` and `evidence/phase2/final_audit/`. Historical
failures remain intact: sandbox-denied `ps`/startup, Worker reflection and
allowlist defects fixed by later runs, and the first buffered-sampling window
miss. The successful follow-up did not rewrite these old verdicts.

Only macOS Apple Silicon / COMSOL 6.4.0.293 / external JDK 11 was tested.
Windows, Intel Mac, COMSOL 6.3 and GUI stay **BLOCKED/UNVERIFIED**. All current
executable cases have observations; the native callback limitation is an
explicit remaining issue, not a universal compatibility claim. This boundary
is prepared for the authorized ordinary fork synchronization. The next work
package is W08 (depends on W04/W06), **NOT_RUN**, requiring a new phase request.

Final phase-boundary software command `python -m pytest -q` returned
**172 passed in 5.30s** (`evidence/phase2/final_software/pytest.txt`).
The final idle control restart loaded the current capability metadata and
preserved the original job/Worker without replay; T028 PASS is retained in
`evidence/phase2/recovery/20260918T143106236174Z/`.
