"""G3 runtime plane: ``runtime.license_inspect`` and ``runtime.capabilities``.

Every test drives the real operation code against a fake persistent Worker.

The fake mirrors the *verified* controlled channel of
``comsol_mcp/worker_java/PersistentComsolWorker.java``: the ``modelutil``
command with its own method allow-list, a bound-model handle reachable through
``client().model(tag)``, and a reply envelope shaped like the Worker's
(``{"ok": bool, "result"|"failure": ...}``).  Two build states are exercised:

* the **current** build, where ``MODEL_UTIL`` does not contain ``hasProduct``
  and the Worker answers ``SecurityException: MODEL_UTIL_METHOD_REJECTED``
  inside a generic ``ENGINE_CALL_FAILED`` failure;
* a **future** build where ``hasProduct`` is allow-listed and returns real
  booleans.

The seat-consuming ``checkoutLicense`` family is recorded by the fake and
asserted never to be called: T042 requires a probe that does not occupy a
license seat.  ``TestT042Alignment`` derives its expectations from
``tools/phase4_run_mcp.py`` itself (the driver's ``_BLOCKED_CODES`` and its
seat-key probe list) so the operation stays aligned with the acceptance driver
instead of with a copy of it.
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping

import pytest

from comsol_mcp._execution_contract import ExecutionContractError

from comsol_mcp import _g3_runtime as runtime
from comsol_mcp._g2_registry import is_implemented
from comsol_mcp._g2_tools import operation_describe
from comsol_mcp._g3_ops import DISPATCH, EFFECTS, IMPLEMENTED_OPERATIONS, OPERATION_ORIGINS, REQUIRES_ISOLATION

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "tools" / "phase4_run_mcp.py"

#: The locally installed JDK 11 unarchiver and the public COMSOL API jar every
#: cited signature in ``_g3_runtime`` was read from.
JAVAP = Path("/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home/bin/javap")
API_JAR = Path("/Applications/COMSOL64/Multiphysics/apiplugins/com.comsol.api_1.0.0.jar")


def _javap(qualified_class: str) -> set[str]:
    """Public members of one installed COMSOL API class, as javap prints them."""
    completed = subprocess.run(
        [str(JAVAP), "-cp", str(API_JAR), qualified_class],
        capture_output=True, text=True, check=True,
    )
    return {
        line.strip().rstrip(";")
        for line in completed.stdout.splitlines()
        if line.strip().startswith("public")
    }

#: The names the T042 driver probes for at the top level of the payload; their
#: presence would be read as "the probe checked out a seat".
DRIVER_SEAT_KEYS = ("checkout", "seats", "seat_count", "checkouts")


# ---------------------------------------------------------------------------
# fake Worker
# ---------------------------------------------------------------------------


class FakeWorkerError(RuntimeError):
    """Transport-level Worker failure; ``reply``/``failure`` feed ``_worker_failure_code``."""

    def __init__(self, message: str, *, reply: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.reply = dict(reply or {})
        failure = self.reply.get("failure")
        self.failure = dict(failure) if isinstance(failure, Mapping) else {}


class FakeModel:
    """Stand-in for a bound ``com.comsol.model.Model`` handle."""

    def __init__(self, *, used_products: Any = ("ACDC",), error: str | None = None,
                 type_id: str = "Model") -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.used_products = used_products
        self.error = error
        self.type_id = type_id

    def getUsedProducts(self) -> Any:
        self.calls.append(("getUsedProducts", ()))
        if self.error is not None:
            raise FakeWorkerError(self.error, reply={"failure": {"code": self.error}})
        if isinstance(self.used_products, Exception):
            raise self.used_products
        return self.used_products


class FakeClient:
    """The MPh-shaped facade; ``model(tag)`` is the only door to a model handle."""

    def __init__(self, models: Mapping[str, FakeModel]) -> None:
        self.models = dict(models)

    def model(self, tag: str) -> FakeModel:
        if tag not in self.models:
            raise KeyError(tag)
        return self.models[tag]


class FakeWorker:
    """Fake persistent Worker owning the ``modelutil`` allow-list."""

    def __init__(self, *, allowlist: tuple[str, ...] = ("getComsolVersion",),
                 version: Any = "6.4.0.293", has_product: Any = None,
                 models: Mapping[str, FakeModel] | None = None, model_tag: str = "mod1",
                 generation: int = 11, transport_failure: tuple[str, ...] = (),
                 metadata: Any = None, submit: bool = True) -> None:
        self.allowlist = set(allowlist)
        self.version = version
        self.has_product = has_product
        self.models = dict(models or {})
        self.model_tag = model_tag
        self.generation = generation
        self.transport_failure = set(transport_failure)
        self.metadata = metadata
        self.calls: list[tuple[str, dict[str, Any]]] = []
        if not submit:
            self.submit = None  # type: ignore[assignment]

    # -- controlled channel -------------------------------------------------
    def submit(self, kind: str, payload: Mapping[str, Any], **_: Any) -> dict[str, Any]:
        request = dict(payload)
        self.calls.append((kind, request))
        if kind != "modelutil":
            return {"ok": False, "status": "FAILED",
                    "failure": {"code": "ENGINE_CALL_FAILED", "message": f"unexpected command {kind}"}}
        method = str(request.get("method"))
        args = list(request.get("args") or [])
        if method in runtime.SEAT_CONSUMING_METHODS:
            return {"ok": False, "status": "FAILED",
                    "failure": {"code": "PERMISSION_DENIED",
                                "message": f"{method} must never be called by a license probe"}}
        if method in self.transport_failure:
            raise FakeWorkerError(f"worker transport failure for {method}",
                                  reply={"failure": {"code": "ENGINE_UNRESPONSIVE"}})
        if method not in self.allowlist:
            # The Java Worker's own refusal shape: SecurityException inside a
            # generic ENGINE_CALL_FAILED failure.
            return {"ok": False, "status": "FAILED",
                    "failure": {"code": "ENGINE_CALL_FAILED",
                                "message": f"SecurityException: MODEL_UTIL_METHOD_REJECTED",
                                "execution_state_unknown": True}}
        if method == "getComsolVersion":
            return {"ok": True, "status": "SUCCEEDED", "result": self.version}
        if method == "hasProduct":
            return {"ok": True, "status": "SUCCEEDED", "result": self._has_product(args[0] if args else "")}
        return {"ok": True, "status": "SUCCEEDED", "result": None}

    def _has_product(self, product: str) -> Any:
        if callable(self.has_product):
            return self.has_product(product)
        if isinstance(self.has_product, Mapping):
            return self.has_product.get(product)
        return self.has_product

    def client(self) -> FakeClient:
        return FakeClient(self.models)

    def runtime_metadata(self) -> Any:
        if self.metadata is None:
            raise RuntimeError("no metadata")
        if isinstance(self.metadata, Exception):
            raise self.metadata
        return self.metadata

    # -- assertions helpers -------------------------------------------------
    @property
    def called_methods(self) -> list[str]:
        return [str(payload.get("method")) for kind, payload in self.calls if kind == "modelutil"]


def model_for(*, used_products: Any = ("ACDC",), error: str | None = None) -> FakeModel:
    return FakeModel(used_products=used_products, error=error)


def worker_for(model: FakeModel | None = None, *, model_tag: str = "mod1", **kwargs: Any) -> FakeWorker:
    if "models" in kwargs:
        return FakeWorker(model_tag=model_tag, **kwargs)
    models = {model_tag: model} if isinstance(model, FakeModel) else {}
    return FakeWorker(models=models, model_tag=model_tag, **kwargs)


def allowlisted_worker(*, has_product: Any = True, model: FakeModel | None = None, **kwargs: Any) -> FakeWorker:
    """A build in which the Worker allow-lists the license probe method."""
    return worker_for(model if model is not None else model_for(),
                      allowlist=("getComsolVersion", "hasProduct"), has_product=has_product, **kwargs)


def expect_error(code: str, function: Any, *args: Any) -> ExecutionContractError:
    with pytest.raises(ExecutionContractError) as info:
        function(*args)
    assert info.value.code == code, f"expected {code}, got {info.value.code}: {info.value}"
    return info.value


def inspect(worker: FakeWorker, *, runtime_id: str = "macos-arm64/comsol6.4",
            products: Any = ("ACDC",), **extra: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {"runtime_id": runtime_id}
    if products is not None:
        arguments["products"] = list(products) if isinstance(products, (list, tuple)) else products
    arguments.update(extra)
    return runtime.license_inspect(worker, worker.model_tag, arguments)


# ---------------------------------------------------------------------------
# publishing surface
# ---------------------------------------------------------------------------


class TestPublishing:
    def test_both_operations_are_published(self):
        assert set(runtime.OPERATIONS) == {"runtime.license_inspect", "runtime.capabilities"}
        for operation_id, function in runtime.OPERATIONS.items():
            assert callable(function), operation_id
            assert DISPATCH[operation_id] is function
            assert operation_id in IMPLEMENTED_OPERATIONS
            assert OPERATION_ORIGINS[operation_id] == "_g3_runtime"
            assert is_implemented(operation_id)

    def test_effects_are_catalogue_reads_and_need_no_isolation(self):
        assert EFFECTS["runtime.license_inspect"] == "READ"
        assert EFFECTS["runtime.capabilities"] == "READ"
        assert "runtime.license_inspect" not in REQUIRES_ISOLATION
        assert "runtime.capabilities" not in REQUIRES_ISOLATION

    @pytest.mark.parametrize("operation_id, required, optional", [
        ("runtime.license_inspect", ["runtime_id"], {"runtime_id", "products"}),
        ("runtime.capabilities", [], {"runtime_id", "refresh"}),
    ])
    def test_published_schema_matches_the_enforced_fields(self, operation_id, required, optional):
        described = operation_describe(operation_id)["data"]
        assert described["executable"] is True
        assert described["implementation_status"] == "SUPPORTED_UNVERIFIED"
        schema = described["input_schema"]
        assert schema["type"] == "object"
        assert sorted(schema["required"]) == required
        assert set(schema["properties"]) == optional | {"request_id"}

    def test_describe_reports_the_probe_limits_as_evidence_not_as_failure(self):
        worker = allowlisted_worker(has_product={"ACDC": True})
        data = runtime.capabilities(worker, worker.model_tag, {"refresh": True})
        assert data["engine"]["comsol_version"] == "6.4.0.293"
        assert data["probes"]["ModelUtil.hasProduct"]["live_probe"]["status"] == "OK"


# ---------------------------------------------------------------------------
# runtime.license_inspect - happy path
# ---------------------------------------------------------------------------


class TestLicenseInspectProbe:
    def test_records_has_product_for_every_requested_product(self):
        worker = allowlisted_worker(has_product={"ACDC": True, "HEATTRANSFER": True})
        data = inspect(worker, products=["ACDC", "HEATTRANSFER"])
        assert data["runtime_id"] == "macos-arm64/comsol6.4"
        assert [row["product"] for row in data["products"]] == ["ACDC", "HEATTRANSFER"]
        assert [row["hasProduct"] for row in data["products"]] == [True, True]
        assert data["missing_products"] == []
        assert data["product_probe"]["status"] == "COMPLETE"
        assert data["product_probe"]["checked_count"] == 2
        assert data["product_probe"]["unresolved_count"] == 0
        assert data["product_probe"]["license_allows_all_requested"] is True

    def test_rows_carry_the_probe_source_and_are_json_serialisable(self):
        worker = allowlisted_worker(has_product=True)
        data = inspect(worker)
        row = data["products"][0]
        assert row["source"] == "ModelUtil.hasProduct(String)"
        assert row["channel"] == "modelutil command"
        assert row["documented_feature_string"] is True
        assert row["product_label"] == "AC/DC Module"
        assert json.loads(json.dumps(data))["products"] == data["products"]

    def test_a_missing_product_is_reported_not_faked(self):
        """The driver's ``_missing_products`` must be able to see the refusal."""
        worker = allowlisted_worker(has_product={"ACDC": True, "HEATTRANSFER": False})
        data = inspect(worker, products=["ACDC", "HEATTRANSFER"])
        rows = {row["product"]: row["hasProduct"] for row in data["products"]}
        assert rows == {"ACDC": True, "HEATTRANSFER": False}
        assert data["missing_products"] == ["HEATTRANSFER"]
        assert data["product_probe"]["license_allows_all_requested"] is False

    def test_undocumented_identifier_is_accepted_and_flagged(self):
        worker = allowlisted_worker(has_product=True)
        data = inspect(worker, products=["HeatTransfer"])
        row = data["products"][0]
        assert row["hasProduct"] is True
        assert row["documented_feature_string"] is False
        assert row["product_label"] is None
        assert any("License Feature Strings" in note for note in data["unverified"])

    def test_engine_identity_and_inventory_are_reported(self):
        worker = allowlisted_worker(has_product=True)
        data = inspect(worker)
        assert data["engine"] == {"comsol_version": "6.4.0.293",
                                  "source": "ModelUtil.getComsolVersion()",
                                  "channel": "modelutil command",
                                  "worker_generation": 11}
        assert data["bound_model"]["used_products"] == ["ACDC"]
        assert data["bound_model"]["provenance"] == "inventory only; never a license decision"

    def test_inventory_failure_does_not_break_the_license_probe(self):
        worker = allowlisted_worker(has_product=True)
        worker.models = {}
        data = inspect(worker)
        assert data["products"][0]["hasProduct"] is True
        assert data["bound_model"]["used_products"] is None
        assert data["bound_model"]["error"]["code"] == "NODE_NOT_FOUND"

    def test_inventory_readback_that_is_not_a_string_list_is_reported(self):
        worker = allowlisted_worker(has_product=True)
        worker.models = {"mod1": model_for(used_products=[1, 2])}
        data = inspect(worker)
        assert data["bound_model"]["used_products"] is None
        assert data["bound_model"]["error"]["code"] == "EXECUTION_STATE_UNKNOWN"

    def test_inventory_method_refused_by_the_allowlist_is_reported(self):
        class NoInventory(FakeModel):
            def getUsedProducts(self) -> Any:
                raise FakeWorkerError("METHOD_REJECTED",
                                      reply={"failure": {"code": "METHOD_REJECTED"}})

        worker = allowlisted_worker(has_product=True)
        worker.models = {"mod1": NoInventory()}
        data = inspect(worker)
        assert data["bound_model"]["used_products"] is None
        assert data["bound_model"]["error"]["code"] == "METHOD_REJECTED"
        assert data["bound_model"]["error"]["allowlist_entry_required"] == "getUsedProducts"

    def test_no_products_is_an_inventory_read(self):
        worker = allowlisted_worker(has_product=True)
        data = inspect(worker, products=None)
        assert data["products"] == []
        assert data["missing_products"] == []
        assert data["product_probe"]["status"] == "NOT_REQUESTED"
        assert data["product_probe"]["checked_count"] == 0
        assert worker.called_methods == ["getComsolVersion"]

    def test_an_explicit_empty_product_list_behaves_like_an_omitted_one(self):
        worker = allowlisted_worker(has_product=True)
        data = inspect(worker, products=[])
        assert data["product_probe"]["status"] == "NOT_REQUESTED"
        assert worker.called_methods == ["getComsolVersion"]


# ---------------------------------------------------------------------------
# runtime.license_inspect - blocked / unresolvable
# ---------------------------------------------------------------------------


class TestLicenseInspectBlocked:
    def test_unallowlisted_probe_blocks_instead_of_faking_a_result(self):
        worker = worker_for(model_for())  # current build: hasProduct is not allow-listed
        error = expect_error("BLOCKED_LICENSE", inspect, worker)
        message = str(error)
        assert "hasProduct" in message
        assert "MODEL_UTIL_METHOD_REJECTED" in message
        assert "unresolved product" in message
        assert worker.called_methods == ["getComsolVersion", "hasProduct"]
        assert "getUsedProducts" not in worker.called_methods

    def test_a_partially_resolved_probe_is_refused_as_a_whole(self):
        """One unresolved product must not be dropped from a license answer."""

        class SelectiveWorker(FakeWorker):
            def _has_product(self, product: str) -> Any:
                return True if product == "ACDC" else None  # None: not a boolean

        worker = SelectiveWorker(allowlist=("getComsolVersion", "hasProduct"),
                                models={"mod1": model_for()})
        error = expect_error("BLOCKED_LICENSE", runtime.license_inspect, worker, "mod1",
                             {"runtime_id": "rt", "products": ["ACDC", "HEATTRANSFER"]})
        assert "1 of 2" in str(error)
        assert "HEATTRANSFER" in str(error)
        assert "did not return a boolean" in str(error)

    def test_non_boolean_probe_result_is_refused(self):
        worker = allowlisted_worker(has_product=None)
        error = expect_error("BLOCKED_LICENSE", inspect, worker)
        assert "did not return a boolean" in str(error)

    def test_engine_channel_failure_keeps_the_worker_code(self):
        worker = worker_for(model_for(), transport_failure=("getComsolVersion",))
        error = expect_error("ENGINE_UNRESPONSIVE", inspect, worker)
        assert "ENGINE_UNRESPONSIVE" in str(error)

    def test_non_string_engine_version_is_execution_state_unknown(self):
        worker = worker_for(model_for(), version=6.4)
        expect_error("EXECUTION_STATE_UNKNOWN", inspect, worker)

    def test_a_worker_without_a_command_channel_is_reported(self):
        worker = FakeWorker(submit=False, models={"mod1": model_for()})
        worker.submit = None  # type: ignore[assignment]
        error = expect_error("ENGINE_UNRESPONSIVE", inspect, worker)
        assert "command channel" in str(error) or "client facade" in str(error)

    def test_a_client_facade_probe_is_used_when_the_worker_has_no_command_channel(self):
        class FacadeClient(FakeClient):
            def getComsolVersion(self) -> str:
                return "6.4.0.293"

            def hasProduct(self, product: str) -> bool:
                return product == "ACDC"

        class FacadeWorker(FakeWorker):
            def client(self) -> FacadeClient:
                return FacadeClient(self.models)

        worker = FacadeWorker(models={"mod1": model_for()})
        worker.submit = None  # type: ignore[assignment]
        data = runtime.license_inspect(worker, "mod1", {"runtime_id": "rt", "products": ["ACDC"]})
        assert data["products"][0]["hasProduct"] is True
        assert data["products"][0]["channel"] == "client facade"


# ---------------------------------------------------------------------------
# runtime.license_inspect - argument validation
# ---------------------------------------------------------------------------


class TestLicenseInspectValidation:
    def test_runtime_id_is_required(self):
        worker = allowlisted_worker(has_product=True)
        expect_error("INVALID_REQUEST", runtime.license_inspect, worker, "mod1", {"products": ["ACDC"]})

    @pytest.mark.parametrize("value", ["", "   ", None, 7, ["rt"]])
    def test_runtime_id_must_be_a_non_empty_string(self, value):
        worker = allowlisted_worker(has_product=True)
        expect_error("INVALID_REQUEST", runtime.license_inspect, worker, "mod1", {"runtime_id": value})

    def test_unknown_argument_is_refused(self):
        worker = allowlisted_worker(has_product=True)
        expect_error("INVALID_REQUEST", runtime.license_inspect, worker, "mod1",
                     {"runtime_id": "rt", "checkout": True})
        assert worker.calls == []

    def test_arguments_must_be_an_object(self):
        worker = allowlisted_worker(has_product=True)
        expect_error("INVALID_REQUEST", runtime.license_inspect, worker, "mod1", "ACDC")

    @pytest.mark.parametrize("products", ["ACDC", 5, {"ACDC": True}, [1], ["AC DC"], ["AC/DC"], [""]])
    def test_products_must_be_an_array_of_tokens(self, products):
        worker = allowlisted_worker(has_product=True)
        expect_error("INVALID_REQUEST", runtime.license_inspect, worker, "mod1",
                     {"runtime_id": "rt", "products": products})
        assert worker.calls == []

    def test_duplicate_products_are_refused(self):
        worker = allowlisted_worker(has_product=True)
        expect_error("INVALID_REQUEST", runtime.license_inspect, worker, "mod1",
                     {"runtime_id": "rt", "products": ["ACDC", "ACDC"]})

    def test_too_many_products_are_refused(self):
        worker = allowlisted_worker(has_product=True)
        products = [f"P{index}" for index in range(runtime.MAX_PRODUCTS + 1)]
        expect_error("INVALID_REQUEST", runtime.license_inspect, worker, "mod1",
                     {"runtime_id": "rt", "products": products})
        assert worker.calls == []

    def test_envelope_fields_pass_through(self):
        worker = allowlisted_worker(has_product=True)
        data = runtime.license_inspect(worker, "mod1", {
            "runtime_id": "rt", "products": ["ACDC"], "project_id": "phase4", "session_id": "s1",
            "model_ref": {"model_tag": "mod1"}, "expected_revision": 3,
            "idempotency_key": "k1", "request_id": "r1", "correlation_id": "c1", "trace_id": "t1",
        })
        assert data["products"][0]["hasProduct"] is True

    def test_the_probe_never_calls_a_seat_consuming_method(self):
        worker = allowlisted_worker(has_product=True)
        runtime.license_inspect(worker, "mod1", {"runtime_id": "rt", "products": ["ACDC"]})
        assert runtime.SEAT_CONSUMING_METHODS.isdisjoint(set(worker.called_methods))
        with pytest.raises(ExecutionContractError) as info:
            runtime._license_api_call(worker, "checkoutLicense", ["ACDC"])
        assert info.value.code == "PERMISSION_DENIED"
        assert "checkoutLicense" not in worker.called_methods

    def test_the_attempt_log_makes_the_seat_refusal_visible(self):
        """A seat method is refused before dispatch and still recorded as attempted."""
        log = runtime._CallLog()
        worker = allowlisted_worker(has_product=True)
        with pytest.raises(ExecutionContractError) as info:
            runtime._license_api_call(worker, "checkoutLicense", ["ACDC"], log=log)
        assert info.value.code == "PERMISSION_DENIED"
        assert worker.calls == [], "the Worker must never see a checkout call"
        assert log.attempted == ["checkoutLicense"]
        assert log.seat_consuming == ["checkoutLicense"]
        assert runtime._CallLog().seat_consuming == []


# ---------------------------------------------------------------------------
# runtime.license_inspect - payload shape the T042 driver inspects
# ---------------------------------------------------------------------------


class TestLicenseInspectPayloadShape:
    def test_no_top_level_seat_key_is_reported(self):
        worker = allowlisted_worker(has_product={"ACDC": True, "HEATTRANSFER": False})
        data = inspect(worker, products=["ACDC", "HEATTRANSFER"])
        for key in DRIVER_SEAT_KEYS:
            assert key not in data, key
        assert data["product_probe"]["license_checkout_attempted"] is False
        assert data["product_probe"]["consumes_license_seat"] is False
        assert data["product_probe"]["seat_consuming_methods_attempted"] == []
        assert data["product_probe"]["seat_consuming_method_family"] == "ModelUtil.checkoutLicense*"
        assert data["product_probe"]["methods_attempted"] == [
            "getComsolVersion", "getUsedProducts", "hasProduct",
        ]

    def test_payload_does_not_echo_a_license_endpoint_configuration(self):
        worker = allowlisted_worker(has_product=True)
        text = json.dumps(inspect(worker))
        assert "=" not in text
        assert "server" not in text.lower()

    def test_rows_keep_the_requested_identifier_verbatim(self):
        worker = allowlisted_worker(has_product=True)
        data = inspect(worker, products=["acdc", "HEATTRANSFER"])
        assert [row["product"] for row in data["products"]] == ["acdc", "HEATTRANSFER"]

    def test_every_row_exposes_has_product(self):
        worker = allowlisted_worker(has_product=False)
        data = inspect(worker, products=["ACDC"])
        assert all("hasProduct" in row for row in data["products"])


# ---------------------------------------------------------------------------
# runtime.capabilities
# ---------------------------------------------------------------------------


class TestCapabilities:
    def test_reports_status_limits_and_evidence_without_refresh(self):
        worker = worker_for(model_for())
        data = runtime.capabilities(worker, "mod1", {"runtime_id": "rt"})
        assert data["runtime_id"] == "rt"
        assert data["refresh_requested"] is False
        assert data["cache"] == {"used": False, "reason": "every field below is read live from the Worker"}
        assert data["engine"]["comsol_version"] == "6.4.0.293"
        assert data["bound_model"]["used_products"] == ["ACDC"]
        assert worker.called_methods == ["getComsolVersion"]

    def test_declares_the_probe_allowlist_state_without_inventing_limits(self):
        worker = worker_for(model_for())
        data = runtime.capabilities(worker, "mod1", {})
        row = data["probes"]["ModelUtil.hasProduct"]
        assert row["worker_allowlist_present"] is True
        assert row["worker_allowlist_entry"] == "hasProduct"
        assert row["consumes_license_seat"] is False
        assert row["live_probe"] == {"status": "NOT_RUN", "reason": "refresh=false"}
        assert row["javap_signature"] == "public static boolean hasProduct(java.lang.String...)"
        assert data["worker_allowlist_requests"] == []
        assert {"api": "ModelUtil.hasProduct", "state": "REFUSED", "reason": None,
                "allowlist_entry_required": None} not in data["limits"]

    def test_refresh_attempts_the_probe_and_records_the_observed_refusal(self):
        worker = worker_for(model_for())
        data = runtime.capabilities(worker, "mod1", {"refresh": True})
        row = data["probes"]["ModelUtil.hasProduct"]
        assert row["live_probe"]["status"] == "REFUSED"
        assert row["live_probe"]["reason"] == "ENGINE_CALL_FAILED"
        assert row["live_probe"]["allowlist_entry_required"] == "hasProduct"
        assert row["live_probe"]["probe_product"] == "ACDC"
        assert worker.called_methods == ["getComsolVersion", "hasProduct"]
        assert {"api": "ModelUtil.hasProduct", "state": "REFUSED", "reason": "ENGINE_CALL_FAILED",
                "allowlist_entry_required": "hasProduct"} in data["limits"]
        # This fake simulates an older Worker without the entry; the fresh
        # build carries it, so nothing is pending in the requests list.
        assert data["worker_allowlist_requests"] == []

    def test_refresh_records_a_real_probe_value_when_the_method_is_allowlisted(self):
        worker = allowlisted_worker(has_product=True)
        data = runtime.capabilities(worker, "mod1", {"refresh": True})
        row = data["probes"]["ModelUtil.hasProduct"]
        assert row["live_probe"]["status"] == "OK"
        assert row["live_probe"]["value"] is True
        assert row["live_probe"]["probe_product"] == "ACDC"
        assert all(item["api"] != "ModelUtil.hasProduct" for item in data["limits"])

    def test_the_seat_consuming_api_is_reported_and_never_called(self):
        worker = allowlisted_worker(has_product=True)
        data = runtime.capabilities(worker, "mod1", {"refresh": True})
        row = data["probes"]["ModelUtil.checkoutLicense"]
        assert row["consumes_license_seat"] is True
        assert row["live_probe"]["status"] == "NOT_CALLED_BY_DESIGN"
        assert runtime.SEAT_CONSUMING_METHODS.isdisjoint(set(worker.called_methods))
        assert data["seat_consuming_methods_attempted"] == []
        assert data["seat_consuming_method_family"] == "ModelUtil.checkoutLicense*"
        assert data["methods_attempted"] == ["getComsolVersion", "getUsedProducts", "hasProduct"]

    def test_a_documented_but_absent_api_is_reported_as_absent(self):
        worker = allowlisted_worker(has_product=True)
        data = runtime.capabilities(worker, "mod1", {})
        row = data["probes"]["ModelUtil.getLicenseNumber"]
        assert row["javap_signature"] is None
        assert "NOT declared" in row["verified_by"]
        assert row["live_probe"]["status"] == "NOT_CALLED_BY_DESIGN"

    def test_worker_identity_is_reported_when_available_and_omitted_safely_otherwise(self):
        worker = allowlisted_worker(has_product=True)
        worker.metadata = {"connected": True, "generation": 12, "instance_id": "inst-1", "pid": 42,
                           "host": "127.0.0.1", "port": 2036}
        data = runtime.capabilities(worker, "mod1", {})
        assert data["worker"] == {"available": True, "connected": True, "generation": 12,
                                  "instance_id": "inst-1",
                                  "source": "PersistentJavaWorker.runtime_metadata()", "error": None}
        assert "port" not in data["worker"]
        plain = allowlisted_worker(has_product=True)
        assert runtime.capabilities(plain, "mod1", {})["worker"]["available"] is False

    def test_capabilities_survives_an_unresolvable_model(self):
        worker = worker_for(None)
        data = runtime.capabilities(worker, "mod1", {})
        assert data["bound_model"]["used_products"] is None
        assert data["probes"]["Model.getUsedProducts"]["live_probe"]["status"] == "UNAVAILABLE"
        assert {"api": "Model.getUsedProducts", "state": "UNAVAILABLE", "reason": "NODE_NOT_FOUND",
                "allowlist_entry_required": None} in data["limits"]

    def test_engine_channel_failure_still_raises(self):
        worker = worker_for(model_for(), transport_failure=("getComsolVersion",))
        expect_error("ENGINE_UNRESPONSIVE", runtime.capabilities, worker, "mod1", {})

    @pytest.mark.parametrize("refresh", ["yes", 1, 0, "true"])
    def test_refresh_must_be_a_boolean(self, refresh):
        worker = allowlisted_worker(has_product=True)
        expect_error("INVALID_REQUEST", runtime.capabilities, worker, "mod1", {"refresh": refresh})

    def test_unknown_argument_is_refused(self):
        worker = allowlisted_worker(has_product=True)
        expect_error("INVALID_REQUEST", runtime.capabilities, worker, "mod1", {"products": ["ACDC"]})

    def test_runtime_id_is_optional_but_validated(self):
        worker = allowlisted_worker(has_product=True)
        assert runtime.capabilities(worker, "mod1", {})["runtime_id"] is None
        expect_error("INVALID_REQUEST", runtime.capabilities, worker, "mod1", {"runtime_id": ""})

    def test_envelope_fields_pass_through(self):
        worker = allowlisted_worker(has_product=True)
        data = runtime.capabilities(worker, "mod1", {"project_id": "phase4", "session_id": "s1",
                                                    "expected_revision": 2, "idempotency_key": "k",
                                                    "request_id": "r"})
        assert data["runtime_id"] is None

    def test_limits_never_contain_a_design_statement_for_a_read_only_api(self):
        worker = allowlisted_worker(has_product=True)
        data = runtime.capabilities(worker, "mod1", {"refresh": True})
        states = {(item["api"], item["state"]) for item in data["limits"]}
        assert ("ModelUtil.getComsolVersion", "REFUSED") not in states
        assert ("ModelUtil.checkoutLicense", "NOT_CALLED_BY_DESIGN") in states


# ---------------------------------------------------------------------------
# W15 T042 driver alignment (expectations parsed from the driver itself)
# ---------------------------------------------------------------------------


def _driver_module() -> ast.Module:
    assert DRIVER.is_file(), "the G3 phase4 acceptance driver is missing"
    return ast.parse(DRIVER.read_text(encoding="utf-8"))


def _driver_blocked_codes() -> set[str]:
    for node in _driver_module().body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(target, ast.Name) and target.id == "_BLOCKED_CODES" for target in targets):
            assert node.value is not None
            return set(ast.literal_eval(node.value))
    raise AssertionError("the driver no longer publishes _BLOCKED_CODES")


def _driver_seat_keys() -> tuple[str, ...]:
    """The tuple of payload keys the driver reads as a checked-out seat."""
    for node in ast.walk(_driver_module()):
        if not isinstance(node, (ast.Tuple, ast.List)):
            continue
        try:
            values = ast.literal_eval(node)
        except ValueError:
            continue
        if isinstance(values, tuple) and "seat_count" in values:
            return values
    raise AssertionError("the driver no longer probes for seat fields")


class TestT042Alignment:
    def test_the_driver_treats_our_refusal_code_as_blocked_not_failed(self):
        assert "BLOCKED_LICENSE" in _driver_blocked_codes()

    def test_the_seat_keys_we_avoid_are_the_ones_the_driver_probes(self):
        assert _driver_seat_keys() == DRIVER_SEAT_KEYS
        data = inspect(allowlisted_worker(has_product=True))
        assert not set(data) & set(_driver_seat_keys())

    def test_driver_has_product_check_is_satisfied_by_a_real_row(self):
        """``_check_license_probe`` needs a row carrying ``hasProduct``."""
        data = inspect(allowlisted_worker(has_product={"ACDC": True}))
        rows = data.get("products")
        assert isinstance(rows, list)
        assert [row for row in rows if isinstance(row, Mapping) and "hasProduct" in row]

    def test_driver_missing_product_detection_matches_our_rows(self):
        """``_missing_products`` reads ``hasProduct is False`` from the same rows."""

        def driver_missing(payload: Mapping[str, Any]) -> list[str]:
            rows_raw = (payload.get("data") or {}).get("products")
            rows = rows_raw if isinstance(rows_raw, list) else []
            return [str(row.get("product") or row.get("name") or "unknown")
                    for row in rows if isinstance(row, Mapping) and row.get("hasProduct") is False]

        worker = allowlisted_worker(has_product={"ACDC": True, "HEATTRANSFER": False})
        data = inspect(worker, products=["ACDC", "HEATTRANSFER"])
        assert driver_missing({"data": data}) == ["HEATTRANSFER"]
        assert data["missing_products"] == ["HEATTRANSFER"]

    def test_driver_license_text_check_does_not_trip_on_our_payload(self):
        """The driver rejects a payload echoing license-server configuration text."""
        data = inspect(allowlisted_worker(has_product={"ACDC": True}))
        text = json.dumps(data)
        assert not ("license" in text.lower() and ("SERVER" in text.upper() and "=" in text))

    def test_unavailable_probe_is_a_capability_block_not_a_fake_success(self):
        worker = worker_for(model_for())
        error = expect_error("BLOCKED_LICENSE", inspect, worker)
        assert error.code in _driver_blocked_codes() or "LICENSE" in error.code.upper()
        assert error.safe_retry is False

    def test_operation_ids_the_case_plans_are_published(self):
        planned = {
            "runtime.license_inspect",
            "runtime.capabilities",
        }
        assert planned <= set(DISPATCH)
        source = DRIVER.read_text(encoding="utf-8")
        for operation in planned:
            assert operation in source, operation
        assert re.search(r"\"W15_T042_license\"", source)


class TestJavapVerification:
    """Every signature the module cites is checked against the installed jar."""

    @pytest.mark.skipif(not JAVAP.is_file() or not API_JAR.is_file(),
                        reason="javap and the COMSOL public API jar are not installed")
    def test_cited_signatures_match_the_installed_jar(self):
        model_util = _javap("com.comsol.model.util.ModelUtil")
        model = _javap("com.comsol.model.Model")
        cited = 0
        for api, row in runtime.LICENSE_API_PROVENANCE.items():
            signature = row["javap_signature"]
            if signature is None:
                continue
            owner = model_util if api.startswith("ModelUtil.") else model
            assert signature in owner, (api, signature)
            cited += 1
        assert cited == 5

    @pytest.mark.skipif(not JAVAP.is_file() or not API_JAR.is_file(),
                        reason="javap and the COMSOL public API jar are not installed")
    def test_the_jar_declares_the_surface_the_module_reports(self):
        declared = _javap("com.comsol.model.util.ModelUtil")
        names = {line.split("(")[0].split()[-1] for line in declared}
        assert {"hasProduct", "hasProductForFile", "checkoutLicense", "getComsolVersion"} <= names
        # documented by the KB but not part of the installed public API jar
        assert "getLicenseNumber" not in names
        assert "getUsedProducts" in {line.split("(")[0].split()[-1]
                                     for line in _javap("com.comsol.model.Model")}

    @pytest.mark.skipif(not JAVAP.is_file() or not API_JAR.is_file(),
                        reason="javap and the COMSOL public API jar are not installed")
    def test_the_seat_consuming_family_is_what_the_jar_calls_it(self):
        declared = _javap("com.comsol.model.util.ModelUtil")
        names = {line.split("(")[0].split()[-1] for line in declared}
        assert runtime.SEAT_CONSUMING_METHODS <= names
        assert {"checkoutLicense", "checkoutLicenseForFile",
                "checkoutLicenseForFileOnServer"} == runtime.SEAT_CONSUMING_METHODS


# ---------------------------------------------------------------------------
# the module's allow-list constants against the Java Worker source
# ---------------------------------------------------------------------------

JAVA_WORKER = ROOT / "comsol_mcp" / "worker_java" / "PersistentComsolWorker.java"


def _java_allowlist(set_name: str) -> set[str]:
    """Extract one Java-side allow-list set from the Worker source."""
    source = JAVA_WORKER.read_text(encoding="utf-8")
    marker = f"Set<String> {set_name} = new HashSet<>(Arrays.asList("
    start = source.index(marker) + len(marker)
    end = source.index("));", start)
    return set(re.findall(r'"([A-Za-z0-9_]+)"', source[start:end]))


class TestWorkerAllowlistProvenance:
    """The declared reachability must match the Java Worker it was read from."""

    def test_license_surface_constants_match_the_worker_source(self):
        assert JAVA_WORKER.is_file()
        assert runtime.WORKER_MODELUTIL_ALLOWLIST == _java_allowlist("MODEL_UTIL")
        node_methods = _java_allowlist("METHODS")
        assert runtime.WORKER_NODE_METHODS_ALLOWLIST <= node_methods
        assert "getUsedProducts" in node_methods
        assert "getComsolVersion" in node_methods

    def test_probe_reachability_matches_the_allowlist_it_cites(self):
        for api, row in runtime.LICENSE_API_PROVENANCE.items():
            entry = row["worker_allowlist_entry"]
            if entry is None:
                assert row["worker_allowlist_present"] is False, api
                continue
            declared = row["worker_allowlist_present"]
            if row["worker_command"] == "modelutil":
                assert declared == (entry in runtime.WORKER_MODELUTIL_ALLOWLIST), api
            else:
                assert declared == (entry in runtime.WORKER_NODE_METHODS_ALLOWLIST), api

    def test_the_probe_method_is_declared_reachable_once_the_allowlist_carries_it(self):
        """Pins the state after the Worker owner applied the requested entry."""
        assert "hasProduct" in _java_allowlist("MODEL_UTIL")
        assert runtime.LICENSE_API_PROVENANCE["ModelUtil.hasProduct"]["worker_allowlist_present"] is True
        assert runtime.LICENSE_API_PROVENANCE["ModelUtil.checkoutLicense"]["consumes_license_seat"] is True
        # Nothing still pending may already be present in the allow-list: a
        # stale request would silently misstate what the Worker still lacks.
        assert not any(
            item["entry"] in runtime.WORKER_MODELUTIL_ALLOWLIST
            for item in runtime.WORKER_ALLOWLIST_REQUESTS
            if item["allowlist"] == "MODEL_UTIL"
        )
