# T10 legacy project binding: synthetic counterexample to current candidate

Status: **1 failed / 1 passed; candidate remains HOLD.** This newly observed result strengthens, but does not replace, the static project-key concern in `T10-T05-qwen-proposals-parent-adjudication-20261113.md`. It does not establish that any actual legacy record exists or that any real project's state was changed.

## Scope and test mechanism

Parent authored `tests/test_t10_legacy_project_binding_pure_20261113.py`, SHA `022e669ec9617f70df75534a84a82f599eccc041f828ac531bc8d4a7bbc614dd`. It builds fresh same-worker Python mappings and a fake memory metadata store, pre-seeding a synthetic project-bound `revisions` record under the ref's original JSON key. It calls the real `restore_runtime_state` and `save_runtime_state`. To avoid importing the entire backend and unrelated modules, the test parses the full `_managed_backend.py` source and compiles **only** its `_model_project_key` and `persist` method definitions into an isolated class, supplying `json` and the real save function; it does not execute the real constructor, full module, daemon, Worker or transport. That substitution is an important limit: the result is evidence about these source methods wired to a fake store, not a real backend lifecycle or principal check.

No historical fixture files are restored, no bool-generation/rebind/nonidentity/request-hash refusal outcome is recreated, no T20 guard/source-identity comparison is performed, and no old model, Worker, SSH target, UNKNOWN or actual database is touched by this test. Python scope hooks are not an OS sandbox.

Parent-authored wrapper `_state/t10-pure-schema-guard-20261113/run_project_binding_pure.py`, SHA `2c2a19a9770f372a91849f466d2799fe875d02a41b1c646e0a5010ea6b62e61e`, reuses inspected pure guard `run_legacy_bool_restore_pure.py` (`debea223…`) with exactly two selected parameter IDs. It pins the wrapper/test/package/current contract and runtime, checks Python-level module origins and forbids selected socket/process/signal/SQLite calls. Wrapper additionally hashes the exact backend source `39d367acbf13cea1de7592e213497a1ade65298452a8de32d801b2ad34073e09` and the reused guard before and after; successful `PROJECT_BINDING_EXTRA_PINS_OK` was printed in the actual launcher output. It does not compare or read the T20 guard.

## Actual observation

On candidate contract `ad488619…` and runtime `0c103145…`, the real execution produced **1P/1F/0 skipped, exit 1**:

- Integer-1 existing project-binding control passed through the same two extracted methods.
- Legacy-true case passed the preceding assertions: fake old entry had the synthetic project ID, restored generation/revision matched, and the new integer-ref key differed from the old boolean-ref key. The extracted `persist()` returned and created a new-key record. The first failure was `published.get('project_id') == PROJECT`: the newly published fake-store value did **not equal** the old synthetic project ID. The following `attribution` assertion was not reached. Source analysis indicates the no-new-binding path constructs `UNATTRIBUTED`, but the assertion log itself does not print the exact published metadata value; do not describe that value as a separate runtime observation.

Detailed persistent pytest log `_state/t10-pure-schema-guard-20261113/restore-pure-binding-red-a-case/pytest.log` records the exact IDs, expected input pre/post pins, one pass and one failure, loaded project module set, and empty `forbidden_calls`. The wrapper printed matching additional guard/backend source pins after the test. The output reports a selected slice, not a whole-suite run. The five earlier T10 schema/restore cases do not cover this property and must not be used to dismiss it.

## Disposition

Do not deploy or use the current candidate on real state. No fix is made by this observation; choose and verify a safe way to retain trusted project binding and persistence semantics without touching real/historical state or bypassing previously refused outcomes. A fail-closed alternative must also address the reconnect path's Worker-start/cleanup ordering; simply rebind/resetting counters is not acceptable. The source-only Qwen reviewer was previously steered to inspect the issue; a later additional steer returned `No live subagent` because it had apparently completed, so that message was not delivered. Do not claim the reviewer has examined this newer test unless its delivered trace/result establishes it. U01–U12 and final independent review remain incomplete.
