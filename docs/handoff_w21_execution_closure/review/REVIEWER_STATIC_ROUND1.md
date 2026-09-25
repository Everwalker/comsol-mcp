# Independent static review, round 1

Reviewer: actual Host subcontext `/root/reviewer`. Scope: current production `_observation_store.py`, `_w21_execution.py`, `_g3_w21.py`, `_managed_backend.py`, directly affected W20 validation. This is pre-candidate review; no final approval. Production source was not edited. Control probe: `reviewer_provenance_probe.py`, result: `reviewer_provenance_probe_result.json`; command `PYTHONPATH=. /private/tmp/w21-dev-venv/bin/python docs/handoff_w21_execution_closure/review/reviewer_provenance_probe.py`. Probe succeeded after resolving the temporary path to avoid `/var` symlink refusal. This is control evidence, not Windows/native retest.

## CHANGES_REQUIRED

### R1-PRODUCER (A1)

- Original requirement: W20 T06 explicitly requires rejecting a nonexistent producer.
- Fact: `resolve_observation` validates artifact/hash/model/revision but never queries the operation record for its stored producer. The independent probe registers an observation with `reviewer-no-such-producer`, verifies `get_operation(...) is None`, and obtains `numerical_verification_status=PASS`, `model_validated=true`. Existing developer tests likewise use nonexistent producer strings.
- Impact: persisted artifact metadata alone is treated as successful backend provenance even if no producer exists. This does not claim that caller JSON alone can forge a database entry; it demonstrates the expressly required missing-producer guard is absent.
- Minimal close: verify producer exists and belongs to the appropriate model/runtime operation and valid execution state. Nested W21 validation needs to permit the actual currently executing managed producer, while independent later validation must reject missing/failed producers. Add the original missing-producer negative and retain native normal flow.

### R1-SOLUTION-REVISION (A1/A2)

- Original requirement/risk path: W20 T06 rejects cross revision/solution; W21 cases preserve correct raw solution correspondence. Public `study.sweep_manage` sequentially reuses one study/solution, with all case observations minted under the single outer operation ID.
- Fact: `_managed_backend.py` post-success loop rewrites **every** observation belonging to that operation to the final model revision. Earlier cases' `sol1` has since been overwritten by later cases. `resolve_observation` ignores `sample_revision` and has no native solution-generation validation. The control probe reproduces this exact metadata rewrite: an old 303 K sample is accepted as `MODEL_VALIDATED` under final revision 8. In production the corresponding trigger is a two-case sweep with distinct temperatures followed by `validate.solution(dataset=dset1, observation_ref=first_case_ref)` at the final revision.
- Impact: earlier stored sample can certify the current model/solution after later solve overwrites it. Immutable historical case data remain useful, but they are not the current model solution. Cache's explicitly historical artifact read is a separate use case.
- Minimal close: bind each sample to its actual solution generation/case state and preserve historical status. Only observations that still correspond to the final actual solution may certify current model state; reject earlier overwritten solution refs in later `validate.solution`. Do not invalidate legitimate per-case immediate validation or historical cache data. Add two-case normal+stale-first-ref negative through the public path.

## Nonblocking observations and remaining execution

`physical_validation_status=UNVERIFIED` fixes the previous automatic physical promotion statically. Cache now uses exact float values and durable store; failed/unknown/nonfinite objective exclusion is present. The current review does not claim native budget/cache/stage behavior passed. Those remain for the planned Windows 6.3/6.4 run and independent retests, as do T11/T14 gaps already documented. No additional framework, GUI certification, signature system, or W22 requirement is introduced.

## Concurrent fix follow-up

Main updated source while this review was executing. R1-PRODUCER now checks OperationStore at registration and resolution, allowing only SUCCEEDED or the actual current RUNNING producer. R1-SOLUTION-REVISION now captures and compares actual solution study/computation date/version/time values; this addresses the static path, pending required two-case public native negative. Parent reports COMSOL computation date is milliseconds; this is not independently retested here.

R1-CACHE-PERSIST (A1, W21-2): identified ResultCache.store envelope lacked sha256, while OperationStore.persist_artifact requires it; execute_case would consequently mark successful solve as FAILED on cache write. Main concurrently reported native smoke08 confirmed it and added sha256. Reviewer direct call after that edit succeeded (`reviewer_cache_probe_result.json`, literal "unexpected_success" means the old failure assertion no longer reproduced on the changed source). Static persistence defect is resolved; native end-to-end rerun remains required.
