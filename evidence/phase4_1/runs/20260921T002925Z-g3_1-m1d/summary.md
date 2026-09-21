# Phase 4 (G3) production stdio acceptance run

- generated: 2026-09-21T00:29:32.583827Z
- run directory: `$REPO/evidence/phase4_1/runs/20260921T002925Z-g3_1-m1d`

| case | package | status | assertions | subcases | first finding |
| --- | --- | --- | --- | --- | --- |
| W13_T006_variables | W13 | FAIL | 10 | 7 | q1_probe: expected 2.0, observed None; q2_probe: expected 3.0, observed None |
| W13_T015_units | W13 | FAIL | 11 | 6 | the source unit check reported an implicit thickness/absorptivity factor |
| R04_LIVE | R | PASS | 15 | 9 |  |
| GUARD_T010 | GUARD | PASS | 12 | 5 |  |
| GUARD_T005 | GUARD | PASS | 8 | 4 |  |
| GUARD_T033 | GUARD | PASS | 14 | 6 |  |

cases: PASS 4 / FAIL 2 / BLOCKED 0 / NOT_RUN 0

## Subcase detail

### W13_T006_variables — FAIL
- `PASS` [static] static_variable_ops_availability
- `PASS` [static] static_legacy_variable_route_available
- `PASS` [live] variables_two_in_one_group
- `PASS` [live] varnames_contains_modified_variable
- `PASS` [live] no_bogus_name_expr_variables
- `FAIL` [live] expression_evaluates_after_modification — q1_probe: expected 2.0, observed None; q2_probe: expected 3.0, observed None
- `NOT_RUN` [live] component_and_global_scope — the component variable evaluation did not pass; the global-scope probe was not attempted

### W13_T015_units — FAIL
- `PASS` [static] static_physics_unit_ops_availability
- `PASS` [live] surface_source_W_per_m2
- `PASS` [live] volume_source_W_per_m3
- `PASS` [live] coordinate_unit_m_and_mm
- `PASS` [live] wrong_unit_warns_or_fails
- `FAIL` [live] no_implicit_thickness_or_absorptivity — the source unit check reported an implicit thickness/absorptivity factor

### R04_LIVE — PASS
- `PASS` [static] static_transaction_ops_executable
- `PASS` [protocol] static_unsupported_invariant_rejected_pre_write
- `PASS` [protocol] root_node_read_refusal_is_structured
- `PASS` [live] invariant_violation_not_reported_as_pass
- `PASS` [live] verified_transaction_status_split
- `PASS` [live] stale_revision_rejected
- `PASS` [live] cross_model_transaction_record_rejected
- `PASS` [live] recorded_pass_with_changed_live_property
- `PASS` [live] checkpoint_recovery_after_failed_invariant — a bound checkpoint exists for the failed-invariant transaction and the restore option was returned

### GUARD_T010 — PASS
- `PASS` [static] static_idempotency_contract_published
- `PASS` [live] repeated_key_not_reexecuted
- `PASS` [live] request_hash_conflict_rejected
- `PASS` [live] same_tag_same_type_duplicate_rejected_or_idempotent
- `PASS` [live] same_tag_different_type_rejected

### GUARD_T005 — PASS
- `PASS` [static] static_evaluation_ops_available
- `PASS` [live] user_derived_nodes_preserved
- `PASS` [live] invalid_expression_does_not_delete_nodes
- `PASS` [live] temporary_nodes_cleaned

### GUARD_T033 — PASS
- `PASS` [static] static_evaluation_policy_source_recorded
- `PASS` [static] static_evaluation_policy_documented
- `PASS` [live] pure_read_rejects_or_isolates
- `PASS` [live] ephemeral_mutation_recorded_and_serial
- `PASS` [live] only_own_temporary_nodes_cleaned
- `PASS` [live] evaluation_expression_kinds_routed — some kinds were not routed on this model; their routing conditions are recorded per kind and none was silently treated as a value

## Unknown-job ledger (driver side)

- gate refusals: 0 (none)
- recorded UNKNOWN jobs: 11 (released: 11, unreleased: 0)
- calls that stayed refused after the release path ran: 1

- `f956d38b-4ed3-4308-887c-35682bbb89e8` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `b02c504f-75ba-4ba1-8639-28de04090874` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `7fde825c-31d9-4777-b58f-344c510d0c1b` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `37d02145-8de4-4f2d-b353-de6158224bda` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `b323e9e0-2447-40e7-a3a5-1025995661eb` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `b100f1b0-ed91-4460-a0c6-63d95a1f5a6a` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `639514fa-d658-4bda-8975-53594e471a5e` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `a9a0afca-4f55-4128-a32d-f6ad6048e428` (create_feature): released=True, attempts=1, reconciled_quiescent=True, status=UNKNOWN
- `61c8a39c-2267-4316-b2b2-0c1564c2fcc7` (create_feature): released=True, attempts=1, reconciled_quiescent=True, status=UNKNOWN
- `3e039cde-d6f0-401e-b3e9-769798ebc8d2` (create_feature): released=True, attempts=1, reconciled_quiescent=True, status=UNKNOWN
- `40de6d95-d052-4009-8aef-bf4b3566fe1c` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN

## First causes (C03)

- per class: DEPENDENCY_BLOCKED 0, HARNESS_FAILURE 0, IMPLEMENTATION_GAP 2, EXTERNAL_BLOCKER 0
- classification rule: each case contributes at most one first cause; downstream subcases list DEPENDENCY_BLOCKED against it instead of becoming pseudo-independent defects
- `W13_T006_variables` → **IMPLEMENTATION_GAP** (classified): q1_probe: expected 2.0, observed None; q2_probe: expected 3.0, observed None
- `W13_T015_units` → **IMPLEMENTATION_GAP** (classified): the source unit check reported an implicit thickness/absorptivity factor

## Case isolation and prerequisites (C03)

- `W13_T006_variables`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
  - established: comp1 → SATISFIED (the component 'comp1' was created and read back from the component list)
- `W13_T015_units`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `R04_LIVE`: isolation=own_model (applied=True) — the stale-revision probe must not dirty the shared model; prerequisites=SATISFIED; shared binding restored: mcp7
- `GUARD_T010`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `GUARD_T005`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `GUARD_T033`: isolation=own_model (applied=True) — the evaluation-policy probes need a clean, validly bound model that no earlier refusal has dirtied; prerequisites=SATISFIED; shared binding restored: mcp7

## Slice (this run)

- stage: M1
- live: True
- selected cases: W13_T006_variables, W13_T015_units, R04_LIVE, GUARD_T010, GUARD_T005, GUARD_T033
- note: a slice is a documented run plan, not a weakened acceptance: every selected case still records its own subcases at their declared evidence level, and a stage subset never turns an unselected case into a pass
- M0: R01_LIVE, R03_LIVE, R_READBACK, W13_T048_selection_drift, W13_T016_2D_data, W14_T009_geometry_edit, W14_T034_local_paths, W15_T007_selections, W15_T017_material, W15_T042_license, W16_T018_mesh, W16_T019_chainC_continue, W16_T020_solver, GUARD_T035, GUARD_T038
- M1: W13_T006_variables, W13_T015_units, R04_LIVE, GUARD_T010, GUARD_T005, GUARD_T033
- M2: W16_T019_chainA_steady, W16_T019_chainB_transient
- M3: R01_LIVE, R03_LIVE, R_READBACK, W13_T048_selection_drift, W13_T016_2D_data, W14_T009_geometry_edit, W14_T034_local_paths, W15_T007_selections, W15_T017_material, W15_T042_license, W16_T018_mesh, W16_T019_chainC_continue, W16_T020_solver, GUARD_T035, GUARD_T038

## Execution context (C02)

- logical requests planned (run/case/step/sequence keys): 90
- refusals classified as: stale_expected 0, external_observation 0, unknown_job 0, generation_mismatch 0, ambiguous_envelope 0, explicit_key_reuse 0
- recorded new plans (replans): 2; same-key replays recorded: 2 (identical body verified: 1)
- dispatch stages recorded: dispatched 29, dispatched_without_mutation 3, refused_before_engine 1, unknown 7
- generation replacements observed: 0
- deliberate negative probes (never auto-repaired): 1
- unfinished jobs known to the context: 0
