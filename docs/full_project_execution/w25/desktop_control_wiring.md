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

The production coordinator selects the platform adapter. The Windows path has
late-bound UI Automation methods; the macOS path has late-bound Accessibility
and ScreenCaptureKit methods. Every control is gated by a protected,
build-specific observed-selector profile and a fresh coordinator model-binding
check. No such COMSOL selector profile is installed or natively validated, so
the native methods remain fail-closed in the current environment. Java Shell
is explicitly Windows-only; macOS returns a typed unsupported result.

The managed queue now supports scoped typed-ModelRef resolve/inspect and a
private `save_copy` migration callback. It checks the exact durable parent/job,
registered session identity, source-copy witness, target tag delta, project
attribution and persisted readiness. A same-lane pre-dispatch guard rejects
writes against unresolved targets before backend receiver selection. These are
fixture-backed software paths; `desktop_binding_verified` remains false and no
native Desktop action is accepted. If READY publication/readback fails, the
finalizer records a NON_READY fence before releasing the session lane, while
preserving an already-SUCCEEDED parent result.

The source-level UIA/AX/capture adapters remain unverified on native COMSOL
windows. All new tests use explicit in-process adapter fixtures or the fake
dispatcher; they do not start COMSOL, enumerate a real window, or perform GUI
automation. See
[`desktop_service_interface.md`](desktop_service_interface.md) for the
per-action native adapter gaps and status matrix, and
[`native-adapters-implementation-r4.md`](native-adapters-implementation-r4.md)
for the current source/software projection.


## Isolated W25 contract003 Phase1 software scope (2026-10-02)

This successor exists only in the isolated source copy. It adds a lazy product PyObjC AX/Quartz prerequisite observer with exact target-window mapping, non-prompt caller permission reads and bounded AX metadata. Codex CUA/observer permissions are not product acceptance. The factory defaults to the existing metadata-only adapter; opting into the observer does not enable controls. Missing/ambiguous mapping, dependencies or permission refuse; no focused-window fallback, screenshot, guessed COMSOL selector, input or launch is performed by the observer. All seven control methods, current-model observation, Shell/save/cancel remain unsupported without actual build-specific evidence.

The Desktop queue now supports explicit JSON-encoded typed ModelRef resolution/inspection through the existing ExecutionService.inspect_evidence and model_project_binding, exact active durable claim and existing ProjectAuthority. It performs a fresh scoped parameters+shallow-tree identity read, not full-model identity/dirty preservation or a GUI binding. Wrong project/epoch, dirty or active model, or an external change refuses. Opaque identifiers without a trusted mapping still refuse. Registered source resolution reuses existing immutable project-artifact integrity/host scope; save-copy destination authorization reuses existing non-overwrite/symlink-confined ArtifactStore path policy. No recursive daemon dispatch, second durable claim, backend accessor or coordinator post-fingerprint semantics change.

This Phase1 snapshot preceded the Phase2b single-claim managed copy-load callback. The later software callback only loads a project-authorized copy into a registered managed session; it does not perform or certify native Desktop save-copy, current-window binding, or Desktop Shell actions. Generic backend.invoke/managed-server Java still cannot substitute for an observed Desktop Shell provider. Original eight actions and T004/T023/T051/T052/T054 are still NOT_RUN/unaccepted. Phase1 software fixture evidence and retained first-run failures remain in the isolated Phase1 HANDOFF. Current scoped source/test hashes and the 188-case software run are recorded under `docs/full_project_continuation_v2/evidence/w25_integration/`; Root review and all native/action-matrix acceptance remain open.

COMSOL 6.4's local Java Shell manual states that this interactive window is only available in the Windows version of COMSOL Desktop. Source: *Interactively Using the COMSOL API for Java in the Java Shell Window*, `doc/help/wtpwebapps/ROOT/doc/com.comsol.help.comsol/comsol_ref_modeling.19.028.html`, chunk 19260, SHA-256 `9c4d7ef193e9525eaa09fbf768507da981bdf6a6b48ace7ef4394c3adc3f39cc`. No supported macOS Desktop API route for current-window/model/endpoint identity, dirty state, or source-preserving save-copy is established by this candidate. Those observations remain `UNOBSERVED`, and their controls must refuse until supported evidence exists.
