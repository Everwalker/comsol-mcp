"""T42 synthetic re-audit/record races for other bundled inputs.

Exercise the real package API and actual re-audit callback; the only injected
fault is an input byte change immediately after that re-audit completes.
"""
import json
from pathlib import Path

import pytest

from tests.test_full_project_release import make_derived_crypto_inputs, release
from tests.test_t42_derived_build_evidence_20261108 import (
    call_package, make_declared_build_evidence, prepare_packaging,
)


@pytest.mark.parametrize("changed_input", ["derived_provenance", "openssl_license", "derived_wheel", "app_wheel",
                                            "requirements", "source_lock", "package_manifest", "sbom",
                                            "licenses", "app_build_receipt", "app_source_manifest"])
def test_packaging_rejects_reaudited_input_changed_before_records(tmp_path: Path, monkeypatch, changed_input):
    house, wheel, requirements, source_lock, provenance, _ = make_derived_crypto_inputs(tmp_path)
    license_path = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    evidence = make_declared_build_evidence(tmp_path, provenance)
    flow = prepare_packaging(tmp_path, house, wheel, requirements, source_lock, provenance,
                             license_path, evidence=evidence)
    targets = {"derived_provenance": provenance, "openssl_license": license_path,
               "derived_wheel": wheel, "app_wheel": flow["app"],
               "requirements": flow["final_lock"], "source_lock": source_lock,
               "package_manifest": flow["metadata"] / "PACKAGE_MANIFEST.json",
               "sbom": flow["metadata"] / "SBOM.cdx.json", "licenses": flow["metadata"] / "LICENSES.json",
               "app_build_receipt": flow["build"] / "wheel-build-receipt.json",
               "app_source_manifest": flow["build"] / "source-snapshot-manifest.json"}
    target = targets[changed_input]
    before = target.read_bytes()
    original_reaudit = release.create_bundle_manifest
    hits = []

    def swap_after_reaudit(*args, **kwargs):
        result = original_reaudit(*args, **kwargs)
        if not hits:
            if changed_input in {"package_manifest", "sbom", "licenses", "app_build_receipt", "app_source_manifest"}:
                payload = json.loads(before)
                if changed_input == "package_manifest":
                    payload["target_execution_verified"] = True
                elif changed_input == "app_build_receipt":
                    payload["sha256"] = "0" * 64
                elif changed_input == "app_source_manifest":
                    payload["manifest_sha256"] = "0" * 64
                else:
                    payload["components"] = []
                target.write_text(json.dumps(payload), encoding="utf-8")
            else:
                # Preserve JSON/TOML validity and zip central-directory
                # readability while changing the exact audited bytes.
                target.write_bytes(before + b"\n")
            hits.append(True)
        return result

    monkeypatch.setattr(release, "create_bundle_manifest", swap_after_reaudit)
    try:
        with pytest.raises(release.ReleaseError):
            call_package(tmp_path, flow, house, provenance, license_path,
                         evidence=evidence, name="changed-" + changed_input)
    finally:
        assert hits == [True], "fault must occur after the real re-audit, not at an earlier precondition"
        assert target.read_bytes() != before
    assert not (tmp_path / ("changed-" + changed_input + "-evidence") /
                "offline-bundle-macos_x86_64.json").exists()
