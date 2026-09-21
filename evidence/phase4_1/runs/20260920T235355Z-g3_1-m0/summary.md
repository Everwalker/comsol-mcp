# Phase 4 (G3) production stdio acceptance run

- generated: 2026-09-20T23:53:57.665846Z
- run directory: `$REPO/evidence/phase4_1/runs/20260920T235355Z-g3_1-m0`

| case | package | status | assertions | subcases | first finding |
| --- | --- | --- | --- | --- | --- |
| R01_LIVE | R | NOT_RUN | 7 | 8 |  |
| R03_LIVE | R | NOT_RUN | 7 | 10 |  |
| R_READBACK | R | NOT_RUN | 4 | 4 |  |
| W13_T048_selection_drift | W13 | NOT_RUN | 5 | 6 |  |
| W13_T016_2D_data | W13 | NOT_RUN | 7 | 6 |  |
| W14_T009_geometry_edit | W14 | NOT_RUN | 5 | 7 |  |
| W14_T034_local_paths | W14 | NOT_RUN | 7 | 5 |  |
| W15_T007_selections | W15 | NOT_RUN | 5 | 6 |  |
| W15_T017_material | W15 | NOT_RUN | 5 | 5 |  |
| W15_T042_license | W15 | NOT_RUN | 5 | 5 |  |
| W16_T018_mesh | W16 | NOT_RUN | 6 | 7 |  |
| W16_T019_chainC_continue | W16 | BLOCKED | 6 | 7 | the user-style continuation model was not supplied |
| W16_T020_solver | W16 | NOT_RUN | 7 | 6 |  |
| GUARD_T035 | GUARD | NOT_RUN | 11 | 7 |  |
| GUARD_T038 | GUARD | PASS | 11 | 7 |  |

cases: PASS 1 / FAIL 0 / BLOCKED 1 / NOT_RUN 13

## Subcase detail

### R01_LIVE — NOT_RUN
- `PASS` [static] static_expression_readback_ops_executable
- `PASS` [protocol] static_malformed_typed_value_rejected_pre_engine
- `NOT_RUN` [live] expression_string_same_text_verified — offline run: --live was not supplied
- `NOT_RUN` [live] expression_array_or_matrix_verified — offline run: --live was not supplied
- `NOT_RUN` [live] unit_expression_text_preserved — offline run: --live was not supplied
- `NOT_RUN` [live] incompatible_kind_or_unit_rejected_before_write — offline run: --live was not supplied
- `NOT_RUN` [live] non_matching_readback_not_silently_accepted — offline run: --live was not supplied
- `NOT_RUN` [live] nonfinite_text_strictly_rejected — offline run: --live was not supplied

### R03_LIVE — NOT_RUN
- `PASS` [static] static_indexed_ops_executable
- `PASS` [protocol] static_malformed_index_and_key_rejected_pre_engine
- `NOT_RUN` [live] vector_element_readback — offline run: --live was not supplied
- `NOT_RUN` [live] matrix_cell_or_row_readback — offline run: --live was not supplied
- `NOT_RUN` [live] keyed_entry_set_and_readback — offline run: --live was not supplied
- `NOT_RUN` [live] wrong_index_rejected_without_write — offline run: --live was not supplied
- `NOT_RUN` [live] setter_noop_or_normalized_value_detected — offline run: --live was not supplied
- `NOT_RUN` [live] non_target_items_unchanged — offline run: --live was not supplied
- `NOT_RUN` [live] empty_or_single_element_property — offline run: --live was not supplied
- `NOT_RUN` [protocol] envelope_consistency — no live indexed/keyed call was made

### R_READBACK — NOT_RUN
- `NOT_RUN` [rollup] r01_expression_readback_status — roll-up of R01_LIVE: NOT_RUN
- `NOT_RUN` [rollup] r03_indexed_keyed_readback_status — roll-up of R03_LIVE: NOT_RUN
- `NOT_RUN` [rollup] r04_invariant_readback_status — roll-up of R04_LIVE: NOT_RUN
- `PASS` [rollup] r_round_not_failed

### W13_T048_selection_drift — NOT_RUN
- `PASS` [static] static_selection_ops_availability
- `NOT_RUN` [live] named_selection_created_and_bound — offline run: --live was not supplied
- `NOT_RUN` [live] geometry_revision_recorded — offline run: --live was not supplied
- `NOT_RUN` [live] revalidation_after_geometry_change — offline run: --live was not supplied
- `NOT_RUN` [live] drift_stops_boundary_application — offline run: --live was not supplied
- `NOT_RUN` [live] entity_measure_change_recorded — offline run: --live was not supplied

### W13_T016_2D_data — NOT_RUN
- `PASS` [static] static_function_data_ops_availability
- `PASS` [fixture] non_axisymmetric_fixture_hash_recorded
- `NOT_RUN` [live] two_d_interpolation_angular_difference_preserved — offline run: --live was not supplied
- `NOT_RUN` [live] radial_average_not_substituted — offline run: --live was not supplied
- `NOT_RUN` [live] m_mm_coordinate_conversion — offline run: --live was not supplied
- `NOT_RUN` [live] interpolation_and_extrapolation_settings_recorded — offline run: --live was not supplied

### W14_T009_geometry_edit — NOT_RUN
- `PASS` [static] static_geometry_ops_availability
- `NOT_RUN` [live] work_plane_and_array_located — offline run: --live was not supplied
- `NOT_RUN` [live] local_subfeature_edit_applied — offline run: --live was not supplied
- `NOT_RUN` [live] left_most_object_preserved — offline run: --live was not supplied
- `NOT_RUN` [live] count_position_spacing_quantified — offline run: --live was not supplied
- `NOT_RUN` [live] main_model_not_replaced — offline run: --live was not supplied
- `NOT_RUN` [live] sibling_features_unchanged — offline run: --live was not supplied

### W14_T034_local_paths — NOT_RUN
- `PASS` [static] static_model_path_ops_availability
- `PASS` [fixture] local_spaces_and_chinese_path
- `NOT_RUN` [live] model_save_load_roundtrip_hash — offline run: --live was not supplied
- `NOT_RUN` [live] missing_dependency_reported — offline run: --live was not supplied
- `NOT_RUN` [live] cad_import_license_limited — offline run: --live was not supplied

### W15_T007_selections — NOT_RUN
- `PASS` [static] static_physics_selection_ops_availability
- `NOT_RUN` [live] physics_level_selection_set — offline run: --live was not supplied
- `NOT_RUN` [live] feature_level_selection_set — offline run: --live was not supplied
- `NOT_RUN` [live] inherited_selection_not_writable — offline run: --live was not supplied
- `NOT_RUN` [live] named_selection_binding_readback — offline run: --live was not supplied
- `NOT_RUN` [live] no_invalid_parent_feature_coupling — offline run: --live was not supplied

### W15_T017_material — NOT_RUN
- `PASS` [static] static_material_ops_availability
- `NOT_RUN` [live] k_T_Cp_T_and_rho_expressions — offline run: --live was not supplied
- `NOT_RUN` [live] anisotropic_tensor_and_coordinate_system — offline run: --live was not supplied
- `NOT_RUN` [live] missing_required_property_preflight_error — offline run: --live was not supplied
- `NOT_RUN` [live] material_readback_after_update — offline run: --live was not supplied

### W15_T042_license — NOT_RUN
- `PASS` [static] static_license_ops_availability
- `NOT_RUN` [live] license_inspect_records_has_product — offline run: --live was not supplied
- `NOT_RUN` [live] missing_product_blocks_with_blocked_license — offline run: --live was not supplied
- `NOT_RUN` [live] authorized_product_usable — offline run: --live was not supplied
- `NOT_RUN` [live] probe_does_not_occupy_license — offline run: --live was not supplied

### W16_T018_mesh — NOT_RUN
- `PASS` [static] static_mesh_ops_availability
- `NOT_RUN` [live] free_tet_sequence_created — offline run: --live was not supplied
- `NOT_RUN` [live] local_size_feature_applied — offline run: --live was not supplied
- `NOT_RUN` [live] modify_and_rebuild — offline run: --live was not supplied
- `NOT_RUN` [live] statistics_counts_and_coverage — offline run: --live was not supplied
- `NOT_RUN` [live] quality_definition_and_low_quality_locations — offline run: --live was not supplied
- `PASS` [protocol] build_success_is_not_quality_pass

### W16_T019_chainC_continue — BLOCKED
- `PASS` [static] static_chain_c_ops_availability
- `NOT_RUN` [fixture] user_style_model_available — no user-style continuation model was supplied (--chain-c-model)
- `BLOCKED` [live] target_only_modified — the user-style continuation model was not supplied
- `BLOCKED` [live] non_target_nodes_preserved — the user-style continuation model was not supplied
- `BLOCKED` [live] manual_solver_preserved — the user-style continuation model was not supplied
- `BLOCKED` [live] derived_values_and_data_association_preserved — the user-style continuation model was not supplied
- `BLOCKED` [live] solve_after_continuation — the user-style continuation model was not supplied

### W16_T020_solver — NOT_RUN
- `PASS` [static] static_solver_ops_availability
- `NOT_RUN` [live] solver_tree_read — offline run: --live was not supplied
- `NOT_RUN` [live] sub_feature_property_update_readback — offline run: --live was not supplied
- `NOT_RUN` [live] solve_after_subfeature_update — offline run: --live was not supplied
- `NOT_RUN` [live] unknown_property_fails_accurately — offline run: --live was not supplied
- `NOT_RUN` [live] manual_solver_not_auto_overwritten — offline run: --live was not supplied

### GUARD_T035 — NOT_RUN
- `PASS` [static] static_docs_guard_ops_executable
- `NOT_RUN` [live] outside_workspace_source_denied — offline run: --live was not supplied
- `NOT_RUN` [live] symlink_outside_denied — offline run: --live was not supplied
- `NOT_RUN` [live] outside_write_attempt_denied — offline run: --live was not supplied
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

## First causes (C03)

- per class: DEPENDENCY_BLOCKED 0, HARNESS_FAILURE 0, IMPLEMENTATION_GAP 1, EXTERNAL_BLOCKER 0
- classification rule: each case contributes at most one first cause; downstream subcases list DEPENDENCY_BLOCKED against it instead of becoming pseudo-independent defects
- `W16_T019_chainC_continue` → **IMPLEMENTATION_GAP** (classified): the user-style continuation model was not supplied

## Case isolation and prerequisites (C03)

- `R01_LIVE`: isolation=own_model (applied=False) — negative probes must not pollute another case's model; prerequisites=SATISFIED
- `R03_LIVE`: isolation=own_model (applied=False) — negative probes must not pollute another case's model; prerequisites=SATISFIED
- `R_READBACK`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `W13_T048_selection_drift`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `W13_T016_2D_data`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `W14_T009_geometry_edit`: isolation=checkpoint_restore (applied=False) — an in-place geometry edit is only safe on a restored fixture; prerequisites=SATISFIED
- `W14_T034_local_paths`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `W15_T007_selections`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `W15_T017_material`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `W15_T042_license`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `W16_T018_mesh`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `W16_T019_chainC_continue`: isolation=checkpoint_restore (applied=False) — the user-style fixture is restored from a verified checkpoint, never edited in place; prerequisites=SATISFIED
- `W16_T020_solver`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `GUARD_T035`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `GUARD_T038`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED

## Slice (this run)

- stage: M0
- live: False
- selected cases: R01_LIVE, R03_LIVE, R_READBACK, W13_T048_selection_drift, W13_T016_2D_data, W14_T009_geometry_edit, W14_T034_local_paths, W15_T007_selections, W15_T017_material, W15_T042_license, W16_T018_mesh, W16_T019_chainC_continue, W16_T020_solver, GUARD_T035, GUARD_T038
- note: a slice is a documented run plan, not a weakened acceptance: every selected case still records its own subcases at their declared evidence level, and a stage subset never turns an unselected case into a pass
- M0: R01_LIVE, R03_LIVE, R_READBACK, W13_T048_selection_drift, W13_T016_2D_data, W14_T009_geometry_edit, W14_T034_local_paths, W15_T007_selections, W15_T017_material, W15_T042_license, W16_T018_mesh, W16_T019_chainC_continue, W16_T020_solver, GUARD_T035, GUARD_T038
- M1: W13_T006_variables, W13_T015_units, R04_LIVE, GUARD_T010, GUARD_T005, GUARD_T033
- M2: W16_T019_chainA_steady, W16_T019_chainB_transient
- M3: R01_LIVE, R03_LIVE, R_READBACK, W13_T048_selection_drift, W13_T016_2D_data, W14_T009_geometry_edit, W14_T034_local_paths, W15_T007_selections, W15_T017_material, W15_T042_license, W16_T018_mesh, W16_T019_chainC_continue, W16_T020_solver, GUARD_T035, GUARD_T038

## Execution context (C02)

- logical requests planned (run/case/step/sequence keys): 4
- refusals classified as: stale_expected 0, external_observation 0, unknown_job 0, generation_mismatch 0, ambiguous_envelope 0, explicit_key_reuse 0
- recorded new plans (replans): 0; same-key replays recorded: 0
- generation replacements observed: 0
- deliberate negative probes (never auto-repaired): 0
- unfinished jobs known to the context: 0
