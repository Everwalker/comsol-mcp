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

## Mac integration / Windows native-node handoff — 2026-09-19

New Goal scope: retain existing code and W01–W07 evidence, close foundational
Windows handoff/runtime gaps, and regress the same traceable source snapshot
on applicable hosts. No W08–W12 expansion and **no automatic main push** are
authorized this round; this supersedes the usual phase-sync rule for this Goal.

Baseline: branch `phase2-delivery`, HEAD
`28605a6896668a64775ac6c1192ed50fef94b47f`. Public source was clean at handoff;
the only untracked input was the private local handoff directory, now excluded
through repository-local `.git/info/exclude` without replacing prior rules.
The design JSON files remain definitions with NOT_STARTED/NOT_RUN defaults;
actual history is the existing phase acceptance ledgers, not those defaults.
Baseline/source inventories and historical ledger hashes are in
`evidence/windows_handoff/20260919/baseline.json`; raw host/account/network
records stay in the ignored local handoff area.

| Work package | Existing implementation / evidence to preserve | Current gap |
|---|---|---|
| W01 | Frozen baseline, legacy mapping and real stdio audit | Reconcile this node/snapshot against current code, not Windows' historical empty source directory |
| W02 | Mac arm64 6.4 real fixed recipe, render/save/reopen | Windows source, native dependencies, JDK11, secure Server and license verification |
| W03 | API inventory; GUI/cancel BLOCKED | No new GUI evidence; do not infer Desktop sharing from SSH |
| W04 | Mac real preservation/variables/selections/ephemeral evaluation PASS within fixture | Same-source Windows regression not yet executed |
| W05 | Identity/revision/path guards; parameter conflict detection | Existing native event callback FAIL remains; Windows native paths/process identity require audit |
| W06 | Persistent Worker, serialized queue, real timeout/response evidence | Windows executable/classpath/process liveness and lifecycle portability |
| W07 | Durable jobs/idempotency, host recovery and atomic-save evidence | Native Windows local-disk regression after prerequisites |

Initial SSH checkpoint: current source address/route match the historical
restricted rule, and the ED25519 host fingerprint matches the handoff. A
single bounded strict-host-key public-key login attempt returned authentication
denied. **SSH authentication BLOCKED; file transfer, Windows dependencies,
MCP and COMSOL NOT_RUN; Windows platform UNVERIFIED.** The user authorized
reuse of the existing public key; a guarded administrator enrollment script
was prepared locally for the user's secure Windows terminal. No password or
private key is requested or copied. Existing firewall/sshd/COMSOL settings
have not been changed. Independent Mac portability fixes and package
preparation continue; this checkpoint is not a phase pass.

### SSH and native-input delivery checkpoint

After the user confirmed key enrollment, strict pinned-host-key public-key
login returned exit 0 and the expected Windows account/SID. A unique probe
file was transferred by SCP; Windows SHA256 matched the Mac source:
`d74b5c03391572402f4f41abb95a6d039cd41f3a670996f96c47b011a711865a`.
**SSH identity and file transfer PASS**; this supersedes only the initial
SSH blocker above, not the historical failure or any COMSOL gate.
Existing Windows design files were inventoried with hashes before delivery;
a new no-overwrite release directory holds native inputs separately.

Fresh Windows inventory confirms all 25 official client-manifest JARs exist
in `apiplugins`; the general `plugins` directory lacks some manifest entries.
The source now selects a complete single root, preferring `apiplugins`, and
rejects partial mixed roots. Windows java/javac suffixes, classpath separator,
read-only process liveness/creation identity, and PID-reuse handling were
fixed. Default pytest discovery now excludes private extracted snapshots.
Mac software regression: **190 PASS** (`python -m pytest -q`), including
real test-owned Java loopback protocol tests; this is not COMSOL acceptance.
Production MCP health-only PASS reused the prior Worker and therefore does
not validate the modified Java source against COMSOL. Fresh same-snapshot
engine regression remains pending. Windows dependencies are being prepared;
MCP/Server/license/GUI acceptance remain **NOT_RUN / UNVERIFIED**.

### Native dependency and cross-platform failure checkpoint

Windows native dependencies now **PASS**: 39 verified wheels (including the
Windows-only `pywin32` and `colorama` dependencies missing from the initial
Mac resolution), offline pip installation and `pip check` exit 0, CPython
3.12.10 x64, and Corretto java/javac 11.0.32.1. Installer Authenticode was
verified; existing interpreters/JDKs and PATH were preserved. See
`evidence/windows_handoff/20260919/native_dependencies.json`. Initial manifest
path, AppleDouble sidecar and missing-conditional-dependency failures remain
in private evidence; they were not discarded or labelled engine acceptance.

Snapshot `win-cp312-28605a689666-dirty-20260919-b2c` was verified on both hosts.
Windows full software run returned **192 PASS, 9 skipped, 6 FAIL**. Five
failures exposed `fsync` of a read-only candidate handle; one was a POSIX-only
path assertion. Mac source now opens the candidate with nontruncating `r+b`,
retains failure-before-publication semantics, and tests both candidate/original
preservation on flush failure. Path assertions are platform-neutral. Mac
full regression after these fixes: **209 PASS**; a superseding Windows snapshot
and native retest are required. The b2c failure record is retained in
`evidence/windows_handoff/20260919/windows_b2c_failures.json`.
An initial Java test incorrectly selected a nonexistent COMSOL63 path; this
is a test setup failure, **not evidence that COMSOL64 is absent**. The installed
COMSOL64 manifest was previously read and verified; native protocol tests must
use that actual root.

Windows localhost access rule: user separately authorized the exact temporary
stock RemoteAddrValve change. Original backup/hash and ACL are preserved,
patched XML/hash verified. Auth-enabled trial startup reached the username
prompt and its owned trial process was stopped. Secure user-local credential
enrollment is pending; actual local-allow/remote-deny probes, license checkout
and real MCP/Worker/Server solve remain **BLOCKED/NOT_RUN**. The temporary
configuration remains active only for the authorized test window, with guarded
restoration prepared. No firewall or sshd configuration was changed.

Mac current-source real regression is **BLOCKED**, independently of Windows:
authenticated read-only inventory found an unsaved in-memory model in the
existing Server session. The historical Worker and models were left untouched.
See `evidence/mac_same_snapshot/20260919/current_snapshot_blocker.json`.
The portable regression driver now separates native Java fixture preparation
from production MCP/new-Worker execution, checks parameter readback and metric
preservation, and requires an idle control queue plus owned-process identity
before authenticated disconnect for independent Worker reopen. Driver code and
software tests do not replace the currently blocked real-engine run.

This round remains **IN_PROGRESS / NOT_PASSED**. Windows platform support is
UNVERIFIED; Desktop sharing and the historical native-event callback failure
remain unresolved. W08–W12 have not been started and no push was performed.

### Final native source and security regression — 2026-09-19

The frozen runtime/test snapshot is
`win-cp312-28605a689666-dirty-20260919-c6f`, based on HEAD
`28605a6896668a64775ac6c1192ed50fef94b47f` plus the declared working-tree
patch/new files. Archive SHA256:
`66caaa792766ca2138818151a2748f40b22ab1f89e5e0a62b905bebe076b43b5`.
All 123 manifest entries matched Mac before delivery; final progress/evidence
updates occur after packaging and do not change runtime/test source.
`evidence/windows_handoff/20260919/source_snapshot.json` records file hashes.

Further native regression found that Windows profile directory ownership is
not a reliable process identity. Worker lock ownership now uses the native
Windows token identity, sets the owner only for a newly created final lock
root, and refuses to take over an existing foreign root. Inherited DACLs are
preserved. The c4d run retained **208 PASS / 2 FAIL** in the ACL test helper;
c5e retained **209 PASS / 1 FAIL** where the helper incorrectly required an
explicit user SID. Actual private pytest files use OWNER RIGHTS with a verified
Administrators owner. The helper now verifies the owner, resolves OWNER RIGHTS,
rejects other Allow principals, and opens the actual endpoint with nontruncating
read/write access. Foreign-owner and Everyone rejection tests were added.
These metadata unit tests are not substitutes for Windows ACL execution.

Mac final full suite (`python -m pytest -q`, project Python 3.12, external JDK11,
installed COMSOL64 classpath): **212 PASS / 1 Windows-only skip**, exit 0.
Evidence: `evidence/mac_same_snapshot/20260919/full_suite_acl_owner_rights.json`.
Earlier b564/c4d evidence is retained. The production Worker source hash is
`b564ff8963b1b8ec04c7475a94e1ab041d69e70d28a183deedb4c9ce47e17b5a`;
final `tests/test_java_worker.py` hash is
`6e595ced34731a22732e6ff54177d694a42a783ab2d415995701fc88141c0fd2`.

Changed-file scope: `_platform_process.py`, `_control_client.py`,
`_control_daemon.py`, `_java_worker.py` and `PersistentComsolWorker.java` cover
native process identity, launch/classpath and lock handling; `_atomic_save.py`
fixes nontruncating Windows flush handles. Corresponding tests, pytest discovery,
`requirements-windows-cp312.txt`, `tools/windows_handoff_snapshot.py` and
`tools/portable_engine_regression.py` provide repeatable verification/delivery.
No public tool names were added by this round. No commit or push was performed.

Temporary Windows installation change has now been **RESTORED**. Fresh guards
found no COMSOL process/listener before restoration; independent readback
confirmed original SHA256
`8bb393d2d803d1b7f6b124498742fa23b6310db9a28a6334f7462ec12d674bbe`,
original ACL, valid XML, no active localhost Valve and no dedicated listener or
port file. See `evidence/windows_handoff/20260919/server_gate.json` for hashes
of both raw restore and independent post-restore records. Local-allow versus
remote-deny, authentication/license checkout and engine solve remain NOT_RUN.
The prior interactive starter deliberately refuses the restored original hash;
resume requires fresh guards, reapplying the same approved temporary rule, and
secure Windows-local credential enrollment. Do not run an unguarded launcher
or put credentials into chat, command arguments, shared files or logs.

Final Windows c6f verification: **213 PASS, 0 skipped, 0 FAIL**, exit 0,
`python -m pytest -q` in 15.38 seconds with Windows CPython 3.12.10,
external Corretto Java/Javac 11.0.32.1 and the actual installed COMSOL64 client
manifest. Windows safe extraction verified all 123 files and the archive hash.
Production `python -m comsol_mcp.mcp_server` stdio initialize, tools/list and
session_health: **PASS**, 58 tools, READY, no active jobs, Worker NOT_STARTED
and no engine connection. Evidence: `windows_c6f_tests.json` and
`windows_c6f_mcp.json` under `evidence/windows_handoff/20260919/`.

| Current-source gate | Mac arm64 / COMSOL 6.4.0.293 | Windows x64 / COMSOL 6.4.0.293 |
|---|---|---|
| Software/native Java protocol | PASS: 212 tests; 1 Windows-only skip | PASS: 213 tests; no skips |
| Production MCP stdio | Historical evidence retained; new engine not connected | PASS: initialization/discovery/health only |
| Current-source engine solve/save/fresh Worker reopen | BLOCKED: preserve existing unsaved model/session | BLOCKED: secure local credential enrollment pending |
| Runtime localhost allow / remote deny | NOT_RUN for a new isolated Server | NOT_RUN; temporary configuration restored |
| Platform support | UNVERIFIED_CURRENT_SNAPSHOT; scoped historical passes preserved | UNVERIFIED |

**本轮部分完成/受阻，阶段未通过。** All currently independent software work is
complete. Remaining work requires a safe current-source Mac engine session and
Windows secure local credential enrollment, followed by access/authentication,
license and real MCP/Worker/Server solve-save-reopen validation. The repeatable
entry is `tools/portable_engine_regression.py` with `prepare-fixture`,
`production-chain` and `reopen`, using host-native dependencies, local artifacts,
confirmed Server identity and separate new private Worker homes. Preserve the
Mac unsaved models; never bypass their existing Worker lock. Desktop binding
remains BLOCKED; the historical native callback FAIL is retained. Other
versions/architectures remain UNVERIFIED. W08–W12 entry gates are not met and
no later work package is started. This round ends without commit or main push.

### Goal blocked audit — 2026-09-19

Three consecutive goal turns retained the same external engine-start blocker.
The last two turns made no further implementation progress; fresh read-only
checks confirmed no Windows COMSOL process/listener and the restored original
configuration/ACL. Final runtime/test hashes remain unchanged. All independent
software work is complete; secure Windows-local first enrollment and a safe Mac
engine session remain required. Goal is BLOCKED, not complete; phase acceptance
remains NOT_PASSED. Evidence: `evidence/windows_handoff/20260919/blocked_audit_03.json`.

### User-ready enrollment window — 2026-09-19

User confirmed Windows is ready for local authentication. Fresh guards found
no COMSOL process/dedicated listener and verified original config, backup and
ACL. Reapplied the previously approved exact localhost rule; patched hash/XML
and unchanged installation ACL passed. A previously nonexistent private
enrollment directory was created with current-user/SYSTEM/Administrators-only
permissions; no existing directory ACL was changed. Earlier restoration remains
in `server_gate_restored_before_enrollment.json`. Current temporary rule is ACTIVE
while the user performs secure local enrollment. No Server was started by this
action and engine acceptance remains NOT_RUN.

### Real Windows authentication checkpoint — 2026-09-19

User started the dedicated Server locally. Process birth identity, port 22036,
private prefs and patched installation hash were verified. Native Java clients
authenticated, read an empty model list and disconnected on both loopback and
the host LAN address. Authentication is PASS; localhost-only access gate FAIL.
Mac remote HTTP/API also reached the application/authentication layer, not a
proven address rejection. No models were created and no solve was run. Initial
diagnostic timeouts were probe JVM non-daemon-thread exit delays, corrected by
explicit exit after disconnect; retain the earlier timeout/stack evidence.
A narrowly scoped temporary program+TCP22036 firewall rule and guarded rollback
are prepared, but require separate authorization under handoff section 5.
Existing firewall rules remain unchanged. See `access_gate_authenticated.json`.

### Approved firewall access gate — 2026-09-19

User separately approved creation, validation and post-test removal of the
COMSOL-program/TCP22036 non-loopback inbound block. Exact rule readback passed;
all firewall profiles were already enabled and no existing rule was changed.
Windows native loopback auth/tags/disconnect PASS (zero models); Mac LAN TCP
connection now times out. Access gate PASS for this test configuration; earlier
Valve-only FAIL remains. Rule is ACTIVE until owned Server shutdown and guarded
rollback. Real bounded fixture/production/fresh-Worker tests are now authorized
to execute; no engine acceptance is inferred from this gate.

### Windows native fixture PASS; production lifecycle FAIL — 2026-09-19

The fixed native Java fixture solved, saved a nonempty MPH and exported a PNG;
hashes matched. First production-chain was rejected because test model/output
paths were mistakenly under reserved `.phase1-private`. This is expected path
protection, not permission to relax the guard. A corrected private artifact
root under project `evidence/` now contains a separately prepared fixture.
The first production attempt also exposed an actual Windows lifecycle defect:
the transport SDK Job Object killed the auto-spawned control/Worker at stdio
teardown. Installed SDK source and win32job availability confirm kill-on-close
with no breakaway allowance. This violates transport/engine independence; it
must be fixed on Mac and genuinely retested, not hidden by disconnecting before
transport close. See `windows_c6f_live_failures.json`. Production solve/save/reopen
has not passed. Server remains running under the approved temporary firewall.

The corrected c6f fixture quantitative evidence is now recorded in
`windows_c6f_native_fixture.json`: max absolute error `1.192379528447418e-13`
against `1e-8`, native recipe exit 0, PNG 800x600, and model/image/log SHA256.
The acceptance ledger now separates this scoped license/fixture PASS from the
production-chain FAIL. The upcoming lifecycle repair is not covered by c6f
software or engine results; a fresh source snapshot and regression are required.

### Windows c7/c8 regression checkpoint — 2026-09-19

Mac lifecycle software tests passed 222 with one Windows-only skip. Windows
c7 found a test fixture lacking process birth metadata (222 PASS, one FAIL).
The Mac-only test correction preserves the identity guard and adds missing-birth
refusal coverage. c8 Windows suite then passed all 223 tests, and the native
fixture solved/saved/exported again (max error 1.1923795284474181e-13).
Production prestart still failed before any model operation: Windows venv Popen
represents the redirector, while the daemon publishes its real interpreter PID.
A separate no-COMSOL native probe confirmed the direct parent/child relationship.
Dual launcher/daemon identity verification is being implemented and requires a
new snapshot and real retest. See windows_c7_tests_failure.json,
windows_c8_tests.json, windows_c8_native_fixture.json,
windows_c8_production_failure.json and windows_python_redirector_diagnostic.json.

The first c7 test wrapper also omitted cwd and collected an unrelated test
from the SSH default directory; import failed on app.run and the run is invalid.
Subsequent runners specify both snapshot cwd and tests. Raw failure evidence is
retained privately. Additional reading of that external file was denied by
automatic approval review; no bypass was attempted and import-side effects are
not fully assessed. COMSOL Server and temporary protection remain active.

Root reran the frozen c8 extraction with the previously verified Mac project
venv: 222 PASS, one Windows-only skip, exit 0 (6.26s). See
`evidence/mac_same_snapshot/20260919/c8_verified_full_suite.json`. This supersedes
the invalid temporary-environment attempt, which remains separately recorded;
it does not supersede the real Windows production prestart failure.

### c9 dual-process identity repair and resumed validation — 2026-09-19

Driver repair now validates both Windows venv launcher and real daemon PID,
creation time, direct parent relationship, executable and private-home command.
Cleanup rechecks both identities and disconnected/idle state, then verifies
both processes exited. New Mac full suite: 230 PASS, one Windows-only skip.
Snapshot c9 contains 123 verified files; see source_snapshot_c9.json and
redirector_repair_full_suite.json. Windows c9 validation remains pending.
Automatic review initially rejected snapshot export; the user subsequently
approved the exact c9 source/script payload and pinned test destination.
Fresh read-only checks confirmed the same dedicated Server and active temporary
protection, with no Python/Java test processes remaining. No commit or push.

c9 delivery subsequently PASS: all 123 Windows files and archive/manifest hashes
matched. Its Windows suite returned 230 PASS, one FAIL in the legacy cleanup
unit fixture, which implicitly selected the host platform without the newly
required base-interpreter identity. The explicit Windows redirector tests passed.
The fixture is being separated into explicit platform cases; production guards
are unchanged. Fixture/production/reopen were not started for c9. Evidence:
`windows_c9_tests_failure.json`; no engine PASS is inferred.

### c10 software checkpoint — 2026-09-19

Only the cleanup test fixtures changed from c9; production driver is unchanged.
POSIX and Windows cases are now explicit, including missing-base and missing-
birth rejection. Mac full suite returned 232 PASS and one Windows-only skip.
c10 has 123 locally verified files. Automatic review rejected its transfer
because the preceding approval was interpreted as c9-specific; a concrete c10
and optional current-round followup-snapshot approval request is pending.
Windows c10 tests and engine chain remain NOT_RUN. The dedicated Server and
approved temporary protection have not yet been closed/restored. Stage is
NOT_PASSED, no commit/push and no W08-W12 expansion.

Pending-approval audit 02: c10 transfer approval remains unanswered; no transfer
or Windows c10 test was retried. Evidence JSON parsing and diff whitespace checks
passed. One account-specific executable path was redacted from public evidence;
its exact command and original record remain privately hashed. The dedicated
test window has not yet been closed/restored. Goal and phase remain incomplete.

The user subsequently approved the pending c10 transfer on 2026-09-19. Resume
the exact frozen c10 archive and matching test scripts using the pinned Windows
host and new non-overwriting destinations. This approval clears the export
blocker; it does not constitute a Windows test PASS. Preserve the protected
test window for regression, then verify model ownership and restore the
approved temporary configuration. The frozen c10 manifest remains unchanged;
this progress entry is a later audit update.

### c10 native regression — 2026-09-19

Windows verified all 123 c10 files and matching archive/manifest hashes. Its
software suite passed 233 tests with no skips or failures; Mac passed 232 with
one Windows-only skip. The native fixture and production MCP chain passed
(15 production assertions), including solve, metric, parameter/variable
readback, ephemeral tree preservation, save, and control/Worker survival after
stdio teardown. Authenticated Worker disconnect and owned control cleanup
passed; this alone is not proof of Java Worker process exit.

Fresh-Worker reopen remains FAIL: the same saved MPH hash loaded successfully
and parameters read back, but the metric failed and subsequent variable
evaluation reported REVISION_CONFLICT. Failure evidence is retained in
windows_c10_reopen_failure.json; no complete save/reopen acceptance is claimed.
The full W04 selection and failure-path matrix is also not established by the
bounded production probe. Diagnosis and additional applicable tests continue;
temporary configuration remains active pending safe final restoration.

Explicitly approved diagnostic readback identified the first reopen error:
"Cannot aggregate without an explicit model dimension; configure the workflow
or model geometry." The production branch configures the fixed 2-D fixture,
but the fresh-home reopen branch omitted that configuration. Its failure
advanced revision 0 to 1 and left reconciliation required, explaining the
subsequent variable conflict. Repair the test driver with explicit fixed-recipe
configuration and rerun a new snapshot; do not weaken product dimension guards
or relabel c10 as PASS. Exact diagnostic SHA and approved sanitized fields are
recorded in windows_c10_reopen_failure.json.

### c11 reopen-driver repair checkpoint — 2026-09-19

Production and fresh-home reopen now share explicit fixed-2D recipe workflow
configuration. Reopen rejects configuration failure before server_connect;
behavior tests exercise both ordering and failure, without claiming engine
acceptance. Targeted tests passed 25; Mac full suite passed 235 with one
Windows-only skip. This run used a uv test environment; exact command/raw hashes
are retained privately and dependency versions require separate recording.
The c11 archive contains 123 independently verified files (373617 bytes), SHA
c35ef26a70d1ba6ff92e291bcd9a7ea21d4036fabe1c789bd24c5d7e59371afc.
Windows c11 regression is pending; c10 remains the last executed native result.

c11 test environment was verified as uv Python 3.13.14, mcp 1.30.0 and pytest
9.1.1; raw stdout/stderr hashes match the saved records. This is software-only
evidence, not a real Mac engine retest. After automatic review rejected c11
export, the user explicitly approved c11, the prepared W04 supplemental test
script and necessary subsequent repair snapshots within this round. Preserve
the rejection history and continue new-directory/pinned-host delivery; do not
infer delivery or runtime PASS from authorization alone.

c11 Windows delivery subsequently verified all 123 files and matching hashes.
Windows software regression passed 236 with no skips. The fixed fixture passed;
production passed all 15 assertions and fresh-Worker reopen passed all 11,
including explicit workflow configuration, metric/parameter/variable readback,
stdio teardown survival and authenticated release/control cleanup. Root verified
the production and reopen saved SHA are equal:
452ceaea86a3e84fe0167b9b73169bd7dc370fe2d200a1ff419061a82025300d.
See windows_c11_tests.json, windows_c11_production.json and
windows_c11_reopen.json. This is bounded Windows chain acceptance; supplemental
W04 checks and final configuration restoration remain pending. No full-platform
VERIFIED claim, commit, push or later work-package entry is made.

### W04 supplemental Windows attempt 1 — 2026-09-19

The separately approved test script (SHA b2905c783d87d22278d158dbc6e07cecab61cb7751160dfa9b466b039ccd1faa)
ran against c11 in a new isolated directory. Parent explicit/named/all and
editable-child explicit selection assertions passed. The default cfeq1
feature readback reported selection_editable=true; its selection operation
failed with a string error that did not match the harness's inherited/noneditable
classification. Exact cause is unresolved, not evidence to weaken production
guards. The attempt remains FAIL and later W04 cases did not execute.

The original Worker/control release assertions failed on the exception path;
later read-only process checks found both absent with no listeners. This is
not model cleanup or Server restoration. Automatic review rejected diagnostic
text disclosure; narrower boolean classification was executed but did not
identify the cause. User authorization for scoped W04 diagnostic readback has
been requested. See windows_w04_c11_attempt1.json. Temporary Server protection
remains active; no user model or Server was stopped.

Pending W04 diagnostic audit: latest c11/W04/Mac evidence consistency checked
without rerunning tests. Eleven key JSON records parse; snapshot hashes and
123-file manifest agree. Public evidence scan found no account absolute paths
or credential-value patterns. Historical c6f scope notes now identify c11 as
the current snapshot, and the acceptance ledger explicitly links both c11
production/reopen records, W04 attempt1 FAIL and restoration NOT_RUN. See
c11_consistency_audit.json. Diagnostic authorization is still pending; no
failed W04 condition was relabeled PASS and no test was restarted.

### Blocked checkpoint — W04 diagnostic authorization, audit 03

The same diagnostic-disclosure blocker persisted for three consecutive Goal
turns. All completed c11 software/production/reopen results remain scoped PASS;
W04 attempt1 remains FAIL and later cases remain NOT_RUN. Prepared W04 v2 is
not an accepted fix until the actual first error is identified and retested.

Root reviewed the prepared local-only inventory helper without executing it.
It covers c11 only, so it cannot prove ownership of all earlier fixture/W04
models on the Server. No complete authenticated inventory or safe-shutdown
proof exists. Last preflight matched the dedicated Server identity, firewall
and patched XML hash, with no established clients or Python/Java processes;
this does not authorize closing unknown models. Server and temporary protection
are retained, with restoration NOT_RUN. The helper's exception cleanup also
needs an explicit idle check before use. No model, Server or configuration was
changed in this audit.

Goal is blocked, not complete; stage is NOT_PASSED. Minimal next user action is
to answer the pending scoped W04/current-round sanitized-diagnostic request.
Preserve private raw logs, snapshots and runners for resumption. After diagnosis
and applicable regression, finish model ownership verification and authorized
XML/ACL/firewall restoration. No commit, push or W08-W12 entry occurred.
See w04_blocked_audit_03.json for the evidence and remaining gates.

The user subsequently approved the pending scoped W04/current-round sanitized
diagnostic disclosure. Resume diagnosis from the retained attempt1 transcript;
do not rerun or loosen the failed condition before identifying the actual
error. The blocked checkpoint remains historical evidence. Acceptance and
configuration restoration remain incomplete until their real checks pass.

Approved W04 diagnostics identify `FlException: Selection_is_not_editable`.
The harness missed underscore-separated wording and incorrectly equated
non-editability with inheritance. Actual revision remained 4 and the control
envelope was clean, but the nested Worker failure explicitly carried
execution_state_unknown=true. That signal was lost through wrapped legacy
exceptions, a separate product safety defect requiring structured propagation;
it must not be dismissed merely to make the test pass. Feature discovery's
not-isInheriting editability inference also needs an evidence-limited result.
Mac repairs and behavior regressions are in progress. c11's bounded chain PASS
and W04 attempt1 FAIL remain unchanged; a new snapshot must verify the repair.

### c12 structured failure propagation checkpoint — 2026-09-19

c12 preserves the structured Worker failure through `JavaWorkerError`, the
legacy exception cause chain, and the execution service. Explicit unknown state
cannot be cleared by a false envelope field; ordinary local input failures
remain clean and retryable. Physics discovery now reports inherited state as an
observation and leaves editability tri-state because COMSOL exposes no
authoritative `isEditable` predicate. The separate W04 v3 handoff normalizes
Java underscore diagnostics, requires the expected unknown/dirty revision
advance, reconciles only the same owned model identity, and keeps native
inherited-feature coverage `UNVERIFIED`. v1 and v2 handoff hashes are
unchanged.

Using the dedicated Mac environment (Python 3.12.13, pytest 9.1.1, mcp 1.29.1),
the full software suite passed 243 tests with one Windows-only skip; focused
c12/v3 tests passed 21. Exact raw output, stderr, exit code and version records
are retained in the private handoff directory. Windows execution, live COMSOL
acceptance and configuration restoration remain NOT_RUN in this checkpoint.
No commit or push was made.

### c12 Windows regression result — 2026-09-19

The frozen c12 snapshot was delivered and extracted on the pinned Windows x64
node with 124 manifest entries verified. The archive SHA is
`f4c34b77a1c6eee03d29062c8a10461cbebf615b527fdcd0941863ce84543340` and the
manifest SHA is
`8b1ac4a9477c8958e580d2e9e975314b25246f4cc253f5ea3e4cb2ea6f0bdd86`.
The recorded environment is Windows x64, CPython 3.12.10, COMSOL 6.4.0.293
build 293 and external JDK 11.0.32_10. Delivery and extraction did not alter
the Server or configuration. See `windows_c12_delivery.json`.

The explicit-source-cwd Windows software suite passed **244 tests**, with zero
failures or skips and exit code 0. Its first wrapper attempt is retained as a
separate `FAIL_WRAPPER_EVIDENCE_WRITE`: the runner did not create its evidence
directory, so that attempt is not counted as a pytest result. The corrected
retry used a new private home and is the accepted software result;
`engine_connection=NOT_RUN` for this suite. See `windows_c12_tests.json`.

The real fixed-recipe chain then passed: production completed 15/15
assertions, and a fresh private Worker reopened the saved artifact with 11/11
assertions. The production and reopen saved SHA match at
`be2653f3764b41e500255fe6398ba31993ff3ae597a4add3e3424202481c55c4`.
Authenticated Worker release, control queue idle checks and owned control
cleanup passed in both phases. This is bounded Windows engine acceptance for
the fixed recipe, not full platform or W01-W07 certification. See
`windows_c12_production.json` and `windows_c12_reopen.json`.

The c12 W04 v3 supplement remains **FAIL**. The first unresolved case is the
unsupported-inheritance rejection: the engine returned
`ENGINE_CALL_FAILED` / `FlException: Inheriting_not_allowed` while the harness
expected a clean non-mutating failure; the observed state was
`execution_state_unknown=true`, `dirty=true`, revision 6 and
`safe_to_continue=false`. The earlier default noneditable rejection returned
`FlException: Selection_is_not_editable` with revision 4 to 5 and the expected
unknown/dirty state at failure. That owned-model failure was reconciled before
continuing, so its post-reconciliation continuation flag is separate from the
observed dirty/unknown values. The control health check was authenticated and
idle, and owned Worker/control release passed; no later W04 cases are counted
as PASS.
The diagnostic readback is now recorded in `windows_c12_w04.json`; no
production guard was weakened. Temporary configuration restoration remains
**NOT_RUN**, and the historical W04 failures remain preserved.

This checkpoint records c12 as software PASS plus bounded Windows
production/reopen PASS, with W04 FAIL and restoration NOT_RUN. No commit, push,
Server stop, configuration rollback or W08-W12 work was performed.

### c12 W04 v4 r2 isolated rerun — 2026-09-19

The root-reviewed v4 harness and corrected r2 foreground runner were verified on
the c12 extraction. The harness SHA is
`92864ce18661298a2fbad85c52acd5b7a3b96e2ac2f153ad8d3af900c2fcb175`; the r2
runner SHA is
`f9f5d2e4acfcc5cab9ca735cf367cbcad9c155c971168df673b426fc6988dd50`. A fresh
read-only gate matched the dedicated Server identity and patched XML/firewall
protection, with zero established port connections, other COMSOL processes, or
Python/Java processes. The Server lifecycle was not touched.

The single v4 r2 run is **FAIL**. Fifteen assertions were executed and each
returned true, but this does not establish complete W04 acceptance because the
first primary failure was a revision conflict: the global variables list
advanced the owned revision from 12 to 13, while the next component operation
used revision 12. The runner exit was 1; private stdout/stderr and the bounded
failure summary are retained. No raw diagnostic text or model metadata is
public.

A second start was rejected by the output-exists guard, so no duplicate engine
run occurred. The first diagnostic extraction had a PowerShell default-encoding
failure; its raw stderr remains private. The corrected readback returned only
scoped booleans and revision values. c12 production/reopen evidence is
unchanged, the c12 product source was not modified, v5 harness repair is
prepared but **NOT_RUN**, and XML/ACL/firewall restoration remains **NOT_RUN**.
See `evidence/windows_handoff/20260919/windows_c12_w04_v4.json`.

### c12 W04 v5 bounded result — 2026-09-19

The corrected v5 W04 harness and runner used the frozen c12 source. The harness
SHA is `caf34aae6326ba25aaa3b9ac042f411a22c6700d0f983837f42716b8e463d43f`;
the runner SHA is
`dd127f66b90133bc97475af9ea05b2742c1c897bf0d802c2bdd53d62c0e2f1a2`. The
runner wrapper passed with exit 0 and the bounded result contained **34/34**
true assertions. Worker release and control cleanup both passed; the result
artifact SHA is
`841D90857EA3CF0D41805EAF3CCD3DE2D1F64124D5ED54700F9EB9A93E220090`.

This is a bounded W04 PASS for the corrected harness. The native inherited-feature
guard remains **UNVERIFIED/NOT_RUN**, so no full selection matrix or full-platform
acceptance claim is made. The earlier v4 revision-conflict failure remains
preserved as historical evidence. An auxiliary reader used the wrong v5 summary
filename; the correct runner summary name is
`windows-c12-w04-v5-runner-r2.json`, and the wrapper stdout is the authoritative
PASS record. No raw stderr or model metadata is public.

The c12 production source and prior production/reopen evidence are unchanged.
Server lifecycle and configuration restoration remain untouched; XML/ACL/firewall
restore is **NOT_RUN** pending ownership/inventory gates. See
`evidence/windows_handoff/20260919/windows_c12_w04_v5.json`.

### c12 protected-window closeout — 2026-09-19

The protected Windows window was closed under the owned Server identity. The
stop record passed with the expected PID/birth identity, worker closed, 43
evidence items preserved, and the post-stop TCP listener and established counts
both at zero. The local-only ownership/artifact inventory passed with 13/13
observed entries matched, unknown count zero, and `otherclients_empty=true`.
Its scope remains an ownership gate: active-client model state and unsaved
scientific evidence were not inferred from the inventory.

The approved original configuration was restored with exit 0. The original
server XML hash `8bb393d2d803d1b7f6b124498742fa23b6310db9a28a6334f7462ec12d674bbe`
matched; XML, ACL, exact temporary firewall removal, and listener absence all
passed. An independent readback then confirmed the same XML/ACL/firewall
state, no dedicated PID, and zero COMSOL processes. See
`evidence/windows_handoff/20260919/windows_c12_closeout.json`; raw stdout and
stderr remain in the private handoff directory.

This is a scoped closeout PASS, not a whole-goal PASS. The c12 v5 W04 result
remains bounded 34/34 PASS, while native inherited-feature coverage is still
**UNVERIFIED/NOT_RUN**. The existing Mac Server session is blocked by an
unsaved model; a new isolated Server has not yet been tested and no
license/concurrency blocker is established. The interactive Desktop binding
and alternate COMSOL 6.3/Intel matrix remain unverified, the historical native
model-change callback FAIL remains retained, and W08-W12 remain outside this
authorized round. The next bounded audit is the Mac isolated-Server
prerequisite validation plus a Windows-equivalent runner for T027/T028
lifecycle; Windows T030 disk-full is **NOT_RUN** by `USER_REQUESTED_SKIP` and
is not a current-round prerequisite. This does not call for rerunning every
test. The c12 v3 and v4 W04 failures remain historical evidence. Independent
restoration readback is PASS; no further Server or engine run was started in
this closeout. The **NOT_RUN** restoration wording in the preceding v5
checkpoint is historical as-of that test phase and is superseded by this
closeout record.

### Scope revision — Windows T030 disk-full — 2026-09-19

Per the user's current scope instruction, the Windows disk-full monitoring/test
is skipped and remains **NOT_RUN** with reason `USER_REQUESTED_SKIP`. It is not
counted as a Windows PASS or a current-round prerequisite. Existing normal-save
evidence and historical T030 records are retained unchanged; no disk-full test
or disposable volume was started in this scope.

### Read-only Mac PF and offline recovery-runner evidence — 2026-09-19

The private PF capture `pf-readonly-20260919T125234Z-4937` was verified from
its recorded exit files and stdout/stderr hashes. PF reported **Disabled**;
the read-only root rules showed only the Apple anchor declarations, and the
anchor listing showed `com.apple`. The recursive query returned exit code 0
but stderr contained `DIOCGETRULES: Invalid argument`, so it is retained as a
limited observation and is not treated as complete recursive PF proof. No PF
rule, anchor, global firewall state, new Mac Server, existing Server, model,
or credential was changed or read by this capture. The user-requested CLI
scope also performed no GUI model write. See
`evidence/mac_same_snapshot/20260919/mac_isolated_pf_readonly.json`; this does
not establish PF protection, isolated Server acceptance, license acceptance,
GUI acceptance, or platform verification.

The separately recorded recovery-runner software check was independently
matched to its private stdout/stderr bytes and hashes: exit 0, **29 passed in
0.38s**, with no stderr. The source hashes in
`evidence/windows_control_lifecycle/20260919/recovery_runner_software.json`
also match the current files. This remains an offline software PASS only;
production COMSOL connection, Windows native execution, and T030 remain
**NOT_RUN** (T030 `USER_REQUESTED_SKIP`).

The root-collected Windows PowerShell `Parser::ParseInput` receipt reported
five reviewed T027/T028 scripts with `errors=[]` for each. Its matching
network manifest records exit 0 and its matching stderr is empty. This is a
syntax parse record only: it does not establish that any script ran or that
COMSOL or the firewall was changed. An earlier local transport attempt with
exit 255 and `Operation not permitted` remains separately labeled historical
and is not mixed into this result. See
`evidence/windows_control_lifecycle/20260919/recovery_parser_syntax_receipt.json`.

The Windows read-only readiness receipt also found zero COMSOL processes, zero
connections on the fixed test port, zero temporary rules, three enabled
firewall profiles, the original server XML, the enrolled login file present,
and no recovery-window directory. No Server or firewall mutation was
performed; this is a prerequisite snapshot only. See
`evidence/windows_control_lifecycle/20260919/recovery_readiness_root.json`.

### G2 Mac W08–W12 start and baseline audit — 2026-09-19

The new goal `NEXT_GOAL_MAC_G2.md` authorizes W08–W12 only. Earlier phase
statements excluding W08 are historical boundaries; no W13 or Windows operation
is authorized by this goal. Existing uncommitted Mac/Windows work is retained
on `codex/mac-g2-w08-w12`; the starting HEAD/tree matches the reference
`28605a6896668a64775ac6c1192ed50fef94b47f` /
`0172bc996879ff92aaeea24b7b666e5b2d7c9be0`. Fetched `origin/main` still matches
that commit. This is a delivery preflight, not a new phase publication.

`evidence/phase3/baseline/` records the initial dirty-file hashes, acceptance
plan, and Phase 2 audit. All Phase 2 ledger paths exist; 17 selected original
success/callback-failure artifacts match their recorded hashes. Historical
W05–W07 scoped PASS and T011 callback FAIL remain unchanged. The current
software baseline is **259 passed, 1 skipped in 5.75s** on the host. The prior
sandbox attempt is retained separately: **250 passed, 1 skipped, 2 failed,
7 errors**, with Java loopback bind denied by the sandbox. Neither run is a
COMSOL engine acceptance claim.

The user explicitly confirmed no Server model required saving and authorized
closure of the old Server. After PID/birth/port verification, SIGTERM was sent
only to PID 84749; port 56388 and its connections were absent on readback.
`authorized_server_stop.json` records that scope. The user subsequently approved
one temporary installation configuration change for G2: add
`address="127.0.0.1"` to the HTTP Connector in the installed `server.xml`, run
the task-owned Server, then stop it and restore the original file/hash. The
reviewed proposal and original/proposed hashes are recorded; no firewall or
Windows changes are included. Live G2 acceptance remains pending.

### G2 owned-runtime isolation attempts — 2026-09-19

The approved temporary Connector address edit was tested on a fresh task-owned
COMSOL Server. The first attempt stopped before Server launch because replacing
`server.xml` introduced a macOS `com.apple.provenance` attribute. Original XML
bytes/hash, mode, ownership and ACL were restored; the added provenance attribute
remains explicitly recorded. Host `xattr -d` returned zero but readback retained
it; noninteractive administrator access was unavailable. No OS security setting
was changed, and full original metadata restoration is **not** claimed.

A subsequent guarded in-place attempt preserved that known metadata but exposed
`*:56389` (PID 26811), despite the Connector address attribute. The parent verified
the exact task directory in the process identity, stopped only that Server with
SIGTERM, confirmed the port absent, and restored the original XML SHA
`8bb393d2d803d1b7f6b124498742fa23b6310db9a28a6334f7462ec12d674bbe`.
No model operation or G2 engine acceptance ran on it. The launcher now fails
immediately on a non-loopback listener and has 11 passing software regression
checks; those checks do not establish live isolation.

`evidence/phase3/runtime_blockers.json` records the live-engine blocker and the
per-attempt records remain under `evidence/phase3/mac_owned_runtime/`. Independent
G2 software, stdio, documentation, and compilation work continues. The installed
manual's alternative RemoteAddrValve configuration is being assessed as a
proposal only; it has not been authorized or applied by the Connector-only
permission. No firewall, Windows integration, or W13 work was performed.

### G2 Mac W08–W12 independent delivery; live acceptance BLOCKED — 2026-09-19

The implementation, generated wire reference and all currently independent
acceptance work are delivered on `codex/mac-g2-w08-w12`. The authoritative ledger
is `evidence/phase3_acceptance.json`; this is the goal file's **blocked closure**,
not G2 Mac engine acceptance. No W13 or Windows integration was performed.

Final software regression: **331 passed, 1 skipped in 6.36s**, recorded in
`evidence/phase3/review/final_pytest_after_preview.txt`. This includes affected
Phase 2 software tests and new permission/revision-before-copy, checkpoint
preservation, scoped trial cleanup and durable UNKNOWN propagation regressions.
It does not replace historical Phase 2 live-engine evidence. Packaged catalog
resources were built into an offline wheel and imported outside the repository;
package/source drift is checked. Original Phase 1/2 evidence is retained; all
100 pre-existing dirty files remain, 95 byte-identical and 5 evolved in G2.

`evidence/phase3/acceptance-final-03/` records actual production stdio calls:

- **W09 scoped PASS:** initialize/list/call, registry pagination, strict fallback,
  structured business errors, and real full/domain/expert publications of
  **65/48/17 tools**. Hidden legacy calls use the managed backend. A simulated
  host without dynamic tools/images/Tasks exercised text/structured output and
  job polling; this does not certify every actual third-party host.
- **W11 scoped PASS:** accessible real COMSOL 6.4 help indexing/search/get with
  source hashes, version separation, no-match NOT_FOUND, unavailable 6.3,
  malicious text as data and outside-root rejection. Real 6.3 material remains
  unavailable; no proprietary corpus or SQLite index is committed.
- **W10 compilation PASS only:** three reviewed Java sources compiled through
  the persistent Worker; the intentional syntax error returned its actual
  line/column diagnostic. Bound-model execution and partial execution remain
  **BLOCKED**, not inferred from compilation.
- **W12 static preview PASS only:** a valid plan runs without a bound model and
  reports `static_only=true`, `engine_called=false`. Live trial, partial apply,
  restore and small-model checkpoint cost remain **BLOCKED**. Large-model cost
  remains **NOT_RUN**.
- **W08 live typed/wp3/idempotency checks remain BLOCKED** by the runtime
  isolation prerequisite. Software/API-signature checks alone are not engine
  round-trip or advanced-geometry acceptance.

The approved Connector edit did not isolate the API listener; task-owned Server
PID 26811 was stopped before any model operation. Original server.xml content
hash, mode and ownership were restored; the provenance xattr residual remains
recorded. Ports 56388/56389 are absent on readback. The RemoteAddrValve proposal
is pending separate authorization and actual API access-proof validation; it is
not implemented as an isolation bypass. Full original metadata restoration and
full G2 acceptance are both explicitly **not claimed**.

The first protocol/compile/docs failures and subsequent reviewer findings are
retained with their scope corrections. `G2_CAPABILITIES.md` documents current
limits; `G2_API.md` is generated from effective registry contracts. Native
callback FAIL, shallow fingerprint scope, Worker epoch identity, GUI/Intel
Mac/Windows/6.3 limitations and timeout-not-cancellation semantics remain intact.
Ordinary non-force fork synchronization is authorized at this boundary; remote
HEAD verification is required before reporting synchronization complete.

Publication review retained raw help-bearing transcripts/manifests privately and
published source/fragment hashes and lengths instead of licensed help text. The
transformation receipt preserves original/public hashes without changing case
statuses. After that serializer-only change, the final full suite is
**332 passed, 1 skipped in 6.36s** (`final_publication_pytest.txt`). Production
modules remain identical to the final stdio run; the two-file serializer/test
delta is recorded in `publication_source_snapshot.json`. All 14 control/compile
Worker processes belonging to the final offline runs were identity-checked and
closed; old Phase 2 processes were not targeted.
