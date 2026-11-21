# T10 independent Qwen candidate review: parent adjudication

Status: **independent source review completed; current candidate remains HOLD / do not deploy or use on real state.** This is not final T10 or U01–U12 acceptance.

## Scope and verified review provenance

`deleg_61e40582` / `sa-0-e88d9df3` returned the two-file candidate review. Parent inspected `/Users/everwalker/.hermes/cache/delegation/live/deleg_61e40582/task-0.log`: visible calls are read-only `read_file` of the frozen diff, candidate note, two product files, two synthetic tests, four existing pytest logs and three `_managed_backend.py` windows. The windows correspond to the project-risk follow-up already requested of the child. No write, execution, network call, refusal or T20 guard read is visible in that trace. Some call-result previews truncate; the child did not calculate hashes or run tests. Runtime reported Qwen route/model `Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S`; actual served identity and reasoning effort are not independently attested.

The child review was based on the earlier candidate note, tests and four prior red/green logs. It did **not** read the subsequently created project-binding synthetic test or its log: do not describe it as independent review of that newer test. The later test and its exact result remain separately documented in `T10-legacy-project-binding-synthetic-red-parent-20261113.md`.

## Accepted source findings

- `_execution_contract.py:81–124` now rejects `schema_version=True` both on direct `ModelRef` construction and on the mapping decoder with `MODEL_IDENTITY_MISMATCH`; this is a potentially breaking public-input behavior change. The prior three targeted synthetic cases reported in the candidate note cover this narrow defect and int control, not full wire/MCP execution. Float `1.0` remains accepted by this code; policy and downstream consequences are not adjudicated.
- `_runtime_state.py:90–112` copies and normalizes a legacy boolean only for same-worker/server restoration, avoiding source mutation and avoiding the other branch's explicit rebinding. Prior two fake-store cases covered generation/revision/counters and empty UNKNOWN, not nonempty active-ticket/UNKNOWN continuity, real worker behavior or durable recovery.
- In the absence of an integer-key in-memory binding and an integer-key persisted project, `_managed_backend.py:436–448,1113–1129` obtains a distinct integer serialization key and can write or report it without the old boolean-key project ID. The child's source finding agrees with the parent's separately observed synthetic **1P/1F** counterexample; the runtime log did not print the failing metadata contents, so `UNATTRIBUTED` is a source inference, not a separately observed runtime value.
- Python's normal equality makes `True == 1`; it is incorrect to claim every ordinary dataclass/dict comparison fails solely because the types differ. That observation does not settle type-sensitive consumers, serialized-key lookups, real persistent history, authorization, or other equality mechanisms.

## Corrections and remaining risks

The child called the ordinary no-new-key outcome “UNATTRIBUTED, not misattributed.” **Do not generalize that safety claim.** `persist()` selects the current integer-key in-memory binding or falls back to that integer key's previous `project_id`. If synthetic old-boolean and new-integer records instead contain *different* project IDs, source analysis does not show a conflict check before taking the new-key value. Whether such conflicting records are reachable or exist in a real store is unknown; no real data has been inspected. After this review, a separate fake-store conflict test reproduced selection of the different synthetic project ID; see `T10-project-binding-conflict-synthetic-red-parent-20261113.md`. Both zero-binding and conflicting-binding conditions require explicit handling in any safe remedy.

Likewise, the child described drift as “only” a serialization effect. That overstates the review: exact-type consumers, public admission and other unexamined state transitions can distinguish types. No prior fenced request-hash/non-identity, bool-generation, rebind or T20 live-guard outcome may be retried or reconstructed in evaluating these risks.

No actual historical bool or float records, project attribution loss, conflict or wrong-project authorization have been observed. Migration, aliasing, fail-closed alternatives, atomic persistence and Worker-start/cleanup ownership require design and appropriately scoped validation; do not automatically access or modify any old model, Worker, SSH, UNKNOWN or historical state. An independent read-only design task was already dispatched to consider these conditions. Existing T10 candidate and full-goal acceptance remain unchanged.
