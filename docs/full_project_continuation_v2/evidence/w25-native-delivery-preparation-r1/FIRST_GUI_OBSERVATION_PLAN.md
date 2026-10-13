# W25 first native GUI observation plan (candidate only)

Status: **NOT AUTHORIZED / NOT RUN**. This document defines a bounded future observation; it does not authorize GUI interaction or a COMSOL launch. The exact Mac Arm64 runtime wheel candidate is `macos-arm64-w25-runtime-candidate-r1.json`.

## Target and entry conditions

Use the existing macOS Arm64 host and CPython 3.12.13 recovery runtime only after the eight hash-pinned wheels are separately approved and installed into an isolated task runtime. The project SSD does not pass the production host-trust verifier because its volume ignores ownership; `/private/tmp` is shared and also fails. The existing task temp directory `/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T` passed `verify_private_path_security` as `posix-owner-mode+filesystem-ownership+no-extended-acl`. It is only an eligible parent candidate: do not create a profile or directory until separately authorized, then create a private task child/file with no-clobber semantics and verify the exact objects again. Current profile path is supplied through `COMSOL_MCP_DESKTOP_SELECTOR_PROFILE`; there is no installed or observed selector profile.

Require macOS 14.0 or newer before any ScreenCaptureKit call. The installed PyPI wheel metadata says ScreenCaptureKit package platform `>=12.3`; the actual `SCScreenshotManager` API is available from macOS 14.0. The adapter gate is 14.0 and `CGPreflightScreenCaptureAccess()` is a non-prompting permission query. Do not call prompting permission APIs or change System Settings. If Accessibility trust or Screen Recording permission is not already granted, stop and report the missing permission without prompting.

## One read-only observation (10-minute ceiling)

1. Inspect only currently running COMSOL candidate processes. Do not launch a process. Accept at most one process and one on-screen main window. Record PID, process start/birth time, UID, executable path and file identity, bundle identifier and version/build from the existing bundle metadata, and session identity. Read those values before and after observation; PID/birth or executable identity drift is a stop condition. If the actual product build cannot be identified, stop.
2. Enumerate on-screen Quartz window metadata only. Pair `kCGWindowNumber` and `kCGWindowOwnerPID`; reject duplicate/ambiguous candidates. Pair the exact ScreenCaptureKit `SCWindow.windowID()` and `owningApplication().processID()` with the same observed values. Do not use title/path alone as model binding.
3. Read at most 4,096 AX nodes from the exact application/window subtree. Record only role, subrole, title, identifier, enabled/focused state, and bounded value where it is a control label. Do not dump arbitrary document text, filesystem paths, or unrelated windows. Preserve raw selector values only in the private profile candidate after privacy review; no guessed selectors or coordinates.
4. If both permission APIs report already-granted and the exact window resolves uniquely, permit at most one in-memory exact-window capture with the ScreenCaptureKit desktop-independent window filter. Respect adapter bounds (12,000,000 pixels and the current capture byte bound). Do not save or publish the image unless an already-authorized local evidence sink is supplied. If the screenshot could expose sensitive model content and no such sink/retention rule exists, skip capture.
5. Do not focus or raise the window, send keyboard/mouse events, open menus, click, type, invoke Java Shell, save, close, run a study, or mutate a model in this observation. A window absent from the on-screen list or any AX/SC mismatch means stop, not search by title.

Budget: one existing process, one exact window, at most one capture, 4,096 AX nodes, 10 minutes. Zero COMSOL API calls, zero solver calls, zero GUI effects, zero permission prompts. Stop immediately on a second process/window, stale identity, ambiguous window mapping, unreadable build, missing permission, profile verification failure, timeout, or any request that would mutate/focus the app.

## Separate disposable-model binding challenge (requires its own approval)

This is not part of the read-only observation. Only after an explicit later scope permits it, create one fresh disposable model in a dedicated task-owned project through the existing registered project/ProjectAuthority and one serialized managed-worker queue. The returned `ModelRef`, worker epoch, endpoint, and revision must prove the model is task-owned and the target Desktop signal maps to that exact same binding; if the Desktop window is a separate process/session or the worker cannot observe the new model, do not challenge it.

If the existing supported model-label operation is proven to share that binding, issue one random one-time label token at the expected revision, verify it through the exact observed AX model signal and managed readback, then restore the original disposable label and verify restoration at the terminal revision. Do not touch a user model. If either setter/readback fails or identity/revision changes, persist `UNKNOWN`, fence further actions and do not retry. No Study/run/solver work. Close only that exact clean task-owned model through its verified safe-close route; otherwise leave it retained for recovery.

Only after the observed profile passes the strict loader and the challenge proves current model binding may any native action selector be registered. Save Copy must wait for the positively observed completion selector, stage into a verified task-private directory, then use the existing registered no-clobber ArtifactStore publish lane. No guessed Save Copy selector, direct caller destination, Save As, or overwrite.

## Acceptance boundary

A future observation may produce software/native selector evidence for this exact host build. It cannot establish numerical/physical acceptance, portable dependency closure, Windows behavior, or production release/install success. The R5 software-only result remains distinct from any future native observation.
