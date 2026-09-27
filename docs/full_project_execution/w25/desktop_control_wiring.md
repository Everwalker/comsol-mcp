# Desktop public control wiring

The eight catalog Desktop operations now have public MCP wrappers. Their MCP
names remain the catalog aliases (`desktop_status`, `desktop_bind`, and so on),
while `GatewayRegistry` dispatches their canonical dotted IDs. The tool
registration does not infer that a native action works from the presence of a
wrapper.

`ControlDaemon` routes each direct Desktop call and each `registry_call` /
`operation_call` containing a Desktop operation to one `DesktopCoordinator`
backed by the daemon's real `OperationStore`. Before dispatch, it calls the
authoritative catalog `validate_call` schema. The seven control operations use
the narrow `allow_coordinator_only` option: this admits the coordinator's
durable authorization/refusal logic while leaving the catalog records
`executable: false` and `PROPOSED_NOT_IMPLEMENTED`. `desktop.status` is a
read-only implemented route and appears in the default registry surface.

`desktop.status` uses the authenticated, same-user loopback control endpoint as
its host read authority when no COMSOL session exists. Once connected, the
managed ledger must grant `inspect`. The other seven actions require the
ledger's `host_control` grant, and `desktop.shell_execute` also requires
`trusted_code`; callers cannot provide permission flags. Permission failures
occur before an operation claim. These existing ledger grants are host-global
capabilities, not per-project ACLs; `project_id` is an execution scope field
and does not itself isolate Desktop permission. Admitted writes create the durable
idempotency claim before adapter or engine callbacks; same-key requests replay
the stored result and conflicting request bodies are rejected.

The production coordinator selects the platform's real metadata adapter. The
Windows and macOS adapters currently report `control_status=UNSUPPORTED_CONTROL`
and cannot mint write leases. There is no current-window/current-model binding
provider, action resolver, artifact resolver, or save-copy path authorizer.
Consequently no Desktop write can reach an operating-system control callback.
The managed API substep callback submits through the existing one-worker queue,
but currently returns a deterministic `stage=validation`,
`engine_dispatched=false` refusal because no trusted Desktop binding route has
been implemented. This path reports a failed unsupported operation, not an
unknown engine outcome.

The source-level desktop status observers and the separate Windows pixel
primitive remain unverified on native COMSOL windows. All new tests use
explicit in-process adapter fixtures or the fake dispatcher; they do not start
COMSOL, enumerate a real window, or perform GUI automation. See
[`desktop_service_interface.md`](desktop_service_interface.md) for the
per-action native adapter gaps and status matrix.
