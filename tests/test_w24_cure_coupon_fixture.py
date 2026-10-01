import os
from pathlib import Path
import subprocess

import pytest


REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tools/java/W24CureCouponFixture.java"
HARNESS = REPO / "tests/java/W24DistributedODEResolverHarness.java"
JAVA_HOME = Path("/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home")
COMSOL_API_JAR = Path("/Applications/COMSOL64/Multiphysics/plugins/com.comsol.api_1.0.0.jar")


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
    compile_result = subprocess.run(
        [str(javac), "-proc:none", "-classpath", str(COMSOL_API_JAR), "-d", str(classes),
         str(FIXTURE), str(HARNESS)],
        cwd=REPO, capture_output=True, text=True, timeout=120, check=False,
    )
    assert compile_result.returncode == 0, (
        "Corretto 11 failed to compile the production fixture and proxy harness:\n"
        + compile_result.stdout + compile_result.stderr
    )

    classpath = os.pathsep.join((str(classes), str(COMSOL_API_JAR)))
    run_result = subprocess.run(
        [str(java), "-Djava.awt.headless=true", "-classpath", classpath,
         "W24DistributedODEResolverHarness"],
        cwd=REPO, capture_output=True, text=True, timeout=30, check=False,
    )
    assert run_result.returncode == 0, (
        "offline DistributedODE proxy checks failed:\n" + run_result.stdout + run_result.stderr
    )
    assert run_result.stdout.strip() == "W24 DistributedODE resolver proxy checks: PASS (3 cases)"
