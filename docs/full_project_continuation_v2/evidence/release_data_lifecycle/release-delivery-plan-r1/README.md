# CPython 3.12 release artifact plan (plan only)

This packet is an exact, hash-pinned acquisition plan generated from `uv.lock` and `release-cache-inventory-r2.json`. It did not download, install, or build anything. It is not an approved network scope or a completed wheelhouse. Future downloads must be restricted to the per-target URLs in `download-plan.json`, use separate fresh task cache directories, and verify expected SHA256 and size before any offline installation.

The three requirement files cover only the current locked project runtime plus `dev` closure. Current source declares an additional Darwin `desktop` extra with `psutil` and three PyObjC frameworks, but uv.lock's project metadata advertises only `dev` and has no package records for those desktop dependencies. Do not delete that extra or describe these files as a full macOS desktop closure. A reviewed lock synchronization and exact hash plan is still required.

macOS Intel has only the `cryptography==50.0.1` locked sdist. The requirements row pins the sdist input only. A CPython 3.12 abi3 `macosx_12_0_x86_64` derived wheel, native audit, OpenSSL 4.0.2 source/license, and provenance-bound output hash are still required through `tools/full_project_release.py`; no alternative wheel/tag/version is accepted.

The local uv cache contains target-compatible extracted package trees for some rows, not verified original wheel files. The plan does not repackage those trees. Build requirements `setuptools>=68` and `wheel` are declared by pyproject but omitted from uv.lock. Existing cache has extracted versions (see JSON), whereas the observed installed host is CPython 3.14.7 arm64 with no installed setuptools/build and wheel 0.47.0. No isolated CP312 builder artifact is presently hash-bound.

Application version remains 0.1.9 in current source and lock. 0.1.10 is only the proposed next release version; version changes and release builds remain deferred until source stabilization and explicit download/build scope.
