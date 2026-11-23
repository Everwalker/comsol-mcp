"""Focused controls for evaluation of models with external functions."""

import json
import unittest
from types import SimpleNamespace

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._java_worker import JavaWorkerError
from comsol_mcp._managed_backend import ManagedBackend


class FunctionNode:
    def __init__(self, type_id):
        self.type_id = type_id

    def getType(self):
        return self.type_id


class Scope:
    def __init__(self, functions):
        self.functions = functions

    def func(self, tag=None):
        return self if tag is None else FunctionNode(self.functions[tag])

    def tags(self):
        return list(self.functions)


class Model(Scope):
    def __init__(self, functions=None, components=None):
        super().__init__(functions or {})
        self.components = components or {}

    def component(self, tag=None):
        return Scope(self.components) if tag is None else Scope(self.components[tag])


class Worker:
    def __init__(self, models):
        self.models = models

    def client(self):
        return self

    def model(self, tag):
        return self.models[tag]


def backend(session_trusted, project_trusted, inventory):
    value = object.__new__(ManagedBackend)
    value.service = SimpleNamespace(ledger=SimpleNamespace(
        permissions={"inspect", "compute"} | ({"trusted_code"} if session_trusted else set())
    ))

    def authorize(project_id, permission):
        if project_id != "project-a" or permission != "trusted_code" or not project_trusted:
            raise ExecutionContractError("PERMISSION_DENIED", "no project permission")

    value.project_authorizer = authorize
    value._external_function_present = inventory
    return value


class ExternalExecutionAuthorityTests(unittest.TestCase):
    def test_inventory_scans_global_and_component_functions(self):
        value = object.__new__(ManagedBackend)
        value.worker = Worker({
            "plain": Model({"interp1": "Interpolation"}),
            "external": Model({"ext1": "External"}),
            "matlab": Model(components={"comp1": {"mat1": "MATLAB"}}),
        })
        self.assertFalse(value._external_function_present("plain"))
        self.assertTrue(value._external_function_present("external"))
        self.assertTrue(value._external_function_present("matlab"))

    def test_external_execution_requires_both_permission_scopes(self):
        for session_trusted, project_trusted in ((False, False), (False, True), (True, False), (True, True)):
            with self.subTest(session=session_trusted, project=project_trusted):
                value = backend(session_trusted, project_trusted, lambda _tag: True)
                if session_trusted and project_trusted:
                    value._require_external_execution_authority(
                        "study.run", {}, model_tag="external", project_id="project-a")
                else:
                    with self.assertRaises(ExecutionContractError) as caught:
                        value._require_external_execution_authority(
                            "study.run", {}, model_tag="external", project_id="project-a")
                    self.assertEqual(caught.exception.code, "PERMISSION_DENIED")

    def test_ordinary_model_passes_and_additional_evaluators_are_guarded(self):
        value = backend(False, False, lambda _tag: False)
        for operation in ("study.run", "metric.evaluate", "model.rebuild_target"):
            self.assertTrue(value._external_execution_required(operation, {}))
            value._require_external_execution_authority(
                operation, {}, model_tag="ordinary", project_id="project-a")

    def test_inventory_unknown_prevents_dispatch_and_preserves_metadata(self):
        error = JavaWorkerError("private sentinel", reply={
            "execution_state_unknown": True,
            "request_id": "worker-request-1",
            "transport_stage": "reply_read",
        })

        def unknown(_tag):
            raise error

        value = backend(False, False, unknown)
        calls = []
        with self.assertRaises(ExecutionContractError) as caught:
            value._require_external_execution_authority(
                "metric.evaluate", {}, model_tag="external", project_id="project-a")
            calls.append("target dispatched")
        self.assertEqual(calls, [])
        self.assertEqual(caught.exception.code, "EXECUTION_STATE_UNKNOWN")
        self.assertFalse(caught.exception.safe_retry)
        self.assertEqual(caught.exception.details["request_id"], "worker-request-1")
        self.assertEqual(caught.exception.details["transport_stage"], "reply_read")
        self.assertNotIn("private sentinel", json.dumps(caught.exception.as_dict()))


if __name__ == "__main__":
    unittest.main()
