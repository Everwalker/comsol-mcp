# G01 V10 scoped evidence package

This local review package records one accepted, source-isolated G01 metadata observer run and the current D2 focused89 failure. It does not promote the repository worktree R or mark any downstream gate complete.

G01 accepted scope: 112 actual tool names, 112 callable mappings, zero mismatches, and 112 input-schema checks passing. The exact scoped decision is identified by SHA-256 `09b5df89a4122f47b235db1f7013f5fc7dade412d25beb0830c1ad2fe3b349f0`; its source tree fingerprint is `f6b596c14898df2d5ac3af412106d24e3fd7933438a954f401d7cf84b2a1c26e` across 207 members. The actual invocation exited 0 (`49b6f1`), and the independent review is `READY_SCOPED`. Full per-name callable mappings and per-schema SHA-256 digests are in [`G01_V10_ACTUAL_SUMMARY001.json`](G01_V10_ACTUAL_SUMMARY001.json).

The accepted observer covers metadata only. Original-50 compatibility, 28 schema deltas, multipage tool listing, profile/business behavior and actual wire protocol remain open or unrun. JVM/native are outside this acceptance.

The D2 focused89 execution remains `FAIL_PRESERVED`: 89 collected, 84 passed, 5 failed, 0 errors/skips, exit 1, with all 27 frozen launch refs unchanged. Fix the harness envelope/boundary assertions using the actual daemon/store without weakening production checks. Required 740+24 regression and successors remain `NOT_RUN`.

The nine files under `g01-frozen-v10/` are byte-identical copies of the frozen V10 source inputs. Raw actual execution and independent review reports remain in their original local scratch locations; exact size/SHA references and stable path aliases appear in the summary. Some frozen text inputs retain static local path and configuration metadata, including task-defined runtime-directory paths, as part of their byte-identical source. The summary does not reproduce observed runtime environment readbacks; no native COMSOL binaries or model files are included.

The exact proposed file scope and the preserved-history/privacy findings are in [`PUBLICATION_ALLOWLIST001.json`](PUBLICATION_ALLOWLIST001.json). This package is for local review only; no commit or push was performed. The earlier exact-scope public auto-review rejection remains in force pending specific direct human approval.
