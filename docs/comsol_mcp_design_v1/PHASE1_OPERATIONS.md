# Phase-one operation and migration notes

Scope: W01–W04 only. No W05 session identity, W06 managed-worker lifecycle,
W09 typed error protocol, or cross-platform release is delivered here.

## Replay

Use a Python environment with the declared `mcp`, `MPh`, `JPype1` dependencies
and pytest for unit tests. The validated interpreter is recorded in PROGRESS.
No global environment or MCP client configuration was changed.

1. Register a local isolated COMSOL 6.4 server with its PID, start time, port,
   preferences directory, and exit policy. Do not reuse a user solve server.
   The tested server used `-graphics -multi on`, private preferences/temp
   directories, and generated authentication. `-login auto` does not mean
   anonymous access. The original test server ownership is in W02 evidence.
2. Run `python tools/w02_run_mcp.py --pid PID --port PORT --prefs PRIVATE_PREFS`.
   Preferences must be below this repository's ignored `.phase1-private/`.
   This entry invokes `runtime_poc_v64` through real stdio MCP and leaves a new
   timestamped evidence directory. It attaches only and does not terminate the
   server. It uses external JDK 11 and the installed official client JAR list.
3. Run `python tools/w04_run_mcp.py --pid PID --port PORT --prefs PRIVATE_PREFS`.
   This test-only MCP server binds real JPype/MPh to that server, loads copies
   of the accepted W02 MPH, and invokes production Python tools. The test-only
   fixture/snapshot actions are never registered in the production server.
4. Run `python -m pytest -q`. These control-flow tests do not certify COMSOL.

The registered server from this run is PID 84749, loopback port 56388, with
preferences under `.phase1-private/20260918T175300Z/prefs-server`. Verify that
identity before reuse; a historical PID is never permanent authorization.
The server remains available for follow-on work and is not stopped by the
attach-only tool. Failed and successful attempts are retained separately.

A client timeout produces `EXECUTION_STATE_UNKNOWN`. Inspect its recorded PID,
logs and server state before retry. The dedicated fixed Java clients exit only
after the successful operation and disconnect. This is not a general worker
cancellation implementation. A failed attempt hung on a vendor idle non-daemon
pool; its thread dump and subsequent client-only SIGTERM are retained.

## Changed legacy behavior

- Evaluation uses an owned UUID numerical node, serializes ephemeral mutation,
  and removes only that node. Cleanup failure is an evaluation error and does
  not trigger another fallback. `pure_read` explicitly refuses this backend.
- Evaluation preserves numerical array axes, including complex real/imaginary
  arrays where the accessor supports them. `max_result_size` no longer silently
  truncates. Consumers should accept arrays rather than assume a scalar.
  `first`, `last`, `all`, or a positive inner solution index select the solution
  axis after retrieval. General dataset/outer-sweep typing remains W17 work.
- `get_core_metrics` and `run_visible_main_iteration` require explicit metric
  definitions. Invalid definitions are rejected before iteration writes/solve.
  No corrosion-specific default expressions are evaluated.
- Global/component variable groups use actual names as setter keys; setting
  an existing group appends/updates entries. COMSOL root `variable()` can expose
  component and generated internal expression groups too. Raw inventories are
  retained; only fixture user groups are compared across MPH reopening because
  generated internal caches are rebuilt. Re-solve after variable changes when
  evaluating against a stored solution.
- Physics parent selections accept an empty feature tag or the physics tag.
  Explicit/all/named selections return actual readback. Noneditable and
  unsupported inheritance requests fail with the engine error. The tested PDE
  default feature was noneditable; its custom flux feature rejected inheritance.
  A successful inheritance change on other physics is not certified.
- Loading/adopting/committing the main model no longer automatically prunes
  other models. Explicit prune is limited to tags created/loaded by this MCP
  process; model generations and shared-server identity hardening belong to W05.

## GUI and capability boundary

The COMSOL Desktop computer-use request was refused by the GUI approval layer:
`Computer Use was not approved to use COMSOL Multiphysics`.
No GUI permission settings were changed and no click/cancel was attempted.
T004/T023/T052 therefore remain BLOCKED, with binding `unknown` and cancel
`UNVERIFIED`. A public API inventory cannot prove cancellation. Batch cancel
semantics are not assigned to shared-server calls. Resume these cases only with
an accessible authorized Desktop window and an explicitly identified test job.

The supplied acceptance specification remains unchanged as a frozen contract.
Live results are in PROGRESS and `evidence/phase1_acceptance.json`; historical
claims in older attempt files are superseded by that ledger, never upgraded
merely because a method exists or `hasProduct` returns true.
