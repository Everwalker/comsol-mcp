"""Shared infrastructure for the G3 domain operations (W13-W16).

This module holds everything the per-domain modules (``_g3_w13`` ...
``_g3_w16``) need and that is not already provided by the G2 layer: verified
type-string vocabularies, node create/remove with readback, SelectionSpec
resolution, and the small argument/value validators.

API provenance (COMSOL 6.4.0.293, installed at /Applications/COMSOL64)

Every COMSOL method name and every ``create(<tag>, <type>)`` type string used
in this module is verified offline against one of two local sources:

* ``javap`` of the installed public API jar
  ``/Applications/COMSOL64/Multiphysics/apiplugins/com.comsol.api_1.0.0.jar``
  (interface signatures), plus ``javap`` of the implementation jar
  ``plugins/com.comsol.model_1.0.0.jar`` where an accessor that the
  Programming Reference documents is *not* declared on the public interface
  (``AbstractModelImpl.func()/variable()/selection()/func(String)/...``).
* The local COMSOL 6.4 documentation corpus
  ``/Users/everwalker/Documents/KnowledgeBases/COMSOL-6.4-KB/kb.py`` - the
  per-type property tables and the documented call sequences.

Specifically:

* ``Model.param()`` -> ``ModelParam``; ``ModelParam.create(String)``,
  ``ModelParamGroupList.create(String)``, ``ModelParamGroup.move(String[])``,
  ``ModelParam.move(String[], String)``, ``ModelParamGroup.group()``
  (javap com.comsol.model.ModelParam / ModelParamGroup / ModelParamGroupList).
  Default parameter group tag ``default`` and the global parameter scope
  ``model.param()`` are documented in the Programming Reference
  ``model.param()`` page.
* ``ExprList.create(String, String)`` / ``ComponentExprList`` /
  ``ModelNode.variable()`` (javap) and the documented global scope
  ``model.variable().create(<tag>)`` with
  ``model.variable(<tag>).selection().named(<seltag>)`` and
  ``model.variable(<tag>).model(<mtag>)`` (Programming Reference
  ``model.variable()`` page: a local selection requires the component node to
  be set first).
* ``FunctionFeatureList.create(String, String)``,
  ``FunctionFeature.functionNames()/importData()/refresh()/getType()`` and the
  full documented type list (Programming Reference ``model.func()`` page).
* ``SelectionList.create(String)`` / ``create(String, String)`` and the
  documented selection type list plus the spatial ``Ball``/``Box`` property
  names ``posx/posy/posz/r/xmin/xmax/.../entitydim/condition``
  (Programming Reference ``model.selection()`` and ``Coordinate-Based
  Selections`` pages, and the ``BallSelection``/``BoxSelection``/
  ``CylinderSelection``/``DiskSelection`` property tables).
* ``ModelNode.measure()`` -> ``GeomMeasureFinal`` -> ``selection()`` ->
  ``MeshSelection`` with ``geom(int)/set(int...)/all()/entities()`` and the
  metric getters ``getArea/getVolume/getLength/getBoundaryArea/...``
  (javap; Programming Reference "Measurements").

Anything that could not be verified offline is *not* guessed here: the op
implementations reject such a path before the first write and report it in
their result/unverified list.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Iterable, Mapping, Sequence

from ._g2_contract import (
    ExecutionContractError,
    NodePath,
    resolve_node_path,
    typed_value_from_engine,
    validate_typed_value,
)
from ._g2_engine import (
    _call,
    _model as _bound_model,
    _typed_readback_comparison,
    _worker_failure_code,
)

# ---------------------------------------------------------------------------
# Identity / envelope fields that the control plane owns and this layer never
# interprets.  They are accepted (and ignored) so a dispatcher may pass the
# whole validated request body through.
# ---------------------------------------------------------------------------
ENVELOPE_FIELDS = frozenset(
    {
        "project_id",
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "correlation_id",
        "trace_id",
    }
)

# COMSOL object tags are not formally specified in the offline corpus beyond
# examples such as ``comp1``, ``sel1``, ``int1``.  This layer therefore only
# accepts a conservative subset (letter start, alphanumerics/underscore) and
# rejects everything else *before* the engine sees it.  The check can only
# narrow, never widen, what the engine accepts.
TAG_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")

# ---------------------------------------------------------------------------
# Verified type vocabularies
# ---------------------------------------------------------------------------

#: ``model.func().create(<tag>, <type>)`` -- Programming Reference
#: ``model.func()``: "The types can be one of the following strings: Analytic,
#: Interpolation, Piecewise, GaussianPulse, Ramp, Rectangle, Step, Triangle,
#: Wave, NormalDistribution, Random, External, MATLAB, Elevation, Image, CGNS,
#: LeastSquares, GaussianProcess, PolynomialChaosExpansion, DNN, and
#: PartialFractionFit.  In addition, model.create(<tag>,"FunctionSwitch")
#: creates a function switch."
FUNCTION_TYPE_IDS = frozenset(
    {
        "Analytic",
        "Interpolation",
        "Piecewise",
        "GaussianPulse",
        "Ramp",
        "Rectangle",
        "Step",
        "Triangle",
        "Wave",
        "NormalDistribution",
        "Random",
        "External",
        "MATLAB",
        "Elevation",
        "Image",
        "CGNS",
        "LeastSquares",
        "GaussianProcess",
        "PolynomialChaosExpansion",
        "DNN",
        "PartialFractionFit",
        "FunctionSwitch",
    }
)

#: ``model.selection().create(<tag>, <type>)`` -- Programming Reference
#: ``model.selection()``: "The following types are available: "Explicit",
#: "Union", "Intersection", "Difference", "Complement", "Adjacent", "Ball",
#: "Box", "Cylinder", "Disk", and "LogicalExpression"."
SELECTION_TYPE_IDS = frozenset(
    {
        "Explicit",
        "Union",
        "Intersection",
        "Difference",
        "Complement",
        "Adjacent",
        "Ball",
        "Box",
        "Cylinder",
        "Disk",
        "LogicalExpression",
    }
)

#: Function types whose documented property table is available offline, mapped
#: to the property names the table lists.  A ``function.create``/``update``
#: definition may only use property names from this table for the node's own
#: type.  Types that are documented as existing but whose property table was
#: not retrieved (``CGNS``, ``DNN``, ``PartialFractionFit``, ``FunctionSwitch``)
#: map to ``None`` and reject any property *before* the write.
FUNCTION_TYPE_PROPERTIES: dict[str, frozenset[str] | None] = {
    # Programming Reference "analytic properties" table.
    "Analytic": frozenset(
        {
            "argders", "args", "complex", "dermethod", "expr", "funcname",
            "periodic", "periodiclower", "periodicupper", "plotargs", "plotaxis",
            "pname", "plist", "argunit", "fununit",
        }
    ),
    # Programming Reference "interpolation properties" table.
    "Interpolation": frozenset(
        {
            "adaptol", "argrange", "defineinv", "definerandom", "defvars",
            "dseparator", "extrap", "extrapvalue", "filename", "frame",
            "funcinvname", "funcname", "funcnametable", "interp", "leftend",
            "modelres", "nargs", "plotfuncname", "plotleftextrap",
            "plotrightextrap", "points", "primfunname", "randomname",
            "randomnargs", "randomrange", "reinterp", "reinterporder",
            "resultTable", "rightend", "sampling", "scaledata", "source",
            "sourcetype", "scrfun", "scrfunname", "struct", "table", "argunit",
            "fununit", "argtrans", "valtrans",
        }
    ),
    # Programming Reference "piecewise properties" table.
    "Piecewise": frozenset(
        {"arg", "extrap", "extrapvalue", "funcname", "pieces", "pname", "plist",
         "smooth", "smoothzone", "argunit", "fununit"}
    ),
    # Programming Reference "Gaussian pulse Properties" table.
    "GaussianPulse": frozenset(
        {"baseline", "funcname", "integralvalue", "location", "peakvalue",
         "plotlimitsactive", "plotlowerlimit", "plotupperlimit", "sigma",
         "normalization"}
    ),
    # Programming Reference "Ramp Properties" table.
    "Ramp": frozenset(
        {"baseline", "cutoffactive", "cutoff", "funcname", "location", "slope",
         "ncontder", "plotlimitsactive", "plotlowerlimit", "plotupperlimit",
         "smoothzonecutoffactive", "smoothzonelocactive", "smoothzonecutoff",
         "smoothzoneloc", "argunit", "fununit", "argtrans", "valtrans"}
    ),
    # Programming Reference "Rectangle properties" table.
    "Rectangle": frozenset(
        {"amplitude", "baseline", "funcname", "lower", "ncontder",
         "plotlimitsactive", "plotlowerlimit", "plotupperlimit", "smooth",
         "smoothzone", "upper"}
    ),
    # Programming Reference "Step properties" table.
    "Step": frozenset(
        {"baseline", "from", "funcname", "location", "locationdef", "ncontder",
         "plotlimitsactive", "plotlowerlimit", "plotupperlimit", "smooth",
         "smoothzone", "to"}
    ),
    # Programming Reference "triangle properties" table.
    "Triangle": frozenset(
        {"amplitude", "baseline", "funcname", "lower", "ncontder",
         "plotlimitsactive", "plotlowerlimit", "plotupperlimit", "smooth",
         "smoothzone", "upper"}
    ),
    # Programming Reference "wave properties" table.
    "Wave": frozenset(
        {"amplitude", "baseline", "delay", "dutycycle", "funcname", "modul",
         "ncontder", "period", "phase", "plotlimitsactive", "plotlowerlimit",
         "plotupperlimit", "sigma", "smooth", "smoothzone", "type"}
    ),
    # Programming Reference "Normal Distribution properties" table.
    "NormalDistribution": frozenset(
        {"cumfuncname", "funcname", "invcumfuncname", "mean", "nargs",
         "randomname", "seed", "seedactive", "sigma"}
    ),
    # Programming Reference "random properties" table.
    "Random": frozenset(
        {"funcname", "mean", "nargs", "normalsigma", "seed", "seedactive",
         "seedtype", "type", "uniformrange"}
    ),
    # Programming Reference "Elevation properties" table.
    "Elevation": frozenset({"extrap", "extrapvalue", "filename"}),
    # Programming Reference "Least Squares Properties" table.
    "LeastSquares": frozenset(
        {"args", "columnType", "dseparator", "exprs", "filecolumns",
         "fileheaders", "filename", "plist", "scale", "source", "table", "unit"}
    ),
    # Programming Reference "Gaussian Process Properties" table.
    "GaussianProcess": frozenset(
        {"args", "columnType", "covfunction", "definestddev", "descr",
         "dseparator", "filecolumns", "fileheaders", "filename", "source",
         "stddevsuffix", "testerrortable", "testtable", "unit", "useseed",
         "useseedtest", "validation", "validationtable"}
    ),
    # Programming Reference "Polynomial Chaos Expansion Properties" table.
    "PolynomialChaosExpansion": frozenset(
        {"args", "columnType", "descr", "distributionselection", "dseparator",
         "filecolumns", "fileheaders", "filename"}
    ),
    # External / MATLAB: the documented table lists only "ders"; the input data
    # is supplied through ``importData`` from an unresolved artifact, so the
    # W13 definition path accepts no property for them.
    "External": frozenset(),
    "MATLAB": frozenset(),
    "Image": frozenset(),
    "CGNS": None,
    "DNN": None,
    "PartialFractionFit": None,
    "FunctionSwitch": None,
}

#: Spatial selection types and the region properties they accept, from the
#: Programming Reference ``BallSelection``/``BoxSelection``/``CylinderSelection``
#: /``DiskSelection`` property tables (Table 4-16..4-20) plus the documented
#: ``Coordinate-Based Selections`` call sequence.
SPATIAL_SELECTION_TYPES = frozenset({"Ball", "Box", "Cylinder", "Disk"})

SELECTION_REGION_PROPERTIES: dict[str, frozenset[str]] = {
    "Ball": frozenset({"posx", "posy", "posz", "r"}),
    "Box": frozenset({"xmin", "xmax", "ymin", "ymax", "zmin", "zmax"}),
    "Cylinder": frozenset(
        {"angle1", "angle2", "axistype", "bottom", "pos", "r", "rin", "top",
         "ax3", "ax2"}
    ),
    "Disk": frozenset({"angle1", "angle2", "posx", "posy", "r", "rin"}),
}

#: Selection properties that every selection type accepts (Table 4-16 plus the
#: per-type tables in ``model.selection()``).
SELECTION_COMMON_PROPERTIES = frozenset(
    {
        "entitydim", "sdim", "input", "inputent", "condition", "groupcontang",
        "angletol", "selshow", "color", "customcolor", "outputdim",
        "expression", "add", "subtract",
    }
)

#: ``condition`` vocabulary for the spatial selection types (Table 4-16).
SPATIAL_CONDITIONS = frozenset({"intersects", "inside", "somevertex", "allvertices"})

#: The geometric measurement metric names verified in the javap dump of
#: ``com.comsol.model.GeomMeasureBase``.  ``centroid`` is deliberately absent:
#: the API exposes no centroid getter (only ``getVtxCoord``, the average
#: coordinate of selected *vertices*), so it is rejected instead of guessed.
MEASURE_METRICS: dict[str, str] = {
    "area": "getArea",
    "boundary_area": "getBoundaryArea",
    "boundary_volume": "getBoundaryVolume",
    "bounding_box": "getBoundingBox",
    "edge_angle": "getEdgeAngle",
    "finite_voids": "getNFiniteVoids",
    "length": "getLength",
    "n_entities": "getNEntities",
    "perimeter": "getPerimeter",
    "volume": "getVolume",
    "vtx_coord": "getVtxCoord",
    "vtx_distance": "getVtxDistance",
}

#: SelectionSpec kinds (``common.schema.json#/$defs/SelectionSpec``).
SELECTION_KINDS = ("named", "explicit", "all", "spatial", "objects", "inherited")

#: Kinds this layer can resolve to an entity list from a verified read path.
RESOLVABLE_SELECTION_KINDS = ("named", "explicit", "all")

#: Kinds this layer can bind to a local selection node.
BINDABLE_SELECTION_KINDS = ("named", "explicit", "all", "inherited")

SELECTION_SPEC_FIELDS = frozenset(
    {
        "kind", "component", "geometry", "entity_dimension", "tag", "entities",
        "object_tags", "query", "geometry_revision",
    }
)

# ---------------------------------------------------------------------------
# Collection registry: the four W13 node collections plus their verified
# create/remove semantics.
# ---------------------------------------------------------------------------

COLLECTION_SPECS: dict[str, dict[str, Any]] = {
    # model.param().create(<tag>) / model.param().group().<tags|remove>().
    # W13 never passes a type string for a parameter group: the Programming
    # Reference documents ``model.param().create(<tag>)`` only, and no
    # parameter-group type vocabulary is documented.
    "param": {
        "collection": "param",
        "accessor": "param",
        "display": "parameter group",
        "arity": 1,
        "type_ids": None,
        "type_readback": False,
        "list_accessor": "group",
        "parent": "model",
        "reserved_tags": frozenset({"default"}),
    },
    # model.variable().create(<tag>) (Programming Reference model.variable()).
    # ``ExprList.create(String, String)`` exists in javap, but the corpus does
    # not document any variable-group type string, so a type string is
    # rejected here rather than guessed.
    "variable": {
        "collection": "variable",
        "accessor": "variable",
        "display": "variable group",
        "arity": 1,
        "type_ids": None,
        "type_readback": False,
        "list_accessor": None,
        "parent": "model",
        "reserved_tags": frozenset(),
    },
    # model.func().create(<tag>, <type>).
    "func": {
        "collection": "func",
        "accessor": "func",
        "display": "function",
        "arity": 2,
        "type_ids": FUNCTION_TYPE_IDS,
        "type_readback": True,
        "list_accessor": None,
        "parent": "model",
        "reserved_tags": frozenset(),
    },
    # model.component(<ctag>).selection().create(<tag>[, <type>]).
    "selection": {
        "collection": "selection",
        "accessor": "selection",
        "display": "named selection",
        "arity": 2,
        "type_ids": SELECTION_TYPE_IDS,
        "type_readback": True,
        "list_accessor": None,
        "parent": "component",
        "reserved_tags": frozenset(),
    },
}


def collection_spec(collection: Any) -> dict[str, Any]:
    """Return the verified spec for a W13 node collection."""
    if not isinstance(collection, str) or not collection:
        raise ExecutionContractError("INVALID_REQUEST", "collection must be a non-empty string")
    spec = COLLECTION_SPECS.get(collection)
    if spec is None:
        raise ExecutionContractError(
            "INVALID_REQUEST",
            f"unsupported collection {collection!r}; this layer supports {sorted(COLLECTION_SPECS)}",
        )
    return spec


# ---------------------------------------------------------------------------
# Argument validation helpers
# ---------------------------------------------------------------------------


def require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must be an object")
    return dict(value)


# ---------------------------------------------------------------------------
# Wire property containers: the catalogue's ``PropertySet`` rows and the
# mapping form the domain modules use internally.
# ---------------------------------------------------------------------------

#: One ``PropertySet`` row (``common.schema.json#/$defs/PropertySet``):
#: ``name`` + ``value`` and nothing else (``additionalProperties`` is false).
PROPERTY_ROW_FIELDS = frozenset({"name", "value"})

#: Wire ``TypedValue`` fields (``common.schema.json#/$defs/TypedValue``).
TYPED_VALUE_FIELDS = frozenset({"kind", "shape", "data", "unit", "java_signature"})


def is_typed_value(value: Any) -> bool:
    """True when a definition value is already a wire ``TypedValue``.

    A property value is never a bare JSON object, so ``kind`` + ``data`` is an
    unambiguous marker.  ``shape`` is not required here because this predicate
    is shared with the value forms the domain layers accepted before the
    catalogue shape was wired through: a caller-declared kind/shape is
    validated against the engine's own metadata by the writer.
    """
    return isinstance(value, Mapping) and "kind" in value and "data" in value


def typed_value_from_wire(value: Any, label: str) -> dict[str, Any]:
    """Validate one wire ``TypedValue`` and return its normalised copy.

    Only the *shape* of the typed value is checked here (kind vocabulary,
    declared-shape/data consistency, scalar kinds, unit rule); the comparison
    against the target property's own metadata belongs to the writer, which is
    the only layer that can read it.  A malformed value is an
    ``INVALID_REQUEST`` before any engine call.
    """
    if not isinstance(value, Mapping):
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must be a TypedValue object")
    missing = sorted({"kind", "shape", "data"} - set(value))
    if missing:
        raise ExecutionContractError(
            "INVALID_REQUEST", f"{label} is missing required TypedValue fields: {missing}"
        )
    try:
        return validate_typed_value(dict(value))
    except ExecutionContractError as exc:
        if exc.code == "INVALID_REQUEST":
            raise
        raise ExecutionContractError("INVALID_REQUEST", f"{label}: {exc}") from exc


def property_definition(value: Any, label: str = "properties") -> dict[str, Any]:
    """Normalise a wire property container to ``{name: definition value}``.

    Two wire shapes reach the domain operations for the same argument:

    * the action catalogue's ``PropertySet`` - an array of ``{name, value}``
      rows whose ``value`` is a wire ``TypedValue`` (this is what the
      production driver and the catalogue use), and
    * the mapping form ``{name: JSON value | TypedValue | {value, unit}}``
      that the domain modules use as their internal spelling.

    A mapping passes straight through, byte for byte: the existing module and
    test callers are untouched.  A row array is validated (every row must be an
    object carrying exactly ``name`` and ``value``, names must be non-empty
    strings and unique, and each value must be a well-formed ``TypedValue``)
    and converted to the mapping form.  Every structural defect is an
    ``INVALID_REQUEST`` *before* the engine is touched; the per-property
    kind/shape/unit check against the engine's own metadata still happens in
    the writer, exactly as it does for the mapping form.
    """
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ExecutionContractError(
            "INVALID_REQUEST",
            f"{label} must be an object of property assignments or an array of {{name, value}} rows (PropertySet)",
        )
    definition: dict[str, Any] = {}
    for index, row in enumerate(value):
        row_label = f"{label}[{index}]"
        if not isinstance(row, Mapping):
            raise ExecutionContractError(
                "INVALID_REQUEST", f"{row_label} must be an object with name and value"
            )
        reject_unknown_keys(row, PROPERTY_ROW_FIELDS, row_label)
        name = row.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ExecutionContractError(
                "INVALID_REQUEST", f"{row_label}.name must be a non-empty string"
            )
        if "value" not in row:
            raise ExecutionContractError("INVALID_REQUEST", f"{row_label} is missing required field 'value'")
        if name in definition:
            raise ExecutionContractError(
                "INVALID_REQUEST", f"{label} assigns property {name!r} more than once"
            )
        definition[name] = typed_value_from_wire(row["value"], f"{row_label}.value")
    return definition


def property_row(name: str, value: Any, schema: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    """One validated ``property_set`` row from a definition value.

    An already-typed value (the catalogue row form) is checked against the
    property's own metadata instead of being re-derived from the JSON type, so
    a row array and the equivalent mapping form take the same path through the
    frozen G2 write discipline.
    """
    if is_typed_value(value):
        return {"name": name, "value": validate_typed_value(value, expected=schema)}
    return {"name": name, "value": typed_value_from_json(value, schema, label=label)}


def require_string(value: Any, label: str, *, pattern: re.Pattern[str] | None = None,
                   max_length: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must be a non-empty string")
    if len(value) > max_length:
        raise ExecutionContractError("INVALID_REQUEST", f"{label} exceeds {max_length} characters")
    if pattern is not None and not pattern.match(value):
        raise ExecutionContractError("INVALID_REQUEST", f"{label} is not a valid COMSOL tag")
    return value


def optional_string(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return require_string(value, label)


def require_string_array(value: Any, label: str, *, allow_empty: bool = False,
                         item_pattern: re.Pattern[str] | None = None) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or isinstance(value, Mapping):
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must be an array of strings")
    items = list(value)
    if not items and not allow_empty:
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must not be empty")
    out: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, str) or not item.strip():
            raise ExecutionContractError("INVALID_REQUEST", f"{label}[{index}] must be a non-empty string")
        if item_pattern is not None and not item_pattern.match(item):
            raise ExecutionContractError("INVALID_REQUEST", f"{label}[{index}] is not a valid COMSOL name")
        out.append(item)
    if len(set(out)) != len(out):
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must not contain duplicates")
    return out


def require_int(value: Any, label: str, *, minimum: int | None = None,
                maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must be <= {maximum}")
    return value


def require_bool(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must be a boolean")
    return value


def require_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must be finite")
    return number


def require_entity_id_array(value: Any, label: str, *, allow_empty: bool = False) -> list[int]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or isinstance(value, Mapping):
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must be an array of entity ids")
    items = list(value)
    if not items and not allow_empty:
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must not be empty")
    out: list[int] = []
    for index, item in enumerate(items):
        if isinstance(item, bool) or not isinstance(item, int) or item < 1:
            raise ExecutionContractError(
                "INVALID_REQUEST",
                f"{label}[{index}] must be a positive entity id (entity ids are 1-based and are never array indices)",
            )
        out.append(item)
    if len(set(out)) != len(out):
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must not contain duplicates")
    return sorted(out)


def reject_unknown_keys(payload: Mapping[str, Any], allowed: Iterable[str], label: str) -> None:
    unknown = sorted(set(payload) - set(allowed) - ENVELOPE_FIELDS)
    if unknown:
        raise ExecutionContractError("INVALID_REQUEST", f"{label} has unsupported fields: {unknown}")


def operation_arguments(arguments: Any, allowed: Iterable[str], required: Iterable[str] = (),
                        label: str = "arguments") -> dict[str, Any]:
    """Validate the operation body: envelope fields pass through, unknown fail closed."""
    payload = require_mapping(arguments, label)
    reject_unknown_keys(payload, allowed, label)
    missing = [name for name in required if payload.get(name) is None]
    if missing:
        raise ExecutionContractError("INVALID_REQUEST", f"{label} is missing required fields: {sorted(missing)}")
    return payload


def validate_tag(value: Any, label: str = "tag") -> str:
    return require_string(value, label, pattern=TAG_PATTERN, max_length=63)


def quantity(value: Any, label: str) -> dict[str, Any]:
    """Validate a ``Quantity`` (value + unit). Units are never converted (R01)."""
    payload = require_mapping(value, label)
    reject_unknown_keys(payload, ("value", "unit"), label)
    if "value" not in payload or "unit" not in payload:
        raise ExecutionContractError("INVALID_REQUEST", f"{label} requires value and unit")
    number = require_number(payload["value"], f"{label}.value")
    unit = require_string(payload["unit"], f"{label}.unit", max_length=64)
    return {"value": number, "unit": unit}


# ---------------------------------------------------------------------------
# Node resolution helpers
# ---------------------------------------------------------------------------


def bound_model(worker: Any, model_tag: str) -> Any:
    return _bound_model(worker, model_tag)


def resolve_path(worker: Any, model_tag: str, path: Any, *, label: str = "path") -> tuple[dict[str, Any], Any]:
    """Resolve a wire NodePath and return (canonical path dict, node)."""
    if not isinstance(path, Mapping):
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must be a NodePath object")
    parsed = NodePath.from_wire(path)
    node = resolve_node_path(bound_model(worker, model_tag), parsed)
    return parsed.as_dict(), node


def accessor_container(worker: Any, model_tag: str, parent_path: Any, collection: str) -> Any:
    """Resolve the list node that owns ``collection`` under ``parent_path``."""
    spec = collection_spec(collection)
    _, node = resolve_path(worker, model_tag, parent_path, label="parent_path")
    return _call(node, spec["collection"])


def child_node(worker: Any, model_tag: str, parent_path: Any, collection: str, tag: Any) -> Any:
    """Resolve one child node through the parent's documented accessor.

    ``model.param(<ptag>)``, ``model.variable(<tag>)``, ``model.func(<tag>)``
    and ``component.selection(<tag>)`` are the Programming Reference accessors;
    ``AbstractModelImpl``/``ModelNodeImpl`` implement them (javap of
    ``plugins/com.comsol.model_1.0.0.jar``).  A *list* node only offers
    ``tags()/get(tag)/create(...)`` (javap ``ModelEntityList``), so this helper
    always goes through the parent, exactly like ``resolve_node_path``.
    """
    spec = collection_spec(collection)
    _, parent = resolve_path(worker, model_tag, parent_path, label="parent_path")
    return _call(parent, spec["accessor"], tag)


def node_tags(worker: Any, model_tag: str, parent_path: Any, collection: str) -> list[str]:
    container = accessor_container(worker, model_tag, parent_path, collection)
    return tag_list(container, collection_spec(collection))


def collection_list_node(container: Any, spec: Mapping[str, Any] | None = None) -> Any:
    """The ``ModelEntityList`` that owns ``collection``.

    Parameter groups are the one verified case where the tag list lives on a
    sub-list: ``model.param().group()`` (``ModelParam.group()`` describes itself
    as "the list of the parameter groups" while the list itself declares no
    groups), so tag reads *and* removals must go through it - ``ModelParam``'s
    own ``remove(String)`` removes a *parameter*, not a group.
    """
    node = container
    if spec is not None and spec.get("list_accessor"):
        node = _call(node, str(spec["list_accessor"]))
    return node


def tag_list(container: Any, spec: Mapping[str, Any] | None = None) -> list[str]:
    """Read a verifiable tag list, honouring the parameter-group list accessor."""
    node = collection_list_node(container, spec)
    raw = _call(node, "tags")
    if not isinstance(raw, (list, tuple)):
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "COMSOL tags() did not return a list")
    if not all(isinstance(item, str) for item in raw):
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "COMSOL tags() returned non-string entries")
    return [str(item) for item in raw]


def node_type(node: Any) -> str | None:
    """Best-effort ``getType()`` readback; ``None`` when the node has no such API."""
    try:
        value = getattr(node, "getType")()
    except Exception:
        return None
    return str(value) if value is not None else None


def call_probe(node: Any, method: str, *args: Any) -> dict[str, Any]:
    """Call an optional accessor, reporting - never hiding - a failure.

    A method the worker allow-list rejects surfaces as ``METHOD_REJECTED``;
    ``allowlist_entry_required`` records that so the caller can be told which
    worker allow-list entry the path needs instead of silently reporting
    "no data".
    """
    try:
        return {"ok": True, "value": getattr(node, method)(*args), "error": None}
    except Exception as exc:  # noqa: BLE001 - probe must not raise
        return {
            "ok": False,
            "value": None,
            "error": {
                "code": error_code_of(exc),
                "message": f"COMSOL {method} probe failed: {type(exc).__name__}",
                "allowlist_entry_required": method if allowlist_rejected(exc) else None,
            },
        }


def error_code_of(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code
    return _worker_failure_code(exc) or "ENGINE_CALL_FAILED"


def allowlist_rejected(exc: BaseException) -> bool:
    """True when the worker refused the method because of its allow-list."""
    if _worker_failure_code(exc) in {"METHOD_REJECTED", "METHOD_NOT_ALLOWED"}:
        return True
    text = f"{exc}"
    return "METHOD_REJECTED" in text or "METHOD_NOT_ALLOWED" in text


def describe_engine_failure(exc: BaseException, method: str) -> dict[str, Any]:
    # The worker's own message carries the engine's exception text; keep a
    # bounded copy so an engine refusal stays diagnosable instead of being
    # flattened into a bare exception-class name.
    detail = str(exc)
    if len(detail) > 600:
        detail = detail[:600] + "…(truncated)"
    return {
        "code": error_code_of(exc),
        "message": f"COMSOL {method} call failed: {type(exc).__name__}: {detail}",
        "allowlist_entry_required": method if allowlist_rejected(exc) else None,
    }


# ---------------------------------------------------------------------------
# Node create / remove with readback
# ---------------------------------------------------------------------------


def path_with_segment(parent_path: Any, collection: str, tag: str) -> dict[str, Any]:
    parsed = NodePath.from_wire(parent_path)
    segments = [segment.as_dict() for segment in parsed.segments]
    segments.append({"collection": collection, "tag": tag})
    return NodePath.from_wire({"segments": segments}).as_dict()


def split_parent_path(path: Any, *, label: str = "path") -> tuple[dict[str, Any], str, str]:
    """Split a node path into (parent path, collection, tag)."""
    parsed = NodePath.from_wire(path)
    if not parsed.segments:
        raise ExecutionContractError("INVALID_NODE_PATH", f"{label} must point at a collection node")
    last = parsed.segments[-1]
    if last.accessor is not None or last.collection is None or last.tag is None:
        raise ExecutionContractError(
            "INVALID_NODE_PATH", f"{label} must end in a collection+tag segment"
        )
    parent = NodePath(tuple(parsed.segments[:-1])).as_dict()
    return parent, str(last.collection), str(last.tag)


def node_create(worker: Any, model_tag: str, parent_path: Any, collection: str, tag: Any,
                type_id: Any = None) -> dict[str, Any]:
    """Create a node with pre-write existence/type checks and post-write readback.

    Returns the created node's data dict::

        {"path": NodePath, "parent_path": NodePath, "collection": str, "tag": str,
         "type_id": str|None, "type_readback": str|None, "created": True,
         "readback": {"tags": [...], "type": ...}}

    Raises ``TAG_CONFLICT`` when the tag already exists with the same type and
    ``TYPE_CONFLICT`` when it exists with a different (verified) type.  Both are
    raised *before* any mutation.
    """
    spec = collection_spec(collection)
    tag = validate_tag(tag)
    if tag in spec["reserved_tags"]:
        raise ExecutionContractError("INVALID_REQUEST", f"tag {tag!r} is reserved for {spec['display']}")
    if spec["arity"] == 2:
        if type_id is None:
            raise ExecutionContractError(
                "INVALID_REQUEST", f"{spec['display']} requires a type_id string"
            )
        type_id = require_string(type_id, "type_id", max_length=64)
        if type_id not in spec["type_ids"]:
            raise ExecutionContractError(
                "API_UNSUPPORTED",
                f"{type_id!r} is not in the verified COMSOL 6.4 {collection} type vocabulary: "
                f"{sorted(spec['type_ids'])}",
            )
    else:
        if type_id is not None:
            raise ExecutionContractError(
                "INVALID_REQUEST",
                f"{spec['display']} does not accept a type_id (the verified COMSOL 6.4 create call "
                f"takes a tag only)",
            )
        type_id = None

    container = accessor_container(worker, model_tag, parent_path, collection)
    existing = tag_list(container, spec)
    if tag in existing:
        current = node_type(child_node(worker, model_tag, parent_path, collection, tag)) if spec["type_readback"] else None
        if spec["type_readback"] and current is not None and current != type_id:
            raise ExecutionContractError(
                "TYPE_CONFLICT",
                f"{spec['display']} {tag!r} already exists with type {current!r} (requested {type_id!r})",
            )
        raise ExecutionContractError(
            "TAG_CONFLICT", f"{spec['display']} {tag!r} already exists"
        )

    if spec["arity"] == 2:
        _call(container, "create", tag, type_id)
    else:
        _call(container, "create", tag)

    readback_tags = tag_list(container, spec)
    if tag not in readback_tags:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            f"{spec['display']} {tag!r} was created but the post-create tag readback does not show it",
        )
    type_readback = None
    if spec["type_readback"]:
        type_readback = node_type(child_node(worker, model_tag, parent_path, collection, tag))
        if type_readback is not None and type_readback != type_id:
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN",
                f"{spec['display']} {tag!r} readback type {type_readback!r} does not match the "
                f"requested type {type_id!r}",
            )
    return {
        "path": path_with_segment(parent_path, collection, tag),
        "parent_path": NodePath.from_wire(parent_path).as_dict(),
        "collection": collection,
        "tag": tag,
        "type_id": type_id,
        "type_readback": type_readback,
        "created": True,
        "readback": {"tags": readback_tags, "type": type_readback},
    }


def node_remove(worker: Any, model_tag: str, parent_path: Any, collection: str, tag: Any) -> dict[str, Any]:
    """Remove a node with existence check and post-remove tag readback."""
    spec = collection_spec(collection)
    tag = validate_tag(tag)
    if tag in spec["reserved_tags"]:
        raise ExecutionContractError(
            "INVALID_REQUEST", f"tag {tag!r} is reserved and cannot be removed"
        )
    container = accessor_container(worker, model_tag, parent_path, collection)
    before = tag_list(container, spec)
    if tag not in before:
        raise ExecutionContractError("NODE_NOT_FOUND", f"{spec['display']} {tag!r} does not exist")
    target = child_node(worker, model_tag, parent_path, collection, tag)
    type_readback = node_type(target) if spec["type_readback"] else None
    _call(collection_list_node(container, spec), "remove", tag)
    after = tag_list(container, spec)
    if tag in after:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            f"{spec['display']} {tag!r} was removed but the post-remove tag readback still shows it",
        )
    return {
        "path": path_with_segment(parent_path, collection, tag),
        "parent_path": NodePath.from_wire(parent_path).as_dict(),
        "collection": collection,
        "tag": tag,
        "type_id": type_readback,
        "removed": True,
        "readback": {"tags": after},
    }


# ---------------------------------------------------------------------------
# Selection specs
# ---------------------------------------------------------------------------


def validate_selection_spec(spec: Any, *, label: str = "selection",
                            allowed_kinds: Sequence[str] = SELECTION_KINDS) -> dict[str, Any]:
    """Validate a ``common.schema.json#/$defs/SelectionSpec`` and its per-kind fields.

    The per-kind requirements are the ones this layer can honour with verified
    API calls; anything else is rejected here, before the first write.
    """
    payload = require_mapping(spec, label)
    reject_unknown_keys(payload, SELECTION_SPEC_FIELDS, label)
    kind = payload.get("kind")
    if not isinstance(kind, str) or not kind:
        raise ExecutionContractError("INVALID_REQUEST", f"{label}.kind is required")
    if kind not in SELECTION_KINDS:
        raise ExecutionContractError(
            "INVALID_REQUEST", f"{label}.kind must be one of {list(SELECTION_KINDS)}"
        )
    if kind not in allowed_kinds:
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            f"{label}.kind={kind!r} has no verified offline path in this operation "
            f"(supported here: {list(allowed_kinds)})",
        )
    out: dict[str, Any] = {"kind": kind}
    for field in ("component", "geometry", "tag"):
        if field in payload:
            out[field] = require_string(payload[field], f"{label}.{field}", max_length=63)
    if "entity_dimension" in payload:
        out["entity_dimension"] = require_int(
            payload["entity_dimension"], f"{label}.entity_dimension", minimum=0, maximum=3
        )
    if "entities" in payload:
        out["entities"] = require_entity_id_array(payload["entities"], f"{label}.entities")
    if "object_tags" in payload:
        out["object_tags"] = require_string_array(payload["object_tags"], f"{label}.object_tags")
    if "query" in payload:
        out["query"] = require_mapping(payload["query"], f"{label}.query")
    if "geometry_revision" in payload:
        out["geometry_revision"] = require_int(
            payload["geometry_revision"], f"{label}.geometry_revision", minimum=0
        )
    if kind == "named":
        if "component" not in out:
            raise ExecutionContractError("INVALID_REQUEST", f"{label}: kind 'named' requires component")
        if "tag" not in out:
            raise ExecutionContractError("INVALID_REQUEST", f"{label}: kind 'named' requires tag")
    if kind == "explicit":
        if "entities" not in out:
            raise ExecutionContractError("INVALID_REQUEST", f"{label}: kind 'explicit' requires entities")
    if kind == "all":
        for field in ("component", "geometry", "entity_dimension"):
            if field not in out:
                raise ExecutionContractError(
                    "INVALID_REQUEST", f"{label}: kind 'all' requires {field}"
                )
    if kind == "spatial":
        for field in ("component", "geometry", "entity_dimension", "query"):
            if field not in out:
                raise ExecutionContractError(
                    "INVALID_REQUEST", f"{label}: kind 'spatial' requires {field}"
                )
    if kind == "objects":
        for field in ("component", "geometry", "object_tags"):
            if field not in out:
                raise ExecutionContractError(
                    "INVALID_REQUEST", f"{label}: kind 'objects' requires {field}"
                )
    if kind == "inherited" and "component" not in out:
        raise ExecutionContractError("INVALID_REQUEST", f"{label}: kind 'inherited' requires component")
    return out


def geometry_node(worker: Any, model_tag: str, component: str, geometry: str) -> Any:
    model = bound_model(worker, model_tag)
    comp = _call(model, "component", component)
    return _call(comp, "geom", geometry)


def component_node(worker: Any, model_tag: str, component: str) -> Any:
    return _call(bound_model(worker, model_tag), "component", component)


def geometry_length_unit(geometry: Any) -> str | None:
    """``GeomSequence.lengthUnit()`` - the unit of every coordinate this layer reports."""
    probe = call_probe(geometry, "lengthUnit")
    return str(probe["value"]) if probe["ok"] and probe["value"] is not None else None


def geometry_sdim(geometry: Any) -> int | None:
    probe = call_probe(geometry, "getSDim")
    value = probe["value"]
    if probe["ok"] and isinstance(value, int) and not isinstance(value, bool):
        return int(value)
    return None


def measure_selection(component: Any, entity_dimension: int, entities: Sequence[int] | None) -> tuple[Any, Any]:
    """Return ``(measure node, mesh selection)`` positioned on ``entity_dimension``.

    ``ModelNode.measure()`` -> ``GeomMeasureFinal`` measures the *finalized*
    geometry (``measureFinal()`` is deprecated in favour of
    ``component.measure()``).  Its selection is a ``MeshSelection``, which
    inherits ``geom(int)/set(int...)/all()/entities()`` from
    ``Selection``.  This state belongs to the measurement tool, not to a model
    feature: every operation that touches it is required to be isolated.
    """
    measure = _call(component, "measure")
    selection = _call(measure, "selection")
    _call(selection, "geom", entity_dimension)
    if entities is not None:
        _call(selection, "set", [int(item) for item in entities])
    return measure, selection


def all_entities_of_dimension(component: Any, entity_dimension: int) -> list[int]:
    """Enumerate every entity of a dimension using the measure tool (``all()``).

    Read-only with respect to the model tree; the measurement tool's transient
    selection is the only state touched.
    """
    _, selection = measure_selection(component, entity_dimension, None)
    _call(selection, "all")
    raw = _call(selection, "entities")
    return require_entity_id_array(raw, "entities()", allow_empty=True)


def resolve_selection_entities(worker: Any, model_tag: str, spec: Any) -> dict[str, Any]:
    """Resolve a SelectionSpec to a concrete entity list through verified reads.

    ``named``  - ``component.selection(<tag>)`` (a ``SelectionFeature``), or the
                 geometry-scoped ``component.geom(<gtag>).selection(<tag>)``
                 (a ``GeomObjectSelectionFeature``, whose entities are read per
                 object tag because that interface exposes only
                 ``entities(String)``).
    ``explicit`` - the caller's entity list, echoed with its dimension.  Entity
                 ids are never treated as array indices and are *not* range
                 checked here; ``selection.validate`` can do that inside the
                 geometry.
    ``all``    - every entity of the dimension, enumerated with the measurement
                 tool (see :func:`all_entities_of_dimension`).

    ``spatial``, ``objects`` and ``inherited`` are rejected with a documented
    reason instead of a guessed implementation.
    """
    resolved = validate_selection_spec(spec, allowed_kinds=RESOLVABLE_SELECTION_KINDS)
    kind = resolved["kind"]
    if kind == "named":
        component = resolved["component"]
        comp = component_node(worker, model_tag, component)
        geometry = resolved.get("geometry")
        if geometry:
            geom = _call(comp, "geom", geometry)
            node = _call(geom, "selection", resolved["tag"])
            objects_probe = call_probe(node, "objects")
            objects = [str(item) for item in objects_probe["value"]] if objects_probe["ok"] and objects_probe["value"] else []
            if not objects:
                single = call_probe(node, "object")
                if single["ok"] and isinstance(single["value"], str) and single["value"]:
                    objects = [single["value"]]
            object_entities: set[int] = set()
            if not objects:
                return {
                    "kind": kind,
                    "component": component,
                    "geometry": geometry,
                    "selection_tag": resolved["tag"],
                    "entity_dimension": resolved.get("entity_dimension"),
                    "entities": [],
                    "entity_count": 0,
                    "source": "geom_named_selection",
                    "notes": [
                        "the geometry-scoped selection reported no object tags; entities are read per "
                        "object (GeomObjectSelection.entities(String)), so the list is empty"
                    ],
                }
            for object_tag in objects:
                rows = _call(node, "entities", object_tag)
                object_entities.update(require_entity_id_array(rows, "entities(object)", allow_empty=True))
            dimension = resolved.get("entity_dimension")
            if dimension is None:
                probe = call_probe(node, "dim")
                if probe["ok"] and isinstance(probe["value"], int) and not isinstance(probe["value"], bool):
                    dimension = int(probe["value"])
            return {
                "kind": kind,
                "component": component,
                "geometry": geometry,
                "selection_tag": resolved["tag"],
                "entity_dimension": dimension,
                "objects": objects,
                "entities": sorted(object_entities),
                "entity_count": len(object_entities),
                "source": "geom_named_selection",
            }
        node = _call(comp, "selection", resolved["tag"])
        return {
            "kind": kind,
            "component": component,
            "selection_tag": resolved["tag"],
            "entity_dimension": selection_dimension(node, resolved.get("entity_dimension")),
            "entities": require_entity_id_array(_call(node, "entities"), "entities()", allow_empty=True),
            "entity_count": None,
            "source": "component_named_selection",
            "geometry": probe_geometry_tag(node),
        }
    if kind == "explicit":
        entities = resolved["entities"]
        return {
            "kind": kind,
            "component": resolved.get("component"),
            "geometry": resolved.get("geometry"),
            "entity_dimension": resolved.get("entity_dimension"),
            "entities": entities,
            "entity_count": len(entities),
            "source": "caller_explicit",
            "notes": [
                "explicit entity ids are validated as positive 1-based ids, duplicate-free and sorted; "
                "use selection.validate to check them inside the geometry"
            ],
        }
    # kind == "all"
    component = resolved["component"]
    comp = component_node(worker, model_tag, component)
    dimension = resolved["entity_dimension"]
    entities = all_entities_of_dimension(comp, dimension)
    return {
        "kind": kind,
        "component": component,
        "geometry": resolved["geometry"],
        "entity_dimension": dimension,
        "entities": entities,
        "entity_count": len(entities),
        "source": "measure_tool_all",
        "notes": [
            "entities come from the measurement tool's finalized-geometry enumeration "
            "(component.measure().selection().geom(dim).all())"
        ],
    }


def selection_dimension(node: Any, fallback: int | None = None) -> int | None:
    probe = call_probe(node, "dim")
    value = probe["value"]
    if probe["ok"] and isinstance(value, int) and not isinstance(value, bool):
        return int(value)
    return fallback


def probe_geometry_tag(node: Any) -> str | None:
    probe = call_probe(node, "geom")
    if probe["ok"] and isinstance(probe["value"], str) and probe["value"]:
        return probe["value"]
    return None


def apply_local_selection(node: Any, worker: Any, model_tag: str, component: str | None,
                          spec: Any, *, owner: Any = None) -> dict[str, Any]:
    """Bind a SelectionSpec to a *local* selection (``Selection`` interface).

    ``Selection.named(String)`` points a local selection at a named selection.
    For a model-global container the Programming Reference requires the model
    component node to be set first (``model.variable(<tag>).model(<mtag>)``);
    when ``component`` is given here that call is made explicitly and its
    readback is reported.  ``explicit`` uses ``geom(<gtag>, dim)`` +
    ``set(int...)``; ``all`` uses ``geom(<gtag>, dim)`` + ``all()`` (both only
    supported for the Explicit selection type, as documented); ``inherited``
    uses ``inherit(true)``.

    ``spatial`` and ``objects`` are rejected: a local ``Selection`` has no
    verified object-scoped or coordinate-query setter in this layer.
    """
    resolved = validate_selection_spec(spec, allowed_kinds=BINDABLE_SELECTION_KINDS)
    kind = resolved["kind"]
    applied: list[dict[str, Any]] = []
    model_binding: dict[str, Any] | None = None
    if component is not None:
        # ``ModelEntity.model()`` / ``model(String)`` (javap) - the Programming
        # Reference requires the model component to be set before a local
        # selection is assigned, in particular for model-global containers.
        # The documented call site is the owning expression entity
        # (``model.variable(<tag>).model(<mtag>)``); both it and the local
        # selection chain to ``ModelEntity``.
        entity = owner if owner is not None else node
        current = call_probe(entity, "model")
        if not (current["ok"] and current["value"] == component):
            _call(entity, "model", component)
            model_binding = {
                "method": "model",
                "requested": component,
                "readback": call_probe(entity, "model")["value"],
            }
    if kind == "named":
        _call(node, "named", resolved["tag"])
        readback = call_probe(node, "named")
        if not (readback["ok"] and readback["value"] == resolved["tag"]):
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN",
                f"selection().named() readback did not confirm {resolved['tag']!r}",
            )
        applied.append({"method": "named", "requested": resolved["tag"], "readback": readback["value"]})
    elif kind == "explicit":
        geometry = resolved.get("geometry")
        dimension = resolved.get("entity_dimension")
        if dimension is None:
            raise ExecutionContractError(
                "INVALID_REQUEST", "selection.kind 'explicit' requires entity_dimension to bind a local selection"
            )
        if geometry:
            _call(node, "geom", geometry, dimension)
        else:
            _call(node, "geom", dimension)
        entities = resolved["entities"]
        _call(node, "set", list(entities))
        readback = _call(node, "entities")
        got = require_entity_id_array(readback, "selection.entities()", allow_empty=True)
        if sorted(got) != sorted(entities):
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN",
                "local selection entity readback does not match the requested entity list",
            )
        applied.append({"method": "geom/set", "entities": entities, "readback": got})
    elif kind == "all":
        geometry = resolved["geometry"]
        dimension = resolved["entity_dimension"]
        _call(node, "geom", geometry, dimension)
        _call(node, "all")
        readback = require_entity_id_array(_call(node, "entities"), "selection.entities()", allow_empty=True)
        applied.append({"method": "geom/all", "readback": readback})
    else:  # inherited
        _call(node, "inherit", True)
        readback = call_probe(node, "isInheriting")
        value = readback["value"] if readback["ok"] else None
        if value is False:
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN", "selection().inherit(true) readback reports isInheriting() == false"
            )
        applied.append({"method": "inherit", "requested": True, "readback": value})
    return {
        "kind": kind,
        "applied": applied,
        "model_binding": model_binding,
        "entity_state": selection_state(node),
    }


def selection_state(node: Any) -> dict[str, Any]:
    """Report the node's current selection wiring without inventing values."""
    named = call_probe(node, "named")
    inheriting = call_probe(node, "isInheriting")
    entities = call_probe(node, "entities")
    return {
        "named": named["value"] if named["ok"] else None,
        "named_error": None if named["ok"] else named["error"],
        "is_inheriting": inheriting["value"] if inheriting["ok"] else None,
        "entities": list(entities["value"]) if entities["ok"] and isinstance(entities["value"], (list, tuple)) else None,
        "entities_error": None if entities["ok"] else entities["error"],
        "dimension": selection_dimension(node),
        "geometry": probe_geometry_tag(node),
    }


# ---------------------------------------------------------------------------
# Expression collections (parameters and variables share ExpressionBase)
# ---------------------------------------------------------------------------


def expression_names(node: Any) -> list[str]:
    raw = _call(node, "varnames")
    return require_string_array(raw, "varnames()", allow_empty=True)


def expression_read(node: Any, name: str, label: str = "name") -> dict[str, Any]:
    value = _call(node, "get", name)
    description = call_probe(node, "descr", name)
    unit = call_probe(node, "evaluateUnit", name)
    return {
        "name": name,
        "expression": value if isinstance(value, str) else typed_value_from_engine(value),
        "description": description["value"] if description["ok"] else None,
        "description_error": None if description["ok"] else description["error"],
        "unit": unit["value"] if unit["ok"] else None,
        "unit_error": None if unit["ok"] else unit["error"],
    }


def expression_write(node: Any, name: str, expression: str, description: str | None = None,
                     *, label: str = "expression") -> dict[str, Any]:
    """Write one parameter/variable and prove the round trip by text readback.

    ``ExpressionBase`` stores both value and description as Java String text;
    the G2 textual comparison rule (expression <-> string, byte-equal text) is
    reused, so a COMSOL-normalised expression is reported as a mismatch rather
    than silently accepted.
    """
    if description is not None:
        _call(node, "set", name, expression, description)
    else:
        _call(node, "set", name, expression)
    returned = _call(node, "get", name)
    comparison = _typed_readback_comparison(
        typed_value_from_engine(expression, kind="expression"),
        typed_value_from_engine(returned, kind="expression"),
    )
    if not comparison["matched"]:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            f"{label} {name!r} readback did not match the requested text (rule: {comparison['rule']})",
        )
    recorded: dict[str, Any] = {
        "name": name,
        "expression": expression,
        "readback": returned,
        "readback_match": True,
        "comparison": comparison,
    }
    if description is not None:
        readback = call_probe(node, "descr", name)
        recorded["description"] = description
        recorded["description_readback"] = readback["value"] if readback["ok"] else None
        if not (readback["ok"] and readback["value"] == description):
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN",
                f"{label} {name!r} description readback did not match the requested text",
            )
    unit = call_probe(node, "evaluateUnit", name)
    recorded["unit"] = unit["value"] if unit["ok"] else None
    recorded["unit_error"] = None if unit["ok"] else unit["error"]
    return recorded


def expression_remove(node: Any, name: str) -> dict[str, Any]:
    before = expression_names(node)
    if name not in before:
        raise ExecutionContractError("NAME_NOT_FOUND", f"{name!r} does not exist in this collection")
    _call(node, "remove", name)
    after = expression_names(node)
    if name in after:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN", f"{name!r} was removed but the readback still lists it"
        )
    return {"name": name, "removed": True, "readback": {"names": after}}


# ---------------------------------------------------------------------------
# Property definitions (functions / selections) -> typed writes
# ---------------------------------------------------------------------------


def typed_value_from_json(value: Any, schema: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    """Convert a JSON definition value to a typed value *without* guessing.

    The target kind/rank come from the property's authoritative engine
    metadata (``getValueType``), never from the JSON value's Python type.  A
    value that does not fit the declared kind/shape, or that is outside the
    engine's own allowed-value enumeration (``getAllowedPropertyValues``), is
    rejected before the write; no numeric widening, no string->number parsing
    and no unit conversion happens here.
    """
    kind = schema.get("kind")
    rank = schema.get("shape_rank")
    if not isinstance(kind, str) or not isinstance(rank, int):
        raise ExecutionContractError(
            "API_UNSUPPORTED", f"authoritative value metadata is unavailable for property {label!r}"
        )

    def scalar(item: Any, position: str) -> Any:
        if kind == "boolean":
            if type(item) is bool:
                return item
            if isinstance(item, str) and item in {"on", "off"}:
                return item == "on"
            raise ExecutionContractError(
                "PROPERTY_TYPE_MISMATCH",
                f"{label}{position} must be a JSON boolean for this property (a 0/1 integer is not "
                f"accepted; COMSOL reports the boolean enumeration as on/off in metadata)",
            )
        if kind == "int32":
            if isinstance(item, bool) or not isinstance(item, int):
                raise ExecutionContractError(
                    "PROPERTY_TYPE_MISMATCH", f"{label}{position} must be an integer for this property"
                )
            return int(item)
        if kind == "float64":
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise ExecutionContractError(
                    "PROPERTY_TYPE_MISMATCH", f"{label}{position} must be a number for this property"
                )
            number = float(item)
            if not math.isfinite(number):
                raise ExecutionContractError(
                    "PROPERTY_TYPE_MISMATCH", f"{label}{position} must be finite"
                )
            return number
        if kind in {"string", "expression"}:
            if not isinstance(item, str):
                raise ExecutionContractError(
                    "PROPERTY_TYPE_MISMATCH",
                    f"{label}{position} must be a string; this layer never stringifies numbers implicitly",
                )
            return item
        raise ExecutionContractError(
            "API_UNSUPPORTED", f"property {label!r} has unverified value kind {kind!r}"
        )

    def convert(node_value: Any, position: str) -> Any:
        if rank == 0:
            return scalar(node_value, position)
        if not isinstance(node_value, Sequence) or isinstance(node_value, (str, bytes)) or isinstance(node_value, Mapping):
            raise ExecutionContractError(
                "PROPERTY_TYPE_MISMATCH", f"{label}{position} must be an array for this property"
            )
        items = list(node_value)
        if rank == 1:
            return [scalar(item, f"{position}[{index}]") for index, item in enumerate(items)]
        rows = []
        for row_index, row in enumerate(items):
            if not isinstance(row, Sequence) or isinstance(row, (str, bytes)) or isinstance(row, Mapping):
                raise ExecutionContractError(
                    "PROPERTY_TYPE_MISMATCH",
                    f"{label}{position}[{row_index}] must be a row array for this rank-2 property",
                )
            rows.append([scalar(item, f"{position}[{row_index}][{col}]") for col, item in enumerate(list(row))])
        return rows

    data = convert(value, "")
    shape: list[int] = []

    def shape_of(item: Any) -> list[int]:
        if not isinstance(item, Sequence) or isinstance(item, (str, bytes)):
            return []
        items = list(item)
        if not items:
            return [0]
        child = shape_of(items[0])
        for other in items[1:]:
            if shape_of(other) != child:
                raise ExecutionContractError(
                    "PROPERTY_TYPE_MISMATCH", f"{label} must be a rectangular array"
                )
        return [len(items)] + child

    if rank > 0:
        shape = shape_of(data)
    converted = {"kind": kind, "shape": shape, "data": data}
    if schema.get("allowed_values") is not None:
        # The engine's own enumeration is the authority for an enumerated
        # property.  Reuse the G2 property-value contract (same code, same
        # on/off normalisation) so an out-of-vocabulary value is refused while
        # the definition is built, not handed to the setter first.
        return validate_typed_value(converted, expected=schema)
    return converted


def property_rows(node: Any, names: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Read authoritative metadata for the requested properties only."""
    from ._g2_contract import property_schema_from_engine  # local import: shared with G2

    rows: dict[str, dict[str, Any]] = {}
    for name in dict.fromkeys(names):
        rows[name] = property_schema_from_engine(node, name)
    return rows


def validate_definition_keys(definition: Mapping[str, Any], allowed: Iterable[str] | None, label: str) -> None:
    reject_unknown_keys(definition, allowed if allowed is not None else (), label)


def definition_properties(node: Any, definition: Mapping[str, Any], allowed: Iterable[str] | None,
                          *, label: str) -> list[dict[str, Any]]:
    """Turn a definition object into a validated ``property_set`` payload.

    Property names are checked against the node type's documented table
    (``allowed``) *before* any engine metadata call, and each value's kind and
    rank are taken from the engine's own property metadata.
    """
    names = [name for name in definition if name not in ENVELOPE_FIELDS]
    if allowed is None:
        if names:
            raise ExecutionContractError(
                "API_UNSUPPORTED",
                f"{label} does not accept properties: the documented property table for this type was not "
                f"available offline, so {sorted(names)} is rejected before the write",
            )
        return []
    reject_unknown_keys(definition, allowed, label)
    if not names:
        return []
    rows = property_rows(node, names)
    payload: list[dict[str, Any]] = []
    for name in names:
        row = rows.get(name) or {}
        if row.get("metadata_status") != "KNOWN":
            raise ExecutionContractError(
                "API_UNSUPPORTED",
                f"authoritative value metadata is unavailable for property {name!r} on this COMSOL build",
            )
        payload.append(property_row(name, definition[name], row, label=name))
    return payload


def property_read_rows(node: Any, names: Sequence[str]) -> dict[str, Any]:
    """Read a bounded set of property values, reporting per-name failures."""
    out: dict[str, Any] = {}
    for name in names:
        probe = call_probe(node, "getString", name)
        if probe["ok"]:
            out[name] = {"value": probe["value"], "error": None}
            continue
        numeric = call_probe(node, "getDouble", name)
        if numeric["ok"]:
            out[name] = {"value": numeric["value"], "error": None, "source_getter": "getDouble"}
            continue
        out[name] = {"value": None, "error": probe["error"]}
    return out


# ---------------------------------------------------------------------------
# Miscellaneous helpers
# ---------------------------------------------------------------------------


def entity_list_hash(entities: Sequence[int]) -> str:
    payload = json.dumps(list(entities), separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def argument_revision(arguments: Mapping[str, Any]) -> int | None:
    """Echo the control plane's model revision when the dispatcher injects it."""
    value = arguments.get("model_revision")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExecutionContractError("INVALID_REQUEST", "model_revision must be an integer")
    return int(value)


__all__ = [
    "BINDABLE_SELECTION_KINDS",
    "COLLECTION_SPECS",
    "ENVELOPE_FIELDS",
    "FUNCTION_TYPE_IDS",
    "FUNCTION_TYPE_PROPERTIES",
    "MEASURE_METRICS",
    "PROPERTY_ROW_FIELDS",
    "RESOLVABLE_SELECTION_KINDS",
    "SELECTION_KINDS",
    "SELECTION_REGION_PROPERTIES",
    "SELECTION_TYPE_IDS",
    "SPATIAL_CONDITIONS",
    "SPATIAL_SELECTION_TYPES",
    "TYPED_VALUE_FIELDS",
    "accessor_container",
    "all_entities_of_dimension",
    "allowlist_rejected",
    "apply_local_selection",
    "argument_revision",
    "bound_model",
    "call_probe",
    "child_node",
    "collection_spec",
    "component_node",
    "definition_properties",
    "describe_engine_failure",
    "entity_list_hash",
    "error_code_of",
    "expression_names",
    "expression_read",
    "expression_remove",
    "expression_write",
    "geometry_length_unit",
    "geometry_node",
    "geometry_sdim",
    "is_typed_value",
    "measure_selection",
    "node_create",
    "node_remove",
    "node_tags",
    "node_type",
    "operation_arguments",
    "path_with_segment",
    "probe_geometry_tag",
    "property_definition",
    "property_read_rows",
    "property_row",
    "property_rows",
    "quantity",
    "reject_unknown_keys",
    "require_bool",
    "require_entity_id_array",
    "require_int",
    "require_mapping",
    "require_number",
    "require_string",
    "require_string_array",
    "resolve_path",
    "resolve_selection_entities",
    "selection_dimension",
    "selection_state",
    "split_parent_path",
    "tag_list",
    "typed_value_from_json",
    "typed_value_from_wire",
    "validate_definition_keys",
    "validate_selection_spec",
    "validate_tag",
]
