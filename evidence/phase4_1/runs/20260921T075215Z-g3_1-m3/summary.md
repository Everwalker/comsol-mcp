# Phase 4 (G3) production stdio acceptance run

- generated: 2026-09-21T07:52:35.191744Z
- run directory: `$REPO/evidence/phase4_1/runs/20260921T075215Z-g3_1-m3`

| case | package | status | assertions | subcases | first finding |
| --- | --- | --- | --- | --- | --- |
| R01_LIVE | R | NOT_RUN | 15 | 8 |  |
| R03_LIVE | R | BLOCKED | 18 | 10 | the keyed entry write could not be verified: the engine state was unknown (node.property_entry_set failed with ENGINE_CALL_FAILED (COMSOL getEntryKeys call fail |
| R_READBACK | R | BLOCKED | 5 | 4 | roll-up of R03_LIVE: BLOCKED |
| W13_T048_selection_drift | W13 | FAIL | 12 | 6 | the selection could not be revalidated after the geometry change |
| W13_T016_2D_data | W13 | BLOCKED | 11 | 6 | function.create returned function.create failed with PROPERTY_TYPE_MISMATCH (fununit must be an array for this property); the callback did not declare a pre-dis |
| W14_T009_geometry_edit | W14 | FAIL | 9 | 7 | the work-plane fixture could not be created: geometry.workplane_create returned OPERATION_FAILED: the operation reported a failed domain status |
| W14_T034_local_paths | W14 | FAIL | 11 | 5 | the missing-dependency fixture needs operations this build does not offer: model.remove |
| W15_T007_selections | W15 | BLOCKED | 9 | 6 | the physics/feature/selection fixture could not be built: definition.component_manage returned an invalid envelope: geometry.build returned EXECUTION_STATE_UNKN |
| W15_T017_material | W15 | BLOCKED | 12 | 5 | the material property readback was refused: the engine state was unknown (node.property_get failed with API_UNSUPPORTED (authoritative value metadata is unavail |
| W15_T042_license | W15 | FAIL | 10 | 5 | runtime.license_inspect returned INVALID_REQUEST |
| W16_T018_mesh | W16 | BLOCKED | 8 | 7 | geometry.build returned EXECUTION_STATE_UNKNOWN: the operation left an unresolved engine state |
| W16_T019_chainC_continue | W16 | BLOCKED | 6 | 7 |  |
| W16_T020_solver | W16 | BLOCKED | 19 | 10 | the bound model exposes no solver sequence for the requested study, so no solver tree exists to read (study.solver_generate was attempted first; see solver_gene |
| GUARD_T035 | GUARD | PASS | 12 | 7 |  |
| GUARD_T038 | GUARD | PASS | 12 | 7 |  |

cases: PASS 2 / FAIL 4 / BLOCKED 8 / NOT_RUN 1

## Subcase detail

### R01_LIVE — NOT_RUN
- `PASS` [static] static_expression_readback_ops_executable
- `PASS` [protocol] static_malformed_typed_value_rejected_pre_engine
- `PASS` [live] expression_string_same_text_verified
- `PASS` [live] expression_array_or_matrix_verified
- `NOT_RUN` [live] unit_expression_text_preserved — no property exposed authoritative unit metadata; unit-bearing text was not exercised
- `PASS` [live] incompatible_kind_or_unit_rejected_before_write
- `PASS` [live] non_matching_readback_not_silently_accepted
- `PASS` [live] nonfinite_text_strictly_rejected

### R03_LIVE — BLOCKED
- `PASS` [static] static_indexed_ops_executable
- `PASS` [protocol] static_malformed_index_and_key_rejected_pre_engine
- `PASS` [live] vector_element_readback
- `NOT_RUN` [live] matrix_cell_or_row_readback — no 2-D property was exposed
- `BLOCKED` [live] keyed_entry_set_and_readback — the keyed entry write could not be verified: the engine state was unknown (node.property_entry_set failed with ENGINE_CALL_FAILED (COMSOL getEntryKeys call failed: JavaWorkerError: {"completed_at_ms": "1789977148239", "failure": {"code": "ENGINE_CALL_FAILED", "execution_state_unknown": true, "message": "NoSuchMethodException: getEntryKeys is not declared by com.comsol.clientapi.impl.MaterialModelClient", "ok": false}, "ok": false, "queued_at_ms": "1789977148239", "request_id": "wrk-4aee4b02-dd4b-459b-8fa6-b8a7f93d022e", "started_at_ms": "1789977148239", "status": "FAILED", "type": "call"}); the callback did not declare a pre-dispatch stage, so the engine state cannot be shown to be unchanged)
- `BLOCKED` [live] wrong_index_rejected_without_write — an out-of-range index neither was rejected cleanly nor reported an explicit expansion
- `PASS` [live] setter_noop_or_normalized_value_detected
- `BLOCKED` [live] non_target_items_unchanged — a non-target element changed or the write was not verified
- `PASS` [live] empty_or_single_element_property
- `PASS` [protocol] envelope_consistency

### R_READBACK — BLOCKED
- `NOT_RUN` [rollup] r01_expression_readback_status — roll-up of R01_LIVE: NOT_RUN
- `BLOCKED` [rollup] r03_indexed_keyed_readback_status — roll-up of R03_LIVE: BLOCKED
- `NOT_RUN` [rollup] r04_invariant_readback_status — roll-up of R04_LIVE: NOT_RUN
- `PASS` [rollup] r_round_not_failed

### W13_T048_selection_drift — FAIL
- `PASS` [static] static_selection_ops_availability
- `PASS` [live] named_selection_created_and_bound
- `PASS` [live] geometry_revision_recorded
- `FAIL` [live] revalidation_after_geometry_change — the selection could not be revalidated after the geometry change
- `NOT_RUN` [live] drift_stops_boundary_application — the measured selection state did not change across the geometry rebuild, so no drift could be provoked
- `NOT_RUN` [live] entity_measure_change_recorded — prerequisite subcase revalidation_after_geometry_change did not pass

### W13_T016_2D_data — BLOCKED
- `PASS` [static] static_function_data_ops_availability
- `PASS` [fixture] non_axisymmetric_fixture_hash_recorded
- `BLOCKED` [live] two_d_interpolation_angular_difference_preserved — function.create returned function.create failed with PROPERTY_TYPE_MISMATCH (fununit must be an array for this property); the callback did not declare a pre-dispatch stage, so the engine state cannot be shown to be unchanged
- `NOT_RUN` [live] radial_average_not_substituted — prerequisite subcase two_d_interpolation_angular_difference_preserved did not pass
- `NOT_RUN` [live] m_mm_coordinate_conversion — prerequisite subcase radial_average_not_substituted did not pass
- `NOT_RUN` [live] interpolation_and_extrapolation_settings_recorded — prerequisite subcase m_mm_coordinate_conversion did not pass

### W14_T009_geometry_edit — FAIL
- `PASS` [static] static_geometry_ops_availability
- `FAIL` [live] work_plane_and_array_located — the work-plane fixture could not be created: geometry.workplane_create returned OPERATION_FAILED: the operation reported a failed domain status
- `FAIL` [live] local_subfeature_edit_applied — the work-plane fixture could not be created: geometry.workplane_create returned OPERATION_FAILED: the operation reported a failed domain status
- `FAIL` [live] left_most_object_preserved — the work-plane fixture could not be created: geometry.workplane_create returned OPERATION_FAILED: the operation reported a failed domain status
- `FAIL` [live] count_position_spacing_quantified — the work-plane fixture could not be created: geometry.workplane_create returned OPERATION_FAILED: the operation reported a failed domain status
- `FAIL` [live] main_model_not_replaced — the work-plane fixture could not be created: geometry.workplane_create returned OPERATION_FAILED: the operation reported a failed domain status
- `FAIL` [live] sibling_features_unchanged — the work-plane fixture could not be created: geometry.workplane_create returned OPERATION_FAILED: the operation reported a failed domain status

### W14_T034_local_paths — FAIL
- `PASS` [static] static_model_path_ops_availability
- `PASS` [fixture] local_spaces_and_chinese_path
- `PASS` [live] model_save_load_roundtrip_hash
- `BLOCKED` [live] missing_dependency_reported — the missing-dependency fixture needs operations this build does not offer: model.remove
- `FAIL` [live] cad_import_license_limited — geometry.import returned OPERATION_FAILED

### W15_T007_selections — BLOCKED
- `PASS` [static] static_physics_selection_ops_availability
- `BLOCKED` [live] physics_level_selection_set — the physics/feature/selection fixture could not be built: definition.component_manage returned an invalid envelope: geometry.build returned EXECUTION_STATE_UNKNOWN: the operation left an unresolved engine state
- `BLOCKED` [live] feature_level_selection_set — the physics/feature/selection fixture could not be built: definition.component_manage returned an invalid envelope: geometry.build returned EXECUTION_STATE_UNKNOWN: the operation left an unresolved engine state
- `BLOCKED` [live] inherited_selection_not_writable — the physics/feature/selection fixture could not be built: definition.component_manage returned an invalid envelope: geometry.build returned EXECUTION_STATE_UNKNOWN: the operation left an unresolved engine state
- `BLOCKED` [live] named_selection_binding_readback — the physics/feature/selection fixture could not be built: definition.component_manage returned an invalid envelope: geometry.build returned EXECUTION_STATE_UNKNOWN: the operation left an unresolved engine state
- `BLOCKED` [live] no_invalid_parent_feature_coupling — the physics/feature/selection fixture could not be built: definition.component_manage returned an invalid envelope: geometry.build returned EXECUTION_STATE_UNKNOWN: the operation left an unresolved engine state

### W15_T017_material — BLOCKED
- `PASS` [static] static_material_ops_availability
- `PASS` [live] k_T_Cp_T_and_rho_expressions
- `PASS` [live] anisotropic_tensor_and_coordinate_system
- `PASS` [live] missing_required_property_preflight_error
- `BLOCKED` [live] material_readback_after_update — the material property readback was refused: the engine state was unknown (node.property_get failed with API_UNSUPPORTED (authoritative value metadata is unavailable for property 'thermalconductivity'); the callback did not declare a pre-dispatch stage, so the engine state cannot be shown to be unchanged)

### W15_T042_license — FAIL
- `PASS` [static] static_license_ops_availability
- `FAIL` [live] license_inspect_records_has_product — runtime.license_inspect returned INVALID_REQUEST
- `FAIL` [live] missing_product_blocks_with_blocked_license — runtime.license_inspect returned INVALID_REQUEST without product rows
- `BLOCKED` [live] authorized_product_usable — the authorized-product probe returned an unknown engine state (EXECUTION_STATE_UNKNOWN), so product usability was not established
- `PASS` [live] probe_does_not_occupy_license

### W16_T018_mesh — BLOCKED
- `PASS` [static] static_mesh_ops_availability
- `BLOCKED` [live] free_tet_sequence_created — geometry.build returned EXECUTION_STATE_UNKNOWN: the operation left an unresolved engine state
- `BLOCKED` [live] local_size_feature_applied — geometry.build returned EXECUTION_STATE_UNKNOWN: the operation left an unresolved engine state
- `BLOCKED` [live] modify_and_rebuild — geometry.build returned EXECUTION_STATE_UNKNOWN: the operation left an unresolved engine state
- `BLOCKED` [live] statistics_counts_and_coverage — geometry.build returned EXECUTION_STATE_UNKNOWN: the operation left an unresolved engine state
- `BLOCKED` [live] quality_definition_and_low_quality_locations — geometry.build returned EXECUTION_STATE_UNKNOWN: the operation left an unresolved engine state
- `NOT_RUN` [protocol] build_success_is_not_quality_pass — planned subcase recorded no observation

### W16_T019_chainC_continue — BLOCKED
- `NOT_RUN` [static] static_chain_c_ops_availability — dependency not established: predecessor:W16_T019_chainA_steady
- `NOT_RUN` [fixture] user_style_model_available — dependency not established: predecessor:W16_T019_chainA_steady
- `NOT_RUN` [live] target_only_modified — dependency not established: predecessor:W16_T019_chainA_steady
- `NOT_RUN` [live] non_target_nodes_preserved — dependency not established: predecessor:W16_T019_chainA_steady
- `NOT_RUN` [live] manual_solver_preserved — dependency not established: predecessor:W16_T019_chainA_steady
- `NOT_RUN` [live] derived_values_and_data_association_preserved — dependency not established: predecessor:W16_T019_chainA_steady
- `NOT_RUN` [live] solve_after_continuation — dependency not established: predecessor:W16_T019_chainA_steady

### W16_T020_solver — BLOCKED
- `PASS` [static] static_solver_ops_availability
- `PASS` [live] study_target_read
- `PASS` [live] study_target_create
- `PASS` [live] study_target_step
- `BLOCKED` [live] solver_tree_read — the bound model exposes no solver sequence for the requested study, so no solver tree exists to read (study.solver_generate was attempted first; see solver_generate)
- `PASS` [live] solver_generate
- `NOT_RUN` [live] sub_feature_property_update_readback — prerequisite subcase solver_tree_read did not pass
- `NOT_RUN` [live] unknown_property_fails_accurately — prerequisite subcase solver_tree_read did not pass
- `NOT_RUN` [live] manual_solver_not_auto_overwritten — prerequisite subcase solver_tree_read did not pass
- `NOT_RUN` [live] solve_after_subfeature_update — planned subcase recorded no observation

### GUARD_T035 — PASS
- `PASS` [static] static_docs_guard_ops_executable
- `PASS` [live] outside_workspace_source_denied
- `PASS` [live] symlink_outside_denied
- `PASS` [live] outside_write_attempt_denied
- `PASS` [live] failed_denial_does_not_modify_files
- `PASS` [protocol] credential_text_not_persisted
- `PASS` [protocol] private_paths_not_in_evidence

### GUARD_T038 — PASS
- `PASS` [protocol] initialize_and_tools_list_schemas
- `PASS` [protocol] error_propagation_structured
- `PASS` [protocol] registry_paging_continuation
- `PASS` [protocol] partial_failure_not_reported_as_success
- `PASS` [protocol] outer_iserror_matches_success
- `PASS` [protocol] structured_content_action_results
- `PASS` [protocol] stdio_log_isolation

## Unknown-job ledger (driver side)

- gate refusals: 0 (none)
- recorded UNKNOWN jobs: 24 (released: 24, unreleased: 0)
- calls that stayed refused after the release path ran: 0

- `10589e36-9c3f-4011-9188-087c016c212d` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `0d272384-0ea4-422a-85ab-9da5d04e816c` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `66aa4b44-a5c6-4247-8b32-a67d2b889260` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `74e0af9a-ef48-468f-b6cf-a07819db9e7c` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `797c1af9-92cb-497b-b04d-ed736d62079b` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `363a32c5-cf81-4c22-ac17-9168e3210376` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `2697c521-11ca-418d-ae8d-6504a2a8e6f0` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `930d6d03-55b2-4c75-8885-9d6308b75e00` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `7e271004-7fdb-4ed1-a7a6-601be40649c4` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `38721c44-6146-425e-a37e-3e3a78528c1a` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `512c37f0-5407-4752-9195-5364bbea5032` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `b0efa0b3-8e70-4f74-994f-d6815e29759e` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `6e4d7c64-e154-4424-924a-be5195215424` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `b14315a9-420a-4d13-b815-e11e8f798d1a` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `2a606449-3f00-4a96-a2dd-cca0baa599e7` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `e2849138-8754-46ba-acd3-fb8e0a6e9e9b` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `93524a27-76b4-4ecb-82a6-a6f47899a212` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `3fe1726e-6cb5-44b3-ba70-b5b9ab9676a2` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `93e390e2-87a2-4d37-a934-9f53dbe0df0f` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `1e47875e-3cf8-4500-ad6a-59b79abad183` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `decb92b8-d002-4f7e-bc02-2cbd2f022288` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `36fddefd-694e-4725-8702-95412a1bf245` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `abeaa175-a5b4-4e92-8e1f-6350d2b702b4` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `11dc7b12-c58f-4f48-9036-91ab1b3fa9ad` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN

## First causes (C03)

- per class: DEPENDENCY_BLOCKED 2, HARNESS_FAILURE 2, IMPLEMENTATION_GAP 7, EXTERNAL_BLOCKER 1
- classification rule: each case contributes at most one first cause; downstream subcases list DEPENDENCY_BLOCKED against it instead of becoming pseudo-independent defects
- `R03_LIVE` → **IMPLEMENTATION_GAP** (classified): the keyed entry write could not be verified: the engine state was unknown (node.property_entry_set failed with ENGINE_CALL_FAILED (COMSOL getEntryKeys call fail
- `R_READBACK` → **IMPLEMENTATION_GAP** (classified): roll-up of R03_LIVE: BLOCKED
- `W13_T048_selection_drift` → **IMPLEMENTATION_GAP** (classified): the selection could not be revalidated after the geometry change
- `W13_T016_2D_data` → **IMPLEMENTATION_GAP** (classified): function.create returned function.create failed with PROPERTY_TYPE_MISMATCH (fununit must be an array for this property); the callback did not declare a pre-dis
- `W14_T009_geometry_edit` → **HARNESS_FAILURE** (OPERATION_FAILED): the work-plane fixture could not be created: geometry.workplane_create returned OPERATION_FAILED: the operation reported a failed domain status
- `W14_T034_local_paths` → **DEPENDENCY_BLOCKED** (classified): the missing-dependency fixture needs operations this build does not offer: model.remove
- `W15_T007_selections` → **HARNESS_FAILURE** (classified): the physics/feature/selection fixture could not be built: definition.component_manage returned an invalid envelope: geometry.build returned EXECUTION_STATE_UNKN
- `W15_T017_material` → **EXTERNAL_BLOCKER** (EXECUTION_STATE_UNKNOWN): the material property readback was refused: the engine state was unknown (node.property_get failed with API_UNSUPPORTED (authoritative value metadata is unavail
- `W15_T042_license` → **IMPLEMENTATION_GAP** (classified): runtime.license_inspect returned INVALID_REQUEST — reclassified from EXTERNAL_BLOCKER: the evidence names a revision/signature/path defect, which is never an unobtainable resource: no license, product or platform claim may stand in for it
- `W16_T018_mesh` → **IMPLEMENTATION_GAP** (classified): geometry.build returned EXECUTION_STATE_UNKNOWN: the operation left an unresolved engine state
- `W16_T019_chainC_continue` → **DEPENDENCY_BLOCKED** (DEPENDENCY_BLOCKED): the case's declared prerequisite(s) are not established: predecessor:W16_T019_chainA_steady (W16_T019_chainA_steady has not run in this run, so its product cann
- `W16_T020_solver` → **IMPLEMENTATION_GAP** (classified): the bound model exposes no solver sequence for the requested study, so no solver tree exists to read (study.solver_generate was attempted first; see solver_gene

## Case isolation and prerequisites (C03)

- `R01_LIVE`: isolation=own_model (applied=True) — negative probes must not pollute another case's model; prerequisites=SATISFIED; shared binding restored: mcp46
- `R03_LIVE`: isolation=own_model (applied=True) — negative probes must not pollute another case's model; prerequisites=SATISFIED; shared binding restored: mcp46
- `R_READBACK`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `W13_T048_selection_drift`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `W13_T016_2D_data`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `W14_T009_geometry_edit`: isolation=checkpoint_restore (applied=False) — an in-place geometry edit is only safe on a restored fixture; prerequisites=SATISFIED
- `W14_T034_local_paths`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `W15_T007_selections`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `W15_T017_material`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `W15_T042_license`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `W16_T018_mesh`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `W16_T019_chainC_continue`: isolation=checkpoint_restore (applied=False) — the user-style fixture is restored from a verified checkpoint, never edited in place; prerequisites=UNSATISFIED; missing: predecessor:W16_T019_chainA_steady
- `W16_T020_solver`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `GUARD_T035`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `GUARD_T038`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED

## Slice (this run)

- stage: M3
- live: True
- selected cases: R01_LIVE, R03_LIVE, R_READBACK, W13_T048_selection_drift, W13_T016_2D_data, W14_T009_geometry_edit, W14_T034_local_paths, W15_T007_selections, W15_T017_material, W15_T042_license, W16_T018_mesh, W16_T019_chainC_continue, W16_T020_solver, GUARD_T035, GUARD_T038
- note: a slice is a documented run plan, not a weakened acceptance: every selected case still records its own subcases at their declared evidence level, and a stage subset never turns an unselected case into a pass
- M0: R01_LIVE, R03_LIVE, R_READBACK, W13_T048_selection_drift, W13_T016_2D_data, W14_T009_geometry_edit, W14_T034_local_paths, W15_T007_selections, W15_T017_material, W15_T042_license, W16_T018_mesh, W16_T019_chainC_continue, W16_T020_solver, GUARD_T035, GUARD_T038
- M1: W13_T006_variables, W13_T015_units, R04_LIVE, GUARD_T010, GUARD_T005, GUARD_T033
- M2: W16_T019_chainA_steady, W16_T019_chainB_transient
- M3: R01_LIVE, R03_LIVE, R_READBACK, W13_T048_selection_drift, W13_T016_2D_data, W14_T009_geometry_edit, W14_T034_local_paths, W15_T007_selections, W15_T017_material, W15_T042_license, W16_T018_mesh, W16_T019_chainC_continue, W16_T020_solver, GUARD_T035, GUARD_T038

## Execution context (C02)

- logical requests planned (run/case/step/sequence keys): 540
- refusals classified as: stale_expected 0, external_observation 0, unknown_job 0, generation_mismatch 0, ambiguous_envelope 0, explicit_key_reuse 0
- recorded new plans (replans): 4; same-key replays recorded: 0 (identical body verified: 0)
- dispatch stages recorded: dispatched 23, dispatched_without_mutation 8, unknown 9
- generation replacements observed: 0
- deliberate negative probes (never auto-repaired): 0
- unfinished jobs known to the context: 0
