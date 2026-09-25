# Independent native review round 1

Candidate wheel: `ea46fcd71311307a0492e38e896aee2dc19eae8065ddd08ac4d5b2da5a90fe63`. Host actual independent reviewer `/root/reviewer`; verified Windows hostname EVERWALKER, 192.168.100.2. Exact executed scripts and hashes are retained in each run directory, with local copies in `evidence/w21_closure/reviewer64_01` and `reviewer64_reopen01`.

The first script-copy approval was rejected because host/destination authorization was unverified. Reviewer then made read-only hostname and frozen-wheel hash checks, read the host identity receipt, and retried the same scoped SCP transfer with those additional facts. Review approved; no alternate transport or bypass was used.

## Native 6.4 results

`reviewer64_01` used new reviewer-owned server/model/control directory and independent parameters k=[381,399], rhoCp=3.5e6. Normal cases had maximum errors .0014523374024975055/.0014989307354653647 K (target .03 K). New unregistered ref, caller-values replacement, and stale first-case ref were rejected through public MCP. Repeat returned two cache hits and zero computations. Wrong stage mapping was rejected; actual stage initial field met .001 K and continuation maximum error .0017064145191625357 K met .03 K. Two-candidate real optimization produced verified feasible best. Saved model SHA256 `ee71658d66a92efc19c78de7742eb24c1ef3b16f1fb33b1d6814b1e79c96b9d6`.

### R1-BUDGET-REVISION (A1, CHANGES_REQUIRED)

- Requirement: W21-2 bounded computation and correct continuation/idempotent operation behavior.
- Reproduction: public sweep with two candidates and max_cases=1 completes one actual case and leaves second NOT_RUN. Aggregate status PARTIAL is treated as OPERATION_FAILED by managed domain handling. Next public sweep after changing the declared input file is refused with REVISION_CONFLICT/external model change requires reconciliation.
- Evidence: final two request/response pairs in reviewer64_01/transcript.jsonl; first returns evaluated=1, failures=0, actual COMPLETED+NOT_RUN; second lacks budget because dispatch refused. Runner terminates FAILED (KeyError budget is the test assertion surfacing the actual upstream refusal).
- Impact: ordinary budget exhaustion poisons the managed revision, preventing valid subsequent calls.
- Minimal close: bounded orchestration success uses COMPLETE plus explicit completion_status BUDGET_EXHAUSTED, while preserving every NOT_RUN and exact budget/count. Actual failed solve still fails. Retest two successive input changes with max_cases=1 and failed-candidate exclusion; no need to rerun unaffected scientific cases solely for this envelope fix.

`reviewer64_reopen01` independently launched a fresh owned server/Worker on the same candidate. Same saved SHA loaded without recomputing, both datasets returned exactly equal stored values and new ObservationRefs, numerical validation passed and physical remained UNVERIFIED. Missing-file and missing-stored-solution negatives were rejected. Exit 0 / REVIEW_PROBES_PASS. This scoped reopen evidence remains valid despite the separate budget failure.

No overall W21 approval: budget defect must close and independent 6.3 run remains outstanding. No W22.
