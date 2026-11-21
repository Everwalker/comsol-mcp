# T10 conflicting synthetic project bindings: safety RED

Status: **1 passed / 1 failed / 0 skipped; candidate remains HOLD.** This is a distinct synthetic conflict control, not evidence that conflicting records exist in a real database, that a real project was mislabeled, or that any principal was authorized incorrectly.

## Scope and candidate

Parent authored `tests/test_t10_legacy_project_conflict_pure_20261113.py` (SHA-256 `dc09e641eccc4d0f40317b0d243d081709a824157c4a6865718c88ffea42b3e6`) and `_state/t10-pure-schema-guard-20261113/run_project_conflict_pure.py` (SHA-256 `ef073f6654258bb2b6ba8a20e9eb465b14c17ef80e33b5858d16e12f81105e2f`). This new scope contains two exact tests: a regular integer identity/project positive control and a legacy-true versus integer-key conflicting-project case. The latter asks that ambiguous project bindings fail closed without a retained metadata change; choosing either project silently is not accepted as conflict resolution.

The test creates a fresh Python memory store and mappings, calls the current actual `restore_runtime_state`/`save_runtime_state` functions, and parses the full `_managed_backend.py` but compiles only `_model_project_key` and `persist` into a minimal class. It does **not** import or construct the full backend, run the control daemon, exercise a real principal, use SQLite, inspect historical state or prove an actual conflict is reachable. It does not run the fenced bool-generation, nonidentity/request-hash, rebind or T20 live-guard outcomes. No historical fixture, model, Worker, SSH or UNKNOWN was accessed.

The runner reused the inspected pure-restore guard `debea223…`, selected and witnessed the two exact IDs, pinned the backend source `39d367ac…` and its own/test/contract/runtime inputs before and after, and saw no hits in its Python process/network/signal/SQLite hooks. Python hooks and fresh scratch are not OS isolation. Inputs were candidate contract `ad488619…`, runtime `0c103145…`; no product code changed during this check.

## Actual result

Run `binding-conflict-a` exited **1**. The ordinary integer-key project positive passed. The conflict case first established that the same synthetic ref serializes to distinct boolean and integer keys and seeded `synthetic-project-original` and `synthetic-project-conflicting` respectively. Restoration and extracted persistence returned instead of rejecting; the failing pytest output recorded:

```text
conflicting bindings were not refused; published_project='synthetic-project-conflicting';
write_tables=['sessions', 'runtimes', 'revisions']
```

Thus, under this intentionally conflicting fake state, the extracted current persistence path adopted the pre-existing different integer-key ID and performed three fake metadata writes. The two metadata entries are synthetic; this result is not proof of a real project authorization bypass, wrong-project access, or on-disk partial commit. The earlier single-bound fake case is separately recorded in `T10-legacy-project-binding-synthetic-red-parent-20261113.md`.

Exact log: `_state/t10-pure-schema-guard-20261113/restore-pure-binding-conflict-a-case/pytest.log`; it contains selected IDs, failed assertion, 1P/1F, no skipped cases, and input/import/hook checks. The wrapper separately printed successful base/backend pre/post pins. Selected `pytest` and interpreter hashes are in that log.

## Test limits and disposition

The positive control covers a normal integer record but not the stronger same-legacy-ref/same-project paired control. The test accepts either `ExecutionContractError` or `RuntimeStateError` from the combined restore/persist block and checks final fake state byte-semantic JSON equality; a future false rejection from an unrelated cause could satisfy it. Therefore this is a useful red counterexample, **not a complete future GREEN acceptance gate**. Before accepting a fix, add a genuinely discriminating same-legacy-ref/same-project positive and assert the selected conflict error/branch and durable transaction behavior using an appropriately authorized fresh store. Retain the original test and log; do not recreate previously refused outcomes.

T10 stays **HOLD / do not deploy or use on real state**. The separate Qwen-route design task is intended to address trusted authority, matching/missing/conflicting keys, atomicity and Worker lifecycle; it has not yet been adjudicated. Final U01–U12 independent review is not complete.
