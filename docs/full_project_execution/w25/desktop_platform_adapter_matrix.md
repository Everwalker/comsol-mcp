# W25 platform adapter profiles and source evidence

Status: `VERSIONED_SOURCE_PROFILES_IMPLEMENTED / WINDOWS_HWND_PIXEL_PRIMITIVE_IMPLEMENTED_NOT_ROUTED / NATIVE_GUI_NOT_RUN`.

This matrix records what the current platform code can observe, what the Windows pixel helper can do in isolation, and what remains unsupported in the public Desktop operation path. Source/API availability is not native acceptance. No GUI, COMSOL process, Java Shell, Windows UI Automation tree, Apple Accessibility tree, or native capture was inspected or invoked for this work.

## Versioned profiles

| Profile | OS evidence source | COMSOL version handling | Source capability | Native status |
|---|---|---|---|---|
| `comsol-mcp.windows-desktop-identity/v1` | `EnumWindows` + visible HWND, owner PID, process creation time, logon SID, Windows session, executable product version | Reports the executable version when available; version is an identity field, not a support certification | `desktop.status` metadata source | `NOT_RUN` |
| `comsol-mcp.macos-desktop-identity/v1` | Quartz window inventory + psutil process birth/login/session + optional bundle version and Accessibility trust query | Reports the bundle version when available; version is an identity field, not a support certification | `desktop.status` metadata source | `NOT_RUN` |
| `comsol-mcp.windows-hwnd-print/v1` | Win32 `PrintWindow` for one previously observed HWND, run by a bounded helper process | OS-level primitive only; COMSOL 6.3/6.4 paint behavior is not certified | Whole-window BMP bytes only; not wired to `desktop.capture` | `NOT_RUN` |

The platform adapter status payload now includes its profile ID, source capabilities, operation gaps, and `native_evidence=NOT_RUN`. A COMSOL executable version string alone does not enable any controls. macOS Apple Silicon and Intel COMSOL 6.3 remain `USER_REQUESTED_SKIP / NOT_RUN` under the campaign decision; this profile does not alter that decision.

The future UI profile should be separately versioned by OS and observed COMSOL build, for example `comsol-mcp.windows-comsol-ui/1`. Its manifest must identify the UIA framework/control patterns and the exact COMSOL build range for each action. It cannot be issued until a real, authorized observation records the relevant UI tree and model association. If a product update changes that tree, the profile must fail closed until the mapping is re-observed.

## How the eight actions obtain identity

| Catalog operation | Identity evidence required by the coordinator | Current adapter result |
|---|---|---|
| `desktop.status` | OS window ID, owner PID, process birth, login, desktop session, and reported COMSOL executable/bundle version. The title remains a display hint. | Source observer exists on Windows and macOS; native enumeration is `NOT_RUN`. Metadata-only profiles keep `control_status=UNSUPPORTED_CONTROL`, so they do not mint action leases. |
| `desktop.bind` | Platform observation of the current Desktop mode, server endpoint, and displayed model tag, crossed with a fresh typed managed `ModelRef` (server instance, model tag, generation, fingerprint). | `MODEL_BINDING_UNKNOWN`. No current-window/current-model reader exists. COMSOL `ModelUtil.tags()` enumerates all models, not the model displayed by a particular Desktop window. |
| `desktop.show_model` | A verified selector for the target COMSOL window, followed by UI readback of its current endpoint/model and a fresh managed snapshot. | `UNSUPPORTED_CONTROL`. No COMSOL window selector or selected-model readback is evidenced. |
| `desktop.select_node` | A fresh model binding plus versioned UI tree mapping from the typed API `NodePath` to an actual tree item and exact selected-path readback. | `UNSUPPORTED_CONTROL`. No COMSOL tree controls or mapping have been observed. |
| `desktop.capture` | A fresh model binding, an exact target-window/region mapping, and a trusted artifact sink that returns the captured bytes' immutable artifact ID and SHA-256. | `UNSUPPORTED_CONTROL`. The Windows single-HWND pixel helper is source-only and not connected to the coordinator or artifact store. Graphics-area selection and model binding are missing. |
| `desktop.action` | A fresh model binding plus an authorized, exact COMSOL menu/control selector for a supported build and post-action model/window readback. | `UNSUPPORTED_CONTROL`. UIA/AX availability does not prove any COMSOL-specific selector. |
| `desktop.shell_execute` | A fresh binding to the displayed model and a verified method to submit to that COMSOL Desktop's Java Shell, with returned execution/readback identity. | `UNSUPPORTED_CONTROL`. COMSOL documents Java Shell as an interactive Desktop window selected from the UI; it does not document an external submission endpoint. The coordinator's managed-server Java artifact path is a different route and is not evidence of Desktop Java Shell execution. |
| `desktop.migrate_standalone` | A fresh observation of the exact standalone window/model identity, dirty state and fingerprint; a new authorized save-copy with hash; managed load; and source preservation recheck. | `UNSUPPORTED_CONTROL`. The coordinator's save-copy state machine is tested with fixtures, but the platform cannot observe standalone identity/dirty state or save the UI model. |

No action becomes supported merely because an OS API can find a window or pixels. The coordinator still rejects public `desktop.capture` without a validated model binding. No guessed widget name, title text, path, or model tag substitutes for that evidence.

## Source-backed native primitives

Windows UI Automation can obtain a UIA element from an HWND, then search within the element tree using conditions and tree scopes. This establishes a way to inspect a target window, not COMSOL control names or model identity. The exact UIA hierarchy, patterns, selectors and observed values must come from the supported COMSOL build before an action adapter can claim support. [Microsoft: obtaining UI Automation elements](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-obtainingelements)

On macOS, Accessibility exposes an application-root element by PID, but the current source only reads the non-prompting trust bit; it does not query or act on the AX hierarchy. No COMSOL AX selectors have been observed. [Apple: `AXUIElementCreateApplication`](https://developer.apple.com/documentation/applicationservices/1459374-axuielementcreateapplication?language=objc)

COMSOL 6.4's Java API `ModelUtil.tags()` returns all model tags. That list has no documented association to the currently displayed model in one particular Desktop HWND. [COMSOL 6.4: `ModelUtil`](https://doc.comsol.com/6.4/doc/com.comsol.help.comsol/api/com/comsol/model/util/ModelUtil.html)

COMSOL 6.4 documents Java Shell as an interactive Java command window opened from the Desktop UI; it accepts commands and Enter/Run to execute them. The documentation does not provide a separate external automation endpoint, nor does the current source have a verified UI selector for the shell prompt. [COMSOL 6.4: Java Shell and Data Viewer windows](https://doc.comsol.com/6.4/doc/com.comsol.help.comsol/application_programming_guide.15.13.html)

The low-level Windows helper calls `PrintWindow(hwnd, hdc, 0)` for the complete window only. Microsoft states this call is blocking/synchronous and the target application processes `WM_PRINT`. The helper therefore runs in a short-lived spawned process, waits a fixed upper bound, and if needed terminates only that helper. A timeout says nothing about whether COMSOL has finished handling the paint message; the helper never signals or terminates COMSOL. It validates the HWND owner PID, process birth, login SID, Windows session and COMSOL executable version before and after painting; checks unchanged dimensions; bounds output to 8192 pixels per side, 12 million pixels and 64 MiB; writes only one new BMP beneath its private temporary directory; then checks the exact path, file type, header, dimensions, length and SHA-256 in the parent process. [Microsoft: `PrintWindow`](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-printwindow)

This helper is not a complete capture action. It does not read a COMSOL model, resolve a graphics-only subregion, publish to the project artifact store, or set `desktop_binding_verified=true`. It returns temporary bytes to a future trusted artifact sink; the public coordinator path continues to fail closed.

## Remaining source and native gaps

The repository's existing visible-main workflow binds an MCP managed-server model for server operations, and `tools/windows_handoff_snapshot.py` handles file handoff. Neither reads or selects a COMSOL Desktop window. The server-side visible-main state and a Desktop window handle are separate identities until a platform provider observes and cross-checks both.

For W25 target environments, status and pixel-source code are platform-labelled, while control support remains uncertified for Windows COMSOL 6.3/6.4 and macOS COMSOL 6.4. No statement here certifies native support for a particular build. The UI evidence needed to close each row is: exact OS/COMSOL build; observed window hierarchy/control identifiers; current Desktop connection and selected-model identifiers; API cross-check against the managed `ModelRef`; and action-specific before/after readback. No native GUI test was run.
