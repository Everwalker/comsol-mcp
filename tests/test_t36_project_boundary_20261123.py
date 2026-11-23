from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path
import sys
import tempfile
import types
import unittest

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "comsol_mcp"
if "comsol_mcp" not in sys.modules:
    package = types.ModuleType("comsol_mcp")
    package.__path__ = [str(PACKAGE_ROOT)]
    sys.modules["comsol_mcp"] = package

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._g2_code import read_source_snapshot
from comsol_mcp._g3_w13 import _RELOAD_DIGEST_PROVIDER_KEY, function_data_reload
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._project_root import close_binding_releasing, open_project_root_binding


class FakeFunction:
    def __init__(self, filename: Path) -> None:
        self.filename = str(filename)
        self.reads: list[bytes] = []

    def getType(self) -> str:
        return "Interpolation"

    def getString(self, name: str):
        return self.filename if name == "filename" else None

    def refresh(self) -> None:
        self.reads.append(Path(self.filename).read_bytes())

    def functionNames(self) -> list[str]:
        return ["synthetic"]


class FakeModel:
    def __init__(self, function: FakeFunction) -> None:
        self.function = function

    def func(self, tag: str):
        if tag != "int_synthetic":
            raise KeyError(tag)
        return self.function


class FakeWorker:
    def __init__(self, model: FakeModel) -> None:
        self._model = model

    def client(self):
        return self

    def model(self, tag: str):
        if tag != "Model":
            raise KeyError(tag)
        return self._model


def _managed_backend(project_root: Path, *, authorized: bool = True):
    backend = object.__new__(ManagedBackend)
    backend._project_root_context = ContextVar("t36_project_root", default=None)
    backend._project_root_binding_context = ContextVar("t36_project_root_binding", default=None)

    def authorize(project_id: str, permission: str):
        if not authorized or project_id != "t36-synthetic-project" or permission != "project_write":
            raise ExecutionContractError("PERMISSION_DENIED", "synthetic project authority denied")
        return {"project_id": project_id, "workspace": str(project_root)}

    backend.project_authorizer = authorize
    return backend


def _reload(worker: FakeWorker, provider):
    return function_data_reload(worker, "Model", {
        "path": {"segments": [{"collection": "func", "tag": "int_synthetic"}]},
        _RELOAD_DIGEST_PROVIDER_KEY: provider,
    })


class T36ProjectBoundaryTests(unittest.TestCase):
    def test_outside_project_reference_is_refused_before_worker_refresh(self) -> None:
        with tempfile.TemporaryDirectory(prefix="t36-outside-", dir="/private/tmp") as raw:
            base = Path(raw)
            project_root = base / "authorized-project"
            project_root.mkdir()
            outside = base / "unapproved" / "dependency.csv"
            outside.parent.mkdir()
            outside.write_bytes(b"T36 fresh synthetic private dependency\n")

            backend = _managed_backend(project_root)
            provider = backend._project_model_file_digest_provider("t36-synthetic-project")
            function = FakeFunction(outside)
            worker = FakeWorker(FakeModel(function))

            with self.assertRaises(ExecutionContractError) as caught:
                _reload(worker, provider)

            self.assertEqual(caught.exception.code, "ACCESS_VIOLATION")
            self.assertEqual(caught.exception.stage, "validation")
            self.assertEqual(caught.exception.details["digest_status"],
                             "not_reported_outside_project_boundary")
            self.assertEqual(function.reads, [])

    def test_missing_project_write_authority_is_refused_before_worker_refresh(self) -> None:
        with tempfile.TemporaryDirectory(prefix="t36-no-authority-", dir="/private/tmp") as raw:
            project_root = Path(raw) / "authorized-project"
            project_root.mkdir()
            data = project_root / "dependency.csv"
            data.write_bytes(b"T36 authorized-root synthetic dependency\n")

            backend = _managed_backend(project_root, authorized=False)
            provider = backend._project_model_file_digest_provider("t36-synthetic-project")
            function = FakeFunction(data)
            worker = FakeWorker(FakeModel(function))

            with self.assertRaises(ExecutionContractError) as caught:
                _reload(worker, provider)

            self.assertEqual(caught.exception.code, "ACCESS_VIOLATION")
            self.assertEqual(caught.exception.details["digest_status"],
                             "not_reported_project_scope_unavailable")
            self.assertEqual(function.reads, [])

    def test_authorized_in_project_reference_still_refreshes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="t36-authorized-", dir="/private/tmp") as raw:
            project_root = Path(raw) / "authorized-project"
            project_root.mkdir()
            data = project_root / "dependency.csv"
            payload = b"T36 in-project synthetic dependency\n"
            data.write_bytes(payload)

            binding = open_project_root_binding(project_root)
            backend = _managed_backend(project_root)
            backend._project_root_binding_context.set(binding)
            provider = backend._project_model_file_digest_provider("t36-synthetic-project")
            function = FakeFunction(data)
            worker = FakeWorker(FakeModel(function))
            try:
                result = _reload(worker, provider)
            finally:
                close_binding_releasing(binding, None)

            self.assertEqual(result["sha256"]["status"], "verified")
            self.assertTrue(result["refresh"]["ok"])
            self.assertEqual(function.reads, [payload])

    def test_code_source_cannot_read_private_project_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="t36-private-source-", dir="/private/tmp") as raw:
            project_root = Path(raw) / "authorized-project"
            private_root = project_root / ".g3-private"
            private_root.mkdir(parents=True)
            (private_root / "private.java").write_text(
                'class SyntheticPrivate { static String marker() { return "T36_PRIVATE"; } }\n',
                encoding="utf-8",
            )

            with self.assertRaises(ExecutionContractError) as caught:
                read_source_snapshot(project_root, ".g3-private/private.java")

            self.assertEqual(caught.exception.code, "ACCESS_VIOLATION")

    def test_code_source_refuses_symlink_component(self) -> None:
        with tempfile.TemporaryDirectory(prefix="t36-source-link-", dir="/private/tmp") as raw:
            base = Path(raw)
            project_root = base / "authorized-project"
            project_root.mkdir()
            outside = base / "outside.java"
            outside.write_text("class SyntheticOutside {}\n", encoding="utf-8")
            (project_root / "linked.java").symlink_to(outside)

            with self.assertRaises(ExecutionContractError) as caught:
                read_source_snapshot(project_root, "linked.java")

            self.assertEqual(caught.exception.code, "ACCESS_VIOLATION")

    def test_code_source_reads_normal_project_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="t36-source-local-", dir="/private/tmp") as raw:
            project_root = Path(raw) / "authorized-project"
            source_dir = project_root / "src"
            source_dir.mkdir(parents=True)
            source = 'class SyntheticLocal { static int value() { return 7; } }\n'
            (source_dir / "Local.java").write_text(source, encoding="utf-8")

            snapshot = read_source_snapshot(project_root, "src/Local.java")

            self.assertEqual(snapshot.source, source)
            self.assertEqual(snapshot.relative_parts, ("src", "Local.java"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
