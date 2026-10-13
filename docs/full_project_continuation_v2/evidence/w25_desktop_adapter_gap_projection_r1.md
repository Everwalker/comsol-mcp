# W25 Desktop adapter: minimum production path and observation gates

Status: `READ_ONLY_ARCHITECTURE_PROJECTION / NATIVE_NOT_RUN`  
Date: 2026-10-12

This note proposes the smallest production path that preserves W25's existing durable operation, permission, identity, and artifact boundaries. It does not add selectors, enable a native control, run GUI automation, or start COMSOL.

## Evidence boundary

The current W25 integration already has a durable `DesktopCoordinator` over the daemon's `OperationStore`. It validates the frozen input contract, checks host permissions before claiming writes, resolves registered source artifacts and typed `ModelRef`s through trusted callbacks, leases observed OS windows by process/window identity, serializes each window and managed session, and records possible effects as `UNKNOWN` when final readback fails. Those are useful production seams, but software fixtures are not native Desktop evidence.

Current `desktop.status` sources are metadata-only. Windows enumerates visible COMSOL-owned HWNDs and associates them with process birth, login SID, Windows session, and executable product version. macOS uses Quartz plus psutil process identity and can lazily inspect Accessibility permission; its bounded AX tree associates one window with its PID and geometry but explicitly returns `model_binding=UNVERIFIED`. Neither adapter currently enables control leases. `desktop.*` action profiles therefore report the controls as unsupported.

One code path needs special treatment before any claim of Shell support: `DesktopCoordinator` currently resolves a `ModelRef` and immutable registered source, then calls its injected managed engine queue with `desktop.shell_execute`. That is a server-side Java callback, not evidence that input reached the COMSOL Desktop Java Shell. The future Windows implementation must dispatch through the observed Desktop UI control provider inside the same existing durable operation and serialized window/session scope. It must not delegate to another engine runtime or create another operation queue.

The local COMSOL 6.4 documentation evidence says the Java Shell is an interactive Desktop window available only in the Windows version; its commands act on the current Desktop model. It establishes neither an external submission endpoint nor a macOS Shell route. Source references supplied for this boundary: doc5207/chunk18347 SHA-256 `847130fbb53867dda5ac9fbc7101e6fbe024183556fc580f4cef1f3f1e46fa7d`; doc5852/chunks 0..9492 SHA-256 `9c4d7ef193e9525eaa09fbf768507da981bdf6a6b48ace7ef4394c3adc3f39cc`. No native UI tree or selector was observed for either platform.

## Minimum architecture

Keep `DesktopCoordinator` as the only operation claimant and lifecycle owner. Platform interaction should be an adapter call made inside its existing operation callback and per-window/session serialized block. Continue resolving all model and artifact references through the existing trusted production callbacks; do not accept selectors, Java text, arbitrary file paths, or claimed observations from the request. A profile may enable an action only when it is versioned against an observed OS/API and exact COMSOL build and its native selector evidence has been frozen.

Before a control action, the coordinator must revalidate the exact window handle, PID and process-birth identity, user/login, desktop session, product version, and bound project `ModelRef`/generation/revision. After the action, it must reacquire the same window and model observations and record the actual readback. A pre-action refusal can remain retryable when no effect began. Any action that may have started but cannot be read back leaves an `UNKNOWN` fence and `safe_retry=false`; it is never automatically replayed. Host UI changes made concurrently by a person remain an external source of drift, so every observation boundary must fail closed if the current selection changes.

A trusted UI profile should contain observed control roles/identifiers, parent-path constraints, supported product build, and per-action pre/postconditions. It should be selected by observed runtime identity, not by caller input or a window title. Any missing, duplicate, disabled, or changed control refuses. The existing artifact resolver and ArtifactStore remain the route for immutable source inputs and capture/save-copy outputs.

## Per-action implementation boundary

| Operation | Existing production seam | Required native observation before enablement |
|---|---|---|
| `desktop.bind` | Resolve exact project `ModelRef`; inspect managed endpoint/tag/fingerprint; retain window lease only after successful durable completion. | A current-model signal from the specific Desktop window that can be independently cross-checked with that `ModelRef`. Window title, path, model tag inventory, or `ModelUtil.tags()` alone is insufficient. |
| `desktop.show_model` | Existing verified ModelRef and pre/post engine snapshots. | Build-specific model picker/current-model selector in that exact window, with an observed readback proving the requested ref is now displayed. |
| `desktop.select_node` | Typed catalog `NodePath` validation and verified binding. | A versioned tree mapping from each supported `NodePath` shape to one actual Desktop tree item, with exact selected-node readback. |
| `desktop.capture` | Target-window capture result must return immutable artifact id and SHA through the existing sink; whole-desktop requests are already rejected. | Exact window and graphics-region mapping plus actual capture API, permission proof, and published bytes/hash readback. macOS has a permission query and CG/AX window association, but no capture implementation or trusted artifact sink in the current adapter. The separate Windows `PrintWindow` helper is a whole-window primitive and is not routed to this operation. |
| `desktop.action` | Trusted action resolver creates a bounded `DesktopActionPlan`; coordinator verifies binding after execution. | Per-build observed menu/control selector for each admitted action, its mutation classification, and action-specific postcondition. Generic AX/UIA availability is not enough. |
| `desktop.shell_execute` | Existing HOST_CONTROL + TRUSTED_CODE admission, project-scoped registered source artifact, ModelRef resolution, and durable claim. | Windows only: observe and identify the Java Shell input, execution control, and output/readback surface in COMSOL Desktop for the exact build; verify current-model identity before and after. Execute only the registered source bytes. Route through the platform adapter, not the managed-server Java engine callback. On macOS, keep typed unsupported unless later official COMSOL evidence establishes a Desktop Shell route. |
| `desktop.migrate_standalone` | Existing software-only copy/load transaction can resolve a save-copy into an already registered managed target. | Observe the exact standalone model, dirty state, save-copy controls/dialog and source-preservation postcondition; publish the copy through the existing no-clobber ArtifactStore path; then verify load and the still-open original. No current native provider proves these facts. |

`desktop.status` can remain a read-only metadata operation and report process/window identity and permission state. It must continue to distinguish metadata availability from control availability; a version string does not certify a selector profile.

## macOS model-label challenge candidate

A challenge-based binding proof is a possible future method for an already managed, task-owned reversible copy, not a capability available from the current source. In the same authorized `ModelRef`/revision lane, an implementation could set a unique unpredictable temporary model label, verify that the same CG window and AX element for the same PID/process-birth displays that exact challenge, restore the original label through the same model reference, and verify the restored label and final model readback. This would be stronger than title/path/tag matching because the visible observation is tied to an action performed against the exact managed model.

This approach remains blocked until a native observation proves that COMSOL exposes the label in an identifiable AX value and the existing model API offers a supported, revision-aware way to set and restore it. The current AX tree is metadata-only and has no such selector. The coordinator also has no app-level lock against a person changing the visible model while the operation runs. A challenge mismatch, window/PID change, unexpected revision, failed restore, or missing final readback must therefore record `UNKNOWN`, retain the exact recovery identity, and refuse retries; it cannot restore by selecting another window or model based on title. Use only the disposable task copy and never temporarily rename a user's uncommitted model.

Window-scoped capture may become separately possible by using an observed CG window ID and the existing capture-permission check, followed by publishing the exact bytes through the registered ArtifactStore. The current `MacOSFrameworkGateway` can check permission and read a bounded AX tree, but it does not capture pixels or prove model binding. Thus this is a source-design seam, not a current PASS.

## Required observations and acceptance

For Windows, first inspect a real authorized COMSOL Desktop 6.3 and 6.4 window using a read-only UI Automation tree. Record the executable product build and window/process identity, active model indicator, Java Shell input/run/output controls, tree controls, and any save-copy controls. For each selector, record its UIA role/identifier and parent chain and prove uniqueness; do not use screen coordinates or guessed labels. Only after those observations can one decide which individual actions merit an allowlisted profile.

For macOS, first observe one installed COMSOL 6.4 process/window and its AX tree under the actual product caller. Determine whether a visible active-model label and a unique actionable AX element exist. If not, keep binding/control unsupported. If they do, test the challenge/restore only on a registered disposable model copy, without solving, and prove that model revision/readback and visible label are restored; any ambiguity fences the model.

Software validation must cover each adapter profile's exact supported build, malformed/duplicate selector profiles, wrong process/window, stale `ModelRef`, missing permission, authorization refusal, idempotent replay, mid-action loss of readback, concurrent-window drift, and artifact no-clobber/hash verification. Native validation is separate and must use the single assigned COMSOL lane. The W25 integration freeze records 188 passing software tests and zero native actions; it does not certify native selectors or any Desktop action.

## Source snapshot reviewed

These are the current local source hashes read for this projection; this note does not alter them:

- `comsol_mcp/_desktop_service.py`: `5e3afae0faafb925d5a8cd2d9e6b708a3e28e7aa173a3f50f1d2527a66953a38`
- `comsol_mcp/_desktop_platforms.py`: `e7c2168904a36645c5ae7ec09b3e8a161b0576448fa41dc242a97fa3d3ae0f96`
- `comsol_mcp/_desktop_windows.py`: `a652a89f18365fe87455ff2aae933274a809cbec77753304cdf085d75f7bf5e3`
- `comsol_mcp/_desktop_macos.py`: `bcb98aff63a94a60d8db12865828129eab852d6fea618fe7b38bc08441146d7c`
- Current frozen action catalogue: `comsol_mcp/data/g2/02_ACTION_CATALOG.json`, SHA-256 `124ef66964e7f2cae78eca79b974f4f8c2ea1a3263f6440427de5edab052124e`
- W25 software/native boundary receipt: `docs/full_project_continuation_v2/evidence/w25_integration/W25_INTEGRATED_FREEZE.json`, SHA-256 `36e0b7601a401552882cf61f77ccff0704f24f3987ca2296aa31875550ce487f`

No source or test files were modified for this projection. `COMSOL native GUI: NOT_RUN`; Windows/Mac selectors remain unobserved.
