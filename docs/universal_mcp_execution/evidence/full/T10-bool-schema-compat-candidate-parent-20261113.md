# T10 bool schema-version candidate and synthetic compatibility checks

Status: **CANDIDATE / NARROW SYNTHETIC CHECKS PASS / NOT INDEPENDENTLY REVIEWED / DO NOT DEPLOY OR USE ON REAL STATE**. Subsequent source inspection found that normalizing a legacy `true` ModelRef may change its JSON-based revision/project-attribution key: on a successful reconnect, `persist()` can write an integer-key entry without the old key's project binding. This conditional risk is not covered by the five tests below. No real legacy state was examined. See [parent adjudication](T10-T05-qwen-proposals-parent-adjudication-20261113.md). Main agent `gpt-6.1-terra` made the two product edits; the schema-only runner was authored on the requested Qwen route, but actual served model and reasoning effort were not independently attested. The separate Qwen design proposal was later received/adjudicated and was not applied as a diff.

## Implementation and compatibility decision

1. `_execution_contract.ModelRef.__post_init__` now rejects a boolean `schema_version` as `MODEL_IDENTITY_MISMATCH` at both direct construction and mapping entry, while leaving the existing non-boolean equality condition unchanged. This specifically removes `True == 1` admission. Other schema representations (including `1.0`) are not decided or independently tested in these checks.
2. In `_runtime_state.restore_runtime_state`, **only** on the existing same-worker/same-epoch/same-server branch, a mapping whose saved ref version is the actual singleton `True` is shallow-copied and normalized to the historical integer `1` before `ModelRef` construction. The source mapping is not changed in place and no rebind/new generation is called. Literal `1` records the historically accepted version; mapping to a future current-version constant would improperly upgrade it if the schema version changes. This is the parent's current compatibility decision, subject to independent review, not proof that arbitrary legacy records are trustworthy.
3. The other restoration branch, revision/counter machinery, ticket/UNKNOWN processing, worker lifecycle, identity-field validation and float policy were not intentionally changed. If the resulting ledger is later saved, its normalized ref may be serialized as integer 1; historical type-exact progress bindings could consequently compare differently and need separate assessment. Actual historical bool-valued data, production reachability, outer error cleanup and real persisted-state behavior are unknown. No real resource or historical record was read/changed in this work.

Final candidate file hashes, verified by `shasum -a 256`:
- `comsol_mcp/_execution_contract.py`: `ad4886193443579fbe7263f3d939037c2ad1fe9cf70753857f7f0e1ddc49ffd5` (baseline `dbb112cb…`).
- `comsol_mcp/_runtime_state.py`: `0c1031458ea4ede768ad095e68bac48cedd730067a5f5e655e9e8b94e9b3621a` (baseline `6d2de604…`).
- Complete two-file tracked diff `_state/t10-pure-schema-guard-20261113/t10-candidate-ad488-0c103-final.diff` SHA `ed85166b18f6bc80d1e8499f7589ff88bb1f01719099d6998486f238d7e37d03`. Scoped `git diff --check` returned 0. No commit was made.

## Paired exact synthetic receipts

| Slice | Baseline | Final candidate | Required changed input(s) |
|---|---|---|---|
| Schema-only direct + mapping + integer default | 2F/1P → | **3P**, zero skipped, exit 0 | contract only |
| Fresh same-worker fake-state restoration | 1F/1P → | **2P**, zero skipped, exit 0 | contract and runtime-state |

A parent script parsed the four named `pytest.log` files, required equal selected/call ID sets, exact pre/post input pins, empty Python forbidden-call lists, zero candidate failed/skipped IDs, and the changed-input sets stated above. Schema test loaded only package and contract; final `_runtime_state.py` was not a dependency, so its later adjustment does not change that test's loaded scope. Restore used the final candidate pair. Assertions in the latter cover a shallow synthetic input mapping remaining unchanged and the recorded identity, generation, revision, counters, fingerprint, ownership and no active operation/tickets. They do **not** prove transactional persistence, migration of actual state, active-ticket or UNKNOWN behavior, worker cleanup, or an OS sandbox.

Receipts:
- `_state/t10-pure-schema-guard-20261113/schema-pure-red-20261113-a-case/pytest.log` and `.../schema-pure-green-20261113-a-case/pytest.log`.
- `_state/t10-pure-schema-guard-20261113/restore-pure-red-20261113-a-case/pytest.log` and `.../restore-pure-green-20261113-b-case/pytest.log`.
- Before the final runtime adjustment an initial two-file candidate (`ad488619…`/`23cfdca5…`) also gave 3P and 2P; its diff is retained as `t10-candidate-ad488-23cfd-initial.diff` SHA `ffa7e9a328db1fbb181dcd9fd32467cd2f688034ab8a20125152d3f2e7e9d127`, and its independent restore receipt ends `green-20261113-a-case/pytest.log`. It normalized to the then-current version constant; the final version changed that to historical 1 for future-version fail-closed behavior. That future-version behavior has not been dynamically tested.

Prior T31/T35 observations bind their then-tested source combinations; do not claim they test this changed contract/runtime combination without checking the relevant dependencies and an allowed verification plan. The installed provisional wheel `434fd502…` predates these edits; prior installation/discovery evidence is not evidence of the new source. The five fenced compatibility outcomes, historical fixture-file restoration, T20 live guard identity comparison and historical resources remain prohibited, not silently covered by these tests. Full-project independent review remains NOT_RUN.
