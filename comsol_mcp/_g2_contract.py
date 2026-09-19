"""Typed, path based contracts shared by the G2 model operations.

The legacy surface intentionally accepts a few JSON strings for compatibility.
The G2 surface keeps the value's kind and shape explicit at the control
boundary and resolves model paths through a small, allow-listed accessor
registry.  This module has no COMSOL dependency, which makes its validation
safe to run before a write enters the engine queue.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence

from ._execution_contract import ExecutionContractError


KIND_NAMES = frozenset({
    "boolean", "int32", "int64", "float64", "string", "expression", "complex128",
})

# PropFeature.getValueType is an authoritative COMSOL API enum-like string.
# Keep the mapping here instead of inferring a getter from the JSON value: an
# empty vector/matrix has no runtime element to infer, and String values may be
# unevaluated parameter expressions.  These names and getter pairs are from
# the installed COMSOL 6.4 PropFeature API; adapters for another COMSOL
# release must extend this table explicitly.
ENGINE_VALUE_SPECS: dict[str, dict[str, Any]] = {
    "Boolean": {"kind": "boolean", "rank": 0, "getter": "getBoolean", "java_signature": "boolean"},
    "BooleanArray": {"kind": "boolean", "rank": 1, "getter": "getBooleanArray", "java_signature": "boolean[]"},
    "BooleanMatrix": {"kind": "boolean", "rank": 2, "getter": "getBooleanMatrix", "java_signature": "boolean[][]"},
    "String": {"kind": "string", "rank": 0, "getter": "getString", "java_signature": "java.lang.String"},
    "StringArray": {"kind": "string", "rank": 1, "getter": "getStringArray", "java_signature": "java.lang.String[]"},
    "StringMatrix": {"kind": "string", "rank": 2, "getter": "getStringMatrix", "java_signature": "java.lang.String[][]"},
    "Int": {"kind": "int32", "rank": 0, "getter": "getInt", "java_signature": "int"},
    "IntArray": {"kind": "int32", "rank": 1, "getter": "getIntArray", "java_signature": "int[]"},
    "IntMatrix": {"kind": "int32", "rank": 2, "getter": "getIntMatrix", "java_signature": "int[][]"},
    "Double": {"kind": "float64", "rank": 0, "getter": "getDouble", "java_signature": "double"},
    "DoubleArray": {"kind": "float64", "rank": 1, "getter": "getDoubleArray", "java_signature": "double[]"},
    "DoubleMatrix": {"kind": "float64", "rank": 2, "getter": "getDoubleMatrix", "java_signature": "double[][]"},
    # DoubleRowMatrix has a matrix getter and a StringArray view.  G2 uses the
    # evaluated matrix view for typed readback and requires an explicit
    # expression/string assignment when callers want the unevaluated view.
    "DoubleRowMatrix": {"kind": "float64", "rank": 2, "getter": "getDoubleMatrix", "java_signature": "double[][]"},
}


def engine_value_spec(value_type: Any) -> dict[str, Any] | None:
    """Return a copy of the known PropFeature value contract, or ``None``."""
    if not isinstance(value_type, str):
        return None
    spec = ENGINE_VALUE_SPECS.get(value_type.strip())
    return dict(spec) if spec is not None else None


def signature_spec(signature: Any) -> dict[str, Any] | None:
    """Map the small, explicit Java signature vocabulary accepted on the wire."""
    if not isinstance(signature, str):
        return None
    aliases = {
        "boolean": ("boolean", 0), "java.lang.Boolean": ("boolean", 0),
        "int": ("int32", 0), "java.lang.Integer": ("int32", 0),
        "long": ("int64", 0), "java.lang.Long": ("int64", 0),
        "double": ("float64", 0), "java.lang.Double": ("float64", 0),
        "java.lang.String": ("string", 0), "String": ("string", 0),
        "[Z": ("boolean", 1), "[I": ("int32", 1), "[J": ("int64", 1),
        "[D": ("float64", 1), "[Ljava.lang.String;": ("string", 1),
        "boolean[]": ("boolean", 1), "int[]": ("int32", 1),
        "long[]": ("int64", 1), "double[]": ("float64", 1),
        "java.lang.String[]": ("string", 1), "String[]": ("string", 1),
        "boolean[][]": ("boolean", 2), "int[][]": ("int32", 2),
        "long[][]": ("int64", 2), "double[][]": ("float64", 2),
        "java.lang.String[][]": ("string", 2), "String[][]": ("string", 2),
    }
    spec = aliases.get(signature.strip())
    return {"kind": spec[0], "rank": spec[1]} if spec else None

# A NodePath is data, never a Python/Java expression.  Keep this list small and
# explicit.  New COMSOL collection accessors must be added with a versioned
# adapter entry rather than becoming an arbitrary method-call escape hatch.
ACCESSOR_METHODS = frozenset({
    "active", "component", "dataset", "feature", "geom", "geometry", "material",
    "mesh", "modelNode", "numerical", "param", "physics", "result", "selection",
    "sol", "study", "table", "variable", "view", "plotGroup", "export",
})


def _error(code: str, message: str) -> ExecutionContractError:
    return ExecutionContractError(code, message)


def _is_sequence(value: Any) -> bool:
    return isinstance(value, (list, tuple))


def _shape_of(value: Any) -> tuple[int, ...]:
    """Return a rectangular JSON array shape, rejecting ragged arrays."""
    if not _is_sequence(value):
        return ()
    values = list(value)
    if not values:
        return (0,)
    child_shapes = [_shape_of(item) for item in values]
    if any(shape != child_shapes[0] for shape in child_shapes[1:]):
        raise _error("PROPERTY_TYPE_MISMATCH", "typed value data must be a rectangular array")
    return (len(values),) + child_shapes[0]


def _flatten(value: Any) -> list[Any]:
    if not _is_sequence(value):
        return [value]
    out: list[Any] = []
    for item in value:
        out.extend(_flatten(item))
    return out


def _normalise_shape(shape: Any) -> tuple[int, ...]:
    if not _is_sequence(shape):
        raise _error("PROPERTY_TYPE_MISMATCH", "typed value shape must be an array")
    out: list[int] = []
    for value in shape:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise _error("PROPERTY_TYPE_MISMATCH", "typed value shape entries must be non-negative integers")
        out.append(value)
    return tuple(out)


def _validate_scalar(kind: str, value: Any) -> None:
    if kind == "boolean":
        if type(value) is not bool:
            raise _error("PROPERTY_TYPE_MISMATCH", "boolean typed value requires a JSON boolean")
        return
    if kind in {"int32", "int64"}:
        if isinstance(value, bool) or not isinstance(value, int):
            raise _error("PROPERTY_TYPE_MISMATCH", f"{kind} typed value requires an integer")
        if kind == "int32" and not -(2**31) <= value < 2**31:
            raise _error("PROPERTY_TYPE_MISMATCH", "int32 typed value is outside its signed range")
        if kind == "int64" and not -(2**63) <= value < 2**63:
            raise _error("PROPERTY_TYPE_MISMATCH", "int64 typed value is outside its signed range")
        return
    if kind == "float64":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise _error("PROPERTY_TYPE_MISMATCH", "float64 typed value requires a finite number")
        return
    if kind in {"string", "expression"}:
        if not isinstance(value, str):
            raise _error("PROPERTY_TYPE_MISMATCH", f"{kind} typed value requires a string")
        return
    if kind == "complex128":
        if isinstance(value, Mapping):
            if set(value) != {"real", "imag"}:
                raise _error("PROPERTY_TYPE_MISMATCH", "complex128 values require real and imag fields")
            for item in (value["real"], value["imag"]):
                if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)):
                    raise _error("PROPERTY_TYPE_MISMATCH", "complex128 components must be finite numbers")
            return
        if _is_sequence(value) and len(value) == 2 and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value):
            return
        raise _error("PROPERTY_TYPE_MISMATCH", "complex128 value requires {real,imag} or a two-number pair")


def validate_typed_value(value: Mapping[str, Any], *, expected: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Validate and copy one wire TypedValue without collapsing its shape."""
    if not isinstance(value, Mapping):
        raise _error("PROPERTY_TYPE_MISMATCH", "typed value must be an object")
    allowed = {"kind", "shape", "data", "unit", "java_signature"}
    unexpected = set(value) - allowed
    if unexpected:
        raise _error("PROPERTY_TYPE_MISMATCH", f"typed value has unsupported fields: {sorted(unexpected)}")
    kind = value.get("kind")
    if kind not in KIND_NAMES:
        raise _error("PROPERTY_TYPE_MISMATCH", f"unsupported typed value kind: {kind!r}")
    shape = _normalise_shape(value.get("shape"))
    data = value.get("data")
    actual_shape = _shape_of(data)
    # A scalar is represented by [] and a one-element array by [1].  This is
    # deliberate: a singleton vector must never silently become a scalar.
    if kind == "complex128" and shape == ():
        _validate_scalar(kind, data)
    elif shape != actual_shape:
        raise _error("PROPERTY_TYPE_MISMATCH", f"declared shape {list(shape)} does not match data shape {list(actual_shape)}")
    elif kind == "complex128":
        for item in _flatten(data):
            _validate_scalar(kind, item)
    else:
        for item in _flatten(data):
            _validate_scalar(kind, item)
    if "java_signature" in value and value["java_signature"] is not None and not isinstance(value["java_signature"], str):
        raise _error("PROPERTY_TYPE_MISMATCH", "java_signature must be a string")
    if "unit" in value and value["unit"] is not None and not isinstance(value["unit"], str):
        raise _error("PROPERTY_TYPE_MISMATCH", "unit must be a string")
    result = {"kind": kind, "shape": list(shape), "data": data}
    for field in ("unit", "java_signature"):
        if field in value:
            result[field] = value[field]
    if expected:
        expected_kind = expected.get("kind") or expected.get("value_type")
        if expected_kind and expected_kind != kind and not (expected_kind == "string" and kind == "expression"):
            raise _error("PROPERTY_TYPE_MISMATCH", f"property expects {expected_kind}, received {kind}")
        expected_shape = expected.get("shape")
        if expected_shape is not None and tuple(expected_shape) != shape:
            raise _error("PROPERTY_TYPE_MISMATCH", f"property expects shape {expected_shape}, received {list(shape)}")
        expected_rank = expected.get("shape_rank")
        if expected_rank is not None and len(shape) != expected_rank:
            raise _error("PROPERTY_TYPE_MISMATCH", f"property expects array rank {expected_rank}, received {len(shape)}")
        if expected.get("java_signature") and value.get("java_signature"):
            expected_sig = signature_spec(expected["java_signature"])
            actual_sig = signature_spec(value["java_signature"])
            if expected_sig is None or actual_sig is None or expected_sig != actual_sig:
                raise _error("PROPERTY_TYPE_MISMATCH", "typed value java_signature does not match property metadata")
        allowed_values = expected.get("allowed_values")
        if allowed_values is not None and any(item not in allowed_values for item in _flatten(data)):
            raise _error("INVALID_PROPERTY_VALUE", "property value is outside the allowed values")
    return result


def validate_property_set(properties: Any, *, schemas: Mapping[str, Mapping[str, Any]] | None = None) -> list[dict[str, Any]]:
    if not isinstance(properties, list):
        raise _error("PROPERTY_TYPE_MISMATCH", "properties must be an array")
    out: list[dict[str, Any]] = []
    names: set[str] = set()
    for item in properties:
        if not isinstance(item, Mapping) or set(item) - {"name", "value"} or not isinstance(item.get("name"), str) or not item["name"]:
            raise _error("PROPERTY_TYPE_MISMATCH", "each property entry requires a non-empty name and typed value")
        name = item["name"]
        if name in names:
            raise _error("PROPERTY_TYPE_MISMATCH", f"duplicate property assignment: {name}")
        names.add(name)
        out.append({"name": name, "value": validate_typed_value(item.get("value"), expected=(schemas or {}).get(name))})
    return out


@dataclass(frozen=True, slots=True)
class NodeSegment:
    collection: str | None = None
    tag: str | None = None
    accessor: str | None = None

    def as_dict(self) -> dict[str, str]:
        if self.accessor is not None:
            return {"accessor": self.accessor}
        return {"collection": str(self.collection), "tag": str(self.tag)}


@dataclass(frozen=True, slots=True)
class NodePath:
    segments: tuple[NodeSegment, ...]

    @classmethod
    def from_wire(cls, value: Any, *, allow_empty: bool = True) -> "NodePath":
        if not isinstance(value, Mapping) or set(value) != {"segments"} or not isinstance(value["segments"], list):
            raise _error("INVALID_NODE_PATH", "NodePath requires a segments array")
        if not allow_empty and not value["segments"]:
            raise _error("INVALID_NODE_PATH", "NodePath must contain at least one segment")
        segments: list[NodeSegment] = []
        for raw in value["segments"]:
            if not isinstance(raw, Mapping):
                raise _error("INVALID_NODE_PATH", "NodePath segment must be an object")
            if set(raw) == {"accessor"}:
                accessor = raw["accessor"]
                if not isinstance(accessor, str) or accessor not in ACCESSOR_METHODS:
                    raise _error("INVALID_NODE_PATH", f"unsupported path accessor: {accessor!r}")
                segments.append(NodeSegment(accessor=accessor))
            elif set(raw) == {"collection", "tag"}:
                collection, tag = raw["collection"], raw["tag"]
                if not all(isinstance(item, str) and item for item in (collection, tag)):
                    raise _error("INVALID_NODE_PATH", "collection and tag must be non-empty strings")
                if collection not in ACCESSOR_METHODS:
                    raise _error("INVALID_NODE_PATH", f"unsupported path collection: {collection!r}")
                segments.append(NodeSegment(collection=collection, tag=tag))
            else:
                raise _error("INVALID_NODE_PATH", "each path segment must be collection+tag or accessor only")
        return cls(tuple(segments))

    def as_dict(self) -> dict[str, Any]:
        return {"segments": [segment.as_dict() for segment in self.segments]}


def resolve_node_path(model: Any, path: Mapping[str, Any] | NodePath) -> Any:
    """Resolve a NodePath through RemoteJava's allow-listed call facade."""
    parsed = path if isinstance(path, NodePath) else NodePath.from_wire(path)
    current = model
    for segment in parsed.segments:
        try:
            method = getattr(current, segment.accessor or segment.collection or "")
            current = method() if segment.accessor is not None else method(segment.tag)
        except ExecutionContractError:
            raise
        except Exception as exc:
            label = segment.accessor or f"{segment.collection}:{segment.tag}"
            raise _error("NODE_NOT_FOUND", f"could not resolve node path segment {label}") from exc
        if current is None:
            raise _error("NODE_NOT_FOUND", "node path resolved to null")
    return current


def typed_value_from_engine(value: Any, *, kind: str | None = None, unit: str | None = None) -> dict[str, Any]:
    """Represent a worker reply while preserving list rank and singleton arrays."""
    if kind is None:
        if type(value) is bool:
            kind = "boolean"
        elif isinstance(value, int) and not isinstance(value, bool):
            kind = "int64"
        elif isinstance(value, float):
            kind = "float64"
        elif isinstance(value, str):
            kind = "string"
        else:
            kind = "string" if value is None else "expression"
    shape = list(_shape_of(value))
    if kind == "complex128" and isinstance(value, Mapping):
        shape = []
    result = {"kind": kind, "shape": shape, "data": value}
    if unit:
        result["unit"] = unit
    return result


def property_schema_from_engine(node: Any, name: str | None = None) -> dict[str, Any]:
    """Read COMSOL PropFeature metadata without guessing unsupported types."""
    try:
        names = [str(item) for item in (node.properties() if name is None else [name])]
    except Exception as exc:
        raise _error("API_UNSUPPORTED", "the selected node does not expose properties()") from exc
    rows: list[dict[str, Any]] = []
    for prop in names:
        row: dict[str, Any] = {"name": prop}
        for field, method in (("value_type", "getValueType"), ("allowed_values", "getAllowedPropertyValues")):
            try:
                raw = getattr(node, method)(prop)
                if raw is not None:
                    row[field] = list(raw) if isinstance(raw, (list, tuple)) else raw
            except Exception:
                # COMSOL returns null or raises for properties where this
                # metadata is not available; report unknown instead of lying.
                row[field] = None
        spec = engine_value_spec(row.get("value_type"))
        if spec is not None:
            row.update({
                "kind": spec["kind"],
                "shape_rank": spec["rank"],
                "getter": spec["getter"],
                "java_signature": spec["java_signature"],
                "metadata_status": "KNOWN",
            })
        else:
            # Unknown COMSOL metadata must remain visible to callers and is
            # never converted into a guessed String/number setter.
            row.update({"kind": None, "shape_rank": None, "getter": None,
                        "java_signature": None, "metadata_status": "UNKNOWN"})
        try:
            row["type_id"] = str(node.getType())
        except Exception:
            row["type_id"] = None
        rows.append(row)
    return {"properties": rows} if name is None else (rows[0] if rows else {"name": name})
