# W25 ScreenCaptureKit acquisition successor candidate

This is a plan-only successor to the immutable Desktop/builder candidate r1. It adds the exact CPython 3.12 universal2 PyObjC ScreenCaptureKit and CoreMedia wheels required by the W25 ScreenCaptureKit window-capture adapter. It reuses the previously reviewed Core, Cocoa, Quartz, ApplicationServices, CoreText and psutil artifacts. It also applies the already reviewed official psutil x86_64 URL correction and replaces the OpenSSL alias with the exact canonical `browser_download_url` and byte count from the official GitHub release API.

The candidate totals 63 unique URL/SHA pairs. The two added PyObjC wheels total 41,019 bytes per macOS target and 41,019 unique bytes because their universal2 artifacts are shared by ARM64 and x86_64. The ScreenCaptureKit package metadata declares macOS 12.3+; W25 runtime code refuses capture on older or unreadable macOS versions. This does not raise the declared target floor for other Desktop operations.

The official metadata projection is saved in `metadata-used-fields-r2-w25.json`; the OpenSSL raw GitHub release JSON is saved separately. PyPI artifact files were not downloaded. `candidate_plan_tool.py --validate` is an offline structural/source-binding check, and `--dry-run` prints but does not execute a future retrieval command.

No source package was fetched, no package was installed or built, and `pyproject.toml`, `uv.lock`, and project version were not edited. The live Desktop extra still lacks `pyobjc-framework-ScreenCaptureKit`, and the base/dev + Desktop + builder candidate is not full Intel offline closure: Rust/Cargo registry source inputs and builders remain unresolved. The W25 code lazily imports ScreenCaptureKit and reports a typed unavailable result if the optional framework is absent.

The candidate intentionally preserves r1's recorded failures: the r1 psutil x86_64 URL contains a duplicated path segment, and r1's OpenSSL candidate used the openssl.org alias. This successor's acquisition rows use the verified URLs; the original candidate bytes are unchanged.
