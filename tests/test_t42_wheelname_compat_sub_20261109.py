"""T42 wheel-name compatibility controls (case-insensitive + legacy '.' form).

Contract basis: PyPA "Binary distribution format" specification, fetched
2026-11-09 from packaging.python.org (raw snapshot in the run root):
  "In distribution names, any run of -_. characters (HYPHEN-MINUS, LOW LINE and
   FULL STOP) should be replaced with _ (LOW LINE), and uppercase characters
   should be replaced with corresponding lowercase ones. ... Tools consuming
   wheels must be prepared to accept . (FULL STOP) and uppercase letters,
   however, as these were allowed by an earlier version of this specification."
So the current generation rule is lowercase+underscore, while the consuming
route here must additionally accept the legacy dotted and uppercase forms.
Identity stays closed by the lock row (exact wheel URL basename + sha256),
the receipt hash and the version equality checks -- never by the prefix alone.

Synthetic fixtures only: no acquisition, no install, no bundle build, no
native or legal certification.
"""
from __future__ import annotations

import hashlib
import io
import json
import pathlib
import re
import tarfile
import zipfile

import pytest
from packaging.utils import canonicalize_name

from tools import full_project_release as release
from tools import source_lock_notices as sln
from tests._t42_lock_notices_support import APP_VERSION, TARGET, Fixture, sha


def make_compat_wheel(path: pathlib.Path, name: str, version: str, dist_component: str) -> str:
    """Wheel zip whose filename-visible dist-info uses dist_component verbatim."""
    info = f"{dist_component}-{version}.dist-info"
    members = {
        f"{info}/METADATA": (
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
            "License: MIT\nClassifier: License :: OSI Approved :: MIT License\n\n"
        ).encode(),
        f"{info}/WHEEL": f"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n".encode(),
        f"{info}/licenses/LICENSE.txt": b"MIT license text\n",
        f"{dist_component}/__init__.py": b"VALUE = 1\n",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member, content in members.items():
            archive.writestr(member, content)
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CompatFixture(Fixture):
    """Package whose wheel filename uses an arbitrary (legacy or current) dist component."""

    def add_compat_package(self, name, version, dist_component, *, receipt=True):
        license_text = f"{name} synthetic license text for {dist_component}.\n".encode()
        buffer = io.BytesIO()
        member = f"{name}-{version}/LICENSE.txt"
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            item = tarfile.TarInfo(member)
            item.size = len(license_text)
            archive.addfile(item, io.BytesIO(license_text))
        raw = buffer.getvalue()
        component_dir = self.materials / name
        component_dir.mkdir()
        (component_dir / "source.tar.gz").write_bytes(raw)
        sdist_url = f"https://files.pythonhosted.org/synthetic/{name}-{version}.tar.gz"
        metadata = {"info": {"name": name, "version": version},
                    "urls": [{"url": sdist_url, "size": len(raw), "digests": {"sha256": sha(raw)}}]}
        metadata_raw = json.dumps(metadata).encode()
        (component_dir / "metadata.json").write_bytes(metadata_raw)
        filename = f"{dist_component}-{version}-py3-none-any.whl"
        wheel_sha = make_compat_wheel(self.wheelhouse / filename, name, version, dist_component)
        wheel_size = (self.wheelhouse / filename).stat().st_size
        self.wheels[filename] = (wheel_sha, wheel_size)
        self.sdists[name], self.members[name], self.licenses[name] = raw, member, license_text
        self.packages[name] = {"name": name, "version": version,
                               "source": {"registry": "https://pypi.org/simple"},
                               "sdist": {"url": sdist_url, "hash": "sha256:" + sha(raw), "size": len(raw)},
                               "wheels": [{"url": f"https://files.pythonhosted.org/synthetic/{filename}",
                                           "hash": "sha256:" + wheel_sha, "size": wheel_size}]}
        if not receipt:
            return filename, wheel_sha
        notice_file = component_dir / "notice-0.txt"
        notice_file.write_bytes(license_text)
        self.receipt_components.append({
            "name": name, "version": version, "source_url": sdist_url,
            "source_sha256": sha(raw), "source_bytes": len(raw),
            "metadata_url": f"https://pypi.org/pypi/{name}/{version}/json",
            "metadata_sha256": sha(metadata_raw),
            "notices": [{"archive_member": member, "file": f"{name}/notice-0.txt",
                         "sha256": sha(license_text), "bytes": len(license_text)}],
            "wheels": {TARGET: {"filename": filename, "sha256": wheel_sha}}})
        return filename, wheel_sha

    def save_requirements_for(self, entries) -> pathlib.Path:
        lines = [f"comsol-mcp=={APP_VERSION} --hash=sha256:{self.app_sha}"]
        for name, version in entries:
            wheel_sha = next(w["hash"][7:] for w in self.packages[name]["wheels"])
            lines.append(f"{name}=={version} --hash=sha256:{wheel_sha}")
        path = self.root / "requirements.lock"
        path.write_text("\n".join(lines) + "\n")
        return path


def one_package_fixture(tmp_path, name, version, dist_component):
    fixture = CompatFixture(tmp_path)
    filename, wheel_sha = fixture.add_compat_package(name, version, dist_component)
    paths = {"lock": fixture.save_lock(), "receipt": fixture.save_receipt(),
             "requirements": fixture.save_requirements_for([(name, version)])}
    return fixture, paths, filename, wheel_sha


# --- Acceptance: spec-normalized plus the legacy forms consumers must accept ---

COMPAT_CASES = [
    ("Syn.theta", "7.0.3", "syn_theta"),   # mixed-case lock name, spec-normalized lowercase wheel
    ("syn.theta", "7.0.3", "Syn_Theta"),   # lowercase name, legacy uppercase escaped wheel
    ("syn.theta", "7.0.3", "syn.theta"),   # legacy FULL STOP form (converted old negative)
    ("Syn.theta", "7.0.3", "Syn.theta"),   # legacy uppercase + FULL STOP combined
    ("Syn--Theta", "5.0.0", "syn_theta"),  # mixed-case run-collapse name, normalized wheel
]


@pytest.mark.parametrize(("name", "version", "dist_component"), COMPAT_CASES)
def test_normalized_and_legacy_wheel_name_forms_accepted(tmp_path, name, version, dist_component):
    fixture, paths, filename, wheel_sha = one_package_fixture(tmp_path, name, version, dist_component)
    payloads, route = sln.lock_bound_notice_payloads(paths["receipt"], source_lock=paths["lock"],
                                                     target=TARGET)
    key = canonicalize_name(name)
    record = payloads[key][0]
    assert record["provenance"]["wheel_filename"] == filename
    assert record["provenance"]["wheel_sha256"] == wheel_sha
    assert wheel_sha == sha((fixture.wheelhouse / filename).read_bytes())
    assert route["kind"] == "SOURCE_LOCK_BOUND_PRIMARY_NOTICES"
    # Cross-entry consistency: the release module parses the same identity.
    dist, parsed_version, _tags = release._wheel_dist_version(pathlib.Path(filename))
    assert dist == key and parsed_version == version


# --- Negative controls: identity bindings must survive the compat relaxation ---

def test_unrelated_distribution_component_refused(tmp_path):
    fixture, paths, _filename, wheel_sha = one_package_fixture(tmp_path, "syn.theta", "7.0.3", "syn.theta")
    component = next(row for row in fixture.receipt_components if row["name"] == "syn.theta")
    bad = "syn_other-7.0.3-py3-none-any.whl"
    make_compat_wheel(fixture.wheelhouse / bad, "syn.theta", "7.0.3", "syn_other")
    bad_sha = sha((fixture.wheelhouse / bad).read_bytes())
    entry = fixture.packages["syn.theta"]["wheels"][0]
    entry["url"] = f"https://files.pythonhosted.org/synthetic/{bad}"
    entry["hash"] = "sha256:" + bad_sha
    component["wheels"][TARGET] = {"filename": bad, "sha256": bad_sha}
    fixture.save_lock()
    fixture.save_receipt()
    with pytest.raises(sln.SourceLockNoticeError,
                       match=r"receipt wheel filename disagrees with the component identity"):
        sln.lock_bound_notice_payloads(paths["receipt"], source_lock=paths["lock"], target=TARGET)


def test_legacy_form_wrong_version_refused(tmp_path):
    fixture, paths, _filename, _sha = one_package_fixture(tmp_path, "syn.theta", "7.0.3", "syn.theta")
    component = next(row for row in fixture.receipt_components if row["name"] == "syn.theta")
    bad = "syn.theta-9.9.9-py3-none-any.whl"
    make_compat_wheel(fixture.wheelhouse / bad, "syn.theta", "9.9.9", "syn.theta")
    bad_sha = sha((fixture.wheelhouse / bad).read_bytes())
    entry = fixture.packages["syn.theta"]["wheels"][0]
    entry["url"] = f"https://files.pythonhosted.org/synthetic/{bad}"
    entry["hash"] = "sha256:" + bad_sha
    component["wheels"][TARGET] = {"filename": bad, "sha256": bad_sha}
    fixture.save_lock()
    fixture.save_receipt()
    with pytest.raises(sln.SourceLockNoticeError,
                       match=r"receipt wheel filename disagrees with the component identity"):
        sln.lock_bound_notice_payloads(paths["receipt"], source_lock=paths["lock"], target=TARGET)


def test_legacy_form_hash_mismatch_refused(tmp_path):
    fixture, paths, _filename, _wheel_sha = one_package_fixture(tmp_path, "syn.theta", "7.0.3", "Syn_Theta")
    component = next(row for row in fixture.receipt_components if row["name"] == "syn.theta")
    component["wheels"][TARGET]["sha256"] = "0" * 64
    fixture.save_receipt()
    with pytest.raises(sln.SourceLockNoticeError, match=r"not the lock-pinned wheel"):
        sln.lock_bound_notice_payloads(paths["receipt"], source_lock=paths["lock"], target=TARGET)


@pytest.mark.parametrize("bad", [
    "../syn.theta-7.0.3-py3-none-any.whl",
    "sub/syn.theta-7.0.3-py3-none-any.whl",
    "syn.theta-7.0.3-py3-none-any.whlz",
    "syn.theta-7.0.3.whl",
    "-7.0.3-py3-none-any.whl",
])
def test_path_and_malformed_wheel_filenames_refused(tmp_path, bad):
    fixture, paths, _filename, bad_sha = one_package_fixture(tmp_path, "syn.theta", "7.0.3", "syn.theta")
    component = next(row for row in fixture.receipt_components if row["name"] == "syn.theta")
    entry = fixture.packages["syn.theta"]["wheels"][0]
    entry["url"] = f"https://files.pythonhosted.org/synthetic/{bad}"
    entry["hash"] = "sha256:" + bad_sha
    component["wheels"][TARGET] = {"filename": bad, "sha256": bad_sha}
    fixture.save_lock()
    fixture.save_receipt()
    with pytest.raises(sln.SourceLockNoticeError,
                       match=r"not a safe relative path|disagrees with the component identity"):
        sln.lock_bound_notice_payloads(paths["receipt"], source_lock=paths["lock"], target=TARGET)


def test_case_insensitive_name_alias_duplicate_lock_rows_refused(tmp_path):
    fixture = CompatFixture(tmp_path)
    fixture.add_compat_package("Syn.theta", "7.0.3", "syn_theta")
    fixture.add_compat_package("syn-theta", "8.0.0", "syn_theta", receipt=False)
    paths = {"lock": fixture.save_lock(), "receipt": fixture.save_receipt(),
             "requirements": fixture.save_requirements_for([("Syn.theta", "7.0.3")])}
    with pytest.raises(sln.SourceLockNoticeError,
                       match=r"ambiguous duplicate package entry"):
        sln.lock_bound_notice_payloads(paths["receipt"], source_lock=paths["lock"], target=TARGET)


# --- Release consumption: legacy dotted wheel flows end-to-end ---

def test_legacy_dot_wheel_consumed_by_release_manifest(tmp_path):
    fixture, paths, filename, wheel_sha = one_package_fixture(tmp_path, "syn.theta", "7.0.3", "syn.theta")
    metadata = tmp_path / "metadata"
    manifest = release.create_bundle_manifest(fixture.wheelhouse, TARGET, paths["requirements"],
                                              paths["lock"], metadata,
                                              primary_notice_receipt=paths["receipt"])
    wheel_row = next(row for row in manifest["wheels"] if canonicalize_name(row["name"]) == "syn-theta")
    assert wheel_row["sha256"] == wheel_sha
    notice_rows = [row for row in manifest["primary_notices"] if "syn-theta" in row["path"]]
    assert notice_rows, manifest["primary_notices"]
    provenance = notice_rows[0]["provenance"]
    assert provenance["wheel_filename"] == filename
    assert provenance["wheel_sha256"] == wheel_row["sha256"] == wheel_sha
    assert manifest["primary_notice_route"]["kind"] == "SOURCE_LOCK_BOUND_PRIMARY_NOTICES"
    licenses_doc = json.loads((metadata / "LICENSES.json").read_text())
    assert any(row.get("primary_source_notices") for row in licenses_doc["components"]
               if canonicalize_name(row["name"]) == "syn-theta")
