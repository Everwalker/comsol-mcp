"""T42: a packaging decision must bind the actual tool bytes placed in the ZIP.

All files here are synthetic, with no COMSOL, network, or native execution.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import zipfile

from tests.test_full_project_release import make_derived_crypto_inputs, release
from tests.test_t42_derived_build_evidence_20261108 import (
    make_declared_build_evidence,
    prepare_packaging,
)


def test_tool_replaced_after_lifecycle_decision_cannot_publish_incomplete_bundle(tmp_path, monkeypatch):
    house, wheel, requirements, source_lock, provenance, _ = make_derived_crypto_inputs(tmp_path)
    license_path = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    evidence = make_declared_build_evidence(tmp_path, provenance)
    flow = prepare_packaging(tmp_path, house, wheel, requirements, source_lock, provenance,
                             license_path, evidence=evidence)
    tool = tmp_path / "packaged-tool.py"
    tool.write_text("# synthetic tool without lifecycle support\n")
    replacement = (
        "import argparse\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('--with-data-lifecycle', action='store_true')\n"
        "if p.parse_args().with_data_lifecycle:\n"
        "    import release_transition_lifecycle\n"
    )
    original_read_text = pathlib.Path.read_text
    original_read_bytes = pathlib.Path.read_bytes
    injections = []

    def inject_after_read(path, result):
        if path == tool and not injections:
            tool.write_text(replacement)
            injections.append(True)
        return result

    def text_then_swap(path, *args, **kwargs):
        return inject_after_read(path, original_read_text(path, *args, **kwargs))

    def bytes_then_swap(path, *args, **kwargs):
        return inject_after_read(path, original_read_bytes(path, *args, **kwargs))

    monkeypatch.setattr(pathlib.Path, "read_text", text_then_swap)
    monkeypatch.setattr(pathlib.Path, "read_bytes", bytes_then_swap)
    archive = tmp_path / "tool-swap.zip"
    evidence_dir = tmp_path / "tool-swap-evidence"
    try:
        result = release.package_wheelhouse_bundle(
            house, flow["final_lock"], source_lock, flow["metadata"], flow["operations"],
            "macos_x86_64", archive, evidence_dir, flow["build"], tool, provenance,
            license_path, derived_build_evidence=evidence,
        )
    except release.ReleaseError as exc:
        assert injections == [True]
        assert "release tool changed after the pre-packaging re-audit" in str(exc)
        assert not archive.exists()
        assert not (evidence_dir / "offline-bundle-macos_x86_64.json").exists()
        return
    assert injections == [True], "the swap must follow the actual tool inspection"
    with zipfile.ZipFile(archive) as zf:
        packaged_tool = zf.read("tools/full_project_release.py").decode()
        helper_present = "tools/release_transition_lifecycle.py" in zf.namelist()
    extracted = tmp_path / "extracted" / "full_project_release.py"
    extracted.parent.mkdir()
    extracted.write_text(packaged_tool)
    probe = subprocess.run([sys.executable, "-I", str(extracted), "--with-data-lifecycle"],
                           cwd=extracted.parent, capture_output=True, text=True, timeout=10)
    assert probe.returncode != 0 and "No module named 'release_transition_lifecycle'" in probe.stderr
    assert False, {
        "success_status": result.get("status"),
        "published_tool_needs_lifecycle": "--with-data-lifecycle" in packaged_tool,
        "published_helper_present": helper_present,
        "synthetic_extracted_tool_exit": probe.returncode,
        "success_receipt_written": (evidence_dir / "offline-bundle-macos_x86_64.json").exists(),
    }
