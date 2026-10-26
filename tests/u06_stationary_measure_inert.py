"""Inert transport leaves for software tests over real RemoteJava wrappers."""
from __future__ import annotations

import math
import re
from typing import Any, Mapping

from comsol_mcp._java_worker import PersistentJavaWorker, RemoteJava, RemoteModel


MODEL = "com.comsol.model.Model"
MODEL_PARAM = "com.comsol.model.ModelParam"
PARAM_BASE = "com.comsol.model.ParamBase"
NUMBER_UNIT = re.compile(r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\[([^\]]+)\]")


class InertRemoteWorker(PersistentJavaWorker):
    """Keep actual RemoteJava/typed-signature dispatch; inert only SDK leaves."""

    def __init__(self, tree: Any) -> None:
        self.tree = tree
        self._generation = 41
        self._objects: dict[str, Any] = {}
        self._handles: dict[int, str] = {}
        self.dispatches: list[dict[str, Any]] = []
        self.descriptor_reads: list[str] = []
        self.typed_evaluations: list[dict[str, Any]] = []
        self.typed_evaluation_failure: BaseException | None = None
        self._root_handle = self._register(tree.model, MODEL)

    def _register(self, native: Any, java_type: str | None = None) -> str:
        key = id(native)
        if key in self._handles:
            return self._handles[key]
        handle = f"inert-{len(self._objects) + 1}"
        if java_type is None:
            java_type = (MODEL_PARAM if native is getattr(self.tree, "parameter_node", None)
                         else "com.comsol.model.ModelEntity")
        self._handles[key] = handle
        self._objects[handle] = native
        return handle

    def native_handle(self, native: Any) -> str:
        return self._register(native)

    class _Client:
        def __init__(self, owner: "InertRemoteWorker") -> None:
            self.owner = owner

        def model(self, _tag: str) -> RemoteModel:
            return RemoteModel(self.owner, self.owner._root_handle, self.owner.generation, MODEL)

    def client(self) -> "InertRemoteWorker._Client":
        return self._Client(self)

    def describe_public(self, receiver: Any) -> dict[str, Any]:
        java_type = getattr(receiver, "java_type", "")
        self.descriptor_reads.append(java_type)
        if java_type == MODEL:
            interfaces = [MODEL]
            methods = [{"interface": MODEL, "method": "param", "parameters": [], "returns": MODEL_PARAM}]
        elif java_type == MODEL_PARAM:
            interfaces = [MODEL_PARAM, PARAM_BASE]
            methods = [{"interface": PARAM_BASE, "method": "evaluate",
                        "parameters": ["java.lang.String", "java.lang.String"], "returns": "double"}]
        else:
            interfaces, methods = [], []
        return {
            "runtime_version": "6.4.0.293",
            "interfaces": interfaces,
            "methods": methods,
            "metadata_complete": True,
            "metadata_limits": {"interfaces": 32, "methods": 256},
        }

    def _is_native_proxy_target(self, value: Any) -> bool:
        if value is getattr(self.tree, "parameter_node", None):
            return True
        # The canonical FSelection fixture intentionally has no generic
        # ``calls`` ledger. Keep its SDK-shaped selection receiver proxied so
        # the production selection methods still travel through RemoteJava.
        if all(callable(getattr(value, name, None)) for name in
               ("set", "all", "entities", "geom", "dim", "named", "isInheriting")):
            return True
        return (
            isinstance(getattr(value, "calls", None), list)
            and (callable(getattr(value, "_guard", None))
                 or callable(getattr(value, "_record", None)))
        )

    def _encode(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, bytes, bool, int, float)):
            return value
        if isinstance(value, Mapping):
            return {str(key): self._encode(child) for key, child in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._encode(child) for child in value]
        if self._is_native_proxy_target(value):
            java_type = MODEL_PARAM if value is getattr(self.tree, "parameter_node", None) else None
            handle = self._register(value, java_type)
            return {"$worker_handle": handle, "generation": self.generation,
                    "java_type": (MODEL_PARAM if self._objects[handle] is getattr(self.tree, "parameter_node", None)
                                  else "com.comsol.model.ModelEntity")}
        raise TypeError(f"inert transport cannot encode leaf {type(value).__name__}")

    @staticmethod
    def _failure_reply(exc: BaseException) -> dict[str, Any]:
        code = getattr(exc, "code", "ENGINE_CALL_FAILED")
        unknown = code == "EXECUTION_STATE_UNKNOWN" or getattr(exc, "execution_state_unknown", False) is True
        failure = {"code": code, "message": str(exc)}
        if unknown:
            failure["execution_state_unknown"] = True
        return {"ok": False, "code": code, "message": str(exc),
                "execution_state_unknown": unknown, "failure": failure}

    def submit(self, kind: str, payload: Mapping[str, Any], **_kwargs: Any) -> dict[str, Any]:
        if kind != "call":
            raise AssertionError(f"unexpected worker command {kind!r}")
        handle = str(payload["handle"])
        method = str(payload["method"])
        args = list(payload.get("args", ()))
        self.dispatches.append({"handle": handle, "method": method, "args": args})
        native = self._objects[handle]
        try:
            if method == "evaluate" and native is getattr(self.tree, "parameter_node", None):
                record = {"handle": handle, "arguments": args}
                if len(args) == 2 and all(isinstance(arg, Mapping) for arg in args):
                    record.update({"expression": args[0].get("data"), "target_unit": args[1].get("data")})
                self.typed_evaluations.append(record)
                if self.typed_evaluation_failure is not None:
                    failure = self.typed_evaluation_failure
                    self.typed_evaluation_failure = None
                    raise failure
                result = self._evaluate_typed_constants(args)
            else:
                result = getattr(native, method)(*args)
            return {"ok": True, "result": self._encode(result)}
        except BaseException as exc:
            return self._failure_reply(exc)

    def _evaluate_typed_constants(self, args: list[Any]) -> float:
        if len(args) != 2 or any(
            not isinstance(item, Mapping) or item.get("kind") != "string"
            or item.get("shape") != [] or item.get("java_signature") != "java.lang.String"
            or type(item.get("data")) is not str
            for item in args
        ):
            raise AssertionError("typed ParamBase.evaluate arguments lost their exact String signatures")
        expression, target_unit = (item["data"] for item in args)
        self.typed_evaluations[-1].update({"expression": expression, "target_unit": target_unit})
        matches = NUMBER_UNIT.findall(expression)
        if len(matches) != 2:
            raise AssertionError(f"unsupported inert constant expression: {expression!r}")
        first_value, first_unit = float(matches[0][0]), matches[0][1]
        second_value, second_unit = float(matches[1][0]), matches[1][1]
        if second_value == 0:
            raise AssertionError("division by zero in inert expression")
        source_unit = self._quotient_unit(first_unit, second_unit)
        value = first_value / second_value
        if "sqrt(" in expression:
            source_unit = self._sqrt_unit(source_unit)
            value = math.sqrt(value)
        if expression.startswith("0*"):
            value = 0.0
        return self._convert(value, source_unit, target_unit)

    @staticmethod
    def _quotient_unit(numerator: str, denominator: str) -> str:
        if numerator == denominator:
            return "1"
        if denominator == "1":
            return numerator
        if numerator.endswith("*" + denominator):
            return numerator[:-(len(denominator) + 1)]
        if numerator == "m^3" and denominator == "m":
            return "m^2"
        if numerator == "m^2" and denominator == "m":
            return "m"
        if numerator == "K^2*m" and denominator == "m":
            return "K^2"
        raise AssertionError(f"unsupported inert quotient units {numerator!r}/{denominator!r}")

    @staticmethod
    def _sqrt_unit(unit: str) -> str:
        if unit == "K^2":
            return "K"
        if unit.endswith("^2"):
            return unit[:-2]
        return unit

    @staticmethod
    def _convert(value: float, source: str, target: str) -> float:
        if source == target:
            return value
        if source in {"K", "degC", "degF"} and target in {"K", "degC", "degF"}:
            if source == "K":
                kelvin = value
            elif source == "degC":
                kelvin = value + 273.15
            else:
                kelvin = (value + 459.67) * 5.0 / 9.0
            if target == "K":
                return kelvin
            if target == "degC":
                return kelvin - 273.15
            return kelvin * 9.0 / 5.0 - 459.67
        scales = {"1": 1.0, "m": 1.0, "cm": 0.01}
        if source not in scales or target not in scales:
            raise AssertionError(f"unsupported inert unit conversion {source!r} to {target!r}")
        return value * scales[source] / scales[target]
