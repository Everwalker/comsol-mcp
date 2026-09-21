# Phase 4 (G3) production stdio acceptance run

- generated: 2026-09-20T22:55:34.264009Z
- run directory: `$REPO/evidence/phase4/runs/20260920T130620Z-g3-live/driver5c`

| case | package | status | assertions | subcases | first finding |
| --- | --- | --- | --- | --- | --- |
| R01_LIVE | R | BLOCKED | 12 | 8 | the negative probe was refused with an unknown engine state (EXECUTION_STATE_UNKNOWN); whether the unsupported write reached the engine was not established |
| R03_LIVE | R | BLOCKED | 14 | 10 | an out-of-range index neither was rejected cleanly nor reported an explicit expansion |
| R04_LIVE | R | PASS | 14 | 9 |  |
| R_READBACK | R | BLOCKED | 3 | 4 | roll-up of R01_LIVE: BLOCKED |
| W13_T006_variables | W13 | FAIL | 8 | 7 | variable.set returned group must be a NodePath object |
| W13_T015_units | W13 | FAIL | 5 | 6 | physics.create returned geometry 'geom1' does not exist in component 'comp1' |
| W13_T048_selection_drift | W13 | FAIL | 5 | 6 | selection.create returned definition has fields that are neither assignment fields nor documented Box properties: ['box'] |
| W13_T016_2D_data | W13 | FAIL | 8 | 6 | function.create returned definition has unsupported fields: ['data_unit', 'extrapolation', 'interpolation'] |
| W14_T009_geometry_edit | W14 | FAIL | 5 | 7 | geometry.feature_create returned could not resolve node path segment geom:geom1 |
| W14_T034_local_paths | W14 | FAIL | 8 | 5 | the driver planted a text probe instead of a real .mph with a missing import; a live reload of a model with an unresolved external dependency must be supplied b |
| W15_T007_selections | W15 | FAIL | 5 | 6 | physics.selection_set returned could not resolve node path segment physics:ht |
| W15_T017_material | W15 | FAIL | 6 | 5 | the material property write was refused |
| W15_T042_license | W15 | BLOCKED | 7 | 5 | BLOCKED_LICENSE: no non-checkout license answer was available for 1 of 1 requested product(s): HeatTransfer (probe ModelUtil.hasProduct refused with ENGINE_CALL |
| W16_T018_mesh | W16 | FAIL | 12 | 7 | mesh.statistics did not report element counts |
| W16_T019_chainA_steady | W16 | BLOCKED | 19 | 12 | the material node exists but its property definition was refused by the product (property expects array rank 1, received 2) |
| W16_T019_chainB_transient | W16 | FAIL | 12 | 6 | physics.feature_create init1 was refused |
| W16_T019_chainC_continue | W16 | FAIL | 12 | 7 | the manual solver configuration changed across the continuation |
| W16_T020_solver | W16 | BLOCKED | 10 | 8 | study.list returned backend execution failed; inspect worker evidence |
| GUARD_T010 | GUARD | NOT_RUN | 8 | 5 |  |
| GUARD_T035 | GUARD | BLOCKED | 11 | 7 | an outside-workspace documentation source was accepted or the denial was not explicit: the control plane refused the call with an unknown engine state (EXECUTIO |
| GUARD_T038 | GUARD | BLOCKED | 11 | 7 | the partial-failure probe was refused with an unknown engine state (EXECUTION_STATE_UNKNOWN) before the operation ran, so the finish-mode refusal was not observ |
| GUARD_T005 | GUARD | BLOCKED | 4 | 4 | required operation(s) not executable in this build: evaluate_expressions(implementation_status=EXECUTION_STATE_UNKNOWN) |
| GUARD_T033 | GUARD | BLOCKED | 4 | 4 | operation_describe does not publish an evaluation policy for evaluate_expressions in this build (no isolation or node-ownership contract), so the T033 policy co |

cases: PASS 1 / FAIL 11 / BLOCKED 10 / NOT_RUN 1

## Subcase detail

### R01_LIVE — BLOCKED
- `PASS` [static] static_expression_readback_ops_executable
- `PASS` [protocol] static_malformed_typed_value_rejected_pre_engine
- `PASS` [live] expression_string_same_text_verified
- `NOT_RUN` [live] expression_array_or_matrix_verified — the bound model exposed no string array/matrix property
- `NOT_RUN` [live] unit_expression_text_preserved — no property exposed authoritative unit metadata; unit-bearing text was not exercised
- `BLOCKED` [live] incompatible_kind_or_unit_rejected_before_write — the negative probe was refused with an unknown engine state (EXECUTION_STATE_UNKNOWN); whether the unsupported write reached the engine was not established
- `BLOCKED` [live] non_matching_readback_not_silently_accepted — the probe write was refused by the product (EXECUTION_STATE_UNKNOWN: legacy callback raised after write dispatch)
- `PASS` [live] nonfinite_text_strictly_rejected

### R03_LIVE — BLOCKED
- `PASS` [static] static_indexed_ops_executable
- `PASS` [protocol] static_malformed_index_and_key_rejected_pre_engine
- `PASS` [live] vector_element_readback
- `NOT_RUN` [live] matrix_cell_or_row_readback — no 2-D property was exposed
- `NOT_RUN` [live] keyed_entry_set_and_readback — no keyed (property-group) entry property could be discovered or created on the bound model
- `BLOCKED` [live] wrong_index_rejected_without_write — an out-of-range index neither was rejected cleanly nor reported an explicit expansion
- `PASS` [live] setter_noop_or_normalized_value_detected
- `BLOCKED` [live] non_target_items_unchanged — a non-target element changed or the write was not verified
- `NOT_RUN` [live] empty_or_single_element_property — the bound model exposed no empty or single-element 1-D property
- `PASS` [protocol] envelope_consistency

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

### R_READBACK — BLOCKED
- `BLOCKED` [rollup] r01_expression_readback_status — roll-up of R01_LIVE: BLOCKED
- `BLOCKED` [rollup] r03_indexed_keyed_readback_status — roll-up of R03_LIVE: BLOCKED
- `PASS` [rollup] r04_invariant_readback_status
- `PASS` [rollup] r_round_not_failed

### W13_T006_variables — FAIL
- `PASS` [static] static_variable_ops_availability
- `PASS` [static] static_legacy_variable_route_available
- `PASS` [live] variables_two_in_one_group
- `FAIL` [live] varnames_contains_modified_variable — variable.set returned group must be a NodePath object
- `FAIL` [live] no_bogus_name_expr_variables — varnames readback was [] instead of the two requested variables
- `NOT_RUN` [live] expression_evaluates_after_modification — prerequisite subcase varnames_contains_modified_variable did not pass
- `NOT_RUN` [live] component_and_global_scope — the component variable evaluation did not pass; the global-scope probe was not attempted

### W13_T015_units — FAIL
- `PASS` [static] static_physics_unit_ops_availability
- `FAIL` [live] surface_source_W_per_m2 — physics.create returned geometry 'geom1' does not exist in component 'comp1'
- `NOT_RUN` [live] volume_source_W_per_m3 — prerequisite subcase surface_source_W_per_m2 did not pass
- `NOT_RUN` [live] coordinate_unit_m_and_mm — prerequisite subcase volume_source_W_per_m3 did not pass
- `NOT_RUN` [live] wrong_unit_warns_or_fails — prerequisite subcase coordinate_unit_m_and_mm did not pass
- `NOT_RUN` [live] no_implicit_thickness_or_absorptivity — prerequisite subcase wrong_unit_warns_or_fails did not pass

### W13_T048_selection_drift — FAIL
- `PASS` [static] static_selection_ops_availability
- `FAIL` [live] named_selection_created_and_bound — selection.create returned definition has fields that are neither assignment fields nor documented Box properties: ['box']
- `NOT_RUN` [live] geometry_revision_recorded — prerequisite subcase named_selection_created_and_bound did not pass
- `NOT_RUN` [live] revalidation_after_geometry_change — prerequisite subcase geometry_revision_recorded did not pass
- `NOT_RUN` [live] drift_stops_boundary_application — prerequisite subcase revalidation_after_geometry_change did not pass
- `NOT_RUN` [live] entity_measure_change_recorded — prerequisite subcase revalidation_after_geometry_change did not pass

### W13_T016_2D_data — FAIL
- `PASS` [static] static_function_data_ops_availability
- `PASS` [fixture] non_axisymmetric_fixture_hash_recorded
- `FAIL` [live] two_d_interpolation_angular_difference_preserved — function.create returned definition has unsupported fields: ['data_unit', 'extrapolation', 'interpolation']
- `NOT_RUN` [live] radial_average_not_substituted — prerequisite subcase two_d_interpolation_angular_difference_preserved did not pass
- `NOT_RUN` [live] m_mm_coordinate_conversion — prerequisite subcase radial_average_not_substituted did not pass
- `NOT_RUN` [live] interpolation_and_extrapolation_settings_recorded — prerequisite subcase m_mm_coordinate_conversion did not pass

### W14_T009_geometry_edit — FAIL
- `PASS` [static] static_geometry_ops_availability
- `FAIL` [live] work_plane_and_array_located — geometry.feature_create returned could not resolve node path segment geom:geom1
- `NOT_RUN` [live] local_subfeature_edit_applied — prerequisite subcase work_plane_and_array_located did not pass
- `NOT_RUN` [live] left_most_object_preserved — prerequisite subcase local_subfeature_edit_applied did not pass
- `NOT_RUN` [live] count_position_spacing_quantified — prerequisite subcase left_most_object_preserved did not pass
- `NOT_RUN` [live] main_model_not_replaced — prerequisite subcase count_position_spacing_quantified did not pass
- `NOT_RUN` [live] sibling_features_unchanged — prerequisite subcase count_position_spacing_quantified did not pass

### W14_T034_local_paths — FAIL
- `PASS` [static] static_model_path_ops_availability
- `PASS` [fixture] local_spaces_and_chinese_path
- `PASS` [live] model_save_load_roundtrip_hash
- `BLOCKED` [live] missing_dependency_reported — the driver planted a text probe instead of a real .mph with a missing import; a live reload of a model with an unresolved external dependency must be supplied by the orchestrator
- `FAIL` [live] cad_import_license_limited — geometry.import returned INVALID_REQUEST

### W15_T007_selections — FAIL
- `PASS` [static] static_physics_selection_ops_availability
- `FAIL` [live] physics_level_selection_set — physics.selection_set returned could not resolve node path segment physics:ht
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

### W15_T042_license — BLOCKED
- `PASS` [static] static_license_ops_availability
- `BLOCKED` [live] license_inspect_records_has_product — BLOCKED_LICENSE: no non-checkout license answer was available for 1 of 1 requested product(s): HeatTransfer (probe ModelUtil.hasProduct refused with ENGINE_CALL_FAILED: NoSuchMethodException: no permitted public overload for hasProduct/1). No seat was requested and no hasProduct value is reported for an unresolved product
- `BLOCKED` [live] missing_product_blocks_with_blocked_license — runtime.license_inspect returned BLOCKED_LICENSE without product rows
- `PASS` [live] authorized_product_usable
- `PASS` [live] probe_does_not_occupy_license

### W16_T018_mesh — FAIL
- `PASS` [static] static_mesh_ops_availability
- `PASS` [live] free_tet_sequence_created
- `PASS` [live] local_size_feature_applied
- `PASS` [live] modify_and_rebuild
- `FAIL` [live] statistics_counts_and_coverage — mesh.statistics did not report element counts
- `NOT_RUN` [live] quality_definition_and_low_quality_locations — prerequisite subcase statistics_counts_and_coverage did not pass
- `PASS` [protocol] build_success_is_not_quality_pass

### W16_T019_chainA_steady — BLOCKED
- `PASS` [fixture] analytic_reference_preregistered
- `PASS` [static] static_chain_a_ops_availability
- `PASS` [live] empty_model_geometry_block
- `BLOCKED` [live] constant_material_assigned — the material node exists but its property definition was refused by the product (property expects array rank 1, received 2)
- `BLOCKED` [live] boundary_temperatures_and_insulation — the engine already carries a feature at tag 'ins1' but did not report a type or label that identifies it as an insulation feature (None), so the insulation part of this line was not established
- `PASS` [live] local_mesh_built
- `PASS` [live] stationary_study_and_solver
- `NOT_RUN` [live] solve_produced_solution — a prerequisite subcase did not pass (constant_material_assigned=BLOCKED; boundary_temperatures_and_insulation=BLOCKED)
- `NOT_RUN` [live] linear_profile_relative_error_le_1e-4 — a prerequisite subcase did not pass (constant_material_assigned=BLOCKED; boundary_temperatures_and_insulation=BLOCKED)
- `NOT_RUN` [live] heat_flux_and_power_balance — a prerequisite subcase did not pass (constant_material_assigned=BLOCKED; boundary_temperatures_and_insulation=BLOCKED)
- `NOT_RUN` [live] sample_table_and_units_recorded — a prerequisite subcase did not pass (constant_material_assigned=BLOCKED; boundary_temperatures_and_insulation=BLOCKED)
- `NOT_RUN` [live] saved_mph_hash_recorded — a prerequisite subcase did not pass (constant_material_assigned=BLOCKED; boundary_temperatures_and_insulation=BLOCKED)

### W16_T019_chainB_transient — FAIL
- `PASS` [fixture] analytic_reference_preregistered
- `PASS` [static] static_chain_b_ops_availability
- `FAIL` [live] transient_study_and_initial_value — physics.feature_create init1 was refused
- `NOT_RUN` [live] transient_solve_produced_solution — prerequisite subcase transient_study_and_initial_value did not pass
- `NOT_RUN` [live] normalized_max_error_le_1e-3 — prerequisite subcase transient_study_and_initial_value did not pass
- `NOT_RUN` [live] time_points_and_mesh_recorded — prerequisite subcase transient_study_and_initial_value did not pass

### W16_T019_chainC_continue — FAIL
- `PASS` [static] static_chain_c_ops_availability
- `PASS` [fixture] user_style_model_available
- `PASS` [live] target_only_modified
- `PASS` [live] non_target_nodes_preserved
- `FAIL` [live] manual_solver_preserved — the manual solver configuration changed across the continuation
- `NOT_RUN` [live] derived_values_and_data_association_preserved — prerequisite subcase manual_solver_preserved did not pass
- `NOT_RUN` [live] solve_after_continuation — prerequisite subcase derived_values_and_data_association_preserved did not pass

### W16_T020_solver — BLOCKED
- `PASS` [static] static_solver_ops_availability
- `BLOCKED` [live] study_target_read — study.list returned backend execution failed; inspect worker evidence
- `BLOCKED` [live] study_target_create — study.create returned reconcile unfinished engine work before new operations
- `BLOCKED` [live] solver_tree_read — no study could be established on the bound model, so no solver sequence can exist
- `BLOCKED` [live] sub_feature_property_update_readback — no study could be established on the bound model, so no solver sequence can exist
- `BLOCKED` [live] solve_after_subfeature_update — no study could be established on the bound model, so no solver sequence can exist
- `BLOCKED` [live] unknown_property_fails_accurately — no study could be established on the bound model, so no solver sequence can exist
- `BLOCKED` [live] manual_solver_not_auto_overwritten — no study could be established on the bound model, so no solver sequence can exist

### GUARD_T010 — NOT_RUN
- `PASS` [static] static_idempotency_contract_published
- `NOT_RUN` [live] repeated_key_not_reexecuted — no scalar property could be discovered or created for the idempotency probe
- `NOT_RUN` [live] request_hash_conflict_rejected — no scalar property could be discovered or created for the idempotency probe
- `PASS` [live] same_tag_same_type_duplicate_rejected_or_idempotent
- `PASS` [live] same_tag_different_type_rejected

### GUARD_T035 — BLOCKED
- `PASS` [static] static_docs_guard_ops_executable
- `BLOCKED` [live] outside_workspace_source_denied — an outside-workspace documentation source was accepted or the denial was not explicit: the control plane refused the call with an unknown engine state (EXECUTION_STATE_UNKNOWN) before the guard could run, so neither a denial nor an acceptance was established
- `BLOCKED` [live] symlink_outside_denied — a symlink pointing outside the workspace was accepted as a documentation source: the control plane refused the call with an unknown engine state (EXECUTION_STATE_UNKNOWN) before the guard could run, so neither a denial nor an acceptance was established
- `BLOCKED` [live] outside_write_attempt_denied — an outside directory could be registered as an indexable source: the control plane refused the call with an unknown engine state (EXECUTION_STATE_UNKNOWN) before the guard could run, so neither a denial nor an acceptance was established
- `PASS` [live] failed_denial_does_not_modify_files
- `PASS` [protocol] credential_text_not_persisted
- `PASS` [protocol] private_paths_not_in_evidence

### GUARD_T038 — BLOCKED
- `PASS` [protocol] initialize_and_tools_list_schemas
- `PASS` [protocol] error_propagation_structured
- `PASS` [protocol] registry_paging_continuation
- `BLOCKED` [protocol] partial_failure_not_reported_as_success — the partial-failure probe was refused with an unknown engine state (EXECUTION_STATE_UNKNOWN) before the operation ran, so the finish-mode refusal was not observed
- `PASS` [protocol] outer_iserror_matches_success
- `PASS` [protocol] structured_content_action_results
- `PASS` [protocol] stdio_log_isolation

### GUARD_T005 — BLOCKED
- `BLOCKED` [static] static_evaluation_ops_available — required operation(s) not executable in this build: evaluate_expressions(implementation_status=EXECUTION_STATE_UNKNOWN)
- `BLOCKED` [live] user_derived_nodes_preserved — required operation(s) not executable in this build: evaluate_expressions(implementation_status=EXECUTION_STATE_UNKNOWN)
- `BLOCKED` [live] invalid_expression_does_not_delete_nodes — required operation(s) not executable in this build: evaluate_expressions(implementation_status=EXECUTION_STATE_UNKNOWN)
- `BLOCKED` [live] temporary_nodes_cleaned — required operation(s) not executable in this build: evaluate_expressions(implementation_status=EXECUTION_STATE_UNKNOWN)

### GUARD_T033 — BLOCKED
- `BLOCKED` [static] static_evaluation_policy_documented — operation_describe does not publish an evaluation policy for evaluate_expressions in this build (no isolation or node-ownership contract), so the T033 policy contract cannot be inspected
- `BLOCKED` [live] pure_read_rejects_or_isolates — required operation(s) not executable in this build: evaluate_expressions(implementation_status=EXECUTION_STATE_UNKNOWN)
- `BLOCKED` [live] ephemeral_mutation_recorded_and_serial — required operation(s) not executable in this build: evaluate_expressions(implementation_status=EXECUTION_STATE_UNKNOWN)
- `BLOCKED` [live] only_own_temporary_nodes_cleaned — required operation(s) not executable in this build: evaluate_expressions(implementation_status=EXECUTION_STATE_UNKNOWN)

## Unknown-job ledger (driver side)

- gate refusals: 24 (create_feature×3, operation_call×21)
- recorded UNKNOWN jobs: 9 (released: 8, unreleased: 1)
- calls that stayed refused after the release path ran: 24

- `98f1caa6-9318-4120-8466-1718f1e0a27b` (operation_call): released=True, attempts=1, reconciled_quiescent=True, status=UNKNOWN
- `280c8c75-1611-4563-9f84-b5a1812d26e9` (operation_call): released=True, attempts=1, reconciled_quiescent=True, status=UNKNOWN
- `c507c1b0-cb20-46d6-abdd-78e1173941cd` (operation_call): released=True, attempts=1, reconciled_quiescent=True, status=UNKNOWN
- `c940c5ef-1004-4842-b18e-ef57b2928cea` (operation_call): released=True, attempts=1, reconciled_quiescent=True, status=UNKNOWN
- `e619cd27-0e1a-4153-b7a9-011104165624` (operation_call): released=True, attempts=1, reconciled_quiescent=True, status=UNKNOWN
- `38fdd74d-bed8-42cc-a138-8b6476dd6419` (operation_call): released=True, attempts=1, reconciled_quiescent=True, status=UNKNOWN
- `a79f3310-4416-4db8-8099-7f7269611f02` (operation_call): released=True, attempts=1, reconciled_quiescent=True, status=UNKNOWN
- `89e6a314-a6ee-4e54-aa24-da82a33e4677` (operation_call): released=True, attempts=1, reconciled_quiescent=True, status=UNKNOWN
- `f4d2fc48-1957-49e5-b54e-cb261cbf2ca0` (operation_call): released=False, attempts=8, reconciled_quiescent=False, status=UNKNOWN
