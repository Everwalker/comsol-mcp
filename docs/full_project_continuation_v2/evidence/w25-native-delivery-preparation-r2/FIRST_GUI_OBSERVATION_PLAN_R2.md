# W25 first native GUI observation plan — R2 successor

Status: **NOT RUN**. This is a bounded candidate for later Native-lane scheduling. It does not launch COMSOL or perform GUI effects. The isolated CPython 3.12.13 task runtime and exact eight-wheel set are now installed and verified under the private task directory recorded in `ARM64_RUNTIME_INSTALL_RECEIPT_R2.json`.

## Task-local preparation

Creating a private child directory and task-owned manifest beneath the already verified, ownership-enforcing `/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T` parent is within the authorized Goal. The task directory and installed `site` are pinned by device/inode and revalidated with the production `verify_private_path_security` helper. No GUI, COMSOL, TCC, permission prompt, user-model access, or OS policy change is included in that authorization. The SSD and `/private/tmp` remain ineligible as trusted selector-profile roots because they do not meet the host ownership verifier.

The runtime is available at:

`/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-w25-arm64-runtime-r2-37bd820ee138424b8946684619d5bb24/site`

It contains only the exact eight pinned candidate wheels. It is not added to the recovered runtime, system Python, live `pyproject.toml`, or `uv.lock`. The observed-selector profile has not been created, and `COMSOL_MCP_DESKTOP_SELECTOR_PROFILE` must not be pointed at a guessed or unobserved profile.

## Entry checks and bounded read-only observation

The future Native-lane task must first establish a concrete COMSOL Desktop executable, bundle identifier, version/build, and exact process identity from the installed launcher/API evidence. No launcher is to be guessed. Root has stated that P3 authorizes preparing a task-owned GUI observation candidate, while Native currently owns the serialized COMSOL lane; wait for that lane handoff before any launch or observation.

Require macOS 14.0 or newer before using `SCScreenshotManager`. The installed `pyobjc-framework-ScreenCaptureKit` package metadata accepting macOS 12.3 does not imply that API is available before macOS 14.0. Query only non-prompting Accessibility and Screen Recording permission state; if permission is absent, return a typed refusal without opening a prompt or changing System Settings.

Once Native schedules this task, use a new task-owned, empty project/model copy and its exact registered project authority. Do not attach to an arbitrary currently open model. The task may identify at most one exact COMSOL process and one visible main window, recording PID, process birth time, UID, executable identity, bundle ID/version, and session before and after observation. PID/birth or executable drift is a stop condition.

Enumerate Quartz window metadata and pair `kCGWindowNumber`/`kCGWindowOwnerPID` with the actual ScreenCaptureKit `SCWindow.windowID()` and `owningApplication().processID()`. Read at most 4,096 AX nodes from the matching application/window subtree. Preserve only bounded selector fields (role, subrole, title, identifier, enabled/focused state, and bounded control-label values); do not collect document text or unrelated windows. Duplicate or ambiguous mapping, missing AX permission, unreadable build, or stale identity means stop.

If both permission checks already pass and the exact window resolves uniquely, at most one in-memory window-only capture may be taken under adapter bounds (12,000,000 pixels and the configured capture byte limit). Keep the capture local to the authorized task evidence directory; record its SHA-256, byte count, pixel dimensions, window ID, PID/birth, and capture API. Do not publish or transmit it. If the image would include sensitive model contents and retention has not been approved, skip capture.

No focus/raise, click, keystroke, menu, Shell command, save, close, model mutation, study, or solver call is part of this observation. Budget: one owned process, one window, 4,096 AX nodes, one optional capture, 10 minutes, zero solver calls, zero COMSOL API calls, and zero permission prompts.

## Model-binding and subsequent actions

If the next authorized step requires proving Desktop-to-Worker model identity, use only the disposable task-owned model and the existing serialized managed-worker queue. The challenge must use a real supported label mutation on the exact `ModelRef` and expected revision, observe the nonce through the proven active-model AX signal, read the label back through the managed Worker, and restore the old label with terminal-revision verification. If the adapter cannot establish that the Desktop window and Worker refer to the same object, do not challenge or mutate anything. Any uncertain mutation/restore becomes durable `UNKNOWN`, fences the model, and is not retried.

Native action selectors are admissible only when observed on the exact supported build and checked by the strict profile loader. Save Copy must wait for a positively observed completion signal, write into a newly created host-trusted task-private staging directory, hash a stable pinned file, and publish with the registered no-clobber ArtifactStore route. No guessed selector, arbitrary caller path, Save As, overwrite, or replay after uncertain completion.

## Status and acceptance boundary

The eight-wheel isolated runtime is **SOFTWARE PREPARATION PASS**. COMSOL Desktop launch, GUI observation, selector profile, capture permission, native control, native COMSOL readback, numerical acceptance, portability, Windows behavior, and release acceptance remain **NOT RUN**. Software fixtures do not count as GUI/native acceptance.
