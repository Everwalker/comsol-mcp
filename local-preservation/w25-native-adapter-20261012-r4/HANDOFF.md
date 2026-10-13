# W25 native adapter r4 source freeze

Status: **240 guarded software tests passed; native UI and COMSOL remain NOT_RUN.**

`SOURCE_FREEZE_R4.json` records 47 exact source/test/documentation/candidate files and 20 copied original preimage files. `W25_SOURCE_DIFF.patch` binds the complete diff to the preserved preimage.

The final raw JUnit records 240 passed, 0 failures, 0 errors, 0 skips. Strict network guard records seven active child-entrypoint events and zero blocked attempts. Runtime: Python 3.12.13 / pytest 9.1.1.

The profile loader uses the host provider's exact Windows ACL contract, matches the pinned CRT fd by the provider's native Win32 identity representation, and checks identity/security before and after reading. Selector profiles remain unobserved; UIA/AX/ScreenCaptureKit and COMSOL native acceptance remain `NOT_RUN`.

Current `_control_daemon.py` SHA-256 is `83fa07fec0286d9d0d004777eab03979295324ef887a7d4ef1368e0fefe9b3db` (810368 bytes); original W25 preimage is `10fa0f3f8fdd34c0fb31c9be5b3866349a60b9c001a1f339ca8920724858b6eb`. This file contains prior W25 integration and concurrent non-W25 shared changes; r3/r4 adapter turns did not edit it. The previously reviewed method-level W25 patch remains separately preserved at `docs/full_project_continuation_v2/evidence/w25_integration/W25_INTEGRATED_SOURCE.patch`.

Candidate `desktop-builder-candidate-r4-w25` validates offline with 63 unique artifacts/143,627,243 bytes. No package download, installation or build occurred; `pyproject.toml`, `uv.lock`, and project version remain unchanged.
