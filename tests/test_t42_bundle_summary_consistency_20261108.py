"""T42 R1: bundle summary hashes must describe audited archived lock bytes.

The hook models a concurrent writer swapping a lock just after its records hash
and restoring it before ZIP member readback. No native or build provenance claim.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import pathlib
import zipfile

import pytest

from tests.test_full_project_release import make_derived_crypto_inputs, release
from tests.test_t42_derived_build_evidence_20261108 import (
    call_package, make_declared_build_evidence, prepare_packaging,
)


@pytest.mark.parametrize("target", ["source_lock", "requirements"])
def test_post_record_lock_swap_cannot_contradict_bundle_summary(tmp_path, monkeypatch, target):
    house, wheel, requirements, source_lock, provenance, _ = make_derived_crypto_inputs(tmp_path)
    license_path = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    evidence = make_declared_build_evidence(tmp_path, provenance)
    flow = prepare_packaging(tmp_path, house, wheel, requirements, source_lock,
                             provenance, license_path, evidence=evidence)
    selected = source_lock if target == "source_lock" else flow["final_lock"]
    arcname = "locks/uv.lock" if target == "source_lock" else "locks/requirements.lock"
    original = selected.read_bytes()
    original_sha = hashlib.sha256(original).hexdigest()
    swapped = original + b"\n# synthetic concurrent replacement\n"
    marker = {"injected": False, "restored_before_member": False}
    real_sha256 = release.sha256_file
    real_write = zipfile.ZipFile.write

    def swap_after_audited_record_read(path):
        digest = real_sha256(path)
        frame = inspect.currentframe()
        caller = frame.f_back if frame is not None else None
        if (pathlib.Path(path).resolve() == selected.resolve()
                and caller is not None
                and caller.f_code.co_name == "package_wheelhouse_bundle"
                and caller.f_locals.get("arcname") == arcname
                and not marker["injected"]):
            selected.write_bytes(swapped)
            marker["injected"] = True
        return digest

    def restore_before_zip_member(archive, path, arcname=None, **kwargs):
        if arcname == ("locks/uv.lock" if target == "source_lock" else "locks/requirements.lock"):
            assert selected.read_bytes() == swapped, "the fault must still be present before the member write"
            selected.write_bytes(original)
            marker["restored_before_member"] = True
        return real_write(archive, path, arcname=arcname, **kwargs)

    monkeypatch.setattr(release, "sha256_file", swap_after_audited_record_read)
    monkeypatch.setattr(zipfile.ZipFile, "write", restore_before_zip_member)
    archive = tmp_path / f"swap-{target}.zip"
    receipt = tmp_path / f"swap-{target}-evidence" / "offline-bundle-macos_x86_64.json"
    try:
        try:
            result, _, _ = call_package(tmp_path, flow, house, provenance, license_path,
                                        evidence=evidence, name=f"swap-{target}")
        except release.ReleaseError:
            assert marker["injected"], "counterexample must reach the post-record window"
            assert not receipt.exists(), "a detected race cannot publish a success receipt"
            return
        assert marker["injected"] and marker["restored_before_member"], marker
        assert result["status"] == "OFFLINE_BUNDLE_PACKAGED_NATIVE_UNVERIFIED"
        assert receipt.is_file()
        with zipfile.ZipFile(archive) as bundle:
            summary = json.loads(bundle.read("OFFLINE_BUNDLE_MANIFEST.json"))
            audited = json.loads(bundle.read("PACKAGE_MANIFEST.json"))
            member_sha = hashlib.sha256(bundle.read(arcname)).hexdigest()
        record = next(row for row in summary["files_excluding_this_manifest"] if row["path"] == arcname)
        assert member_sha == original_sha == audited[target]["sha256"] == record["sha256"]
        assert summary[target]["sha256"] == summary[f"{target}_sha256"] == member_sha, {
            "target": target, "nested_summary": summary[target]["sha256"],
            "flat_summary": summary[f"{target}_sha256"], "member": member_sha,
        }
    finally:
        selected.write_bytes(original)
