"""C05 regressions: the ``hasProduct`` call marshals, and the runtime is bound.

The live G3.1 evidence is explicit:

    ``runtime.license_inspect`` — BLOCKED_LICENSE: ... probe ModelUtil.hasProduct
    refused with ENGINE_CALL_FAILED: NoSuchMethodException: no permitted public
    overload for hasProduct/1

``ModelUtil.hasProduct`` is declared ``hasProduct(java.lang.String...)``: **one**
``String[]`` parameter.  A bare scalar argument therefore cannot be converted to
the declared parameter and the Worker (correctly) refuses the overload.  These
tests pin the three things that fix implies:

* the argument is packed as a declared ``java.lang.String[]`` value;
* the five refusal causes (missing API, unmarshalled argument, unreachable
  runtime, refused seat, allow-list) are reported separately, and ``false`` is
  reported as ``false`` rather than as an error;
* both operations are bound to the *live* runtime identity and need neither a
  bound model nor a model revision.

The marshalling facts themselves are proved by ``marshalling_selftest`` **inside
the real Worker** (a local fixture, no COMSOL, no seat); those assertions skip
when the Worker cannot be built.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from comsol_mcp import _g3_ops, _g3_runtime as runtime
from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger, model_ref_from_mapping
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._managed_backend import ManagedBackend, RUNTIME_SCOPED_OPERATIONS
from comsol_mcp._operation_store import OperationStore

COMSOL_ROOT = Path(os.environ.get("COMSOL_ROOT", "/Applications/COMSOL64/Multiphysics")).expanduser()
JDK11 = Path(
    os.environ.get("COMSOL_JAVA_HOME") or os.environ.get("JAVA_HOME")
    or "/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home"
).expanduser()
JAVAC_NAME = "javac.exe" if sys.platform == "win32" else "javac"


def _worker_build_environment_available() -> bool:
    return (COMSOL_ROOT / "bin" / "comsolclientpath.txt").is_file() and (JDK11 / "bin" / JAVAC_NAME).is_file()


class _ProbeWorker:
    """A Worker double that answers ``modelutil`` and keeps every payload."""

    def __init__(self, *, products: dict[str, object] | None = None, failure: dict | None = None,
                 raise_transport: bool = False, version: str = "6.4.0.293") -> None:
        self.generation = 1
        self.products = products if products is not None else {"ACDC": True, "HeatTransfer": False}
        self.failure = failure
        self.raise_transport = raise_transport
        self.version = version
        self.payloads: list[tuple[str, dict]] = []

    def submit(self, kind, payload, **_kwargs):
        request = dict(payload)
        self.payloads.append((kind, request))
        if self.raise_transport and request.get("method") == "hasProduct":
            raise OSError("the worker endpoint did not answer")
        if self.failure is not None and request.get("method") == "hasProduct":
            return {"ok": False, "status": "FAILED", "failure": dict(self.failure)}
        if request.get("method") == "getComsolVersion":
            return {"ok": True, "status": "SUCCEEDED", "result": self.version}
        if request.get("method") == "hasProduct":
            product = _declared_product(request.get("args"))
            return {"ok": True, "status": "SUCCEEDED", "result": self.products.get(product)}
        return {"ok": True, "status": "SUCCEEDED", "result": None}

    def runtime_metadata(self):
        return {"connected": True, "generation": 1, "instance_id": "instance-abc", "pid": 4242}

    @property
    def has_product_payloads(self):
        return [payload for kind, payload in self.payloads if kind == "modelutil"
                and payload.get("method") == "hasProduct"]


def _declared_product(args):
    """Read the product from the declared ``String[]`` argument (C05 contract)."""
    assert isinstance(args, list) and len(args) == 1, args
    value = args[0]
    assert isinstance(value, dict), f"hasProduct needs a declared value, got {type(value).__name__}"
    assert value.get("java_signature") == runtime.STRING_ARRAY_SIGNATURE, value
    assert value.get("kind") == "string" and value.get("shape") == [1], value
    return value["data"][0]


# ---------------------------------------------------------------------------
# the argument shape
# ---------------------------------------------------------------------------


def test_has_product_is_packed_as_a_declared_string_array():
    value = runtime.string_array_value(["ACDC"])

    assert value == {"kind": "string", "shape": [1], "data": ["ACDC"],
                     "java_signature": "java.lang.String[]"}


def test_the_license_probe_sends_the_declared_string_array_payload():
    worker = _ProbeWorker()
    outcome = runtime._license_api_call(worker, "hasProduct", [runtime.string_array_value(["ACDC"])])

    assert outcome["ok"] is True and outcome["value"] is True
    method, payload = worker.payloads[-1]
    assert method == "modelutil"
    # This is the payload the Worker's marshaller receives: one declared
    # String[] value, never the bare scalar that hasProduct/1 cannot convert.
    assert payload == {"method": "hasProduct",
                       "args": [{"kind": "string", "shape": [1], "data": ["ACDC"],
                                 "java_signature": "java.lang.String[]"}]}


def test_every_requested_product_is_probed_with_its_own_string_array():
    worker = _ProbeWorker()
    rows, unresolved = runtime._product_rows(worker, ["ACDC", "HeatTransfer"])

    assert unresolved == []
    assert [(row["product"], row["hasProduct"]) for row in rows] == [("ACDC", True), ("HeatTransfer", False)]
    assert all(row["argument_shape"] == {"count": 1, "java_signature": "java.lang.String[]"} for row in rows)
    assert [_declared_product(payload["args"]) for payload in worker.has_product_payloads] == ["ACDC", "HeatTransfer"]


def test_a_product_without_a_license_is_false_not_an_error():
    worker = _ProbeWorker()
    data = runtime.license_inspect(worker, "mod1", {"runtime_id": "COMSOL-6.4", "products": ["HeatTransfer"]})

    assert data["products"][0]["hasProduct"] is False
    assert data["missing_products"] == ["HeatTransfer"]
    assert data["product_probe"]["status"] == "COMPLETE"
    assert data["product_probe"]["license_allows_all_requested"] is False
    assert data["product_probe"]["license_checkout_attempted"] is False


@pytest.mark.parametrize(
    "failure,kind,expected_code",
    [
        ({"code": "MODEL_UTIL_METHOD_ABSENT", "message": "hasProduct is not declared by ModelUtil"}, "api_unsupported", "BLOCKED_LICENSE"),
        ({"code": "MODEL_UTIL_ARGUMENT_CONVERSION_FAILED", "message": "arguments are not convertible to hasProduct/1"}, "marshalling_failed", "BLOCKED_LICENSE"),
        ({"code": "ENGINE_UNRESPONSIVE", "message": "not connected"}, "runtime_unavailable", "BLOCKED_LICENSE"),
        ({"code": "PERMISSION_DENIED", "message": "the license seat was refused"}, "seat_or_permission_refused", "BLOCKED_LICENSE"),
        ({"code": "ENGINE_CALL_FAILED", "message": "SecurityException: MODEL_UTIL_METHOD_REJECTED"}, "worker_allowlist", "BLOCKED_LICENSE"),
    ],
    ids=["api-absent", "conversion-failed", "not-connected", "seat-refused", "allowlist"],
)
def test_the_five_probe_causes_are_reported_separately(failure, kind, expected_code):
    worker = _ProbeWorker(failure=failure)

    with pytest.raises(ExecutionContractError) as info:
        runtime.license_inspect(worker, "mod1", {"runtime_id": "rt", "products": ["ACDC"]})

    assert info.value.code == expected_code
    assert failure["code"] in str(info.value)
    assert kind in str(info.value)


def test_a_transport_failure_keeps_its_own_code_and_is_not_a_license_answer():
    worker = _ProbeWorker(raise_transport=True)

    with pytest.raises(ExecutionContractError) as info:
        runtime.license_inspect(worker, "mod1", {"runtime_id": "rt", "products": ["ACDC"]})

    assert info.value.code == "BLOCKED_LICENSE"
    assert "engine_call_failed" in str(info.value)


def test_a_seat_consuming_method_is_never_called_by_the_probe():
    worker = _ProbeWorker()

    with pytest.raises(ExecutionContractError) as info:
        runtime._license_api_call(worker, "checkoutLicense", ["ACDC"])

    assert info.value.code == "PERMISSION_DENIED"
    assert worker.payloads == []


def test_the_facade_channel_receives_the_product_not_the_declared_value():
    class FacadeWorker(_ProbeWorker):
        def __init__(self):
            super().__init__()
            self.submit = None  # no command channel at all

        def client(self):
            return SimpleNamespace(getComsolVersion=lambda: "6.4.0.293",
                                   hasProduct=lambda product: product == "ACDC")

    data = runtime.license_inspect(FacadeWorker(), "mod1", {"runtime_id": "rt", "products": ["ACDC"]})

    assert data["products"][0]["hasProduct"] is True
    assert data["products"][0]["channel"] == "client facade"


# ---------------------------------------------------------------------------
# the runtime binding
# ---------------------------------------------------------------------------


def test_the_probe_reports_the_live_runtime_identity():
    worker = _ProbeWorker()
    data = runtime.license_inspect(worker, "mod1", {"runtime_id": "COMSOL-6.4", "products": ["ACDC"]})

    assert data["runtime_id"] == "COMSOL-6.4"  # the request label is echoed as a label
    identity = data["runtime_identity"]
    assert identity["runtime_id"] == "runtime-instance-abc"  # the runtime that answered
    assert identity["runtime_id_source"] == "PersistentJavaWorker.runtime_metadata().instance_id"
    assert identity["instance_id"] == "instance-abc"
    assert identity["revision_dependency"]["required"] is False
    assert identity["bound_model_required"] is False


def test_capabilities_reports_the_bound_runtime_id_when_the_request_omits_one():
    worker = _ProbeWorker()
    data = runtime.capabilities(worker, "mod1", {})

    assert data["runtime_id"] == "runtime-instance-abc"
    assert data["runtime_id_source"] == "PersistentJavaWorker.runtime_metadata().instance_id"
    assert data["runtime_identity"]["instance_id"] == "instance-abc"


def test_capabilities_refresh_sends_the_same_declared_argument():
    worker = _ProbeWorker()
    data = runtime.capabilities(worker, "mod1", {"runtime_id": "rt", "refresh": True})

    assert data["probes"]["ModelUtil.hasProduct"]["live_probe"]["status"] == "OK"
    assert data["probes"]["ModelUtil.hasProduct"]["live_probe"]["argument_shape"] == {
        "count": 1, "java_signature": "java.lang.String[]"}
    assert [_declared_product(payload["args"]) for payload in worker.has_product_payloads] == ["ACDC"]


def test_the_inventory_needs_no_model_and_says_so():
    worker = _ProbeWorker()
    data = runtime.license_inspect(worker, "", {"runtime_id": "rt"})

    assert data["bound_model"]["used_products"] is None
    assert data["bound_model"]["error"]["code"] == "NOT_BOUND"
    assert data["product_probe"]["status"] == "NOT_REQUESTED"


class _Adapter:
    def __init__(self) -> None:
        self.counter = 0
        self.fingerprint = "fingerprint"

    def model_snapshot(self, tag):
        return {"model_tag": tag, "server_instance_id": "server",
                "external_event_counter": self.counter, "fingerprint": self.fingerprint}


def _backend(tmp_path, worker):
    service = ExecutionService(SessionLedger("session", "server"), _Adapter(), project_root=tmp_path)
    return ManagedBackend(tmp_path / "home", OperationStore(tmp_path / "operations.sqlite3"),
                          service=service, worker=worker, registry={})


def test_an_unbound_runtime_probe_reaches_the_engine_without_a_model_or_revision(tmp_path):
    """The live refusal was ``MODEL_IDENTITY_MISMATCH: model_ref is required``."""
    worker = _ProbeWorker()
    backend = _backend(tmp_path, worker)
    operation = "runtime.license_inspect"
    assert operation in RUNTIME_SCOPED_OPERATIONS and operation in _g3_ops.IMPLEMENTED_OPERATIONS

    result = backend.invoke(operation, {"runtime_id": "COMSOL-6.4", "products": ["ACDC"]},
                            {"request_id": "c05-unbound"}, "c05-unbound", lambda *_args: None)

    assert result["success"] is True
    assert result["execution"]["model_ref"] is None
    assert result["data"]["model_binding"] == {
        "bound": False, "model_tag": None, "revision_required": False,
        "reason": "a runtime capability answer does not depend on a model revision"}
    assert result["data"]["products"][0]["hasProduct"] is True
    assert _declared_product(worker.has_product_payloads[-1]["args"]) == "ACDC"


def test_a_runtime_probe_may_not_carry_an_expected_revision(tmp_path):
    backend = _backend(tmp_path, _ProbeWorker())

    with pytest.raises(ExecutionContractError) as info:
        backend.invoke("runtime.capabilities", {"runtime_id": "rt"},
                       {"request_id": "c05-rev", "expected_revision": 3}, "c05-rev", lambda *_args: None)

    assert info.value.code == "INVALID_REQUEST"
    assert "expected_revision" in str(info.value)


def test_a_bound_request_still_works_and_reports_the_model_it_read(tmp_path):
    worker = _ProbeWorker()
    backend = _backend(tmp_path, worker)
    bound = backend.service.bind_model("mod1")
    ref = model_ref_from_mapping(bound["execution"]["model_ref"])

    result = backend.invoke("runtime.capabilities", {"runtime_id": "rt"},
                            {"request_id": "c05-bound", "model_ref": ref.as_dict()},
                            "c05-bound", lambda *_args: None)

    assert result["success"] is True
    assert result["data"]["model_binding"]["bound"] is True
    assert result["data"]["model_binding"]["model_tag"] == "mod1"
    assert result["data"]["model_binding"]["revision_required"] is False


# ---------------------------------------------------------------------------
# the real Worker's marshaller (offline: a local fixture, no COMSOL, no seat)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_worker(tmp_path_factory):
    if not _worker_build_environment_available():
        pytest.skip("COMSOL 6.4/JDK 11 local Worker build environment unavailable")
    from comsol_mcp._java_worker import JavaWorkerPaths, PersistentJavaWorker

    instance = PersistentJavaWorker(JavaWorkerPaths(COMSOL_ROOT, JDK11),
                                    state_dir=tmp_path_factory.mktemp("c05-worker-state"))
    instance.start()
    yield instance
    instance.close()


def test_the_real_marshaller_accepts_the_declared_string_array(real_worker):
    outcome = real_worker._request({"type": "marshalling_selftest"}, timeout_s=5)
    result = outcome["result"]

    assert result["typed_string_array"]["ok"] is True
    assert result["typed_string_array"]["value"] == ["ACDC"]
    assert result["nested_list"]["ok"] is True
    assert result["nested_list"]["value"] == ["ACDC", "HeatTransfer"]


def test_the_real_marshaller_refuses_a_bare_scalar_and_says_why(real_worker):
    result = real_worker._request({"type": "marshalling_selftest"}, timeout_s=5)["result"]

    # This is the recorded live failure: hasProduct/1 with a bare scalar.
    assert result["bare_string"]["ok"] is False
    assert result["bare_string"]["code"] == "MODEL_UTIL_ARGUMENT_CONVERSION_FAILED"
    assert "not convertible" in result["bare_string"]["message"]


def test_the_real_marshaller_separates_a_missing_api_from_a_bad_argument(real_worker):
    result = real_worker._request({"type": "marshalling_selftest"}, timeout_s=5)["result"]

    assert result["signature_mismatch"]["ok"] is False
    assert result["signature_mismatch"]["code"] == "MODEL_UTIL_ARGUMENT_CONVERSION_FAILED"
    assert result["undeclared_method"]["ok"] is False
    assert result["undeclared_method"]["code"] == "MODEL_UTIL_METHOD_ABSENT"
    assert "is not declared by" in result["undeclared_method"]["message"]
    assert result["undeclared_arity"]["ok"] is False
    assert result["undeclared_arity"]["code"] == "MODEL_UTIL_METHOD_ABSENT"


def test_the_allow_list_still_gates_has_product(real_worker):
    """The marshalling fix must not open the allow-list: it only fixes the call."""
    assert runtime.WORKER_MODELUTIL_ALLOWLIST and "hasProduct" in runtime.WORKER_MODELUTIL_ALLOWLIST
    rejected = real_worker.submit("modelutil", {"method": "checkoutLicense", "args": [["ACDC"]]},
                                  request_id="c05-allowlist", rpc_timeout_s=5)
    assert rejected["status"] == "FAILED"
    assert "MODEL_UTIL_METHOD_REJECTED" in rejected["failure"]["message"]
