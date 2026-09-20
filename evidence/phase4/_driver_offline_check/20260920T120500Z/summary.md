# Phase 4 (G3) production stdio acceptance run

- generated: 2026-09-20T11:48:39.605251Z
- run directory: `$REPO/evidence/phase4/_driver_offline_check/20260920T120500Z`

| case | package | status | assertions | subcases | first finding |
| --- | --- | --- | --- | --- | --- |
| R01_LIVE | R | NOT_RUN | 5 | 8 |  |
| R03_LIVE | R | NOT_RUN | 5 | 10 |  |
| R04_LIVE | R | NOT_RUN | 5 | 8 |  |
| R_READBACK | R | NOT_RUN | 2 | 4 |  |
| W13_T006_variables | W13 | NOT_RUN | 4 | 7 |  |
| W13_T015_units | W13 | BLOCKED | 3 | 6 | required operation(s) not executable in this build: physics.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_create(implementation_status |
| W13_T048_selection_drift | W13 | BLOCKED | 3 | 6 | required operation(s) not executable in this build: physics.selection_set(implementation_status=PROPOSED_NOT_IMPLEMENTED) |
| W13_T016_2D_data | W13 | NOT_RUN | 5 | 6 |  |
| W14_T009_geometry_edit | W14 | BLOCKED | 3 | 7 | required operation(s) not executable in this build: geometry.workplane_edit(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.array_create(implementatio |
| W14_T034_local_paths | W14 | BLOCKED | 5 | 5 | required operation(s) not executable in this build: model.save(implementation_status=PROPOSED_NOT_IMPLEMENTED), model.load(implementation_status=PROPOSED_NOT_IM |
| W15_T007_selections | W15 | BLOCKED | 3 | 6 | required operation(s) not executable in this build: physics.selection_set(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_create(implementation |
| W15_T017_material | W15 | BLOCKED | 3 | 5 | required operation(s) not executable in this build: material.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), material.set_properties(implementation_stat |
| W15_T042_license | W15 | BLOCKED | 3 | 5 | required operation(s) not executable in this build: runtime.license_inspect(implementation_status=PROPOSED_NOT_IMPLEMENTED) |
| W16_T018_mesh | W16 | BLOCKED | 4 | 7 | required operation(s) not executable in this build: mesh.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), mesh.feature_create(implementation_status=PROPO |
| W16_T019_chainA_steady | W16 | BLOCKED | 5 | 12 | required operation(s) not executable in this build: model.create(implementation_status=unknown), geometry.feature_create(implementation_status=PROPOSED_NOT_IMPL |
| W16_T019_chainB_transient | W16 | BLOCKED | 4 | 6 | required operation(s) not executable in this build: model.create(implementation_status=unknown), geometry.feature_create(implementation_status=unknown), geometr |
| W16_T019_chainC_continue | W16 | BLOCKED | 4 | 7 | required operation(s) not executable in this build: model.load(implementation_status=unknown), node.find(implementation_status=unknown), node.property_get(imple |
| W16_T020_solver | W16 | BLOCKED | 3 | 6 | required operation(s) not executable in this build: solver.list(implementation_status=PROPOSED_NOT_IMPLEMENTED), solver.inspect(implementation_status=PROPOSED_N |
| GUARD_T010 | GUARD | NOT_RUN | 3 | 5 |  |
| GUARD_T035 | GUARD | BLOCKED | 9 | 6 | required operation(s) not executable in this build: docs.index(implementation_status=unknown), docs.search(implementation_status=unknown) |
| GUARD_T038 | GUARD | PASS | 9 | 7 |  |
| GUARD_T005 | GUARD | NOT_RUN | 3 | 4 |  |
| GUARD_T033 | GUARD | BLOCKED | 3 | 4 | operation_describe does not publish an evaluation policy for evaluate_expressions in this build, so the T033 policy contract cannot be inspected |

cases: PASS 1 / FAIL 0 / BLOCKED 14 / NOT_RUN 8

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

### R04_LIVE — NOT_RUN
- `PASS` [static] static_transaction_ops_executable
- `PASS` [protocol] static_unsupported_invariant_rejected_pre_write
- `NOT_RUN` [live] invariant_violation_not_reported_as_pass — offline run: --live was not supplied
- `NOT_RUN` [live] verified_transaction_status_split — offline run: --live was not supplied
- `NOT_RUN` [live] stale_revision_rejected — offline run: --live was not supplied
- `NOT_RUN` [live] cross_model_transaction_record_rejected — offline run: --live was not supplied
- `NOT_RUN` [live] recorded_pass_with_changed_live_property — offline run: --live was not supplied
- `NOT_RUN` [live] checkpoint_recovery_after_failed_invariant — offline run: --live was not supplied

### R_READBACK — NOT_RUN
- `NOT_RUN` [rollup] r01_expression_readback_status — roll-up of R01_LIVE: NOT_RUN
- `NOT_RUN` [rollup] r03_indexed_keyed_readback_status — roll-up of R03_LIVE: NOT_RUN
- `NOT_RUN` [rollup] r04_invariant_readback_status — roll-up of R04_LIVE: NOT_RUN
- `PASS` [rollup] r_round_not_failed

### W13_T006_variables — NOT_RUN
- `PASS` [static] static_variable_ops_availability
- `PASS` [static] static_legacy_variable_route_available
- `NOT_RUN` [live] variables_two_in_one_group — offline run: --live was not supplied
- `NOT_RUN` [live] varnames_contains_modified_variable — offline run: --live was not supplied
- `NOT_RUN` [live] no_bogus_name_expr_variables — offline run: --live was not supplied
- `NOT_RUN` [live] expression_evaluates_after_modification — offline run: --live was not supplied
- `NOT_RUN` [live] component_and_global_scope — offline run: --live was not supplied

### W13_T015_units — BLOCKED
- `BLOCKED` [static] static_physics_unit_ops_availability — required operation(s) not executable in this build: physics.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_update(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.validate(implementation_status=PROPOSED_NOT_IMPLEMENTED)
- `BLOCKED` [live] surface_source_W_per_m2 — required operation(s) not executable in this build: physics.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_update(implementation_status=PROPOSED_NOT_IMPLEMENTED)
- `BLOCKED` [live] volume_source_W_per_m3 — required operation(s) not executable in this build: physics.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_update(implementation_status=PROPOSED_NOT_IMPLEMENTED)
- `BLOCKED` [live] coordinate_unit_m_and_mm — required operation(s) not executable in this build: physics.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_update(implementation_status=PROPOSED_NOT_IMPLEMENTED)
- `BLOCKED` [live] wrong_unit_warns_or_fails — required operation(s) not executable in this build: physics.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_update(implementation_status=PROPOSED_NOT_IMPLEMENTED)
- `BLOCKED` [live] no_implicit_thickness_or_absorptivity — required operation(s) not executable in this build: physics.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_update(implementation_status=PROPOSED_NOT_IMPLEMENTED)

### W13_T048_selection_drift — BLOCKED
- `BLOCKED` [static] static_selection_ops_availability — required operation(s) not executable in this build: physics.selection_set(implementation_status=PROPOSED_NOT_IMPLEMENTED)
- `BLOCKED` [live] named_selection_created_and_bound — required operation(s) not executable in this build: physics.selection_set(implementation_status=PROPOSED_NOT_IMPLEMENTED)
- `BLOCKED` [live] geometry_revision_recorded — required operation(s) not executable in this build: physics.selection_set(implementation_status=PROPOSED_NOT_IMPLEMENTED)
- `BLOCKED` [live] revalidation_after_geometry_change — required operation(s) not executable in this build: physics.selection_set(implementation_status=PROPOSED_NOT_IMPLEMENTED)
- `BLOCKED` [live] drift_stops_boundary_application — required operation(s) not executable in this build: physics.selection_set(implementation_status=PROPOSED_NOT_IMPLEMENTED)
- `BLOCKED` [live] entity_measure_change_recorded — required operation(s) not executable in this build: physics.selection_set(implementation_status=PROPOSED_NOT_IMPLEMENTED)

### W13_T016_2D_data — NOT_RUN
- `PASS` [static] static_function_data_ops_availability
- `PASS` [fixture] non_axisymmetric_fixture_hash_recorded
- `NOT_RUN` [live] two_d_interpolation_angular_difference_preserved — offline run: --live was not supplied
- `NOT_RUN` [live] radial_average_not_substituted — offline run: --live was not supplied
- `NOT_RUN` [live] m_mm_coordinate_conversion — offline run: --live was not supplied
- `NOT_RUN` [live] interpolation_and_extrapolation_settings_recorded — offline run: --live was not supplied

### W14_T009_geometry_edit — BLOCKED
- `BLOCKED` [static] static_geometry_ops_availability — required operation(s) not executable in this build: geometry.workplane_edit(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.array_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.feature_update(implementation_status=unknown), geometry.build(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.measure(implementation_status=PROPOSED_NOT_IMPLEMENTED), node.find(implementation_status=unknown), node.property_get(implementation_status=unknown)
- `BLOCKED` [live] work_plane_and_array_located — required operation(s) not executable in this build: geometry.workplane_edit(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.array_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.feature_update(implementation_status=unknown), geometry.build(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.measure(implementation_status=PROPOSED_NOT_IMPLEMENTED), node.find(implementation_status=unknown), node.property_get(implementation_status=unknown)
- `BLOCKED` [live] local_subfeature_edit_applied — required operation(s) not executable in this build: geometry.workplane_edit(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.array_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.feature_update(implementation_status=unknown), geometry.build(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.measure(implementation_status=PROPOSED_NOT_IMPLEMENTED), node.find(implementation_status=unknown), node.property_get(implementation_status=unknown)
- `BLOCKED` [live] left_most_object_preserved — required operation(s) not executable in this build: geometry.workplane_edit(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.array_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.feature_update(implementation_status=unknown), geometry.build(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.measure(implementation_status=PROPOSED_NOT_IMPLEMENTED), node.find(implementation_status=unknown), node.property_get(implementation_status=unknown)
- `BLOCKED` [live] count_position_spacing_quantified — required operation(s) not executable in this build: geometry.workplane_edit(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.array_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.feature_update(implementation_status=unknown), geometry.build(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.measure(implementation_status=PROPOSED_NOT_IMPLEMENTED), node.find(implementation_status=unknown), node.property_get(implementation_status=unknown)
- `BLOCKED` [live] main_model_not_replaced — required operation(s) not executable in this build: geometry.workplane_edit(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.array_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.feature_update(implementation_status=unknown), geometry.build(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.measure(implementation_status=PROPOSED_NOT_IMPLEMENTED), node.find(implementation_status=unknown), node.property_get(implementation_status=unknown)
- `BLOCKED` [live] sibling_features_unchanged — required operation(s) not executable in this build: geometry.workplane_edit(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.array_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.feature_update(implementation_status=unknown), geometry.build(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.measure(implementation_status=PROPOSED_NOT_IMPLEMENTED), node.find(implementation_status=unknown), node.property_get(implementation_status=unknown)

### W14_T034_local_paths — BLOCKED
- `BLOCKED` [static] static_model_path_ops_availability — required operation(s) not executable in this build: model.save(implementation_status=PROPOSED_NOT_IMPLEMENTED), model.load(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.import(implementation_status=PROPOSED_NOT_IMPLEMENTED)
- `PASS` [fixture] local_spaces_and_chinese_path
- `BLOCKED` [live] model_save_load_roundtrip_hash — required operation(s) not executable in this build: model.save(implementation_status=PROPOSED_NOT_IMPLEMENTED), model.load(implementation_status=PROPOSED_NOT_IMPLEMENTED)
- `BLOCKED` [live] missing_dependency_reported — BLOCKED
- `BLOCKED` [live] cad_import_license_limited — required operation(s) not executable in this build: geometry.import(implementation_status=PROPOSED_NOT_IMPLEMENTED)

### W15_T007_selections — BLOCKED
- `BLOCKED` [static] static_physics_selection_ops_availability — required operation(s) not executable in this build: physics.selection_set(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_create(implementation_status=unknown), physics.feature_update(implementation_status=PROPOSED_NOT_IMPLEMENTED), selection.create(implementation_status=unknown), node.property_get(implementation_status=unknown)
- `BLOCKED` [live] physics_level_selection_set — required operation(s) not executable in this build: physics.selection_set(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_create(implementation_status=unknown), physics.feature_update(implementation_status=PROPOSED_NOT_IMPLEMENTED), selection.create(implementation_status=unknown), node.property_get(implementation_status=unknown)
- `BLOCKED` [live] feature_level_selection_set — required operation(s) not executable in this build: physics.selection_set(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_create(implementation_status=unknown), physics.feature_update(implementation_status=PROPOSED_NOT_IMPLEMENTED), selection.create(implementation_status=unknown), node.property_get(implementation_status=unknown)
- `BLOCKED` [live] inherited_selection_not_writable — required operation(s) not executable in this build: physics.selection_set(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_create(implementation_status=unknown), physics.feature_update(implementation_status=PROPOSED_NOT_IMPLEMENTED), selection.create(implementation_status=unknown), node.property_get(implementation_status=unknown)
- `BLOCKED` [live] named_selection_binding_readback — required operation(s) not executable in this build: physics.selection_set(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_create(implementation_status=unknown), physics.feature_update(implementation_status=PROPOSED_NOT_IMPLEMENTED), selection.create(implementation_status=unknown), node.property_get(implementation_status=unknown)
- `BLOCKED` [live] no_invalid_parent_feature_coupling — required operation(s) not executable in this build: physics.selection_set(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_create(implementation_status=unknown), physics.feature_update(implementation_status=PROPOSED_NOT_IMPLEMENTED), selection.create(implementation_status=unknown), node.property_get(implementation_status=unknown)

### W15_T017_material — BLOCKED
- `BLOCKED` [static] static_material_ops_availability — required operation(s) not executable in this build: material.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), material.set_properties(implementation_status=PROPOSED_NOT_IMPLEMENTED), material.selection_set(implementation_status=unknown), material.validate(implementation_status=PROPOSED_NOT_IMPLEMENTED), node.property_get(implementation_status=unknown)
- `BLOCKED` [live] k_T_Cp_T_and_rho_expressions — required operation(s) not executable in this build: material.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), material.set_properties(implementation_status=PROPOSED_NOT_IMPLEMENTED), material.selection_set(implementation_status=unknown), material.validate(implementation_status=PROPOSED_NOT_IMPLEMENTED), node.property_get(implementation_status=unknown)
- `BLOCKED` [live] anisotropic_tensor_and_coordinate_system — required operation(s) not executable in this build: material.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), material.set_properties(implementation_status=PROPOSED_NOT_IMPLEMENTED), material.selection_set(implementation_status=unknown), material.validate(implementation_status=PROPOSED_NOT_IMPLEMENTED), node.property_get(implementation_status=unknown)
- `BLOCKED` [live] missing_required_property_preflight_error — required operation(s) not executable in this build: material.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), material.set_properties(implementation_status=PROPOSED_NOT_IMPLEMENTED), material.selection_set(implementation_status=unknown), material.validate(implementation_status=PROPOSED_NOT_IMPLEMENTED), node.property_get(implementation_status=unknown)
- `BLOCKED` [live] material_readback_after_update — required operation(s) not executable in this build: material.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), material.set_properties(implementation_status=PROPOSED_NOT_IMPLEMENTED), material.selection_set(implementation_status=unknown), material.validate(implementation_status=PROPOSED_NOT_IMPLEMENTED), node.property_get(implementation_status=unknown)

### W15_T042_license — BLOCKED
- `BLOCKED` [static] static_license_ops_availability — required operation(s) not executable in this build: runtime.license_inspect(implementation_status=PROPOSED_NOT_IMPLEMENTED)
- `BLOCKED` [live] license_inspect_records_has_product — required operation(s) not executable in this build: runtime.license_inspect(implementation_status=PROPOSED_NOT_IMPLEMENTED)
- `BLOCKED` [live] missing_product_blocks_with_blocked_license — required operation(s) not executable in this build: runtime.license_inspect(implementation_status=PROPOSED_NOT_IMPLEMENTED)
- `BLOCKED` [live] authorized_product_usable — required operation(s) not executable in this build: runtime.license_inspect(implementation_status=PROPOSED_NOT_IMPLEMENTED)
- `BLOCKED` [live] probe_does_not_occupy_license — required operation(s) not executable in this build: runtime.license_inspect(implementation_status=PROPOSED_NOT_IMPLEMENTED)

### W16_T018_mesh — BLOCKED
- `BLOCKED` [static] static_mesh_ops_availability — required operation(s) not executable in this build: mesh.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), mesh.feature_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), mesh.feature_update(implementation_status=unknown), mesh.build(implementation_status=PROPOSED_NOT_IMPLEMENTED), mesh.statistics(implementation_status=PROPOSED_NOT_IMPLEMENTED), mesh.quality(implementation_status=PROPOSED_NOT_IMPLEMENTED)
- `BLOCKED` [live] free_tet_sequence_created — required operation(s) not executable in this build: mesh.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), mesh.feature_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), mesh.feature_update(implementation_status=unknown), mesh.build(implementation_status=PROPOSED_NOT_IMPLEMENTED), mesh.statistics(implementation_status=PROPOSED_NOT_IMPLEMENTED), mesh.quality(implementation_status=PROPOSED_NOT_IMPLEMENTED)
- `BLOCKED` [live] local_size_feature_applied — required operation(s) not executable in this build: mesh.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), mesh.feature_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), mesh.feature_update(implementation_status=unknown), mesh.build(implementation_status=PROPOSED_NOT_IMPLEMENTED), mesh.statistics(implementation_status=PROPOSED_NOT_IMPLEMENTED), mesh.quality(implementation_status=PROPOSED_NOT_IMPLEMENTED)
- `BLOCKED` [live] modify_and_rebuild — required operation(s) not executable in this build: mesh.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), mesh.feature_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), mesh.feature_update(implementation_status=unknown), mesh.build(implementation_status=PROPOSED_NOT_IMPLEMENTED), mesh.statistics(implementation_status=PROPOSED_NOT_IMPLEMENTED), mesh.quality(implementation_status=PROPOSED_NOT_IMPLEMENTED)
- `BLOCKED` [live] statistics_counts_and_coverage — required operation(s) not executable in this build: mesh.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), mesh.feature_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), mesh.feature_update(implementation_status=unknown), mesh.build(implementation_status=PROPOSED_NOT_IMPLEMENTED), mesh.statistics(implementation_status=PROPOSED_NOT_IMPLEMENTED), mesh.quality(implementation_status=PROPOSED_NOT_IMPLEMENTED)
- `BLOCKED` [live] quality_definition_and_low_quality_locations — required operation(s) not executable in this build: mesh.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), mesh.feature_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), mesh.feature_update(implementation_status=unknown), mesh.build(implementation_status=PROPOSED_NOT_IMPLEMENTED), mesh.statistics(implementation_status=PROPOSED_NOT_IMPLEMENTED), mesh.quality(implementation_status=PROPOSED_NOT_IMPLEMENTED)
- `PASS` [protocol] build_success_is_not_quality_pass

### W16_T019_chainA_steady — BLOCKED
- `PASS` [fixture] analytic_reference_preregistered
- `BLOCKED` [static] static_chain_a_ops_availability — required operation(s) not executable in this build: model.create(implementation_status=unknown), geometry.feature_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.build(implementation_status=unknown), material.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), material.set_properties(implementation_status=unknown), material.selection_set(implementation_status=unknown), physics.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_create(implementation_status=unknown), mesh.create(implementation_status=unknown), mesh.feature_create(implementation_status=unknown), mesh.build(implementation_status=PROPOSED_NOT_IMPLEMENTED), study.create(implementation_status=unknown), study.step_create(implementation_status=unknown), study.run(implementation_status=PROPOSED_NOT_IMPLEMENTED), result.sample_path(implementation_status=unknown), model.save(implementation_status=unknown)
- `BLOCKED` [live] empty_model_geometry_block — required operation(s) not executable in this build: model.create(implementation_status=unknown), geometry.feature_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.build(implementation_status=unknown), material.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), material.set_properties(implementation_status=unknown), material.selection_set(implementation_status=unknown), physics.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_create(implementation_status=unknown), mesh.create(implementation_status=unknown), mesh.feature_create(implementation_status=unknown), mesh.build(implementation_status=PROPOSED_NOT_IMPLEMENTED), study.create(implementation_status=unknown), study.step_create(implementation_status=unknown), study.run(implementation_status=PROPOSED_NOT_IMPLEMENTED), result.sample_path(implementation_status=unknown), model.save(implementation_status=unknown)
- `BLOCKED` [live] constant_material_assigned — required operation(s) not executable in this build: model.create(implementation_status=unknown), geometry.feature_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.build(implementation_status=unknown), material.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), material.set_properties(implementation_status=unknown), material.selection_set(implementation_status=unknown), physics.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_create(implementation_status=unknown), mesh.create(implementation_status=unknown), mesh.feature_create(implementation_status=unknown), mesh.build(implementation_status=PROPOSED_NOT_IMPLEMENTED), study.create(implementation_status=unknown), study.step_create(implementation_status=unknown), study.run(implementation_status=PROPOSED_NOT_IMPLEMENTED), result.sample_path(implementation_status=unknown), model.save(implementation_status=unknown)
- `BLOCKED` [live] boundary_temperatures_and_insulation — required operation(s) not executable in this build: model.create(implementation_status=unknown), geometry.feature_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.build(implementation_status=unknown), material.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), material.set_properties(implementation_status=unknown), material.selection_set(implementation_status=unknown), physics.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_create(implementation_status=unknown), mesh.create(implementation_status=unknown), mesh.feature_create(implementation_status=unknown), mesh.build(implementation_status=PROPOSED_NOT_IMPLEMENTED), study.create(implementation_status=unknown), study.step_create(implementation_status=unknown), study.run(implementation_status=PROPOSED_NOT_IMPLEMENTED), result.sample_path(implementation_status=unknown), model.save(implementation_status=unknown)
- `BLOCKED` [live] local_mesh_built — required operation(s) not executable in this build: model.create(implementation_status=unknown), geometry.feature_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.build(implementation_status=unknown), material.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), material.set_properties(implementation_status=unknown), material.selection_set(implementation_status=unknown), physics.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_create(implementation_status=unknown), mesh.create(implementation_status=unknown), mesh.feature_create(implementation_status=unknown), mesh.build(implementation_status=PROPOSED_NOT_IMPLEMENTED), study.create(implementation_status=unknown), study.step_create(implementation_status=unknown), study.run(implementation_status=PROPOSED_NOT_IMPLEMENTED), result.sample_path(implementation_status=unknown), model.save(implementation_status=unknown)
- `BLOCKED` [live] stationary_study_and_solver — required operation(s) not executable in this build: model.create(implementation_status=unknown), geometry.feature_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.build(implementation_status=unknown), material.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), material.set_properties(implementation_status=unknown), material.selection_set(implementation_status=unknown), physics.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_create(implementation_status=unknown), mesh.create(implementation_status=unknown), mesh.feature_create(implementation_status=unknown), mesh.build(implementation_status=PROPOSED_NOT_IMPLEMENTED), study.create(implementation_status=unknown), study.step_create(implementation_status=unknown), study.run(implementation_status=PROPOSED_NOT_IMPLEMENTED), result.sample_path(implementation_status=unknown), model.save(implementation_status=unknown)
- `BLOCKED` [live] solve_produced_solution — required operation(s) not executable in this build: model.create(implementation_status=unknown), geometry.feature_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.build(implementation_status=unknown), material.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), material.set_properties(implementation_status=unknown), material.selection_set(implementation_status=unknown), physics.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_create(implementation_status=unknown), mesh.create(implementation_status=unknown), mesh.feature_create(implementation_status=unknown), mesh.build(implementation_status=PROPOSED_NOT_IMPLEMENTED), study.create(implementation_status=unknown), study.step_create(implementation_status=unknown), study.run(implementation_status=PROPOSED_NOT_IMPLEMENTED), result.sample_path(implementation_status=unknown), model.save(implementation_status=unknown)
- `BLOCKED` [live] linear_profile_relative_error_le_1e-4 — required operation(s) not executable in this build: model.create(implementation_status=unknown), geometry.feature_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.build(implementation_status=unknown), material.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), material.set_properties(implementation_status=unknown), material.selection_set(implementation_status=unknown), physics.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_create(implementation_status=unknown), mesh.create(implementation_status=unknown), mesh.feature_create(implementation_status=unknown), mesh.build(implementation_status=PROPOSED_NOT_IMPLEMENTED), study.create(implementation_status=unknown), study.step_create(implementation_status=unknown), study.run(implementation_status=PROPOSED_NOT_IMPLEMENTED), result.sample_path(implementation_status=unknown), model.save(implementation_status=unknown)
- `BLOCKED` [live] heat_flux_and_power_balance — required operation(s) not executable in this build: model.create(implementation_status=unknown), geometry.feature_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.build(implementation_status=unknown), material.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), material.set_properties(implementation_status=unknown), material.selection_set(implementation_status=unknown), physics.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_create(implementation_status=unknown), mesh.create(implementation_status=unknown), mesh.feature_create(implementation_status=unknown), mesh.build(implementation_status=PROPOSED_NOT_IMPLEMENTED), study.create(implementation_status=unknown), study.step_create(implementation_status=unknown), study.run(implementation_status=PROPOSED_NOT_IMPLEMENTED), result.sample_path(implementation_status=unknown), model.save(implementation_status=unknown)
- `BLOCKED` [live] sample_table_and_units_recorded — required operation(s) not executable in this build: model.create(implementation_status=unknown), geometry.feature_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.build(implementation_status=unknown), material.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), material.set_properties(implementation_status=unknown), material.selection_set(implementation_status=unknown), physics.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_create(implementation_status=unknown), mesh.create(implementation_status=unknown), mesh.feature_create(implementation_status=unknown), mesh.build(implementation_status=PROPOSED_NOT_IMPLEMENTED), study.create(implementation_status=unknown), study.step_create(implementation_status=unknown), study.run(implementation_status=PROPOSED_NOT_IMPLEMENTED), result.sample_path(implementation_status=unknown), model.save(implementation_status=unknown)
- `BLOCKED` [live] saved_mph_hash_recorded — required operation(s) not executable in this build: model.create(implementation_status=unknown), geometry.feature_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), geometry.build(implementation_status=unknown), material.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), material.set_properties(implementation_status=unknown), material.selection_set(implementation_status=unknown), physics.create(implementation_status=PROPOSED_NOT_IMPLEMENTED), physics.feature_create(implementation_status=unknown), mesh.create(implementation_status=unknown), mesh.feature_create(implementation_status=unknown), mesh.build(implementation_status=PROPOSED_NOT_IMPLEMENTED), study.create(implementation_status=unknown), study.step_create(implementation_status=unknown), study.run(implementation_status=PROPOSED_NOT_IMPLEMENTED), result.sample_path(implementation_status=unknown), model.save(implementation_status=unknown)

### W16_T019_chainB_transient — BLOCKED
- `PASS` [fixture] analytic_reference_preregistered
- `BLOCKED` [static] static_chain_b_ops_availability — required operation(s) not executable in this build: model.create(implementation_status=unknown), geometry.feature_create(implementation_status=unknown), geometry.build(implementation_status=unknown), material.create(implementation_status=unknown), material.set_properties(implementation_status=unknown), physics.create(implementation_status=unknown), physics.feature_create(implementation_status=unknown), mesh.create(implementation_status=unknown), mesh.build(implementation_status=unknown), study.create(implementation_status=unknown), study.step_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), study.run(implementation_status=PROPOSED_NOT_IMPLEMENTED), result.sample_path(implementation_status=PROPOSED_NOT_IMPLEMENTED)
- `BLOCKED` [live] transient_study_and_initial_value — required operation(s) not executable in this build: model.create(implementation_status=unknown), geometry.feature_create(implementation_status=unknown), geometry.build(implementation_status=unknown), material.create(implementation_status=unknown), material.set_properties(implementation_status=unknown), physics.create(implementation_status=unknown), physics.feature_create(implementation_status=unknown), mesh.create(implementation_status=unknown), mesh.build(implementation_status=unknown), study.create(implementation_status=unknown), study.step_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), study.run(implementation_status=PROPOSED_NOT_IMPLEMENTED), result.sample_path(implementation_status=PROPOSED_NOT_IMPLEMENTED)
- `BLOCKED` [live] transient_solve_produced_solution — required operation(s) not executable in this build: model.create(implementation_status=unknown), geometry.feature_create(implementation_status=unknown), geometry.build(implementation_status=unknown), material.create(implementation_status=unknown), material.set_properties(implementation_status=unknown), physics.create(implementation_status=unknown), physics.feature_create(implementation_status=unknown), mesh.create(implementation_status=unknown), mesh.build(implementation_status=unknown), study.create(implementation_status=unknown), study.step_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), study.run(implementation_status=PROPOSED_NOT_IMPLEMENTED), result.sample_path(implementation_status=PROPOSED_NOT_IMPLEMENTED)
- `BLOCKED` [live] normalized_max_error_le_1e-3 — required operation(s) not executable in this build: model.create(implementation_status=unknown), geometry.feature_create(implementation_status=unknown), geometry.build(implementation_status=unknown), material.create(implementation_status=unknown), material.set_properties(implementation_status=unknown), physics.create(implementation_status=unknown), physics.feature_create(implementation_status=unknown), mesh.create(implementation_status=unknown), mesh.build(implementation_status=unknown), study.create(implementation_status=unknown), study.step_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), study.run(implementation_status=PROPOSED_NOT_IMPLEMENTED), result.sample_path(implementation_status=PROPOSED_NOT_IMPLEMENTED)
- `BLOCKED` [live] time_points_and_mesh_recorded — required operation(s) not executable in this build: model.create(implementation_status=unknown), geometry.feature_create(implementation_status=unknown), geometry.build(implementation_status=unknown), material.create(implementation_status=unknown), material.set_properties(implementation_status=unknown), physics.create(implementation_status=unknown), physics.feature_create(implementation_status=unknown), mesh.create(implementation_status=unknown), mesh.build(implementation_status=unknown), study.create(implementation_status=unknown), study.step_create(implementation_status=PROPOSED_NOT_IMPLEMENTED), study.run(implementation_status=PROPOSED_NOT_IMPLEMENTED), result.sample_path(implementation_status=PROPOSED_NOT_IMPLEMENTED)

### W16_T019_chainC_continue — BLOCKED
- `BLOCKED` [static] static_chain_c_ops_availability — required operation(s) not executable in this build: model.load(implementation_status=unknown), node.find(implementation_status=unknown), node.property_get(implementation_status=unknown), study.step_update(implementation_status=unknown), study.run(implementation_status=PROPOSED_NOT_IMPLEMENTED), solver.inspect(implementation_status=unknown), model.save(implementation_status=unknown)
- `NOT_RUN` [fixture] user_style_model_available — no user-style continuation model was supplied (--chain-c-model)
- `BLOCKED` [live] target_only_modified — the user-style continuation model was not supplied
- `BLOCKED` [live] non_target_nodes_preserved — the user-style continuation model was not supplied
- `BLOCKED` [live] manual_solver_preserved — the user-style continuation model was not supplied
- `BLOCKED` [live] derived_values_and_data_association_preserved — the user-style continuation model was not supplied
- `BLOCKED` [live] solve_after_continuation — the user-style continuation model was not supplied

### W16_T020_solver — BLOCKED
- `BLOCKED` [static] static_solver_ops_availability — required operation(s) not executable in this build: solver.list(implementation_status=PROPOSED_NOT_IMPLEMENTED), solver.inspect(implementation_status=PROPOSED_NOT_IMPLEMENTED), solver.feature_update(implementation_status=PROPOSED_NOT_IMPLEMENTED), study.run(implementation_status=PROPOSED_NOT_IMPLEMENTED), node.property_get(implementation_status=unknown)
- `BLOCKED` [live] solver_tree_read — required operation(s) not executable in this build: solver.list(implementation_status=PROPOSED_NOT_IMPLEMENTED), solver.inspect(implementation_status=PROPOSED_NOT_IMPLEMENTED), solver.feature_update(implementation_status=PROPOSED_NOT_IMPLEMENTED), study.run(implementation_status=PROPOSED_NOT_IMPLEMENTED), node.property_get(implementation_status=unknown)
- `BLOCKED` [live] sub_feature_property_update_readback — required operation(s) not executable in this build: solver.list(implementation_status=PROPOSED_NOT_IMPLEMENTED), solver.inspect(implementation_status=PROPOSED_NOT_IMPLEMENTED), solver.feature_update(implementation_status=PROPOSED_NOT_IMPLEMENTED), study.run(implementation_status=PROPOSED_NOT_IMPLEMENTED), node.property_get(implementation_status=unknown)
- `BLOCKED` [live] solve_after_subfeature_update — required operation(s) not executable in this build: solver.list(implementation_status=PROPOSED_NOT_IMPLEMENTED), solver.inspect(implementation_status=PROPOSED_NOT_IMPLEMENTED), solver.feature_update(implementation_status=PROPOSED_NOT_IMPLEMENTED), study.run(implementation_status=PROPOSED_NOT_IMPLEMENTED), node.property_get(implementation_status=unknown)
- `BLOCKED` [live] unknown_property_fails_accurately — required operation(s) not executable in this build: solver.list(implementation_status=PROPOSED_NOT_IMPLEMENTED), solver.inspect(implementation_status=PROPOSED_NOT_IMPLEMENTED), solver.feature_update(implementation_status=PROPOSED_NOT_IMPLEMENTED), study.run(implementation_status=PROPOSED_NOT_IMPLEMENTED), node.property_get(implementation_status=unknown)
- `BLOCKED` [live] manual_solver_not_auto_overwritten — required operation(s) not executable in this build: solver.list(implementation_status=PROPOSED_NOT_IMPLEMENTED), solver.inspect(implementation_status=PROPOSED_NOT_IMPLEMENTED), solver.feature_update(implementation_status=PROPOSED_NOT_IMPLEMENTED), study.run(implementation_status=PROPOSED_NOT_IMPLEMENTED), node.property_get(implementation_status=unknown)

### GUARD_T010 — NOT_RUN
- `PASS` [static] static_idempotency_contract_published
- `NOT_RUN` [live] repeated_key_not_reexecuted — offline run: --live was not supplied
- `NOT_RUN` [live] same_tag_same_type_duplicate_rejected_or_idempotent — offline run: --live was not supplied
- `NOT_RUN` [live] same_tag_different_type_rejected — offline run: --live was not supplied
- `NOT_RUN` [live] request_hash_conflict_rejected — offline run: --live was not supplied

### GUARD_T035 — BLOCKED
- `BLOCKED` [live] outside_workspace_source_denied — required operation(s) not executable in this build: docs.index(implementation_status=unknown), docs.search(implementation_status=unknown)
- `BLOCKED` [live] symlink_outside_denied — required operation(s) not executable in this build: docs.index(implementation_status=unknown), docs.search(implementation_status=unknown)
- `BLOCKED` [live] outside_write_attempt_denied — required operation(s) not executable in this build: docs.index(implementation_status=unknown), docs.search(implementation_status=unknown)
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

### GUARD_T005 — NOT_RUN
- `PASS` [static] static_evaluation_ops_available
- `NOT_RUN` [live] user_derived_nodes_preserved — offline run: --live was not supplied
- `NOT_RUN` [live] invalid_expression_does_not_delete_nodes — offline run: --live was not supplied
- `NOT_RUN` [live] temporary_nodes_cleaned — offline run: --live was not supplied

### GUARD_T033 — BLOCKED
- `BLOCKED` [static] static_evaluation_policy_documented — operation_describe does not publish an evaluation policy for evaluate_expressions in this build, so the T033 policy contract cannot be inspected
- `NOT_RUN` [live] pure_read_rejects_or_isolates — offline run: --live was not supplied
- `NOT_RUN` [live] ephemeral_mutation_recorded_and_serial — offline run: --live was not supplied
- `NOT_RUN` [live] only_own_temporary_nodes_cleaned — offline run: --live was not supplied
