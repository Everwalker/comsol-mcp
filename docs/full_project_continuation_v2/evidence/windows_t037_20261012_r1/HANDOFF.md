# Windows T037 software handoff

This checkpoint freezes the current T037 observer, startup profile, process-factory seam, and daemon wiring. The exact working-tree bytes are preserved at `local-preservation/windows-t037-20261012-r1/`; `SOURCE_FREEZE.json` and `RUNS.json` bind the source and test evidence. The shared source files also contain adjacent resource/model work; the freeze records complete file bytes so the integration can be reviewed without presenting those files as T037-only diffs.

## Implemented

- `comsol_mcp/_windows_t037.py` defines a closed host-bound profile schema, pinned ACL-verified profile loading, strict read-only Win32 token/process/job/volume queries, and a read-only feasibility CLI. The observer does not claim WFP, SCM, descendant, or COMSOL loopback verification.
- The Windows process factory implements a suspended AppContainer launch path: it pins and hashes the executable, uses an explicit stdio handle list, creates the child suspended, assigns the per-session Job, reads back the exact image/token/job binding, and resumes only after those checks. The factory has no `Popen` fallback. No proof issuer exists in this checkpoint, so the launch path remains unreachable and refuses before process creation.
- `ControlDaemon` accepts `--windows-t037-profile` or `COMSOL_MCP_WINDOWS_T037_PROFILE`. Configured `session.start`, `session.connect`, `session.reconnect`, and trusted-code routes fail closed before durable admission unless fresh native assessment is verified. Unconfigured legacy behavior remains unchanged.
- `OwnedServerLauncher` and `PersistentJavaWorker` accept an injected per-session process factory. Their preflight runs before private Server directories or Worker runtime/classpath/javac work. Worker reconnect retains the existing process handle and does not replace it.
- `WINDOWS_T037_PROFILE.md` documents the closed schema, startup options, and current refusal boundary.

## Verification

- Final T037 source tests: 24 passed under the strict network guard, zero denied events.
- ControlDaemon and SessionServer no-network regression: 25 passed; the one actual loopback/process-birth test was separately run.
- Local-only loopback subset: 8 passed, 49 deselected. This used only temporary `127.0.0.1` sockets and one temporary child, with no COMSOL/JAR, GUI, external network, or shared Server. Product and selected test source hashes match before and after that run.
- `py_compile` passed for all touched Python modules and tests.
- Read-only CLI ran on Darwin and returned the expected `UNSUPPORTED_HOST` result; this is not a Windows observation.
- Earlier r2/r4/r5 failures remain in their original evidence folders. r4 had 11 failures and 7 errors while the strict guard denied local socket operations and one Windows path-budget fixture exceeded its synthetic path limit. Shortening pytest's base path fixed the path case in r6; the guarded loopback tests were run separately in r7. r2's test-harness failure was corrected before r3. See `RUNS.json` for JUnit and guard hashes.

## Open acceptance items

This is `SOFTWARE_ONLY`. Native Windows kernel tests and T037 host-policy acceptance remain `NOT_RUN`/`UNVERIFIED`. The current observer intentionally cannot verify or configure the dedicated SCM identity, WFP query/apply/restore, AppContainer profile/ACL deployment, COMSOL Server/Worker loopback compatibility, or the complete descendant boundary. Hard quota and Job Object memory enforcement are separate and not implemented here. Consequently a configured profile currently produces a typed policy refusal; this unit does not enable restricted execution. No COMSOL engine, model, GUI, account, ACL, firewall rule, quota, or system setting was changed.

## Next memory-admission design

Implement host-wide memory admission through the existing trusted resource configuration, OperationStore SQL ledger, project Authority, and single serial execution lane; do not add a queue. Keep three inputs distinct: the configured host memory budget, a fresh native available-memory observation, and trusted per-operation memory estimates. The reservation scope is host-wide across projects, and an unresolved `UNKNOWN` reservation from any project continues to count.

At queued-job admission, bind the job and operation through the existing Authority, resolve estimates from trusted configuration, and reserve disk and memory in the same SQL transaction when both apply. A failure in either dimension rolls back both reservations and occurs before `RUNNING` or Worker send. Reuse existing durable terminal and process-quiescence rules for release: durable terminal permits release only when the writer is quiescent; `UNKNOWN` remains reserved until fresh trusted evidence proves the owned process and writes have stopped. Queued-only crash recovery may release only from the existing durable no-dispatch proof, never from age or timeout. Null execution deadlines do not remove admission limits.

The native memory observation adapter must report platform semantics and freshness without implying a hard cap. A reservation budget is admission control; Job Object committed-memory limits, process RSS, Java heap, COMSOL native allocations, and OS hard limits remain separately reported controls. Add positive, low-budget, stale/wrong-host, cross-project concurrent-reservation, rollback-of-the-other-dimension, terminal-release, `UNKNOWN` retention, and restart-retention tests against the real OperationStore/coordinator. Do not claim a hard memory limit until the OS adapter is separately implemented and verified.
