# Current handoff — r407 local six-file candidate

U12 current provenance is an accepted SOURCE supplement, not U12 group completion or native/runtime/Goal acceptance. Public delivery is only `tools/generate_current_coverage_provenance.py` (11112B) and `docs/universal_mcp_execution/state/GENERAL_COVERAGE_CURRENT_PROVENANCE.json` (183003B), byte-identical to Main-accepted inputs. It binds product source 958970b6 and the unchanged historical coverage SHA 00af26d7. No private task, receipt, audit, SourcePins or exported tree is published.

The generator reads 58 tracked source files plus GENERAL_COVERAGE. RESTORE_RECEIPT is NOT_IN_PRODUCT_NOT_READ; private acceptance receipts are not rebuild dependencies. It reports 10 fingerprint differences, 455 current definition-location differences and 12 unresolved entries. Current AST definition spans are not replacements for historical internal callsites. Historical overlay bytes are unavailable in the public input set; no exact old-overlay equivalence is claimed. GENERAL_COVERAGE classifications, route statuses and frozen 273/60/427 remain unchanged.

Rebuild from the repository root to an absent output, then compare it byte-for-byte with the sidecar:

```text
python3 -I -B tools/generate_current_coverage_provenance.py . NEW_EXCLUSIVE_OUTPUT.json
```

The r406 diagnostic compact remains byte-identical and r406 is preserved. Session 4584 is SPENT: Main accepted only the DETACHED + binary-LF finite diagnostic, with child0/Tool0, dual EOF, Job0 and aggregate close reports. Source-postimage success gate is not a complete inventory or per-handle close journal. Earlier 0xc0000142 causes remain undetermined; normal Windows installation remains FAIL, prior pip entry/root postimage UNVERIFIED. Original 40869 external terminal remains UNVERIFIED_NO_FURTHER_RECEIVE after its sole exception transcript; no SDK UNKNOWN, parent exit or typed-close inference and no further owner operations. Original 75965 external-terminal gap remains.

Next: Main exact six-file gate, then fresh normal-materialization + normal-consumer private source/data preparation under separate Main conditions; only private Native flags 0x0008040c are planned to change, with no new remote/runtime authorization here. Product .15/95 bytes, accepted e77bd27f publication, scoped Mac .15 actual/SPENT, five fences/frozen readbacks/completed_groups=[]/heldouts NOT_SELECTED/final Reviewer NOT_STARTED and historical Mac Core block remain unchanged. No commit/push until Main release.
