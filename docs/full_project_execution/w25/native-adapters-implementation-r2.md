# W25 native adapter implementation (source and software scope)

Status: `SOURCE_IMPLEMENTED / SOFTWARE_GUARDED / NATIVE_GUI_NOT_RUN`.

This note supersedes the 2026-10-02 source-capability claims in `desktop_platform_adapter_matrix.md`. The implementation adds real late-bound Windows UI Automation and macOS Accessibility/ScreenCaptureKit API paths behind the existing `DesktopCoordinator`, durable operation claim, lease, model binding, project authorization, and ArtifactStore sink. It does not supply a trusted observed-selector profile or native acceptance.

## Profile and identity gates

The host profile loader accepts only strict, duplicate-key-free JSON from a protected host path. It pins the file while reading and binds the profile to an exact OS build, COMSOL version, recent observation timestamp, observation SHA-256, platform and selector inventory. Unknown keys, duplicate selectors, missing profiles, changed bytes, stale build/version and unobserved actions fail closed. Request arguments cannot provide selectors or executable code.

The loader now binds that read to the host provider's actual security contract: it records the provider's initial identity, opens the file, checks the descriptor against that identity, then rechecks expected identity and ownership/ACL evidence before and after the bounded read. Windows requires the exact `windows-read-control-owner-dacl-persistent-acls` contract and compares the pinned CRT descriptor through the provider's native `GetFileInformationByHandle` identity representation; it does not compare CPython's potentially wider `fstat` file IDs to the provider's narrower ID. Windows ACL digest changes and verify/open replacement fail closed. These are software controls; Windows kernel behavior remains `NOT_RUN`.

Action entries bind one exact selector and exact before/after selector values. The coordinator resolves a profile-backed action only after lease/window and model binding checks; the adapter rechecks the before value immediately before UI action and requires the exact post-value. Missing readback after invocation is `UNKNOWN` with retry disabled. Model label challenge continues through the same ModelRef/revision-checked Worker queue and restores the original label; no static title, tag or path establishes model binding.

## Platform route matrix

| Operation | Windows source path | macOS source path | Remaining gate |
|---|---|---|---|
| `desktop.status` | Observed HWND/process/session metadata | Quartz/psutil/Accessibility prerequisite metadata | Native enumeration and real profile status are `NOT_RUN` |
| `desktop.bind` / `desktop.show_model` | Exact UIA selectors plus coordinator ModelRef readback/challenge | Exact AX selectors plus coordinator ModelRef readback/challenge | No build-observed COMSOL selector profile is installed |
| `desktop.select_node` | Hash of canonical NodePath maps to one exact UIA selector | Hash of canonical NodePath maps to one exact AX selector | Mapping must be observed for the exact host/build; absent mapping refuses |
| `desktop.capture` | One observed HWND through bounded `PrintWindow`; exact target fingerprint; PNG/BMP validation; project-local no-clobber ArtifactStore publication and registration | One observed CGWindow/PID through `SCContentFilter.initWithDesktopIndependentWindow_` and `SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_`; Screen Recording permission; exact target fingerprint; project-local no-clobber ArtifactStore publication and registration | Window-only; no graphics subregion is guessed. macOS ScreenCaptureKit requires macOS 12.3+. Native capture and registration are `NOT_RUN` |
| `desktop.action` | Host profile action descriptor, exact UIA precondition, action, postcondition | Host profile action descriptor, exact AX precondition, action, postcondition | No observed action is certified; missing/stale selectors refuse |
| `desktop.shell_execute` | Exact UIA Java Shell fields; only registered immutable Java source is entered; completion/output/source hash readback | Typed `UNSUPPORTED_PLATFORM` | COMSOL 6.4 documents Java Shell as Windows Desktop only. Windows UIA selectors/native Shell remain `NOT_RUN` |
| `desktop.migrate_standalone` | Exact UIA Save Copy/path/confirm selectors, new project-authorized `.mph`, streamed SHA check and source-state reread, then existing managed load callback | Exact AX Save Copy/path/confirm selectors, new canonical project-authorized `.mph`, stable pinned-handle SHA check and source-state reread, then existing managed load callback | No overwrite; no copy if the source lacks exact standalone ID/fingerprint/dirty baseline. Native UI and COMSOL load are `NOT_RUN` |

## Capture and optional dependency

macOS capture does not call the deprecated display/window-list screenshot route. It resolves one ScreenCaptureKit shareable window by both observed window ID and owning PID, creates a desktop-independent window filter, bounds the requested image size, encodes PNG with AppKit and returns bytes plus the observed identity fingerprint. `MacOSDesktopAdapter.capture` refuses an unreadable OS version or release older than 12.3 before permission/API access; it then requires non-prompt Screen Recording permission. It lazily imports `ScreenCaptureKit` and reports a typed unavailable result if the optional wrapper is not installed.

The live Desktop extra still omits `pyobjc-framework-ScreenCaptureKit`; its official 12.2.2 PyPI metadata requires `pyobjc-core>=12.2.2`, `pyobjc-framework-CoreMedia>=12.2.2` and `pyobjc-framework-Cocoa>=12.2.2`. The selected CPython 3.12 universal2 wheels and exact URL/SHA/size fields are recorded in the plan-only successor `docs/full_project_continuation_v2/evidence/release_data_lifecycle/desktop-builder-candidate-r2-w25/`. That candidate adds two wheel artifacts (41,019 bytes unique) and keeps `pyproject.toml`, `uv.lock` and project version unchanged. No package file was downloaded, installed or built.

## Evidence limits

The W25 tests exercise actual coordinator, ControlDaemon, ArtifactStore, selector-loader, UIA/AX gateway and ScreenCaptureKit adapter code with synthetic local frameworks and image bytes. They establish the software contracts and fail-closed behavior only. They do not prove the presence or correctness of COMSOL controls, OS permission grants, UIA/AX selectors, native capture, active-model binding on a real desktop, COMSOL Save Copy, numerical state preservation, or Windows/macOS runtime support. Native Windows UIA, macOS AX/ScreenCaptureKit and COMSOL execution remain `NOT_RUN`.

Official API references:

- [Microsoft UI Automation: obtaining elements](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-obtainingelements)
- [Microsoft UI Automation: SelectionItemPattern](https://learn.microsoft.com/en-us/dotnet/api/system.windows.automation.selectionitempattern)
- [Apple `SCContentFilter`](https://developer.apple.com/documentation/screencapturekit/sccontentfilter)
- [Apple shareable ScreenCaptureKit content](https://developer.apple.com/documentation/screencapturekit/scshareablecontent/getexcludingdesktopwindows%28_%3Aonscreenwindowsonly%3Acompletionhandler%3A?language=objc)
- [Apple `SCScreenshotManager.captureImage`](https://developer.apple.com/documentation/screencapturekit/scscreenshotmanager/captureimage%28contentfilter%3Aconfiguration%3Acompletionhandler%3A?changes=latest_major&language=objc)
- [PyPI ScreenCaptureKit 12.2.2 JSON](https://pypi.org/pypi/pyobjc-framework-ScreenCaptureKit/12.2.2/json)
- [PyPI CoreMedia 12.2.2 JSON](https://pypi.org/pypi/pyobjc-framework-CoreMedia/12.2.2/json)
