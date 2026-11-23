from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir
import unittest

from comsol_mcp._control_daemon import ControlDaemon


INVALID_ARGUMENTS = [
    ("null-solution", {"solution": None}),
    ("empty-solution", {"solution": {}}),
    ("null-dataset", {"solution": {"dataset": None}}),
    ("empty-dataset", {"solution": {"dataset": ""}}),
    ("whitespace-dataset", {"solution": {"dataset": "   "}}),
    ("empty-bound-solution", {"solution": {"dataset": "d_new", "solution": ""}}),
    ("unknown-solution-field", {"solution": {"dataset": "d_new", "inner": "first"}}),
    ("conflicting-options-dataset", {
        "solution": {"dataset": "d_new"}, "options": {"dataset": "d_old"},
    }),
    ("conflicting-options-data-alias", {
        "solution": {"dataset": "d_new"}, "options": {"dataset": "d_new", "data": "d_old"},
    }),
    ("unmapped-options-solution", {
        "solution": {"dataset": "d_new"}, "options": {"solution": "sol_new"},
    }),
]


def _daemon(home: Path, permissions: list[str]) -> tuple[ControlDaemon, str]:
    project_root = home / "projects"
    project_root.mkdir()
    daemon = ControlDaemon(home / "control", project_root=project_root, registry={})
    created = daemon.dispatch({
        "operation": "project.create",
        "arguments": {
            "label": "synthetic-plot-project",
            "workspace": "synthetic-plot-project",
            "policy": {"permissions": permissions},
        },
        "execution": {"request_id": "create-project", "idempotency_key": "create-project"},
    })
    if created["success"] is not True:
        daemon.close()
        raise AssertionError(created)
    return daemon, created["data"]["project"]["project_id"]


class T29AdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_root = Path(gettempdir()).resolve()
        self.temp = TemporaryDirectory(dir=self.temp_root)
        self.addCleanup(self.temp.cleanup)
        self.daemon, self.project_id = _daemon(Path(self.temp.name), ["inspect", "project_write"])
        self.addCleanup(self.daemon.close)

    def test_invalid_nested_plot_bindings_never_create_jobs_for_either_alias(self) -> None:
        calls = []

        def fake_execute(*args, **kwargs):
            calls.append((args, kwargs))
            self.fail("invalid plot input reached durable execution")

        self.daemon._execute = fake_execute
        before = self.daemon.store.list_jobs().total
        for operation in ("plot.render", "plot_render"):
            for case, invalid in INVALID_ARGUMENTS:
                with self.subTest(operation=operation, case=case):
                    request_id = f"invalid-{operation}-{case}"
                    result = self.daemon.dispatch({
                        "operation": operation,
                        "arguments": {"path": "pg_synthetic", **invalid},
                        "execution": {
                            "project_id": self.project_id,
                            "request_id": request_id,
                            "idempotency_key": request_id,
                        },
                    })
                    self.assertIs(result["success"], False)
                    self.assertEqual(result["error"]["code"], "INVALID_REQUEST")
                    self.assertEqual(self.daemon.store.list_jobs().total, before)
        self.assertEqual(calls, [])

    def test_valid_legacy_and_explicit_forms_reach_fake_backend_with_outer_identity(self) -> None:
        dispatched = []

        def fake_execute(record, operation, arguments, execution, _timeouts, _submitted, *, resume_context=None):
            dispatched.append((operation, dict(arguments), dict(execution), record["operation_id"]))
            self.assertIsNone(resume_context)
            return self.daemon._finish(
                record, {"success": True, "data": {"synthetic_rendered": True}}, "SUCCEEDED",
            )

        self.daemon._execute = fake_execute
        valid_arguments = [
            {"path": "pg_synthetic"},
            {"path": "pg_synthetic", "options": {}},
            {"path": "pg_synthetic", "solution": {"dataset": "d_synthetic"}},
        ]
        before = self.daemon.store.list_jobs().total
        index = 0
        for operation in ("plot.render", "plot_render"):
            for arguments in valid_arguments:
                with self.subTest(operation=operation, arguments=arguments):
                    request_id = f"valid-{index}"
                    index += 1
                    result = self.daemon.dispatch({
                        "operation": operation,
                        "arguments": arguments,
                        "execution": {
                            "project_id": self.project_id,
                            "request_id": request_id,
                            "idempotency_key": request_id,
                        },
                    })
                    self.assertIs(result["success"], True, result)

        self.assertEqual(self.daemon.store.list_jobs().total, before + len(valid_arguments) * 2)
        self.assertEqual(len(dispatched), len(valid_arguments) * 2)
        for operation, arguments, execution, _operation_id in dispatched:
            self.assertEqual(operation, "plot.render")
            self.assertIn(arguments, valid_arguments)
            self.assertEqual(execution["project_id"], self.project_id)
            self.assertEqual(execution["request_id"], execution["idempotency_key"])

    def test_plot_write_authorization_still_uses_outer_execution_project(self) -> None:
        self.daemon.close()
        inspect_root = Path(self.temp.name) / "inspect-only"
        inspect_root.mkdir()
        daemon, project_id = _daemon(inspect_root, ["inspect"])
        dispatched = []
        daemon.backend.invoke = lambda *args, **kwargs: dispatched.append(args)
        self.addCleanup(daemon.close)
        before = daemon.store.list_jobs().total

        result = daemon.dispatch({
            "operation": "plot.render",
            "arguments": {"path": "pg_synthetic"},
            "execution": {
                "project_id": project_id,
                "request_id": "inspect-only-plot",
                "idempotency_key": "inspect-only-plot",
            },
        })

        self.assertIs(result["success"], False)
        self.assertEqual(result["error"]["code"], "PERMISSION_DENIED")
        self.assertEqual(daemon.store.list_jobs().total, before)
        self.assertEqual(dispatched, [])


if __name__ == "__main__":
    unittest.main()
