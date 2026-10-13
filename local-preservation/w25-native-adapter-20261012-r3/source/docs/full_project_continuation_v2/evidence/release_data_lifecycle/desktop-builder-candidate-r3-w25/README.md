# W25 ScreenCaptureKit acquisition successor candidate r3

This plan-only successor preserves the immutable 63-artifact acquisition set from candidate r2 and updates source bindings to the final W25 ScreenCaptureKit adapter and selector-profile security implementation. It records the exact Windows host-provider ownership/DACL semantics and reads a Windows CRT file descriptor through the provider's native identity API; it does not claim Windows kernel validation.

The two added PyObjC wheels remain the CPython 3.12 universal2 ScreenCaptureKit and CoreMedia artifacts from r2. Their URLs, SHA-256 values, exact byte counts, selected dependency closure, and official metadata evidence are unchanged. The live `pyproject.toml`, `uv.lock`, and project version remain unchanged.

`candidate_plan_tool.py --validate` is an offline structural and source-binding check; `--dry-run` only prints a future retrieval plan. No package artifact was downloaded, installed, or built. Intel Rust/Cargo closure and CPython 3.12 target builders remain unresolved.

The r2 candidate remains immutable and records the prior adapter binding. This r3 successor changes no acquisition rows; it adds exact source bindings for `_desktop_macos.py`, `_desktop_selector_profile.py`, `_host_scoped_provider.py`, `_windows_parameter_publish.py`, and the guarded W25 regression receipt.
