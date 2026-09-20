# G2 Mac capability boundaries

This note describes the W08–W12 implementation and its limits. The authoritative
acceptance result is `evidence/phase3_acceptance.json`; generated wire schemas
are in `G2_API.md`. A route existing in the registry is not engine acceptance.

| Work package | Implemented route | Limits that remain explicit |
|---|---|---|
| W08 | Structured nested node paths; metadata-based typed getters/setters and indexed/entry writes | Unknown metadata and unsupported Java signatures fail closed. Real scalar/empty/singleton/matrix round trips, primitive kinds, same-tag type rejection and wp3 sibling preservation passed on the isolated Mac 6.4 test model (`live-g2-rerun8-20260920T024658Z`). T009 does not cover W14 advanced geometry. |
| W09 | Registry describe/list/search/call; full/domain/expert static publication; explicit legacy fallback; structured results and business errors | Profiles change publication, not backend authorization. Dynamic tools, image rendering and MCP Tasks are not claimed; text/structured output and durable job polling are the fallback. |
| W10 | Project-local source description, source hashes, actual Worker compilation and diagnostics; bound-model trusted Java execution/readback | Compilation is separate from execution. trusted_code is an independent deployment permission, not an OS sandbox. No full-model conflict detector or cancellation guarantee is claimed. |
| W11 | Local help indexing, exact version filters, source/content hashes, search/get and explicit missing-result states | Only accessible local 6.4 material is eligible for real-source acceptance here. Synthetic version tests do not certify real 6.3 documentation. Corpus contents and index databases stay private. |
| W12 | Static preview, scoped trial copy, sequential transaction outcome, checkpoint creation/restore and durable records | Trial compares only named typed properties plus the existing shallow fingerprint. Full-model untouched status remains unknown. Arbitrary Java trial, failed cleanup or unobservable scope is conservative UNKNOWN. MPH restoration does not restore external files or GUI state. Solution restoration needs separate verification. |

## External changes and isolation

Phase 2's parameter-fingerprint PASS and native ModelChangedHandler callback FAIL
are retained. The fingerprint is not full-model conflict detection or CAS.
`server_instance_id` is a Worker connection epoch, not an independently observed
COMSOL process UUID. Broad G2 mutation therefore requires control-side validation
of an owned runtime receipt, live process birth/command and the exact IPv4
loopback COMSOL/Worker socket pair. The original proof requires an exact
loopback listener. The reviewed RemoteAddrValve branch additionally checks the
installed configuration inode/hash and a same-process COMSOL Java API probe:
authenticated localhost access and explicit non-loopback API HTTP 403 denial.
That branch accurately labels its listener as wildcard; request filtering does
not change socket binding. A caller-supplied exclusive boolean
is insufficient. The receipt adapter is currently macOS-only; unsupported
platforms fail explicitly instead of claiming support.

The approved Connector `address="127.0.0.1"` experiment still produced a wildcard
listener on the accessible COMSOL 6.4.0.293 runtime. It was stopped before any
model acceptance. Original server.xml bytes/hash, mode and ownership were
restored; a provenance xattr introduced by the first replacement attempt remains
recorded. The user approved temporary RemoteAddrValve validation on 2026-09-20.
The first Valve attempt on the previously proposed `bin/tomcat/conf/server.xml`
allowed the non-loopback Java API connection and was stopped/restored without
model operations. Read-only inspection identified the actual configuration base
as `bin/servers/webbridge`; see `valve_resume/config_target_diagnosis.json`.
The user subsequently authorized that corrected installed-file change. The
active-target run `g2-valve-runtime-20260920T002102445575Z` observed authenticated
localhost Java API access, rejection of the fresh non-loopback Java connection,
and HTTP 403 on the same API route in both the Server access log and an explicit
non-loopback-source transport probe. The Java exception did not expose its HTTP
status, so the original strict receipt retained FAIL. That Server was stopped
and original XML content/inode/mode/ownership restored; the recorded provenance
attribute remains. A subsequent run (`g2-valve-runtime-20260920T003546033861Z`)
passed correlated Server-log proof and independent production-adapter readback.
It checks the bounded log interval surrounding only the Java API request, its
inode/hash, exact source/route/status and capture time; the independent raw probe
runs afterward. The Java exception status remains null. Model acceptance is
separate: W08/W10/W12 passed in `live-g2-rerun8-20260920T024658Z`;
affected Phase2 regressions passed and the dedicated runtime was stopped with
original XML content/inode/mode/owner restored. The recorded provenance xattr
remains; no full metadata equality is claimed. Firewall changes,
Windows integration and W13 are outside this goal.

## Recovery and replay

After an uncertain request, inspect the existing durable job; do not submit a
replacement solve. An execution deadline does not prove COMSOL stopped.
Checkpoints preserve the last complete file through the existing atomic-save
mechanism and record save count, time and bytes. Restore validates recorded file
hashes and retires the old model reference. GUI rebind remains unknown.

Trial requires an explicit `checkpoint.create` result bound to the current model
reference, committed revision, fingerprint and external event counter. It verifies
the source artifact hash and copies that file; it does not save the main model
during trial. A stale, unbound or altered checkpoint is rejected before copy/load.
Internal transaction recovery checkpoints remain restorable but are not trial
sources. Source checkpoint save cost and trial copy cost are recorded separately.

`tools/phase3_run_mcp.py` is the production stdio replay entrypoint. Run without
`--live` for protocol, compilation, local help and static-preview checks. A live
replay requires a separately verified owned runtime and the appropriate explicit
trusted-code setting. Do not bypass the receipt gate or replay against a shared
user Server. The runtime blocker receipt records the missing prerequisite.

Windows, Intel Mac, COMSOL 6.3, GUI, unlicensed modules and large-model checkpoint
cost are not certified by this Mac goal. No W13 work is authorized by this note.
