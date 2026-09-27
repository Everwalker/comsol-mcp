# W23 TE API preflight freeze template

This template defines the managed native **property-readback-only** run. A
generated `freeze.json` records the exact candidate file hashes and installed
COMSOL Java classpath. `execute` also requires its exact SHA-256 on the command
line. A reviewed freeze hash is only permission to run this bounded preflight;
it does not accept a numerical result or authorize a later solve campaign.

## Frozen scope and budget

- Target: one task-owned COMSOL 6.4.0.293 server on macOS, loopback-only, with
  one persistent Worker session.
- Budget: at most 900 seconds from the owned engine's process birth, reserving
  the final 60 seconds for cleanup. Do not create a second server.
- Native action: one managed `code.execute_java` invocation of
  `NativeW23TEFixture#run` with `phase=api_preflight`. Geometry and feature-node
  construction and COMSOL property/selection/field readback are in scope.
- The fixture's output Port selection at `x=10 um` is an API-only outer-edge
  probe. It is not the science receiver or an overlap/coupling plane. The later
  science model must bind the signal, reference mode, and incident reference at
  the internal receiver plane `x=8 um`, with a separate PML power balance.
- Prohibited: every `Study.run`, solver submission, result evaluation, export,
  GUI interaction, or numerical/physical acceptance claim.
- Required evidence: source/classpath freeze, two quiescent prelaunch
  inventories, exact engine process birth/command/PID and loopback listener,
  Worker engine identity, model revision, request identity, raw managed
  response, study-run event audit, exact cleanup and post-cleanup inventory.

## Dispatch and unknown-state rule

Write one unique request ID and idempotency key to the evidence directory
before dispatch. The runner dispatches once. If the call throws, times out,
returns without a terminal Worker receipt, or the daemon cannot prove all work
terminal, record `UNKNOWN`, do not retry or create a replacement request, and
leave the task-owned server running for inspection. A later observation may
reconcile the same request; it must not resubmit it.

## Cleanup rule

Disconnect and stop only after the Worker request is terminal and the managed
daemon proves all jobs and Worker requests terminal. Before stopping, refresh
the exact PID's birth and command and its loopback listener identity. If any
identity is absent or differs, send no stop signal and preserve the process.
After stopping, verify the original PID is absent, its listener is absent, and
a fresh process/listener inventory is quiescent. Any gap is
`CLEANUP_UNVERIFIED`, not a clean pass.

## Acceptance boundary

Successful completion may establish only that the frozen Java fixture built
the intended scaffold and returned exact COMSOL properties, allowed values,
value types, defaults, field/component tags, and selection readbacks. Record
the exact `PortType` property's presence and allowed values. Seeing `Numeric`
is API evidence only; choosing that value, configuring or exciting the Ports,
solving, measuring native fields/integrals, and accepting overlap remain later
gates. `native result`, numerical acceptance, and physical acceptance stay
`NOT_RUN`.

For the API-only outer-edge probe, read back and preserve the declared plane
semantics: output Port at `x=10 um`, science receiver at `x=8 um`, and an
explicit prohibition on reusing the API probe as the science coupling plane.
