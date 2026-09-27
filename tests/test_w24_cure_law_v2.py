import copy
import math
from pathlib import Path

import pytest

from tools.w24_cure_law_v2 import (
    ADHESIVE_Z_MIN_M,
    ADHESIVE_Z_SURFACE_M,
    ALPHA_FIELD,
    DOSE_FIELD,
    MAXWELL_CONTROL_REQUIRED_TIMES_S,
    MU_BASE_M_INV,
    QPOST_FIELD,
    GEL_STRESS_COMPONENTS,
    AcceptanceError,
    compare_v2_history_handoff,
    elastic_bulk_shear,
    isothermal_alpha_from_relative_dose,
    maxwell_control_reference,
    relative_uv_dose,
    relative_uv_intensity,
    relative_uv_sensitivity,
    validate_gel_stress_free_control,
    validate_maxwell_control_capture,
)
from tools.w24_science_acceptance import analytic_alpha_iso


REPO = Path(__file__).resolve().parents[1]
COUPON_FIXTURE = REPO / "tools/java/W24CureCouponFixture.java"
CONTROL_FIXTURE = REPO / "tools/java/W24CureLawV2ControlFixture.java"


def test_v2_beer_lambert_dose_is_adhesive_only_dimensionless_and_synthetic():
    bottom = ADHESIVE_Z_MIN_M
    surface = ADHESIVE_Z_SURFACE_M
    assert relative_uv_intensity(0, surface) == pytest.approx(1.0)
    assert relative_uv_intensity(0, bottom) == pytest.approx(math.exp(-1.0))
    assert relative_uv_intensity(0, bottom, mu_scale=2.0) == pytest.approx(math.exp(-2.0))
    assert relative_uv_intensity(120, surface) == 0.0
    assert relative_uv_dose(121, surface) == pytest.approx(120.0)

    sensitivity = relative_uv_sensitivity(120, bottom)
    values = sensitivity["dose_by_mu_scale"]
    assert values["0.5"] > values["1.0"] > values["2.0"]
    assert sensitivity["unit"] == "s"
    assert sensitivity["synthetic_estimated"] is True
    assert sensitivity["absolute_irradiance"] is False
    assert MU_BASE_M_INV == pytest.approx(25_000.0)

    with pytest.raises(AcceptanceError, match="only inside the adhesive"):
        relative_uv_intensity(10, bottom - 1e-9)
    with pytest.raises(AcceptanceError, match="geometry top"):
        relative_uv_dose(10, surface, z_surface_m=surface + 1e-9)
    with pytest.raises(AcceptanceError, match="frozen 0.5x"):
        relative_uv_dose(10, surface, mu_scale=1.5)


def test_surface_v2_isothermal_alpha_matches_existing_uniform_alpha_iso_control():
    times = [0, 10, 30, 47, 60, 120, 240]
    v2 = isothermal_alpha_from_relative_dose(
        times,
        alpha0=0.2,
        t0_k=298.15,
        pre_exponential_s=1e5,
        activation_j_mol=55e3,
        gas_constant_j_mol_k=8.31446261815324,
        uv_rate_s=1e-2,
        z_m=ADHESIVE_Z_SURFACE_M,
    )
    old_uniform = analytic_alpha_iso(
        times,
        alpha0=0.2,
        t0_k=298.15,
        pre_exponential_s=1e5,
        activation_j_mol=55e3,
        gas_constant_j_mol_k=8.31446261815324,
        uv_rate_s=1e-2,
        uv_duration_s=120,
    )
    assert v2 == pytest.approx(old_uniform, abs=1e-14)
    inside = isothermal_alpha_from_relative_dose(
        [47], alpha0=0.2, t0_k=298.15, pre_exponential_s=1e5,
        activation_j_mol=55e3, gas_constant_j_mol_k=8.31446261815324,
        uv_rate_s=1e-2, z_m=(ADHESIVE_Z_MIN_M + ADHESIVE_Z_SURFACE_M) / 2,
    )
    assert inside[0] < v2[times.index(47)]


def test_v2_moduli_and_maxwell_ramp_hold_reference_check_absolute_xx_yy_amplitudes():
    k_inf, g_inf = elastic_bulk_shear(0.5e9, 0.35)
    k_branch, g_branch = elastic_bulk_shear(1.5e9, 0.35)
    assert k_inf == pytest.approx(0.5e9 / 0.9)
    assert g_inf == pytest.approx(0.5e9 / 2.7)
    assert k_branch == pytest.approx(1.5e9 / 0.9)
    assert g_branch == pytest.approx(1.5e9 / 2.7)

    times = [0.0, 0.5, 1.0, 301.0, 601.0, 901.0]
    response = maxwell_control_reference(times)
    xx_branch_end = response["coefficients_pa"]["xx_branch"] * 1e-3 * 300.0 * (
        1.0 - math.exp(-1.0 / 300.0)
    )
    yy_branch_end = response["coefficients_pa"]["yy_branch"] * 1e-3 * 300.0 * (
        1.0 - math.exp(-1.0 / 300.0)
    )
    assert response["sigma_xx_branch_pa"][2] == pytest.approx(xx_branch_end)
    assert response["sigma_yy_branch_pa"][2] == pytest.approx(yy_branch_end)
    assert response["sigma_xx_branch_pa"][3] == pytest.approx(xx_branch_end / math.e)
    assert response["sigma_yy_branch_pa"][3] == pytest.approx(yy_branch_end / math.e)
    assert response["sigma_xx_long_term_pa"][2:] == pytest.approx(
        [response["coefficients_pa"]["xx_long_term"] * 1e-3] * 4
    )
    assert response["sigma_yy_long_term_pa"][2:] == pytest.approx(
        [response["coefficients_pa"]["yy_long_term"] * 1e-3] * 4
    )


def test_v2_java_control_capture_binds_dataset_solver_full_times_and_real_quasistatic_readback():
    source = CONTROL_FIXTURE.read_text(encoding="utf-8")
    science = (REPO / "tools/java/W24CureScienceFixture.java").read_text(encoding="utf-8")
    assert '"capture_maxwell_control".equals(action)' in source
    assert '"capture_gel_control".equals(action)' in source
    assert '"solution_snapshot_v2".equals(action)' in source
    assert 'getString("StructuralTransientBehavior")' in source
    assert 'result.put("quasistatic_readback", requireQuasistatic(model))' in source
    assert 'new String[]{"solid.sx", "solid.sy"}' in source
    assert '"solid.sxy",' in source and '"solid.syz", "solid.isactive", "solid.wasactive"' in source
    assert 'dataset.set("solution", solverTag)' in source
    assert 'interpolation.set("solnum", "all")' in source
    assert 'interpolation.setInterpolationCoordinates(coordinates)' in source
    assert '"time_source", "SolverSequence.getPVals"' in source
    assert '"native_study_run_calls", 0' in source
    assert "study.run(" not in source
    start = science.index("private static Map<String, Object> historyCaptureV2")
    end = science.index("private static final class NumericalValues", start)
    assert "study.run(" not in science[start:end]


def test_v2_history_capture_includes_relative_dose_all_cure_displacement_and_both_activation_variables():
    source = (REPO / "tools/java/W24CureScienceFixture.java").read_text(encoding="utf-8")
    start = source.index("private static Map<String, Object> historyCaptureV2")
    end = source.index("private static final class NumericalValues", start)
    action = source[start:end]
    for expected in (
        '"T", "alpha", "Duv_rel", "qpost", "u", "w"',
        '"solid.isactive", "solid.wasactive"',
        '"K", "1", "s", "1", "m", "m", "1", "1"',
        '"native_study_run_calls", 0',
        '"maxwell_branch_state", "UNVERIFIED_NO_PUBLIC_REFERENCE_STATE_CAPTURE"',
    ):
        assert expected in action
    assert "solution.getPVals()" in action
    assert 'dataset.set("solution", solverTag)' in action
    assert '"Quasistatic".equals(quasistatic)' in action


def test_maxwell_capture_requires_fixed_checkpoints_native_pa_and_absolute_match():
    times = [0.0, 0.5, *MAXWELL_CONTROL_REQUIRED_TIMES_S]
    reference = maxwell_control_reference(times)
    capture = {
        "case_id": "maxwell_ramp_hold_control",
        "native_metrics_readback": True,
        "stress_unit": "Pa",
        "stored_times_s": times,
        "sigma_xx_pa": reference["sigma_xx_pa"],
        "sigma_yy_pa": reference["sigma_yy_pa"],
    }
    receipt = validate_maxwell_control_capture(
        capture, absolute_tolerance_pa=1.0, relative_tolerance=1e-8
    )
    assert receipt["status"] == "ANALYTIC_VALUES_MATCH_SOURCE_AUTH_REQUIRED"
    assert receipt["source_identity_authenticated"] is False
    assert receipt["input_native_metrics_readback_assertion"] is True
    assert receipt["uses_measured_sigma0_fit"] is False
    assert receipt["long_term_baseline_checked"] is True
    assert receipt["branch_amplitude_checked"] is True
    assert receipt["branch_decay_checked"] is True

    wrong_amplitude = copy.deepcopy(capture)
    wrong_amplitude["sigma_xx_pa"][2] += 1e6
    with pytest.raises(AcceptanceError, match="absolute-amplitude error"):
        validate_maxwell_control_capture(
            wrong_amplitude, absolute_tolerance_pa=1.0, relative_tolerance=1e-8
        )

    missing_checkpoint = copy.deepcopy(capture)
    idx = missing_checkpoint["stored_times_s"].index(601.0)
    for key in ("stored_times_s", "sigma_xx_pa", "sigma_yy_pa"):
        missing_checkpoint[key].pop(idx)
    with pytest.raises(AcceptanceError, match="lacks exact stored checkpoint 601 s"):
        validate_maxwell_control_capture(
            missing_checkpoint, absolute_tolerance_pa=1.0, relative_tolerance=1e-8
        )

    wrong_units = copy.deepcopy(capture)
    wrong_units["stress_unit"] = "MPa"
    with pytest.raises(AcceptanceError, match="native control-model metrics"):
        validate_maxwell_control_capture(
            wrong_units, absolute_tolerance_pa=1.0, relative_tolerance=1e-8
        )


def _history_frame(time_s, values=None, *, history_overrides=None, wasactive_values=None,
                   isactive_values=None, snapshot_schema="W24-DOF-SNAPSHOT-2", axes=2):
    names = ["comp1_T", "comp1_alpha", "comp1_Duv_rel", "comp1_qpost", "comp1_u"]
    base = [300.0, 0.6, 120.0, 0.1, 1.0e-6]
    if axes == 3:
        names.append("comp1_v")
        base.append(1.5e-6)
    names.extend(["comp1_w", "solid.branch_state_fixture"])
    base.extend([2.0e-6, 2.0e6])
    values = list(base if values is None else values)
    assert len(values) == len(names)
    count = len(names)
    expressions = ["T", "alpha", "Duv_rel", "qpost", "u", "w", "solid.isactive", "solid.wasactive"]
    rows = [[[300.0] * 3, [300.0] * 3], [[0.6] * 3, [0.6] * 3],
            [[120.0] * 3, [120.0] * 3], [[0.1] * 3, [0.1] * 3],
            [[1e-6] * 3, [1e-6] * 3], [[2e-6] * 3, [2e-6] * 3],
            [[1.0] * 3, [1.0] * 3], [[1.0] * 3, [1.0] * 3]]
    for field, value in (history_overrides or {}).items():
        rows[expressions.index(field)][1] = [value, value, value]
    if isactive_values is not None:
        rows[expressions.index("solid.isactive")][1] = list(isactive_values)
    if wasactive_values is not None:
        rows[expressions.index("solid.wasactive")][1] = list(wasactive_values)
    return {
        "time_s": time_s,
        "dofs": {
            "snapshot_schema": snapshot_schema,
            "coordinate_axes": axes,
            "complete_xmesh_dofs": True,
            "geomNums": [1] * count,
            "nodes": list(range(1, count + 1)),
            "coords": [[float(i) * 1e-6 for i in range(count)] for _ in range(axes)],
            "dofNames": names,
            "nameInds": list(range(count)),
            "solVectorInds": list(range(count)),
        },
        "u_real": values,
        "u_imag": [0.0] * count,
        "history_capture": {
            "schema": "W24_CURE_LAW_V2_HISTORY_CAPTURE_V1",
            "stored_times_s": [0.0, time_s],
            "expressions": expressions,
            "units": ["K", "1", "s", "1", "m", "m", "1", "1"],
            "coordinates_m": [[25e-6, 520e-6], [50e-6, 530e-6], [75e-6, 540e-6]],
            "data": rows,
        },
    }


def _handoff_tolerances(axes=2):
    result = {"comp1_T": 1e-6, "comp1_alpha": 1e-12, "comp1_Duv_rel": 1e-12,
              "comp1_qpost": 1e-12, "comp1_u": 1e-15, "comp1_w": 1e-15,
              "solid.branch_state_fixture": 1e-9}
    if axes == 3:
        result["comp1_v"] = 1e-15
    return result


def _history_handoff_tolerances():
    return {"T": 1e-6, "alpha": 1e-12, "Duv_rel": 1e-12, "qpost": 1e-12,
            "u": 1e-15, "w": 1e-15, "solid.isactive": 0.0, "solid.wasactive": 0.0}


def test_history_handoff_requires_full_dofs_visible_fields_and_keeps_branch_state_unverified():
    source = _history_frame(300.0)
    target = _history_frame(300.0)
    result = compare_v2_history_handoff(
        source, target, branch_dof_names=["solid.branch_state_fixture"],
        dof_abs_tolerances=_handoff_tolerances(),
        history_abs_tolerances=_history_handoff_tolerances())
    assert result["status"] == "VISIBLE_HISTORY_MATCH_BRANCH_STATE_UNVERIFIED"
    assert result["source_identity_authenticated"] is False
    assert result["full_dof_count"] == 7
    assert result["branch_state_dof_count"] == 0
    assert result["caller_branch_name_hints_ignored_for_acceptance"] == ["solid.branch_state_fixture"]
    assert result["isactive_identical"] is True and result["wasactive_identical"] is True
    assert result["maxwell_branch_state"] == "UNVERIFIED_NO_PUBLIC_REFERENCE_STATE_CAPTURE"


@pytest.mark.parametrize("field", ["comp1_T", "comp1_alpha", "comp1_Duv_rel", "comp1_qpost",
                                    "comp1_u", "comp1_w"])
def test_history_handoff_detects_full_field_reset_even_when_probe_history_is_unchanged(field):
    source = _history_frame(300.0)
    target = _history_frame(300.0)
    field_index = target["dofs"]["dofNames"].index(field)
    vector_index = target["dofs"]["solVectorInds"][field_index]
    target["u_real"][vector_index] = 0.0
    with pytest.raises(AcceptanceError, match="changed by|DOF"):
        compare_v2_history_handoff(
            source, target,
            dof_abs_tolerances=_handoff_tolerances(),
            history_abs_tolerances=_history_handoff_tolerances())


def test_history_handoff_detects_visible_cure_activation_and_displacement_resets():
    source = _history_frame(300.0)
    for field in ("T", "alpha", "Duv_rel", "qpost", "u", "w"):
        with pytest.raises(AcceptanceError, match=f"native history {field} changed"):
            compare_v2_history_handoff(
                source, _history_frame(300.0, history_overrides={field: 0.0}),
                dof_abs_tolerances=_handoff_tolerances(),
                history_abs_tolerances=_history_handoff_tolerances())
    with pytest.raises(AcceptanceError, match="native history solid.isactive changed"):
        compare_v2_history_handoff(
            source, _history_frame(300.0, isactive_values=[0, 0, 0]),
            dof_abs_tolerances=_handoff_tolerances(),
            history_abs_tolerances=_history_handoff_tolerances())
    with pytest.raises(AcceptanceError, match="wasactive activation history reset"):
        compare_v2_history_handoff(
            source, _history_frame(300.0, wasactive_values=[0, 0, 0]),
            dof_abs_tolerances=_handoff_tolerances(),
            history_abs_tolerances=_history_handoff_tolerances())


def test_history_handoff_rejects_v1_or_incomplete_capture():
    source = _history_frame(300.0)
    legacy = _history_frame(300.0, snapshot_schema="W24-DOF-SNAPSHOT-1")
    with pytest.raises(AcceptanceError, match="complete V2 Xmesh"):
        compare_v2_history_handoff(
            source, legacy, dof_abs_tolerances=_handoff_tolerances(),
            history_abs_tolerances=_history_handoff_tolerances())
    missing = copy.deepcopy(source)
    missing["history_capture"]["expressions"].remove("Duv_rel")
    with pytest.raises(AcceptanceError, match="missing or reordered expressions/units"):
        compare_v2_history_handoff(
            source, missing, dof_abs_tolerances=_handoff_tolerances(),
            history_abs_tolerances=_history_handoff_tolerances())
    missing_dof = copy.deepcopy(source)
    name_index = missing_dof["dofs"]["dofNames"].index("comp1_w")
    replacement_index = missing_dof["dofs"]["dofNames"].index("comp1_u")
    missing_dof["dofs"]["nameInds"] = [
        replacement_index if item == name_index else item for item in missing_dof["dofs"]["nameInds"]
    ]
    with pytest.raises(AcceptanceError, match="lacks required cure/displacement DOF members"):
        compare_v2_history_handoff(
            missing_dof, source, dof_abs_tolerances=_handoff_tolerances(),
            history_abs_tolerances=_history_handoff_tolerances())


@pytest.mark.parametrize("missing_name", ["comp1_T", "comp1_alpha", "comp1_Duv_rel", "comp1_qpost"])
def test_history_handoff_rejects_missing_full_cure_dof_members_even_if_name_table_remains(missing_name):
    source = _history_frame(300.0)
    target = _history_frame(300.0)
    tolerances = _handoff_tolerances()
    tolerances.pop(missing_name)
    for frame in (source, target):
        names = frame["dofs"]["dofNames"]
        missing_name_index = names.index(missing_name)
        replacement_name_index = names.index("comp1_u")
        # Preserve the name table entry while removing all actual Xmesh members
        # for the required field from both sides of the comparison.
        frame["dofs"]["nameInds"] = [
            replacement_name_index if name_index == missing_name_index else name_index
            for name_index in frame["dofs"]["nameInds"]
        ]
    with pytest.raises(AcceptanceError, match="lacks required cure/displacement DOF members"):
        compare_v2_history_handoff(
            source, target, dof_abs_tolerances=tolerances,
            history_abs_tolerances=_history_handoff_tolerances())


def test_history_handoff_rejects_coordinate_axis_count_that_does_not_match_coords_rows():
    source = _history_frame(300.0)
    target = _history_frame(300.0)
    source["dofs"]["coordinate_axes"] = 3
    with pytest.raises(AcceptanceError, match="coordinate_axes does not match"):
        compare_v2_history_handoff(
            source, target, dof_abs_tolerances=_handoff_tolerances(),
            history_abs_tolerances=_history_handoff_tolerances())


def test_history_handoff_requires_all_three_dimensional_displacement_dofs_when_present():
    source = _history_frame(300.0, axes=3)
    target = _history_frame(300.0, axes=3)
    report = compare_v2_history_handoff(
        source, target, dof_abs_tolerances=_handoff_tolerances(axes=3),
        history_abs_tolerances=_history_handoff_tolerances())
    assert report["status"] == "VISIBLE_HISTORY_MATCH_BRANCH_STATE_UNVERIFIED"
    assert report["full_dof_count"] == 8

    changed = _history_frame(300.0, axes=3)
    field_index = changed["dofs"]["dofNames"].index("comp1_v")
    changed["u_real"][changed["dofs"]["solVectorInds"][field_index]] = 0.0
    with pytest.raises(AcceptanceError, match="comp1_v changed by"):
        compare_v2_history_handoff(
            source, changed,
            dof_abs_tolerances=_handoff_tolerances(axes=3),
            history_abs_tolerances=_history_handoff_tolerances())


@pytest.mark.parametrize("missing_name", ["comp1_u", "comp1_v", "comp1_w"])
def test_history_handoff_rejects_each_missing_three_dimensional_displacement_dof(missing_name):
    incomplete = _history_frame(300.0, axes=3)
    name_index = incomplete["dofs"]["dofNames"].index(missing_name)
    replacement_field = "comp1_w" if missing_name == "comp1_u" else "comp1_u"
    replacement_index = incomplete["dofs"]["dofNames"].index(replacement_field)
    incomplete["dofs"]["nameInds"] = [
        replacement_index if item == name_index else item for item in incomplete["dofs"]["nameInds"]
    ]
    with pytest.raises(AcceptanceError, match="lacks required cure/displacement DOF members"):
        compare_v2_history_handoff(
            incomplete, _history_frame(300.0, axes=3),
            dof_abs_tolerances=_handoff_tolerances(axes=3),
            history_abs_tolerances=_history_handoff_tolerances())


def test_gel_stress_free_control_is_independent_and_uses_postgel_pa_samples():
    capture = {
        "case_id": "gel_stress_free_control",
        "native_metrics_readback": True,
        "stress_unit": "Pa",
        "stored_times_s": [0.0, 1.0, 2.0, 2.5, 3.0],
        "wasactive_samples": [
            {"time_s": time, "native_evaluated": True, "expression": "solid.wasactive",
             "coordinates_m": [[50e-6, 50e-6, 50e-6]], "values": [1]}
            for time in (2.5, 3.0)
        ],
        "stress_components_pa": {
            "solid.sx": [0.0, 1e3, 0.0, 0.0, 0.0],
            "solid.sy": [0.0, 1e3, 0.0, 0.0, 0.0],
            "solid.sz": [0.0, 1e3, 0.0, 0.0, 0.0],
            "solid.sxy": [0.0, 0.0, 0.0, 0.0, 0.0],
            "solid.sxz": [0.0, 0.0, 0.0, 0.0, 0.0],
            "solid.syz": [0.0, 0.0, 0.0, 0.0, 0.0],
        },
    }
    receipt = validate_gel_stress_free_control(capture, maximum_abs_stress_pa=1e-6)
    assert receipt["status"] == "GEL_STRESS_FREE_VALUES_MATCH_SOURCE_AUTH_REQUIRED"
    assert receipt["source_identity_authenticated"] is False
    assert receipt["input_native_metrics_readback_assertion"] is True
    assert receipt["max_abs_stress_pa"] == 0.0
    assert receipt["post_gel_sample_times_s"] == [2.0, 2.5, 3.0]
    assert receipt["wasactive_post_gel_native"] is True
    assert receipt["wasactive_samples_are_caller_assertions"] is True
    assert receipt["independent_from_maxwell_decay"] is True
    assert receipt["native_activation_semantics_verified"] is False

    nonzero = copy.deepcopy(capture)
    nonzero["stress_components_pa"]["solid.sx"][3] = 1.0
    with pytest.raises(AcceptanceError, match="exceeds"):
        validate_gel_stress_free_control(nonzero, maximum_abs_stress_pa=1e-6)
    no_postgel_checkpoint = copy.deepcopy(capture)
    no_postgel_checkpoint["stored_times_s"].remove(3.0)
    for values in no_postgel_checkpoint["stress_components_pa"].values():
        values.pop()
    with pytest.raises(AcceptanceError, match="lacks exact post-gel checkpoint 3 s"):
        validate_gel_stress_free_control(no_postgel_checkpoint, maximum_abs_stress_pa=1e-6)
    inactive = copy.deepcopy(capture)
    inactive["wasactive_samples"][0]["values"] = [0]
    with pytest.raises(AcceptanceError, match="not fully activated"):
        validate_gel_stress_free_control(inactive, maximum_abs_stress_pa=1e-6)
    nonfinite_pre_gel = copy.deepcopy(capture)
    nonfinite_pre_gel["stress_components_pa"]["solid.sx"][0] = float("nan")
    with pytest.raises(AcceptanceError, match="is not finite"):
        validate_gel_stress_free_control(nonfinite_pre_gel, maximum_abs_stress_pa=1e-6)
    missing_component = copy.deepcopy(capture)
    # A zero-valued xx series cannot stand in for omitted yz stress.
    missing_component["stress_components_pa"].pop("solid.syz")
    with pytest.raises(AcceptanceError, match="exact six native stress components"):
        validate_gel_stress_free_control(missing_component, maximum_abs_stress_pa=1e-6)
    unknown_component = copy.deepcopy(capture)
    unknown_component["stress_components_pa"]["solid.szz"] = [0.0] * 5
    with pytest.raises(AcceptanceError, match=r"unknown=\['solid.szz'\]"):
        validate_gel_stress_free_control(unknown_component, maximum_abs_stress_pa=1e-6)


def test_java_v2_fixtures_are_build_only_and_keep_v1_coupon_path():
    coupon = COUPON_FIXTURE.read_text(encoding="utf-8")
    control = CONTROL_FIXTURE.read_text(encoding="utf-8")
    assert 'if ("build_v2".equals(phase)) return buildV2(model, args);' in coupon
    assert 'if ("readback_v2".equals(phase)) return readbackV2(model);' in coupon
    assert 'set("rate", "(kUV*IUV+kT)*(1-alpha)")' in coupon
    assert 'model.component(COMPONENT).variable("v1").set("rate", "(kUV*Irel+kT)*(1-alpha)")' in coupon
    assert '"S_uv*exp(-muUV*(zUVSurface-z))"' in coupon
    assert 'set("f", "Irel")' in coupon
    assert 'set("CustomDependentVariableUnit", "s")' in coupon
    assert 'set("SourceTermQuantity", "dimensionless")' in coupon
    assert 'set("CustomSourceTermUnit", "1")' in coupon
    assert 'set("activation_expression", "alpha>=alpha_gel || solid.wasactive")' in coupon
    assert 'activation.set("actfac"' not in coupon
    assert 'set("MaterialModel", "GeneralizedMaxwell")' in coupon
    assert 'set("deformationModel", "full")' in coupon
    assert 'set("Kvm_v", new String[]{"Kbranch"})' in coupon
    assert 'set("Gvm", new String[]{"Gbranch"})' in coupon
    assert 'set("tauvm", new String[]{"tauMaxwell"})' in coupon

    for token in (
        '"build_maxwell_ramp_hold"', '"build_gel_stress_free"',
        '"readback_maxwell_ramp_hold"', '"readback_gel_stress_free"',
        '"Direction", new String[]{"prescribed", "prescribed", "prescribed"}',
        '"U0", new String[]{"epsFinal*min(t/Tramp,1)*x", "0[m]", "0[m]"}',
        '"U0", new String[]{"epsPreGel*min(t/Tramp,1)*x", "0[m]", "0[m]"}',
        '"range(0[s],1[s],901[s])"',
        '"t>=tGel || solid.wasactive"',
    ):
        assert token in control
    assert 'activation.set("actfac"' not in control
    for source in (coupon, control):
        assert "study.run(" not in source
        assert ".compute(" not in source
