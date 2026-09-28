from __future__ import annotations

import json
import gzip
import hashlib
import shutil
import struct
import sys
import uuid
from pathlib import Path

import pytest

from tools import run_native_w24_cure_science as runner


def _equation_view_inventory(work_root: Path):
    path = work_root / "solid_equation_view_expression_inventory.json"
    rows = [
        ["fixture.var_1", "Radial normal Cauchy stress", "Pa"],
        ["fixture.var_2", "Axial normal Cauchy stress", "Pa"],
        ["fixture.var_3", "Hoop normal Cauchy stress", "Pa"],
        ["fixture.var_4", "r-z shear stress", "Pa"],
    ]
    cues = [
        ["stress", "cauchy", "radial"],
        ["stress", "cauchy"],
        ["stress", "cauchy", "hoop"],
        ["stress", "shear"],
    ]
    candidates = [
        {"row_index": index, "cues": cues[index], "raw_row": row}
        for index, row in enumerate(rows)
    ]
    artifact = {
        "schema": "W24_COMSOL_EQUATION_VIEW_EXPRESSION_INVENTORY_V1",
        "status": "COMPLETE_NOT_EVALUATED",
        "complete": True,
        "read_only": True,
        "model_mutations": 0,
        "study_run_calls": 0,
        "component_tag": "comp1",
        "observed_component_physics_tags": ["ht", "solid", "ode"],
        "solid_physics_tags": ["solid"],
        "feature_tags_are_native_observations": True,
        "table_request": ["Expression", "recursive", "all"],
        "table_request_source": "COMSOL 6.4 FeatureInfo.getInfoTable(String,String...) API documentation",
        "candidate_rule": (
            "candidate rows have case-insensitive stress/cauchy/shear text or a "
            "solid.s[a-z0-9_]* identifier; optional component cues are "
            "hoop/radial/circumferential/azimuthal; full raw rows are preserved; discovery only"
        ),
        "feature_count": 1,
        "expression_row_count": len(rows),
        "stress_candidate_row_count": len(candidates),
        "errors": [],
        "feature_tables": [{
            "physics_tag": "solid", "feature_tag": "lemm1", "status": "READ",
            "row_count": len(rows), "raw_rows": rows,
            "stress_candidate_rows": candidates,
        }],
    }
    raw = (json.dumps(artifact, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    path.write_bytes(raw)
    return {
        "path": str(path),
        "size_bytes": len(raw),
        "sha256": runner.sha256(path),
        "feature_count": 1,
        "expression_row_count": len(rows),
        "stress_candidate_row_count": len(candidates),
        "status": "COMPLETE_NOT_EVALUATED",
        "scope": "complete raw Solid Mechanics Equation View table inventory; candidates are not evaluated variables",
    }


def _write_java_utf(stream, value: str):
    encoded = value.encode("utf-8")
    stream.write(struct.pack(">H", len(encoded)))
    stream.write(encoded)


def _write_solution_snapshot(path: Path, times: list[float]):
    with gzip.open(path, "wb") as stream:
        _write_java_utf(stream, "W24-DOF-SNAPSHOT-1")
        stream.write(struct.pack(">i", 2))
        stream.write(struct.pack(">i", 1))
        _write_java_utf(stream, "comp1.T")
        stream.write(struct.pack(">i", 1))
        stream.write(struct.pack(">iiii", 1, 1, 0, 0))
        stream.write(struct.pack(">dd", 0.0, 0.0))
        stream.write(struct.pack(">i", len(times)))
        for index, time_s in enumerate(times):
            stream.write(struct.pack(">di", time_s, 1))
            stream.write(struct.pack(">d", float(index)))


def _write_solution_snapshot_v2(path: Path, times: list[float], *, axes=3):
    names = ["comp1_T", "comp1_u", "comp1_v", "comp1_w"]
    points = [
        (1, 1, 0, 0, [0.0, 0.0, 0.0]),
        (1, 2, 1, 1, [1e-6, 0.0, 0.0]),
        (1, 3, 2, 2, [0.0, 1e-6, 0.0]),
        (1, 4, 3, 3, [0.0, 0.0, 1e-6]),
    ]
    with gzip.open(path, "wb") as stream:
        _write_java_utf(stream, "W24-DOF-SNAPSHOT-2")
        stream.write(struct.pack(">i", axes))
        stream.write(struct.pack(">i", len(names)))
        for name in names:
            _write_java_utf(stream, name)
        stream.write(struct.pack(">i", len(points)))
        for geom, node, name_index, vector_index, coordinates in points:
            stream.write(struct.pack(">iiii", geom, node, name_index, vector_index))
            stream.write(struct.pack(">" + "d" * axes, *coordinates[:axes]))
        stream.write(struct.pack(">i", len(times)))
        for index, time_s in enumerate(times):
            stream.write(struct.pack(">di", time_s, len(names)))
            stream.write(struct.pack(">" + "d" * len(names), *[float(index + j) for j in range(len(names))]))


def _write_solution_snapshot_v3(path: Path, times: list[float], *,
                                vector_indices=(1, 0), vector_length=2):
    axes = 3
    field_names = ["comp1_T", "comp1_alpha"]
    field_counts = [1, 1]
    dof_names = ["comp1_T", "comp1_alpha"]
    points = [
        (1, 1, 0, vector_indices[0], (0.0, 0.0, 0.0)),
        (1, 2, 1, vector_indices[1], (1e-6, 0.0, 0.0)),
    ]
    with gzip.open(path, "wb") as stream:
        _write_java_utf(stream, "W24-DOF-SNAPSHOT-3")
        stream.write(struct.pack(">ii", axes, len(points)))
        stream.write(struct.pack(">i", len(field_names)))
        for name, count in zip(field_names, field_counts):
            _write_java_utf(stream, name)
            stream.write(struct.pack(">i", count))
        stream.write(struct.pack(">i", len(dof_names)))
        for name in dof_names:
            _write_java_utf(stream, name)
        stream.write(struct.pack(">i", len(points)))
        for geom, node, name_index, vector_index, coordinates in points:
            stream.write(struct.pack(">iiii", geom, node, name_index, vector_index))
            stream.write(struct.pack(">ddd", *coordinates))
        stream.write(struct.pack(">i", 1))
        _write_java_utf(stream, "geom1")
        stream.write(struct.pack(">i", 1))
        _write_java_utf(stream, "mesh1")
        stream.write(struct.pack(">ii", 2, 3))
        _write_java_utf(stream, "comp1_T")
        _write_java_utf(stream, "comp1_alpha")
        stream.write(struct.pack(">dddddd", 0.0, 1e-6, 0.0, 0.0, 0.0, 0.0))
        stream.write(struct.pack(">ii", 3, 2))
        stream.write(struct.pack(">dddddd", 0.0, 1e-6, 0.0, 0.0, 0.0, 0.0))
        stream.write(struct.pack(">ii", 1, 2))
        stream.write(struct.pack(">ii", 1, 2))
        stream.write(struct.pack(">i", 2))
        stream.write(struct.pack(">ii", 0, 1))
        stream.write(struct.pack(">i", len(times)))
        for index, time_s in enumerate(times):
            values = [float(index + j + 1) for j in range(vector_length)]
            stream.write(struct.pack(">di", time_s, len(values)))
            stream.write(struct.pack(">" + "d" * len(values), *values))


def _metric_readback(feature_type, units, dimension, axisymmetric_key, *, expression=None,
                     time_count=2, entity_count=1):
    return {"native_tag": f"test_{feature_type}", "type": feature_type, "dataset": "dset1",
            "expression": list(expression or []), "unit": units, "solution_selection": "all",
            "geometry": "geom1" if dimension is not None else None,
            "entity_dimension": dimension,
            "entity_ids": list(range(1, entity_count + 1)) if dimension is not None else [],
            "axisymmetric_measure_property": axisymmetric_key,
            "axisymmetric_measure_value": "on" if axisymmetric_key is not None else None,
            "shape": [len(units), time_count, 3] if feature_type == "Interp" else [len(units), time_count]}


def _test_runtime_environment():
    executable = str(Path(sys.executable).absolute())
    resolved = str(Path(sys.executable).resolve())
    distributions = []
    digest = hashlib.sha256(json.dumps(distributions, sort_keys=True, separators=(",", ":"),
                                      ensure_ascii=False).encode()).hexdigest()
    version = " ".join(str(value) for value in sys.version_info[:3])
    return {
        "python_executable": executable,
        "python_resolved_executable": resolved,
        "python_version": ".".join(str(value) for value in sys.version_info[:3]),
        "distributions": distributions,
        "distributions_sha256": digest,
        "ignored_appledouble_metadata": [],
        "pip_check_python": executable,
        "pip_check_python_resolved": resolved,
        "pip_check_python_version": version,
        "pip_check_version": "synthetic-test-only",
    }


def _synthetic_template_candidate(tmp_path: Path):
    work_root = Path("/private/tmp") / f"comsol-mcp-w24-cure-science-lifecycle-{uuid.uuid4().hex}"
    workspace = work_root / "project" / "science"
    workspace.mkdir(parents=True)
    template = workspace / "cure_template.mph"
    template.write_bytes(b"synthetic native template bytes; test only")
    inventory = _equation_view_inventory(workspace)
    inventory["registered_project_path"] = inventory["path"]
    fixture_source = workspace / "W24CureCouponFixture.java"
    fixture_source.write_text("// synthetic setup source", encoding="utf-8")
    classes_dir = workspace / "offline-classes"
    classes_dir.mkdir()
    (classes_dir / "W24CureCouponFixture.class").write_bytes(b"synthetic class")
    evidence_dir = work_root / "setup-evidence"
    evidence_dir.mkdir()
    creation_path = evidence_dir / "project_workspace_created_before_birth.json"
    project_record = {
        "project_id": "project-w24-lifecycle-test", "workspace": str(workspace),
        "policy": {"permissions": ["inspect", "project_write", "compute", "trusted_code"]},
    }
    creation = {
        "status": "PROJECT_CREATED_BEFORE_ENGINE_BIRTH",
        "project_id": project_record["project_id"], "workspace": str(workspace),
        "host_grants": {"startup_ceiling": ["trusted_code"],
                        "effective_host_permissions": ["trusted_code"]},
        "response": {"success": True, "data": {"project": project_record}},
        "inspection_response": {"success": True, "data": {
            "project": project_record,
            "effective_permissions": ["inspect", "project_write", "compute", "trusted_code"],
        }},
    }
    creation_path.write_text(json.dumps(creation), encoding="utf-8")
    receipt = {
        "status": "NATIVE_CONFIGURED_TEMPLATE_SAVED_NOT_SOLVED",
        "reopen_status": "NATIVE_TEMPLATE_REOPEN_READBACK_PASS_NOT_SOLVED",
        "native_solver_submissions": [],
        "engine_identity": {"matches_frozen_target": True},
        "freeze_sha256": "b" * 64,
        "path": str(template), "size_bytes": template.stat().st_size,
        "sha256": runner.sha256(template),
        "project_id": project_record["project_id"],
        "project_workspace": str(workspace),
        "project_creation_receipt": str(creation_path),
        "project_creation_receipt_sha256": runner.sha256(creation_path),
        "fixture_source_path": str(fixture_source),
        "fixture_source_sha256": runner.sha256(fixture_source),
        "fixture_compile_output_dir": str(classes_dir),
        "fixture_compile_classes": ["W24CureCouponFixture.class"],
        "fixture_readback": {
            "status": "BUILT_NOT_SOLVED", "geometry_dimension": 2,
            "geometry_axisymmetric": True, "geometry_domain_count": 4,
            "domain_readbacks": {name: {"relative_volume_error": 0.0}
                                 for name in ("alumina", "gold", "adhesive", "fiber")},
            "solver_readbacks": {name: {} for name in ("stdUV", "stdBake", "stdCool")},
            "solid_quasistatic_readback": "Quasistatic",
        },
        "equation_view_inventory": inventory,
        "runtime_environment": _test_runtime_environment(),
    }
    receipt_path = work_root / "configured_template_receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    candidate_path = tmp_path / f"w24-candidate-{uuid.uuid4().hex}.json"
    candidate = runner.prepare_campaign_candidate(receipt_path, candidate_path)
    candidate_data = json.loads(candidate_path.read_text(encoding="utf-8"))
    approval_path = tmp_path / f"w24-approval-{uuid.uuid4().hex}.json"
    approval = {
        "schema": "W24_ROOT_NATIVE_CAMPAIGN_APPROVAL_V1",
        "status": runner.APPROVAL_STATUS,
        "candidate_id": runner.CANDIDATE_ID,
        "freeze_sha256": candidate["freeze_sha256"],
        "native_scope": candidate_data["native_scope"],
        "approval_authority": "root", "no_automatic_retry": True,
    }
    approval_path.write_text(json.dumps(approval), encoding="utf-8")
    return work_root, receipt_path, candidate_path, candidate, approval_path


class _FakeCampaignLifecycle:
    """Synthetic no-COMSOL lifecycle for CLI call-order/failure tests only."""

    def __init__(self, *, fail_at=None, cleanup_safe=True):
        self.events = []
        self.fail_at = fail_at
        self.cleanup_safe = cleanup_safe
        self.birth = {"birth_observed": True, "pid": 777, "birth": "synthetic-birth",
                      "command": "synthetic-comsol", "port": 54321,
                      "birth_epoch_s": 1000.0,
                      "process_identity": {"pid": 777, "birth": "synthetic-birth",
                                           "command": "synthetic-comsol"}}
        self.budget = None

    def prepare_prebirth(self, *, setup, candidate, work, evidence):
        self.events.extend(["prebirth", "project.create", "stage.inputs", "compile.fixture"])
        if self.fail_at == "prebirth":
            raise RuntimeError("synthetic prebirth refusal")
        return {"status": "PREBIRTH_GATES_PASSED", "scope": "SYNTHETIC_TEST_ONLY",
                "engine_births": 0, "study_run_submissions": 0}

    def start_server(self):
        self.events.append("server.birth")
        if self.fail_at == "server":
            raise TimeoutError("synthetic listener readback failure after birth")
        return dict(self.birth)

    def observe_server_birth(self):
        return dict(self.birth) if "server.birth" in self.events else {"birth_observed": False}

    def start_worker(self, *, birth_budget):
        self.events.append("worker.connect")
        if self.fail_at == "worker":
            raise TimeoutError("synthetic direct Worker startup not terminal")
        self.budget = birth_budget
        return {"status": "WORKER_CONNECTED_LOOPBACK_PROOF_PASSED", "scope": "SYNTHETIC_TEST_ONLY"}

    def create_adapter(self, *, stress_components, work, evidence):
        self.events.append("adapter.create")
        return object()

    def run_controller(self, adapter, *, birth_budget, stress_components, work, evidence):
        del adapter, stress_components, work, evidence
        self.events.append("controller.run")
        self.budget = birth_budget
        return {"status": "SYNTHETIC_TEST_ONLY", "synthetic_slot_count": 10,
                "birth_budget": birth_budget.receipt(now_epoch_s=1010.0),
                "native_study_run_submissions": 0}

    def reconcile_for_cleanup(self):
        self.events.append("cleanup.reconcile")
        return {"status": "SAFE_FOR_TEST_CLEANUP" if self.cleanup_safe else "ACTIVE_OR_UNKNOWN",
                "safe_for_owned_cleanup": self.cleanup_safe,
                "scope": "SYNTHETIC_TEST_ONLY"}

    def cleanup_exact_owned(self, reconciliation):
        assert reconciliation.get("safe_for_owned_cleanup") is True
        self.events.append("cleanup.exact")
        return {"status": "CLEANUP_VERIFIED_EXACT_OWNED_PIDS_AND_PORTS_ABSENT",
                "scope": "SYNTHETIC_TEST_ONLY"}


class _FakeScienceAdapter:
    def __init__(self, evidence: Path, *, fail_slot: int | None = None,
                 fail_after_ledger: bool = False, wrong_mechanics_model: bool = False):
        self.evidence = evidence
        self.fail_slot = fail_slot
        self.fail_after_ledger = fail_after_ledger
        self.wrong_mechanics_model = wrong_mechanics_model
        self.run_calls: list[tuple[str, str]] = []
        self.events: list[str] = []
        self.ledger_path: Path | None = None
        self.stress_components = None
        self.mechanics_bindings = runner.MechanicsModelBindings("project-native")

    def prepare_campaign(self, stress_components, *, timeout_s):
        assert set(stress_components) == set(runner._STRESS_ROLES)
        self.stress_components = dict(stress_components)
        assert timeout_s > 0
        for case_id in sorted(runner.MechanicsModelBindings.CASES):
            tag = f"w24mech_{case_id}_native"
            model_ref = {"schema_version": 1, "session_id": "session-native",
                         "server_instance_id": "server-native", "model_tag": tag,
                         "generation": 1}
            response = {"success": True, "execution": {
                "project_id": "project-native", "session_id": "session-native",
                "model_ref": model_ref, "revision": 0}}
            self.mechanics_bindings.register(case_id, response, expected_model_tag=tag)
        self.events.append("prepare")
        return {"status": "NATIVE_CAMPAIGN_PREPARED", "worker_sessions": 1,
                "mechanics_model_bindings": self.mechanics_bindings.as_records()}

    def configure_slot(self, slot, *, timeout_s):
        assert timeout_s > 0
        self.events.append(f"configure:{slot.study_tag}")
        result = {"status": "NATIVE_SLOT_CONFIGURED", "model_copy": slot.case_id}
        if slot.case_id in runner.MechanicsModelBindings.CASES:
            binding = self.mechanics_bindings.for_case(slot.case_id)
            record = binding.as_record()
            if self.wrong_mechanics_model:
                parent_tag = "w24-cure-coupon-parent"
                record = {**record, "model_tag": parent_tag,
                          "model_ref": {**record["model_ref"], "model_tag": parent_tag}}
            result["model_binding"] = record
        return result

    def run_study(self, slot, *, ledger_path, save_after_success_path, timeout_s):
        assert timeout_s > 0
        self.ledger_path = ledger_path
        self.run_calls.append((slot.case_id, slot.study_tag))
        row = {"event": "study_run_submitted", "submission_index": len(self.run_calls),
               "case_id": slot.case_id, "study_tag": slot.study_tag,
               "at_utc": f"2026-09-27T01:00:{len(self.run_calls):02d}Z"}
        with ledger_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row) + "\n")
            stream.flush()
            import os
            os.fsync(stream.fileno())
        self.events.append(f"run:{slot.study_tag}")
        if self.fail_slot == len(self.run_calls) and self.fail_after_ledger:
            raise TimeoutError("simulated terminal/unobserved Worker request after durable invocation")
        save_path_text = None
        if save_after_success_path is not None:
            save_after_success_path.parent.mkdir(parents=True, exist_ok=True)
            save_after_success_path.write_bytes(b"staged baseline mph")
            save_path_text = str(save_after_success_path)
            self.events.append("immediate-save")
        return {"status": "NATIVE_STUDY_RUN_RETURNED", "submission_index": len(self.run_calls),
                "study_run_calls_from_this_action": 1,
                "immediate_save_path": save_path_text}

    def capture_solution(self, slot, *, output_dir, timeout_s):
        assert timeout_s > 0
        output_dir.mkdir(parents=True, exist_ok=True)
        field_path = output_dir / "field_snapshot.bin.gz"
        times = [0.0, 1500.0] if slot.case_id not in runner.MechanicsModelBindings.CASES else [0.0]
        _write_solution_snapshot(field_path, times)
        field = {"path": str(field_path), "size_bytes": field_path.stat().st_size,
                 "sha256": runner.sha256(field_path), "real_solution": True}
        result = {"status": "NATIVE_RAW_SNAPSHOT_CAPTURED", "field_snapshot": field,
                  "stored_times_s": times}
        if slot.case_id in runner.MechanicsModelBindings.CASES:
            mechanics_path = output_dir / "mechanics_metrics.json"
            dataset = "dset_mechanics_test"
            expressions = [self.stress_components[role]["expression"] for role in runner.STRESS_ROLES]
            pressure = -2e9 / (3.0 * (1.0 - 2.0 * 0.35)) * 3e-4
            if slot.case_id == "mechanics_free_expansion":
                means = {role: 0.0 for role in runner.STRESS_ROLES}
                maxima = {role: 0.0 for role in runner.STRESS_ROLES}
                displacement_max = [10e-9, 20e-9]
                displacement_probe = [10e-9, 20e-9]
            else:
                means = {role: (pressure if role != "rz_shear" else 0.0)
                         for role in runner.STRESS_ROLES}
                maxima = {role: (abs(pressure) if role != "rz_shear" else 0.0)
                          for role in runner.STRESS_ROLES}
                displacement_max = [0.0, 0.0]
                displacement_probe = [0.0, 0.0]

            def mechanics_row(feature_type, units, exprs, shape, *, dimension=None, measure=None):
                return {"type": feature_type, "dataset": dataset, "expression": list(exprs),
                        "unit": list(units), "solution_selection": "all", "shape": list(shape),
                        "geometry": "geom1" if dimension is not None else None,
                        "entity_dimension": dimension,
                        "entity_ids": [1] if dimension is not None else [],
                        "axisymmetric_measure_property": measure,
                        "axisymmetric_measure_value": "on" if measure else None}

            mechanics_artifact = {
                "schema": "W24_NATIVE_MECHANICS_METRICS_V1",
                "status": "NATIVE_MECHANICS_METRICS_CAPTURED", "native": True,
                "native_study_run_calls": 0, "case_id": slot.case_id,
                "dataset_tag": dataset, "stored_times_s": [0.0],
                "stress_component_descriptors": self.stress_components,
                "stress_mean_pa": means, "stress_abs_max_pa": maxima,
                "displacement_abs_max_m": displacement_max,
                "displacement_probe_m": displacement_probe,
                "displacement_probe_coordinate_m": [100e-6, 200e-6],
                "feature_readbacks": {
                    "stress_mean": mechanics_row("AvVolume", ["Pa"] * 4, expressions,
                                                  [4, 1], dimension=2, measure="intvolume"),
                    "stress_abs_max": mechanics_row("MaxVolume", ["Pa"] * 4,
                                                     [f"abs({expr})" for expr in expressions],
                                                     [4, 1], dimension=2),
                    "displacement_abs_max": mechanics_row("MaxVolume", ["m", "m"],
                                                           ["abs(u)", "abs(w)"], [2, 1], dimension=2),
                    "displacement_probe": {
                        "type": "Interp", "dataset": dataset, "expression": ["u", "w"],
                        "unit": ["m", "m"], "solution_selection": "all",
                        "coordinate_error": "on", "math_error": "on",
                        "coordinates_m": [[100e-6, 200e-6]], "shape": [2, 1, 1]},
                },
            }
            mechanics_path.write_text(json.dumps(mechanics_artifact), encoding="utf-8")
            result["mechanics_metrics"] = {
                "status": "NATIVE_MECHANICS_METRICS_CAPTURED", "case_id": slot.case_id,
                "path": str(mechanics_path), "size_bytes": mechanics_path.stat().st_size,
                "sha256": runner.sha256(mechanics_path), "temporary_dataset_removed": True,
                "temporary_numerical_tags_removed": ["w24m1", "w24m2", "w24m3", "w24mi4"],
            }
        else:
            metrics_path = output_dir / "native_metrics.json"
            readbacks = {
                "adhesive_cure_averages": _metric_readback(
                    "AvVolume", ["1", "1", "1"], 2, "intvolume",
                    expression=("alpha_iso", "alpha", "qpost")),
                "energy_alumina": _metric_readback("IntVolume", ["J"], 2, "intvolume"),
                "energy_gold": _metric_readback("IntVolume", ["J"], 2, "intvolume"),
                "energy_adhesive": _metric_readback("IntVolume", ["J"], 2, "intvolume"),
                "energy_fiber": _metric_readback("IntVolume", ["J"], 2, "intvolume"),
                "reaction_power": _metric_readback("IntVolume", ["W"], 2, "intvolume",
                                                   expression=("Qrxn",)),
                "outward_power": _metric_readback("IntSurface", ["W"], 1, "intsurface",
                                                   expression=("hconv*(T-Tenv)",), entity_count=8),
                "adhesive_mean_stress": _metric_readback(
                    "AvVolume", ["Pa"] * 4, 2, "intvolume",
                    expression=tuple(self.stress_components[role]["expression"] for role in runner.STRESS_ROLES)),
                "stress_probes": {**_metric_readback(
                    "Interp", ["Pa"] * 4, None, None,
                    expression=tuple(self.stress_components[role]["expression"] for role in runner.STRESS_ROLES)),
                                  "coordinate_error": "on", "math_error": "on"},
                "fiber_end_displacement": {
                    "native_tag": "test_fiber_end", "type": "Interp", "dataset": "dset1",
                    "expression": ["u", "w"], "unit": ["m", "m"],
                    "solution_selection": "all", "coordinate_error": "on", "math_error": "on",
                    "coordinates_m": [[62.5e-6, 1050e-6]], "shape": [2, len(times), 1]},
            }
            metrics = {
                "schema": "W24_NATIVE_CURE_METRICS_V1", "status": "NATIVE_CURE_METRICS_CAPTURED",
                "native": True, "native_study_run_calls": 0, "solver_tag": slot.study_tag,
                "dataset_tag": "dset1", "stored_times_s": times,
                "alpha_iso_mean": [0.2 for _ in times], "alpha_mean": [0.2 for _ in times],
                "qpost_mean": [0.0 for _ in times],
                "energy_samples": [
                    {"time_s": t, "stored_energy_j": 0.0,
                     "stored_energy_by_domain_j": {k: 0.0 for k in ("alumina", "gold", "adhesive", "fiber")},
                     "reaction_power_w": 0.0, "outward_power_w": 0.0,
                     "outward_sign": "positive_outward"} for t in times],
                "stress_component_descriptors": self.stress_components,
                "adhesive_mean_stress_pa": {role: [0.0 for _ in times] for role in runner.STRESS_ROLES},
                "stress_probe_values_pa": {role: [[0.0, 0.0, 0.0] for _ in times]
                                            for role in runner.STRESS_ROLES},
                "stress_probe_coordinates_m": [list(row) for row in runner.STRESS_PROBE_COORDINATES_M],
                "feature_readbacks": readbacks,
                "fiber_end_displacement_m": [[0.0, 0.0] for _ in times],
                "axisymmetric_physical_measure_readback": "intvolume=on; intsurface=on",
                "material_heat_capacity_readback": [
                    ["alumina", "3900[kg/m^3]", "880[J/(kg*K)]", "rhoAl"],
                    ["gold", "19300[kg/m^3]", "129[J/(kg*K)]", "rhoAu"],
                    ["adhesive", "1200[kg/m^3]", "1000[J/(kg*K)]", "rhoAdh"],
                    ["fiber", "2200[kg/m^3]", "703[J/(kg*K)]", "rhoFiber"],
                ],
            }
            metrics_path.write_text(json.dumps(metrics), encoding="utf-8")
            result["native_metrics"] = {"status": "NATIVE_CURE_METRICS_CAPTURED",
                                        "path": str(metrics_path),
                                        "size_bytes": metrics_path.stat().st_size,
                                        "sha256": runner.sha256(metrics_path),
                                        "temporary_numerical_tags_removed": [f"w24n{i}" for i in range(8)] + ["w24i8", "w24i9"],
                                        "temporary_dataset_removed": True}
        self.events.append(f"capture:{slot.study_tag}")
        return result

    def validate_slot(self, slot, capture, prior_captures, *, timeout_s):
        assert timeout_s > 0
        return {"status": "PASS", "scope": "fake orchestration-only test result"}

    def reopen_staged_baseline(self, saved_path, *, timeout_s):
        assert saved_path.is_file()
        assert timeout_s > 0
        assert self.ledger_path is not None
        assert len(runner.read_solve_ledger(self.ledger_path)) == 5
        self.events.append("reopen-worker-2")
        return {"status": "NATIVE_REOPEN_READBACK_PASS", "study_run_submissions": 5}


def _native_metric_fixture(tmp_path: Path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    adapter = _FakeScienceAdapter(evidence)
    adapter.stress_components = {
        role: {"expression": f"solid.{role}", "unit": "Pa",
               "description": f"native {role} Cauchy stress"}
        for role in runner.STRESS_ROLES
    }
    capture = adapter.capture_solution(
        runner.SolveSlot("staged_baseline", "stdUV", "unit fixture"),
        output_dir=evidence / "slot", timeout_s=5.0)
    return evidence, adapter.stress_components, capture


def test_equation_view_inventory_gate_keeps_candidates_unverified_until_solved(tmp_path):
    private = Path("/private/tmp") / f"comsol-mcp-w24-cure-science-inventory-{uuid.uuid4().hex}"
    private.mkdir()
    try:
        inventory = _equation_view_inventory(private)
        validated = runner.validate_equation_view_inventory(inventory)
        assert validated["status"] == "COMPLETE_NOT_EVALUATED"
        assert validated["stress_semantics"] == "DOCUMENTED_NATIVE_TABLE_CANDIDATES_NOT_EVALUATED"
        assert validated["stress_candidate_row_count"] == 4
        assert "axisymmetric_cauchy_stress_components" not in validated

        artifact_path = Path(inventory["path"])
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        artifact["feature_tables"][0]["raw_rows"].pop()
        artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
        inventory["size_bytes"] = artifact_path.stat().st_size
        inventory["sha256"] = runner.sha256(artifact_path)
        with pytest.raises(runner.CampaignError, match="raw rows are truncated"):
            runner.validate_equation_view_inventory(inventory)
    finally:
        shutil.rmtree(private, ignore_errors=True)


def test_stress_roles_come_from_unique_native_descriptions_and_pa_units(tmp_path):
    private = Path("/private/tmp") / f"comsol-mcp-w24-cure-science-stress-map-{uuid.uuid4().hex}"
    private.mkdir()
    try:
        inventory = _equation_view_inventory(private)
        mapped = runner.map_stress_components(inventory)
        assert set(mapped) == set(runner._STRESS_ROLES)
        assert mapped["radial_normal"]["expression"] == "fixture.var_1"
        assert mapped["rz_shear"]["unit"] == "Pa"

        artifact_path = Path(inventory["path"])
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        artifact["feature_tables"][0]["raw_rows"].append(
            ["fixture.var_5", "An additional radial normal stress", "Pa"])
        artifact["feature_tables"][0]["row_count"] += 1
        artifact["feature_tables"][0]["stress_candidate_rows"].append(
            {"row_index": 4, "cues": runner._expression_candidate_cues(
                artifact["feature_tables"][0]["raw_rows"][4]),
             "raw_row": artifact["feature_tables"][0]["raw_rows"][4]})
        artifact["expression_row_count"] += 1
        artifact["stress_candidate_row_count"] += 1
        raw = (json.dumps(artifact, separators=(",", ":")) + "\n").encode()
        artifact_path.write_bytes(raw)
        inventory.update({"size_bytes": len(raw), "sha256": runner.sha256(artifact_path),
                          "expression_row_count": 5, "stress_candidate_row_count": 5})
        with pytest.raises(runner.CampaignError, match="do not uniquely identify"):
            runner.map_stress_components(inventory)
    finally:
        shutil.rmtree(private, ignore_errors=True)


def test_java_solution_snapshot_reader_streams_exact_dofs_and_times(tmp_path):
    path = tmp_path / "fields.bin.gz"
    _write_solution_snapshot(path, [0.0, 1.0, 2.0])
    frames = list(runner.iter_solution_snapshot(path))
    assert [frame["time_s"] for frame in frames] == [0.0, 1.0, 2.0]
    assert frames[1]["dofs"]["dofNames"] == ["comp1.T"]
    assert frames[1]["u_real"] == [1.0]

    truncated = tmp_path / "truncated.bin.gz"
    _write_solution_snapshot(truncated, [0.0, 1.0])
    truncated.write_bytes(truncated.read_bytes()[:-6])
    with pytest.raises((runner.CampaignError, EOFError)):
        list(runner.iter_solution_snapshot(truncated))


def test_java_solution_snapshot_v2_preserves_full_three_dimensional_xmesh_and_v1_stays_compatible(tmp_path):
    path = tmp_path / "fields-v2.bin.gz"
    _write_solution_snapshot_v2(path, [0.0, 1.0])
    frames = list(runner.iter_solution_snapshot(path))
    assert [frame["time_s"] for frame in frames] == [0.0, 1.0]
    assert frames[0]["dofs"]["snapshot_schema"] == "W24-DOF-SNAPSHOT-2"
    assert frames[0]["dofs"]["coordinate_axes"] == 3
    assert frames[0]["dofs"]["complete_xmesh_dofs"] is True
    assert frames[0]["dofs"]["coords"] == [[0.0, 1e-6, 0.0, 0.0], [0.0, 0.0, 1e-6, 0.0], [0.0, 0.0, 0.0, 1e-6]]
    assert frames[1]["u_real"] == [1.0, 2.0, 3.0, 4.0]

    invalid_axes = tmp_path / "fields-v2-invalid-axes.bin.gz"
    _write_solution_snapshot_v2(invalid_axes, [0.0], axes=1)
    with pytest.raises(runner.CampaignError, match="coordinate/name dimension"):
        list(runner.iter_solution_snapshot(invalid_axes))

    # V1 remains readable after adding the 3D V2 format.
    legacy = tmp_path / "legacy-v1.bin.gz"
    _write_solution_snapshot(legacy, [0.0, 1.0])
    assert [frame["dofs"]["snapshot_schema"] for frame in runner.iter_solution_snapshot(legacy)] == [
        "W24-DOF-SNAPSHOT-1", "W24-DOF-SNAPSHOT-1"]


def test_java_solution_snapshot_v3_preserves_fields_local_maps_full_vectors_and_mapping_diagnostics(tmp_path):
    path = tmp_path / "fields-v3.bin.gz"
    _write_solution_snapshot_v3(path, [0.0, 1.0])
    frames = list(runner.iter_solution_snapshot(path))
    assert [frame["time_s"] for frame in frames] == [0.0, 1.0]
    metadata = frames[0]["dofs"]
    assert metadata["snapshot_schema"] == "W24-DOF-SNAPSHOT-3"
    assert metadata["coordinate_axes"] == len(metadata["coords"]) == 3
    assert metadata["fieldNames"] == ["comp1_T", "comp1_alpha"]
    assert metadata["fieldNDofs"] == [1, 1]
    assert metadata["xmesh_n_dofs"] == len(metadata["geomNums"]) == 2
    assert metadata["element_local_map_group_count"] == 1
    assert metadata["element_local_map_entries"] == 2
    assert metadata["complete_xmesh_internal_dof_capture"] is True
    assert metadata["solVectorInds"] == [1, 0]
    assert metadata["layout_sha256"] and len(metadata["layout_sha256"]) == 64
    assert frames[0]["u_real"] == [1.0, 2.0]
    assert frames[1]["u_real"] == [2.0, 3.0]

    # Duplicate Xmesh rows are recorded explicitly. An aliased layout may be
    # complete when every real solution-vector index remains represented.
    alias = tmp_path / "fields-v3-alias.bin.gz"
    _write_solution_snapshot_v3(alias, [0.0], vector_indices=(0, 0), vector_length=1)
    alias_frame = next(runner.iter_solution_snapshot(alias))
    summary = alias_frame["dofs"]["mapping_summary"]
    assert summary["duplicate_solution_vector_index_rows"] == 1
    assert summary["unrepresented_solution_vector_indices"] == 0
    assert alias_frame["dofs"]["complete_xmesh_internal_dof_capture"] is True

    missing = tmp_path / "fields-v3-missing-index.bin.gz"
    _write_solution_snapshot_v3(missing, [0.0], vector_indices=(0, 0), vector_length=2)
    missing_frame = next(runner.iter_solution_snapshot(missing))
    assert missing_frame["dofs"]["mapping_summary"]["unrepresented_solution_vector_indices"] == 1
    assert missing_frame["dofs"]["complete_xmesh_internal_dof_capture"] is False

    out_of_range = tmp_path / "fields-v3-out-of-range.bin.gz"
    _write_solution_snapshot_v3(out_of_range, [0.0], vector_indices=(0, 2), vector_length=2)
    out_of_range_frame = next(runner.iter_solution_snapshot(out_of_range))
    assert out_of_range_frame["dofs"]["mapping_summary"]["out_of_range_solution_indices"] == 1
    assert out_of_range_frame["dofs"]["complete_xmesh_internal_dof_capture"] is False


def test_native_metric_receipt_requires_full_time_and_material_readbacks(tmp_path):
    evidence, stresses, capture = _native_metric_fixture(tmp_path)
    verified = runner.verify_capture_receipt(
        capture, evidence, requires_energy=True, stress_components=stresses)
    assert verified["stored_times_s"] == [0.0, 1500.0]
    assert verified["native_metrics"]["temporary_dataset_removed"] is True
    artifact_path = Path(capture["native_metrics"]["path"])
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))

    # Two endpoint energy samples cannot stand in for the full accepted-time
    # vector, even when the field snapshot has the same two endpoint frames.
    artifact["energy_samples"].pop()
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    capture["native_metrics"].update({
        "size_bytes": artifact_path.stat().st_size,
        "sha256": runner.sha256(artifact_path),
    })
    with pytest.raises(runner.CampaignError, match="cover each accepted solution time"):
        runner.verify_capture_receipt(
            capture, evidence, requires_energy=True, stress_components=stresses)

    # Restore the complete row count, then confirm native material readbacks
    # are checked instead of silently accepting stale density/Cp literals.
    artifact["energy_samples"].append({
        "time_s": 1500.0, "stored_energy_j": 0.0,
        "stored_energy_by_domain_j": {name: 0.0 for name in
                                       ("alumina", "gold", "adhesive", "fiber")},
        "reaction_power_w": 0.0, "outward_power_w": 0.0,
        "outward_sign": "positive_outward",
    })
    artifact["material_heat_capacity_readback"][0][1] = "3800[kg/m^3]"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    capture["native_metrics"].update({
        "size_bytes": artifact_path.stat().st_size,
        "sha256": runner.sha256(artifact_path),
    })
    with pytest.raises(runner.CampaignError, match="density/Cp readback differs"):
        runner.verify_capture_receipt(
            capture, evidence, requires_energy=True, stress_components=stresses)


def test_birth_budget_keeps_the_full_cleanup_reserve():
    budget = runner.BirthBudget(100.0, budget_s=1000.0, cleanup_reserve_s=90.0)
    assert budget.rpc_timeout_s(500.0, now_epoch_s=200.0) == 500.0
    assert budget.rpc_timeout_s(500.0, now_epoch_s=700.0) == 310.0
    with pytest.raises(runner.CampaignError, match="cleanup window"):
        budget.rpc_timeout_s(10.0, now_epoch_s=1010.0)


def test_ten_solve_controller_uses_durable_order_immediate_save_and_sequential_reopen(tmp_path):
    private = Path("/private/tmp") / f"comsol-mcp-w24-campaign-{uuid.uuid4().hex}"
    private.mkdir()
    evidence = private / "evidence"
    evidence.mkdir()
    ledger = private / "study_run_events.jsonl"
    staged_save = private / "staged_baseline.mph"
    adapter = _FakeScienceAdapter(evidence)
    stress_components = {role: {"expression": f"solid.{role}", "unit": "Pa",
                                "description": f"native {role} stress"}
                         for role in runner._STRESS_ROLES}
    try:
        result = runner.execute_solve_plan(
            adapter, ledger_path=ledger, evidence=evidence,
            birth_budget=runner.BirthBudget(100.0),
            stress_components=stress_components, staged_baseline_path=staged_save,
            clock=lambda: 200.0,
        )
        assert result["status"] == "NATIVE_SCIENCE_EXECUTION_COMPLETE_ACCEPTANCE_RECEIPTS_RECORDED"
        assert result["actual_study_run_submissions"] == 10
        assert len(adapter.run_calls) == 10
        assert len(runner.read_solve_ledger(ledger)) == 10
        assert adapter.events.index("immediate-save") < adapter.events.index("capture:stdCool")
        assert adapter.events.index("reopen-worker-2") < adapter.events.index("run:stdCont")
        assert staged_save.is_file()
        assert result["slots"][2]["capture"]["energy_samples"] is not None
    finally:
        shutil.rmtree(private, ignore_errors=True)


def test_two_mechanics_slots_require_distinct_project_adopted_refs_before_any_solve(tmp_path):
    private = Path("/private/tmp") / f"comsol-mcp-w24-mechanics-bindings-{uuid.uuid4().hex}"
    private.mkdir()
    evidence = private / "evidence"
    evidence.mkdir()
    stress_components = {role: {"expression": f"solid.{role}", "unit": "Pa",
                                "description": f"native {role} stress"}
                         for role in runner._STRESS_ROLES}
    adapter = _FakeScienceAdapter(evidence, wrong_mechanics_model=True)
    ledger = private / "study_run_events.jsonl"
    try:
        result = runner.execute_solve_plan(
            adapter, ledger_path=ledger, evidence=evidence,
            birth_budget=runner.BirthBudget(100.0),
            stress_components=stress_components,
            staged_baseline_path=private / "staged_baseline.mph",
            clock=lambda: 200.0,
        )
        assert result["status"] == "FAIL_OR_INCOMPLETE_NO_RETRY"
        assert "model identity differs" in result["error"]
        assert adapter.run_calls == []
        assert runner.read_solve_ledger(ledger) == []
        assert len(result["mechanics_model_bindings"]) == 2
        refs = [record["model_ref"] for record in result["mechanics_model_bindings"].values()]
        assert refs[0]["model_tag"] != refs[1]["model_tag"]
        assert refs[0] != refs[1]
    finally:
        shutil.rmtree(private, ignore_errors=True)


def test_mechanics_model_bindings_reject_parent_tag_project_mismatch_and_reuse():
    bindings = runner.MechanicsModelBindings("project-native")

    def response(tag, project_id="project-native"):
        return {"success": True, "execution": {
            "project_id": project_id, "session_id": "session-native", "revision": 0,
            "model_ref": {"schema_version": 1, "session_id": "session-native",
                          "server_instance_id": "server-native", "model_tag": tag,
                          "generation": 1}}}

    free_tag = "w24mech_mechanics_free_expansion_native"
    fixed_tag = "w24mech_mechanics_fully_fixed_native"
    free = bindings.register("mechanics_free_expansion", response(free_tag),
                             expected_model_tag=free_tag)
    fixed = bindings.register("mechanics_fully_fixed", response(fixed_tag),
                              expected_model_tag=fixed_tag)
    assert free.model_ref != fixed.model_ref
    assert bindings.for_case("mechanics_free_expansion", expected_model_tag=free_tag) == free
    assert bindings.for_case("mechanics_fully_fixed", expected_model_tag=fixed_tag) == fixed
    with pytest.raises(runner.CampaignError, match="does not identify the requested native tag"):
        runner.ManagedModelBinding.from_adopt_response(
            response("w24-cure-coupon-parent"), project_id="project-native",
            expected_model_tag=free_tag)
    with pytest.raises(runner.CampaignError, match="registered project"):
        runner.ManagedModelBinding.from_adopt_response(
            response(free_tag, project_id="different-project"), project_id="project-native",
            expected_model_tag=free_tag)
    duplicate_bindings = runner.MechanicsModelBindings("project-native")
    duplicate_bindings.register("mechanics_free_expansion", response(free_tag),
                                expected_model_tag=free_tag)
    with pytest.raises(runner.CampaignError, match="distinct managed ModelRefs"):
        duplicate_bindings.register("mechanics_fully_fixed", response(free_tag),
                                    expected_model_tag=free_tag)


def test_failed_or_unobserved_solve_keeps_ledger_count_and_never_retries(tmp_path):
    private = Path("/private/tmp") / f"comsol-mcp-w24-no-retry-{uuid.uuid4().hex}"
    private.mkdir()
    evidence = private / "evidence"
    evidence.mkdir()
    ledger = private / "study_run_events.jsonl"
    adapter = _FakeScienceAdapter(evidence, fail_slot=2, fail_after_ledger=True)
    stress_components = {role: {"expression": f"solid.{role}", "unit": "Pa",
                                "description": f"native {role} stress"}
                         for role in runner._STRESS_ROLES}
    try:
        result = runner.execute_solve_plan(
            adapter, ledger_path=ledger, evidence=evidence,
            birth_budget=runner.BirthBudget(100.0),
            stress_components=stress_components,
            staged_baseline_path=private / "staged_baseline.mph",
            clock=lambda: 200.0,
        )
        assert result["status"] == "FAIL_OR_INCOMPLETE_NO_RETRY"
        assert result["actual_study_run_submissions"] == 2
        assert len(adapter.run_calls) == 2
        assert len(runner.read_solve_ledger(ledger)) == 2
        assert not any(event.startswith("run:") and event != "run:stdMech"
                       for event in adapter.events[2:])
    finally:
        shutil.rmtree(private, ignore_errors=True)


def test_solve_plan_has_ten_slots_but_is_not_itself_an_executed_count():
    assert len(runner.SOLVE_PLAN) == 10
    assert runner.next_solve_slot([]) == runner.SOLVE_PLAN[0]
    assert runner.next_solve_slot([{} for _ in runner.SOLVE_PLAN]) is None
    assert runner.MAX_STUDY_RUN_SUBMISSIONS == 10
    assert runner.MAX_SEQUENTIAL_WORKERS == 2


def test_study_run_ledger_counts_only_durable_invocation_events_and_checks_order(tmp_path):
    path = tmp_path / "study_run_events.jsonl"
    assert runner.read_solve_ledger(path) == []
    rows = []
    for index, slot in enumerate(runner.SOLVE_PLAN[:2], 1):
        rows.append({"event": "study_run_submitted", "submission_index": index,
                     "case_id": slot.case_id, "study_tag": slot.study_tag,
                     "at_utc": f"2026-09-27T00:00:0{index}Z"})
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    assert runner.read_solve_ledger(path) == rows
    assert runner.next_solve_slot(rows) == runner.SOLVE_PLAN[2]

    reordered = dict(rows[0], case_id="continuous_comparator", study_tag="stdCont")
    path.write_text(json.dumps(reordered) + "\n", encoding="utf-8")
    with pytest.raises(runner.CampaignError, match="does not match the frozen order"):
        runner.read_solve_ledger(path)


def test_solve_ledger_fails_closed_on_corruption_or_overrun(tmp_path):
    path = tmp_path / "study_run_events.jsonl"
    path.write_text('{"event":"planned"}\n', encoding="utf-8")
    with pytest.raises(runner.CampaignError, match="unexpected native solve ledger event"):
        runner.read_solve_ledger(path)
    path.write_text('{broken\n', encoding="utf-8")
    with pytest.raises(runner.CampaignError, match="malformed native solve ledger"):
        runner.read_solve_ledger(path)


def test_native_campaign_refuses_missing_setup_receipt_before_creating_paths(tmp_path):
    work = tmp_path / "not-created-work"
    evidence = tmp_path / "not-created-evidence"
    args = type("Args", (), {
        "template_receipt": str(tmp_path / "missing-template-receipt.json"),
        "candidate_freeze": str(tmp_path / "missing-freeze.json"),
        "expected_candidate_sha256": "a" * 64,
        "approval": str(tmp_path / "missing-approval.json"),
        "work": str(work), "evidence": str(evidence),
    })()
    with pytest.raises(runner.PrebirthRefusal, match="prebirth validation refused"):
        runner.run(args)
    assert not work.exists()
    assert not evidence.exists()


def test_lifecycle_summary_write_failure_preserves_known_postbirth_outcome_as_unknown(tmp_path, monkeypatch):
    work_root, receipt, candidate_path, candidate, approval = _synthetic_template_candidate(tmp_path)
    work = work_root / "campaign-work"
    evidence = work_root / "campaign-evidence"
    runtime = _FakeCampaignLifecycle()
    original_write = runner._write_json_fsynced

    def fail_only_final_summary(path, value):
        if Path(path).name == "summary.json":
            raise OSError("synthetic final summary fsync failure")
        return original_write(path, value)

    monkeypatch.setattr(runner, "_write_json_fsynced", fail_only_final_summary)
    try:
        result = runner.execute_campaign_lifecycle(
            template_receipt=receipt, candidate_path=candidate_path,
            expected_candidate_sha256=candidate["freeze_sha256"],
            approval_path=approval, work=work, evidence=evidence,
            runtime_factory=lambda **kwargs: runtime,
            platform_name="test", clock=lambda: 1010.0)
        assert result["status"] == "OUTCOME_UNKNOWN"
        assert result["engine_births"] == 1
        assert result["study_run_submissions"] == 0
        assert result["native_acceptance"] == "NOT_ACCEPTED"
        assert result["summary_write_error"] == "OSError: synthetic final summary fsync failure"
        assert json.loads((evidence / "summary_write_failure.json").read_text())["engine_births"] == 1
        assert runtime.events[-2:] == ["cleanup.reconcile", "cleanup.exact"]
    finally:
        shutil.rmtree(work_root, ignore_errors=True)


def test_synthetic_campaign_driver_wires_full_lifecycle_without_native_acceptance(tmp_path):
    work_root, receipt, candidate_path, candidate, approval = _synthetic_template_candidate(tmp_path)
    work = work_root / "campaign-work"
    evidence = work_root / "campaign-evidence"
    runtime = _FakeCampaignLifecycle()
    try:
        result = runner.execute_campaign_lifecycle(
            template_receipt=receipt, candidate_path=candidate_path,
            expected_candidate_sha256=candidate["freeze_sha256"],
            approval_path=approval, work=work, evidence=evidence,
            runtime_factory=lambda **kwargs: runtime,
            platform_name="test", clock=lambda: 1010.0)
        assert runtime.events == [
            "prebirth", "project.create", "stage.inputs", "compile.fixture",
            "server.birth", "worker.connect", "adapter.create", "controller.run",
            "cleanup.reconcile", "cleanup.exact",
        ]
        assert result["status"] == "SYNTHETIC_TEST_ONLY_CLEANUP_VERIFIED"
        assert result["engine_births"] == 1
        assert result["study_run_submissions"] == 0
        assert result["science_controller"]["synthetic_slot_count"] == 10
        assert result["native_acceptance"] == "NOT_NATIVE_SYNTHETIC_TEST_ONLY"
        assert runtime.budget.birth_epoch_s == 1000.0
        assert runtime.budget.deadline_epoch_s == 4600.0
        assert json.loads((evidence / "summary.json").read_text())["native_acceptance"] == "NOT_NATIVE_SYNTHETIC_TEST_ONLY"
    finally:
        shutil.rmtree(work_root, ignore_errors=True)


def test_server_launch_readback_exception_preserves_observed_birth_and_cleanup(tmp_path):
    work_root, receipt, candidate_path, candidate, approval = _synthetic_template_candidate(tmp_path)
    work = work_root / "campaign-work"
    evidence = work_root / "campaign-evidence"
    runtime = _FakeCampaignLifecycle(fail_at="server")
    try:
        result = runner.execute_campaign_lifecycle(
            template_receipt=receipt, candidate_path=candidate_path,
            expected_candidate_sha256=candidate["freeze_sha256"],
            approval_path=approval, work=work, evidence=evidence,
            runtime_factory=lambda **kwargs: runtime,
            platform_name="test", clock=lambda: 1010.0)
        assert result["status"] == "FAIL_OR_INCOMPLETE_CLEANUP_VERIFIED"
        assert result["engine_births"] == 1
        assert result["error"] == "TimeoutError: synthetic listener readback failure after birth"
        assert runtime.events[-2:] == ["cleanup.reconcile", "cleanup.exact"]
    finally:
        shutil.rmtree(work_root, ignore_errors=True)


def test_cli_unclassified_exception_does_not_claim_prebirth_or_zero_birth(monkeypatch, capsys):
    def unclassified(_args):
        raise OSError("synthetic failure outside lifecycle classification")

    monkeypatch.setattr(runner, "run", unclassified)
    monkeypatch.setattr(sys, "argv", [
        "run_native_w24_cure_science.py", "execute",
        "--template-receipt", "/missing/template.json",
        "--candidate-freeze", "/missing/freeze.json",
        "--expected-candidate-sha256", "a" * 64,
        "--approval", "/missing/approval.json",
        "--work", "/private/tmp/comsol-mcp-w24-cure-science-test",
        "--evidence", "/private/tmp/comsol-mcp-w24-cure-science-evidence-test",
    ])
    assert runner.main() == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "OUTCOME_UNKNOWN"
    assert result["engine_births"] is None
    assert result["study_run_submissions"] is None
    assert "no zero-birth or zero-solve claim" in result["outcome_note"]


def test_cli_explicit_prebirth_validation_failure_reports_known_zero_birth(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(sys, "argv", [
        "run_native_w24_cure_science.py", "execute",
        "--template-receipt", str(tmp_path / "missing-template.json"),
        "--candidate-freeze", str(tmp_path / "missing-freeze.json"),
        "--expected-candidate-sha256", "b" * 64,
        "--approval", str(tmp_path / "missing-approval.json"),
        "--work", "/private/tmp/comsol-mcp-w24-cure-science-prebirth-test",
        "--evidence", "/private/tmp/comsol-mcp-w24-cure-science-prebirth-evidence",
    ])
    assert runner.main() == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "PREBIRTH_REFUSED_NO_ENGINE"
    assert result["engine_births"] == 0
    assert result["study_run_submissions"] == 0


def test_template_gate_requires_native_saved_unsolved_file_hash_and_full_equation_table(tmp_path, monkeypatch):
    work_root = Path("/private/tmp") / f"comsol-mcp-w24-cure-science-test-{uuid.uuid4().hex}"
    work_root.mkdir(parents=True)
    try:
        project_id = "project-w24-test"
        workspace = work_root / "project" / "science"
        workspace.mkdir(parents=True)
        template = workspace / "cure_template.mph"
        template.write_bytes(b"native-template-bytes")
        inventory = _equation_view_inventory(workspace)
        inventory["registered_project_path"] = inventory["path"]
        fixture_source = workspace / "W24CureCouponFixture.java"
        fixture_source.write_text("// synthetic test fixture", encoding="utf-8")
        classes_dir = workspace / "offline-classes"
        classes_dir.mkdir()
        (classes_dir / "W24CureCouponFixture.class").write_bytes(b"synthetic-class")
        evidence_dir = work_root / "evidence"
        evidence_dir.mkdir()
        creation_path = evidence_dir / "project_workspace_created_before_birth.json"
        project_record = {
            "project_id": project_id, "workspace": str(workspace),
            "policy": {"permissions": ["inspect", "project_write", "compute", "trusted_code"]},
        }
        creation = {
            "status": "PROJECT_CREATED_BEFORE_ENGINE_BIRTH", "project_id": project_id,
            "workspace": str(workspace),
            "host_grants": {"startup_ceiling": ["trusted_code"],
                            "effective_host_permissions": ["trusted_code"]},
            "response": {"success": True, "data": {"project": project_record}},
            "inspection_response": {"success": True, "data": {
                "project": project_record,
                "effective_permissions": ["inspect", "project_write", "compute", "trusted_code"],
            }},
        }
        creation_path.write_text(json.dumps(creation), encoding="utf-8")
        receipt = {
            "status": "NATIVE_CONFIGURED_TEMPLATE_SAVED_NOT_SOLVED",
            "reopen_status": "NATIVE_TEMPLATE_REOPEN_READBACK_PASS_NOT_SOLVED",
            "native_solver_submissions": [],
            "engine_identity": {"matches_frozen_target": True},
            "freeze_sha256": "a" * 64,
            "path": str(template),
            "size_bytes": template.stat().st_size,
            "sha256": runner.sha256(template),
            "project_id": project_id,
            "project_workspace": str(workspace),
            "project_creation_receipt": str(creation_path),
            "project_creation_receipt_sha256": runner.sha256(creation_path),
            "fixture_source_path": str(fixture_source),
            "fixture_source_sha256": runner.sha256(fixture_source),
            "fixture_compile_output_dir": str(classes_dir),
            "fixture_compile_classes": ["W24CureCouponFixture.class"],
            "fixture_readback": {
                "status": "BUILT_NOT_SOLVED", "geometry_dimension": 2,
                "geometry_axisymmetric": True, "geometry_domain_count": 4,
                "domain_readbacks": {
                    material: {"relative_volume_error": 0.0}
                    for material in ("alumina", "gold", "adhesive", "fiber")
                },
                "solver_readbacks": {"stdUV": {}, "stdBake": {}, "stdCool": {}},
                "solid_quasistatic_readback": "Quasistatic",
            },
            "equation_view_inventory": inventory,
            "runtime_environment": _test_runtime_environment(),
        }
        receipt_path = work_root / "configured_template_receipt.json"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        verified = runner.validate_template_receipt(receipt_path)
        assert verified["template_sha256"] == runner.sha256(template)
        assert verified["equation_view_inventory"]["status"] == "COMPLETE_NOT_EVALUATED"
        assert verified["project_id"] == project_id
        assert verified["registered_project"]["workspace"] == str(workspace)

        template.write_bytes(b"tampered")
        with pytest.raises(runner.CampaignError, match="bytes no longer match"):
            runner.validate_template_receipt(receipt_path)
    finally:
        shutil.rmtree(work_root, ignore_errors=True)


def test_template_receipt_rejects_a_solved_or_non_native_candidate(tmp_path):
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps({"status": "BUILT_NOT_SOLVED"}), encoding="utf-8")
    with pytest.raises(runner.CampaignError, match="successful unsolved native template"):
        runner.validate_template_receipt(path)
