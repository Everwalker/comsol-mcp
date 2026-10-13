# Isolated ARM64 GUI dependency runtime — R2 execution record

Status: **SOFTWARE PREPARATION PASS**. The only acquired artifacts were the eight exact wheels in the frozen R1/R2 candidate (7,388,559 bytes total). No sdist, Rust component, crate, CPython source archive, OpenSSL source archive, unrelated dependency, or live lockfile was acquired.

The task-local destination is `/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-w25-arm64-runtime-r2-37bd820ee138424b8946684619d5bb24`. Its parent, task directory, wheelhouse and installed site were checked through the production host trust verifier. The current `site` has identity `(device=16777231, inode=14842239)` and remains separate from the recovered CPython 3.12.13 runtime and system Python.

The exact original candidate remains at `../w25-native-delivery-preparation-r1/macos-arm64-w25-runtime-candidate-r1.json`; its bytes were not edited. R2 copies the hash-locked pip requirements to `requirements-arm64-R2.txt` so the execution input is preserved with this receipt.

## Executed commands and effects

Download command (the exact manifest is checked by the downloader before it opens the eight approved HTTPS URLs):

```sh
/private/tmp/comsol-v2-runtime-20261011-new/comsol-mcp-recovery-runtime-20261004-001/python312/bin/python \
  docs/full_project_continuation_v2/evidence/w25-native-delivery-preparation-r2/fetch_pinned_arm64_wheels_r2.py \
  --execute-download
```

The downloader validated the ownership-enforcing parent and task directory before and after transfer; required exact host, status, size and SHA-256; streamed at most each pinned size; wrote through exclusive partial files; published by no-clobber link; and read back each published wheel through the pinned destination directory. Result: eight `DOWNLOADED_VERIFIED_NO_CLOBBER` files, 7,388,559 bytes. The raw network command stdout was not retained; `ARM64_RUNTIME_INSTALL_RECEIPT_R2.json` re-verifies each wheel from disk and records this limitation.

The offline, hash-locked install invocation was:

```sh
/private/tmp/comsol-v2-runtime-20261011-new/comsol-mcp-recovery-runtime-20261004-001/python312/bin/python \
  -m pip install --no-index --no-deps --only-binary=:all: --require-hashes \
  --find-links /private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-w25-arm64-runtime-r2-37bd820ee138424b8946684619d5bb24/wheelhouse \
  --target /private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-w25-arm64-runtime-r2-37bd820ee138424b8946684619d5bb24/install.staging \
  -r /private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-w25-arm64-runtime-r2-37bd820ee138424b8946684619d5bb24/requirements-arm64.txt
```

After successful pip exit, staging was activated with the existing `_state_backup._rename_directory_noreplace` pinned-parent/no-replace directory operation. The final `site` retained the staging directory identity. No file was overwritten, and the recovered runtime's `site-packages` was not modified. Post-install imports of `psutil`, `objc`, `AppKit`, `ApplicationServices`, `Quartz`, `CoreText`, `CoreMedia`, and `ScreenCaptureKit` came from the isolated `site`; the `SCScreenshotManager` binding exists. No ScreenCaptureKit API, GUI, Accessibility/Screen Recording permission, COMSOL process, or solver was invoked.

## Rollback

No rollback was needed. If this isolated runtime must be retired, first reverify the trusted parent and exact task-root identity; move only the exact `site` or whole task directory through the existing pinned-parent no-replace rename into a fresh task-local quarantine name; verify the moved identity; retain it for inspection. Do not recursively delete by pathname, and do not touch the recovered runtime, system Python, project SSD, user packages, or any other task directory.

## Scope limits

This is a disposable local development dependency runtime, not a release/install acceptance result. It proves package bytes, a successful task-local install, and software imports only. It does not prove a real ScreenCaptureKit capture, macOS permission state, selector profile, GUI behavior, Intel wheel availability, or Windows behavior.
