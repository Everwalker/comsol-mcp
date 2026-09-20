# Phase 4 (G3) production stdio acceptance run

- generated: 2026-09-20T13:17:06.673961Z
- run directory: `$REPO/evidence/phase4/runs/20260920T130620Z-g3-live/driver`

| case | package | status | assertions | subcases | first finding |
| --- | --- | --- | --- | --- | --- |
| R01_LIVE | R | NOT_RUN | 6 | 8 |  |
| R03_LIVE | R | FAIL | 7 | 10 | an indexed/keyed call returned an inner success that disagrees with the outer isError flag |
| R04_LIVE | R | FAIL | 8 | 8 | a violated required invariant did not produce a non-PASS verification status |
| R_READBACK | R | FAIL | 2 | 4 | roll-up of R03_LIVE: FAIL |
| W13_T006_variables | W13 | BLOCKED | 5 | 7 | variable.group_create returned EXECUTION_STATE_UNKNOWN |
| W13_T015_units | W13 | BLOCKED | 4 | 6 | physics.create returned EXECUTION_STATE_UNKNOWN |
| W13_T048_selection_drift | W13 | BLOCKED | 4 | 6 | selection.create returned EXECUTION_STATE_UNKNOWN |
| W13_T016_2D_data | W13 | BLOCKED | 7 | 6 | function.create returned EXECUTION_STATE_UNKNOWN |
| W14_T009_geometry_edit | W14 | BLOCKED | 4 | 7 | geometry.feature_create returned EXECUTION_STATE_UNKNOWN |
| W14_T034_local_paths | W14 | BLOCKED | 8 | 5 | model.save returned EXECUTION_STATE_UNKNOWN |
| W15_T007_selections | W15 | BLOCKED | 4 | 6 | physics.selection_set returned EXECUTION_STATE_UNKNOWN |
| W15_T017_material | W15 | FAIL | 4 | 5 | the temperature-dependent property expressions were not preserved in the readback |
| W15_T042_license | W15 | BLOCKED | 6 | 5 | BLOCKED_LICENSE: EXECUTION_STATE_UNKNOWN |
| W16_T018_mesh | W16 | BLOCKED | 5 | 7 | mesh.create returned EXECUTION_STATE_UNKNOWN |
| W16_T019_chainA_steady | W16 | BLOCKED | 6 | 12 | model.create returned EXECUTION_STATE_UNKNOWN |
| W16_T019_chainB_transient | W16 | BLOCKED | 5 | 6 | model.create returned EXECUTION_STATE_UNKNOWN |
| W16_T019_chainC_continue | W16 | BLOCKED | 5 | 7 | model.load returned EXECUTION_STATE_UNKNOWN |
| W16_T020_solver | W16 | FAIL | 5 | 6 | the solver tree could not be read |
| GUARD_T010 | GUARD | NOT_RUN | 5 | 5 |  |
| GUARD_T035 | GUARD | FAIL | 9 | 7 | an outside-workspace documentation source was accepted or the denial was not explicit |
| GUARD_T038 | GUARD | PASS | 9 | 7 |  |
| GUARD_T005 | GUARD | PASS | 5 | 4 |  |
| GUARD_T033 | GUARD | FAIL | 5 | 4 | operation_describe does not publish an evaluation policy for evaluate_expressions in this build, so the T033 policy contract cannot be inspected |

cases: PASS 2 / FAIL 7 / BLOCKED 12 / NOT_RUN 2

## Subcase detail

### R01_LIVE — NOT_RUN
- `PASS` [static] static_expression_readback_ops_executable
- `PASS` [protocol] static_malformed_typed_value_rejected_pre_engine
- `NOT_RUN` [live] expression_string_same_text_verified — the bound model exposed no scalar string-kind property; the R01 comparison rule was not exercised
- `NOT_RUN` [live] expression_array_or_matrix_verified — the bound model exposed no string array/matrix property
- `NOT_RUN` [live] unit_expression_text_preserved — no property exposed authoritative unit metadata; unit-bearing text was not exercised
- `NOT_RUN` [live] incompatible_kind_or_unit_rejected_before_write — the bound model exposed no scalar numeric property for the negative contract probe
- `NOT_RUN` [live] non_matching_readback_not_silently_accepted — no scalar string property was available
- `NOT_RUN` [live] nonfinite_text_strictly_rejected — no scalar numeric property was available

### R03_LIVE — FAIL
- `PASS` [static] static_indexed_ops_executable
- `PASS` [protocol] static_malformed_index_and_key_rejected_pre_engine
- `NOT_RUN` [live] vector_element_readback — no 1-D property with at least two elements was exposed
- `NOT_RUN` [live] matrix_cell_or_row_readback — no 2-D property was exposed
- `NOT_RUN` [live] keyed_entry_set_and_readback — the bound model exposed no keyed (property-group) entry property to probe with setEntry
- `NOT_RUN` [live] wrong_index_rejected_without_write — no 1-D property was exposed
- `NOT_RUN` [live] setter_noop_or_normalized_value_detected — no 1-D property was exposed
- `NOT_RUN` [live] non_target_items_unchanged — no multi-element property was exposed
- `NOT_RUN` [live] empty_or_single_element_property — the bound model exposed no empty or single-element 1-D property
- `FAIL` [protocol] envelope_consistency — an indexed/keyed call returned an inner success that disagrees with the outer isError flag

### R04_LIVE — FAIL
- `PASS` [static] static_transaction_ops_executable
- `PASS` [protocol] static_unsupported_invariant_rejected_pre_write
- `FAIL` [live] invariant_violation_not_reported_as_pass — a violated required invariant did not produce a non-PASS verification status
- `FAIL` [live] verified_transaction_status_split — a satisfied transaction did not report execution and verification status separately
- `BLOCKED` [live] stale_revision_rejected — a stale expected_revision was not rejected
- `NOT_RUN` [live] cross_model_transaction_record_rejected — no transaction_id was returned to re-verify
- `NOT_RUN` [live] recorded_pass_with_changed_live_property — no transaction_id was returned to re-verify
- `BLOCKED` [live] checkpoint_recovery_after_failed_invariant — checkpoint creation unavailable: EXECUTION_STATE_UNKNOWN

### R_READBACK — FAIL
- `NOT_RUN` [rollup] r01_expression_readback_status — roll-up of R01_LIVE: NOT_RUN
- `FAIL` [rollup] r03_indexed_keyed_readback_status — roll-up of R03_LIVE: FAIL
- `FAIL` [rollup] r04_invariant_readback_status — roll-up of R04_LIVE: FAIL
- `FAIL` [rollup] r_round_not_failed — an R-round readback case reported FAIL

### W13_T006_variables — BLOCKED
- `PASS` [static] static_variable_ops_availability
- `PASS` [static] static_legacy_variable_route_available
- `BLOCKED` [live] variables_two_in_one_group — variable.group_create returned EXECUTION_STATE_UNKNOWN
- `NOT_RUN` [live] varnames_contains_modified_variable — prerequisite subcase variables_two_in_one_group did not pass
- `NOT_RUN` [live] no_bogus_name_expr_variables — prerequisite subcase variables_two_in_one_group did not pass
- `NOT_RUN` [live] expression_evaluates_after_modification — prerequisite subcase varnames_contains_modified_variable did not pass
- `NOT_RUN` [live] component_and_global_scope — the component variable evaluation did not pass; the global-scope probe was not attempted

### W13_T015_units — BLOCKED
- `PASS` [static] static_physics_unit_ops_availability
- `BLOCKED` [live] surface_source_W_per_m2 — physics.create returned EXECUTION_STATE_UNKNOWN
- `NOT_RUN` [live] volume_source_W_per_m3 — prerequisite subcase surface_source_W_per_m2 did not pass
- `NOT_RUN` [live] coordinate_unit_m_and_mm — prerequisite subcase volume_source_W_per_m3 did not pass
- `NOT_RUN` [live] wrong_unit_warns_or_fails — prerequisite subcase coordinate_unit_m_and_mm did not pass
- `NOT_RUN` [live] no_implicit_thickness_or_absorptivity — prerequisite subcase wrong_unit_warns_or_fails did not pass

### W13_T048_selection_drift — BLOCKED
- `PASS` [static] static_selection_ops_availability
- `BLOCKED` [live] named_selection_created_and_bound — selection.create returned EXECUTION_STATE_UNKNOWN
- `NOT_RUN` [live] geometry_revision_recorded — prerequisite subcase named_selection_created_and_bound did not pass
- `NOT_RUN` [live] revalidation_after_geometry_change — prerequisite subcase geometry_revision_recorded did not pass
- `NOT_RUN` [live] drift_stops_boundary_application — prerequisite subcase revalidation_after_geometry_change did not pass
- `NOT_RUN` [live] entity_measure_change_recorded — prerequisite subcase revalidation_after_geometry_change did not pass

### W13_T016_2D_data — BLOCKED
- `PASS` [static] static_function_data_ops_availability
- `PASS` [fixture] non_axisymmetric_fixture_hash_recorded
- `BLOCKED` [live] two_d_interpolation_angular_difference_preserved — function.create returned EXECUTION_STATE_UNKNOWN
- `NOT_RUN` [live] radial_average_not_substituted — prerequisite subcase two_d_interpolation_angular_difference_preserved did not pass
- `NOT_RUN` [live] m_mm_coordinate_conversion — prerequisite subcase radial_average_not_substituted did not pass
- `NOT_RUN` [live] interpolation_and_extrapolation_settings_recorded — prerequisite subcase m_mm_coordinate_conversion did not pass

### W14_T009_geometry_edit — BLOCKED
- `PASS` [static] static_geometry_ops_availability
- `BLOCKED` [live] work_plane_and_array_located — geometry.feature_create returned EXECUTION_STATE_UNKNOWN
- `NOT_RUN` [live] local_subfeature_edit_applied — prerequisite subcase work_plane_and_array_located did not pass
- `NOT_RUN` [live] left_most_object_preserved — prerequisite subcase local_subfeature_edit_applied did not pass
- `NOT_RUN` [live] count_position_spacing_quantified — prerequisite subcase left_most_object_preserved did not pass
- `NOT_RUN` [live] main_model_not_replaced — prerequisite subcase count_position_spacing_quantified did not pass
- `NOT_RUN` [live] sibling_features_unchanged — prerequisite subcase count_position_spacing_quantified did not pass

### W14_T034_local_paths — BLOCKED
- `PASS` [static] static_model_path_ops_availability
- `PASS` [fixture] local_spaces_and_chinese_path
- `BLOCKED` [live] model_save_load_roundtrip_hash — model.save returned EXECUTION_STATE_UNKNOWN
- `BLOCKED` [live] missing_dependency_reported — the driver planted a text probe instead of a real .mph with a missing import; a live reload of a model with an unresolved external dependency must be supplied by the orchestrator
- `BLOCKED` [live] cad_import_license_limited — CAD import is license-limited in this installation: EXECUTION_STATE_UNKNOWN

### W15_T007_selections — BLOCKED
- `PASS` [static] static_physics_selection_ops_availability
- `BLOCKED` [live] physics_level_selection_set — physics.selection_set returned EXECUTION_STATE_UNKNOWN
- `NOT_RUN` [live] feature_level_selection_set — prerequisite subcase physics_level_selection_set did not pass
- `NOT_RUN` [live] inherited_selection_not_writable — prerequisite subcase feature_level_selection_set did not pass
- `NOT_RUN` [live] named_selection_binding_readback — prerequisite subcase feature_level_selection_set did not pass
- `NOT_RUN` [live] no_invalid_parent_feature_coupling — prerequisite subcase named_selection_binding_readback did not pass

### W15_T017_material — FAIL
- `PASS` [static] static_material_ops_availability
- `FAIL` [live] k_T_Cp_T_and_rho_expressions — the temperature-dependent property expressions were not preserved in the readback
- `NOT_RUN` [live] anisotropic_tensor_and_coordinate_system — prerequisite subcase k_T_Cp_T_and_rho_expressions did not pass
- `NOT_RUN` [live] missing_required_property_preflight_error — prerequisite subcase anisotropic_tensor_and_coordinate_system did not pass
- `NOT_RUN` [live] material_readback_after_update — prerequisite subcase k_T_Cp_T_and_rho_expressions did not pass

### W15_T042_license — BLOCKED
- `PASS` [static] static_license_ops_availability
- `BLOCKED` [live] license_inspect_records_has_product — BLOCKED_LICENSE: EXECUTION_STATE_UNKNOWN
- `BLOCKED` [live] missing_product_blocks_with_blocked_license — runtime.license_inspect returned EXECUTION_STATE_UNKNOWN without product rows
- `PASS` [live] authorized_product_usable
- `PASS` [live] probe_does_not_occupy_license

### W16_T018_mesh — BLOCKED
- `PASS` [static] static_mesh_ops_availability
- `BLOCKED` [live] free_tet_sequence_created — mesh.create returned EXECUTION_STATE_UNKNOWN
- `NOT_RUN` [live] local_size_feature_applied — prerequisite subcase free_tet_sequence_created did not pass
- `NOT_RUN` [live] modify_and_rebuild — prerequisite subcase local_size_feature_applied did not pass
- `NOT_RUN` [live] statistics_counts_and_coverage — prerequisite subcase modify_and_rebuild did not pass
- `NOT_RUN` [live] quality_definition_and_low_quality_locations — prerequisite subcase statistics_counts_and_coverage did not pass
- `PASS` [protocol] build_success_is_not_quality_pass

### W16_T019_chainA_steady — BLOCKED
- `PASS` [fixture] analytic_reference_preregistered
- `PASS` [static] static_chain_a_ops_availability
- `BLOCKED` [live] empty_model_geometry_block — model.create returned EXECUTION_STATE_UNKNOWN
- `NOT_RUN` [live] constant_material_assigned — prerequisite subcase empty_model_geometry_block did not pass
- `NOT_RUN` [live] boundary_temperatures_and_insulation — prerequisite subcase empty_model_geometry_block did not pass
- `NOT_RUN` [live] local_mesh_built — prerequisite subcase empty_model_geometry_block did not pass
- `NOT_RUN` [live] stationary_study_and_solver — prerequisite subcase empty_model_geometry_block did not pass
- `NOT_RUN` [live] solve_produced_solution — prerequisite subcase empty_model_geometry_block did not pass
- `NOT_RUN` [live] linear_profile_relative_error_le_1e-4 — prerequisite subcase empty_model_geometry_block did not pass
- `NOT_RUN` [live] heat_flux_and_power_balance — prerequisite subcase empty_model_geometry_block did not pass
- `NOT_RUN` [live] sample_table_and_units_recorded — prerequisite subcase empty_model_geometry_block did not pass
- `NOT_RUN` [live] saved_mph_hash_recorded — prerequisite subcase empty_model_geometry_block did not pass

### W16_T019_chainB_transient — BLOCKED
- `PASS` [fixture] analytic_reference_preregistered
- `PASS` [static] static_chain_b_ops_availability
- `BLOCKED` [live] transient_study_and_initial_value — model.create returned EXECUTION_STATE_UNKNOWN
- `NOT_RUN` [live] transient_solve_produced_solution — prerequisite subcase transient_study_and_initial_value did not pass
- `NOT_RUN` [live] normalized_max_error_le_1e-3 — prerequisite subcase transient_study_and_initial_value did not pass
- `NOT_RUN` [live] time_points_and_mesh_recorded — prerequisite subcase transient_study_and_initial_value did not pass

### W16_T019_chainC_continue — BLOCKED
- `PASS` [static] static_chain_c_ops_availability
- `PASS` [fixture] user_style_model_available
- `BLOCKED` [live] target_only_modified — model.load returned EXECUTION_STATE_UNKNOWN
- `NOT_RUN` [live] non_target_nodes_preserved — prerequisite subcase target_only_modified did not pass
- `NOT_RUN` [live] manual_solver_preserved — prerequisite subcase target_only_modified did not pass
- `NOT_RUN` [live] derived_values_and_data_association_preserved — prerequisite subcase target_only_modified did not pass
- `NOT_RUN` [live] solve_after_continuation — prerequisite subcase target_only_modified did not pass

### W16_T020_solver — FAIL
- `PASS` [static] static_solver_ops_availability
- `FAIL` [live] solver_tree_read — the solver tree could not be read
- `NOT_RUN` [live] sub_feature_property_update_readback — prerequisite subcase solver_tree_read did not pass
- `FAIL` [live] solve_after_subfeature_update — the solver sub-feature property was not updated
- `NOT_RUN` [live] unknown_property_fails_accurately — prerequisite subcase solver_tree_read did not pass
- `NOT_RUN` [live] manual_solver_not_auto_overwritten — prerequisite subcase solver_tree_read did not pass

### GUARD_T010 — NOT_RUN
- `PASS` [static] static_idempotency_contract_published
- `NOT_RUN` [live] repeated_key_not_reexecuted — no scalar property was exposed for the idempotency probe
- `NOT_RUN` [live] request_hash_conflict_rejected — no scalar property was exposed for the idempotency probe
- `PASS` [live] same_tag_same_type_duplicate_rejected_or_idempotent
- `PASS` [live] same_tag_different_type_rejected

### GUARD_T035 — FAIL
- `PASS` [static] static_docs_guard_ops_executable
- `FAIL` [live] outside_workspace_source_denied — an outside-workspace documentation source was accepted or the denial was not explicit
- `FAIL` [live] symlink_outside_denied — a symlink pointing outside the workspace was accepted as a documentation source
- `FAIL` [live] outside_write_attempt_denied — an outside directory could be registered as an indexable source
- `PASS` [live] failed_denial_does_not_modify_files
- `PASS` [protocol] credential_text_not_persisted
- `FAIL` [protocol] private_paths_not_in_evidence — the run-wide scan found private absolute paths in the evidence tree

### GUARD_T038 — PASS
- `PASS` [protocol] initialize_and_tools_list_schemas
- `PASS` [protocol] error_propagation_structured
- `PASS` [protocol] registry_paging_continuation
- `PASS` [protocol] partial_failure_not_reported_as_success
- `PASS` [protocol] outer_iserror_matches_success
- `PASS` [protocol] structured_content_action_results
- `PASS` [protocol] stdio_log_isolation

### GUARD_T005 — PASS
- `PASS` [static] static_evaluation_ops_available
- `PASS` [live] user_derived_nodes_preserved
- `PASS` [live] invalid_expression_does_not_delete_nodes
- `PASS` [live] temporary_nodes_cleaned

### GUARD_T033 — FAIL
- `BLOCKED` [static] static_evaluation_policy_documented — operation_describe does not publish an evaluation policy for evaluate_expressions in this build, so the T033 policy contract cannot be inspected
- `PASS` [live] pure_read_rejects_or_isolates
- `FAIL` [live] ephemeral_mutation_recorded_and_serial — consecutive ephemeral evaluations were not both recorded
- `FAIL` [live] only_own_temporary_nodes_cleaned — the evaluation did not report which temporary nodes it owned and cleaned
