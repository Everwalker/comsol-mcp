"""Portable T38 inventory tests using only ownerless in-memory fakes."""
from __future__ import annotations

from pathlib import Path
import unittest

from comsol_mcp import _g3_common, _g3_model_delivery, _g3_w13, _model_ops, _state
from comsol_mcp._g2_contract import ExecutionContractError


def _unknown() -> ExecutionContractError:
    return ExecutionContractError(
        "EXECUTION_STATE_UNKNOWN",
        "synthetic ownerless inventory read",
        safe_retry=False,
        details={"execution_state_unknown": True, "safe_retry": False},
        stage="post_dispatch",
    )


class _Tags:
    def tags(self) -> list[str]:
        return []


class _Parameters:
    def __init__(self, ordinary_error: bool = False) -> None:
        self.ordinary_error = ordinary_error

    def varnames(self) -> list[str]:
        if self.ordinary_error:
            raise RuntimeError("synthetic ordinary parameter inventory error")
        return []


class _FakeJava:
    def __init__(self, *, label_unknown: bool = False,
                 label_ordinary_error: bool = False,
                 path_unknown_at: int | None = None,
                 parameter_ordinary_error: bool = False) -> None:
        self.label_unknown = label_unknown
        self.label_ordinary_error = label_ordinary_error
        self.path_unknown_at = path_unknown_at
        self.parameter_ordinary_error = parameter_ordinary_error
        self.label_calls = 0
        self.path_calls = 0
        self.parameter_calls = 0

    def label(self) -> str:
        self.label_calls += 1
        if self.label_unknown:
            raise _unknown()
        if self.label_ordinary_error:
            raise RuntimeError("synthetic ordinary label error")
        return "synthetic model"

    def getFilePath(self) -> str:
        self.path_calls += 1
        if self.path_calls == self.path_unknown_at:
            raise _unknown()
        return ""

    def component(self) -> _Tags:
        return _Tags()

    def param(self) -> _Parameters:
        self.parameter_calls += 1
        return _Parameters(self.parameter_ordinary_error)

    def study(self) -> _Tags:
        return _Tags()

    def sol(self) -> _Tags:
        return _Tags()

    def result(self) -> _FakeJava:
        return self

    def dataset(self) -> _Tags:
        return _Tags()

    def getFileResourceInventory(self) -> list[dict[str, str]]:
        return []


class _FakeModel:
    def __init__(self, java: _FakeJava) -> None:
        self.java = java
        self.name_calls = 0

    def name(self) -> str:
        self.name_calls += 1
        return "fallback label"

    def func(self) -> _Tags:
        return _Tags()


class _FakeWorker:
    def __init__(self, model: _FakeModel) -> None:
        self._model = model

    def client(self) -> _FakeWorker:
        return self

    def model(self, _model_tag: str) -> _FakeModel:
        return self._model


class _MetadataNode:
    def __init__(self, *, unknown: bool = False, ordinary_error: bool = False) -> None:
        self.unknown = unknown
        self.ordinary_error = ordinary_error
        self.calls: list[str] = []

    def getValueType(self, _name: str) -> str:
        self.calls.append("getValueType")
        if self.unknown:
            raise _unknown()
        if self.ordinary_error:
            raise RuntimeError("synthetic ordinary metadata error")
        return "String"

    def getAllowedPropertyValues(self, _name: str) -> list[str]:
        self.calls.append("getAllowedPropertyValues")
        return []

    def getType(self) -> str:
        self.calls.append("getType")
        return "Interpolation"


def _managed_context() -> dict[str, object]:
    return {
        "project_root": str(Path(__file__).resolve().parents[1]),
        "project_id": "synthetic-project",
        "model_ref": {"schema_version": 1, "model_tag": "synthetic-model"},
    }


class T38InventoryUnknownPortableTests(unittest.TestCase):
    def test_real_state_label_and_path_helpers_fail_closed_on_unknown(self) -> None:
        for probe in ("label", "path"):
            with self.subTest(probe=probe):
                java = _FakeJava(
                    label_unknown=probe == "label",
                    path_unknown_at=1 if probe == "path" else None,
                )
                model = _FakeModel(java)
                with _g3_common.fail_on_unknown_probes():
                    callback = _g3_common.scoped_unknown_probe_callback()
                    with self.assertRaises(ExecutionContractError) as raised:
                        _model_ops._model_tree_data(model, on_probe_error=callback)
                self.assertEqual(raised.exception.code, "EXECUTION_STATE_UNKNOWN")
                self.assertFalse(raised.exception.safe_retry)
                if probe == "label":
                    self.assertEqual(model.name_calls, 0)
                    self.assertEqual(java.path_calls, 0)
                else:
                    self.assertEqual(java.path_calls, 1)
                    self.assertEqual(java.parameter_calls, 0)

    def test_real_state_label_keeps_ordinary_name_fallback(self) -> None:
        java = _FakeJava(label_ordinary_error=True)
        model = _FakeModel(java)
        with _g3_common.fail_on_unknown_probes():
            value = _state._safe_model_label(
                model, on_probe_error=_g3_common.scoped_unknown_probe_callback(),
            )
        self.assertEqual(value, "fallback label")
        self.assertEqual(model.name_calls, 1)

    def test_w13_metadata_unknown_stops_following_metadata_getters(self) -> None:
        node = _MetadataNode(unknown=True)
        with _g3_common.fail_on_unknown_probes():
            callback = _g3_common.scoped_unknown_probe_callback()
            with self.assertRaises(ExecutionContractError) as raised:
                _g3_w13._metadata_rows(node, ["interp"], on_probe_error=callback)
        self.assertEqual(raised.exception.code, "EXECUTION_STATE_UNKNOWN")
        self.assertFalse(raised.exception.safe_retry)
        self.assertEqual(node.calls, ["getValueType"])

    def test_w13_ordinary_metadata_error_remains_best_effort(self) -> None:
        node = _MetadataNode(ordinary_error=True)
        with _g3_common.fail_on_unknown_probes():
            callback = _g3_common.scoped_unknown_probe_callback()
            rows = _g3_w13._metadata_rows(node, ["interp"], on_probe_error=callback)
        self.assertEqual(rows["interp"]["metadata_status"], "UNKNOWN")
        self.assertEqual(
            node.calls,
            ["getValueType", "getAllowedPropertyValues", "getType"],
        )

    def test_ordinary_tree_error_reports_partial_inventory_coverage(self) -> None:
        model = _FakeModel(_FakeJava(parameter_ordinary_error=True))
        report = _g3_model_delivery.dependencies(
            _FakeWorker(model), "synthetic-model",
            {"verify_hashes": False, "_managed_context": _managed_context()},
        )
        self.assertEqual(report["status"], "INCOMPLETE")
        self.assertEqual(report["coverage"]["functions"], "PARTIAL")
        self.assertEqual(report["coverage"]["geometry_import_features"], "PARTIAL")
        self.assertTrue(any(item.startswith("model_structure:RuntimeError")
                            for item in report["inventory_errors"]))

    def test_saved_model_path_unknown_is_propagated(self) -> None:
        model = _FakeModel(_FakeJava(path_unknown_at=2))
        with self.assertRaises(ExecutionContractError) as raised:
            _g3_model_delivery.dependencies(
                _FakeWorker(model), "synthetic-model",
                {"verify_hashes": True, "_managed_context": _managed_context()},
            )
        self.assertEqual(raised.exception.code, "EXECUTION_STATE_UNKNOWN")
        self.assertFalse(raised.exception.safe_retry)
        self.assertEqual(raised.exception.stage, "post_dispatch")
        self.assertEqual(model.java.path_calls, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
