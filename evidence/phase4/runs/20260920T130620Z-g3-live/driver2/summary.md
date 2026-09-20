# Phase 4 (G3) production stdio acceptance run

- generated: 2026-09-20T13:39:53.894993Z
- run directory: `$REPO/evidence/phase4/runs/20260920T130620Z-g3-live/driver2`

| case | package | status | assertions | subcases | first finding |
| --- | --- | --- | --- | --- | --- |
| R01_LIVE | R | NOT_RUN | 6 | 8 |  |
| R03_LIVE | R | FAIL | 8 | 10 | an indexed/keyed call returned an inner success that disagrees with the outer isError flag |
| R04_LIVE | R | FAIL | 11 | 9 | a satisfied transaction did not report execution and verification status separately |
| R_READBACK | R | FAIL | 2 | 4 | roll-up of R03_LIVE: FAIL |
| W13_T006_variables | W13 | FAIL | 5 | 7 | variable.group_create returned REVISION_CONFLICT |
| W13_T015_units | W13 | FAIL | 4 | 6 | physics.create returned REVISION_CONFLICT |
| W13_T048_selection_drift | W13 | FAIL | 4 | 6 | selection.create returned REVISION_CONFLICT |
| W13_T016_2D_data | W13 | FAIL | 7 | 6 | function.create returned REVISION_CONFLICT |
| W14_T009_geometry_edit | W14 | FAIL | 4 | 7 | geometry.feature_create returned REVISION_CONFLICT |
| W14_T034_local_paths | W14 | FAIL | 7 | 5 | model.save returned REVISION_CONFLICT |
| W15_T007_selections | W15 | FAIL | 4 | 6 | physics.selection_set returned REVISION_CONFLICT |
| W15_T017_material | W15 | FAIL | 5 | 5 | the material property write was refused |
| W15_T042_license | W15 | FAIL | 6 | 5 | BLOCKED_LICENSE: BLOCKED_LICENSE |
| W16_T018_mesh | W16 | FAIL | 5 | 7 | mesh.create returned REVISION_CONFLICT |
| W16_T019_chainA_steady | W16 | FAIL | 7 | 12 | geometry.feature_create returned NODE_NOT_FOUND |
| W16_T019_chainB_transient | W16 | FAIL | 11 | 6 | geometry.feature_create returned NODE_NOT_FOUND |
| W16_T019_chainC_continue | W16 | FAIL | 5 | 7 | model.load returned MODEL_IDENTITY_MISMATCH |
| W16_T020_solver | W16 | BLOCKED | 5 | 7 | the bound model exposes no solver sequence for the requested study, so no solver tree exists to read (study.solver_generate was attempted first; see solver_gene |
| GUARD_T010 | GUARD | FAIL | 5 | 5 | a duplicate same-tag same-type feature was neither reported as existing nor rejected |
| GUARD_T035 | GUARD | PASS | 9 | 7 |  |
| GUARD_T038 | GUARD | PASS | 9 | 7 |  |
| GUARD_T005 | GUARD | PASS | 5 | 4 |  |
| GUARD_T033 | GUARD | FAIL | 5 | 4 | operation_describe does not publish an evaluation policy for evaluate_expressions in this build, so the T033 policy contract cannot be inspected |

cases: PASS 3 / FAIL 18 / BLOCKED 1 / NOT_RUN 1

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
- `NOT_RUN` [live] keyed_entry_set_and_readback — no keyed (property-group) entry property could be discovered or created on the bound model
- `NOT_RUN` [live] wrong_index_rejected_without_write — no 1-D property was exposed
- `NOT_RUN` [live] setter_noop_or_normalized_value_detected — no 1-D property was exposed
- `NOT_RUN` [live] non_target_items_unchanged — no multi-element property was exposed
- `NOT_RUN` [live] empty_or_single_element_property — the bound model exposed no empty or single-element 1-D property
- `FAIL` [protocol] envelope_consistency — an indexed/keyed call returned an inner success that disagrees with the outer isError flag

### R04_LIVE — FAIL
- `PASS` [static] static_transaction_ops_executable
- `PASS` [protocol] static_unsupported_invariant_rejected_pre_write
- `PASS` [protocol] root_node_read_refusal_is_structured
- `PASS` [live] invariant_violation_not_reported_as_pass
- `FAIL` [live] verified_transaction_status_split — a satisfied transaction did not report execution and verification status separately
- `PASS` [live] stale_revision_rejected
- `PASS` [live] cross_model_transaction_record_rejected
- `PASS` [live] recorded_pass_with_changed_live_property
- `BLOCKED` [live] checkpoint_recovery_after_failed_invariant — checkpoint creation unavailable: REVISION_CONFLICT

### R_READBACK — FAIL
- `NOT_RUN` [rollup] r01_expression_readback_status — roll-up of R01_LIVE: NOT_RUN
- `FAIL` [rollup] r03_indexed_keyed_readback_status — roll-up of R03_LIVE: FAIL
- `FAIL` [rollup] r04_invariant_readback_status — roll-up of R04_LIVE: FAIL
- `FAIL` [rollup] r_round_not_failed — an R-round readback case reported FAIL

### W13_T006_variables — FAIL
- `PASS` [static] static_variable_ops_availability
- `PASS` [static] static_legacy_variable_route_available
- `FAIL` [live] variables_two_in_one_group — variable.group_create returned REVISION_CONFLICT
- `NOT_RUN` [live] varnames_contains_modified_variable — prerequisite subcase variables_two_in_one_group did not pass
- `NOT_RUN` [live] no_bogus_name_expr_variables — prerequisite subcase variables_two_in_one_group did not pass
- `NOT_RUN` [live] expression_evaluates_after_modification — prerequisite subcase varnames_contains_modified_variable did not pass
- `NOT_RUN` [live] component_and_global_scope — the component variable evaluation did not pass; the global-scope probe was not attempted

### W13_T015_units — FAIL
- `PASS` [static] static_physics_unit_ops_availability
- `FAIL` [live] surface_source_W_per_m2 — physics.create returned REVISION_CONFLICT
- `NOT_RUN` [live] volume_source_W_per_m3 — prerequisite subcase surface_source_W_per_m2 did not pass
- `NOT_RUN` [live] coordinate_unit_m_and_mm — prerequisite subcase volume_source_W_per_m3 did not pass
- `NOT_RUN` [live] wrong_unit_warns_or_fails — prerequisite subcase coordinate_unit_m_and_mm did not pass
- `NOT_RUN` [live] no_implicit_thickness_or_absorptivity — prerequisite subcase wrong_unit_warns_or_fails did not pass

### W13_T048_selection_drift — FAIL
- `PASS` [static] static_selection_ops_availability
- `FAIL` [live] named_selection_created_and_bound — selection.create returned REVISION_CONFLICT
- `NOT_RUN` [live] geometry_revision_recorded — prerequisite subcase named_selection_created_and_bound did not pass
- `NOT_RUN` [live] revalidation_after_geometry_change — prerequisite subcase geometry_revision_recorded did not pass
- `NOT_RUN` [live] drift_stops_boundary_application — prerequisite subcase revalidation_after_geometry_change did not pass
- `NOT_RUN` [live] entity_measure_change_recorded — prerequisite subcase revalidation_after_geometry_change did not pass

### W13_T016_2D_data — FAIL
- `PASS` [static] static_function_data_ops_availability
- `PASS` [fixture] non_axisymmetric_fixture_hash_recorded
- `FAIL` [live] two_d_interpolation_angular_difference_preserved — function.create returned REVISION_CONFLICT
- `NOT_RUN` [live] radial_average_not_substituted — prerequisite subcase two_d_interpolation_angular_difference_preserved did not pass
- `NOT_RUN` [live] m_mm_coordinate_conversion — prerequisite subcase radial_average_not_substituted did not pass
- `NOT_RUN` [live] interpolation_and_extrapolation_settings_recorded — prerequisite subcase m_mm_coordinate_conversion did not pass

### W14_T009_geometry_edit — FAIL
- `PASS` [static] static_geometry_ops_availability
- `FAIL` [live] work_plane_and_array_located — geometry.feature_create returned REVISION_CONFLICT
- `NOT_RUN` [live] local_subfeature_edit_applied — prerequisite subcase work_plane_and_array_located did not pass
- `NOT_RUN` [live] left_most_object_preserved — prerequisite subcase local_subfeature_edit_applied did not pass
- `NOT_RUN` [live] count_position_spacing_quantified — prerequisite subcase left_most_object_preserved did not pass
- `NOT_RUN` [live] main_model_not_replaced — prerequisite subcase count_position_spacing_quantified did not pass
- `NOT_RUN` [live] sibling_features_unchanged — prerequisite subcase count_position_spacing_quantified did not pass

### W14_T034_local_paths — FAIL
- `PASS` [static] static_model_path_ops_availability
- `PASS` [fixture] local_spaces_and_chinese_path
- `FAIL` [live] model_save_load_roundtrip_hash — model.save returned REVISION_CONFLICT
- `BLOCKED` [live] missing_dependency_reported — the driver planted a text probe instead of a real .mph with a missing import; a live reload of a model with an unresolved external dependency must be supplied by the orchestrator
- `FAIL` [live] cad_import_license_limited — geometry.import returned REVISION_CONFLICT

### W15_T007_selections — FAIL
- `PASS` [static] static_physics_selection_ops_availability
- `FAIL` [live] physics_level_selection_set — physics.selection_set returned REVISION_CONFLICT
- `NOT_RUN` [live] feature_level_selection_set — prerequisite subcase physics_level_selection_set did not pass
- `NOT_RUN` [live] inherited_selection_not_writable — prerequisite subcase feature_level_selection_set did not pass
- `NOT_RUN` [live] named_selection_binding_readback — prerequisite subcase feature_level_selection_set did not pass
- `NOT_RUN` [live] no_invalid_parent_feature_coupling — prerequisite subcase named_selection_binding_readback did not pass

### W15_T017_material — FAIL
- `PASS` [static] static_material_ops_availability
- `FAIL` [live] k_T_Cp_T_and_rho_expressions — the material property write was refused
- `NOT_RUN` [live] anisotropic_tensor_and_coordinate_system — prerequisite subcase k_T_Cp_T_and_rho_expressions did not pass
- `NOT_RUN` [live] missing_required_property_preflight_error — prerequisite subcase anisotropic_tensor_and_coordinate_system did not pass
- `NOT_RUN` [live] material_readback_after_update — prerequisite subcase k_T_Cp_T_and_rho_expressions did not pass

### W15_T042_license — FAIL
- `PASS` [static] static_license_ops_availability
- `BLOCKED` [live] license_inspect_records_has_product — BLOCKED_LICENSE: BLOCKED_LICENSE
- `FAIL` [live] missing_product_blocks_with_blocked_license — runtime.license_inspect returned MODEL_IDENTITY_MISMATCH without product rows
- `PASS` [live] authorized_product_usable
- `PASS` [live] probe_does_not_occupy_license

### W16_T018_mesh — FAIL
- `PASS` [static] static_mesh_ops_availability
- `FAIL` [live] free_tet_sequence_created — mesh.create returned REVISION_CONFLICT
- `NOT_RUN` [live] local_size_feature_applied — prerequisite subcase free_tet_sequence_created did not pass
- `NOT_RUN` [live] modify_and_rebuild — prerequisite subcase local_size_feature_applied did not pass
- `NOT_RUN` [live] statistics_counts_and_coverage — prerequisite subcase modify_and_rebuild did not pass
- `NOT_RUN` [live] quality_definition_and_low_quality_locations — prerequisite subcase statistics_counts_and_coverage did not pass
- `PASS` [protocol] build_success_is_not_quality_pass

### W16_T019_chainA_steady — FAIL
- `PASS` [fixture] analytic_reference_preregistered
- `PASS` [static] static_chain_a_ops_availability
- `FAIL` [live] empty_model_geometry_block — geometry.feature_create returned NODE_NOT_FOUND
- `NOT_RUN` [live] boundary_temperatures_and_insulation — prerequisite subcase empty_model_geometry_block did not pass
- `NOT_RUN` [live] constant_material_assigned — prerequisite subcase boundary_temperatures_and_insulation did not pass
- `NOT_RUN` [live] local_mesh_built — prerequisite subcase constant_material_assigned did not pass
- `NOT_RUN` [live] stationary_study_and_solver — prerequisite subcase local_mesh_built did not pass
- `NOT_RUN` [live] solve_produced_solution — prerequisite subcase stationary_study_and_solver did not pass
- `NOT_RUN` [live] linear_profile_relative_error_le_1e-4 — prerequisite subcase solve_produced_solution did not pass
- `NOT_RUN` [live] heat_flux_and_power_balance — prerequisite subcase solve_produced_solution did not pass
- `NOT_RUN` [live] sample_table_and_units_recorded — prerequisite subcase solve_produced_solution did not pass
- `NOT_RUN` [live] saved_mph_hash_recorded — prerequisite subcase solve_produced_solution did not pass

### W16_T019_chainB_transient — FAIL
- `PASS` [fixture] analytic_reference_preregistered
- `PASS` [static] static_chain_b_ops_availability
- `FAIL` [live] transient_study_and_initial_value — geometry.feature_create returned NODE_NOT_FOUND
- `FAIL` [live] transient_solve_produced_solution — study.run returned REVISION_CONFLICT
- `FAIL` [live] normalized_max_error_le_1e-3 — result.sample_path returned REVISION_CONFLICT
- `FAIL` [live] time_points_and_mesh_recorded — the time points and mesh identity were not recorded together with the samples

### W16_T019_chainC_continue — FAIL
- `PASS` [static] static_chain_c_ops_availability
- `PASS` [fixture] user_style_model_available
- `FAIL` [live] target_only_modified — model.load returned MODEL_IDENTITY_MISMATCH
- `NOT_RUN` [live] non_target_nodes_preserved — prerequisite subcase target_only_modified did not pass
- `NOT_RUN` [live] manual_solver_preserved — prerequisite subcase target_only_modified did not pass
- `NOT_RUN` [live] derived_values_and_data_association_preserved — prerequisite subcase target_only_modified did not pass
- `NOT_RUN` [live] solve_after_continuation — prerequisite subcase target_only_modified did not pass

### W16_T020_solver — BLOCKED
- `PASS` [static] static_solver_ops_availability
- `BLOCKED` [live] solver_tree_read — the bound model exposes no solver sequence for the requested study, so no solver tree exists to read (study.solver_generate was attempted first; see solver_generate)
- `NOT_RUN` [live] solver_generate — prerequisite subcase solver_tree_read did not pass
- `BLOCKED` [live] sub_feature_property_update_readback — no solver sequence was readable on the bound model, so no sub-feature could be addressed
- `BLOCKED` [live] solve_after_subfeature_update — no solver sequence was readable on the bound model, so no sub-feature could be addressed
- `BLOCKED` [live] manual_solver_not_auto_overwritten — no solver sequence was readable on the bound model, so no sub-feature could be addressed
- `BLOCKED` [live] unknown_property_fails_accurately — no solver sequence was readable on the bound model, so no sub-feature could be addressed

### GUARD_T010 — FAIL
- `PASS` [static] static_idempotency_contract_published
- `NOT_RUN` [live] repeated_key_not_reexecuted — no scalar property could be discovered or created for the idempotency probe
- `NOT_RUN` [live] request_hash_conflict_rejected — no scalar property could be discovered or created for the idempotency probe
- `FAIL` [live] same_tag_same_type_duplicate_rejected_or_idempotent — a duplicate same-tag same-type feature was neither reported as existing nor rejected
- `NOT_RUN` [live] same_tag_different_type_rejected — prerequisite subcase same_tag_same_type_duplicate_rejected_or_idempotent did not pass

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

### GUARD_T005 — PASS
- `PASS` [static] static_evaluation_ops_available
- `PASS` [live] user_derived_nodes_preserved
- `PASS` [live] invalid_expression_does_not_delete_nodes
- `PASS` [live] temporary_nodes_cleaned

### GUARD_T033 — FAIL
- `BLOCKED` [static] static_evaluation_policy_documented — operation_describe does not publish an evaluation policy for evaluate_expressions in this build, so the T033 policy contract cannot be inspected
- `PASS` [live] pure_read_rejects_or_isolates
- `PASS` [live] ephemeral_mutation_recorded_and_serial
- `FAIL` [live] only_own_temporary_nodes_cleaned — the evaluation did not report which temporary nodes it owned and cleaned
