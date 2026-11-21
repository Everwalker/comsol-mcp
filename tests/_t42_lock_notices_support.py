"""Synthetic lock/receipt/material builder for T42 source-lock notice tests.

Fixture data only: synthetic packages, synthetic licenses, no acquisition and
no legal or native certification. Shared by the verifier and release
consumption tests so the consumption tests can run against a baseline that
lacks tools.source_lock_notices (RED) without import-time coupling.
"""
from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

from tests.test_full_project_release import make_wheel

TARGET = "macos_arm64"
APP_VERSION = "0.1.10"
COMPONENT_SPECS = [
    ("synth-alpha", "7.1.0", "License.txt", b"Alpha synthetic license text.\n"),
    ("synth-beta", "2.0.0", "COPYING", b"Beta synthetic license differs: file name and bytes.\n"),
]
DELTA = ("synth-delta", "0.5.0", "license.txt", b"Delta synthetic license; no wheel for the target.\n")
EPSILON = ("synth-epsilon", "1.1.0", "License.txt", b"Epsilon synthetic license; the receipt declares none.\n")
GAMMA = ("synth-gamma", "3.3.3", "NOTICE.txt", b"Gamma is lock-only and never named by the receipt.\n")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sdist_bytes(name: str, version: str, member_name: str, license_text: bytes, extra=None) -> tuple[bytes, str]:
    member = f"{name.replace('-', '_')}-{version}/{member_name}"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        item = tarfile.TarInfo(member)
        item.size = len(license_text)
        archive.addfile(item, io.BytesIO(license_text))
        for kind in (extra or ()):
            other = tarfile.TarInfo(member if kind == "duplicate" else "../outside" if kind == "traversal" else "unsafe")
            if kind in {"symlink-member", "hardlink-member"}:
                other.type = tarfile.SYMTYPE if kind == "symlink-member" else tarfile.LNKTYPE
                other.linkname = member
                archive.addfile(other)
            else:
                other.size = 1
                archive.addfile(other, io.BytesIO(b"x"))
    return buffer.getvalue(), member


class Fixture:
    def __init__(self, root: Path):
        self.root = root
        self.materials = root / "materials"
        self.materials.mkdir()
        self.wheelhouse = root / "wheelhouse"
        self.wheelhouse.mkdir()
        self.wheels: dict[str, tuple[str, int]] = {}
        self.sdists: dict[str, bytes] = {}
        self.members: dict[str, str] = {}
        self.licenses: dict[str, bytes] = {}
        self.packages: dict[str, dict] = {}
        self.receipt_components: list[dict] = []
        self.app_sha = make_wheel(self.wheelhouse / f"comsol_mcp-{APP_VERSION}-py3-none-any.whl",
                                  name="comsol-mcp", version=APP_VERSION)

    def add_package(self, name, version, member_name, license_text, *, receipt_notices=None,
                    wheel_targets=(TARGET,), lock_only=False, extra_members=None):
        raw, member = sdist_bytes(name, version, member_name, license_text, extra_members)
        dist = name.replace("-", "_")
        component_dir = self.materials / name
        component_dir.mkdir()
        (component_dir / "source.tar.gz").write_bytes(raw)
        sdist_url = f"https://files.pythonhosted.org/synthetic/{dist}-{version}.tar.gz"
        metadata = {"info": {"name": name, "version": version},
                    "urls": [{"url": sdist_url, "size": len(raw), "digests": {"sha256": sha(raw)}}]}
        metadata_raw = json.dumps(metadata).encode()
        (component_dir / "metadata.json").write_bytes(metadata_raw)
        self.sdists[name], self.members[name], self.licenses[name] = raw, member, license_text
        wheel_entries = []
        for target in wheel_targets:
            filename = f"{dist}-{version}-py3-none-any.whl"
            sha256, size = self.wheels.get(filename, (None, 0))
            if sha256 is None:
                sha256 = make_wheel(self.wheelhouse / filename, name=name, version=version)
                size = (self.wheelhouse / filename).stat().st_size
                self.wheels[filename] = (sha256, size)
            wheel_entries.append({"url": f"https://files.pythonhosted.org/synthetic/{filename}",
                                  "hash": "sha256:" + sha256, "size": size})
        self.packages[name] = {"name": name, "version": version, "source": {"registry": "https://pypi.org/simple"},
                               "sdist": {"url": sdist_url, "hash": "sha256:" + sha(raw), "size": len(raw)},
                               "wheels": wheel_entries}
        if lock_only:
            return
        notices = []
        for index, notice_text in enumerate(receipt_notices if receipt_notices is not None else [license_text]):
            notice_file = component_dir / f"notice-{index}.txt"
            notice_file.write_bytes(notice_text)
            notices.append({"archive_member": member, "file": f"{name}/notice-{index}.txt",
                            "sha256": sha(notice_text), "bytes": len(notice_text)})
        wheels_claim = {}
        for entry in wheel_entries:
            wheels_claim[TARGET] = {"filename": Path(entry["url"]).name, "sha256": entry["hash"][7:]}
        self.receipt_components.append({"name": name, "version": version, "source_url": sdist_url,
                                        "source_sha256": sha(raw), "source_bytes": len(raw),
                                        "metadata_url": f"https://pypi.org/pypi/{name}/{version}/json",
                                        "metadata_sha256": sha(metadata_raw), "notices": notices,
                                        "wheels": wheels_claim})

    def save_lock(self, path: Path | None = None) -> Path:
        path = path or self.root / "uv.lock"
        lines = ['version = 1\n', 'requires-python = ">=3.12"\n']
        for name, row in self.packages.items():
            lines.append('\n[[package]]\n')
            lines.append(f'name = "{row["name"]}"\nversion = "{row["version"]}"\n')
            lines.append('source = { registry = "https://pypi.org/simple" }\n')
            sdist = row["sdist"]
            lines.append(f'sdist = {{ url = "{sdist["url"]}", hash = "{sdist["hash"]}", size = {sdist["size"]} }}\n')
            lines.append("wheels = [\n")
            for wheel in row["wheels"]:
                lines.append(f'    {{ url = "{wheel["url"]}", hash = "{wheel["hash"]}", size = {wheel["size"]} }},\n')
            lines.append("]\n")
        path.write_text("".join(lines))
        return path

    def save_receipt(self) -> Path:
        lock_path = self.root / "uv.lock"
        receipt = {"lock_sha256": sha(lock_path.read_bytes()), "scope": "SYNTHETIC_FIXTURE_NOT_LEGAL_OR_NATIVE",
                   "targets": {TARGET: {"components": len(self.receipt_components)}},
                   "components": self.receipt_components}
        path = self.materials / "receipt.json"
        path.write_text(json.dumps(receipt, indent=1))
        return path

    def save_requirements(self) -> Path:
        lines = [f"comsol-mcp=={APP_VERSION} --hash=sha256:{self.app_sha}"]
        for name, _version, _member, _text in COMPONENT_SPECS + [EPSILON]:
            filename = f"{name.replace('-', '_')}-{_version}-py3-none-any.whl"
            lines.append(f"{name}=={_version} --hash=sha256:{self.wheels[filename][0]}")
        path = self.root / "requirements.lock"
        path.write_text("\n".join(lines) + "\n")
        return path

    def finalize(self) -> dict:
        lock = self.save_lock()
        receipt = self.save_receipt()
        requirements = self.save_requirements()
        return {"lock": lock, "receipt": receipt, "requirements": requirements}

    def sdist_path(self, name: str) -> Path:
        return self.materials / name / "source.tar.gz"

    def modify_sdist(self, name: str, raw: bytes, sync_receipt_and_lock: bool = False) -> None:
        self.sdist_path(name).write_bytes(raw)
        if not sync_receipt_and_lock:
            return
        row = self.packages[name]
        row["sdist"]["hash"], row["sdist"]["size"] = "sha256:" + sha(raw), len(raw)
        metadata_path = self.materials / name / "metadata.json"
        metadata = json.loads(metadata_path.read_bytes())
        metadata["urls"][0]["size"] = len(raw)
        metadata["urls"][0]["digests"]["sha256"] = sha(raw)
        metadata_raw = json.dumps(metadata).encode()
        metadata_path.write_bytes(metadata_raw)
        for component in self.receipt_components:
            if component["name"] == name:
                component["source_sha256"], component["source_bytes"] = sha(raw), len(raw)
                component["metadata_sha256"] = sha(metadata_raw)
        self.save_lock()
        self.save_receipt()


def build_fixture(tmp_path: Path) -> tuple[Fixture, dict]:
    fixture = Fixture(tmp_path)
    for name, version, member, text in COMPONENT_SPECS:
        fixture.add_package(name, version, member, text)
    fixture.add_package(*DELTA, wheel_targets=())
    fixture.add_package(*EPSILON, receipt_notices=[])
    fixture.add_package(*GAMMA, wheel_targets=(), lock_only=True)
    return fixture, fixture.finalize()
