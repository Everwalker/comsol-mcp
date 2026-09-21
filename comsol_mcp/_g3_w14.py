"""W14 domain operations: geometry sequences, work planes, CAD import paths,
coordinate systems, pairs and couplings.

Frozen publishing contract (the control plane dispatches through this):

    OPERATIONS: dict[str, Callable[[Any, str, dict], dict]]

Each entry is called as ``fn(worker, model_tag, arguments)``.  It returns the
operation's ``data`` dictionary on success and raises
``comsol_mcp._g2_contract.ExecutionContractError`` for a refusal, a pre-write
validation failure or a failed engine call that could not have changed the
model.  It never wraps its own result in a ``{"success": ...}`` envelope: the
control plane owns that.  A failure *after* the first engine mutation - a
setter ran but the readback did not match, a build raised, a documented
property name turned out to be absent from the node's own enumeration - is
returned as ``data`` with ``ok``/``status``/``partial_change``/
``execution_state_unknown``/``not_executed`` so the caller sees exactly what
happened.  Nothing is retried silently.

op -> COMSOL API -> verification source
---------------------------------------
geometry.sequence_create
    ``component.geom().create(<tag>,<sdim>)`` -> Programming Reference
    ``model.geom()`` ("Creating and Deleting a Geometry"); javap
    ``GeomList.create(String,int)``.  Readback ``getSDim()``/``lengthUnit()``.
geometry.inspect
    ``geom.feature().tags()`` / ``feature(<ftag>).getType()`` / ``label()`` /
    ``active()`` / ``properties()`` / ``getNEntities()`` / ``getBoundingBox()``
    -> Programming Reference "Features for Creating Geometric Primitives",
    "Geometric Entity Counters" (``getNEntities()`` is an int[] whose length is
    2/3/4 for a 1D/2D/3D geometry) and javap ``GeomSequence``/``GeomInfo``.
geometry.feature_create / feature_update / feature_remove
    ``geom.create(<ftag>,<ftype>)`` (Programming Reference "Adding a Geometry
    Feature": *"Feature types are capitalized and case-sensitive, for example
    Rectangle"*), ``feature(<ftag>).set(property,<value>)`` ("Editing a Geometry
    Feature"), ``feature().remove(<ftag>)`` ("Deleting and Disabling Geometry
    Features").  Create types come from the local KB "Geometry Commands" index
    page plus the command pages that were read in full (Rectangle, Block,
    Array, WorkPlane, Import, Finalize/FormUnion+FormAssembly, Move/Copy,
    Compose/Union/Intersection/Difference).
geometry.workplane_create / workplane_edit
    ``geom.create(<ftag>,"WorkPlane")`` + ``feature(<ftag>).geom()`` for the
    nested 2D sequence (Programming Reference WorkPlane page and
    "Geometry Sequences" note: ``model.geom(<gtag>).feature(<ftag>).geom()``
    *"also exists if <ftag> is a work plane feature"*); javap
    ``GeomFeature.geom()``.  The nested sequence is reachable through NodePath
    as ``... feature:<wp> accessor:geom``.
geometry.array_create
    ``geom.create(<ftag>,"Array")`` with the documented table (``type`` in
    linear|rectangular|three-dimensional, ``size`` int, ``fullsize`` double[],
    ``displ`` double[], ``linearsize`` double, ``input`` selection).
geometry.build
    ``geom.run()`` / ``geom.run(<ftag>)`` (Programming Reference "Building
    Geometry Features"; ``run(<ftag>)`` builds ``<ftag>`` and every preceding
    feature and makes it current, ``run()`` also builds Finalize and the
    virtual operations).
geometry.finalize
    ``geom.create("fin","FormUnion"|"FormAssembly")`` + the documented property
    table (``action`` union|assembly, ``imprint``, ``createpairs``,
    ``pairtype``, ``repairtol``...).  "The only allowed tag for the Finalize
    feature is "fin"".
geometry.import
    ``geom.create(<ftag>,"Import")`` + ``set("filename",<path>)``; the
    documented property table differs per detected format (DXF, MPHBIN/MPHTXT
    ``native``, geometry ``sequence``, ``Mesh``).  The engine path is passed
    through verbatim: no normalisation, no globbing, no shell expansion.
geometry.measure
    ``geom.measure()`` -> ``GeomMeasure`` -> ``selection()``
    (``GeomObjectSelection``) + the ``GeomMeasureBase`` getters (javap), and,
    for entity ids, the finalized ``component.measure()`` path already verified
    in W13 (``_g3_common.measure_selection``).
geometry.validate
    composition of the verified reads above plus the documented geometric
    entity counters; a minimum-distance ("design spacing") expectation is
    refused because it would need a ``DistanceMeasurement`` feature whose
    parameter readback has no verified worker path.
definition.component_manage
    ``model.component().create(<tag>[,<type>])`` with the documented type
    vocabulary Component|ExtraDim|MeshComponent, ``remove(<tag>)``,
    ``getType()`` (Programming Reference ``model.component()``).  ``copy`` is
    refused: the Programming Reference page does not document a component copy
    call sequence and the worker allow-list has no ``copy``/``duplicate``.
definition.coordinate_manage
    ``coordSystem().create(<tag>,<gtag>,<type>)`` with the documented type
    vocabulary (Mapping, VectorBase, Rotated, Boundary, Scaling, Cylindrical,
    SystemFromGeometry, PML, InfiniteElement, AbsorbingLayer), plus the
    ``PropFeature`` property surface; ``coord()``/``isLinear()``/
    ``isOrthonormal()`` are reported as unavailable worker methods instead of
    being guessed.
definition.pair_manage
    ``pair().create(<tag>,<type>[,<gtag>])`` with the documented type
    vocabulary Contact|GeneralContact|Identity|SectorSymmetry,
    ``pair().remove(<tag>)``, ``getType()``.  ``source()``/``destination()``/
    ``type()``/``pairName()`` are documented on the pair node but are not in the
    worker method allow-list, so a selection-binding request is refused before
    the first write and the allow-list entry is reported.
definition.coupling_manage
    ``cpl().create(<tag>,<type>[,<gtag>])`` with the documented type vocabulary
    (GeneralExtrusion, LinearExtrusion, BoundarySimilarity, IdentityMapping,
    GeneralProjection, LinearProjection, Integration, Average, Maximum,
    Minimum), ``cpl(<tag>).selection().named(<seltag>)`` / ``set(...)`` for the
    source selection, ``remove(<tag>)`` and the ``PropFeature`` property
    surface (``opname`` is a documented property).

Known, reported gaps (refused before the first write, never guessed):
``axisymmetric`` geometry sequences (the documented setter
``GeomSequence.axisymmetric(boolean)`` is not in the worker allow-list),
component ``copy``/``duplicate``, pair source/destination binding, coordinate
system ``coord()``/``isLinear()``/``isOrthonormal()``/``masterSystem()``
readback, geometry ``object()``/``objectNames()``/``problems()``/
``isBuilt()``/``current()``/``angularUnit()`` readback, per-feature properties
of a type whose documented table was not retrieved offline (the engine's own
``properties()`` enumeration is used as the name authority instead, and a name
the node does not expose is reported as a failure instead of being written).

Worker allow-list additions this module would use if the parent applies them
(read from ``worker_java/PersistentComsolWorker.java`` METHODS; the module
never edits Java): ``axisymmetric``, ``isAxisymmetric``, ``angularUnit``,
``current``, ``isBuilt``, ``status``, ``problems``, ``problem``, ``message``,
``hasError``, ``errors``, ``warnings``, ``object``, ``objects``, ``obj``,
``objectNames``, ``coord``, ``isLinear``, ``isOrthonormal``, ``masterSystem``,
``source``, ``destination``, ``type``, ``pairName``, ``swap``,
``hasAutoSelection``, ``manualSelection``, ``searchMethod``, ``searchDist``,
``copy``, ``duplicate``.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Callable, Mapping, Sequence

from ._g2_contract import ExecutionContractError, NodePath
from ._g2_engine import _call, property_set
from ._g3_common import (
    MEASURE_METRICS,
    all_entities_of_dimension,
    call_probe,
    definition_properties,
    measure_selection,
    node_type,
    node_not_found,
    operation_arguments,
    property_definition,
    property_read_rows,
    reject_unknown_keys,
    require_bool,
    require_int,
    require_mapping,
    require_number,
    require_string,
    require_string_array,
    resolve_path,
    tag_conflict,
    tag_list,
    validate_tag,
)

# ---------------------------------------------------------------------------
# Verified vocabularies
# ---------------------------------------------------------------------------

#: ``geom.create(<ftag>,<ftype>)`` type strings.  Source: the local COMSOL 6.4
#: knowledge base page "Geometry Commands"
#: (``doc/help/.../comsol_api_geom.48.057.html``), whose index lists the
#: case-sensitive command names; multi-command pages are split on ", " and the
#: four ``Import ...`` pages collapse to the single ``Import`` type string
#: because all four are documented as ``create(<ftag>,"Import")`` with the
#: format selected by the ``filename`` property.  ``Finalize`` is the page for
#: the two create types ``FormUnion``/``FormAssembly`` (that page's SYNTAX
#: block is quoted in the module docstring).  The create *shape*
#: (``create(<ftag>,ftype)``, capitalized and case-sensitive) is the documented
#: "Adding a Geometry Feature" rule, and ``GeomSequence.create(String,String)``
#: is javap-verified.
GEOMETRY_FEATURE_TYPE_IDS = frozenset(
    {
        "AdjacentSelection",
        "Array",
        "BallSelection", "BoxSelection", "CylinderSelection", "DiskSelection",
        "BezierPolygon",
        "Block",
        "CentroidMeasurement",
        "Chamfer",
        "Circle",
        "CircularArc",
        "CollapseEdges",
        "CollapseFaces",
        "CollapseFaceRegions",
        "Compose", "Union", "Intersection", "Difference",
        "CompositeDomains",
        "CompositeEdges",
        "CompositeFaces",
        "Cone",
        "ConvertToSolid", "ConvertToSurface", "ConvertToCurve", "ConvertToPoint",
        "CrossSection",
        "CubicBezier",
        "Cylinder",
        "Delete",
        "DistanceMeasurement",
        "ECone",
        "EditObject",
        "Ellipse",
        "Ellipsoid",
        "ExplicitSelection",
        "Extract",
        "Extrude",
        "Fillet",
        "FormAssembly", "FormUnion",
        "Helix",
        "Hexahedron",
        "IgnoreEdges",
        "IgnoreFaces",
        "IgnoreVertices",
        "Import",
        "InterpolationCurve",
        "Interval",
        "LineSegment",
        "LogicalExpressionSelection",
        "MergeEdges",
        "MergeFaces",
        "MergeVertices",
        "MeshControlDomains",
        "MeshControlEdges",
        "MeshControlFaces",
        "MeshControlVertices",
        "Mirror",
        "Move", "Copy",
        "Offset",
        "ParameterCheck",
        "ParametricCurve",
        "ParametricSurface",
        "PartInstance",
        "Partition",
        "PartitionDomains",
        "PartitionEdges",
        "PartitionFaces",
        "Point",
        "Polygon",
        "Pyramid",
        "QuadraticBezier",
        "Rectangle",
        "RemoveDetails",
        "Revolve",
        "RigidTransform",
        "Rotate",
        "Scale",
        "Sphere",
        "Split",
        "Square",
        "Sweep",
        "Tangent",
        "Tetrahedron",
        "Thicken2D",
        "Torus",
        "UnionSelection", "IntersectionSelection", "DifferenceSelection", "ComplementSelection",
        "WorkPlane",
    }
)

#: Geometry feature type strings the offline COMSOL 6.4 knowledge base does NOT
#: quote in a ``geom(<gtag>).create(<ftag>,"<Type>")`` call, so this layer
#: refuses them before the first engine change instead of guessing: they stay
#: visible as refused names with their reason, never as accepted ones.
GEOMETRY_FEATURE_TYPE_IDS_UNVERIFIED = frozenset(
    {
        "CompositeCurve",   # no create() quote in any geometry page
        "FromMesh",         # no create() quote in any geometry page
        "If", "ElseIf", "Else", "EndIf",  # conditional commands: pages exist, no create() quote
    }
)

#: Geometry *sequence* type strings that are not geometry *features*; the
#: Programming Reference creates them as ``model.geom().create(<tag>,"Part",sDim)``
#: (Geometry Parts page), which is a different call than feature creation, so
#: W14 does not accept them as a ``type_id``.
GEOMETRY_SEQUENCE_TYPE_IDS_UNSUPPORTED = frozenset({"Part"})

#: The Finalize feature's tag is fixed by the Programming Reference: "The only
#: allowed tag for the Finalize feature is "fin"".
FINALIZE_TAGS = {"FormUnion": "fin", "FormAssembly": "fin"}

#: Documented property tables for the geometry feature types whose command page
#: was read in full from the local COMSOL 6.4 knowledge base.  A type present
#: here has its property *names* checked before the first write; a type absent
#: from this table falls back to the node's own ``properties()`` enumeration
#: (see ``_feature_properties``).
GEOMETRY_FEATURE_PROPERTIES: dict[str, frozenset[str]] = {
    # comsol_api_geom.48.128.html (Rectangle), Table 3-146 + Table 3-147.
    "Rectangle": frozenset(
        {
            "base", "color", "contributeto", "customcolor", "createpar", "definedby",
            "layer", "layerleft", "layerright", "layertop", "layerbottom", "pos",
            "postype", "posvertex", "rot", "sellayer", "sellayershow", "selresult",
            "selresultshow", "size", "type", "orientdef", "input", "keep",
            "leftmargin", "rightmargin", "bottommargin", "topmargin", "height",
            "width",
        }
    ),
    # comsol_api_geom.48.062.html (Block), Table 3-31 + Table 3-32.
    "Block": frozenset(
        {
            "axis", "axistype", "base", "color", "customcolor", "createpar",
            "definedby", "layer", "layertop", "layerbottom", "layerleft",
            "layerright", "layerfront", "layerback", "size", "pos", "postype",
            "posvertex", "rot", "type", "sellayer", "sellayershow", "selresult",
            "selresultshow", "contributeto", "workplanesrc", "workplane",
        }
    ),
    # comsol_api_geom.48.059.html (Array), Table 3-27.
    "Array": frozenset(
        {
            "displ", "input", "propagatesel", "size", "type", "fullsize",
            "linearsize", "indexattr", "indexattrtag", "indexattrtags",
            "selresult", "selresultshow", "color", "customcolor",
        }
    ),
    # comsol_api_geom.48.143.html (WorkPlane), Tables 3-184..3-195.
    "WorkPlane": frozenset(
        {
            "absrepairtol", "repairtol", "repairtoltype", "unite",
            "showcoincident", "showintersection", "showcoordsys",
            "showprojection", "workplane3d",
            "planetype", "contributeto", "quickplane", "quickorigin",
            "originvertex", "quickaxis", "axisvertex", "displ", "rot",
            "face", "offset", "reverse", "offsettype", "offsetvertex", "origin",
            "faceparallelaxis", "edge", "edgeparallelorigin", "edgeparallelaxis",
            "angle", "adjface", "circedge", "circpoint", "circvertex",
            "circoffset", "normalvector", "normalpoint", "normalcoord",
            "normalvertex", "vertex1", "vertex2", "vertex3", "genpoints",
            "transax2", "transax3", "transaxis", "selresult", "selresultshow",
            "color", "customcolor",
        }
    ),
    # comsol_api_geom.48.090.html (Finalize), Table 3-87.
    "FormUnion": frozenset(
        {
            "absrepairtol", "action", "createpairs", "fastpairdetection",
            "frame", "imprint", "repairtol", "repairtoltype", "pairtype",
            "splitpairs",
        }
    ),
    "FormAssembly": frozenset(
        {
            "absrepairtol", "action", "createpairs", "fastpairdetection",
            "frame", "imprint", "repairtol", "repairtoltype", "pairtype",
            "splitpairs",
        }
    ),
    # comsol_api_geom.48.114.html (Move, Copy), Table 3-118.
    "Move": frozenset(
        {
            "displx", "disply", "displz", "displ", "specify", "input", "keep",
            "indexattr", "indexattrtag", "indexattrtags", "selresult",
            "selresultshow", "color", "customcolor", "contributeto",
        }
    ),
    "Copy": frozenset(
        {
            "displx", "disply", "displz", "displ", "specify", "input", "keep",
            "indexattr", "indexattrtag", "indexattrtags", "selresult",
            "selresultshow", "color", "customcolor", "contributeto",
        }
    ),
    # comsol_api_geom.48.070.html (Compose, Union, Intersection, Difference),
    # Table 3-45.
    "Compose": frozenset(
        {
            "absrepairtol", "color", "customcolor", "formula", "input", "input2",
            "intbnd", "keep", "keepadd", "keeplowerdim", "keepsubtract",
            "repairtol", "repairtoltype", "selresult", "selresultshow",
        }
    ),
    "Union": frozenset(
        {
            "absrepairtol", "color", "customcolor", "formula", "input", "input2",
            "intbnd", "keep", "keepadd", "keeplowerdim", "keepsubtract",
            "repairtol", "repairtoltype", "selresult", "selresultshow",
        }
    ),
    "Intersection": frozenset(
        {
            "absrepairtol", "color", "customcolor", "formula", "input", "input2",
            "intbnd", "keep", "keepadd", "keeplowerdim", "keepsubtract",
            "repairtol", "repairtoltype", "selresult", "selresultshow",
        }
    ),
    "Difference": frozenset(
        {
            "absrepairtol", "color", "customcolor", "formula", "input", "input2",
            "intbnd", "keep", "keepadd", "keeplowerdim", "keepsubtract",
            "repairtol", "repairtoltype", "selresult", "selresultshow",
        }
    ),
    # comsol_api_geom.48.098/099/100/101.html (Import DXF / Geometry Sequence /
    # Mesh Part / mphbin-mphtxt).  The union of the four documented tables; the
    # engine selects the table from the detected format.
    "Import": frozenset(
        {
            "alllayers", "color", "contributeto", "convert", "customcolor",
            "filename", "includevirtual", "layers", "repairgeom", "repairtol",
            "sequence", "mesh", "meshfilename", "type", "selresult",
            "selresultshow", "selindividual", "selindividualshow",
        }
    ),
}

#: Documented object-selection *property* names of a geometry feature
#: (``feature(<ftag>).selection(<property>)``, Programming Reference "Editing a
#: Geometry Feature" and the per-feature pages).  A type listed here only
#: accepts those names; a type that is not listed accepts the caller's names and
#: lets the engine reject an unknown one.
GEOMETRY_FEATURE_INPUT_SELECTIONS: dict[str, frozenset[str]] = {
    "Rectangle": frozenset({"input"}),
    "Block": frozenset({"input"}),
    "Array": frozenset({"input"}),
    "Move": frozenset({"input"}),
    "Copy": frozenset({"input"}),
    "Compose": frozenset({"input", "input2"}),
    "Union": frozenset({"input", "input2"}),
    "Intersection": frozenset({"input", "input2"}),
    "Difference": frozenset({"input", "input2"}),
}

#: Pair types (Programming Reference ``model.pair()``): "The type type is
#: Contact, GeneralContact, Identity, or SectorSymmetry."
PAIR_TYPE_IDS = frozenset({"Contact", "GeneralContact", "Identity", "SectorSymmetry"})

#: Coupling types (Programming Reference ``model.cpl()``): "The supported types
#: are GeneralExtrusion, LinearExtrusion, BoundarySimilarity, IdentityMapping,
#: GeneralProjection, LinearProjection, Integration, Average, Maximum, and
#: Minimum."
COUPLING_TYPE_IDS = frozenset(
    {
        "GeneralExtrusion", "LinearExtrusion", "BoundarySimilarity",
        "IdentityMapping", "GeneralProjection", "LinearProjection",
        "Integration", "Average", "Maximum", "Minimum",
    }
)

#: Coordinate system types (Programming Reference ``model.coordSystem()``,
#: COMSOL 6.4 KB docs 4096 and 3626).  Every string below is quoted in a
#: ``coordSystem().create(<tag>,<gtag>,"<Type>")`` example: Mapping, VectorBase,
#: Rotated, Boundary, Cylindrical, Spherical, FromGeometry, Scaling, Combined,
#: Composite, PML, InfiniteElement, AbsorbingLayer.  ``SystemFromGeometry`` is
#: the name the same page's type list uses for the ``FromGeometry`` example
#: ("system from geometry (SystemFromGeometry)"), so both spellings are
#: accepted and neither is guessed.
COORDINATE_SYSTEM_TYPE_IDS = frozenset(
    {
        "Mapping", "VectorBase", "Rotated", "Boundary", "Scaling",
        "Cylindrical", "Spherical", "FromGeometry", "SystemFromGeometry",
        "Combined", "Composite", "PML", "InfiniteElement", "AbsorbingLayer",
    }
)

#: Component node types (Programming Reference ``model.component()``): "one of
#: the following types, set as the string <type>: Component, for a normal
#: geometry component; ExtraDim, for an extra dimension; or MeshComponent, for
#: a mesh component."
COMPONENT_TYPE_IDS = frozenset({"Component", "ExtraDim", "MeshComponent"})

#: Methods that the installed worker refuses because they are not listed in
#: ``comsol_mcp/worker_java/PersistentComsolWorker.java``'s ``METHODS`` set.
#: Every one of these is documented COMSOL API surface; the worker allow-list is
#: the only reason this layer cannot call it.  Verifying the absence here turns
#: "we would need an allow-list entry" into a precise, testable refusal instead
#: of a silent null.  ``test_g3_w14.py`` re-reads the Java source and fails if
#: one of these methods has been added without this table being repaired.
#: Repaired 2026-09-20: ``current``, ``problems``, ``problem``, ``hasError`` and
#: ``type`` were added to the worker allow-list for the W15/W16 surface and were
#: removed here so this table keeps meaning "still absent from the worker".
WORKER_UNAVAILABLE_METHODS = frozenset(
    {
        "axisymmetric", "isAxisymmetric", "angularUnit", "isBuilt",
        "status", "message", "errors",
        "warnings", "obj", "objectNames", "coord",
        "isLinear", "isOrthonormal", "masterSystem", "source", "destination",
        "pairName", "swap", "hasAutoSelection", "manualSelection",
        "searchMethod", "searchDist", "copy", "duplicate",
    }
)

#: Geometry entity-counter getters verified in javap of ``com.comsol.model.GeomInfo``.
#: The Programming Reference "Geometric Entity Counters" page documents the
#: return shapes: ``getNEntities()`` is an int[] whose length is 2/3/4 for a
#: 1D/2D/3D geometry (one count per entity dimension).  Only ``getNEntities``
#: and ``getNFiniteVoids`` are also in the worker allow-list; the other four
#: getters are documented but not allow-listed and are therefore reported as
#: unavailable instead of being called and silently failed.
GEOMETRY_ENTITY_COUNTERS: dict[str, str] = {
    "n_entities": "getNEntities",
    "n_domains": "getNDomains",
    "n_boundaries": "getNBoundaries",
    "n_edges": "getNEdges",
    "n_vertices": "getNVertices",
    "n_finite_voids": "getNFiniteVoids",
}

#: The subset of :data:`GEOMETRY_ENTITY_COUNTERS` that can actually be called.
GEOMETRY_COUNTERS_ALLOWLISTED = ("n_entities", "n_finite_voids")

#: Expectations ``geometry.validate`` can evaluate with a verified read path.
GEOMETRY_EXPECTATION_KEYS = frozenset(
    {
        "dimension", "entity_counts", "bounding_box", "feature_tags",
        "length_unit", "volume", "area", "tolerance",
    }
)

#: Expectations the catalogue names but this layer refuses: "design spacing"
#: needs a ``DistanceMeasurement`` feature, and its parameter readback path is
#: not verified for the installed worker.
GEOMETRY_REFUSED_EXPECTATION_KEYS = frozenset(
    {"design_spacing", "min_distance", "clearance"}
)


# ---------------------------------------------------------------------------
# shared helpers (kept local: this module must not edit _g3_common)
# ---------------------------------------------------------------------------


def _status(applied: Sequence[Any], failed: Sequence[Any], not_executed: Sequence[Any],
            execution_state_unknown: bool) -> dict[str, Any]:
    if execution_state_unknown:
        status = "EXECUTION_STATE_UNKNOWN"
    elif failed:
        status = "PARTIAL_FAILURE" if applied else "FAILED"
    else:
        status = "APPLIED"
    return {
        "ok": not failed,
        "status": status,
        "partial_change": bool(applied) or bool(execution_state_unknown),
        "execution_state_unknown": bool(execution_state_unknown),
        "applied_count": len(applied),
        "failed_count": len(failed),
        "not_executed_count": len(not_executed),
    }


def _property_write(node_path: Mapping[str, Any], worker: Any, model_tag: str,
                    payload: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Run the G2 property-set discipline and normalise its envelope."""
    if not payload:
        return {"applied": [], "failed": [], "not_executed": [], "execution_state_unknown": False,
                "readback_values": {}, "engine_error": None}
    result = property_set(worker, model_tag, node_path, list(payload))
    data = result.get("data") if isinstance(result, Mapping) else None
    data = data if isinstance(data, Mapping) else {}
    applied = list(data.get("applied") or [])
    return {
        "applied": applied,
        "failed": list(data.get("failed") or []),
        "not_executed": list(data.get("not_executed") or []),
        "execution_state_unknown": bool(result.get("execution_state_unknown")),
        "readback_values": {row.get("name"): row.get("readback") for row in applied if isinstance(row, Mapping) and row.get("name") is not None},
        "engine_error": result.get("error"),
    }


def _unavailable(method: str) -> dict[str, Any]:
    """Evidence record for a documented method the installed worker refuses."""
    return {
        "status": "UNVERIFIED_WORKER_METHOD_UNAVAILABLE",
        "method": method,
        "allowlist_entry_required": method,
        "note": (
            "the COMSOL 6.4 API documents this method, but the installed "
            "PersistentComsolWorker METHODS allow-list does not contain it, so no "
            "value can be read or written through the worker"
        ),
    }


def _refuse_unavailable(method: str, action: str) -> ExecutionContractError:
    return ExecutionContractError(
        "API_UNSUPPORTED",
        f"{action} requires the documented COMSOL method {method!r}, which is not in the installed "
        f"worker's method allow-list; refusing before the first engine change "
        f"(allowlist_entry_required={method!r})",
    )


def _component_path(component: str) -> dict[str, Any]:
    return {"segments": [{"collection": "component", "tag": component}]}


def _component_tag_from_path(path: Any, *, label: str = "path") -> str:
    parsed = NodePath.from_wire(path)
    if not parsed.segments:
        raise ExecutionContractError("INVALID_NODE_PATH", f"{label} must start with a component segment")
    first = parsed.segments[0]
    if first.accessor is not None or first.collection != "component" or first.tag is None:
        raise ExecutionContractError(
            "INVALID_NODE_PATH", f"{label} must start with a component:<tag> segment"
        )
    return str(first.tag)


def _require_component(worker: Any, model_tag: str, component: str) -> Any:
    component = validate_tag(component, "component")
    model = _call(worker.client(), "model", model_tag)
    probe = call_probe(model, "component")
    if probe["ok"] and probe["value"] is not None:
        tags = tag_list(probe["value"])
        if component not in tags:
            raise node_not_found(f"component {component!r} does not exist")
    return _call(model, "component", component)


def _node_kind(node: Any) -> str | None:
    """``getType()`` readback through the shared helper (``None`` when absent)."""
    return node_type(node)


def _sequence_from_path(worker: Any, model_tag: str, path: Any, *,
                        label: str = "geometry") -> tuple[dict[str, Any], Any, str, dict[str, Any]]:
    """Resolve a path that yields a ``GeomSequence``.

    ``component:<c> geom:<g>``            -> the geometry sequence itself.
    ``component:<c> geom:<g> feature:<wp>`` with ``getType() == "WorkPlane"``
    -> ``feature(<wp>).geom()``, the nested 2D sequence (javap
    ``GeomFeature.geom()``; the Programming Reference WorkPlane page documents
    ``...feature(<ftag>).geom().feature()`` for a work plane feature).

    Returns ``(canonical path, sequence node, kind, info)``.
    """
    parsed = NodePath.from_wire(path)
    if not parsed.segments:
        raise ExecutionContractError("INVALID_NODE_PATH", f"{label} must name a geometry or work plane")
    last = parsed.segments[-1]
    if last.accessor is not None or last.collection is None or last.tag is None:
        raise ExecutionContractError(
            "INVALID_NODE_PATH", f"{label} must end in a geom:<tag> or feature:<tag> segment"
        )
    canonical, node = resolve_path(worker, model_tag, path, label=label)
    if last.collection == "geom":
        return canonical, node, "geometry_sequence", {"geometry": str(last.tag), "workplane": None}
    if last.collection == "feature":
        kind = _node_kind(node)
        if kind != "WorkPlane":
            raise ExecutionContractError(
                "INVALID_NODE_PATH",
                f"{label} ends in feature {last.tag!r} of type {kind!r}; only a WorkPlane feature owns a "
                f"nested geometry sequence",
            )
        segments = list(parsed.segments) + [("accessor", "geom")]
        inner = NodePath.from_wire(
            {"segments": [segment.as_dict() for segment in parsed.segments] + [{"accessor": "geom"}]}
        )
        inner_path = inner.as_dict()
        sequence = resolve_path(worker, model_tag, inner_path, label=label)[1]
        parent = NodePath(tuple(parsed.segments[:-1])).as_dict()
        return inner_path, sequence, "workplane_sequence", {
            "geometry": None, "workplane": str(last.tag), "parent_path": parent,
            "segments": len(segments),
        }
    raise ExecutionContractError(
        "INVALID_NODE_PATH",
        f"{label} must end in a geom:<tag> segment or a feature:<tag> WorkPlane segment "
        f"(received {last.collection!r})",
    )


def _feature_collection(sequence: Any) -> Any:
    return _call(sequence, "feature")


def _feature_tags(sequence: Any) -> list[str]:
    raw = _call(_feature_collection(sequence), "tags")
    if not isinstance(raw, (list, tuple)) or not all(isinstance(item, str) for item in raw):
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "COMSOL feature().tags() did not return a string list")
    return [str(item) for item in raw]


def _feature_node(sequence: Any, tag: str) -> Any:
    return _call(sequence, "feature", tag)


def _feature_type(sequence: Any, tag: str) -> str | None:
    return _node_kind(_feature_node(sequence, tag))


def _require_feature(sequence: Any, tag: str, *, label: str = "feature") -> Any:
    if tag not in _feature_tags(sequence):
        raise node_not_found(f"{label} {tag!r} does not exist in this geometry sequence")
    return _feature_node(sequence, tag)


def _validate_feature_type(type_id: Any) -> str:
    type_id = require_string(type_id, "type_id", max_length=64)
    if type_id == "Finalize":
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            'the geometry "Finalize" page documents the two create type strings "FormUnion" and '
            '"FormAssembly"; use one of those (Finalize itself is not a create type string)',
        )
    if type_id not in GEOMETRY_FEATURE_TYPE_IDS:
        if type_id in GEOMETRY_FEATURE_TYPE_IDS_UNVERIFIED:
            raise ExecutionContractError(
                "API_UNSUPPORTED",
                f"{type_id!r} is a documented geometry command in the local COMSOL 6.4 knowledge base, but no "
                f"create() call quoting that type string was retrieved offline, so this layer refuses it before "
                f"the first engine change instead of guessing the string",
            )
        if type_id in GEOMETRY_SEQUENCE_TYPE_IDS_UNSUPPORTED:
            raise ExecutionContractError(
                "API_UNSUPPORTED",
                f"{type_id!r} is a geometry *sequence* type (model.geom().create(<tag>,\"Part\",sDim)), not a "
                f"geometry feature type: it cannot be passed as a feature type_id",
            )
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            f"{type_id!r} is not in the verified COMSOL 6.4 geometry feature type vocabulary "
            f"(local KB \"Geometry Commands\" index)",
        )
    return type_id


def _feature_create_node(worker: Any, model_tag: str, sequence_path: Mapping[str, Any], sequence: Any,
                         tag: Any, type_id: Any) -> dict[str, Any]:
    """Create one geometry feature with pre-write conflict checks and readback."""
    tag = validate_tag(tag, "tag")
    type_id = _validate_feature_type(type_id)
    required_tag = FINALIZE_TAGS.get(type_id)
    if required_tag is not None and tag != required_tag:
        raise ExecutionContractError(
            "INVALID_REQUEST",
            f'the Finalize feature ({type_id}) has the fixed tag {required_tag!r}: "The only allowed tag for '
            f'the Finalize feature is "fin""',
        )
    existing = _feature_tags(sequence)
    if tag in existing:
        current = _feature_type(sequence, tag)
        if current is not None and current != type_id:
            raise ExecutionContractError(
                "TYPE_CONFLICT",
                f"geometry feature {tag!r} already exists with type {current!r} (requested {type_id!r})",
            )
        raise tag_conflict(f"geometry feature {tag!r} already exists")
    _call(_feature_collection(sequence), "create", tag, type_id)
    after = _feature_tags(sequence)
    if tag not in after:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            f"geometry feature {tag!r} was created but the post-create tag readback does not show it",
        )
    readback = _feature_type(sequence, tag)
    if readback is not None and readback != type_id:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            f"geometry feature {tag!r} readback type {readback!r} does not match the requested {type_id!r}",
        )
    segments = list(NodePath.from_wire(sequence_path).segments)
    feature_path = NodePath(tuple(segments) + (NodePath.from_wire(
        {"segments": [{"collection": "feature", "tag": tag}]}).segments[0],)).as_dict()
    return {
        "path": feature_path,
        "sequence_path": NodePath.from_wire(sequence_path).as_dict(),
        "tag": tag,
        "type_id": type_id,
        "type_readback": readback,
        "created": True,
        "readback": {"tags": after, "type": readback},
    }


def _engine_properties(node: Any) -> list[str]:
    """The node's own ``properties()`` enumeration (PropFeature, javap)."""
    raw = _call(node, "properties")
    if not isinstance(raw, (list, tuple)) or not all(isinstance(item, str) for item in raw):
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "COMSOL properties() did not return a string list")
    return [str(item) for item in raw]


def _feature_properties(node: Any, definition: Mapping[str, Any], type_id: str, *,
                        label: str = "definition") -> tuple[list[dict[str, Any]], str]:
    """Build a validated ``property_set`` payload for a geometry feature.

    Property *names* come from the documented table for ``type_id`` when the
    command page was read offline; otherwise from the node's own
    ``properties()`` enumeration (the engine, not this layer, is then the name
    authority).  Value kind and rank always come from the node's authoritative
    ``getValueType`` metadata, exactly like W13.
    """
    documented = GEOMETRY_FEATURE_PROPERTIES.get(type_id)
    if documented is not None:
        payload = definition_properties(node, definition, documented, label=label)
        return payload, "documented_property_table"
    # No documented table for this type: the node's own properties()
    # enumeration is the name authority (definition_properties rejects every
    # name it does not contain *before* the first setter runs).
    allowed = _engine_properties(node)
    payload = definition_properties(node, definition, allowed, label=label)
    return payload, "engine_property_enumeration"


def _apply_inputs(worker: Any, model_tag: str, feature_path: Mapping[str, Any], type_id: str,
                  inputs: Mapping[str, Any] | None) -> dict[str, Any]:
    """Bind documented object-selection properties of one geometry feature.

    ``feature(<ftag>).selection("input").set("blk1")`` is the documented call
    (Programming Reference "Editing a Geometry Feature", Array, Compose family,
    Move/Copy pages); javap ``GeomFeature.selection(String)`` ->
    ``GeomObjectSelection.set(String...)``/``objects()``.
    """
    if not inputs:
        return {"requested": {}, "applied": [], "failed": [], "not_executed": []}
    documented = GEOMETRY_FEATURE_INPUT_SELECTIONS.get(type_id)
    if documented is not None:
        reject_unknown_keys(inputs, documented, "inputs")
    node = resolve_path(worker, model_tag, feature_path, label="path")[1]
    applied: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    not_executed: list[dict[str, Any]] = []
    names = list(inputs)
    for index, name in enumerate(names):
        try:
            tags = require_string_array(inputs[name], f"inputs.{name}")
            selection = _call(node, "selection", name)
            _call(selection, "set", list(tags))
            named = call_probe(selection, "named")
            per_object = {tag: call_probe(selection, "entities", tag)["value"] for tag in tags}
            applied.append(
                {
                    "property": name,
                    "objects": tags,
                    "readback": {
                        "named": named["value"] if named["ok"] else None,
                        "per_object_entities": per_object,
                        "authoritative": bool(named["ok"] and named["value"]),
                        "objects_method": _unavailable("objects") if not named["ok"] else None,
                    },
                }
            )
        except Exception as exc:  # noqa: BLE001 - reported as data, never re-raised after a write
            failed.append({"property": name, "error": str(exc), "partial_change": bool(applied)})
            not_executed.extend({"property": remaining} for remaining in names[index + 1:])
            break
    return {"requested": dict(inputs), "applied": applied, "failed": failed, "not_executed": not_executed}


def _geometry_counters(sequence: Any) -> dict[str, Any]:
    counters: dict[str, Any] = {"n_entities": None, "n_finite_voids": None}
    errors: dict[str, Any] = {}
    for key in GEOMETRY_COUNTERS_ALLOWLISTED:
        probe = call_probe(sequence, GEOMETRY_ENTITY_COUNTERS[key])
        if probe["ok"]:
            value = probe["value"]
            counters[key] = [int(item) for item in value] if isinstance(value, (list, tuple)) else value
        else:
            errors[key] = probe["error"]
    counters["errors"] = errors
    counters["unavailable"] = {
        key: _unavailable(GEOMETRY_ENTITY_COUNTERS[key])
        for key in GEOMETRY_ENTITY_COUNTERS
        if key not in GEOMETRY_COUNTERS_ALLOWLISTED
    }
    return counters


def _geometry_bounding_box(sequence: Any) -> dict[str, Any]:
    probe = call_probe(sequence, "getBoundingBox")
    if probe["ok"] and isinstance(probe["value"], (list, tuple)):
        return {"value": [float(item) for item in probe["value"]], "error": None}
    return {"value": None, "error": probe["error"]}


def _geometry_state(sequence: Any) -> dict[str, Any]:
    return {
        "dimension": call_probe(sequence, "getSDim")["value"],
        "length_unit": call_probe(sequence, "lengthUnit")["value"],
        "entity_counters": _geometry_counters(sequence),
        "bounding_box": _geometry_bounding_box(sequence),
        "unavailable": {
            name: _unavailable(name)
            for name in ("isAxisymmetric", "angularUnit", "current", "problems", "objectNames")
        },
    }


def _feature_report(sequence: Any, tag: str, *, properties: Sequence[str] = ()) -> dict[str, Any]:
    node = _feature_node(sequence, tag)
    active = call_probe(node, "active")
    label = call_probe(node, "label")
    props = call_probe(node, "properties")
    row: dict[str, Any] = {
        "tag": tag,
        "type_id": _node_kind(node),
        "label": label["value"] if label["ok"] else None,
        "enabled": active["value"] if active["ok"] else None,
        "properties": list(props["value"]) if props["ok"] and isinstance(props["value"], (list, tuple)) else None,
        "state": {
            "is_built": _unavailable("isBuilt"),
            "status": _unavailable("status"),
            "problems": _unavailable("problems"),
        },
    }
    if properties:
        row["property_values"] = property_read_rows(node, list(properties))
    return row


def _sequence_feature_rows(sequence: Any, *, properties: Mapping[str, Sequence[str]] | None = None) -> list[dict[str, Any]]:
    rows = []
    wanted = dict(properties or {})
    for tag in _feature_tags(sequence):
        rows.append(_feature_report(sequence, tag, properties=wanted.get(tag, ())))
    return rows


def _build(sequence: Any, until_tag: str | None) -> dict[str, Any]:
    """``geom.run()`` / ``geom.run(<ftag>)`` (Programming Reference: building)."""
    if until_tag is None:
        _call(sequence, "run")
        return {"scope": "all_features", "until_tag": None}
    _call(sequence, "run", until_tag)
    return {"scope": "feature_and_preceding", "until_tag": until_tag}


def _validate_engine_path(value: Any, *, label: str = "artifact_id") -> str:
    """Validate an engine-side file path without rewriting it.

    The path is passed to COMSOL *verbatim*: no normalisation, no
    ``expanduser``, no shell interpretation, and spaces or non-ASCII characters
    are preserved exactly as the caller sent them.  Only structurally unsafe
    values are rejected before the write.
    """
    path = require_string(value, label, max_length=4096)
    if "\x00" in path or any(ord(char) < 32 for char in path):
        raise ExecutionContractError("INVALID_REQUEST", f"{label} contains a control character")
    if not os.path.isabs(path):
        raise ExecutionContractError(
            "INVALID_REQUEST",
            f"{label} must be an absolute path on the machine that runs the COMSOL engine; relative paths are "
            f"resolved against an unknown working directory and are refused instead of guessed",
        )
    return path


def _local_file_probe(path: str) -> dict[str, Any]:
    """Report a local existence probe for a path; never used to rewrite it."""
    exists = os.path.exists(path)
    is_file = os.path.isfile(path)
    size = None
    digest = None
    if is_file:
        try:
            size = os.path.getsize(path)
        except OSError:
            size = None
        digest = _local_sha256(path)
    return {
        "path": path,
        "exists": exists,
        "is_file": is_file,
        "size_bytes": size,
        "sha256": digest,
        "non_ascii": any(ord(char) > 127 for char in path),
        "contains_space": " " in path,
    }


def _local_sha256(path: str) -> str | None:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# geometry domain
# ---------------------------------------------------------------------------


def geometry_sequence_create(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("component", "tag", "dimension", "axisymmetric"),
                               ("component", "tag", "dimension"))
    component = validate_tag(args["component"], "component")
    tag = validate_tag(args["tag"])
    dimension = require_int(args["dimension"], "dimension", minimum=1, maximum=3)
    axisymmetric = args.get("axisymmetric")
    if axisymmetric is not None:
        axisymmetric = require_bool(axisymmetric, "axisymmetric")
        if axisymmetric:
            raise _refuse_unavailable("axisymmetric", "creating an axisymmetric geometry sequence")
    comp = _require_component(worker, model_tag, component)
    container = call_probe(comp, "geom")
    if container["ok"] and container["value"] is not None:
        existing = tag_list(container["value"])
        if tag in existing:
            current = _node_kind(_call(comp, "geom", tag))
            raise tag_conflict(
                f"geometry {tag!r} already exists in component {component!r} (type {current!r})",
            )
    _call(_call(comp, "geom"), "create", tag, dimension)
    sequence = _call(comp, "geom", tag)
    after = tag_list(_call(comp, "geom"))
    if tag not in after:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN", f"geometry {tag!r} was created but the tag readback does not show it"
        )
    readback_dimension = call_probe(sequence, "getSDim")["value"]
    if isinstance(readback_dimension, int) and not isinstance(readback_dimension, bool) and readback_dimension != dimension:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            f"geometry {tag!r} readback space dimension {readback_dimension} does not match the requested {dimension}",
        )
    return {
        "component": component,
        "path": {"segments": [{"collection": "component", "tag": component}, {"collection": "geom", "tag": tag}]},
        "tag": tag,
        "dimension": dimension,
        "dimension_readback": readback_dimension,
        "length_unit": call_probe(sequence, "lengthUnit")["value"],
        "axisymmetric": None,
        "axisymmetric_requested": False,
        "features": _feature_tags(sequence),
        "created": True,
        "readback": {"geometries": after},
        "unavailable": {"axisymmetric": _unavailable("axisymmetric"), "isAxisymmetric": _unavailable("isAxisymmetric")},
        "notes": [
            "geometry sequences are created with the documented create(<tag>,<sdim>) call; the axisymmetric "
            "flag is refused before the write because its documented setter is not in the worker allow-list, "
            "so this layer never claims an axisymmetric geometry it did not set",
        ],
    }


def geometry_inspect(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path", "feature_properties"), ("path",))
    path = args["path"]
    _, sequence, kind, info = _sequence_from_path(worker, model_tag, path, label="path")
    wanted: dict[str, Sequence[str]] = {}
    if args.get("feature_properties") is not None:
        mapping = require_mapping(args["feature_properties"], "feature_properties")
        for feature_tag, names in mapping.items():
            wanted[str(feature_tag)] = require_string_array(names, f"feature_properties.{feature_tag}")
    rows = _sequence_feature_rows(sequence, properties=wanted)
    return {
        "path": path,
        "kind": kind,
        **info,
        "feature_count": len(rows),
        "features": rows,
        "geometry_state": _geometry_state(sequence),
        "notes": [
            "feature tags, types, enabled state and the node's own property enumeration are read through the "
            "verified accessors; build status and build problems are reported as unavailable worker methods "
            "instead of being guessed",
        ],
    }


def _feature_create_common(worker: Any, model_tag: str, args: Mapping[str, Any], *,
                           label: str = "parent") -> dict[str, Any]:
    parent = args[label]
    sequence_path, sequence, kind, info = _sequence_from_path(worker, model_tag, parent, label=label)
    tag = validate_tag(args["tag"])
    type_id = _validate_feature_type(args["type_id"])
    definition = property_definition(args.get("definition") or {}, "definition")
    property_source = args.get("property_source", "documented")
    if property_source not in {"documented", "engine"}:
        raise ExecutionContractError("INVALID_REQUEST", "property_source must be 'documented' or 'engine'")
    documented = GEOMETRY_FEATURE_PROPERTIES.get(type_id)
    if definition and documented is None and property_source != "engine":
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            f"the documented property table for geometry feature type {type_id!r} was not retrieved offline; "
            f"pass property_source='engine' to use the node's own properties() enumeration as the name authority, "
            f"or create the feature without a definition",
        )
    if definition and documented is not None:
        reject_unknown_keys(definition, documented, "definition")
    inputs = args.get("inputs")
    if inputs is not None:
        inputs = require_mapping(inputs, "inputs")
        documented_inputs = GEOMETRY_FEATURE_INPUT_SELECTIONS.get(type_id)
        if documented_inputs is not None:
            reject_unknown_keys(inputs, documented_inputs, "inputs")
    created = _feature_create_node(worker, model_tag, sequence_path, sequence, tag, type_id)
    result: dict[str, Any] = {
        "path": created["path"],
        "sequence_path": sequence_path,
        "sequence_kind": kind,
        "tag": created["tag"],
        "type_id": created["type_id"],
        "type_readback": created["type_readback"],
        "created": True,
        "feature_tags": created["readback"]["tags"],
        "property_source": property_source if documented is None else "documented_property_table",
        "applied": [],
        "failed": [],
        "not_executed": [],
        "properties": {},
        "inputs": {"requested": {}, "applied": [], "failed": [], "not_executed": []},
        "execution_state_unknown": False,
    }
    node = _feature_node(sequence, created["tag"])
    if definition:
        try:
            payload, source = _feature_properties(node, definition, type_id)
        except ExecutionContractError as exc:
            result.update({
                "ok": False,
                "status": "FAILED",
                "partial_change": True,
                "execution_state_unknown": False,
                "failed": [{
                    "stage": "property_payload", "code": exc.code, "message": str(exc),
                    "feature_created": True, "partial_change": True,
                }],
                "not_executed": [{"stage": "property_write", "names": sorted(definition)}],
            })
            result["applied_count"] = 0
            result["failed_count"] = 1
            result["not_executed_count"] = 1
            return result
        result["property_source"] = source
        write = _property_write(created["path"], worker, model_tag, payload)
        result["applied"] = write["applied"]
        result["failed"] = write["failed"]
        result["not_executed"] = write["not_executed"]
        result["properties"] = write["readback_values"]
        result["execution_state_unknown"] = write["execution_state_unknown"]
        result["engine_error"] = write["engine_error"]
    if inputs:
        result["inputs"] = _apply_inputs(worker, model_tag, created["path"], type_id, inputs)
    result.update(_status(result["applied"], result["failed"], result["not_executed"],
                          result["execution_state_unknown"]))
    return result


def geometry_feature_create(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(
        arguments, ("parent", "tag", "type_id", "properties", "definition", "inputs", "property_source"),
        ("parent", "tag", "type_id"),
    )
    merged = dict(args)
    if merged.get("definition") is None and merged.get("properties") is not None:
        merged["definition"] = merged["properties"]
    return _feature_create_common(worker, model_tag, merged, label="parent")


def geometry_feature_update(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(
        arguments, ("path", "definition", "properties", "inputs", "property_source"), ("path",)
    )
    definition = args.get("definition") if args.get("definition") is not None else args.get("properties")
    inputs = args.get("inputs")
    if definition is None and inputs is None:
        raise ExecutionContractError("INVALID_REQUEST", "feature_update requires definition and/or inputs")
    property_source = args.get("property_source", "documented")
    if property_source not in {"documented", "engine"}:
        raise ExecutionContractError("INVALID_REQUEST", "property_source must be 'documented' or 'engine'")
    path, node = resolve_path(worker, model_tag, args["path"], label="path")
    parsed = NodePath.from_wire(path)
    last = parsed.segments[-1]
    if last.collection != "feature" or last.tag is None:
        raise ExecutionContractError("INVALID_NODE_PATH", "path must end in a feature:<tag> segment")
    type_id = _node_kind(node)
    if type_id is None:
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            "the geometry feature type could not be read (getType is unavailable or failed), so the documented "
            "property table cannot be selected; refusing the write",
        )
    sequence_path = NodePath(tuple(parsed.segments[:-1])).as_dict()
    sequence = resolve_path(worker, model_tag, sequence_path, label="path")[1]
    result: dict[str, Any] = {
        "path": path,
        "tag": str(last.tag),
        "type_id": type_id,
        "properties": {},
        "applied": [],
        "failed": [],
        "not_executed": [],
        "inputs": {"requested": {}, "applied": [], "failed": [], "not_executed": []},
        "execution_state_unknown": False,
    }
    if definition is not None:
        definition = property_definition(definition, "definition")
        documented = GEOMETRY_FEATURE_PROPERTIES.get(type_id)
        if documented is None and property_source != "engine":
            raise ExecutionContractError(
                "API_UNSUPPORTED",
                f"the documented property table for geometry feature type {type_id!r} was not retrieved offline; "
                f"pass property_source='engine' to use the node's own properties() enumeration as the name authority",
            )
        if documented is not None:
            reject_unknown_keys(definition, documented, "definition")
        payload, source = _feature_properties(node, definition, type_id)
        write = _property_write(path, worker, model_tag, payload)
        result["property_source"] = source
        result["applied"] = write["applied"]
        result["failed"] = write["failed"]
        result["not_executed"] = write["not_executed"]
        result["properties"] = write["readback_values"]
        result["execution_state_unknown"] = write["execution_state_unknown"]
        result["engine_error"] = write["engine_error"]
    if inputs is not None:
        inputs = require_mapping(inputs, "inputs")
        documented_inputs = GEOMETRY_FEATURE_INPUT_SELECTIONS.get(type_id)
        if documented_inputs is not None:
            reject_unknown_keys(inputs, documented_inputs, "inputs")
        result["inputs"] = _apply_inputs(worker, model_tag, path, type_id, inputs)
    result["feature_tags"] = _feature_tags(sequence)
    result.update(_status(result["applied"], result["failed"], result["not_executed"],
                          result["execution_state_unknown"]))
    return result


def geometry_feature_remove(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path",), ("path",))
    path, node = resolve_path(worker, model_tag, args["path"], label="path")
    parsed = NodePath.from_wire(path)
    last = parsed.segments[-1]
    if last.collection != "feature" or last.tag is None:
        raise ExecutionContractError("INVALID_NODE_PATH", "path must end in a feature:<tag> segment")
    tag = str(last.tag)
    sequence_path = NodePath(tuple(parsed.segments[:-1])).as_dict()
    sequence = resolve_path(worker, model_tag, sequence_path, label="path")[1]
    before = _feature_tags(sequence)
    if tag not in before:
        raise node_not_found(f"geometry feature {tag!r} does not exist")
    type_readback = _node_kind(node)
    _call(_feature_collection(sequence), "remove", tag)
    after = _feature_tags(sequence)
    if tag in after:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            f"geometry feature {tag!r} was removed but the post-remove tag readback still shows it",
        )
    return {
        "path": path,
        "sequence_path": sequence_path,
        "tag": tag,
        "type_id": type_readback,
        "removed": True,
        "feature_tags": after,
        "readback": {"tags": after},
        "dependency_risk": {
            "level": "UNSCANNED",
            "note": (
                "removing a geometry feature does not scan downstream features (input selections, named "
                "selections, physics selections) for references to the objects it produced"
            ),
        },
    }


def geometry_workplane_create(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(
        arguments, ("geometry", "tag", "definition", "properties", "inputs", "property_source"),
        ("geometry", "tag"),
    )
    merged = dict(args)
    if merged.get("definition") is None and merged.get("properties") is not None:
        merged["definition"] = merged["properties"]
    merged.setdefault("type_id", "WorkPlane")
    return _feature_create_common(worker, model_tag, merged, label="geometry")


def geometry_workplane_edit(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Apply a typed action plan to the nested 2D geometry of a work plane."""
    args = operation_arguments(arguments, ("workplane", "actions"), ("workplane", "actions"))
    workplane_path, sequence, kind, info = _sequence_from_path(worker, model_tag, args["workplane"], label="workplane")
    if kind != "workplane_sequence":
        raise ExecutionContractError(
            "INVALID_NODE_PATH", "workplane_edit requires a WorkPlane feature path (its nested 2D geometry)"
        )
    raw_actions = args["actions"]
    if not isinstance(raw_actions, Sequence) or isinstance(raw_actions, (str, bytes, Mapping)):
        raise ExecutionContractError("INVALID_REQUEST", "actions must be an array of action objects")
    if not raw_actions:
        raise ExecutionContractError("INVALID_REQUEST", "actions must not be empty")
    planned: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_actions):
        action = require_mapping(raw, f"actions[{index}]")
        name = require_string(action.get("action"), f"actions[{index}].action", max_length=32)
        if name not in {"create", "update", "remove", "set_active", "build"}:
            raise ExecutionContractError(
                "INVALID_REQUEST",
                f"actions[{index}].action must be one of create/update/remove/set_active/build",
            )
        # Per-action request shape is validated here, before the first engine
        # change, so a malformed plan can never leave a half-applied sequence.
        if name == "create":
            reject_unknown_keys(
                action,
                ("action", "tag", "type_id", "definition", "properties", "inputs", "property_source"),
                f"actions[{index}]",
            )
            tag = validate_tag(action.get("tag"), f"actions[{index}].tag")
            _validate_feature_type(action.get("type_id"))
            if action.get("definition", action.get("properties")) is not None:
                property_definition(action.get("definition", action.get("properties")),
                                    f"actions[{index}].definition")
            if action.get("inputs") is not None:
                require_mapping(action["inputs"], f"actions[{index}].inputs")
        elif name == "update":
            reject_unknown_keys(
                action, ("action", "tag", "definition", "properties", "inputs", "property_source"),
                f"actions[{index}]",
            )
            validate_tag(action.get("tag"), f"actions[{index}].tag")
            if action.get("definition", action.get("properties")) is None and action.get("inputs") is None:
                raise ExecutionContractError(
                    "INVALID_REQUEST",
                    f"actions[{index}] must carry definition/properties or inputs",
                )
        elif name == "remove":
            reject_unknown_keys(action, ("action", "tag"), f"actions[{index}]")
            validate_tag(action.get("tag"), f"actions[{index}].tag")
        elif name == "set_active":
            reject_unknown_keys(action, ("action", "tag", "enabled"), f"actions[{index}]")
            validate_tag(action.get("tag"), f"actions[{index}].tag")
            require_bool(action.get("enabled"), f"actions[{index}].enabled")
        else:  # build
            reject_unknown_keys(action, ("action", "until_tag"), f"actions[{index}]")
            if action.get("until_tag") is not None:
                validate_tag(action["until_tag"], f"actions[{index}].until_tag")
        planned.append(action)
    before = _feature_tags(sequence)
    applied: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    not_executed: list[dict[str, Any]] = []
    execution_state_unknown = False
    parent_path = info.get("parent_path")
    parent_sequence = None
    if parent_path is not None:
        parent_sequence = resolve_path(worker, model_tag, parent_path, label="workplane")[1]
    # A feature inside a work plane is addressed through the nested sequence
    # (Programming Reference WorkPlane page: feature(<wp>).geom().feature(<ftag>)),
    # so the child's parent path is the work plane feature path without the
    # trailing geom accessor of the canonical sequence path.
    wp_segments = tuple(NodePath.from_wire(workplane_path).segments)
    if wp_segments and wp_segments[-1].accessor == "geom":
        wp_segments = wp_segments[:-1]
    workplane_feature_path = NodePath(wp_segments).as_dict()
    for index, action in enumerate(planned):
        name = action["action"]
        try:
            if name == "create":
                record = _feature_create_common(
                    worker, model_tag,
                    {
                        "parent": workplane_feature_path,
                        "tag": action.get("tag"),
                        "type_id": action.get("type_id"),
                        "definition": action.get("definition", action.get("properties")),
                        "inputs": action.get("inputs"),
                        "property_source": action.get("property_source", "documented"),
                    },
                    label="parent",
                )
            elif name == "update":
                sub = {"path": None, "definition": action.get("definition", action.get("properties")),
                       "inputs": action.get("inputs"), "property_source": action.get("property_source", "documented")}
                tag = validate_tag(action.get("tag"), "actions[%d].tag" % index)
                inner = NodePath.from_wire(workplane_path)
                sub["path"] = NodePath(tuple(inner.segments) + (NodePath.from_wire(
                    {"segments": [{"collection": "feature", "tag": tag}]}).segments[0],)).as_dict()
                record = geometry_feature_update(worker, model_tag, sub)
            elif name == "remove":
                tag = validate_tag(action.get("tag"), "actions[%d].tag" % index)
                inner = NodePath.from_wire(workplane_path)
                record = geometry_feature_remove(
                    worker, model_tag,
                    {"path": NodePath(tuple(inner.segments) + (NodePath.from_wire(
                        {"segments": [{"collection": "feature", "tag": tag}]}).segments[0],)).as_dict()},
                )
            elif name == "set_active":
                reject_unknown_keys(action, ("action", "tag", "enabled"), f"actions[{index}]")
                tag = validate_tag(action.get("tag"), "actions[%d].tag" % index)
                enabled = require_bool(action.get("enabled"), f"actions[{index}].enabled")
                node = _require_feature(sequence, tag)
                _call(node, "active", enabled)
                readback = call_probe(node, "active")
                if not readback["ok"] or bool(readback["value"]) != enabled:
                    execution_state_unknown = True
                    failed.append({"action": name, "tag": tag, "error": "active() readback did not confirm the request",
                                   "readback": readback["value"] if readback["ok"] else None,
                                   "partial_change": True, "execution_state_unknown": True})
                    not_executed.extend(planned[index + 1:])
                    break
                record = {"action": name, "tag": tag, "enabled": enabled, "readback": readback["value"]}
            else:  # build
                reject_unknown_keys(action, ("action", "until_tag"), f"actions[{index}]")
                until_tag = action.get("until_tag")
                if until_tag is not None:
                    until_tag = validate_tag(until_tag, f"actions[{index}].until_tag")
                    _require_feature(sequence, until_tag, label="until_tag")
                if parent_sequence is None:
                    raise ExecutionContractError(
                        "API_UNSUPPORTED",
                        "a work plane's nested geometry is built by building the owning geometry sequence "
                        "(run(<workplane_tag>)), so a workplane path without a resolvable parent sequence "
                        "cannot be built from here",
                    )
                try:
                    if until_tag is None:
                        _call(parent_sequence, "run", str(info["workplane"]))
                        record = {"action": name, "scope": "workplane_feature_and_preceding",
                                  "workplane": info["workplane"], "until_tag": None}
                    else:
                        _call(sequence, "run", until_tag)
                        record = {"action": name, "scope": "nested_sequence_until_feature",
                                  "until_tag": until_tag}
                except ExecutionContractError as exc:
                    execution_state_unknown = True
                    failed.append({"action": name, "error": str(exc), "code": exc.code, "partial_change": True,
                                   "execution_state_unknown": True})
                    not_executed.extend(planned[index + 1:])
                    break
            applied.append(record)
        except ExecutionContractError as exc:
            failed.append({"action": name, "error": str(exc), "code": exc.code, "partial_change": bool(applied)})
            not_executed.extend(planned[index + 1:])
            break
    after = _feature_tags(sequence)
    preserved = [tag for tag in before if tag in after]
    result: dict[str, Any] = {
        "workplane": info["workplane"],
        "workplane_path": workplane_path,
        "parent_path": parent_path,
        "features_before": before,
        "features_after": after,
        "preserved_features": preserved,
        "added_features": [tag for tag in after if tag not in before],
        "removed_features": [tag for tag in before if tag not in after],
        "applied": applied,
        "failed": failed,
        "not_executed": not_executed,
        "geometry_state": _geometry_state(sequence),
    }
    result.update(_status(applied, failed, not_executed, execution_state_unknown))
    return result


def geometry_array_create(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(
        arguments, ("geometry", "tag", "definition", "properties", "inputs", "property_source"),
        ("geometry", "tag", "definition"),
    )
    definition = require_mapping(args["definition"], "definition")
    reject_unknown_keys(definition, GEOMETRY_FEATURE_PROPERTIES["Array"], "definition")
    if definition.get("type") is not None:
        array_type = require_string(definition["type"], "definition.type", max_length=32)
        if array_type not in {"linear", "rectangular", "three-dimensional"}:
            raise ExecutionContractError(
                "API_UNSUPPORTED",
                f"{array_type!r} is not a documented Array type; the Array page lists linear, rectangular and "
                f"three-dimensional",
            )
    merged = dict(args)
    merged["type_id"] = "Array"
    merged["definition"] = definition
    result = _feature_create_common(worker, model_tag, merged, label="geometry")
    result["array_type"] = definition.get("type")
    result["notes"] = [
        "the documented Array creation is create(<tag>,\"Array\") + the fullsize/displ/size properties; the "
        "input objects are bound through the feature's own input selection and the resulting object count and "
        "extents are read back from the built geometry (getNEntities/getBoundingBox)",
        "the linear/rectangular/three-dimensional type vocabulary is quoted from the Array command page",
    ]
    return result


def geometry_build(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("geometry", "until_tag"), ("geometry",))
    path = args["geometry"]
    sequence_path, sequence, kind, info = _sequence_from_path(worker, model_tag, path, label="geometry")
    until_tag = args.get("until_tag")
    parent_sequence = None
    if kind == "workplane_sequence":
        parent_sequence = resolve_path(worker, model_tag, info["parent_path"], label="geometry")[1]
    if until_tag is not None:
        until_tag = validate_tag(until_tag, "until_tag")
        _require_feature(sequence, until_tag, label="until_tag")
    try:
        if kind == "workplane_sequence":
            if until_tag is None:
                _call(parent_sequence, "run", str(info["workplane"]))
                scope = {"scope": "workplane_feature_and_preceding", "until_tag": None}
            else:
                _call(sequence, "run", until_tag)
                scope = {"scope": "nested_sequence_until_feature", "until_tag": until_tag}
        else:
            scope = _build(sequence, until_tag)
    except ExecutionContractError as exc:
        return {
            "geometry": path,
            "kind": kind,
            **info,
            "built": False,
            "ok": False,
            "status": "EXECUTION_STATE_UNKNOWN",
            "partial_change": True,
            "execution_state_unknown": True,
            "failed": [{"stage": "run", "code": exc.code, "message": str(exc)}],
            "not_executed": [{"stage": "post_build_readback"}],
            "geometry_state": _geometry_state(sequence),
            "notes": [
                "the build call raised: a COMSOL geometry build stops at the failing feature and leaves the "
                "features before it built, so the model state is unknown and the readback above is the best "
                "available evidence",
            ],
        }
    return {
        "geometry": path,
        "kind": kind,
        **info,
        "built": True,
        **scope,
        "ok": True,
        "status": "APPLIED",
        "partial_change": True,
        "execution_state_unknown": False,
        "geometry_state": _geometry_state(sequence),
        "features": _sequence_feature_rows(sequence),
    }


def geometry_finalize(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("geometry", "mode", "options"), ("geometry", "mode"))
    mode = require_string(args["mode"], "mode", max_length=32)
    type_id = {"union": "FormUnion", "assembly": "FormAssembly"}.get(mode)
    if type_id is None:
        raise ExecutionContractError(
            "INVALID_REQUEST", "mode must be 'union' or 'assembly' (the Finalize feature's documented action values)"
        )
    options = require_mapping(args.get("options") or {}, "options")
    reject_unknown_keys(options, GEOMETRY_FEATURE_PROPERTIES["FormUnion"], "options")
    sequence_path, sequence, kind, info = _sequence_from_path(worker, model_tag, args["geometry"], label="geometry")
    if kind != "geometry_sequence":
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            "the Finalize feature exists in geometry, 2D and 3D sequences; a work plane sequence only gets one "
            "when it is created explicitly and its property path is not part of the W14 scope",
        )
    tags = _feature_tags(sequence)
    definition = dict(options)
    definition["action"] = mode
    if "fin" in tags:
        current = _feature_type(sequence, "fin")
        if current is not None and current != type_id:
            raise ExecutionContractError(
                "TYPE_CONFLICT",
                f"the Finalize feature already exists with type {current!r}; mode {mode!r} requests {type_id!r} "
                f"(remove the existing Finalize feature first - there can only be one per sequence)",
            )
        feature = None
        node = _feature_node(sequence, "fin")
        try:
            payload, source = _feature_properties(node, definition, type_id)
        except ExecutionContractError as exc:
            return {
                "geometry": args["geometry"],
                "mode": mode,
                "type_id": type_id,
                "tag": "fin",
                "created": False,
                "ok": False,
                "status": "FAILED",
                "partial_change": False,
                "execution_state_unknown": False,
                "failed": [{"stage": "property_payload", "code": exc.code, "message": str(exc)}],
                "not_executed": [{"stage": "property_write", "names": sorted(definition)}],
            }
        write = _property_write(
            NodePath(tuple(NodePath.from_wire(sequence_path).segments) + (NodePath.from_wire(
                {"segments": [{"collection": "feature", "tag": "fin"}]}).segments[0],)).as_dict(),
            worker, model_tag, payload,
        )
        result: dict[str, Any] = {
            "geometry": args["geometry"],
            "mode": mode,
            "type_id": type_id,
            "type_readback": current,
            "tag": "fin",
            "created": False,
            "property_source": source,
            "applied": write["applied"],
            "failed": write["failed"],
            "not_executed": write["not_executed"],
            "properties": write["readback_values"],
            "execution_state_unknown": write["execution_state_unknown"],
            "feature_tags": _feature_tags(sequence),
        }
        result.update(_status(result["applied"], result["failed"], result["not_executed"],
                              result["execution_state_unknown"]))
        return result
    created = _feature_create_node(worker, model_tag, sequence_path, sequence, "fin", type_id)
    node = _feature_node(sequence, "fin")
    result = {
        "geometry": args["geometry"],
        "mode": mode,
        "type_id": type_id,
        "type_readback": created["type_readback"],
        "tag": "fin",
        "created": True,
        "applied": [],
        "failed": [],
        "not_executed": [],
        "properties": {},
        "execution_state_unknown": False,
        "feature_tags": created["readback"]["tags"],
    }
    try:
        payload, source = _feature_properties(node, definition, type_id)
    except ExecutionContractError as exc:
        result.update({
            "ok": False, "status": "FAILED", "partial_change": True,
            "failed": [{"stage": "property_payload", "code": exc.code, "message": str(exc),
                        "feature_created": True, "partial_change": True}],
            "not_executed": [{"stage": "property_write", "names": sorted(definition)}],
        })
        result["applied_count"] = 0
        result["failed_count"] = 1
        result["not_executed_count"] = 1
        return result
    result["property_source"] = source
    write = _property_write(created["path"], worker, model_tag, payload)
    result["applied"] = write["applied"]
    result["failed"] = write["failed"]
    result["not_executed"] = write["not_executed"]
    result["properties"] = write["readback_values"]
    result["execution_state_unknown"] = write["execution_state_unknown"]
    result["engine_error"] = write["engine_error"]
    result.update(_status(result["applied"], result["failed"], result["not_executed"],
                          result["execution_state_unknown"]))
    return result


def geometry_import(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(
        arguments, ("geometry", "tag", "artifact_id", "options"), ("geometry", "tag", "artifact_id"),
    )
    options = require_mapping(args.get("options") or {}, "options")
    reject_unknown_keys(
        options, ("path_check", "build", "includevirtual", "layers", "local_path"), "options",
    )
    path_check = options.get("path_check", "engine")
    if path_check not in {"engine", "local"}:
        raise ExecutionContractError("INVALID_REQUEST", "options.path_check must be 'engine' or 'local'")
    artifact_id = _validate_engine_path(args["artifact_id"])
    local_probe = None
    if path_check == "local":
        local_probe = _local_file_probe(artifact_id)
        if not local_probe["is_file"]:
            raise ExecutionContractError(
                "ARTIFACT_NOT_FOUND",
                f"options.path_check='local' requires {artifact_id!r} to be an existing regular file on this "
                f"machine; nothing was sent to the engine",
            )
    elif options.get("local_path") is not None:
        local_probe = _local_file_probe(require_string(options["local_path"], "options.local_path", max_length=4096))
    sequence_path, sequence, kind, info = _sequence_from_path(worker, model_tag, args["geometry"], label="geometry")
    definition: dict[str, Any] = {"filename": artifact_id}
    if options.get("includevirtual") is not None:
        definition["includevirtual"] = require_bool(options["includevirtual"], "options.includevirtual")
    if options.get("layers") is not None:
        definition["layers"] = require_string_array(options["layers"], "options.layers")
    merged = {
        "parent": args["geometry"],
        "tag": args["tag"],
        "type_id": "Import",
        "definition": definition,
        "property_source": "documented",
    }
    result = _feature_create_common(worker, model_tag, merged, label="parent")
    result.update({
        "artifact_id": artifact_id,
        "artifact_path_verbatim": True,
        "path_check": path_check,
        "local_probe": local_probe,
        "import_type_readback": result.get("properties", {}).get("type"),
        "license": {
            "status": "UNVERIFIED",
            "note": (
                "the geometry import property table differs per detected format; the COMSOL 6.4 geometry docs "
                "state that imported CAD objects are represented with the COMSOL geometry kernel or the CAD "
                "Import Module's kernel (Parasolid), and 'Perfectly matched layers, infinite elements, and "
                "absorbing layers are all available with a set of add-on products only.' This layer therefore "
                "reports the format the engine detected and never asserts that a CAD import succeeded or that a "
                "product licence is present"
            ),
        },
    })
    if options.get("build"):
        build_args = {"geometry": args["geometry"]}
        result["build"] = geometry_build(worker, model_tag, build_args)
    result["notes"] = [
        "the engine-side path is passed through verbatim: spaces and non-ASCII characters are preserved, and "
        "no shell expansion or path normalisation happens here",
        "the engine resolves the format from the file itself and sets the feature's 'type' property; the "
        "readback above is the engine's own answer, not an assumption from the file extension",
    ]
    return result


def geometry_measure(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("geometry", "query"), ("geometry", "query"))
    query = require_mapping(args["query"], "query")
    reject_unknown_keys(query, ("mode", "objects", "entity_dimension", "entities", "metrics", "all"), "query")
    mode = query.get("mode", "objects")
    if mode not in {"objects", "entities"}:
        raise ExecutionContractError("INVALID_REQUEST", "query.mode must be 'objects' or 'entities'")
    metrics = require_string_array(query.get("metrics", ["volume", "area", "bounding_box", "n_entities"]), "query.metrics")
    for name in metrics:
        if name not in MEASURE_METRICS:
            raise ExecutionContractError(
                "API_UNSUPPORTED",
                f"measurement metric {name!r} has no verified COMSOL 6.4 getter in this layer; verified metrics: "
                f"{sorted(MEASURE_METRICS)}",
            )
    geometry_path = args["geometry"]
    _, sequence, kind, info = _sequence_from_path(worker, model_tag, geometry_path, label="geometry")
    if mode == "entities":
        component = _component_tag_from_path(geometry_path, label="geometry")
        if query.get("entity_dimension") is None:
            raise ExecutionContractError("INVALID_REQUEST", "query.entity_dimension is required in 'entities' mode")
        dimension = require_int(query["entity_dimension"], "query.entity_dimension", minimum=0, maximum=3)
        entities = query.get("entities")
        explicit = None
        if entities is not None:
            from ._g3_common import require_entity_id_array

            explicit = require_entity_id_array(entities, "query.entities")
        elif query.get("all") is None:
            raise ExecutionContractError(
                "INVALID_REQUEST", "query requires either entities or all=true in 'entities' mode"
            )
        comp = _require_component(worker, model_tag, component)
        measure, selection = measure_selection(comp, dimension, explicit)
        if explicit is None:
            _call(selection, "all")
        selected = _call(selection, "entities")
        rows = _measure_rows(measure, metrics)
        return {
            "geometry": geometry_path,
            "kind": kind,
            **info,
            "mode": mode,
            "component": component,
            "source": "component.measure().selection()",
            "entity_dimension": dimension,
            "entities": list(selected),
            "entity_count": len(list(selected)),
            "entity_list_hash": hashlib.sha256(
                repr(sorted(int(item) for item in selected)).encode("utf-8")
            ).hexdigest(),
            "metrics": rows,
            "length_unit": call_probe(sequence, "lengthUnit")["value"],
            "notes": [
                "values are returned in the geometry's own unit system; the length unit is reported next to "
                "them and no unit conversion is performed",
                "the measurement tool's selection is transient state on the component, so this operation is "
                "classified as EVALUATE and runs isolated",
            ],
        }
    objects = query.get("objects")
    if objects is not None:
        objects = require_string_array(objects, "query.objects")
    elif query.get("all") is None:
        raise ExecutionContractError(
            "INVALID_REQUEST", "query requires objects or all=true in 'objects' mode"
        )
    measure = _call(sequence, "measure")
    selection = _call(measure, "selection")
    if objects is None:
        _call(selection, "all")
        selected_objects = None
    else:
        _call(selection, "set", list(objects))
        selected_objects = objects
    rows = _measure_rows(measure, metrics)
    return {
        "geometry": geometry_path,
        "kind": kind,
        **info,
        "mode": mode,
        "source": "geom.measure().selection() (GeomMeasure/GeomObjectSelection)",
        "objects": selected_objects,
        "object_entities": (
            {tag: call_probe(selection, "entities", tag)["value"] for tag in selected_objects}
            if selected_objects else None
        ),
        "metrics": rows,
        "length_unit": call_probe(sequence, "lengthUnit")["value"],
        "notes": [
            "the geometry-sequence measurement tool measures the objects in the current build state "
            "(Programming Reference \"Measurements\")",
            "the selection is the measurement tool's transient state, so this operation is classified as "
            "EVALUATE and runs isolated",
        ],
    }


def _measure_rows(measure: Any, metrics: Sequence[str]) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for name in metrics:
        getter = MEASURE_METRICS[name]
        probe = call_probe(measure, getter)
        rows[name] = {
            "getter": getter,
            "value": probe["value"] if probe["ok"] else None,
            "error": None if probe["ok"] else probe["error"],
        }
    return rows


def geometry_validate(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("geometry", "expectations"), ("geometry",))
    expectations = require_mapping(args.get("expectations") or {}, "expectations")
    refused = sorted(set(expectations) & GEOMETRY_REFUSED_EXPECTATION_KEYS)
    if refused:
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            f"{refused} need a DistanceMeasurement/CentroidMeasurement feature whose parameter readback has no "
            f"verified path for the installed worker; refusing before the first engine change",
        )
    reject_unknown_keys(expectations, GEOMETRY_EXPECTATION_KEYS, "expectations")
    geometry_path = args["geometry"]
    _, sequence, kind, info = _sequence_from_path(worker, model_tag, geometry_path, label="geometry")
    checks: list[dict[str, Any]] = []
    tolerance = require_number(expectations.get("tolerance", 0.0), "expectations.tolerance")
    if tolerance < 0:
        raise ExecutionContractError("INVALID_REQUEST", "expectations.tolerance must not be negative")

    if expectations.get("dimension") is not None:
        expected = require_int(expectations["dimension"], "expectations.dimension", minimum=1, maximum=3)
        actual = call_probe(sequence, "getSDim")["value"]
        checks.append({"name": "dimension", "ok": actual == expected, "expected": expected, "actual": actual})

    if expectations.get("length_unit") is not None:
        expected = require_string(expectations["length_unit"], "expectations.length_unit", max_length=64)
        actual = call_probe(sequence, "lengthUnit")["value"]
        checks.append({"name": "length_unit", "ok": actual == expected, "expected": expected, "actual": actual})

    counters = _geometry_counters(sequence)
    if expectations.get("entity_counts") is not None:
        wanted = require_mapping(expectations["entity_counts"], "expectations.entity_counts")
        values = counters["n_entities"]
        for key, expected_raw in wanted.items():
            if not isinstance(key, str) or not key.isdigit():
                raise ExecutionContractError(
                    "INVALID_REQUEST",
                    "expectations.entity_counts keys are entity dimensions (\"0\"=vertices, \"1\"=edges, "
                    "\"2\"=boundaries/domains, ...) because getNEntities() returns one count per dimension",
                )
            dimension = int(key)
            expected = require_int(expected_raw, f"expectations.entity_counts[{key}]", minimum=0)
            actual = None
            if isinstance(values, list) and 0 <= dimension < len(values):
                actual = int(values[dimension])
            checks.append({
                "name": f"entity_counts[{dimension}]", "ok": actual == expected,
                "expected": expected, "actual": actual,
            })

    boxes = None
    if expectations.get("bounding_box") is not None:
        expected_raw = expectations["bounding_box"]
        if not isinstance(expected_raw, Sequence) or isinstance(expected_raw, (str, bytes, Mapping)):
            raise ExecutionContractError("INVALID_REQUEST", "expectations.bounding_box must be an array")
        expected = [require_number(item, "expectations.bounding_box[]") for item in expected_raw]
        probe = _geometry_bounding_box(sequence)
        boxes = probe["value"]
        ok = False
        detail = None
        if boxes is not None and len(boxes) == len(expected):
            deltas = [abs(a - b) for a, b in zip(boxes, expected)]
            ok = max(deltas) <= tolerance
            detail = {"max_abs_delta": max(deltas), "deltas": deltas}
        checks.append({
            "name": "bounding_box", "ok": ok, "expected": expected, "actual": boxes,
            "tolerance": tolerance, "detail": detail,
        })

    if expectations.get("feature_tags") is not None:
        wanted = require_mapping(expectations["feature_tags"], "expectations.feature_tags")
        reject_unknown_keys(wanted, ("present", "absent"), "expectations.feature_tags")
        tags = _feature_tags(sequence)
        if wanted.get("present") is not None:
            names = require_string_array(wanted["present"], "expectations.feature_tags.present")
            missing = [name for name in names if name not in tags]
            checks.append({"name": "feature_tags.present", "ok": not missing, "expected": names,
                           "actual": tags, "missing": missing})
        if wanted.get("absent") is not None:
            names = require_string_array(wanted["absent"], "expectations.feature_tags.absent")
            present = [name for name in names if name in tags]
            checks.append({"name": "feature_tags.absent", "ok": not present, "expected": names,
                           "actual": tags, "unexpected": present})

    measure = _call(sequence, "measure")
    selection = _call(measure, "selection")
    _call(selection, "all")
    for name, getter in (("volume", "getVolume"), ("area", "getArea")):
        if expectations.get(name) is None:
            continue
        wanted = require_mapping(expectations[name], f"expectations.{name}")
        reject_unknown_keys(wanted, ("value", "tolerance"), f"expectations.{name}")
        expected_value = require_number(wanted["value"], f"expectations.{name}.value")
        local_tolerance = require_number(wanted.get("tolerance", tolerance), f"expectations.{name}.tolerance")
        probe = call_probe(measure, getter)
        actual_value = probe["value"] if probe["ok"] else None
        ok = False
        delta = None
        if isinstance(actual_value, (int, float)) and not isinstance(actual_value, bool):
            delta = abs(float(actual_value) - expected_value)
            ok = delta <= local_tolerance
        checks.append({"name": name, "ok": ok, "expected": expected_value, "actual": actual_value,
                       "tolerance": local_tolerance, "abs_delta": delta, "error": None if probe["ok"] else probe["error"]})

    failed = [row for row in checks if not row["ok"]]
    return {
        "geometry": geometry_path,
        "kind": kind,
        **info,
        "expectations": dict(expectations),
        "checks": checks,
        "check_count": len(checks),
        "failed_checks": [row["name"] for row in failed],
        "ok": not failed,
        "status": "APPLIED" if not failed else "FAILED",
        "partial_change": True,
        "execution_state_unknown": False,
        "geometry_state": {
            "dimension": call_probe(sequence, "getSDim")["value"],
            "length_unit": call_probe(sequence, "lengthUnit")["value"],
            "entity_counters": counters,
            "bounding_box": {"value": boxes, "error": None},
            "unavailable": {"problems": _unavailable("problems"), "isBuilt": _unavailable("isBuilt")},
        },
        "notes": [
            "checks are limited to the reads this layer verified offline: space dimension, length unit, "
            "per-dimension entity counts, bounding box, the feature tag list and the measurement-tool metrics; "
            "build problems and minimum-distance expectations are reported as unavailable instead of PASS",
            "validation is read-only apart from the measurement tool's transient selection",
        ],
    }


# ---------------------------------------------------------------------------
# definition domain (components, coordinate systems, pairs, couplings)
# ---------------------------------------------------------------------------


def _optional_tag(value: Any, label: str) -> str | None:
    return None if value is None else validate_tag(value, label)


def _definition_list(worker: Any, model_tag: str, component: str, collection: str) -> Any:
    if collection == "component":
        return _call(_call(worker.client(), "model", model_tag), "component")
    comp = _require_component(worker, model_tag, component)
    return _call(comp, collection)


def _definition_tags(container: Any) -> list[str]:
    return tag_list(container)


def _definition_path(component: str, collection: str, tag: str) -> dict[str, Any]:
    if collection == "component":
        return {"segments": [{"collection": "component", "tag": tag}]}
    return {
        "segments": [
            {"collection": "component", "tag": component},
            {"collection": collection, "tag": tag},
        ]
    }


def _definition_create(worker: Any, model_tag: str, component: str, collection: str, tag: str,
                       types: frozenset[str] | None, type_id: str | None, extra: Sequence[Any] = (),
                       display: str = "node", extra_position: str = "before_type") -> dict[str, Any]:
    tag = validate_tag(tag)
    container = _definition_list(worker, model_tag, component, collection)
    existing = _definition_tags(container)
    if tag in existing:
        current = _node_kind(_call(container, "get", tag))
        if types is not None and type_id is not None and current is not None and current != type_id:
            raise ExecutionContractError(
                "TYPE_CONFLICT",
                f"{display} {tag!r} already exists with type {current!r} (requested {type_id!r})",
            )
        raise tag_conflict(f"{display} {tag!r} already exists")
    if types is not None:
        if type_id is None:
            raise ExecutionContractError("INVALID_REQUEST", f"{display} requires a type_id string")
        type_id = require_string(type_id, "type_id", max_length=64)
        if type_id not in types:
            raise ExecutionContractError(
                "API_UNSUPPORTED",
                f"{type_id!r} is not in the verified COMSOL 6.4 {display} type vocabulary: {sorted(types)}",
            )
    elif type_id is not None:
        raise ExecutionContractError(
            "INVALID_REQUEST", f"{display} does not accept a type_id (the verified create call takes a tag only)"
        )
    if extra_position == "before_type":
        # Programming Reference: coordSystem().create(<tag>,<gtag>,type)
        # (doc "model.component().coordSystem()", example
        # create("sys2","geom1","Cylindrical")).
        args: list[Any] = [*extra, type_id] if type_id is not None else list(extra)
    elif extra_position == "after_type":
        # Programming Reference: pair().create(<tag>,type,<gtag>) and
        # cpl().create(<tag>,type,<gtag>) (docs "model.component().pair()" /
        # "model.component().cpl()", example
        # pair().create("p1","Contact","geom1")).
        args = [type_id, *extra] if type_id is not None else list(extra)
    else:
        raise ExecutionContractError(
            "INVALID_REQUEST", "extra_position must be 'before_type' or 'after_type'"
        )
    _call(container, "create", tag, *args)
    after = _definition_tags(container)
    if tag not in after:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN", f"{display} {tag!r} was created but the tag readback does not show it"
        )
    readback = _node_kind(_call(container, "get", tag))
    if type_id is not None and readback is not None and readback != type_id:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            f"{display} {tag!r} readback type {readback!r} does not match the requested {type_id!r}",
        )
    return {
        "path": _definition_path(component, collection, tag),
        "tag": tag,
        "type_id": type_id,
        "type_readback": readback,
        "created": True,
        "readback": {"tags": after},
    }


def _definition_remove(worker: Any, model_tag: str, component: str, collection: str, tag: str,
                       *, display: str = "node") -> dict[str, Any]:
    tag = validate_tag(tag)
    container = _definition_list(worker, model_tag, component, collection)
    before = _definition_tags(container)
    if tag not in before:
        raise node_not_found(f"{display} {tag!r} does not exist")
    node = _call(container, "get", tag)
    type_readback = _node_kind(node)
    _call(container, "remove", tag)
    after = _definition_tags(container)
    if tag in after:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN", f"{display} {tag!r} was removed but the tag readback still shows it"
        )
    return {
        "path": _definition_path(component, collection, tag),
        "tag": tag,
        "type_id": type_readback,
        "removed": True,
        "readback": {"tags": after},
    }


def _definition_node_path(args: Mapping[str, Any]) -> dict[str, Any]:
    path = args.get("path")
    if path is None:
        raise ExecutionContractError("INVALID_REQUEST", "path is required for this action")
    return path


def _split_definition_path(path: Any, collection: str, *, label: str = "path") -> tuple[str, str]:
    parsed = NodePath.from_wire(path)
    if len(parsed.segments) != 2:
        raise ExecutionContractError(
            "INVALID_NODE_PATH", f"{label} must be component:<ctag> / {collection}:<tag>"
        )
    first, last = parsed.segments
    if first.collection != "component" or last.collection != collection or last.tag is None:
        raise ExecutionContractError(
            "INVALID_NODE_PATH", f"{label} must be component:<ctag> / {collection}:<tag>"
        )
    return str(first.tag), str(last.tag)


def definition_component_manage(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("action", "tag", "definition"), ("action",))
    action = require_string(args["action"], "action", max_length=32)
    definition = require_mapping(args.get("definition") or {}, "definition")
    reject_unknown_keys(definition, ("type",), "definition")
    if action == "list":
        if args.get("tag") is not None:
            raise ExecutionContractError("INVALID_REQUEST", "action 'list' does not accept a tag")
        probe = call_probe(_call(worker.client(), "model", model_tag), "component")
        if not probe["ok"]:
            raise ExecutionContractError("ENGINE_CALL_FAILED", "the model does not expose component()")
        rows = []
        for tag in _definition_tags(probe["value"]):
            node = _call(probe["value"], "get", tag)
            rows.append({
                "tag": tag,
                "type_id": _node_kind(node),
                "identifier": call_probe(node, "identifier")["value"],
                "unavailable": {"defineAllFrames": _unavailable("defineAllFrames")},
            })
        return {
            "action": action,
            "component_count": len(rows),
            "components": rows,
            "readback": {"tags": [row["tag"] for row in rows]},
            "notes": [
                "component(), create(<tag>), create(<tag>,<type>) with the documented type vocabulary and "
                "remove(<tag>) are the verified surface; component copy/duplicate is refused because the "
                "Programming Reference page does not document a call sequence for it",
            ],
        }
    if action == "create":
        tag = validate_tag(args.get("tag"), "tag")
        type_id = definition.get("type")
        if type_id is not None:
            type_id = require_string(type_id, "definition.type", max_length=64)
            if type_id not in COMPONENT_TYPE_IDS:
                raise ExecutionContractError(
                    "API_UNSUPPORTED",
                    f"{type_id!r} is not in the documented component type vocabulary "
                    f"{sorted(COMPONENT_TYPE_IDS)}",
                )
        created = _definition_create(worker, model_tag, "", "component", tag, None, None,
                                     extra=(type_id,) if type_id is not None else (), display="component")
        created = dict(created)
        created.pop("path", None)
        return {
            "action": action,
            "tag": tag,
            "type_id": type_id,
            "type_readback": created["type_readback"],
            "created": True,
            "readback": created["readback"],
            "notes": [
                "the two-argument create form is create(<tag>,<type>) with Component|ExtraDim|MeshComponent; "
                "the ambiguous create(<tag>,<basetag>) form is not offered",
            ],
        }
    if action == "inspect":
        tag = validate_tag(args.get("tag"), "tag")
        node = _require_component(worker, model_tag, tag)
        return {
            "action": action,
            "tag": tag,
            "type_id": _node_kind(node),
            "identifier": call_probe(node, "identifier")["value"],
            "geometries": tag_list(call_probe(node, "geom")["value"]) if call_probe(node, "geom")["ok"] else None,
            "selections": tag_list(call_probe(node, "selection")["value"]) if call_probe(node, "selection")["ok"] else None,
            "materials": tag_list(call_probe(node, "material")["value"]) if call_probe(node, "material")["ok"] else None,
            "physics": tag_list(call_probe(node, "physics")["value"]) if call_probe(node, "physics")["ok"] else None,
            "pairs": tag_list(call_probe(node, "pair")["value"]) if call_probe(node, "pair")["ok"] else None,
            "coordinate_systems": (
                tag_list(call_probe(node, "coordSystem")["value"])
                if call_probe(node, "coordSystem")["ok"] else None
            ),
            "couplings": tag_list(call_probe(node, "cpl")["value"]) if call_probe(node, "cpl")["ok"] else None,
            "unavailable": {
                "defineAllFrames": _unavailable("defineAllFrames"),
                "geometricModel": _unavailable("geometricModel"),
            },
        }
    if action == "remove":
        tag = validate_tag(args.get("tag"), "tag")
        removed = _definition_remove(worker, model_tag, "", "component", tag, display="component")
        removed.pop("path", None)
        return {"action": action, **removed}
    if action == "copy":
        raise _refuse_unavailable("copy", "copying a model component")
    raise ExecutionContractError(
        "INVALID_REQUEST", "action must be one of list/create/inspect/remove/copy"
    )


def definition_coordinate_manage(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(
        arguments, ("action", "path", "definition", "geometry", "component", "tag"), ("action",)
    )
    action = require_string(args["action"], "action", max_length=32)
    definition = require_mapping(args.get("definition") or {}, "definition")
    reject_unknown_keys(definition, ("type", "geometry", "properties", "readback_properties"), "definition")
    readback_names = definition.get("readback_properties")
    if readback_names is not None:
        readback_names = require_string_array(readback_names, "definition.readback_properties")
    if action == "list":
        component = _optional_tag(args.get("component"), "component")
        if component is None:
            path = _definition_node_path(args)
            component, _ = _split_definition_path(path, "component")
        container = _definition_list(worker, model_tag, component, "coordSystem")
        rows = []
        for tag in _definition_tags(container):
            node = _call(container, "get", tag)
            rows.append({"tag": tag, "type_id": _node_kind(node), "label": call_probe(node, "label")["value"]})
        return {
            "action": action,
            "component": component,
            "coordinate_system_count": len(rows),
            "coordinate_systems": rows,
            "readback": {"tags": [row["tag"] for row in rows]},
            "unavailable": {"coord": _unavailable("coord"), "isLinear": _unavailable("isLinear"),
                            "isOrthonormal": _unavailable("isOrthonormal")},
        }
    if action == "create":
        component = _optional_tag(args.get("component"), "component")
        geometry = definition.get("geometry", args.get("geometry"))
        if component is None or geometry is None:
            raise ExecutionContractError(
                "INVALID_REQUEST",
                "coordinate system creation requires component and geometry: the documented call is "
                "coordSystem().create(<tag>,<gtag>,<type>)",
            )
        geometry = validate_tag(geometry, "geometry")
        tag = validate_tag(args.get("tag"), "tag")
        type_id = definition.get("type")
        comp = _require_component(worker, model_tag, component)
        geom_list = call_probe(comp, "geom")
        if geom_list["ok"] and geometry not in tag_list(geom_list["value"]):
            raise node_not_found(
                f"geometry {geometry!r} does not exist in component {component!r}; "
                f"the engine reports {tag_list(geom_list['value'])}",
            )
        created = _definition_create(
            worker, model_tag, component, "coordSystem", tag, COORDINATE_SYSTEM_TYPE_IDS, type_id,
            extra=(geometry,), display="coordinate system",
        )
        result: dict[str, Any] = {
            "action": action,
            "component": component,
            "geometry": geometry,
            "path": created["path"],
            "tag": tag,
            "type_id": type_id,
            "type_readback": created["type_readback"],
            "created": True,
            "readback": created["readback"],
            "unavailable": {"coord": _unavailable("coord"), "isLinear": _unavailable("isLinear"),
                            "isOrthonormal": _unavailable("isOrthonormal"),
                            "masterSystem": _unavailable("masterSystem")},
        }
        properties = definition.get("properties")
        if properties is not None:
            node = resolve_path(worker, model_tag, created["path"], label="path")[1]
            write = _property_write(created["path"], worker, model_tag,
                                    definition_properties(node, property_definition(properties, "definition.properties"),
                                                          _engine_properties(node), label="definition.properties"))
            result.update({
                "applied": write["applied"], "failed": write["failed"],
                "not_executed": write["not_executed"], "properties": write["readback_values"],
                "execution_state_unknown": write["execution_state_unknown"],
                "property_source": "engine_property_enumeration",
            })
            result.update(_status(write["applied"], write["failed"], write["not_executed"],
                                  write["execution_state_unknown"]))
        else:
            result.update(_status([], [], [], False))
        return result
    if action in {"inspect", "update", "remove"}:
        path = _definition_node_path(args)
        component, tag = _split_definition_path(path, "coordSystem")
        if action == "remove":
            return {"action": action, **(
                {k: v for k, v in _definition_remove(worker, model_tag, component, "coordSystem", tag,
                                                     display="coordinate system").items()}
            )}
        node = resolve_path(worker, model_tag, path, label="path")[1]
        if action == "inspect":
            names = readback_names
            if names is None:
                names = _engine_properties(node)
            return {
                "action": action,
                "component": component,
                "path": path,
                "tag": tag,
                "type_id": _node_kind(node),
                "properties": (
                    {name: row for name, row in property_read_rows(node, names).items()}
                ),
                "property_names": _engine_properties(node),
                "property_source": "engine_property_enumeration",
                "unavailable": {"coord": _unavailable("coord"), "isLinear": _unavailable("isLinear"),
                                "isOrthonormal": _unavailable("isOrthonormal"),
                                "masterSystem": _unavailable("masterSystem")},
                "notes": [
                    "the coordinate frame matrix (coord()), isLinear() and isOrthonormal() are documented on "
                    "the coordinate system node but are not in the worker method allow-list, so they are "
                    "reported as unavailable instead of being reconstructed from property guesses",
                ],
            }
        properties = property_definition(definition.get("properties", {}), "definition.properties")
        if not properties:
            raise ExecutionContractError("INVALID_REQUEST", "update requires definition.properties")
        payload = definition_properties(node, properties, _engine_properties(node), label="definition.properties")
        write = _property_write(path, worker, model_tag, payload)
        result = {
            "action": action,
            "component": component,
            "path": path,
            "tag": tag,
            "applied": write["applied"],
            "failed": write["failed"],
            "not_executed": write["not_executed"],
            "properties": write["readback_values"],
            "execution_state_unknown": write["execution_state_unknown"],
            "property_source": "engine_property_enumeration",
        }
        result.update(_status(write["applied"], write["failed"], write["not_executed"],
                              write["execution_state_unknown"]))
        return result
    raise ExecutionContractError("INVALID_REQUEST", "action must be one of list/create/inspect/update/remove")


def definition_pair_manage(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(
        arguments, ("action", "path", "definition", "component", "tag"), ("action",)
    )
    action = require_string(args["action"], "action", max_length=32)
    definition = require_mapping(args.get("definition") or {}, "definition")
    reject_unknown_keys(definition, ("type", "geometry", "source", "destination"), "definition")
    if "source" in definition or "destination" in definition:
        raise _refuse_unavailable(
            "source", "binding a pair source/destination selection (Pair.source()/destination() and their "
            "named()/set() methods are documented but not in the worker method allow-list)"
        )
    if action == "list":
        component = _optional_tag(args.get("component"), "component")
        if component is None:
            path = _definition_node_path(args)
            component, _ = _split_definition_path(path, "component")
        container = _definition_list(worker, model_tag, component, "pair")
        rows = []
        for tag in _definition_tags(container):
            node = _call(container, "get", tag)
            rows.append({
                "tag": tag,
                "type_id": _node_kind(node),
                "label": call_probe(node, "label")["value"],
                "active": call_probe(node, "isActive")["value"],
                "unavailable": {
                    "pair_type": _unavailable("type"),
                    "pairName": _unavailable("pairName"),
                    "source": _unavailable("source"),
                    "destination": _unavailable("destination"),
                },
            })
        return {
            "action": action,
            "component": component,
            "pair_count": len(rows),
            "pairs": rows,
            "readback": {"tags": [row["tag"] for row in rows]},
        }
    if action == "create":
        component = validate_tag(require_string(args.get("component"), "component"), "component")
        tag = validate_tag(args.get("tag"), "tag")
        type_id = definition.get("type")
        geometry = definition.get("geometry")
        if geometry is not None:
            # The three-argument form create(<tag>,<type>,<gtag>) is declared on
            # PairList (javap) and documented on the model.pair() page; the
            # component-level two-argument overload is
            # ComponentPairList.create(String,String) (javap).
            created = _definition_create(
                worker, model_tag, component, "pair", tag, PAIR_TYPE_IDS, type_id,
                extra=(validate_tag(geometry, "definition.geometry"),), display="pair",
                extra_position="after_type",
            )
        else:
            created = _definition_create(worker, model_tag, component, "pair", tag, PAIR_TYPE_IDS, type_id,
                                         display="pair")
        return {
            "action": action,
            "component": component,
            "path": created["path"],
            "tag": tag,
            "type_id": type_id,
            "type_readback": created["type_readback"],
            "geometry": geometry,
            "created": True,
            "readback": created["readback"],
            "unavailable": {
                "pair_type": _unavailable("type"),
                "pairName": _unavailable("pairName"),
                "source": _unavailable("source"),
                "destination": _unavailable("destination"),
                "swap": _unavailable("swap"),
            },
            "notes": [
                "the pair type vocabulary is quoted from the model.pair() page (Contact, GeneralContact, "
                "Identity, SectorSymmetry); a Pair is not a PropFeature in the verified API surface, so this "
                "layer does not accept a property block for it",
                "binding the source/destination selections is refused before the first write because "
                "Pair.source()/destination() are not in the worker method allow-list",
            ],
        }
    if action in {"inspect", "remove"}:
        path = _definition_node_path(args)
        component, tag = _split_definition_path(path, "pair")
        if action == "remove":
            return {"action": action, **_definition_remove(worker, model_tag, component, "pair", tag,
                                                           display="pair")}
        node = resolve_path(worker, model_tag, path, label="path")[1]
        return {
            "action": action,
            "component": component,
            "path": path,
            "tag": tag,
            "type_id": _node_kind(node),
            "label": call_probe(node, "label")["value"],
            "active": call_probe(node, "isActive")["value"],
            "unavailable": {
                "pair_type": _unavailable("type"),
                "pairName": _unavailable("pairName"),
                "source": _unavailable("source"),
                "destination": _unavailable("destination"),
                "hasAutoSelection": _unavailable("hasAutoSelection"),
                "manualSelection": _unavailable("manualSelection"),
                "searchMethod": _unavailable("searchMethod"),
                "searchDist": _unavailable("searchDist"),
            },
            "notes": [
                "a Pair is not a PropFeature in the verified API surface, so no property read or write is "
                "attempted for it",
            ],
        }
    raise ExecutionContractError("INVALID_REQUEST", "action must be one of list/create/inspect/remove")


def definition_coupling_manage(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(
        arguments, ("action", "path", "definition", "component", "tag", "geometry"), ("action",)
    )
    action = require_string(args["action"], "action", max_length=32)
    definition = require_mapping(args.get("definition") or {}, "definition")
    reject_unknown_keys(
        definition, ("type", "geometry", "properties", "selection", "readback_properties"), "definition",
    )
    if action == "list":
        component = _optional_tag(args.get("component"), "component")
        if component is None:
            path = _definition_node_path(args)
            component, _ = _split_definition_path(path, "component")
        container = _definition_list(worker, model_tag, component, "cpl")
        rows = []
        for tag in _definition_tags(container):
            node = _call(container, "get", tag)
            rows.append({
                "tag": tag,
                "type_id": _node_kind(node),
                "label": call_probe(node, "label")["value"],
                "unavailable": {"type": _unavailable("type")},
            })
        return {
            "action": action,
            "component": component,
            "coupling_count": len(rows),
            "couplings": rows,
            "readback": {"tags": [row["tag"] for row in rows]},
        }
    if action == "create":
        component = validate_tag(require_string(args.get("component"), "component"), "component")
        tag = validate_tag(args.get("tag"), "tag")
        type_id = definition.get("type")
        geometry = definition.get("geometry", args.get("geometry"))
        # The selection spec is request *shape*, so it is validated before the
        # first engine change; only the engine's own readback of a well-formed
        # request is reported as data after the coupling exists.
        selection_plan: dict[str, Any] | None = None
        selection_spec = definition.get("selection")
        if selection_spec is not None:
            selection_spec = require_mapping(selection_spec, "definition.selection")
            reject_unknown_keys(selection_spec, ("kind", "tag", "entities", "property"), "definition.selection")
            selection_kind = require_string(selection_spec.get("kind"), "definition.selection.kind", max_length=32)
            if selection_kind == "named":
                selection_plan = {
                    "kind": selection_kind,
                    "tag": require_string(selection_spec.get("tag"), "definition.selection.tag", max_length=63),
                }
            elif selection_kind == "entities":
                from ._g3_common import require_entity_id_array

                selection_plan = {
                    "kind": selection_kind,
                    "entities": require_entity_id_array(selection_spec.get("entities"),
                                                        "definition.selection.entities"),
                    "dimension": require_int(selection_spec.get("property"), "definition.selection.property",
                                             minimum=0, maximum=3),
                }
            else:
                raise ExecutionContractError(
                    "INVALID_REQUEST", "definition.selection.kind must be 'named' or 'entities'"
                )
        extra: tuple[Any, ...] = tuple()
        if geometry is not None:
            extra = (validate_tag(geometry, "definition.geometry"),)
            created = _definition_create(
                worker, model_tag, component, "cpl", tag, COUPLING_TYPE_IDS, type_id, extra=extra,
                display="coupling", extra_position="after_type",
            )
        else:
            created = _definition_create(worker, model_tag, component, "cpl", tag, COUPLING_TYPE_IDS, type_id,
                                         display="coupling")
        result: dict[str, Any] = {
            "action": action,
            "component": component,
            "geometry": extra[0] if extra else None,
            "path": created["path"],
            "tag": tag,
            "type_id": type_id,
            "type_readback": created["type_readback"],
            "created": True,
            "readback": created["readback"],
            "unavailable": {"type": _unavailable("type")},
            "selection": None,
            "properties": {},
            "applied": [],
            "failed": [],
            "not_executed": [],
            "execution_state_unknown": False,
        }
        node = resolve_path(worker, model_tag, created["path"], label="path")[1]
        if selection_plan is not None:
            try:
                selection = _call(node, "selection")
                if selection_plan["kind"] == "named":
                    named_tag = selection_plan["tag"]
                    _call(selection, "named", named_tag)
                    readback = call_probe(selection, "named")
                    if not (readback["ok"] and readback["value"] == named_tag):
                        result["execution_state_unknown"] = True
                        result["failed"].append({
                            "stage": "selection", "kind": selection_plan["kind"], "requested": named_tag,
                            "readback": readback["value"] if readback["ok"] else None,
                            "error": "selection().named() readback did not confirm the requested tag",
                            "partial_change": True, "execution_state_unknown": True,
                        })
                    else:
                        result["selection"] = {"kind": selection_plan["kind"], "tag": named_tag,
                                               "readback": readback["value"]}
                        result["applied"].append({"stage": "selection", "kind": selection_plan["kind"],
                                                  "tag": named_tag, "readback": readback["value"]})
                else:
                    entities = selection_plan["entities"]
                    _call(selection, "geom", selection_plan["dimension"])
                    _call(selection, "set", list(entities))
                    got = call_probe(selection, "entities")
                    value = [int(item) for item in got["value"]] if got["ok"] and isinstance(got["value"], (list, tuple)) else None
                    if value is None or sorted(value) != sorted(entities):
                        result["execution_state_unknown"] = True
                        result["failed"].append({
                            "stage": "selection", "kind": selection_plan["kind"], "requested": entities,
                            "readback": value,
                            "error": "coupling selection entity readback does not match the requested entity list",
                            "partial_change": True, "execution_state_unknown": True,
                        })
                    else:
                        result["selection"] = {"kind": selection_plan["kind"], "entities": entities,
                                               "readback": value}
                        result["applied"].append({"stage": "selection", "kind": selection_plan["kind"],
                                                  "entities": entities, "readback": value})
            except ExecutionContractError as exc:
                result["failed"].append({"stage": "selection", "code": exc.code, "message": str(exc),
                                         "partial_change": True})
        properties = definition.get("properties")
        if properties is not None:
            payload = definition_properties(node, property_definition(properties, "definition.properties"),
                                            _engine_properties(node), label="definition.properties")
            write = _property_write(created["path"], worker, model_tag, payload)
            result["applied"].extend(write["applied"])
            result["failed"].extend(write["failed"])
            result["not_executed"].extend(write["not_executed"])
            result["properties"] = write["readback_values"]
            result["execution_state_unknown"] = result["execution_state_unknown"] or write["execution_state_unknown"]
            result["property_source"] = "engine_property_enumeration"
        result.update(_status(result["applied"], result["failed"], result["not_executed"],
                              result["execution_state_unknown"]))
        return result
    if action in {"inspect", "update", "remove"}:
        path = _definition_node_path(args)
        component, tag = _split_definition_path(path, "cpl")
        if action == "remove":
            return {"action": action, **_definition_remove(worker, model_tag, component, "cpl", tag,
                                                           display="coupling")}
        node = resolve_path(worker, model_tag, path, label="path")[1]
        if action == "inspect":
            names = definition.get("readback_properties")
            if names is not None:
                names = require_string_array(names, "definition.readback_properties")
            selection = call_probe(node, "selection")
            selected = None
            if selection["ok"]:
                selected = {
                    "named": call_probe(selection["value"], "named")["value"],
                    "dimension": call_probe(selection["value"], "dim")["value"],
                }
            return {
                "action": action,
                "component": component,
                "path": path,
                "tag": tag,
                "type_id": _node_kind(node),
                "selection": selected,
                "properties": (
                    property_read_rows(node, names) if names else {}
                ),
                "property_names": _engine_properties(node),
                "property_source": "engine_property_enumeration",
                "unavailable": {"type": _unavailable("type")},
            }
        properties = property_definition(definition.get("properties", {}), "definition.properties")
        if not properties:
            raise ExecutionContractError("INVALID_REQUEST", "update requires definition.properties")
        payload = definition_properties(node, properties, _engine_properties(node), label="definition.properties")
        write = _property_write(path, worker, model_tag, payload)
        result = {
            "action": action,
            "component": component,
            "path": path,
            "tag": tag,
            "applied": write["applied"],
            "failed": write["failed"],
            "not_executed": write["not_executed"],
            "properties": write["readback_values"],
            "execution_state_unknown": write["execution_state_unknown"],
            "property_source": "engine_property_enumeration",
        }
        result.update(_status(write["applied"], write["failed"], write["not_executed"],
                              write["execution_state_unknown"]))
        return result
    raise ExecutionContractError("INVALID_REQUEST", "action must be one of list/create/inspect/update/remove")


# ---------------------------------------------------------------------------
# publishing table
# ---------------------------------------------------------------------------

OPERATIONS: dict[str, Callable[[Any, str, dict], dict]] = {
    "geometry.sequence_create": geometry_sequence_create,
    "geometry.inspect": geometry_inspect,
    "geometry.feature_create": geometry_feature_create,
    "geometry.feature_update": geometry_feature_update,
    "geometry.feature_remove": geometry_feature_remove,
    "geometry.workplane_create": geometry_workplane_create,
    "geometry.workplane_edit": geometry_workplane_edit,
    "geometry.array_create": geometry_array_create,
    "geometry.build": geometry_build,
    "geometry.finalize": geometry_finalize,
    "geometry.import": geometry_import,
    "geometry.measure": geometry_measure,
    "geometry.validate": geometry_validate,
    "definition.component_manage": definition_component_manage,
    "definition.coordinate_manage": definition_coordinate_manage,
    "definition.pair_manage": definition_pair_manage,
    "definition.coupling_manage": definition_coupling_manage,
}

__all__ = ["OPERATIONS"]
