"""T42 (2026-11-08): the release CLI must exit 0 for a successful compose-derived-lock.

Evidence `_state/t42-intel-continuation-20261108/logs/compose-derived-lock.json`
records status `DERIVED_TARGET_LOCK_HASHED` with an empty stderr, yet the CLI
returned exit 1 because `main()`'s successful-artifact status allowlist omitted
that exact existing status. These subprocess-level tests pin the correct
behaviour: success JSON receipt exits 0 with a self-consistent sha256, invalid
inputs still exit non-zero and must never yield a legitimate success artifact,
and unknown statuses are never waved through by the allowlist fix.

All inputs are synthetic fixtures under pytest-owned tmp dirs; the CLI runs in
a fresh isolated directory, offline, with no COMSOL and no network.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import subprocess
import sys
import zipfile

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "full_project_release.py"

_spec = importlib.util.spec_from_file_location("t42_release_under_test", TOOL)
release = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
sys.modules[_spec.name] = release
_spec.loader.exec_module(release)


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_wheel(path: pathlib.Path, *, name: str = "demo-pkg", version: str = "1.0",
               tag: str = "py3-none-any") -> str:
    dist = name.replace("-", "_")
    info = f"{dist}-{version}.dist-info"
    members = {
        f"{info}/METADATA": (
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
            "License: MIT\nClassifier: License :: OSI Approved :: MIT License\n\n"
        ).encode(),
        f"{info}/WHEEL": f"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: {tag}\n".encode(),
        f"{info}/licenses/LICENSE.txt": b"MIT license text\n",
        f"{dist}/__init__.py": b"VALUE = 1\n",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member, content in members.items():
            archive.writestr(member, content)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_derived_crypto_inputs(tmp_path: pathlib.Path):
    """Same synthetic shape as tests/test_full_project_release.py's fixture."""
    wheelhouse = tmp_path / "wheelhouse"
    wheel = wheelhouse / "cryptography-50.0.1-cp312-abi3-macosx_12_0_x86_64.whl"
    wheel_sha = make_wheel(wheel, name="cryptography", version="50.0.1",
                           tag="cp312-abi3-macosx_12_0_x86_64")
    source_sha = "5dd9bda1c12b4162f6ff568eeb5e0ff956c28d14406e875cfe8a63a2d414ff20"
    source_url = ("https://files.pythonhosted.org/packages/bb/ad/"
                  "5d6702db60b1e40b41ef513b6967ff5848f307d50f8449baf1634f5908f1/"
                  "cryptography-50.0.1.tar.gz")
    source_lock = tmp_path / "uv.lock"
    source_lock.write_text(
        "version = 1\n\n[[package]]\nname = \"cryptography\"\nversion = \"50.0.1\"\n"
        f"sdist = {{ url = \"{source_url}\", hash = \"sha256:{source_sha}\" }}\n",
        encoding="utf-8",
    )
    license_path = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    license_path.write_text("Apache License\nVersion 2.0\n", encoding="utf-8")
    provenance_path = tmp_path / "derived-wheel-provenance.json"
    provenance = {
        "schema": "comsol-mcp-derived-wheel/1", "status": "BUILD_CANDIDATE", "target": "macos_x86_64",
        "package": {"name": "cryptography", "version": "50.0.1"},
        "wheel": {"filename": wheel.name, "bytes": wheel.stat().st_size, "sha256": wheel_sha,
                  "tag": "cp312-abi3-macosx_12_0_x86_64"},
        "source": {"url": source_url, "sha256": source_sha},
        "source_lock": {"filename": source_lock.name, "sha256": _sha256(source_lock),
                        "sdist_sha256": source_sha},
        "native_component": {
            "name": "OpenSSL", "version": "4.0.2", "source_url": release.OPENSSL_402_SOURCE_URL,
            "source_sha256": release.OPENSSL_402_SOURCE_SHA256, "linkage": "static", "license": "Apache-2.0",
            "license_file": {"filename": license_path.name, "bytes": license_path.stat().st_size,
                             "sha256": _sha256(license_path)},
        },
        "native_audit": {"architecture": "x86_64", "minimum_os": "12.0",
                         "dynamic_libraries": ["/usr/lib/libiconv.2.dylib", "/usr/lib/libSystem.B.dylib"],
                         "openssl_dynamic_dependency": False, "temporary_dylib_dependencies": []},
        "uv_lock_modified": False, "upstream_source_patched": False,
    }
    provenance_path.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    requirements = tmp_path / "requirements.download.lock"
    requirements.write_text(f"cryptography==50.0.1 --hash=sha256:{source_sha}\n", encoding="utf-8")
    return wheelhouse, wheel, requirements, source_lock, provenance_path, wheel_sha


def run_cli(*argv: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), *argv],
        cwd=cwd, capture_output=True, text=True, timeout=120,
        env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def derived_lock_argv(requirements: pathlib.Path, wheel: pathlib.Path, source_lock: pathlib.Path,
                      provenance: pathlib.Path, openssl_license: pathlib.Path, out: pathlib.Path) -> list[str]:
    return ["compose-derived-lock", "--requirements", str(requirements), "--wheel", str(wheel),
            "--source-lock", str(source_lock), "--derived-provenance", str(provenance),
            "--openssl-license", str(openssl_license), "--target", "macos_x86_64", "--out", str(out)]


def test_cli_compose_derived_lock_success_exits_zero(tmp_path: pathlib.Path) -> None:
    """RED (T42): success receipt `DERIVED_TARGET_LOCK_HASHED` must exit 0, not 1."""
    isolated = tmp_path / "isolated-run"
    isolated.mkdir()
    (_house, wheel, requirements, source_lock, provenance, wheel_sha) = make_derived_crypto_inputs(tmp_path)
    openssl_license = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    out = isolated / "requirements-derived.lock"
    input_hashes = {p: _sha256(p) for p in (wheel, requirements, source_lock, provenance, openssl_license)}

    proc = run_cli(*derived_lock_argv(requirements, wheel, source_lock, provenance, openssl_license, out),
                   cwd=isolated)
    assert proc.returncode == 0, f"exit={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}"
    receipt = json.loads(proc.stdout)
    assert receipt["status"] == "DERIVED_TARGET_LOCK_HASHED"
    assert receipt["filename"] == out.name
    assert receipt["target"] == "macos_x86_64"
    assert out.is_file()
    actual = _sha256(out)
    assert receipt["sha256"] == actual, "receipt sha256 must match the file actually written"
    assert receipt["cryptography_wheel_sha256"] == wheel_sha == _sha256(wheel)
    parsed = release.parse_hash_requirements(out, "macos_x86_64")
    assert wheel_sha in parsed["cryptography"]["hashes"]
    # read-only reuse: inputs byte-identical, no success output outside the isolated dir
    assert {p: _sha256(p) for p in input_hashes} == input_hashes


@pytest.mark.parametrize("tamper", ["lock-drift", "missing-crypto-pin"])
def test_cli_compose_derived_lock_invalid_input_still_exits_nonzero_without_success_artifact(
        tmp_path: pathlib.Path, tamper: str) -> None:
    """Fixing the success allowlist must not turn invalid inputs into a legal success."""
    isolated = tmp_path / "isolated-run"
    isolated.mkdir()
    (_house, wheel, requirements, source_lock, provenance, _wheel_sha) = make_derived_crypto_inputs(tmp_path)
    openssl_license = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    if tamper == "lock-drift":
        payload = json.loads(provenance.read_text(encoding="utf-8"))
        payload["source_lock"]["sha256"] = "0" * 64
        provenance.write_text(json.dumps(payload), encoding="utf-8")
    else:
        requirements.write_text("other-pkg==1.0 --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8")
    out = isolated / "requirements-derived.lock"

    proc = run_cli(*derived_lock_argv(requirements, wheel, source_lock, provenance, openssl_license, out),
                   cwd=isolated)
    assert proc.returncode != 0, f"invalid input must not exit 0: stdout={proc.stdout!r}"
    assert not out.exists(), "invalid input must not produce a derived lock artifact"
    assert "FAIL" in proc.stderr
    success_rows = [line for line in proc.stdout.splitlines() if "DERIVED_TARGET_LOCK_HASHED" in line]
    assert success_rows == [], "no success status may be emitted for invalid input"


def test_success_allowlist_fix_is_exact_not_a_blanket_pass(monkeypatch: pytest.MonkeyPatch,
                                                          capsys: pytest.CaptureFixture[str]) -> None:
    """The fix adds exactly the existing success status; unknown statuses still exit non-zero."""
    argv = ["compose-derived-lock", "--requirements", "r", "--wheel", "w", "--source-lock", "l",
            "--derived-provenance", "p", "--openssl-license", "c", "--target", "macos_x86_64", "--out", "o"]

    def fake(requirements, wheel, source_lock, provenance, openssl_license, target, output):
        return {"status": "SOMETHING_UNKNOWN"}

    monkeypatch.setattr(release, "compose_derived_wheel_lock", fake)
    assert release.main(argv) != 0, "unknown status must not be waved through"

    def fake_success(requirements, wheel, source_lock, provenance, openssl_license, target, output):
        return {"status": "DERIVED_TARGET_LOCK_HASHED"}

    monkeypatch.setattr(release, "compose_derived_wheel_lock", fake_success)
    assert release.main(argv) == 0, "the exact existing success status must exit 0"
