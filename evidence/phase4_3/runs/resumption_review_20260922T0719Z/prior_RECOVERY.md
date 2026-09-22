# G3.3 clean-room recovery and continuation

## Current recovery boundary

The original workpack independently restores source at commit
`2cb4627924d1a3240818ea7cd00453d4bd2d2da8`, tree
`dd3095e89c640c19aeb151cd0e8efe4af3c55802` over the network. This has been rechecked in a
new directory in the current session. It does not contain or restore commercial COMSOL,
a license, credentials, private models, or the uncommitted repairs in this working tree.
A final source workpack and published repair SHA are still pending acceptance; do not
claim that the fixed PIN reproduces these current uncommitted changes.

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
The historical dependency file is used as a requirements file (it includes `-e .`):

```bash
python3 -m venv .venv-new
.venv-new/bin/python -m pip install -r constraints-macos-arm64-py313.txt
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

Before declaring completion, finish all locally executable required W17 cases, three-chain
Gate A, dependency-based G2/G3 regressions, wheel resource/import checks, a clean-path
restore of the final repaired source, publication scrub/hash audit and ordinary non-force
synchronization with recorded SHAs. Until then keep overall acceptance false and do not
enter W18. Windows, Intel Mac, COMSOL 6.3 and GUI remain unverified unless separately run.
