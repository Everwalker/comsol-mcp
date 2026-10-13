# W25 adapter r3 source freeze

Status: **240 guarded software tests passed; native UI and COMSOL remain NOT_RUN.**

The exact copied source/test/doc/candidate file hashes are in `SOURCE_FREEZE_R3.json`; the complete current-vs-preimage patch is `W25_SOURCE_DIFF.patch`. The source snapshot has 38 entries, and 20 original preimage files were copied and reverified.

The final raw JUnit records 240 passed, 0 failures, 0 errors, 0 skips. Strict network guard records seven active child-entrypoint events and zero blocked attempts. Runtime: Python 3.12.13 / pytest 9.1.1. Test evidence is copied under `evidence/`.

Profile loading now uses the host provider's exact Windows ACL semantics and binds the pinned read to provider identity; Windows fd identity uses the same native Win32 `GetFileInformationByHandle` representation as the provider. POSIX profile trust is rechecked before and after the read. Replacement/ACL-change controls pass in software.

`_control_daemon.py` current SHA-256 is `83fa07fec0286d9d0d004777eab03979295324ef887a7d4ef1368e0fefe9b3db` (810368 bytes), compared with original W25 preimage SHA-256 `10fa0f3f8fdd34c0fb31c9be5b3866349a60b9c001a1f339ca8920724858b6eb`. It contains prior Desktop integration and concurrent non-W25 additions; this r3 turn did not edit that file. The exact current file is included in the source snapshot. The previously reviewed W25 method-level patch remains separately preserved at `docs/full_project_continuation_v2/evidence/w25_integration/W25_INTEGRATED_SOURCE.patch`.

The ScreenCaptureKit dependency successor `desktop-builder-candidate-r3-w25` passes offline validation with 63 unique artifacts/143,627,243 bytes. No package was downloaded or installed and `pyproject.toml`, `uv.lock`, and project version are unchanged. Native selector profiles remain unobserved; all UI/native acceptance remains open.
