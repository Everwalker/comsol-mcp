# Current handoff — r393 local checkpoint candidate

## Baseline and candidate history

Git commit `74c284fb6bc83f81c87b8645d300038d7eb88524`, tree `9cf520f5f0d30d8da5204bd8911c2bc0bc48dc68`; Main r389 publication remains accepted. r392 and its freeze/raw/self-check artifacts are preserved unchanged as an intermediate local candidate; r392 did not receive Main final acceptance. The exact r392 canonical files and compact evidence bytes are snapshotted in `_state/universal-main-review/20261028-checkpoint-r393/prior-r392-candidate/`.

## Windows normal Stage1

Main accepted the exact-once 29-file / 46,524,935-byte transfer and actual READY/identity readback. The subsequent Main closeout is pinned by SHA-256 `28ad1a2ca03e8e432c588f3e538b9f5c4252e84e08baefc2cfeeca53558ea268` (2,987 bytes); the closeout gate return is exact exit 0 and pinned by `3ba8f1c6373733cd76ef3fbdf198582a78c3f78cad1d53602de5c94531fb98cf`. It records one original-handle observation: exact integer exit 1, empty output, then SPENT. Never poll the original handle again. Main's cross-namespace lookup failure is only a Main handle lookup failure, not Nativeunknown.

The original executor confirms the expired publisher/GO task was not executed. Saved stdout is only the 1,042-byte READY frame. Saved stderr records CreatorRefusal for an expired GO dispatch deadline at the GO-wait deadline guard, before continue/apply. This owner did not reach continue/apply and did not create its material root. Do not infer that the current filesystem path is absent, or that every native resource is closed. JDK-at-use, materialization, pip and native acceptance remain NOT_RUN.

## Next bounded work

The old task and namespace are closed; no replay, retry, adoption, cleanup, or activation. The next candidate work is a new-namespace DATA rebind by Luna Socrates in a new NM, `fresh-namespace-data-rebind-r1`, after Main review. Terra N is preparing the local binder only; there is no new lease and no remote action. Five immutable fences and frozen scope readbacks are unchanged; `completed_groups=[]`, heldouts NOT_SELECTED, final Reviewer NOT_STARTED, and MASTER_GOAL remains active/incomplete. Compact evidence: `evidence/windows-normal-stage1-ready-20261028.json`. This r393 package is local pending Main review; no commit or push.
