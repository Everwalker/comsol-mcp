# `ModelUtil.blockOtherClients` isolated validation plan

**Current state: the 2026-09-26T2253Z native receipt remains `FAILED` and
immutable. An offline protocol correction now holds owner A connected after
release while B performs a separate read. Its focused tests and bundled-runtime
preflight pass, but no further W25 server is authorized until root reviews the
new freeze.**
The current 6.4.0.293 compile receipt proves only that the installed API
contains `ModelUtil.blockOtherClients(boolean)` and `ModelUtil.disconnect()`.
The matching vendor help states that blocking is server-wide, has no built-in
timeout, defers other clients' messages, and is released by the requesting
client or when that client disconnects. This is not the Worker's local
endpoint lock.

## Scope and setup

Run only after the native slot is explicitly released and root approves this
specific API-only experiment. Use a new task-owned, loopback-only COMSOL 6.4
server with no model loaded, plus two independent Java client JVMs A and B.
The server, client source/class files, logs and scratch data stay under a new
private task directory. Do not connect either client to the shared/geometric
server, use the GUI, load/save a model, execute trusted code, or run a solver.

Both clients must connect and prove the endpoint before A acquires the block.
B first calls `ModelUtil.tags()` and records the empty baseline. Each later B
call prints and flushes `REQUEST_START` before making that same read-only call;
the caller records whether it returns before or after A's release. `tags()` is
used because it is a server API query with no model mutation, not because its
contents establish a Desktop/model binding.

The frozen source is `java/BlockOtherClientsClient.java`; its supervisor is
`run_block_other_clients.py`. The supervisor refuses to start unless passed
`--execute-native`, inventories for a pre-existing COMSOL process, creates a
private loopback shadow install, and verifies the exact owned listener PID and
endpoint before launching either client. Both Java clients must be separate
JVMs. Signals are restricted to their retained child handles after checking
PID, executable path, and start identity. Compile inputs and the API JAR are
bound by [the compile receipt](modelutil_block_other_clients_runner_compile.json).

## Budgets

- Overall wall-clock budget: **300 seconds** from starting the owned server to
  verified shutdown and evidence flush. Per-stage scheduling/wait limits are
  server startup 120 seconds, both connects and baseline 30 seconds, each
  block trial 5 seconds, each post-release B read 5 seconds, and owned-server
  shutdown 15 seconds. These are waiting/recovery budgets, not a server-side
  lock-expiry guarantee; `blockOtherClients` has no built-in timeout. The
  300-second deadline is the hard total cap.
- The supervisor reserves 25 seconds of the total budget for owned-client
  reconciliation and normal server shutdown. It writes a supervisor
  `RELEASE_COMMAND_SENT` / `INJECT_COMMAND_SENT` monotonic timestamp immediately
  before sending the command to A. All ordering comparisons use the supervisor's
  monotonic timestamps; equal timestamps do not prove an after-boundary result.
- A's block is never intentionally held longer than 1.5 seconds. An external
  supervisor timestamps `ACQUIRED`; at 3 seconds it begins cleanup and at 5
  seconds it stops waiting on that trial. No Java/Python timeout is treated as
  proof that the server lock was released.
- If a call does not return by its stage limit, record `UNKNOWN`, retain and
  reconcile the original client connection, and do not automatically rerun
  that trial or proceed to a later trial. A cleanup health read, if safe after
  resolving the original call, is recorded as cleanup and cannot promote the
  trial verdict. Any new native campaign requires a separate decision. Never
  kill the server to make a test pass.

## Trials

1. **Explicit release:** A acquires `blockOtherClients(true)`. The supervisor
   starts B's `tags()` call and observes `READ_CALL_ENTERED`, then allows one
   second for a result. A return before the release command is a blocking
   failure. A pre-boundary error is `BUSY_REFUSAL` only when the exact B event
   is a `READ_CALL_ERROR` for that trial label, with class
   `com.comsol.util.exceptions.FlException` and exact message key
   `Server_is_in_use_by_another_client`, after A's `ACQUIRED` event and before
   the supervisor's `RELEASE_COMMAND_SENT` boundary. Other errors are failures
   and are never promoted by substring matching.

   A emits `RELEASE_REQUESTED`, calls `blockOtherClients(false)`, then emits
   `RELEASE_RETURNED` and `OWNER_READY_FOR_POST_RELEASE_READ`. It remains
   connected and makes no COMSOL API calls while it waits for `FINISH`. If the
   original B call was pending, the supervisor records its completion/error
   separately. It then issues an independent B `tags()` call and records its
   result while A is still connected. Only after that result does the
   supervisor send `FINISH`; A records `OWNER_FINISH_ACCEPTED` and disconnects.
   The verdicts `WAITED_COMPLETION` and `BUSY_REFUSAL_RECOVERED` remain distinct.
2. **Exception and `finally`:** A repeats acquisition; B starts the same
   read. The supervisor sends `INJECT` after the bounded observation window;
   A throws a private sentinel inside the protected block. A's `finally`
   performs `blockOtherClients(false)`, emits `RELEASE_RETURNED`, and follows
   the same `OWNER_READY_FOR_POST_RELEASE_READ` / B fresh-read / `FINISH`
   handshake as explicit release. A missing release, fresh read, finish, or
   disconnect result is a failure or unknown, not a pass. Its verdict remains
   distinct from the explicit-release trial and from the waiting-completion
   branch.
3. **Owner disconnect recovery:** A repeats acquisition; B starts the read.
   After `READ_CALL_ENTERED` is observed, the supervisor terminates only the
   exact A JVM child it launched (verified by retained process handle, PID,
   executable path and start identity). It does not signal B or COMSOL Server.
   B must either complete after the termination boundary or produce the exact
   contextual busy refusal before it, then make an independent fresh read after
   A exits. Record `OWNER_DISCONNECT_WAITED_COMPLETION` separately from
   `OWNER_DISCONNECT_BUSY_REFUSAL_RECOVERED`; other errors fail. This tests the
   documented disconnect release path independently of Java `finally`.

Between trials, B must complete a fresh `tags()` call; it remains connected for
the next bounded trial and disconnects during final cleanup.
After any failed or unknown trial, later trials are recorded `NOT_RUN`; there
is no automatic repeat. Cross-process ordering uses one supervisor's monotonic
event times for commands and monotonic receipt times for flushed client events;
do not compare `System.nanoTime()` from different JVMs or infer release order
from pipe arrival order alone. `RELEASE_RETURNED` by itself is not recovery
evidence; the independent B read must finish before A receives `FINISH`. At the
end, both client processes must be known gone and the server known idle before the owned server
is shut down normally. The server must stop within its shutdown limit, and
the append-only event log and server log must be flushed with UTC wall-clock
timestamps, monotonic durations, PID/start identity and endpoint. Any
incomplete cleanup leaves the result `UNKNOWN` and requires manual
reconciliation; it is never reported as `PASS`.

## Acceptance boundary

A successful run may claim only **COMSOL 6.4 API-only server-wide client
blocking, explicit release, Java-finally cleanup, and owner-disconnect
recovery on one task-owned local server**. It does not validate a Desktop UI,
window/model binding, GUI controls, behavior under a long solve, any Windows
target, or COMSOL 6.3. The current user-scoped Mac 6.3 native target is skipped;
no 6.3 claim is made here.

The installed-client exact busy-key mapping and minimal `javap` excerpt, bound
to the installed client/resource JAR hashes and without copying either JAR,
are recorded in
[`installed_client_busy_refusal_definition.json`](../evidence/w25_block_other_clients/protocol_correction_20260926T2328Z/installed_client_busy_refusal_definition.json).
The current offline protocol correction is frozen in
[`protocol_correction_20260926T2328Z`](../evidence/w25_block_other_clients/protocol_correction_20260926T2328Z/).
It records the focused tests and Java 21 classpath preflight. The immutable
native receipt at
[`campaign_20260926T2252Z/run_20260926T2253Z`](../evidence/w25_block_other_clients/campaign_20260926T2252Z/run_20260926T2253Z/)
remains `FAILED`; its post-release `exception_finally` error was observed after
`RELEASE_RETURNED` and before A's disconnect, and did not establish release
recovery. No native result is inferred from the offline correction.
