# G2 Mac capability boundaries

This note describes the W08–W12 implementation and its limits. The authoritative
acceptance result is `evidence/phase3_acceptance.json`; generated wire schemas
are in `G2_API.md`. A route existing in the registry is not engine acceptance.

| Work package | Implemented route | Limits that remain explicit |
|---|---|---|
| W08 | Structured nested node paths; metadata-based typed getters/setters and indexed/entry writes | Unknown metadata and unsupported Java signatures fail closed. Real scalar/array/matrix round trips and wp3 sibling preservation require isolated COMSOL acceptance. T009 does not cover W14 advanced geometry. |
| W09 | Registry describe/list/search/call; full/domain/expert static publication; explicit legacy fallback; structured results and business errors | Profiles change publication, not backend authorization. Dynamic tools, image rendering and MCP Tasks are not claimed; text/structured output and durable job polling are the fallback. |
| W10 | Project-local source description, source hashes, actual Worker compilation and diagnostics; bound-model trusted Java execution/readback | Compilation is separate from execution. trusted_code is an independent deployment permission, not an OS sandbox. No full-model conflict detector or cancellation guarantee is claimed. |
| W11 | Local help indexing, exact version filters, source/content hashes, search/get and explicit missing-result states | Only accessible local 6.4 material is eligible for real-source acceptance here. Synthetic version tests do not certify real 6.3 documentation. Corpus contents and index databases stay private. |
| W12 | Static preview, scoped trial copy, sequential transaction outcome, checkpoint creation/restore and durable records | Trial compares only named typed properties plus the existing shallow fingerprint. Full-model untouched status remains unknown. Arbitrary Java trial, failed cleanup or unobservable scope is conservative UNKNOWN. MPH restoration does not restore external files or GUI state. Solution restoration needs separate verification. |

## External changes and isolation

Phase 2's parameter-fingerprint PASS and native ModelChangedHandler callback FAIL
are retained. The fingerprint is not full-model conflict detection or CAS.
`server_instance_id` is a Worker connection epoch, not an independently observed
COMSOL process UUID. Broad G2 mutation therefore requires control-side validation
of an owned runtime receipt, live process birth/command, exact IPv4 loopback
listener and the COMSOL/Worker socket pair. A caller-supplied exclusive boolean
is insufficient. The receipt adapter is currently macOS-only; unsupported
platforms fail explicitly instead of claiming support.

The approved Connector `address="127.0.0.1"` experiment still produced a wildcard
listener on the accessible COMSOL 6.4.0.293 runtime. It was stopped before any
model acceptance. Original server.xml bytes/hash, mode and ownership were
restored; a provenance xattr introduced by the first replacement attempt remains
recorded. The proposed RemoteAddrValve is request filtering, not socket binding;
it has not been established as a replacement isolation proof. Firewall changes,
Windows integration and W13 are outside this goal.

## Recovery and replay

After an uncertain request, inspect the existing durable job; do not submit a
replacement solve. An execution deadline does not prove COMSOL stopped.
Checkpoints preserve the last complete file through the existing atomic-save
mechanism and record save count, time and bytes. Restore validates recorded file
hashes and retires the old model reference. GUI rebind remains unknown.

`tools/phase3_run_mcp.py` is the production stdio replay entrypoint. Run without
`--live` for protocol, compilation, local help and static-preview checks. A live
replay requires a separately verified owned runtime and the appropriate explicit
trusted-code setting. Do not bypass the receipt gate or replay against a shared
user Server. The runtime blocker receipt records the missing prerequisite.

Windows, Intel Mac, COMSOL 6.3, GUI, unlicensed modules and large-model checkpoint
cost are not certified by this Mac goal. No W13 work is authorized by this note.
