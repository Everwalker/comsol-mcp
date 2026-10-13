# W25 native desktop adapters — R5 source correction

R5 corrects the five findings in the immutable R4 source review while keeping
native desktop and COMSOL execution out of scope. The prior R4 freeze and its
review remain unchanged; this note describes the successor source only.

## Source changes

- ScreenCaptureKit capture now checks for a readable macOS 14.0-or-newer
  release before permission checks or framework loading. The exact-window
  gateway repeats the gate, so direct gateway calls cannot reach
  `SCScreenshotManager` on macOS 12 or 13.
- PyObjC ScreenCaptureKit access uses callable Objective-C getters for
  `windows()`, `windowID()`, `owningApplication()`, `processID()`, and
  `frame()`. The window ID and owning PID must each have one exact match.
- Windows and macOS source-preservation receipts return the currently observed
  standalone model ID. Save Copy compares the full pre-effect baseline
  (standalone mode, model ID, label, fingerprint, and dirty state), and the
  coordinator compares the current ID again after managed loading.
- The Windows UI Automation process pin holds a native process handle for the
  request and checks the pinned process birth time, login SID, session, and
  HWND owner before and after every effect. The handle is disposed in a
  `finally` block. Missing or changed identity makes the operation UNKNOWN;
  the coordinator does not replay it.
- Native Save Copy targets a new owner-trusted private task directory. The
  coordinator waits for a two-second stable window, opens and hashes the
  staged file through a pinned handle, and streams those exact bytes through
  the existing ArtifactStore no-clobber publisher under the durable
  `desktop.migrate_standalone` claim. A final-target race preserves the
  foreign file. The managed model is loaded only from the verified published
  receipt. Failed or uncertain Save Copy leaves its private evidence and
  cannot be replayed into a second UI effect.
- Private stage removal uses exact pinned identities. The Windows publisher
  helper deletes only the exact stage file and directory handles; an
  identity/security mismatch leaves the residue in place.

## Verification

The R5 focused/affected software suite is bound in the adjacent freeze packet.
It covers ScreenCaptureKit OS and getter gates, source-ID drift, process
identity request fields, exact durable migration callback admission,
no-clobber destination races, private-stage replacement during stream/hash,
delayed partial Save Copy writes, and sticky UNKNOWN/idempotency behavior.
Fixtures and mocked Win32/PyObjC boundaries establish software behavior only.

Windows UI Automation, Windows kernel handle/ACL behavior, native macOS
ScreenCaptureKit/Accessibility, and licensed COMSOL desktop behavior remain
`NOT_RUN`. No native UI or COMSOL operation was launched for R5.
