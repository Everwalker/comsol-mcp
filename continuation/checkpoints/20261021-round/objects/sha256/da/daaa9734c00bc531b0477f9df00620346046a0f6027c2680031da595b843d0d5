"""Actual archived-byte validation for the finite Intel target-input profile.

This prepares only input wheelhouses from already accepted ARM 43 inputs and
the preserved x86 development archive.  It imports or runs no wheel code and
does not claim Intel runtime or hardware acceptance.
"""
from __future__ import annotations

import hashlib
import base64
import csv
import io
import json
import shutil
import sys
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path

CANDIDATE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE / "source"))

from tools import w26_target_inputs as target_inputs  # noqa: E402
from tools import full_project_release as release  # noqa: E402
from tools.w26_prepare_current_inputs import _helpers  # noqa: E402


DEPENDENCIES = Path("/private/tmp/cvw26-dependencies-r17-ae038da79aa0")
PROJECT = CANDIDATE / "source" / "pyproject.toml"
ARCHIVE = CANDIDATE / "inputs" / "comsol-mcp-macos_x86_64-0.2.0.dev1.zip"


class HistoricalIntelTargetProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not DEPENDENCIES.is_dir() or not (DEPENDENCIES / "INPUTS_MANIFEST.json").is_file():
            raise RuntimeError("Main-accepted current43 dependency input root is unavailable")
        cls.helper = _helpers()
        cls.archive_record = cls.helper.raw_file(ARCHIVE)[1]
        cls.root = Path("/private/tmp") / ("cvw26-target-input-r19-" + uuid.uuid4().hex[:8])
        cls.root.mkdir(mode=0o700)
        cls.addClassCleanup(shutil.rmtree, cls.root, ignore_errors=True)
        cls.input_record = target_inputs.prepare_targets(
            cls.root, DEPENDENCIES, PROJECT,
            intel_target_profile=target_inputs.HISTORICAL_INTEL_PROFILE,
            historical_archive_record=cls.archive_record,
        )
        cls.verified = target_inputs.verify_targets(cls.input_record, PROJECT)

    def test_real_target_prepare_and_existing_consumer_verify(self):
        self.assertEqual(self.verified["schema"], target_inputs.HISTORICAL_INTEL_SCHEMA)
        self.assertEqual(self.verified["status"], "INPUTS_VERIFIED_NOT_INSTALLED_OR_RELEASED")
        self.assertEqual(set(self.verified["targets"]), set(target_inputs.COUNTS))
        intel = self.verified["intel_historical_prebuilt_profile"]
        self.assertEqual(intel["profile"], target_inputs.HISTORICAL_INTEL_PROFILE)
        self.assertEqual(intel["status"], "PASS_STATIC_INPUT_VALIDATION_ONLY")
        self.assertFalse(intel["historical_build_full_lock_matches_current"])
        self.assertEqual(intel["native_static_audit"]["minimum_os"], [12, 0, 0])
        self.assertEqual(intel["native_static_audit"]["RPATH"], [])
        self.assertFalse(intel["native_static_audit"]["executed"])
        crypto = [row for row in self.verified["targets"]["macos_x86_64"]["rows"]
                  if row["name"] == "cryptography"]
        self.assertEqual(len(crypto), 1)
        self.assertEqual(crypto[0]["actual_record"]["sha256"],
                         target_inputs.HISTORICAL_INTEL_EXPECTED["wheel"]["sha256"])
        self.assertEqual(crypto[0]["copy"]["path"], str(
            self.root / "target-inputs/macos_x86_64/wheelhouse" / Path(crypto[0]["filename"]).name))

    def test_real_historical_inputs_reach_release_manifest_and_bundle_consumers(self):
        profile = self.verified["intel_historical_prebuilt_profile"]
        prov = Path(profile["provenance_copy"]["path"])
        license_path = Path(profile["license_copy"]["path"])
        wheel_src = next(Path(row["copy"]["path"])
                         for row in self.verified["targets"]["macos_x86_64"]["rows"]
                         if row["name"] == "cryptography")
        checked = release.verify_historical_prebuilt_wheel(
            profile, prov, wheel_src, CANDIDATE / "source" / "uv.lock", license_path,
            "macos_x86_64")
        self.assertEqual(checked["profile"], target_inputs.HISTORICAL_INTEL_PROFILE)
        self.assertFalse(checked["historical_build_full_lock_matches_current"])
        with tempfile.TemporaryDirectory(prefix="w26-r19-release-consumer-", dir="/private/tmp") as tmp:
            root = Path(tmp)
            wheelhouse = root / "wheelhouse"
            wheelhouse.mkdir(mode=0o700)
            for row in self.verified["targets"]["macos_x86_64"]["rows"]:
                shutil.copyfile(row["copy"]["path"], wheelhouse / row["filename"])
            app_wheel = wheelhouse / "comsol_mcp-0.1.10-py3-none-any.whl"
            _write_test_application_wheel(app_wheel)
            base_lock = Path(self.verified["targets"]["macos_x86_64"]["requirements"]["path"])
            install_lock = root / "requirements.lock"
            release.compose_install_lock(base_lock, app_wheel, "macos_x86_64", install_lock)
            source_lock = CANDIDATE / "source" / "uv.lock"
            metadata = root / "metadata"
            manifest = release.create_bundle_manifest(
                wheelhouse, "macos_x86_64", install_lock, source_lock, metadata,
                historical_prebuilt_profile=profile, historical_provenance=prov,
                historical_openssl_license=license_path)
            self.assertNotIn("derived_wheel", manifest)
            self.assertEqual(manifest["historical_prebuilt_wheel"]["origin"],
                             "historical-prebuilt-input")
            self.assertFalse(manifest["historical_prebuilt_wheel"]["execution_or_installation_verified"])
            sbom = json.loads((metadata / "SBOM.cdx.json").read_text(encoding="utf-8"))
            crypto = next(row for row in sbom["components"] if row.get("name") == "cryptography")
            self.assertIn({"name": "release.origin", "value": "historical-prebuilt-input"}, crypto["properties"])
            build_evidence = root / "build-evidence"
            build_evidence.mkdir(mode=0o700)
            source_rows = []
            source_manifest_sha = hashlib.sha256(json.dumps(
                source_rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            (build_evidence / "source-snapshot-manifest.json").write_text(json.dumps({
                "kind": "IMMUTABLE_RUNTIME_SOURCE_SNAPSHOT", "files": source_rows,
                "file_count": 0, "manifest_sha256": source_manifest_sha,
            }), encoding="utf-8")
            app_meta = release.wheel_metadata(app_wheel)
            (build_evidence / "wheel-build-receipt.json").write_text(json.dumps({
                "status": "WHEEL_BUILT_NATIVE_UNVERIFIED", "wheel": app_wheel.name,
                "sha256": release.sha256_file(app_wheel),
                "wheel_metadata": {"version": app_meta["version"]},
                "source_file_count": 0, "source_manifest_sha256": source_manifest_sha,
            }), encoding="utf-8")
            operations = root / "RELEASE_OPERATIONS.md"
            operations.write_text("Offline input-only test fixture.\n", encoding="utf-8")
            archive_path = root / "offline-bundle.zip"
            receipt = release.package_wheelhouse_bundle(
                wheelhouse, install_lock, source_lock, metadata, operations, "macos_x86_64",
                archive_path, root / "package-evidence", build_evidence,
                CANDIDATE / "source" / "tools" / "full_project_release.py",
                historical_prebuilt_profile=profile, historical_provenance=prov,
                historical_openssl_license=license_path)
            self.assertEqual(receipt["status"], "OFFLINE_BUNDLE_PACKAGED_NATIVE_UNVERIFIED")
            with zipfile.ZipFile(archive_path) as archive:
                members = set(archive.namelist())
                self.assertIn("evidence/cryptography-x86-historical-prebuilt-provenance.json", members)
                self.assertIn("licenses/openssl-4.0.2-LICENSE.txt", members)
                packaged = json.loads(archive.read("OFFLINE_BUNDLE_MANIFEST.json"))
                manifest_files = {row["path"]: row for row in packaged["files_excluding_this_manifest"]}
                self.assertEqual(manifest_files[
                    "evidence/cryptography-x86-historical-prebuilt-provenance.json"]["sha256"],
                    target_inputs.HISTORICAL_INTEL_EXPECTED["provenance"]["sha256"])

    def test_release_consumer_rejects_profile_and_current_lock_drift(self):
        profile = self.verified["intel_historical_prebuilt_profile"]
        prov = Path(profile["provenance_copy"]["path"])
        license_path = Path(profile["license_copy"]["path"])
        wheel_src = Path(profile["wheel_copy"]["path"])
        wrong = dict(profile, profile="unapproved-historical-profile")
        with self.assertRaisesRegex(release.ReleaseError, "unknown identity"):
            release.verify_historical_prebuilt_wheel(
                wrong, prov, wheel_src, CANDIDATE / "source" / "uv.lock", license_path,
                "macos_x86_64")
        with tempfile.TemporaryDirectory(prefix="w26-r19-lock-drift-", dir="/private/tmp") as tmp:
            changed_lock = Path(tmp) / "uv.lock"
            changed_lock.write_bytes((CANDIDATE / "source" / "uv.lock").read_bytes() + b"\n# changed\n")
            with self.assertRaisesRegex(release.ReleaseError, "not bound to this current uv.lock"):
                release.verify_historical_prebuilt_wheel(
                    profile, prov, wheel_src, changed_lock, license_path, "macos_x86_64")

    def test_old_default_profile_hashes_remain_exact_and_never_alias_archive(self):
        self.assertEqual(target_inputs.CRYPTO[1],
                         "a2a2e473f8ccaf1fb9a2e4ee51723f6989dedb16bf3da7a98b1099003d6c0321")
        self.assertEqual(target_inputs.PROVENANCE[1],
                         "b4b8a5561570b81d03812e653cf289ea8f1534665b7c9545e98aca196e31da71")
        self.assertNotEqual(target_inputs.CRYPTO[1],
                            target_inputs.HISTORICAL_INTEL_EXPECTED["wheel"]["sha256"])
        self.assertNotEqual(target_inputs.PROVENANCE[1],
                            target_inputs.HISTORICAL_INTEL_EXPECTED["provenance"]["sha256"])

    def test_unknown_profile_wrong_archive_record_and_wrong_lock_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError, "unknown finite Intel"):
            target_inputs.prepare_targets(self.root, DEPENDENCIES, PROJECT,
                                          intel_target_profile="x86-latest")
        bad = dict(self.archive_record, sha256="0" * 64)
        with self.assertRaises(RuntimeError):
            target_inputs._historical_intel_archive_record(bad)
        archive_raw, _ = self.helper.raw_file(ARCHIVE)
        with tempfile.TemporaryDirectory(prefix="w26-r19-lock-", dir="/private/tmp") as tmp:
            tmp_path = Path(tmp)
            lock = CANDIDATE / "source" / "uv.lock"
            raw = lock.read_bytes()
            good = target_inputs.HISTORICAL_INTEL_EXPECTED["sdist_sha256"].encode()
            changed = raw.replace(good, b"0" * 64, 1)
            self.assertNotEqual(changed, raw)
            (tmp_path / "uv.lock").write_bytes(changed)
            with self.assertRaisesRegex(RuntimeError, "current uv.lock sdist differs"):
                target_inputs._historical_intel_material(archive_raw, self.archive_record,
                                                          tmp_path / "pyproject.toml")

    def test_existing_consumer_rejects_changed_target_wheel_bytes(self):
        crypto = next(row for row in self.verified["targets"]["macos_x86_64"]["rows"]
                      if row["name"] == "cryptography")
        path = Path(crypto["copy"]["path"])
        original = path.read_bytes()
        altered = bytearray(original)
        altered[len(altered) // 2] ^= 1
        try:
            path.write_bytes(altered)
            with self.assertRaises(RuntimeError):
                target_inputs.verify_targets(self.input_record, PROJECT)
        finally:
            path.write_bytes(original)
        target_inputs.verify_targets(self.input_record, PROJECT)

    def test_wheel_record_and_macho_controls_reject_tampering(self):
        archive_raw, _ = self.helper.raw_file(ARCHIVE)
        _, members, _ = target_inputs._historical_intel_material(
            archive_raw, self.archive_record, PROJECT)
        wheel_raw = members["wheel"]
        with zipfile.ZipFile(io.BytesIO(wheel_raw)) as source:
            files = {name: source.read(name) for name in source.namelist()}
        metadata_name = next(name for name in files if name.endswith(".dist-info/METADATA"))
        changed_metadata = bytearray(files[metadata_name])
        changed_metadata[0] ^= 1
        files[metadata_name] = bytes(changed_metadata)
        altered_wheel = io.BytesIO()
        with zipfile.ZipFile(altered_wheel, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for name, raw in files.items():
                output.writestr(name, raw)
        with self.assertRaisesRegex(RuntimeError, "RECORD byte digest differs"):
            target_inputs.wheel_record(
                altered_wheel.getvalue(),
                target_inputs.HISTORICAL_INTEL_EXPECTED["wheel"]["member"].split("/")[-1],
                "macos_x86_64")
        extension = bytearray(files["cryptography/hazmat/bindings/_rust.abi3.so"])
        extension[40] ^= 1
        with self.assertRaisesRegex(RuntimeError, "extension bytes differ"):
            target_inputs._intel_crypto_macho(bytes(extension))

    def test_consumer_rejects_wrong_named_profile_metadata(self):
        manifest_path = Path(self.input_record["path"])
        original = manifest_path.read_bytes()
        value = json.loads(original)
        value["intel_historical_prebuilt_profile"]["profile"] = "unreviewed-profile"
        manifest_path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
        try:
            changed_record = self.helper.raw_file(manifest_path)[1]
            with self.assertRaisesRegex(RuntimeError, "historical Intel target profile identity"):
                target_inputs.verify_targets(changed_record, PROJECT)
        finally:
            manifest_path.write_bytes(original)
        target_inputs.verify_targets(self.input_record, PROJECT)


def _write_test_application_wheel(path: Path) -> None:
    package = "comsol_mcp/__init__.py"
    dist_info = "comsol_mcp-0.1.10.dist-info"
    members = {
        package: b"__version__ = '0.1.10'\n",
        f"{dist_info}/METADATA": b"Metadata-Version: 2.1\nName: comsol-mcp\nVersion: 0.1.10\nLicense: MIT\n\n",
        f"{dist_info}/WHEEL": b"Wheel-Version: 1.0\nGenerator: R19-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n",
    }
    rows = []
    for name, payload in members.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).decode().rstrip("=")
        rows.append((name, "sha256=" + digest, str(len(payload))))
    rows.append((f"{dist_info}/RECORD", "", ""))
    record = io.StringIO(newline="")
    csv.writer(record, lineterminator="\n").writerows(rows)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
        archive.writestr(f"{dist_info}/RECORD", record.getvalue())


if __name__ == "__main__":
    unittest.main()
