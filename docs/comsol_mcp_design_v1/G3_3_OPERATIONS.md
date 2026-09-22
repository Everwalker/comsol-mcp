> Delivery update: repaired source snapshot `84ec0962a6424cd54c523770e4898bf42b7af645` has passed fresh extraction, wheel rebuild, and physically outside-source installation (64 source/package files, 272 operations, four Java resources; pip check clean). Remote synchronization and final documentary archive remain pending. See `evidence/phase4_3/runs/final_recovery_84ec096_20260922T095300Z/final_recovery_receipt.json`.

> Current status: **RUNTIME_VALIDATED_DELIVERY_PENDING** at source `a3f39b3022b417e16c3087b8b35f1daa06f30b8a`. The eight final6 public-MCP groups, full software suite (1824 passed, one Windows-only skip), source-outside wheel/resource check, and task-owned cleanup inventory have PASS receipts. Repaired-source recovery/archive, clean publication, and ordinary remote synchronization remain required; overall acceptance remains false. W18 is not authorized.

# G3.3 independent acceptance operations

This is a procedure, not a passing certificate. Current final-source evidence is
assembled under `evidence/phase4_3/runs/final_batch_a3f39b3_20260922T0907Z/`, with
software and wheel receipts under the corresponding `full_pytest_a3f39b3_*` and
`wheel_a3f39b3_*` directories. Historical correction ledgers remain preserved and
W18 is not authorized.
Prior operations text is preserved in the independent run's `prior_state/` directory.

## Fresh environment and runtime

The current independent software environment uses CPython 3.14.7. The historical
`constraints-macos-arm64-py313.txt` name is not evidence that this run used Python 3.13.
Commercial COMSOL 6.4 and JDK 11 must already be legitimately installed. Source restore,
unit tests and wheel import do not establish a usable license or engine.

`tools/g3_3_protocol_acceptance.py` uses public stdio MCP requests to construct new
fixtures and query results. It creates a task-owned server with a fresh ephemeral port,
auto-login preferences, temporary files and recovery directory under its new run path.
It reads the existing RemoteAddrValve configuration and verifies actual authenticated
loopback access and remote denial. It does not edit COMSOL installation configuration.
Do not copy an old isolation receipt, credential directory, model or process identity.

## Running a bounded group

From this repository root, using the newly installed environment's Python:

```bash
python tools/g3_3_protocol_acceptance.py --m1-only --run-dir evidence/phase4_3/runs/<new-run-id>
python tools/g3_3_protocol_acceptance.py --numeric-only --run-dir evidence/phase4_3/runs/<another-new-run-id>
python tools/g3_3_protocol_acceptance.py --nodes-only --run-dir evidence/phase4_3/runs/<another-new-run-id>
python tools/g3_3_protocol_acceptance.py --export-only --run-dir evidence/phase4_3/runs/<another-new-run-id>
python tools/g3_3_protocol_acceptance.py --run-dir evidence/phase4_3/runs/<another-new-run-id>
```

Use one command at a time and a never-used directory for each run. The default command
is the three-chain stored-solution reopen group and negative controls, not all W17 cases.
Native `--axis-probe-only`, `--coordinate-probe-only` and `--measure-probe-only`
produce API observations; their completion does not certify numerical acceptance.

Every run records source hashes, commit/tree/dirty state, actual UTC times, public
request/reply transcripts and process cleanup. Any source drift invalidates the run as
final frozen-source acceptance. A zero process exit code is insufficient: inspect all
nested case statuses and independently recompute numerical assertions. Failed original
records remain unchanged; a correction is a separate record.

## Safety and evidence

UNKNOWN requests must be observed through their original job status/result/reconcile
before any later mutation. Quiescence can be in `data.metadata.reconciled_quiescent`;
UNKNOWN itself does not prove either active execution or safe completion. Same-key
retries must preserve the exact original wire body. Observation requests use new keys.

Before a fresh Worker reopen, confirm the previous task-owned Worker and control daemon
have exited. The public lifecycle route does not grant permission to disconnect a shared
server. Cleanup is limited to verified owned process identities; no PID from old logs
is reused. The user's separate cleanup authorization allowed the inventoried prior
server/orphans to be terminated; it does not turn an arbitrary PID into an owned one.

Private preferences, tokens, endpoint credentials, venvs, installed JARs and licensed
manuals must stay out of publication. Publish a scrubbed, hash-traceable evidence subset
and reconstruction instructions. Preserve scientific files and failure evidence.

The delivery snapshot must preserve the original development branch locally, use the
verified `origin/main` PIN `2cb4627924d1a3240818ea7cd00453d4bd2d2da8` as its real parent,
carry the same tested source file hashes, and exclude the three historical worker
bearer-token blobs. Verify the snapshot before an ordinary non-force push; do not invent
a future snapshot SHA or archive name in advance.
