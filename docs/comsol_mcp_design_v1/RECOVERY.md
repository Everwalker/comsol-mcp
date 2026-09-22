> Source recovery is now verified for snapshot `84ec0962a6424cd54c523770e4898bf42b7af645`: evidence/phase4_3/runs/final_recovery_84ec096_20260922T095300Z/final_recovery_receipt.json. The initial 698-member archive is a source-rebuild checkpoint; the final documentary archive adds historical proof references and final delivery records. Remote synchronization is still pending.

# G3.3 clean-room recovery and continuation

## Fixed-baseline bootstrap boundary

The original workpack independently restores source at commit
`2cb4627924d1a3240818ea7cd00453d4bd2d2da8`, tree
`dd3095e89c640c19aeb151cd0e8efe4af3c55802` over the network. This has been rechecked in a
new directory in the current session. It does not contain or restore commercial COMSOL,
a license, credentials, private models, or later repairs beyond that PIN.
A final source workpack and published repair SHA are still pending acceptance; do not
claim that the fixed PIN reproduces the later repair commits.

## Repaired snapshot and delivery boundary

The current tested development candidate is commit
`a3f39b3022b417e16c3087b8b35f1daa06f30b8a`, tree
`5a3e1b72e95a9df8892def2ca0994a8737508612`. It is the repaired source used by the
eight final6 public-MCP groups, the 1824-pass software run with one Windows-only skip,
and the source-outside wheel/resource check. The task-owned cleanup inventory is also
PASS with zero selected processes remaining. These receipts are runtime/software and
cleanup evidence for their stated scopes; they do not create a clean recovery archive or
public publication snapshot.

Bootstrap recovery and repaired-source delivery are separate operations: `bootstrap.py`
restores the fixed baseline PIN above, while the repaired candidate must be preserved in
the local development history and then copied into a separately verified publication
snapshot. Do not claim that a baseline bootstrap contains the repaired candidate, and do
not invent a future snapshot SHA or archive name.

## Restore the fixed baseline

Read the workpack's START_HERE.md, NEXT_GOAL.md, PIN.json, REVIEW.md, RESTORE.md and
ACCEPTANCE.md. From the workpack root, choose a destination that does not exist:

```bash
python3 tools/bootstrap.py --destination <new-empty-destination>
python3 tools/audit_repository.py --repo <new-empty-destination> --output <new-review-json>
```

Verify the restore receipt's commit/tree and per-file blob/hash checks. Do not silently
track main or copy an old local checkout. Read the recovered AGENTS.md and original design
specifications before implementation. The all-file inventory is not a semantic audit.
The historical `audit/semantic_review.json` reference is missing; retain that fact.

Create a new branch and new virtual environment in the recovered source directory.
For the repaired checkout on the recorded macOS arm64 / Python 3.14 environment, install explicitly with the current machine constraints (the historical py313 file remains unchanged):

```bash
python3 -m venv .venv-new
.venv-new/bin/python -m pip install -e ".[dev]" -c constraints-macos-arm64-py314.txt
.venv-new/bin/python -m pip check
.venv-new/bin/python -m pytest -q
```

Record the actual interpreter and full freeze; the filename does not certify Python 3.13.
The current fresh baseline used Python 3.14.7. Final repairs need their own machine lock,
full tests and source-outside wheel install before publication. A missing external license
is separate from a software defect and must not hide one.

## Continue the current repairs

Consult the latest correction ledger, baseline review and independent run findings.
Historical phase4_1, phase4_2 and w17 files are protected; 600 current historical files
were freshly compared against the network-restored PIN with no mismatches. Original
failed development runs must remain available. New evidence must bind new source hashes.

Follow G3_3_OPERATIONS.md for bounded public-MCP runs. Rebuild all test fixtures through
new authorized MCP operations. Stored-solution acceptance loads the identical saved MPH
hash in a fresh Worker and does not solve again; independent re-solving is another case.
Use the same production checker for all negative controls. Treat source audits and fake
control tests as their own evidence levels.

Before declaring completion, retain the final runtime/software/cleanup receipts, create a
clean-path recovery archive for the repaired candidate, and complete the publication
scrub/hash audit. Preserve the original development branch and evidence locally; create
the publication snapshot as a new child of verified `origin/main` PIN
`2cb4627924d1a3240818ea7cd00453d4bd2d2da8`, verify that it carries the same tested source
file hashes, exclude the three historical worker bearer-token blobs, and synchronize with
an ordinary non-force push. Recovery, publication and remote sync remain pending, so keep
overall acceptance false and do not enter W18. Windows, Intel Mac, COMSOL 6.3 and GUI
remain unverified unless separately run.

## Repaired source recovery

The first clean publication snapshot is `84ec0962a6424cd54c523770e4898bf42b7af645`, with real parent `2cb4627924d1a3240818ea7cd00453d4bd2d2da8`. The actual native tests ran at local development commit `a3f39b3022b417e16c3087b8b35f1daa06f30b8a`; `audit/publication_source_bridge.json` verifies all 184 tested source/config files against the publication snapshot. The original development ancestry remains local because it contains deleted credentials. It is not needed for rebuilding the repaired source.

For network recovery, clone the published repository into a new directory and detach at the explicit delivery commit recorded in the final receipt. Do not bootstrap the old PIN expecting repaired behavior. For source workpack recovery, extract into an empty directory and verify each entry in `ARCHIVE_MANIFEST.json`; use the extracted `repository/` directory. The source archive is a source snapshot, not a Git-history mirror.

Use Python 3.14 on the verified macOS arm64 environment, create a new venv, then install `.[dev]` with `constraints-macos-arm64-py314.txt`. COMSOL 6.4, a legal license, and JDK 11 remain external prerequisites for native tests. The package includes source, tests, public runtime reports, and fixture-generating drivers; it excludes credentials, installed JARs, virtual environments and generated MPH files. Recreate the A/B/C fixtures with `tools/g3_3_protocol_acceptance.py` under a fresh run directory; those newly generated files will have new run identities and must undergo their own same-hash fresh-worker reopen checks. Do not substitute a fresh solve for stored-solution verification.

Recorded absolute paths in historical receipts are provenance only. No recovery step requires those original directories or credentials. Windows, Linux, Intel Mac, COMSOL 6.3 and GUI remain unverified; W18 is not authorized.
