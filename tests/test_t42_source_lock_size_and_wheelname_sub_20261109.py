"""T42 sub v3 controls: notice-member pre-read size cap and PEP 427 name generality.

Synthetic lock/receipt/material fixtures only. No acquisition, no bundle build,
no installation, no native or legal certification. The size controls prove the
declared notice member is bounded before any unbounded read; they do not claim
that expansion/traversal of the whole archive is bounded. The name controls use
legitimate dotted distributions with correctly PEP 427-escaped wheel filenames
and keep wrong version/hash/name refusals intact.
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

from tools import full_project_release as release
from tools import source_lock_notices as sln
from tests._t42_lock_notices_support import APP_VERSION, TARGET, Fixture, sha

MAX = sln.MAX_NOTICE_BYTES


def wheel_distribution_component(name: str) -> str:
    """Independent re-implementation of the PEP 427 escape for fixtures."""
    return re.sub(r"[-_.]+", "_", name)


def make_normalized_wheel(path: pathlib.Path, name: str, version: str) -> str:
    dist = wheel_distribution_component(name)
    info = f"{dist}-{version}.dist-info"
    members = {
        f"{info}/METADATA": (
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
            "License: MIT\nClassifier: License :: OSI Approved :: MIT License\n\n"
        ).encode(),
        f"{info}/WHEEL": f"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n".encode(),
        f"{info}/licenses/LICENSE.txt": b"MIT license text\n",
        f"{dist}/__init__.py": b"VALUE = 1\n",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member, content in members.items():
            archive.writestr(member, content)
    return hashlib.sha256(path.read_bytes()).hexdigest()


class NormalizedFixture(Fixture):
    """Adds packages whose wheel filenames follow the PEP 427 escape rule."""

    def add_normalized_package(self, name, version, member_name, license_text):
        buffer = io.BytesIO()
        member = f"{name}-{version}/{member_name}"
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            item = tarfile.TarInfo(member)
            item.size = len(license_text)
            archive.addfile(item, io.BytesIO(license_text))
        raw = buffer.getvalue()
        dist = wheel_distribution_component(name)
        component_dir = self.materials / name
        component_dir.mkdir()
        (component_dir / "source.tar.gz").write_bytes(raw)
        sdist_url = f"https://files.pythonhosted.org/synthetic/{name}-{version}.tar.gz"
        metadata = {"info": {"name": name, "version": version},
                    "urls": [{"url": sdist_url, "size": len(raw), "digests": {"sha256": sha(raw)}}]}
        metadata_raw = json.dumps(metadata).encode()
        (component_dir / "metadata.json").write_bytes(metadata_raw)
        filename = f"{dist}-{version}-py3-none-any.whl"
        wheel_sha = make_normalized_wheel(self.wheelhouse / filename, name, version)
        wheel_size = (self.wheelhouse / filename).stat().st_size
        self.wheels[filename] = (wheel_sha, wheel_size)
        self.sdists[name], self.members[name], self.licenses[name] = raw, member, license_text
        wheel_entry = {"url": f"https://files.pythonhosted.org/synthetic/{filename}",
                       "hash": "sha256:" + wheel_sha, "size": wheel_size}
        self.packages[name] = {"name": name, "version": version,
                               "source": {"registry": "https://pypi.org/simple"},
                               "sdist": {"url": sdist_url, "hash": "sha256:" + sha(raw), "size": len(raw)},
                               "wheels": [wheel_entry]}
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

    def save_requirements_for(self, names) -> pathlib.Path:
        lines = [f"comsol-mcp=={APP_VERSION} --hash=sha256:{self.app_sha}"]
        for name in names:
            version = self.packages[name]["version"]
            filename = f"{wheel_distribution_component(name)}-{version}-py3-none-any.whl"
            lines.append(f"{name}=={version} --hash=sha256:{self.wheels[filename][0]}")
        path = self.root / "requirements.lock"
        path.write_text("\n".join(lines) + "\n")
        return path


def dotted_fixture(tmp_path: pathlib.Path, cases: list[tuple[str, str]]):
    fixture = NormalizedFixture(tmp_path)
    fixture.add_package("synth-alpha", "7.1.0", "License.txt", b"Alpha synthetic license text.\n")
    for name, version in cases:
        fixture.add_normalized_package(name, version, "LICENSE.txt",
                                       f"{name} synthetic license text.\n".encode())
    names = ["synth-alpha"] + [name for name, _ in cases]
    paths = {"lock": fixture.save_lock(), "receipt": fixture.save_receipt(),
             "requirements": fixture.save_requirements_for(names)}
    return fixture, paths


def _stage_oversized_notice(fixture, name: str, payload: bytes) -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        item = tarfile.TarInfo(fixture.members[name])
        item.size = len(payload)
        archive.addfile(item, io.BytesIO(payload))
    raw = buffer.getvalue()
    fixture.modify_sdist(name, raw, sync_receipt_and_lock=True)
    component = next(row for row in fixture.receipt_components if row["name"] == name)
    assert len(component["notices"]) == 1
    component["notices"][0]["sha256"] = sha(payload)
    component["notices"][0]["bytes"] = len(payload)
    (fixture.materials / name / "notice-0.txt").write_bytes(payload)
    fixture.save_receipt()
    return raw


@pytest.fixture
def io_calls(monkeypatch):
    calls: dict[str, list] = {"extract": [], "read": []}
    original_extract = tarfile.TarFile.extractfile

    def extractfile(self, member):
        calls["extract"].append(getattr(member, "name", str(member)))
        return original_extract(self, member)

    monkeypatch.setattr(tarfile.TarFile, "extractfile", extractfile)
    original_read = tarfile.ExFileObject.read

    def read(self, size=-1):
        data = original_read(self, size)
        calls["read"].append({"requested": size, "returned": len(data or b"")})
        return data

    monkeypatch.setattr(tarfile.ExFileObject, "read", read)
    return calls


def _assert_bounded_reads(io_calls):
    assert all(row["requested"] >= 0 for row in io_calls["read"]), io_calls["read"]
    assert all(row["requested"] <= MAX + 1 for row in io_calls["read"]), io_calls["read"]
    assert all(row["returned"] <= MAX + 1 for row in io_calls["read"]), io_calls["read"]


def test_oversized_notice_member_refused_before_any_extract(tmp_path, io_calls):
    fixture, paths = dotted_fixture(tmp_path, [])
    payload = b"X" * 600_000
    raw = _stage_oversized_notice(fixture, "synth-alpha", payload)
    assert len(raw) * 100 < len(payload)  # small compressed archive, huge expanded member
    with pytest.raises(sln.SourceLockNoticeError,
                       match=r"primary source notice exceeds its byte maximum"):
        sln.lock_bound_notice_payloads(paths["receipt"], source_lock=paths["lock"], target=TARGET)
    assert io_calls["extract"] == []  # named member never extracted
    _assert_bounded_reads(io_calls)


def test_notice_one_byte_over_max_refused_without_full_member_read(tmp_path, io_calls):
    fixture, paths = dotted_fixture(tmp_path, [])
    payload = b"Y" * (MAX + 1)
    _stage_oversized_notice(fixture, "synth-alpha", payload)
    with pytest.raises(sln.SourceLockNoticeError,
                       match=r"primary source notice exceeds its byte maximum"):
        sln.lock_bound_notice_payloads(paths["receipt"], source_lock=paths["lock"], target=TARGET)
    assert io_calls["extract"] == []
    _assert_bounded_reads(io_calls)


def test_notice_exactly_at_max_is_accepted_with_bounded_reads(tmp_path, io_calls):
    fixture, paths = dotted_fixture(tmp_path, [])
    payload = b"".join(hashlib.sha256(str(i).encode()).digest() for i in range(MAX // 32))
    assert len(payload) == MAX
    _stage_oversized_notice(fixture, "synth-alpha", payload)
    payloads, _route = sln.lock_bound_notice_payloads(paths["receipt"], source_lock=paths["lock"],
                                                      target=TARGET)
    records = payloads["synth-alpha"]
    assert [len(item["license_bytes"]) for item in records] == [MAX]
    assert records[0]["license_bytes"] == payload
    assert io_calls["extract"] == [fixture.members["synth-alpha"]]
    _assert_bounded_reads(io_calls)


DOTTED_CASES = [
    ("syn.theta", "7.0.3"),
    ("syn.alpha.beta", "1.4.2"),
    ("syn-x.y", "2.1.0"),
    ("syn_x.y", "0.9.1"),
    ("syn--theta", "5.0.0"),
]


@pytest.mark.parametrize(("name", "version"), DOTTED_CASES)
def test_legitimate_dotted_names_with_normalized_wheel_filenames_accepted(tmp_path, name, version):
    fixture, paths = dotted_fixture(tmp_path, [(name, version)])
    payloads, route = sln.lock_bound_notice_payloads(paths["receipt"], source_lock=paths["lock"],
                                                     target=TARGET)
    from packaging.utils import canonicalize_name
    key = canonicalize_name(name)
    assert sorted(payloads) == [key, "synth-alpha"]
    record = payloads[key][0]
    expected_filename = f"{wheel_distribution_component(name)}-{version}-py3-none-any.whl"
    assert record["provenance"]["wheel_filename"] == expected_filename
    assert expected_filename in {path.name for path in fixture.wheelhouse.iterdir()}
    assert route["kind"] == "SOURCE_LOCK_BOUND_PRIMARY_NOTICES"


def test_dot_in_wheel_filename_distribution_component_legacy_form_accepted(tmp_path):
    # Converted 2026-11-09 from test_dot_in_wheel_filename_distribution_component_still_refused.
    # That negative pinned the wrong contract: the PyPA binary distribution format says
    # "Tools consuming wheels must be prepared to accept . (FULL STOP) and uppercase
    # letters, however, as these were allowed by an earlier version of this specification."
    # The material below carries a genuine lock+hash binding (lock wheel URL basename is
    # the exact dotted filename, the lock hash is the measured wheel hash, and the receipt
    # claims the same hash), so the dotted legacy form must be accepted, not refused.
    fixture, paths = dotted_fixture(tmp_path, [("syn.theta", "7.0.3")])
    component = next(row for row in fixture.receipt_components if row["name"] == "syn.theta")
    legacy = "syn.theta-7.0.3-py3-none-any.whl"
    component["wheels"][TARGET]["filename"] = legacy
    entry = fixture.packages["syn.theta"]["wheels"][0]
    entry["url"] = f"https://files.pythonhosted.org/synthetic/{legacy}"
    make_normalized_wheel(fixture.wheelhouse / legacy, "syn.theta", "7.0.3")
    legacy_sha = sha((fixture.wheelhouse / legacy).read_bytes())
    entry["hash"] = "sha256:" + legacy_sha
    component["wheels"][TARGET]["sha256"] = legacy_sha
    fixture.save_lock()
    fixture.save_receipt()
    payloads, _route = sln.lock_bound_notice_payloads(paths["receipt"], source_lock=paths["lock"],
                                                      target=TARGET)
    record = payloads["syn-theta"][0]
    assert record["provenance"]["wheel_filename"] == legacy
    assert record["provenance"]["wheel_sha256"] == legacy_sha
    assert legacy_sha == sha((fixture.wheelhouse / legacy).read_bytes())
    # Acceptance keeps the hash binding: a receipt hash that no longer matches the
    # lock-pinned wheel for this exact filename is still refused.
    component["wheels"][TARGET]["sha256"] = "0" * 64
    fixture.save_receipt()
    with pytest.raises(sln.SourceLockNoticeError,
                       match=r"not the lock-pinned wheel"):
        sln.lock_bound_notice_payloads(paths["receipt"], source_lock=paths["lock"], target=TARGET)


def test_dotted_name_wrong_wheel_version_still_refused(tmp_path):
    fixture, paths = dotted_fixture(tmp_path, [("syn.theta", "7.0.3")])
    component = next(row for row in fixture.receipt_components if row["name"] == "syn.theta")
    bad = "syn_theta-9.9.9-py3-none-any.whl"
    make_normalized_wheel(fixture.wheelhouse / bad, "syn.theta", "9.9.9")
    bad_sha = sha((fixture.wheelhouse / bad).read_bytes())
    component["wheels"][TARGET]["filename"] = bad
    component["wheels"][TARGET]["sha256"] = bad_sha
    fixture.save_receipt()
    with pytest.raises(sln.SourceLockNoticeError,
                       match=r"receipt wheel filename disagrees with the component identity|"
                             r"not the lock-pinned wheel"):
        sln.lock_bound_notice_payloads(paths["receipt"], source_lock=paths["lock"], target=TARGET)


def test_dotted_name_hash_mismatch_still_refused(tmp_path):
    fixture, paths = dotted_fixture(tmp_path, [("syn.theta", "7.0.3")])
    component = next(row for row in fixture.receipt_components if row["name"] == "syn.theta")
    component["wheels"][TARGET]["sha256"] = "0" * 64
    fixture.save_receipt()
    with pytest.raises(sln.SourceLockNoticeError,
                       match=r"not the lock-pinned wheel"):
        sln.lock_bound_notice_payloads(paths["receipt"], source_lock=paths["lock"], target=TARGET)


def test_dotted_component_consumed_consistently_by_release_manifest(tmp_path):
    fixture, paths = dotted_fixture(tmp_path, [("syn.theta", "7.0.3")])
    metadata = tmp_path / "metadata"
    manifest = release.create_bundle_manifest(fixture.wheelhouse, TARGET, paths["requirements"],
                                              paths["lock"], metadata,
                                              primary_notice_receipt=paths["receipt"])
    dotted_rows = [row for row in manifest["primary_notices"] if "syn-theta" in row["path"]]
    assert dotted_rows, manifest["primary_notices"]
    provenance = dotted_rows[0]["provenance"]
    assert provenance["wheel_filename"] == "syn_theta-7.0.3-py3-none-any.whl"
    assert provenance["version"] == "7.0.3"
    wheel_row = next(row for row in manifest["wheels"]
                     if re.sub(r"[-_.]+", "-", row["name"]).lower() == "syn-theta")
    assert provenance["wheel_sha256"] == wheel_row["sha256"]
    licenses_doc = json.loads((metadata / "LICENSES.json").read_text())
    assert any(row.get("primary_source_notices") for row in licenses_doc["components"]
               if row["name"] in {"syn.theta", "syn-theta"})
