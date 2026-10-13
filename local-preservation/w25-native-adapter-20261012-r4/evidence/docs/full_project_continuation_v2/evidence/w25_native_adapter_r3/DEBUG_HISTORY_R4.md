# W25 adapter debug history r4

This record retains the material intermediate outcomes without upgrading them to final acceptance.

- Initial selector-profile focused run: 3 passed, 2 failed. One implementation import was missing (`sys` in `_desktop_selector_profile.py`); one test had a function-local `os` import that shadowed the module import. Both were corrected immediately. The failed traceback is preserved in the task transcript; that run had no JUnit artifact.
- Corrected focused run after those fixes: 5 passed.
- The first full guarded run before the Windows descriptor-identity regression was added: 239 passed. It was superseded by the final guarded 240-test run.
- Root identified the Windows mismatch between CPython `fstat` identity widths and the host provider's Win32 file identity. The implementation now queries `_API.info(msvcrt.get_osfhandle(fd))`, compares that representation with provider `expected_identity`, and does not close the CRT-owned handle. A software ABI-width control was added.
- Final focused run after the Windows identity test: 6 passed. Final affected suite: 240 passed, 0 failed, 0 errors, 0 skipped; hashes are in `BOUND_SUITE_RECEIPT.json`.
- Candidate r2 validation initially detected that the W25 macOS source binding was stale after adapter edits. The candidate r2 bytes were retained. Candidate r3 corrected the source binding and passed offline validation; the later final test receipt changed when the Windows identity case was added, so candidate r4 supersedes the stale receipt binding. Candidate r4 validates offline and retains identical download rows.

All software tests use local fixtures. Windows kernel, macOS UI, COMSOL execution, package download, package installation, and package build were not performed.
