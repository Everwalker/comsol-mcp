import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest


REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tools/java/W24CureCouponFixture.java"
SCIENCE_FIXTURE = REPO / "tools/java/W24CureScienceFixture.java"
HARNESS = REPO / "tests/java/W24DistributedODEResolverHarness.java"
SOLVER_HARNESS = REPO / "tests/java/W24SolverSequenceDiagnosticsHarness.java"
JAVA_HOME = Path("/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home")
COMSOL_API_JAR = Path("/Applications/COMSOL64/Multiphysics/plugins/com.comsol.api_1.0.0.jar")


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _capture(command, *, cwd, timeout, output_prefix):
    try:
        result = subprocess.run(
            command, cwd=cwd, capture_output=True, text=False, timeout=timeout, check=False,
        )
        stdout = result.stdout
        stderr = result.stderr
        returncode = result.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or b""
        stderr = exc.stderr or b""
        returncode = None
        timed_out = True
    stdout_path = output_prefix.with_suffix(".stdout.bin")
    stderr_path = output_prefix.with_suffix(".stderr.bin")
    stdout_path.write_bytes(stdout)
    stderr_path.write_bytes(stderr)
    return {
        "command": list(command),
        "returncode": returncode,
        "timed_out": timed_out,
        "stdout_path": str(stdout_path),
        "stdout_sha256": _sha256(stdout_path),
        "stderr_path": str(stderr_path),
        "stderr_sha256": _sha256(stderr_path),
        "stdout_text": stdout.decode("utf-8", errors="replace"),
        "stderr_text": stderr.decode("utf-8", errors="replace"),
    }


def test_w24_fixture_is_build_only_and_uses_correct_cure_rates():
    source = FIXTURE.read_text(encoding="utf-8")

    assert 'set("kT", "A*exp(-Ea/(Rgas*T))")' in source
    assert 'set("kT0", "A*exp(-Ea/(Rgas*T0))")' in source
    assert 'set("rate", "(kUV*IUV+kT)*(1-alpha)")' in source
    assert '"(kUV*IUV+kT0)*(1-alpha_iso)"' in source
    assert '"if(alpha>=alpha_gel,rate/(1-alpha_gel),0[1/s])"' in source
    assert "study.run(" not in source
    assert ".compute(" not in source
    assert '"native_study_run_calls", 0' in source


def test_w24_fixture_gates_exact_axisymmetric_domain_and_external_edge_geometry():
    source = FIXTURE.read_text(encoding="utf-8")

    assert "geom.axisymmetric(true)" in source
    assert 'geom.feature().create("uni1", "Union")' in source
    assert "if (geom.getNDomains() != 4)" in source
    assert "relativeError > 1.0e-6" in source
    assert "selection must identify one edge" in source
    assert "assertBoundingBox(tag, boundingBox, EXTERNAL_EDGE_BOXES[i])" in source
    assert "axis-of-symmetry edge selected for convection" in source
    # External surfaces must be geometrically contained; intersects also pulls
    # in touching axis/interface entities at box endpoints.
    assert 'b[2] - TOL, b[3] + TOL,\n                      "inside"' in source


def test_w24_fixture_freezes_quasistatic_volumetric_strain_and_exact_stage_outputs():
    source = FIXTURE.read_text(encoding="utf-8")

    assert '"StructuralTransientBehavior", "Quasistatic"' in source
    assert 'feature.set("StrainInput", "VolumetricStrain")' in source
    assert 'feature.set("dV", expression)' in source
    assert '"stdUV", "range(0[s],1[s],120[s])"' in source
    assert '"stdBake", "range(120[s],10[s],960[s])"' in source
    assert '"stdCool", "range(960[s],10[s],1500[s])"' in source


def test_external_strain_is_a_verified_linear_elastic_material_child_in_both_fixtures():
    coupon = FIXTURE.read_text(encoding="utf-8")
    science = (REPO / "tools/java/W24CureScienceFixture.java").read_text(encoding="utf-8")

    for source, create_expression in (
        (coupon, 'parent.feature().create(tag, "ExternalStrain", 2)'),
        (science, 'linearElastic.feature().create("estrain", "ExternalStrain", 2)'),
    ):
        assert 'feature("lemm1")' in source
        assert 'getType();' in source
        assert '"LinearElasticModel".equals(parentType)' in source
        assert create_expression in source
        assert '"ExternalStrain".equals(eigenstrain.getType())' in source or \
               '"ExternalStrain".equals(feature.getType())' in source
        assert 'getString("StrainInput")' in source
        assert 'getString("dV")' in source

    assert 'model.physics("solid").create(tag, "ExternalStrain", 2)' not in coupon
    assert 'model.physics("solid").create("estrain", "ExternalStrain", 2)' not in science
    assert 'parent.feature(tag)' in coupon
    assert '"parent_feature_tag", "lemm1"' in coupon
    assert '"external_strain_parent_tag", "lemm1"' in science
    assert 'sameEntitySet(domainIds, selectedEigenstrainDomains)' in science


def test_w24_ode_equations_are_resolved_by_unique_feature_type_and_reported_tag(tmp_path):
    javac = JAVA_HOME / "bin/javac"
    java = JAVA_HOME / "bin/java"
    if not javac.is_file() or not java.is_file() or not COMSOL_API_JAR.is_file():
        pytest.skip("local resolver proxy requires COMSOL 6.4 public API and Amazon Corretto 11")

    classes = tmp_path / "classes"
    classes.mkdir()
    sources = (FIXTURE, SCIENCE_FIXTURE, HARNESS, SOLVER_HARNESS)
    compile_command = [
        str(javac), "-proc:none", "-classpath", str(COMSOL_API_JAR), "-d", str(classes),
        *(str(source) for source in sources),
    ]
    receipt_path = tmp_path / "w24-java-proxy-receipt.json"
    version_results = {
        "javac": _capture([str(javac), "-version"], cwd=REPO, timeout=15,
                           output_prefix=tmp_path / "javac-version"),
        "java": _capture([str(java), "-version"], cwd=REPO, timeout=15,
                          output_prefix=tmp_path / "java-version"),
    }
    compile_result = _capture(compile_command, cwd=REPO, timeout=120,
                              output_prefix=tmp_path / "javac-compile")
    classpath = os.pathsep.join((str(classes), str(COMSOL_API_JAR)))
    run_results = {}
    if compile_result["returncode"] == 0 and not compile_result["timed_out"]:
        for main_class, output_name in (
            ("W24DistributedODEResolverHarness", "ode-proxy"),
            ("W24SolverSequenceDiagnosticsHarness", "solver-diagnostics-proxy"),
        ):
            run_results[main_class] = _capture(
                [str(java), "-Djava.awt.headless=true", "-classpath", classpath, main_class],
                cwd=REPO, timeout=30, output_prefix=tmp_path / output_name,
            )

    class_files = sorted(classes.rglob("*.class"))
    class_artifacts = {}
    for class_file in class_files:
        bytecode = class_file.read_bytes()
        class_artifacts[class_file.relative_to(classes).as_posix()] = {
            "sha256": _sha256(class_file),
            "major_version": int.from_bytes(bytecode[6:8], "big") if len(bytecode) >= 8 else None,
        }
    receipt = {
        "schema": "W24_JAVA_PROXY_DIAGNOSTIC_RECEIPT_V1",
        "receipt_path": str(receipt_path),
        "jdk_home": str(JAVA_HOME),
        "jdk_artifacts": {
            "javac_path": str(javac), "javac_sha256": _sha256(javac),
            "java_path": str(java), "java_sha256": _sha256(java),
            "javac_version": version_results["javac"],
            "java_version": version_results["java"],
        },
        "comsol_api_jar": str(COMSOL_API_JAR),
        "comsol_api_jar_sha256": _sha256(COMSOL_API_JAR),
        "sources": {str(source.relative_to(REPO)): _sha256(source) for source in sources},
        "compile": compile_result,
        "runs": run_results,
        "classes": class_artifacts,
    }
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"W24_JAVA_PROXY_RECEIPT={receipt_path}")

    assert compile_result["returncode"] == 0 and not compile_result["timed_out"], (
        "Corretto 11 failed to compile the production fixture and proxy harnesses:\n"
        + compile_result["stdout_text"] + compile_result["stderr_text"]
    )
    for main_class, expected_stdout in (
        ("W24DistributedODEResolverHarness", "W24 DistributedODE resolver proxy checks: PASS (3 cases)"),
        ("W24SolverSequenceDiagnosticsHarness", "W24 solver-sequence diagnostics proxy checks: PASS (122 cases)"),
    ):
        result = run_results[main_class]
        assert result["returncode"] == 0 and not result["timed_out"], (
            f"offline {main_class} failed:\n" + result["stdout_text"] + result["stderr_text"]
        )
        assert result["stdout_text"].strip() == expected_stdout
    assert all(value["major_version"] == 55 for value in class_artifacts.values()), (
        "Corretto 11 output must retain Java class major version 55"
    )
