# Phase 4 (G3) production stdio acceptance run

- generated: 2026-09-20T22:53:07.103113Z
- run directory: `$REPO/evidence/phase4/runs/20260920T130620Z-g3-live/driver5b`

| case | package | status | assertions | subcases | first finding |
| --- | --- | --- | --- | --- | --- |
| R01_LIVE | R | NOT_RUN | 6 | 8 |  |
| R03_LIVE | R | NOT_RUN | 8 | 10 |  |
| R04_LIVE | R | BLOCKED | 10 | 9 | the transaction was refused with ISOLATION_PROOF_REQUIRED |
| R_READBACK | R | BLOCKED | 2 | 4 | roll-up of R04_LIVE: BLOCKED |
| W13_T006_variables | W13 | BLOCKED | 5 | 7 | variable.group_create returned remote_addr_valve receipt has no API probe |
| W13_T015_units | W13 | BLOCKED | 4 | 6 | physics.create returned remote_addr_valve receipt has no API probe |
| W13_T048_selection_drift | W13 | BLOCKED | 4 | 6 | selection.create returned remote_addr_valve receipt has no API probe |
| W13_T016_2D_data | W13 | BLOCKED | 7 | 6 | function.create returned remote_addr_valve receipt has no API probe |
| W14_T009_geometry_edit | W14 | BLOCKED | 4 | 7 | geometry.feature_create returned remote_addr_valve receipt has no API probe |
| W14_T034_local_paths | W14 | BLOCKED | 8 | 5 | the driver planted a text probe instead of a real .mph with a missing import; a live reload of a model with an unresolved external dependency must be supplied b |
| W15_T007_selections | W15 | BLOCKED | 4 | 6 | physics.selection_set returned remote_addr_valve receipt has no API probe |
| W15_T017_material | W15 | BLOCKED | 5 | 5 | the material property write was refused: ISOLATION_PROOF_REQUIRED |
| W15_T042_license | W15 | BLOCKED | 6 | 5 | BLOCKED_LICENSE: no non-checkout license answer was available for 1 of 1 requested product(s): HeatTransfer (probe ModelUtil.hasProduct refused with ENGINE_CALL |
| W16_T018_mesh | W16 | BLOCKED | 6 | 7 | mesh.create returned remote_addr_valve receipt has no API probe |
| W16_T019_chainA_steady | W16 | BLOCKED | 7 | 12 | the chain component could not be created: ISOLATION_PROOF_REQUIRED |
| W16_T019_chainB_transient | W16 | BLOCKED | 6 | 6 | the chain component could not be created: ISOLATION_PROOF_REQUIRED |
| W16_T019_chainC_continue | W16 | FAIL | 9 | 7 | the continuation update did not apply |
| W16_T020_solver | W16 | BLOCKED | 14 | 7 | the solver sub-feature {'segments': [{'collection': 'sol', 'tag': 'sol1'}, {'collection': 'feature', 'tag': 't1'}, {'collection': 'feature', 'tag': 'se1'}]} cou |
| GUARD_T010 | GUARD | NOT_RUN | 8 | 5 |  |
| GUARD_T035 | GUARD | PASS | 10 | 7 |  |
| GUARD_T038 | GUARD | PASS | 10 | 7 |  |
| GUARD_T005 | GUARD | PASS | 6 | 4 |  |
| GUARD_T033 | GUARD | BLOCKED | 7 | 4 | operation_describe does not publish an evaluation policy for evaluate_expressions in this build (no isolation or node-ownership contract), so the T033 policy co |

cases: PASS 3 / FAIL 1 / BLOCKED 16 / NOT_RUN 3

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

### R03_LIVE — NOT_RUN
- `PASS` [static] static_indexed_ops_executable
- `PASS` [protocol] static_malformed_index_and_key_rejected_pre_engine
- `NOT_RUN` [live] vector_element_readback — no 1-D property with at least two elements was exposed
- `NOT_RUN` [live] matrix_cell_or_row_readback — no 2-D property was exposed
- `NOT_RUN` [live] keyed_entry_set_and_readback — no keyed (property-group) entry property could be discovered or created on the bound model
- `NOT_RUN` [live] wrong_index_rejected_without_write — no 1-D property was exposed
- `NOT_RUN` [live] setter_noop_or_normalized_value_detected — no 1-D property was exposed
- `NOT_RUN` [live] non_target_items_unchanged — no multi-element property was exposed
- `NOT_RUN` [live] empty_or_single_element_property — the bound model exposed no empty or single-element 1-D property
- `PASS` [protocol] envelope_consistency

### R04_LIVE — BLOCKED
- `PASS` [static] static_transaction_ops_executable
- `PASS` [protocol] static_unsupported_invariant_rejected_pre_write
- `PASS` [protocol] root_node_read_refusal_is_structured
- `BLOCKED` [live] invariant_violation_not_reported_as_pass — the transaction was refused with ISOLATION_PROOF_REQUIRED
- `BLOCKED` [live] verified_transaction_status_split — a satisfied transaction did not report execution and verification status separately: ISOLATION_PROOF_REQUIRED
- `BLOCKED` [live] stale_revision_rejected — a stale expected_revision was not rejected: ISOLATION_PROOF_REQUIRED
- `NOT_RUN` [live] cross_model_transaction_record_rejected — no transaction_id was returned to re-verify
- `NOT_RUN` [live] recorded_pass_with_changed_live_property — no transaction_id was returned to re-verify
- `BLOCKED` [live] checkpoint_recovery_after_failed_invariant — checkpoint creation unavailable: ISOLATION_PROOF_REQUIRED

### R_READBACK — BLOCKED
- `NOT_RUN` [rollup] r01_expression_readback_status — roll-up of R01_LIVE: NOT_RUN
- `NOT_RUN` [rollup] r03_indexed_keyed_readback_status — roll-up of R03_LIVE: NOT_RUN
- `BLOCKED` [rollup] r04_invariant_readback_status — roll-up of R04_LIVE: BLOCKED
- `PASS` [rollup] r_round_not_failed

### W13_T006_variables — BLOCKED
- `PASS` [static] static_variable_ops_availability
- `PASS` [static] static_legacy_variable_route_available
- `BLOCKED` [live] variables_two_in_one_group — variable.group_create returned remote_addr_valve receipt has no API probe
- `NOT_RUN` [live] varnames_contains_modified_variable — prerequisite subcase variables_two_in_one_group did not pass
- `NOT_RUN` [live] no_bogus_name_expr_variables — prerequisite subcase variables_two_in_one_group did not pass
- `NOT_RUN` [live] expression_evaluates_after_modification — prerequisite subcase varnames_contains_modified_variable did not pass
- `NOT_RUN` [live] component_and_global_scope — the component variable evaluation did not pass; the global-scope probe was not attempted

### W13_T015_units — BLOCKED
- `PASS` [static] static_physics_unit_ops_availability
- `BLOCKED` [live] surface_source_W_per_m2 — physics.create returned remote_addr_valve receipt has no API probe
- `NOT_RUN` [live] volume_source_W_per_m3 — prerequisite subcase surface_source_W_per_m2 did not pass
- `NOT_RUN` [live] coordinate_unit_m_and_mm — prerequisite subcase volume_source_W_per_m3 did not pass
- `NOT_RUN` [live] wrong_unit_warns_or_fails — prerequisite subcase coordinate_unit_m_and_mm did not pass
- `NOT_RUN` [live] no_implicit_thickness_or_absorptivity — prerequisite subcase wrong_unit_warns_or_fails did not pass

### W13_T048_selection_drift — BLOCKED
- `PASS` [static] static_selection_ops_availability
- `BLOCKED` [live] named_selection_created_and_bound — selection.create returned remote_addr_valve receipt has no API probe
- `NOT_RUN` [live] geometry_revision_recorded — prerequisite subcase named_selection_created_and_bound did not pass
- `NOT_RUN` [live] revalidation_after_geometry_change — prerequisite subcase geometry_revision_recorded did not pass
- `NOT_RUN` [live] drift_stops_boundary_application — prerequisite subcase revalidation_after_geometry_change did not pass
- `NOT_RUN` [live] entity_measure_change_recorded — prerequisite subcase revalidation_after_geometry_change did not pass

### W13_T016_2D_data — BLOCKED
- `PASS` [static] static_function_data_ops_availability
- `PASS` [fixture] non_axisymmetric_fixture_hash_recorded
- `BLOCKED` [live] two_d_interpolation_angular_difference_preserved — function.create returned remote_addr_valve receipt has no API probe
- `NOT_RUN` [live] radial_average_not_substituted — prerequisite subcase two_d_interpolation_angular_difference_preserved did not pass
- `NOT_RUN` [live] m_mm_coordinate_conversion — prerequisite subcase radial_average_not_substituted did not pass
- `NOT_RUN` [live] interpolation_and_extrapolation_settings_recorded — prerequisite subcase m_mm_coordinate_conversion did not pass

### W14_T009_geometry_edit — BLOCKED
- `PASS` [static] static_geometry_ops_availability
- `BLOCKED` [live] work_plane_and_array_located — geometry.feature_create returned remote_addr_valve receipt has no API probe
- `NOT_RUN` [live] local_subfeature_edit_applied — prerequisite subcase work_plane_and_array_located did not pass
- `NOT_RUN` [live] left_most_object_preserved — prerequisite subcase local_subfeature_edit_applied did not pass
- `NOT_RUN` [live] count_position_spacing_quantified — prerequisite subcase left_most_object_preserved did not pass
- `NOT_RUN` [live] main_model_not_replaced — prerequisite subcase count_position_spacing_quantified did not pass
- `NOT_RUN` [live] sibling_features_unchanged — prerequisite subcase count_position_spacing_quantified did not pass

### W14_T034_local_paths — BLOCKED
- `PASS` [static] static_model_path_ops_availability
- `PASS` [fixture] local_spaces_and_chinese_path
- `PASS` [live] model_save_load_roundtrip_hash
- `BLOCKED` [live] missing_dependency_reported — the driver planted a text probe instead of a real .mph with a missing import; a live reload of a model with an unresolved external dependency must be supplied by the orchestrator
- `BLOCKED` [live] cad_import_license_limited — CAD import is license-limited in this installation: ISOLATION_PROOF_REQUIRED

### W15_T007_selections — BLOCKED
- `PASS` [static] static_physics_selection_ops_availability
- `BLOCKED` [live] physics_level_selection_set — physics.selection_set returned remote_addr_valve receipt has no API probe
- `NOT_RUN` [live] feature_level_selection_set — prerequisite subcase physics_level_selection_set did not pass
- `NOT_RUN` [live] inherited_selection_not_writable — prerequisite subcase feature_level_selection_set did not pass
- `NOT_RUN` [live] named_selection_binding_readback — prerequisite subcase feature_level_selection_set did not pass
- `NOT_RUN` [live] no_invalid_parent_feature_coupling — prerequisite subcase named_selection_binding_readback did not pass

### W15_T017_material — BLOCKED
- `PASS` [static] static_material_ops_availability
- `BLOCKED` [live] k_T_Cp_T_and_rho_expressions — the material property write was refused: ISOLATION_PROOF_REQUIRED
- `NOT_RUN` [live] anisotropic_tensor_and_coordinate_system — prerequisite subcase k_T_Cp_T_and_rho_expressions did not pass
- `NOT_RUN` [live] missing_required_property_preflight_error — prerequisite subcase anisotropic_tensor_and_coordinate_system did not pass
- `NOT_RUN` [live] material_readback_after_update — prerequisite subcase k_T_Cp_T_and_rho_expressions did not pass

### W15_T042_license — BLOCKED
- `PASS` [static] static_license_ops_availability
- `BLOCKED` [live] license_inspect_records_has_product — BLOCKED_LICENSE: no non-checkout license answer was available for 1 of 1 requested product(s): HeatTransfer (probe ModelUtil.hasProduct refused with ENGINE_CALL_FAILED: NoSuchMethodException: no permitted public overload for hasProduct/1). No seat was requested and no hasProduct value is reported for an unresolved product
- `BLOCKED` [live] missing_product_blocks_with_blocked_license — runtime.license_inspect returned BLOCKED_LICENSE without product rows
- `PASS` [live] authorized_product_usable
- `PASS` [live] probe_does_not_occupy_license

### W16_T018_mesh — BLOCKED
- `PASS` [static] static_mesh_ops_availability
- `BLOCKED` [live] free_tet_sequence_created — mesh.create returned remote_addr_valve receipt has no API probe
- `NOT_RUN` [live] local_size_feature_applied — prerequisite subcase free_tet_sequence_created did not pass
- `NOT_RUN` [live] modify_and_rebuild — prerequisite subcase local_size_feature_applied did not pass
- `NOT_RUN` [live] statistics_counts_and_coverage — prerequisite subcase modify_and_rebuild did not pass
- `NOT_RUN` [live] quality_definition_and_low_quality_locations — prerequisite subcase statistics_counts_and_coverage did not pass
- `PASS` [protocol] build_success_is_not_quality_pass

### W16_T019_chainA_steady — BLOCKED
- `PASS` [fixture] analytic_reference_preregistered
- `PASS` [static] static_chain_a_ops_availability
- `BLOCKED` [live] empty_model_geometry_block — the chain component could not be created: ISOLATION_PROOF_REQUIRED
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
- `BLOCKED` [live] transient_study_and_initial_value — the chain component could not be created: ISOLATION_PROOF_REQUIRED
- `NOT_RUN` [live] transient_solve_produced_solution — prerequisite subcase transient_study_and_initial_value did not pass
- `NOT_RUN` [live] normalized_max_error_le_1e-3 — prerequisite subcase transient_study_and_initial_value did not pass
- `NOT_RUN` [live] time_points_and_mesh_recorded — prerequisite subcase transient_study_and_initial_value did not pass

### W16_T019_chainC_continue — FAIL
- `PASS` [static] static_chain_c_ops_availability
- `PASS` [fixture] user_style_model_available
- `PASS` [live] target_only_modified
- `FAIL` [live] non_target_nodes_preserved — the continuation update did not apply
- `NOT_RUN` [live] manual_solver_preserved — prerequisite subcase non_target_nodes_preserved did not pass
- `NOT_RUN` [live] derived_values_and_data_association_preserved — prerequisite subcase manual_solver_preserved did not pass
- `NOT_RUN` [live] solve_after_continuation — prerequisite subcase derived_values_and_data_association_preserved did not pass

### W16_T020_solver — BLOCKED
- `PASS` [static] static_solver_ops_availability
- `PASS` [live] study_target_read
- `PASS` [live] solver_tree_read
- `BLOCKED` [live] sub_feature_property_update_readback — the solver sub-feature {'segments': [{'collection': 'sol', 'tag': 'sol1'}, {'collection': 'feature', 'tag': 't1'}, {'collection': 'feature', 'tag': 'se1'}]} could not be updated: ISOLATION_PROOF_REQUIRED
- `BLOCKED` [live] solve_after_subfeature_update — the solver sub-feature property was not updated, so the solve was not attempted
- `PASS` [live] unknown_property_fails_accurately
- `BLOCKED` [live] manual_solver_not_auto_overwritten — the manual-configuration probe could not be observed because the call was blocked

### GUARD_T010 — NOT_RUN
- `PASS` [static] static_idempotency_contract_published
- `NOT_RUN` [live] repeated_key_not_reexecuted — no scalar property could be discovered or created for the idempotency probe
- `NOT_RUN` [live] request_hash_conflict_rejected — no scalar property could be discovered or created for the idempotency probe
- `PASS` [live] same_tag_same_type_duplicate_rejected_or_idempotent
- `PASS` [live] same_tag_different_type_rejected

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

### GUARD_T033 — BLOCKED
- `BLOCKED` [static] static_evaluation_policy_documented — operation_describe does not publish an evaluation policy for evaluate_expressions in this build (no isolation or node-ownership contract), so the T033 policy contract cannot be inspected
- `PASS` [live] pure_read_rejects_or_isolates
- `PASS` [live] ephemeral_mutation_recorded_and_serial
- `BLOCKED` [live] only_own_temporary_nodes_cleaned — the build reports ephemeral_mutation=true but publishes no temporary-node ownership inventory, so node ownership cannot be established (reported keys: count, datetime, ephemeral_mutation, evaluation_policy, label, log_path, max_result_size_ignored, operations_path, results, server, timestamp, tool)

## Unknown-job ledger (driver side)

- gate refusals: 0 (none)
- recorded UNKNOWN jobs: 3 (released: 3, unreleased: 0)
- calls that stayed refused after the release path ran: 0

- `8f540023-7edc-4fc7-91c2-57996512e985` (create_feature): released=True, attempts=1, reconciled_quiescent=True, status=UNKNOWN
- `604e893a-a478-4e5e-88be-168f5d6cc0b6` (create_feature): released=True, attempts=1, reconciled_quiescent=True, status=UNKNOWN
- `bc7fc75a-46b5-4dd8-9744-16fff513cb10` (create_feature): released=True, attempts=1, reconciled_quiescent=True, status=UNKNOWN
