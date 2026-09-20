"""G3 W15: material, physics and multiphysics domain operations.

Contract (identical to ``_g3_w13``)
-----------------------------------
``OPERATIONS`` maps a catalogue ``operation_id`` to ``fn(worker, model_tag,
arguments) -> data``.  The function raises
``comsol_mcp._g2_contract.ExecutionContractError`` **before the first write**
for anything that cannot be verified offline, and on success returns the
operation's own ``data`` dictionary -- never a ``{"success": ...}`` envelope
(the control plane owns the envelope).  Post-write results carry the shared
``status``/``partial_change``/``execution_state_unknown``/``not_executed``
shape and the readback that proves what actually happened (``applied``).

API provenance (COMSOL 6.4.0.293, installed at /Applications/COMSOL64)
----------------------------------------------------------------------
Every method name and every ``create(<tag>, <type>)`` / ``create(<tag>, <type>,
<geom>[, <sdim>])`` type string used below is verified against one of these
local sources; nothing is guessed from a display name.

* ``javap`` of the installed public API jar
  ``Multiphysics/apiplugins/com.comsol.api_1.0.0.jar`` and, where an accessor
  the reference documents is implemented rather than declared, of
  ``Multiphysics/plugins/com.comsol.model_1.0.0.jar``:

  - ``Model.material()`` -> ``MaterialList``; ``ModelNode.material()`` ->
    ``ComponentMaterialList``; ``Model.multiphysics()`` -> ``MultiphysicsCouplingList``;
    ``ModelNode.multiphysics()`` -> ``ComponentMultiphysicsCouplingList``;
    ``ModelNode.physics()`` -> ``ComponentPhysicsList``.
  - ``MaterialList.create(String)``, ``create(String, String)`` (arity 1-2).
  - ``Material.propertyGroup()`` -> ``MaterialModelList``,
    ``MaterialModelList.create(String, String)`` / ``create(String, String, String)``
    (two or three arguments; this layer only ever uses the documented
    two-argument form), ``MaterialModel.set(String, String|double|...)``,
    ``getString/getDouble/getStringArray/getDoubleArray/getStringMatrix/
    getDoubleMatrix/getValueType/hasProperty/properties/getType`` (through
    ``ParameterEntity``).
  - ``PhysicsList.create(String, String, String)`` (tag, interface type, geometry),
    ``Physics.getType()``/``geom()``/``feature()``, ``PhysicsFeatureList.create(String, String)``
    / ``create(String, String, int)``, ``PhysicsFeature.feature()``,
    ``PhysicsFeature.selection()``.
  - ``MultiphysicsCouplingList.create(String, String, String)`` (component:
    tag, coupling type, geometry) and ``create(String, String, String, int)``
    (tag, coupling type, geometry, space dimension), ``MultiphysicsCoupling.getType()``.
  - ``Selection.entities()``, ``entities(int)``, ``set(int...)``, ``all()``,
    ``geom(int)``, ``geom(String, int)``, ``inherit(boolean)``,
    ``isInheriting()``, ``dim()``; ``LocalSelection.named(String)``.
  - ``Model.getUsedProducts()`` and ``Model.isReadOnly()``.

* The local COMSOL 6.4 documentation corpus
  ``/Users/everwalker/Documents/KnowledgeBases/COMSOL-6.4-KB/kb.py``:

  - ``COMSOL_ProgrammingReferenceManual`` page 148 (PDF) / page 165: material
    type vocabulary (``Common``, ``Switch``, ``Link``, ``PorousMedia``,
    ``External``), ``propertyGroup`` semantics, and the
    ``model.multiphysics().create(<tag>,<coupling>,<geom>,<sdim>)`` contract.
  - ``ApplicationProgrammingGuide`` "Working with Model Objects": the exact
    heat-transfer material property names ``thermalconductivity``, ``density``
    and ``heatcapacity`` in the ``def`` property group, including the 3x3
    ``thermalconductivity`` matrix form.
  - ``comsol_api_fileformats.53.15`` "Supported Property Groups and Material
    Properties" for the property-group model.
  - ``IntroductionToLiveLinkForMATLAB`` for ``comp1.multiphysics.create('emh',
    'ElectromagneticHeatSource')``.

* The installed completion data ``Multiphysics/data/completion/physics.xml``
  (part of the 6.4.0.293 build) for the physics-interface type tokens and for
  the feature tokens ``TemperatureBoundary``, ``HeatSource``,
  ``HeatFluxBoundary``, ``ThermalInsulation``, ``InitialValues``,
  ``Symmetry``, ``HeatFlux`` (each appears as a ``name="..."`` token in that
  file), plus the plugin jar ``Multiphysics/plugins/com.comsol.heat_1.0.0.jar``
  for ``HeatTransferInSolids``.

Anything that could not be verified is *not* guessed: those workstreams return
``API_UNSUPPORTED`` before the first write (see ``UNVERIFIED_PATHS``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Callable

from ._g2_contract import (
    ExecutionContractError,
    NodePath,
    property_schema_from_engine,
    typed_value_from_engine,
)
from ._g2_engine import (
    _call,
    _typed_readback_comparison,
    property_entry_set,
    property_index_set,
    property_set,
)
from ._g3_common import (
    ENVELOPE_FIELDS,
    accessor_container,
    apply_local_selection,
    bound_model,
    call_probe,
    child_node,
    component_node,
    entity_list_hash,
    error_code_of,
    node_type,
    operation_arguments,
    is_typed_value,
    path_with_segment,
    probe_geometry_tag,
    property_definition,
    property_row,
    reject_unknown_keys,
    require_bool,
    require_entity_id_array,
    require_int,
    require_mapping,
    require_string,
    require_string_array,
    resolve_path,
    resolve_selection_entities,
    selection_dimension,
    selection_state,
    split_parent_path,
    tag_list,
    validate_definition_keys,
    validate_selection_spec,
    validate_tag,
)

# ---------------------------------------------------------------------------
# Verified vocabularies (see the module docstring for the exact sources)
# ---------------------------------------------------------------------------

#: ``material().create(<tag>[, <type>])`` -- Programming Reference p.148:
#: "model.component(<ctag>).material().create(<tag>) creates a new material",
#: ``create(<tag>,"Common")``, ``create(<tag>,"Switch")``, ``create(<tag>,"Link")``
#: and ``create(<ptag>,"PorousMedia")``, ``create(<tag>,"External")`` are the
#: documented type strings; ``material().create(<tag>,<type>)`` also creates a
#: *global* material, switch or link.
MATERIAL_TYPE_IDS: frozenset[str] = frozenset(
    {"Common", "Switch", "Link", "PorousMedia", "External"}
)

#: Populated material switches/porous media accept nested material features
#: (Programming Reference p.148: ``material("sw1").feature().create("mat1",
#: "Common", "")``); a plain ``Common`` material does not.
MATERIAL_CONTAINER_TYPES: frozenset[str] = frozenset({"Switch", "PorousMedia"})

#: ``physics().create(<tag>, <type>, <geometry>)`` interface type tokens taken
#: from the installed completion data (``data/completion/physics.xml`` of the
#: 6.4.0.293 build), each also named in the local documentation examples.  The
#: license product strings are the ``LicenseRequirement products`` entries
#: recorded for that interface in the same file; an empty tuple means the
#: interface is part of the base product.
PHYSICS_INTERFACE_IDS: dict[str, dict[str, Any]] = {
    "HeatTransfer": {"completion_id": "ht", "license_products": (), "source": "completion_data"},
    "HeatTransferInSolids": {
        "completion_id": "ht", "license_products": (),
        "source": "plugin_jar:com.comsol.heat (HeatTransferInSolids token)",
    },
    "HeatTransferInFluids": {"completion_id": "ht", "license_products": (), "source": "completion_data"},
    "HeatTransferInSolidsAndFluids": {
        "completion_id": "ht", "license_products": (), "source": "completion_data",
    },
    "BioHeat": {"completion_id": "ht", "license_products": ("HEATTRANSFER",), "source": "completion_data"},
    "Electrostatics": {"completion_id": "es", "license_products": (), "source": "completion_data"},
    "ConductiveMedia": {"completion_id": "ec", "license_products": (), "source": "completion_data"},
    "ElectricCurrents": {"completion_id": "ec", "license_products": (), "source": "completion_data"},
    "LaminarFlow": {"completion_id": "spf", "license_products": (), "source": "completion_data"},
    "CreepingFlow": {"completion_id": "spf", "license_products": ("CFD",), "source": "completion_data"},
    "SolidMechanics": {"completion_id": "solid", "license_products": (), "source": "completion_data"},
    "CoefficientFormPDE": {"completion_id": "c", "license_products": (), "source": "completion_data"},
    "GeneralFormPDE": {"completion_id": "g", "license_products": (), "source": "completion_data"},
    "WeakFormPDE": {"completion_id": "w", "license_products": (), "source": "completion_data"},
    "DomainODE": {"completion_id": "dode", "license_products": (), "source": "completion_data"},
    "BoundaryODE": {"completion_id": "bode", "license_products": (), "source": "completion_data"},
    "PressureAcoustics": {"completion_id": "acpr", "license_products": (), "source": "completion_data"},
    "PorousMediaFlowDarcy": {
        "completion_id": "dl", "license_products": ("POROUSMEDIAFLOW",), "source": "completion_data",
    },
    "Chemistry": {"completion_id": "chem", "license_products": ("CHEM",), "source": "completion_data"},
    "MoistureTransportPorousMedia": {
        "completion_id": "mt", "license_products": ("HEATTRANSFER", "POROUSMEDIAFLOW"),
        "source": "completion_data",
    },
    "ElectromagneticWaves": {"completion_id": "emw", "license_products": ("RF",), "source": "completion_data"},
}

#: ``multiphysics().create(<tag>, <coupling>, <geom>[, <sdim>])`` coupling
#: tokens.  ``ThermalExpansion`` and ``TemperatureCoupling`` are the two the
#: Programming Reference ``model.multiphysics()`` page uses verbatim;
#: ``ElectromagneticHeatSource`` and ``ElectromagneticHeating`` are the
#: documented LiveLink/help tokens for the same call.  This is deliberately an
#: allow-list: the completion data's coupling namespace is not separable
#: offline into "type tokens" and "feature tokens", so an unknown coupling is
#: refused before the write with the reason recorded rather than guessed.
MULTIPHYSICS_COUPLING_IDS: frozenset[str] = frozenset(
    {"ThermalExpansion", "TemperatureCoupling", "ElectromagneticHeatSource", "ElectromagneticHeating"}
)

#: Physics *feature* tokens this layer creates without a pre-write vocabulary
#: check.  They are recorded here because they appear as ``name="..."`` tokens
#: in the installed completion data; the create call itself is still verified
#: by ``getType()`` readback, so a token COMSOL does not accept never reaches a
#: caller as a success.
PHYSICS_FEATURE_TOKENS: frozenset[str] = frozenset(
    {
        "TemperatureBoundary", "HeatSource", "HeatFluxBoundary", "ThermalInsulation",
        "InitialValues", "Symmetry", "HeatFlux",
    }
)

#: Property group tag the Programming Reference and the Application Programming
#: Guide use for a material's user-defined group.
DEFAULT_MATERIAL_PROPERTY_GROUP = "def"

#: Heat-transfer property names verified against the Application Programming
#: Guide (``propertyGroup("def").set("thermalconductivity"| "density" |
#: "heatcapacity", ...)``).  ``material.validate`` uses these for the one
#: bounded pre-check ruleset of this module -- "density, heat capacity, thermal
#: conductivity in the ``def`` group of a ``Common`` material that covers
#: domains, for an enabled heat-transfer interface".  Any rule outside that
#: bounded set is reported as ``unknown``, never as a pass.
MATERIAL_REQUIRED_PROPERTIES: dict[str, dict[str, tuple[str, ...]]] = {
    "HeatTransfer": {
        "def": (
            "thermalconductivity",
            "density",
            "heatcapacity",
        )
    },
    "HeatTransferInSolids": {
        "def": (
            "thermalconductivity",
            "density",
            "heatcapacity",
        )
    },
    "HeatTransferInFluids": {
        "def": (
            "thermalconductivity",
            "density",
            "heatcapacity",
        )
    },
    "HeatTransferInSolidsAndFluids": {
        "def": (
            "thermalconductivity",
            "density",
            "heatcapacity",
        )
    },
    # Conductive-media names ("electricconductivity") and every other physics
    # family were not verified offline; those checks stay UNKNOWN on purpose.
}

#: The ``def``-group property names a *fresh* ``Common`` material accepts by
#: direct assignment, with the value type and shape the local 6.4 corpus
#: documents for them.
#:
#: A recorded live run (W15_T017, evidence/phase4/runs/20260920T130620Z-g3-live/
#: driver2/transcript.json) showed that on a material created by
#: ``material().create("p4mat","Common")`` the probe
#: ``propertyGroup("def").hasProperty("thermalconductivity"|"density"|
#: "heatcapacity")`` is false, while the official example writes exactly these
#: properties into a *fresh* Common material's ``def`` group by direct
#: assignment, without creating anything first:
#:
#:   model.component("comp1").material().create("mat1", "Common");
#:   model.component("comp1").material("mat1").propertyGroup("def").set("density", "7850[kg/m^3]");
#:   model.component("comp1").material("mat1").propertyGroup("def").set("heatcapacity", "475[J/(kg*K)]");
#:   model.component("comp1").material("mat1").propertyGroup("def")
#:        .set("thermalconductivity", new String[]{"44.5[W/(m*K)]", "0", ...});
#:
#: ``set`` therefore *creates* the property on assignment, so the
#: ``hasProperty`` probe alone must not refuse a documented name: the write is
#: admitted here and the post-write readback decides (``_property_write`` ->
#: the frozen G2 property-set discipline, the only layer that dispatches the
#: setter and reads the property back).
#:
#: Sources (local COMSOL 6.4 knowledge base; each row's ``source`` cites them):
#:
#: * Application Programming Guide, ``ApplicationProgrammingGuide.pdf`` p.72
#:   (heat-transfer example; sha256 c36e7e6e82289b063134e617294fbd05260d10be1a1e2efe25a0fbffa310d9a6)
#:   and p.59 ("Material ... a basic material property group for heat transfer");
#:   HTML twin ``application_programming_guide.15.26.html`` /
#:   ``application_programming_guide.15.30.html`` ("Working with Model Objects";
#:   sha256 1f8df6a1f746bb34336fe19f0d059bcd9a85eb14aa48f23cab42edf27376c33d).
#: * Programming Reference, ``COMSOL_ProgrammingReferenceManual.pdf`` p.152
#:   (sha256 5b7f23ad2eae77f59d71935f4f6c9b6b9ede380dc34da065181841e512bbc105):
#:   "To define a method to set an output property of a material ... set" and
#:   ``mm.getValueType(<pname>) returns the main data type that a property can
#:   return. The data types are: String, StringArray, and StringMatrix``; the
#:   available physical property names of Table 2-108 ("AVAILABLE PHYSICAL
#:   QUANTITIES", pp.153-158: "Density density", "Heat capacity at constant
#:   pressure heatcapacity", "Thermal conductivity thermalconductivity").
#: * API javadoc ``api/com/comsol/model/MaterialModel.html``
#:   (sha256 9da30715f6fa5a18c7864a663666a4a8d5566d1f13821320f376af1f35af5bdb):
#:   ``set(String,String|String[]|String[][])`` and ``getValueType(String)``;
#:   ``api/com/comsol/model/ParameterEntity.html``: ``hasProperty(String)``
#:   ("Returns true if this feature supports a given parameter"), ``properties()``.
#:
#: Only these three heat-transfer names were verified offline -- the same three
#: ``material.validate`` already checks -- so every other name stays refused
#: before the write unless the live node's own metadata knows it.
MATERIAL_DEF_GROUP_PROPERTIES: dict[str, dict[str, Any]] = {
    "density": {
        "value_type": "String", "kind": "string", "shape_rank": 0,
        "source": "Application Programming Guide 6.4 p.59/p.72 propertyGroup(def).set(density, 7850[kg/m^3]); "
                  "Programming Reference 6.4 p.152 (set/getValueType) + Table 2-108 Density density (p.154); "
                  "MaterialModel/ParameterEntity javadoc (api/com/comsol/model)",
    },
    "heatcapacity": {
        "value_type": "String", "kind": "string", "shape_rank": 0,
        "source": "Application Programming Guide 6.4 p.59/p.72 propertyGroup(def).set(heatcapacity, 475[J/(kg*K)]); "
                  "Programming Reference 6.4 p.152 (set/getValueType) + Table 2-108 Heat capacity at constant "
                  "pressure heatcapacity (p.155); MaterialModel javadoc",
    },
    "thermalconductivity": {
        "value_type": "StringMatrix", "kind": "string", "shape_rank": 2,
        "source": "Application Programming Guide 6.4 p.72 propertyGroup(def).set(thermalconductivity, "
                  "new String[]{44.5[W/(m*K)], ...}); Programming Reference 6.4 p.152 (getValueType: "
                  "String/StringArray/StringMatrix) + Table 2-108 Thermal conductivity thermalconductivity "
                  "(p.157); MaterialModel javadoc set(String,String[][])",
    },
}

#: Paths this module refuses *before* the first write because they could not be
#: verified against a local source.  Kept as data so the dispatcher/capabilities
#: document can publish them without re-reading the code.
UNVERIFIED_PATHS: tuple[dict[str, str], ...] = (
    {
        "path": "material.import",
        "reason": "material library import (artifact store / material library tags) has no locally verified "
                  "artifact contract; the operation is not published by this module",
    },
    {
        "path": "physics.pde_manage",
        "reason": "coefficient/weak-form PDE configuration is catalogued as G5 and no PDE property table was "
                  "retrieved offline; not published by this module",
    },
    {
        "path": "material.create type 'MATLAB'/'function' material",
        "reason": "the documented material types are Common/Switch/Link/PorousMedia/External only",
    },
    {
        "path": "physics.create dependent_variables array",
        "reason": "ComponentPhysicsList.create(String,String,String[]) exists in javap but no offline source "
                  "gives the variable-name/order contract, so this layer creates interfaces with the "
                  "geometry-bound three-argument form and reports dependent_variables as rejected",
    },
)

#: Physics-level child collections this module walks when inspecting an
#: interface (javap: Physics.feature/field/prop; ModelNode.multiphysics;
#: ComponentSelectionList).
PHYSICS_CHILD_COLLECTIONS: tuple[tuple[str, str], ...] = (
    ("feature", "feature"),
    ("field", "field"),
    ("prop", "prop"),
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _status(applied: Sequence[Any], failed: Sequence[Any], not_executed: Sequence[Any],
            execution_state_unknown: bool) -> dict[str, Any]:
    """The shared status block (same shape as ``_g3_w13``)."""
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


def _seed_envelope_change(envelope: Mapping[str, Any]) -> bool:
    """True when a typed-write envelope changed the model (mirrors W13)."""
    applied = envelope.get("applied") or []
    failed = envelope.get("failed") or []
    return bool(applied and (failed or envelope.get("execution_state_unknown")))


def _property_write(node_path: Mapping[str, Any], worker: Any, model_tag: str,
                    payload: list[dict[str, Any]]) -> dict[str, Any]:
    """Run the G2 property-set discipline and normalise its envelope (W13 shape)."""
    if not payload:
        return {"applied": [], "failed": [], "not_executed": [], "execution_state_unknown": False,
                "readback_values": {}, "engine_error": None}
    result = property_set(worker, model_tag, node_path, payload)
    data = result.get("data") if isinstance(result, Mapping) else None
    data = data if isinstance(data, Mapping) else {}
    applied = list(data.get("applied") or [])
    return {
        "applied": applied,
        "failed": list(data.get("failed") or []),
        "not_executed": list(data.get("not_executed") or []),
        "execution_state_unknown": bool(result.get("execution_state_unknown")),
        "readback_values": {row.get("name"): row.get("readback") for row in applied if isinstance(row, Mapping)},
        "engine_error": result.get("error"),
    }


def _component_path(component: str) -> dict[str, Any]:
    return {"segments": [{"collection": "component", "tag": component}]}


def _require_component(worker: Any, model_tag: str, component: str) -> Any:
    component = validate_tag(component, "component")
    model = bound_model(worker, model_tag)
    probe = call_probe(model, "component")
    if probe["ok"] and probe["value"] is not None:
        tags = [str(item) for item in tag_list(probe["value"])]
        if component not in tags:
            raise ExecutionContractError("NODE_NOT_FOUND", f"component {component!r} does not exist")
    return _call(model, "component", component)


def _scope_parent(worker: Any, model_tag: str, component: str | None) -> tuple[dict[str, Any], Any]:
    """Return (parent path, parent node) for a component-scoped or global collection."""
    if component is None:
        return {"segments": []}, bound_model(worker, model_tag)
    component = validate_tag(component, "component")
    return _component_path(component), _require_component(worker, model_tag, component)


def _require_geometry(worker: Any, model_tag: str, component: str, geometry: str) -> Any:
    """Validate a geometry tag inside a component; returns the geometry node."""
    geometry = validate_tag(geometry, "geometry")
    comp = _require_component(worker, model_tag, component)
    probe = call_probe(comp, "geom")
    if probe["ok"] and probe["value"] is not None:
        tags = [str(item) for item in tag_list(probe["value"])]
        if geometry not in tags:
            raise ExecutionContractError(
                "NODE_NOT_FOUND", f"geometry {geometry!r} does not exist in component {component!r}"
            )
    return _call(comp, "geom", geometry)


def _list_tags(container: Any) -> list[str]:
    raw = _call(container, "tags")
    if not isinstance(raw, (list, tuple)):
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "COMSOL tags() did not return a list")
    return [str(item) for item in raw]


def _verified_type(type_id: Any, vocabulary: Mapping[str, Any] | frozenset[str], label: str,
                   source: str) -> dict[str, Any]:
    """Refuse a type string that is not in a locally verified vocabulary.

    Returns ``{"type_id": str, "verified_source": str, "license_products": [...]}``;
    the refusal happens here, before any engine write.
    """
    type_id = require_string(type_id, "type_id", max_length=128)
    if isinstance(vocabulary, frozenset):
        if type_id not in vocabulary:
            raise ExecutionContractError(
                "API_UNSUPPORTED",
                f"{label} {type_id!r} is not in the verified COMSOL 6.4 vocabulary "
                f"({sorted(vocabulary)}); source: {source}",
            )
        return {"type_id": type_id, "verified_source": source, "license_products": []}
    entry = vocabulary.get(type_id)
    if entry is None:
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            f"{label} {type_id!r} is not in the verified COMSOL 6.4 vocabulary "
            f"({sorted(vocabulary)}); source: {source}",
        )
    return {
        "type_id": type_id,
        "verified_source": str(entry.get("source") or source),
        "license_products": list(entry.get("license_products") or []),
    }


def _node_exists(path: Mapping[str, Any], worker: Any, model_tag: str) -> bool:
    """Existence probe used before a write that may create or modify a node."""
    try:
        resolve_path(worker, model_tag, path)
    except ExecutionContractError:
        return False
    return True


def _feature_identity(node: Any) -> dict[str, Any]:
    tag_probe = call_probe(node, "tag")
    type_probe = call_probe(node, "getType")
    return {
        "tag": tag_probe["value"] if tag_probe["ok"] else None,
        "type_id": type_probe["value"] if type_probe["ok"] else None,
        "type_error": None if type_probe["ok"] else type_probe["error"],
    }


def _describe_entity_failure(exc: BaseException, method: str) -> dict[str, Any]:
    from ._g3_common import describe_engine_failure

    return describe_engine_failure(exc, method)


def _engine_call(node: Any, method: str, *args: Any) -> Any:
    """Call a *mutating* engine method, preserving the worker's failure code.

    ``_g2_engine._call`` collapses every failure to ``ENGINE_CALL_FAILED``; for
    a delete that the worker allow-list refuses, the informative code is
    ``METHOD_REJECTED`` (``_g3_common.allowlist_rejected``), and callers need it
    to tell "this layer may not call the method" apart from "COMSOL raised".
    """
    try:
        return getattr(node, method)(*args)
    except AttributeError as exc:
        raise ExecutionContractError(
            "API_UNSUPPORTED", f"selected node does not expose {method}()"
        ) from exc
    except ExecutionContractError:
        raise
    except Exception as exc:  # noqa: BLE001 - re-raised as a typed contract error
        failure = _describe_entity_failure(exc, method)
        raise ExecutionContractError(failure["code"], f"{failure['message']} ({method})") from exc


def _selection_summary(node: Any) -> dict[str, Any]:
    """Selection readback for a physics interface or one of its features."""
    state = selection_state(node)
    return {
        "dimension": state.get("dimension"),
        "geometry": state.get("geometry"),
        "named": state.get("named"),
        "is_inheriting": state.get("is_inheriting"),
        "entities": state.get("entities"),
        "entities_error": state.get("entities_error"),
        "named_error": state.get("named_error"),
    }


def _read_properties(node: Any, names: Sequence[str], *, limit: int = 200) -> dict[str, Any]:
    """Bounded property readback driven by the engine's own value metadata."""
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for name in list(dict.fromkeys(names))[:limit]:
        schema = property_schema_from_engine(node, name)
        row: dict[str, Any] = {
            "name": name,
            "value_type": schema.get("value_type"),
            "kind": schema.get("kind"),
            "shape_rank": schema.get("shape_rank"),
            "metadata_status": schema.get("metadata_status"),
            "getter": schema.get("getter"),
        }
        getter = schema.get("getter")
        if schema.get("metadata_status") != "KNOWN" or not isinstance(getter, str):
            row["value"] = None
            row["error"] = {
                "code": "API_UNSUPPORTED",
                "message": f"authoritative value metadata is unavailable for {name!r} on this COMSOL build",
            }
            errors.append({"name": name, "error": row["error"]})
        else:
            probe = call_probe(node, getter, name)
            if probe["ok"]:
                row["value"] = probe["value"]
                row["error"] = None
            else:
                row["value"] = None
                row["error"] = probe["error"]
                errors.append({"name": name, "error": probe["error"]})
        rows.append(row)
    return {"properties": rows, "errors": errors}


def _property_payload(node: Any, definition: Mapping[str, Any], *, label: str,
                      allowed: frozenset[str] | None = None,
                      documented: Mapping[str, Mapping[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Turn a JSON property object into a validated ``property_set`` payload.

    Two value shapes are accepted for each property:

    * the existing typed form ``{"value": ..., "unit": "..."}``, where
      ``value`` may be a bare JSON number/string/array/matrix (its kind and
      rank come from the engine's ``getValueType`` metadata, never from the
      JSON type), and
    * an already typed ``{"kind": ..., "shape": [...], "data": ...}`` payload
      from a caller that read the metadata itself.

    Property names are checked against ``allowed`` when a caller supplies a
    verified table; otherwise the engine's own metadata is the gate, so an
    unknown name is refused *before* the write instead of being created.

    ``documented`` carries the properties of a *fresh* node that the local
    COMSOL 6.4 corpus documents as written by direct assignment, with the
    documented value type and shape (see ``MATERIAL_DEF_GROUP_PROPERTIES`` for
    the material ``def`` group).  A recorded live run showed
    ``hasProperty(<name>)`` is false on a brand-new ``Common`` material for
    exactly those names although the documented API assigns them to the same
    fresh material, so ``hasProperty`` alone cannot be the existence proof
    there: a documented name (or a name whose engine metadata is readable) is
    admitted and the *post-write readback* decides.  A name that is neither
    documented nor known to the engine is still refused before the write, and
    when the engine can supply neither metadata nor a documented row, the value
    is refused here rather than dispatched with a guessed kind.
    """
    payload: list[dict[str, Any]] = []
    for key in definition:
        if key in ENVELOPE_FIELDS:
            raise ExecutionContractError(
                "INVALID_REQUEST", f"{label} must not carry control-plane field {key!r}"
            )
    names = list(definition)
    if allowed is not None:
        unknown = sorted(set(names) - set(allowed))
        if unknown:
            raise ExecutionContractError(
                "API_UNSUPPORTED",
                f"{label} has properties outside the verified COMSOL 6.4 table for this type: {unknown}",
            )
    for name in names:
        raw = definition[name]
        schema = property_schema_from_engine(node, name)
        if schema.get("metadata_status") != "KNOWN":
            fallback = documented.get(name) if documented is not None else None
            if fallback is None:
                if allowed is None:
                    has = call_probe(node, "hasProperty", name)
                    if has["ok"] and has["value"] is False:
                        raise ExecutionContractError(
                            "API_UNSUPPORTED",
                            f"{label} property {name!r} does not exist on this COMSOL node (hasProperty() is false); "
                            f"this layer never creates an unknown material/physics property",
                        )
                raise ExecutionContractError(
                    "API_UNSUPPORTED",
                    f"authoritative value metadata is unavailable for {label} property {name!r} on this COMSOL build",
                )
            # The documented row is the name's admission proof *and* its
            # shape/type: without engine metadata it is the only honest schema
            # this layer can use, and it is cited (source) instead of guessed.
            schema = {"value_type": fallback.get("value_type"), "kind": fallback.get("kind"),
                      "shape_rank": fallback.get("shape_rank"),
                      "metadata_status": "DOCUMENTED", "documented_source": fallback.get("source")}
            if not isinstance(schema["kind"], str) or not isinstance(schema["shape_rank"], int):
                raise ExecutionContractError(
                    "API_UNSUPPORTED",
                    f"the documented COMSOL 6.4 table has no verified value type for {label} property {name!r}",
                )
        if is_typed_value(raw):
            payload.append(property_row(name, raw, schema, label=name))
            continue
        if isinstance(raw, Mapping):
            row = dict(raw)
            reject_unknown_keys(row, ("value", "unit"), f"{label}.{name}")
            if "value" not in row:
                raise ExecutionContractError("INVALID_REQUEST", f"{label}.{name}.value is required")
            if "unit" not in row and isinstance(row["value"], (int, float)) and not isinstance(row["value"], bool):
                # A bare number plus a unit is the only honest spelling of a
                # quantity the engine could evaluate; this layer never performs
                # unit conversion, so a number without a unit is refused
                # instead of being written into the property's own unit.
                raise ExecutionContractError(
                    "INVALID_REQUEST",
                    f"{label}.{name}: a numeric quantity must carry both 'value' and 'unit' (write a text "
                    f"expression such as \"7850[kg/m^3]\" for a unit-bearing string property)",
                )
            typed = _json_to_typed(row["value"], schema, label=name)
            if row.get("unit") is not None:
                typed["unit"] = require_string(row["unit"], f"{label}.{name}.unit", max_length=63)
            payload.append({"name": name, "value": typed})
            continue
        payload.append({"name": name, "value": _json_to_typed(raw, schema, label=name)})
    return payload


def _json_to_typed(value: Any, schema: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    """Build a typed value from a bare JSON value using engine metadata."""
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
                f"{label}{position} must be a JSON boolean for this property (COMSOL reports the boolean "
                f"enumeration as on/off in metadata)",
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
            return float(item)
        if kind in {"string", "expression"}:
            if not isinstance(item, str):
                raise ExecutionContractError(
                    "PROPERTY_TYPE_MISMATCH",
                    f"{label}{position} must be a string; this layer never stringifies numbers implicitly "
                    f"(the engine metadata declares kind {kind!r})",
                )
            return item
        raise ExecutionContractError(
            "API_UNSUPPORTED", f"property {label!r} has unverified value kind {kind!r}"
        )

    if rank == 0:
        if isinstance(value, Mapping):
            raise ExecutionContractError(
                "PROPERTY_TYPE_MISMATCH", f"{label} must be a scalar for this property"
            )
        return {"kind": kind, "shape": [], "data": scalar(value, "")}
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or isinstance(value, Mapping):
        raise ExecutionContractError(
            "PROPERTY_TYPE_MISMATCH", f"{label} must be an array for this property"
        )
    items = list(value)
    if rank == 1:
        data = [scalar(item, f"[{index}]") for index, item in enumerate(items)]
        return {"kind": kind, "shape": [len(data)], "data": data}
    if not all(isinstance(row, Sequence) and not isinstance(row, (str, bytes)) for row in items):
        raise ExecutionContractError(
            "PROPERTY_TYPE_MISMATCH", f"{label} must be a matrix (array of rows) for this property"
        )
    rows = [[scalar(item, f"[{row_index}][{col}]") for col, item in enumerate(list(row))]
            for row_index, row in enumerate(items)]
    return {"kind": kind, "shape": [len(rows), len(rows[0]) if rows else 0], "data": rows}


def _material_property_group_handle(material: Any, group: str, *, create_missing: bool) -> tuple[Any, dict[str, Any]]:
    """Resolve a material property group through the verified ``propertyGroup()`` API.

    The Programming Reference records that a created material "always" carries
    the default user-defined property group -- the ``def`` group the Application
    Programming Guide writes with ``material("mat1").propertyGroup("def").set(...)``
    without creating it first -- while ``propertyGroup().tags()`` lists only the
    *additional* groups.  ``def`` is therefore reachable on a plain material even
    when the tag list does not show it; every other group must be listed (or be
    created here when the caller asked for it).
    """
    group = validate_tag(group, "group")
    probe = call_probe(material, "propertyGroup")
    if not probe["ok"] or probe["value"] is None:
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            "material.propertyGroup() is unavailable on this COMSOL build; a material property write cannot "
            "be verified without it",
        )
    container = probe["value"]
    if not hasattr(container, "tags"):
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            "material.propertyGroup() did not return a property-group list; the group cannot be verified",
        )
    listed = [str(item) for item in tag_list(container)]
    tags = list(listed)
    if DEFAULT_MATERIAL_PROPERTY_GROUP not in tags:
        # The default user-defined group is always present on a created
        # material and is reached by tag; it is not part of the tag list.
        tags.insert(0, DEFAULT_MATERIAL_PROPERTY_GROUP)
    created = False
    if group not in tags:
        if not create_missing:
            raise ExecutionContractError(
                "NODE_NOT_FOUND",
                f"material property group {group!r} does not exist in this material "
                f"(present: {sorted(tags)}); pass group_manage action 'create' first",
            )
        _call(container, "create", group, "Common")
        tags = [str(item) for item in tag_list(container)]
        if group not in tags:
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN",
                f"material property group {group!r} was created but the readback does not list it",
            )
        created = True
    node = _call(material, "propertyGroup", group)
    return node, {
        "container_tags": tags,
        "listed_tags": listed,
        "created": created,
        "implicit_default_group": group == DEFAULT_MATERIAL_PROPERTY_GROUP and group not in listed,
    }


def _def_group_documented(group: str) -> Mapping[str, Mapping[str, Any]] | None:
    """The documented property table for a material's ``def`` group, else ``None``.

    Only the default user-defined group carries a locally cited property table
    (``MATERIAL_DEF_GROUP_PROPERTIES``); every other group keeps the stricter
    engine-metadata-only gate.
    """
    return MATERIAL_DEF_GROUP_PROPERTIES if group == DEFAULT_MATERIAL_PROPERTY_GROUP else None


def _normalise_property_failures(rows: Sequence[Any], *, action: str,
                                 group: str | None = None) -> list[dict[str, Any]]:
    """Give every failed property entry both an ``action`` and a ``property`` key.

    ``_g2_engine.property_set`` reports a failure as ``{"name": ..., "error": ...}``;
    the W15 ops report failures by action, so the name is mirrored into
    ``property`` (and ``name`` is kept) instead of forcing callers to know which
    of the two envelopes they are looking at.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            out.append({"action": action, "error": {"code": "ENGINE_CALL_FAILED", "message": str(row)}})
            continue
        entry = dict(row)
        entry.setdefault("action", action)
        if entry.get("property") is None and isinstance(entry.get("name"), str):
            entry["property"] = entry["name"]
        if group is not None:
            entry.setdefault("group", group)
        out.append(entry)
    return out


def _require_explicit_dimension(selection: Mapping[str, Any], label: str) -> None:
    """Refuse an ``explicit`` selection that names no entity dimension.

    ``Selection.geom(<dim>)``/``geom(<gtag>, <dim>)`` is the verified call that
    fixes the entity dimension before ``set(int...)``; without it the write
    would be dispatched against whatever dimension the selection happens to
    carry, so the refusal happens here, before the first engine call.
    """
    if selection.get("kind") == "explicit" and selection.get("entity_dimension") is None:
        raise ExecutionContractError(
            "INVALID_REQUEST",
            f"{label}.entity_dimension is required for kind 'explicit': a local selection is bound with "
            f"geom(<dim>) + set(int...) and this layer never guesses the dimension",
        )


def _effective_group_tags(type_id: Any, listed: Sequence[str]) -> list[str]:
    """The group tags a caller can address: the listed ones plus the default group.

    A created material always carries the default user-defined property group,
    which ``propertyGroup().tags()`` does not list (verified by the documented
    ``material("mat1").propertyGroup("def").set(...)`` call sequence, which
    never creates ``def`` first), so readbacks report both views: ``tags`` is
    the addressable set, ``listed_tags`` is what the engine enumerated.
    """
    tags = [str(item) for item in listed]
    if type_id in MATERIAL_CONTAINER_TYPES or type_id == "Common":
        if DEFAULT_MATERIAL_PROPERTY_GROUP not in tags:
            tags.insert(0, DEFAULT_MATERIAL_PROPERTY_GROUP)
    return tags


def _material_kind(material: Any) -> dict[str, Any]:
    type_probe = call_probe(material, "getType")
    material_type_probe = call_probe(material, "materialType")
    group_probe = call_probe(material, "propertyGroup")
    groups: list[str] = []
    group_error: dict[str, Any] | None = None
    if group_probe["ok"] and group_probe["value"] is not None:
        probe = call_probe(group_probe["value"], "tags")
        if probe["ok"] and isinstance(probe["value"], (list, tuple)):
            groups = [str(item) for item in probe["value"]]
            # ``propertyGroup().tags()`` lists the additional groups; the
            # default user-defined group is reported explicitly so callers see
            # the group a property write reaches by default.
            if DEFAULT_MATERIAL_PROPERTY_GROUP not in groups:
                groups.insert(0, DEFAULT_MATERIAL_PROPERTY_GROUP)
        else:
            group_error = probe["error"]
    else:
        group_error = group_probe["error"]
    payload: dict[str, Any] = {
        "type_id": type_probe["value"] if type_probe["ok"] else None,
        # ``Material.materialType()`` is the verified accessor for the material
        # *type token* (javap: ``Material.materialType()``); ``getType()`` is
        # absent on a material node, so the type token comes from here.
        "material_type": material_type_probe["value"] if material_type_probe["ok"] else None,
        "property_groups": groups,
    }
    if material_type_probe["ok"] and material_type_probe["value"] is not None \
            and payload["type_id"] is None:
        payload["type_id_source"] = "materialType()"
    if group_error is not None:
        payload["property_group_error"] = group_error
    return payload


def _material_domain_selection(material: Any) -> dict[str, Any]:
    probe = call_probe(material, "selection")
    if not probe["ok"] or probe["value"] is None:
        return {
            "selection": None,
            "selection_error": probe["error"],
            "read_status": "UNREADABLE",
            "entities": None,
            "dimension": None,
            "geometry": None,
            "is_inheriting": None,
            "named": None,
        }
    node = probe["value"]
    summary = _selection_summary(node)
    return {"selection": "material.selection()", "selection_error": None,
            "read_status": "OK" if summary.get("entities") is not None else "PARTIAL", **summary}


# ---------------------------------------------------------------------------
# License / product probing (T042)
# ---------------------------------------------------------------------------


def used_products(worker: Any, model_tag: str) -> dict[str, Any]:
    """Read ``Model.getUsedProducts()`` -- never guessed from the file name.

    The installed 6.4 API declares ``String[] getUsedProducts()`` (javap of
    ``com.comsol.api_1.0.0.jar``: ``Model.getUsedProducts()``; the Programming
    Reference documents it as "the products that this model uses").  A failed
    probe is reported as UNKNOWN with the engine error; the value is never
    replaced by a guess.
    """
    model = bound_model(worker, model_tag)
    probe = call_probe(model, "getUsedProducts")
    if probe["ok"] and isinstance(probe["value"], (list, tuple)):
        return {
            "status": "OBSERVED",
            "source": "model.getUsedProducts()",
            "products": [str(item) for item in probe["value"]],
            "error": None,
            "allowlist_entry_required": None,
        }
    return {
        "status": "UNKNOWN",
        "source": "model.getUsedProducts()",
        "products": None,
        "error": probe["error"],
        "allowlist_entry_required": (probe["error"] or {}).get("allowlist_entry_required"),
    }


def license_probe(worker: Any, model_tag: str, *, required_products: Sequence[str] = (),
                  source: str = "caller") -> dict[str, Any]:
    """Compare required product tokens against the products the model uses.

    The probe is read-only and allocates nothing.  When the used-product list
    cannot be read the status is ``UNKNOWN`` -- never a fabricated PASS and
    never a fabricated license block.  When a required token is absent from the
    observed list the status is ``BLOCKED_LICENSE`` (T042's negative outcome):
    the caller must not treat the action as succeeded.
    """
    observed = used_products(worker, model_tag)
    required = [str(item) for item in required_products if item]
    payload: dict[str, Any] = {
        "required_products": sorted(dict.fromkeys(required)),
        "required_source": source,
        "observed": observed,
        "read_only": True,
        "allocates_license": False,
    }
    if observed["status"] != "OBSERVED":
        payload.update({
            "status": "UNKNOWN",
            "blocked": False,
            "missing_products": None,
            "message": "the used-product list could not be read; no license conclusion is asserted",
        })
        return payload
    used = {item.upper() for item in observed["products"]}
    missing = sorted({item for item in required if item.upper() not in used})
    payload.update({
        "status": "BLOCKED_LICENSE" if missing else "OK",
        "blocked": bool(missing),
        "missing_products": missing,
        "message": (
            f"required product(s) {missing} are not in the model's used-product list; the action is blocked "
            f"rather than simulated"
            if missing else
            "every required product token is present in the model's used-product list"
        ),
    })
    return payload


# ---------------------------------------------------------------------------
# material domain
# ---------------------------------------------------------------------------


def material_list(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("component",))
    component = args.get("component")
    scope = "model" if component is None else "component"
    parent_path, parent = _scope_parent(worker, model_tag, component)
    container = _call(parent, "material")
    tags = _list_tags(container)
    entries: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for tag in tags:
        node = _call(container, "get", tag)
        row: dict[str, Any] = {"tag": tag, **_material_kind(node)}
        row.update(_material_domain_selection(node))
        label_probe = call_probe(node, "label")
        row["label"] = label_probe["value"] if label_probe["ok"] else None
        entries.append(row)
        if row.get("property_group_error"):
            errors.append({"tag": tag, "error": row["property_group_error"]})
    return {
        "scope": scope,
        "component": component,
        "path": parent_path if scope == "component" else {"segments": []},
        "collection": "material",
        "material_count": len(entries),
        "materials": entries,
        "errors": errors,
        "tag_source": "model.material().tags()" if scope == "model" else f"component({component!r}).material().tags()",
    }


def _material_lookup(worker: Any, model_tag: str, path: Mapping[str, Any]) -> tuple[Any, dict[str, Any]]:
    """Resolve a material path and return (material node, canonical path dict)."""
    parsed = NodePath.from_wire(path)
    if not parsed.segments or parsed.segments[-1].collection != "material":
        raise ExecutionContractError(
            "INVALID_NODE_PATH", "path must end in a material segment (component.material or model.material)"
        )
    canonical, node = resolve_path(worker, model_tag, path)
    return node, canonical


def material_create(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("component", "tag", "type_id", "definition", "label"), ("tag", "type_id"))
    component = args.get("component")
    if component is not None:
        component = validate_tag(component, "component")
    tag = validate_tag(args["tag"], "tag")
    verified = _verified_type(args["type_id"], MATERIAL_TYPE_IDS, "material type", "Programming Reference p.148")
    type_id = verified["type_id"]
    definition = args.get("definition")
    label = args.get("label")
    if label is not None:
        label = require_string(label, "label", max_length=512)

    parent_path, parent = _scope_parent(worker, model_tag, component)
    container = _call(parent, "material")
    existing = _list_tags(container)
    if tag in existing:
        current = node_type(_call(parent, "material", tag))
        if current is not None and current != type_id:
            raise ExecutionContractError(
                "TYPE_CONFLICT",
                f"material {tag!r} already exists with type {current!r} (requested {type_id!r})",
            )
        raise ExecutionContractError("TAG_CONFLICT", f"material {tag!r} already exists")

    if component is None:
        # Programming Reference: model.material().create(<tag>,<type>) creates a
        # global material, material switch or material link.
        _call(container, "create", tag, type_id)
    else:
        _call(container, "create", tag, type_id)

    after = _list_tags(container)
    if tag not in after:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            f"material {tag!r} was created but the post-create tag readback does not show it",
        )
    node = _call(parent, "material", tag)
    readback_type = node_type(node)
    if readback_type is not None and readback_type != type_id:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            f"material {tag!r} readback type {readback_type!r} does not match the requested type {type_id!r}",
        )
    path = path_with_segment(parent_path, "material", tag)

    applied: list[dict[str, Any]] = [{
        "action": "create",
        "method": "material().create",
        "tag": tag,
        "type_id": type_id,
        "readback": {"tags": after, "type": readback_type},
    }]
    failed: list[dict[str, Any]] = []
    not_executed: list[dict[str, Any]] = []
    execution_state_unknown = False

    if label is not None:
        try:
            _call(node, "label", label)
            probe = call_probe(node, "label")
            if not (probe["ok"] and probe["value"] == label):
                raise ExecutionContractError(
                    "EXECUTION_STATE_UNKNOWN", f"material label readback did not confirm {label!r}"
                )
            applied.append({"action": "label", "requested": label, "readback": probe["value"]})
        except ExecutionContractError as exc:
            execution_state_unknown = execution_state_unknown or exc.code == "EXECUTION_STATE_UNKNOWN"
            failed.append({"name": "label", "error": {"code": exc.code, "message": str(exc)}})

    if definition is not None:
        definition = require_mapping(definition, "definition")
        reject_unknown_keys(definition, ("group", "properties"), "definition")
        group = definition.get("group") or DEFAULT_MATERIAL_PROPERTY_GROUP
        properties = definition.get("properties")
        try:
            if properties is None:
                raise ExecutionContractError(
                    "INVALID_REQUEST", "definition.properties is required when definition is given"
                )
            properties = property_definition(properties, "definition.properties")
            group_node, group_info = _material_property_group_handle(
                node, str(group), create_missing=type_id in MATERIAL_CONTAINER_TYPES
            )
            payload = _property_payload(group_node, properties, label=f"definition.properties[{group}]",
                                        documented=_def_group_documented(str(group)))
            envelope = _property_write(path_with_segment(path, "propertyGroup", str(group)),
                                       worker, model_tag, payload)
            applied.append({
                "action": "set_properties",
                "group": str(group),
                "group_readback": group_info["container_tags"],
                "properties": envelope["applied"],
                "readback_values": envelope["readback_values"],
            })
            failed.extend(_normalise_property_failures(envelope["failed"], action="set_properties",
                                                       group=str(group)))
            not_executed.extend(envelope["not_executed"])
            execution_state_unknown = execution_state_unknown or envelope["execution_state_unknown"]
        except ExecutionContractError as exc:
            execution_state_unknown = execution_state_unknown or exc.code == "EXECUTION_STATE_UNKNOWN"
            requested = list((properties or {}).keys()) if isinstance(properties, Mapping) else []
            failed.append({
                "action": "set_properties",
                "group": str(group),
                "property": requested[0] if len(requested) == 1 else None,
                "requested_properties": requested,
                "error": {"code": exc.code, "message": str(exc)},
                "partial_change": bool(applied),
                "execution_state_unknown": exc.code == "EXECUTION_STATE_UNKNOWN",
            })

    return {
        "scope": "model" if component is None else "component",
        "component": component,
        "path": path,
        "tag": tag,
        "type_id": type_id,
        "type_verified_source": verified["verified_source"],
        "license_hint": {
            "products": verified["license_products"],
            "source": "completion_data LicenseRequirement" if verified["license_products"] else "base_product",
            "note": "a hint only: the authoritative check is the model's own used-product list",
        },
        "readback": {"tags": after, "type": readback_type, "property_groups": _material_kind(node)["property_groups"]},
        "applied": applied,
        "failed": failed,
        "not_executed": not_executed,
        **_status(applied, failed, not_executed, execution_state_unknown),
    }


def material_inspect(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path", "groups", "properties"), ("path",))
    node, canonical = _material_lookup(worker, model_tag, args["path"])
    kind = _material_kind(node)
    group_probe = call_probe(node, "propertyGroup")
    requested_groups = args.get("groups")
    if requested_groups is not None:
        requested_groups = require_string_array(requested_groups, "groups")
    else:
        requested_groups = kind["property_groups"]
    requested_properties = args.get("properties")
    if requested_properties is not None:
        requested_properties = require_string_array(requested_properties, "properties")

    groups: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = list()
    for group in requested_groups:
        group = validate_tag(group, "group")
        if group not in kind["property_groups"]:
            errors.append({"group": group, "error": {"code": "NODE_NOT_FOUND",
                                                     "message": f"property group {group!r} does not exist"}})
            continue
        group_node = _call(node, "propertyGroup", group)
        names = requested_properties
        if names is None:
            probe = call_probe(group_node, "properties")
            names = [str(item) for item in probe["value"]] if probe["ok"] and probe["value"] else []
            if names is None:
                names = []
            if not probe["ok"]:
                errors.append({"group": group, "error": probe["error"]})
        read = _read_properties(group_node, names)
        errors.extend({"group": group, **row} for row in read["errors"])
        group_row: dict[str, Any] = {
            "group": group,
            "type_id": node_type(group_node),
            "property_count": len(read["properties"]),
            "properties": read["properties"],
        }
        if not requested_properties:
            input_probe = call_probe(group_node, "input")
            group_row["inputs"] = input_probe["value"] if input_probe["ok"] else None
            if not input_probe["ok"]:
                group_row["inputs_error"] = input_probe["error"]
        groups.append(group_row)

    selection = _material_domain_selection(node)
    coverage: dict[str, Any] | None = None
    if selection.get("dimension") is not None and isinstance(selection.get("entities"), list):
        coverage = {
            "entity_dimension": selection["dimension"],
            "entities": selection["entities"],
            "entity_count": len(selection["entities"]),
            "entity_list_hash": entity_list_hash(selection["entities"]),
        }
    return {
        "path": canonical,
        "tag": canonical["segments"][-1]["tag"],
        "type_id": kind["type_id"],
        "selection": selection,
        "domain_coverage": coverage,
        "property_groups": groups,
        "property_group_error": kind.get("property_group_error"),
        "material_property_source": "material.propertyGroup(<group>) ParameterEntity metadata "
                                    "(getValueType/properties/getString/getDouble)",
        "errors": errors,
        "unverified": [
            "material provenance (library source, file origin) is not exposed by the verified 6.4 API surface "
            "used here and is reported as null rather than guessed"
        ],
    }


def material_set_properties(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path", "group", "properties", "provenance"),
                              ("path", "group", "properties"))
    node, canonical = _material_lookup(worker, model_tag, args["path"])
    group = validate_tag(args["group"], "group")
    properties = property_definition(args["properties"], "properties")
    provenance = args.get("provenance")
    if provenance is not None:
        provenance = require_mapping(provenance, "provenance")

    group_node, group_info = _material_property_group_handle(node, group, create_missing=False)
    payload = _property_payload(group_node, properties, label=f"properties[{group}]",
                                documented=_def_group_documented(group))
    group_path = path_with_segment(canonical, "propertyGroup", group)
    envelope = _property_write(group_path, worker, model_tag, payload)

    applied = [{
        "group": group,
        "path": group_path,
        "properties": envelope["applied"],
        "readback_values": envelope["readback_values"],
        "group_readback": group_info["container_tags"],
    }] if envelope["applied"] or not envelope["failed"] else []
    failed = list(envelope["failed"])
    not_executed = list(envelope["not_executed"])
    result: dict[str, Any] = {
        "path": canonical,
        "group": group,
        "group_path": group_path,
        "property_count": len(payload),
        "applied": applied,
        "failed": failed,
        "not_executed": not_executed,
        "unit_policy": "expression text (including any [unit]) is written and read back as text; this layer "
                       "never converts units or evaluates an expression to a number for comparison",
        "temperature_dependency_note": "a temperature-dependent property such as k(T)/Cp(T) is written as an "
                                       "expression string and verified by exact text readback; the engine "
                                       "metadata kind is reported per property",
        **_status(applied, failed, not_executed, bool(envelope["execution_state_unknown"])),
    }
    if envelope["engine_error"]:
        result["engine_error"] = envelope["engine_error"]
    if provenance is not None:
        result["provenance"] = {**provenance, "status": "caller_declared_not_engine_verified"}
    else:
        result["provenance"] = None
    return result


def material_group_manage(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path", "action", "group", "definition", "from_group"),
                              ("path", "action"))
    node, canonical = _material_lookup(worker, model_tag, args["path"])
    action = require_string(args["action"], "action", max_length=32)
    if action not in {"create", "inspect", "update", "remove"}:
        raise ExecutionContractError(
            "INVALID_REQUEST", "action must be one of ['create', 'inspect', 'update', 'remove']"
        )
    group = validate_tag(args["group"], "group")
    definition = args.get("definition")
    if definition is not None:
        definition = require_mapping(definition, "definition")

    kind = _material_kind(node)
    existing = kind["property_groups"]
    group_probe = call_probe(node, "propertyGroup")
    if not group_probe["ok"] or group_probe["value"] is None:
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            "material.propertyGroup() is unavailable on this COMSOL build; property-group management cannot "
            "be verified",
        )
    container = group_probe["value"]

    # Entry refusals are raised *before* the first write, as the contract
    # requires: a group that cannot be created/removed/inspected/updated is a
    # request-level refusal, not a partially applied change.  Everything after
    # the first write is reported as data (applied/failed/EXECUTION_STATE_UNKNOWN).
    if action == "create" and group in existing:
        raise ExecutionContractError("TAG_CONFLICT", f"material property group {group!r} already exists")
    if action in {"remove", "inspect", "update"} and group not in existing:
        raise ExecutionContractError("NODE_NOT_FOUND", f"material property group {group!r} does not exist")
    if action == "update" and definition is None:
        raise ExecutionContractError("INVALID_REQUEST", "group_manage action 'update' requires definition")
    if action == "update":
        reject_unknown_keys(definition, ("description", "inputs", "properties"), "definition")

    applied: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    not_executed: list[dict[str, Any]] = []
    execution_state_unknown = False
    try:
        if action == "create":
            # MaterialModelList.create(String, String): tag + group type.  The
            # interpreted form "Common" is the type the documented
            # propertyGroup("def") example uses.
            _call(container, "create", group, "Common")
            after = _list_tags(container)
            if group not in after:
                raise ExecutionContractError(
                    "EXECUTION_STATE_UNKNOWN",
                    f"property group {group!r} was created but the readback does not list it",
                )
            applied.append({"action": "create", "group": group,
                            "readback": {"tags": _effective_group_tags(kind["material_type"], after),
                                         "listed_tags": after}})
            if definition is not None:
                props = definition.get("properties")
                if props is not None:
                    props = property_definition(props, "definition.properties")
                    group_node = _call(node, "propertyGroup", group)
                    payload = _property_payload(group_node, props, label=f"definition.properties[{group}]",
                                                documented=_def_group_documented(group))
                    envelope = _property_write(path_with_segment(canonical, "propertyGroup", group),
                                               worker, model_tag, payload)
                    applied.append({"action": "create.set_properties", "properties": envelope["applied"],
                                    "readback_values": envelope["readback_values"]})
                    failed.extend(_normalise_property_failures(envelope["failed"], action="set_properties",
                                                               group=group))
                    not_executed.extend(envelope["not_executed"])
                    execution_state_unknown = execution_state_unknown or envelope["execution_state_unknown"]
        elif action == "remove":
            _call(container, "remove", group)
            after = _list_tags(container)
            if group in after:
                raise ExecutionContractError(
                    "EXECUTION_STATE_UNKNOWN",
                    f"property group {group!r} was removed but the readback still lists it",
                )
            applied.append({"action": "remove", "group": group,
                            "readback": {"tags": _effective_group_tags(kind["material_type"], after),
                                         "listed_tags": after}})
        elif action == "inspect":
            group_node = _call(node, "propertyGroup", group)
            probe = call_probe(group_node, "properties")
            input_probe = call_probe(group_node, "input")
            applied.append({
                "action": "inspect",
                "group": group,
                "type_id": node_type(group_node),
                "properties": probe["value"] if probe["ok"] else None,
                "properties_error": None if probe["ok"] else probe["error"],
                "inputs": input_probe["value"] if input_probe["ok"] else None,
                "inputs_error": None if input_probe["ok"] else input_probe["error"],
            })
        else:  # update
            group_node = _call(node, "propertyGroup", group)
            recorded: dict[str, Any] = {"action": "update", "group": group}
            description = definition.get("description")
            if description is not None:
                description = require_string(description, "definition.description", max_length=512)
                _call(group_node, "descr", description)
                probe = call_probe(group_node, "descr")
                if not (probe["ok"] and probe["value"] == description):
                    raise ExecutionContractError(
                        "EXECUTION_STATE_UNKNOWN",
                        f"property group description readback did not confirm {description!r}",
                    )
                recorded["description"] = description
            inputs = definition.get("inputs")
            if inputs is not None:
                # MaterialModel.addInput(String) (javap) / removeInput(String);
                # the Programming Reference documents property-group model inputs
                # as the quantities a property can depend on.
                current_probe = call_probe(group_node, "input")
                current = ([str(item) for item in current_probe["value"]]
                           if current_probe["ok"] and current_probe["value"] else [])
                add: list[str] = []
                remove: list[str] = []
                if isinstance(inputs, Mapping):
                    reject_unknown_keys(inputs, ("add", "remove"), "definition.inputs")
                    if inputs.get("add") is not None:
                        add = require_string_array(inputs["add"], "definition.inputs.add")
                    if inputs.get("remove") is not None:
                        remove = require_string_array(inputs["remove"], "definition.inputs.remove")
                else:
                    add = require_string_array(inputs, "definition.inputs")
                changes: list[dict[str, Any]] = []
                for name in add:
                    if name in current:
                        raise ExecutionContractError(
                            "TAG_CONFLICT", f"property group input {name!r} already exists"
                        )
                    _call(group_node, "addInput", name)
                    changes.append({"method": "addInput", "name": name})
                for name in remove:
                    if name not in current:
                        raise ExecutionContractError(
                            "NODE_NOT_FOUND", f"property group input {name!r} does not exist"
                        )
                    _call(group_node, "removeInput", name)
                    changes.append({"method": "removeInput", "name": name})
                after_probe = call_probe(group_node, "input")
                readback = ([str(item) for item in after_probe["value"]]
                            if after_probe["ok"] and after_probe["value"] else [])
                expected = sorted((set(current) | set(add)) - set(remove))
                if sorted(readback) != expected:
                    raise ExecutionContractError(
                        "EXECUTION_STATE_UNKNOWN",
                        f"property group input readback {sorted(readback)} does not match the requested "
                        f"state {expected}",
                    )
                recorded["inputs"] = {"changes": changes, "readback": readback}
            props = definition.get("properties")
            if props is not None:
                props = property_definition(props, "definition.properties")
                payload = _property_payload(group_node, props, label=f"definition.properties[{group}]",
                                            documented=_def_group_documented(group))
                envelope = _property_write(path_with_segment(canonical, "propertyGroup", group),
                                           worker, model_tag, payload)
                recorded["properties"] = envelope["applied"]
                recorded["readback_values"] = envelope["readback_values"]
                failed.extend(_normalise_property_failures(envelope["failed"], action="set_properties",
                                                           group=group))
                not_executed.extend(envelope["not_executed"])
                execution_state_unknown = execution_state_unknown or envelope["execution_state_unknown"]
            applied.append(recorded)
    except ExecutionContractError as exc:
        execution_state_unknown = execution_state_unknown or exc.code == "EXECUTION_STATE_UNKNOWN"
        failed.append({
            "action": action,
            "group": group,
            "error": {"code": exc.code, "message": str(exc)},
            "partial_change": bool(applied),
            "execution_state_unknown": exc.code == "EXECUTION_STATE_UNKNOWN",
        })

    return {
        "path": canonical,
        "action": action,
        "group": group,
        "applied": applied,
        "failed": failed,
        "not_executed": not_executed,
        **_status(applied, failed, not_executed, execution_state_unknown),
    }


def material_selection_set(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path", "selection"), ("path", "selection"))
    node, canonical = _material_lookup(worker, model_tag, args["path"])
    selection = validate_selection_spec(args["selection"])
    _require_explicit_dimension(selection, "selection")
    geometry_probe = call_probe(node, "selection")
    if not geometry_probe["ok"] or geometry_probe["value"] is None:
        raise ExecutionContractError(
            "API_UNSUPPORTED", "material.selection() is unavailable; the material domain binding cannot be set"
        )
    local = geometry_probe["value"]
    component = None
    if canonical["segments"] and canonical["segments"][0].get("collection") == "component":
        component = canonical["segments"][0].get("tag")

    applied: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    not_executed: list[dict[str, Any]] = []
    execution_state_unknown = False
    try:
        before = _selection_summary(local)
        # As in physics.selection_set: the local selection write does not carry
        # a component prefix (no verified ``model(String)`` on the installed
        # XDLocalSelection), so ``owner``/``component`` are not forwarded.
        applied.append({"action": "bind_selection", **apply_local_selection(
            local, worker, model_tag, None, selection)})
        after = _selection_summary(local)
        applied[-1]["selection_before"] = before
        applied[-1]["selection_after"] = after
    except ExecutionContractError as exc:
        execution_state_unknown = execution_state_unknown or exc.code == "EXECUTION_STATE_UNKNOWN"
        failed.append({
            "action": "bind_selection",
            "error": {"code": exc.code, "message": str(exc)},
            "partial_change": bool(applied),
            "execution_state_unknown": exc.code == "EXECUTION_STATE_UNKNOWN",
        })
    return {
        "path": canonical,
        "selection_requested": selection,
        "selection_readback": _material_domain_selection(node),
        "applied": applied,
        "failed": failed,
        "not_executed": not_executed,
        **_status(applied, failed, not_executed, execution_state_unknown),
    }


def material_remove(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path",), ("path",))
    node, canonical = _material_lookup(worker, model_tag, args["path"])
    parent_path, collection, tag = split_parent_path(canonical, label="path")
    if collection != "material":
        raise ExecutionContractError("INVALID_NODE_PATH", "path must end in a material segment")
    _require_component_scope_match(parent_path)
    if parent_path.get("segments"):
        component = parent_path["segments"][0]["tag"]
    else:
        component = None
    _, parent = _scope_parent(worker, model_tag, component)
    container = _call(parent, "material")
    before = _list_tags(container)
    if tag not in before:
        raise ExecutionContractError("NODE_NOT_FOUND", f"material {tag!r} does not exist")
    lost = _material_domain_selection(node)
    lost_summary: dict[str, Any] = {}
    if isinstance(lost.get("entities"), list):
        lost_summary = {
            "entity_dimension": lost.get("dimension"),
            "entities": lost["entities"],
            "entity_count": len(lost["entities"]),
            "entity_list_hash": entity_list_hash(lost["entities"]),
        }
    _call(container, "remove", tag)
    after = _list_tags(container)
    if tag in after:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN", f"material {tag!r} was removed but the readback still lists it"
        )
    applied = [{
        "action": "remove",
        "method": "material().remove",
        "tag": tag,
        "readback": {"tags_before": before, "tags_after": after},
        "domains_losing_material": lost_summary,
    }]
    return {
        "path": canonical,
        "tag": tag,
        "applied": applied,
        "failed": [],
        "not_executed": [],
        "domains_losing_material": lost_summary,
        "note": "domains_losing_material is the material's selection immediately before removal; it is not a "
                "proof that no other material still covers those domains",
        **_status(applied, [], [], False),
    }


def _require_component_scope_match(parent_path: Mapping[str, Any]) -> None:
    segments = (parent_path or {}).get("segments") or []
    if len(segments) > 1:
        raise ExecutionContractError("INVALID_NODE_PATH", "material paths have at most one scope segment")


def material_validate(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("scope", "component", "checks"))
    scope = args.get("scope")
    component = args.get("component")
    checks = args.get("checks")
    if checks is not None:
        checks = require_mapping(checks, "checks")
        reject_unknown_keys(checks, ("required_products", "required_properties"), "checks")

    if scope is not None:
        node, canonical = _material_lookup(worker, model_tag, scope)
        materials = [(canonical["segments"][-1]["tag"], node)]
        scope_path: dict[str, Any] = canonical
        component = (canonical["segments"][0]["tag"]
                     if canonical["segments"] and canonical["segments"][0]["collection"] == "component" else None)
    else:
        if component is not None:
            component = validate_tag(component, "component")
        parent_path, parent = _scope_parent(worker, model_tag, component)
        container = _call(parent, "material")
        tags = _list_tags(container)
        materials = [(tag, _call(parent, "material", tag)) for tag in tags]
        scope_path = parent_path

    facts: list[dict[str, Any]] = []
    for tag, node in materials:
        kind = _material_kind(node)
        selection = _material_domain_selection(node)
        facts.append({
            "tag": tag,
            "type_id": kind["type_id"],
            "property_groups": kind["property_groups"],
            "property_group_error": kind.get("property_group_error"),
            "selection": selection,
        })

    violations: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    covered: list[str] = []

    # Rule family 3 (only when the caller names properties): every requested
    # property must exist in one of the material's property groups.  The check
    # is a verified read (``propertyGroup(<g>).hasProperty(<name>)``, the same
    # read rule family 2 uses), so an absent name is a violation and never
    # counted as a pass; an unreadable group list is UNKNOWN instead.
    requested_properties: list[str] = []
    if checks and checks.get("required_properties") is not None:
        requested_properties = require_string_array(checks["required_properties"],
                                                    "checks.required_properties")
    property_checks: list[dict[str, Any]] = []
    missing_properties: list[dict[str, Any]] = []
    for tag, node in materials:
        material_kind = _material_kind(node)
        if material_kind.get("property_group_error"):
            unknown.append({
                "rule": "material_required_properties",
                "material": tag,
                "reason": "the material's property groups could not be read; the requested properties are "
                          "reported as unknown, never as present",
                "property_group_error": material_kind.get("property_group_error"),
            })
            continue
        present: list[str] = []
        absent: list[str] = []
        for name in requested_properties:
            found = False
            for group in material_kind["property_groups"]:
                if _has_property(_call(node, "propertyGroup", group), name):
                    found = True
                    break
            (present if found else absent).append(name)
        property_checks.append({"material": tag, "requested": list(requested_properties),
                                "present": present, "missing": absent})
        if absent:
            missing_properties.append({
                "rule": "material_required_properties",
                "material": tag,
                "missing": absent,
                "property_groups": material_kind["property_groups"],
                "detail": f"material {tag!r} provides none of the requested properties {absent} in its "
                          f"property groups {material_kind['property_groups']}",
            })

    # Rule family 1 (bounded, documented): a material with a domain selection
    # must not leave an entity unassigned when another material already covers
    # it -- overlapping material domains are the classic material conflict.
    entity_owner: dict[tuple[Any, int], str] = {}
    for row in facts:
        selection = row["selection"]
        entities = selection.get("entities")
        dimension = selection.get("dimension")
        if selection.get("read_status") == "UNREADABLE":
            unknown.append({
                "rule": "material_domain_coverage",
                "material": row["tag"],
                "reason": "the material's domain selection could not be read; an unreadable selection is "
                          "reported as unknown, never as coverage",
                "selection_error": selection.get("selection_error"),
            })
            continue
        if not isinstance(entities, list) or dimension is None:
            unknown.append({
                "rule": "material_domain_coverage",
                "material": row["tag"],
                "reason": "the material's domain selection has no readable dimension/entity list; the entity "
                          "list is reported as unavailable rather than assumed empty",
                "selection_error": selection.get("entities_error") or selection.get("selection_error"),
            })
            continue
        if not entities:
            violations.append({
                "rule": "material_selection_non_empty",
                "material": row["tag"],
                "detail": f"material {row['tag']!r} has an explicitly empty domain selection at dimension "
                          f"{dimension}; no entity in the geometry is assigned to it",
            })
            continue
        covered.append(row["tag"])
        for entity in entities:
            key = (dimension, int(entity))
            other = entity_owner.get(key)
            if other is not None and other != row["tag"]:
                violations.append({
                    "rule": "material_domain_conflict",
                    "materials": [other, row["tag"]],
                    "detail": f"entity {entity} at dimension {dimension} is claimed by both {other!r} and "
                              f"{row['tag']!r}",
                })
            entity_owner.setdefault(key, row["tag"])

    # Rule family 2 (bounded, documented): for an enabled heat-transfer
    # interface, a Common material must carry thermalconductivity, density and
    # heatcapacity in the def group.  Other physics families are UNKNOWN.
    interfaces = _component_physics_facts(worker, model_tag, component)
    for entry in interfaces:
        type_id = entry.get("type_id")
        required = MATERIAL_REQUIRED_PROPERTIES.get(str(type_id))
        if required is None:
            unknown.append({
                "rule": "material_required_properties",
                "physics": entry.get("tag"),
                "type_id": type_id,
                "reason": "no locally verified property table covers this physics interface type",
            })
            continue
        for group, names in required.items():
            owners = []
            missing: list[str] = []
            for row in facts:
                if row["type_id"] not in {"Common", "PorousMedia", None}:
                    continue
                if group not in row["property_groups"]:
                    missing.append(row["tag"])
                    continue
                node = _material_node_for_tag(worker, model_tag, component, row["tag"])
                group_node = _call(node, "propertyGroup", group)
                present = [name for name in names if _has_property(group_node, name)]
                if len(present) != len(names):
                    missing.append(f"{row['tag']}[{', '.join(sorted(set(names) - set(present)))}]")
                else:
                    owners.append(row["tag"])
            if not facts:
                violations.append({
                    "rule": "material_definition_present",
                    "physics": entry.get("tag"),
                    "type_id": type_id,
                    "detail": "an enabled heat-transfer interface requires at least one material with "
                              f"{sorted(names)} in the {group!r} group, and this scope has no material",
                })
            elif not owners:
                violations.append({
                    "rule": "material_required_properties",
                    "physics": entry.get("tag"),
                    "type_id": type_id,
                    "group": group,
                    "missing_in": sorted(set(missing)),
                    "detail": f"no material in this scope provides {sorted(names)} in the {group!r} group, "
                              f"which the {type_id} interface requires",
                })

    license_info = license_probe(
        worker, model_tag, required_products=(checks or {}).get("required_products") or (),
        source="material.validate checks.required_products" if checks else "caller",
    )
    violations = violations + missing_properties
    return {
        "scope": scope_path,
        "component": component,
        "materials": facts,
        "physics_interfaces": interfaces,
        "violations": violations,
        "unknown": unknown,
        "required_properties": property_checks,
        "missing": missing_properties,
        "missing_count": len(missing_properties),
        "valid": (not violations) and (not unknown),
        "checked_rules": [
            "material_selection_non_empty",
            "material_domain_conflict",
            "material_definition_present",
            "material_required_properties (heat-transfer material names only)",
            "material_required_properties (caller-named properties via checks.required_properties)",
        ],
        "coverage_note": "this pre-check covers only the rules listed in checked_rules and the heat-transfer "
                         "material names verified offline; every other physics family is reported in unknown "
                         "and is never counted as a pass",
        "license_probe": license_info,
        "verdict": "VIOLATION" if violations else ("UNKNOWN" if unknown else "NO_VIOLATION_DETECTED"),
    }


def _material_node_for_tag(worker: Any, model_tag: str, component: str | None, tag: str) -> Any:
    parent_path, parent = _scope_parent(worker, model_tag, component)
    return _call(parent, "material", tag)


def _has_property(node: Any, name: str) -> bool:
    probe = call_probe(node, "hasProperty", name)
    if probe["ok"]:
        return bool(probe["value"])
    return False


# ---------------------------------------------------------------------------
# physics domain
# ---------------------------------------------------------------------------


def _physics_parent(worker: Any, model_tag: str, component: str | None) -> tuple[dict[str, Any], Any, str]:
    """Physics interfaces live in a component; a missing component is refused.

    The catalogue signature is ``component:str`` for ``physics.create`` and
    ``component:str?`` for ``physics.list``; without a component there is no
    place to put an interface, and defaulting to ``comp1`` would be a guess.
    """
    if component is None:
        model = bound_model(worker, model_tag)
        probe = call_probe(model, "component")
        tags = [str(item) for item in tag_list(probe["value"])] if probe["ok"] and probe["value"] is not None else []
        if len(tags) == 1:
            component = tags[0]
        else:
            raise ExecutionContractError(
                "INVALID_REQUEST",
                f"physics operations need an explicit component: this model has {len(tags)} components "
                f"({tags}); defaulting to the first one would be a guess",
            )
    component = validate_tag(component, "component")
    return _component_path(component), _require_component(worker, model_tag, component), component


def _physics_interface_facts(worker: Any, model_tag: str, component: str | None) -> list[dict[str, Any]]:
    if component is None:
        model = bound_model(worker, model_tag)
        probe = call_probe(model, "component")
        tags = [str(item) for item in tag_list(probe["value"])] if probe["ok"] and probe["value"] is not None else []
        facts: list[dict[str, Any]] = []
        for tag in tags:
            facts.extend(_component_physics_facts(worker, model_tag, tag))
        return facts
    return _component_physics_facts(worker, model_tag, validate_tag(component, "component"))


def _component_physics_facts(worker: Any, model_tag: str, component: str | None) -> list[dict[str, Any]]:
    if component is None:
        return _physics_interface_facts(worker, model_tag, None)
    comp = _require_component(worker, model_tag, component)
    container = _call(comp, "physics")
    facts: list[dict[str, Any]] = []
    for tag in _list_tags(container):
        node = _call(comp, "physics", tag)
        type_probe = call_probe(node, "getType")
        geom_probe = call_probe(node, "geom")
        facts.append({
            "component": component,
            "tag": tag,
            "type_id": type_probe["value"] if type_probe["ok"] else None,
            "type_error": None if type_probe["ok"] else type_probe["error"],
            "geometry": geom_probe["value"] if geom_probe["ok"] else None,
        })
    return facts


def _feature_tree(node: Any, container_method: str, *, depth: int, remaining: list[int],
                  include_properties: bool, include_selection: bool) -> dict[str, Any]:
    """Recursive feature tree for a physics interface or a feature node."""
    probe = call_probe(node, container_method)
    if not probe["ok"] or probe["value"] is None:
        return {"children": [], "child_error": probe["error"]}
    container = probe["value"]
    tags_probe = call_probe(container, "tags")
    if not tags_probe["ok"] or not isinstance(tags_probe["value"], (list, tuple)):
        return {"children": [], "child_error": tags_probe["error"]}
    children: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for tag in tags_probe["value"]:
        remaining[0] -= 1
        if remaining[0] < 0:
            errors.append({"error": {"code": "BUDGET_EXHAUSTED",
                                     "message": "the feature-tree walk hit its node budget"}})
            break
        child = _call(container, "get", tag)
        child_type = call_probe(child, "getType")
        row: dict[str, Any] = {
            "tag": str(tag),
            "type_id": child_type["value"] if child_type["ok"] else None,
        }
        child_selection = call_probe(child, "selection")
        if include_selection:
            row["selection"] = _selection_summary(child) if child_selection["ok"] else None
        if include_properties:
            names_probe = call_probe(child, "properties")
            names = [str(item) for item in names_probe["value"]] if names_probe["ok"] and names_probe["value"] else []
            read = _read_properties(child, names, limit=40)
            row["properties"] = read["properties"]
            row["property_count"] = len(read["properties"])
        if depth > 1:
            nested = _feature_tree(child, "feature", depth=depth - 1, remaining=remaining,
                                   include_properties=include_properties, include_selection=include_selection)
            row["children"] = nested["children"]
            if nested.get("child_error"):
                row["children_error"] = nested["child_error"]
        children.append(row)
    return {"children": children, "child_error": None, "errors": errors}


def _multiphysics_containers(worker: Any, model_tag: str, component: str | None) -> list[dict[str, Any]]:
    """Multiphysics coupling containers this model exposes, per verified scope.

    COMSOL has two documented holders -- ``ComponentMultiphysicsCouplingList``
    reached through ``component(<tag>).multiphysics()`` and the model-global
    ``MultiphysicsCouplingList`` through ``model.multiphysics()``.  A container
    the build does not expose is skipped (its probe error is reported), so a
    caller never sees a silently truncated list.
    """
    holders: list[tuple[str, Any]] = []
    if component is not None:
        holders.append(("component", _require_component(worker, model_tag, component)))
    holders.append(("model", bound_model(worker, model_tag)))
    containers: list[dict[str, Any]] = []
    for scope, holder in holders:
        probe = call_probe(holder, "multiphysics")
        if probe["ok"] and probe["value"] is not None:
            containers.append({"scope": scope, "path": (
                _component_path(component) if scope == "component" else {"segments": []}),
                "container": probe["value"], "error": None})
        else:
            containers.append({"scope": scope, "path": (
                _component_path(component) if scope == "component" else {"segments": []}),
                "container": None, "error": probe["error"]})
    return containers


def physics_list(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("component",))
    component = args.get("component")
    if component is not None:
        component = validate_tag(component, "component")
    parent_path, parent, resolved_component = _physics_parent(worker, model_tag, component)
    container = _call(parent, "physics")
    tags = _list_tags(container)
    entries: list[dict[str, Any]] = []
    for tag in tags:
        node = _call(parent, "physics", tag)
        type_probe = call_probe(node, "getType")
        geom_probe = call_probe(node, "geom")
        feature_tags: list[str] = []
        feature_probe = call_probe(node, "feature")
        if feature_probe["ok"] and feature_probe["value"] is not None:
            inner = call_probe(feature_probe["value"], "tags")
            if inner["ok"] and isinstance(inner["value"], (list, tuple)):
                feature_tags = [str(item) for item in inner["value"]]
        entries.append({
            "component": resolved_component,
            "tag": tag,
            "type_id": type_probe["value"] if type_probe["ok"] else None,
            "geometry": geom_probe["value"] if geom_probe["ok"] else None,
            "selection": _selection_summary(node),
            "feature_tags": feature_tags,
        })
    multiphysics_tags: list[str] = []
    multiphysics_by_scope: list[dict[str, Any]] = []
    for container_row in _multiphysics_containers(worker, model_tag, resolved_component):
        scope_tags: list[str] = []
        if container_row["container"] is not None:
            mp_tags = call_probe(container_row["container"], "tags")
            if mp_tags["ok"] and isinstance(mp_tags["value"], (list, tuple)):
                scope_tags = [str(item) for item in mp_tags["value"]]
        for item in scope_tags:
            if item not in multiphysics_tags:
                multiphysics_tags.append(item)
        multiphysics_by_scope.append({
            "scope": container_row["scope"],
            "tags": scope_tags,
            "error": container_row["error"],
        })
    return {
        "scope": "component",
        "component": resolved_component,
        "path": parent_path,
        "interface_count": len(entries),
        "interfaces": entries,
        "multiphysics_couplings": multiphysics_tags,
        "multiphysics_couplings_by_scope": multiphysics_by_scope,
        "multiphysics_scopes": "component(<tag>).multiphysics() plus the model-global model.multiphysics(); the "
                              "per-scope tag lists never overwrite one another",
        "type_source": "physics(<tag>).getType()",
        "geometry_source": "physics(<tag>).geom()",
        "tag_source": f"component({resolved_component!r}).physics().tags()",
    }


def physics_create(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("component", "tag", "type_id", "geometry", "dependent_variables"),
                              ("tag", "type_id", "geometry"))
    component = args.get("component")
    tag = validate_tag(args["tag"], "tag")
    verified = _verified_type(args["type_id"], PHYSICS_INTERFACE_IDS, "physics interface type",
                              "installed 6.4 completion data (data/completion/physics.xml)")
    type_id = verified["type_id"]
    geometry = validate_tag(args["geometry"], "geometry")
    dependent_variables = args.get("dependent_variables")
    if dependent_variables is not None:
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            "dependent_variables is not supported: ComponentPhysicsList.create(String,String,String[]) exists in "
            "javap but no offline source gives the variable-name/order contract, so this layer creates the "
            "interface with the documented geometry-bound three-argument form instead of guessing",
        )

    parent_path, parent, resolved_component = _physics_parent(worker, model_tag, component)
    geometry_node = _require_geometry(worker, model_tag, resolved_component, geometry)
    geometries = _list_tags(_call(parent, "geom"))
    if len(geometries) > 1:
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            "the installed ComponentPhysicsList has no geometry-tag overload "
            "(javap 6.4.0.293: create(String tag, String physIntID, String[] defaultFieldNames)); a component "
            f"with several geometries ({', '.join(sorted(geometries))}) cannot be bound unambiguously",
        )
    container = _call(parent, "physics")
    existing = _list_tags(container)
    if tag in existing:
        current = node_type(_call(parent, "physics", tag))
        if current is not None and current != type_id:
            raise ExecutionContractError(
                "TYPE_CONFLICT",
                f"physics interface {tag!r} already exists with type {current!r} (requested {type_id!r})",
            )
        raise ExecutionContractError("TAG_CONFLICT", f"physics interface {tag!r} already exists")

    # javap (com.comsol.api_1.0.0.jar, 6.4.0.293): the runtime container is a
    # ComponentPhysicsList whose create overloads are create(String tag,
    # String physIntID, String[] defaultFieldNames) / (..., String[][]) — there
    # is no geometry-tag overload, and the geometry argument of this operation
    # binds implicitly through the component's single geometry (validated
    # above; multi-geometry components are refused).  An empty default-field
    # name list asks the engine for the interface's own default field names.
    _call(container, "create", tag, type_id, [])
    after = _list_tags(container)
    if tag not in after:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            f"physics interface {tag!r} was created but the post-create tag readback does not show it",
        )
    node = _call(parent, "physics", tag)
    readback_type = node_type(node)
    if readback_type is not None and readback_type != type_id:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            f"physics interface {tag!r} readback type {readback_type!r} does not match the requested type "
            f"{type_id!r}",
        )
    readback_geom = call_probe(node, "geom")
    if readback_geom["ok"] and readback_geom["value"] is not None and readback_geom["value"] != geometry:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            f"physics interface {tag!r} readback geometry {readback_geom['value']!r} does not match the "
            f"requested geometry {geometry!r}",
        )
    path = path_with_segment(parent_path, "physics", tag)
    applied = [{
        "action": "create",
        "method": "physics().create(tag, type_id, geometry)",
        "tag": tag,
        "type_id": type_id,
        "geometry": geometry,
        "readback": {"tags": after, "type": readback_type, "geometry": readback_geom["value"] if readback_geom["ok"] else None},
        "geometry_length_unit": (lambda p: str(p["value"]) if p["ok"] and p["value"] is not None else None)(
            call_probe(geometry_node, "lengthUnit")),
    }]
    return {
        "scope": "component",
        "component": resolved_component,
        "path": path,
        "tag": tag,
        "type_id": type_id,
        "type_verified_source": verified["verified_source"],
        "license_hint": {
            "products": verified["license_products"],
            "source": "completion_data LicenseRequirement" if verified["license_products"] else "base_product",
            "note": "a hint only; physics.create does not allocate a license on its own",
        },
        "selection": _selection_summary(node),
        "applied": applied,
        "failed": [],
        "not_executed": [],
        **_status(applied, [], [], False),
    }


def physics_inspect(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path", "depth", "properties"), ("path",))
    depth = args.get("depth")
    depth = 2 if depth is None else require_int(depth, "depth", minimum=1, maximum=6)
    include_properties = bool(args.get("properties"))
    if args.get("properties") is not None:
        require_bool(args["properties"], "properties")
    node, canonical = _physics_lookup(worker, model_tag, args["path"])
    tag = canonical["segments"][-1]["tag"]
    type_probe = call_probe(node, "getType")
    geom_probe = call_probe(node, "geom")
    remaining = [200]
    trees: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for collection, method in PHYSICS_CHILD_COLLECTIONS:
        tree = _feature_tree(node, method, depth=depth, remaining=remaining,
                             include_properties=include_properties, include_selection=True)
        if tree.get("child_error"):
            errors.append({"collection": collection, "error": tree["child_error"]})
            continue
        if tree["children"]:
            trees.append({"collection": collection, "children": tree["children"]})
        errors.extend({"collection": collection, **row} for row in tree.get("errors") or [])
    payload: dict[str, Any] = {
        "path": canonical,
        "tag": tag,
        "type_id": type_probe["value"] if type_probe["ok"] else None,
        "geometry": geom_probe["value"] if geom_probe["ok"] else None,
        "selection": _selection_summary(node),
        "depth": depth,
        "children": trees,
        "errors": errors,
        "readback": "physics(<tag>).getType()/geom()/selection() and feature()/field()/prop() containers",
    }
    if type_probe["ok"] and type_probe["value"] is not None and str(type_probe["value"]) not in PHYSICS_INTERFACE_IDS:
        payload["type_vocabulary_note"] = (
            f"interface type {type_probe['value']!r} is not in this layer's local interface vocabulary; it was "
            f"observed on the node (not created by this layer)"
        )
    return payload


def _physics_lookup(worker: Any, model_tag: str, path: Mapping[str, Any]) -> tuple[Any, dict[str, Any]]:
    parsed = NodePath.from_wire(path)
    if not parsed.segments or parsed.segments[-1].collection not in {"physics", "feature", "field", "prop"}:
        raise ExecutionContractError(
            "INVALID_NODE_PATH",
            "path must end in a physics/feature/field/prop segment",
        )
    canonical, node = resolve_path(worker, model_tag, path)
    return node, canonical


def _multiphysics_lookup(worker: Any, model_tag: str, path: Mapping[str, Any]) -> tuple[Any, dict[str, Any]]:
    """Resolve a coupling path: ``model.multiphysics(<tag>)`` or ``component.multiphysics(<tag>)``.

    ``multiphysics`` is one of the validated path collections
    (``_g2_contract.ACCESSOR_METHODS``), so ``resolve_node_path`` performs the
    engine call; this wrapper only refuses a path that does not end at a
    coupling.  Both holders exist in the installed API: the model-global
    ``MultiphysicsCouplingList`` and the component-scoped
    ``ComponentMultiphysicsCouplingList`` (javap of the installed
    ``apiplugins/com.comsol.api_1.0.0.jar``).
    """
    parsed = NodePath.from_wire(path)
    if not parsed.segments or parsed.segments[-1].collection != "multiphysics":
        raise ExecutionContractError(
            "INVALID_NODE_PATH",
            "path must end in a multiphysics coupling segment: model.multiphysics(<tag>) or "
            "component(<tag>).multiphysics(<tag>)",
        )
    canonical, node = resolve_path(worker, model_tag, path)
    return node, canonical


def _physics_interface_of(canonical: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    segments = canonical["segments"]
    for index, segment in enumerate(segments):
        if segment.get("collection") == "physics":
            return {"segments": segments[: index + 1]}, str(segment.get("tag"))
    raise ExecutionContractError("INVALID_NODE_PATH", "path does not contain a physics segment")


def physics_remove(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path",), ("path",))
    node, canonical = _physics_lookup(worker, model_tag, args["path"])
    interface_path, tag = _physics_interface_of(canonical)
    if len(canonical["segments"]) != len(interface_path["segments"]):
        raise ExecutionContractError(
            "INVALID_NODE_PATH",
            "physics.remove deletes a whole interface; for a child feature use physics.feature_remove",
        )
    parent_path, parent, resolved_component = _physics_parent(
        worker, model_tag,
        canonical["segments"][0]["tag"] if canonical["segments"][0].get("collection") == "component" else None,
    )
    container = _call(parent, "physics")
    before = _list_tags(container)
    if tag not in before:
        raise ExecutionContractError("NODE_NOT_FOUND", f"physics interface {tag!r} does not exist")

    dependents: list[dict[str, Any]] = []
    scanned_scopes: list[dict[str, Any]] = []
    for container_row in _multiphysics_containers(worker, model_tag, resolved_component):
        if container_row["container"] is None:
            scanned_scopes.append({"scope": container_row["scope"], "tags": [],
                                   "error": container_row["error"]})
            continue
        mp_container = container_row["container"]
        mp_tags_probe = call_probe(mp_container, "tags")
        scope_tags = ([str(item) for item in mp_tags_probe["value"]]
                      if mp_tags_probe["ok"] and isinstance(mp_tags_probe["value"], (list, tuple)) else [])
        scanned_scopes.append({"scope": container_row["scope"], "tags": scope_tags,
                               "error": None if mp_tags_probe["ok"] else mp_tags_probe["error"]})
        for mp_tag in scope_tags:
            coupling = _call(mp_container, "get", mp_tag)
            probe = call_probe(coupling, "properties")
            names = [str(item) for item in probe["value"]] if probe["ok"] and probe["value"] else []
            referenced = False
            for name in names:
                value_probe = call_probe(coupling, "getString", name)
                if value_probe["ok"] and isinstance(value_probe["value"], str) and value_probe["value"] == tag:
                    referenced = True
                    dependents.append({
                        "kind": "multiphysics",
                        "tag": str(mp_tag),
                        "property": name,
                        "value": value_probe["value"],
                    })
            if not referenced:
                ident = _feature_identity(coupling)
                if ident["tag"] == tag:
                    dependents.append({"kind": "multiphysics", "tag": str(mp_tag), "property": "tag"})

    _engine_call(container, "remove", tag)
    after = _list_tags(container)
    if tag in after:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            f"physics interface {tag!r} was removed but the readback still lists it",
        )
    applied = [{
        "action": "remove",
        "method": "physics().remove",
        "tag": tag,
        "readback": {"tags_before": before, "tags_after": after},
        "dependents_observed_before_removal": dependents,
    }]
    return {
        "path": interface_path,
        "component": resolved_component,
        "tag": tag,
        "applied": applied,
        "failed": [],
        "not_executed": [],
        "dependent_references": dependents,
        "dependents_scanned_scopes": scanned_scopes,
        "note": "dependent_references lists multiphysics/coupling references observed before the removal, "
                "across the component and model coupling scopes; the study-side dependency list is reported "
                "through physics.validate where a study object is reachable",
        **_status(applied, [], [], False),
    }


def physics_feature_create(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("parent", "tag", "type_id", "entity_dimension", "properties"),
                              ("parent", "tag", "type_id"))
    parent_path = require_mapping(args["parent"], "parent")
    tag = validate_tag(args["tag"], "tag")
    type_id = require_string(args["type_id"], "type_id", max_length=128)
    if len(parent_path.get("segments") or []) == 0:
        raise ExecutionContractError("INVALID_NODE_PATH", "parent must be a resolved node path")
    entity_dimension = args.get("entity_dimension")
    if entity_dimension is not None:
        entity_dimension = require_int(entity_dimension, "entity_dimension", minimum=0, maximum=3)
    properties = args.get("properties")
    if properties is not None:
        properties = property_definition(properties, "properties")

    canonical_parent, parent_node = resolve_path(worker, model_tag, parent_path, label="parent")
    parent_identity = _feature_identity(parent_node)
    parent_tags_probe = call_probe(parent_node, "feature")
    if not parent_tags_probe["ok"] or parent_tags_probe["value"] is None:
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            f"parent node type {parent_identity['type_id']!r} does not expose feature(); physics features are "
            f"created under a physics interface or an existing physics feature",
        )
    container = parent_tags_probe["value"]
    existing = _list_tags(container)
    if tag in existing:
        current = node_type(_call(container, "get", tag))
        if current is not None and current != type_id:
            raise ExecutionContractError(
                "TYPE_CONFLICT",
                f"feature {tag!r} already exists under the parent with type {current!r} (requested {type_id!r})",
            )
        raise ExecutionContractError("TAG_CONFLICT", f"feature {tag!r} already exists under this parent")

    if entity_dimension is None:
        _call(container, "create", tag, type_id)
    else:
        _call(container, "create", tag, type_id, entity_dimension)
    after = _list_tags(container)
    if tag not in after:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            f"feature {tag!r} was created but the post-create tag readback does not show it",
        )
    node = _call(container, "get", tag)
    readback_type = node_type(node)
    if readback_type is not None and readback_type != type_id:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            f"feature {tag!r} readback type {readback_type!r} does not match the requested type {type_id!r}",
        )
    path = path_with_segment(canonical_parent, "feature", tag)
    verified_token = type_id in PHYSICS_FEATURE_TOKENS
    applied = [{
        "action": "create",
        "method": "feature().create(tag, type_id[, dim])",
        "tag": tag,
        "type_id": type_id,
        "entity_dimension": entity_dimension,
        "readback": {"tags": after, "type": readback_type},
        "token_source": ("installed 6.4 completion data name token" if verified_token
                         else "engine getType() readback only (token not in the local feature-token list)"),
    }]
    failed: list[dict[str, Any]] = []
    not_executed: list[dict[str, Any]] = []
    execution_state_unknown = False
    if properties is not None:
        try:
            payload = _property_payload(node, properties, label=f"properties[{tag}]")
            envelope = _property_write(path, worker, model_tag, payload)
            applied.append({"action": "set_properties", "properties": envelope["applied"],
                            "readback_values": envelope["readback_values"]})
            failed.extend(envelope["failed"])
            not_executed.extend(envelope["not_executed"])
            execution_state_unknown = execution_state_unknown or envelope["execution_state_unknown"]
        except ExecutionContractError as exc:
            execution_state_unknown = execution_state_unknown or exc.code == "EXECUTION_STATE_UNKNOWN"
            failed.append({"action": "set_properties", "error": {"code": exc.code, "message": str(exc)},
                           "partial_change": bool(applied)})
    return {
        "parent_path": canonical_parent,
        "path": path,
        "tag": tag,
        "type_id": type_id,
        "entity_dimension": entity_dimension,
        "selection": _selection_summary(node),
        "applied": applied,
        "failed": failed,
        "not_executed": not_executed,
        **_status(applied, failed, not_executed, execution_state_unknown),
    }


def physics_feature_update(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path", "properties"), ("path", "properties"))
    properties = property_definition(args["properties"], "properties")
    node, canonical = _physics_lookup(worker, model_tag, args["path"])
    if canonical["segments"][-1]["collection"] not in {"feature", "physics", "field", "prop"}:
        raise ExecutionContractError("INVALID_NODE_PATH", "path must point at a physics feature node")
    payload = _property_payload(node, properties, label="properties")
    envelope = _property_write(canonical, worker, model_tag, payload)
    applied = [{
        "action": "update",
        "path": canonical,
        "tag": canonical["segments"][-1]["tag"],
        "type_id": node_type(node),
        "properties": envelope["applied"],
        "readback_values": envelope["readback_values"],
    }] if envelope["applied"] or not envelope["failed"] else []
    failed = list(envelope["failed"])
    not_executed = list(envelope["not_executed"])
    return {
        "path": canonical,
        "property_count": len(payload),
        "applied": applied,
        "failed": failed,
        "not_executed": not_executed,
        **_status(applied, failed, not_executed, bool(envelope["execution_state_unknown"])),
    }


def physics_feature_remove(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path",), ("path",))
    node, canonical = _physics_lookup(worker, model_tag, args["path"])
    if canonical["segments"][-1]["collection"] != "feature":
        raise ExecutionContractError(
            "INVALID_NODE_PATH", "physics.feature_remove requires a feature segment; use physics.remove for an "
                                 "interface"
        )
    parent_path, _, _ = split_parent_path(canonical, label="path")
    _, parent_node = resolve_path(worker, model_tag, parent_path, label="path")
    container_probe = call_probe(parent_node, "feature")
    if not container_probe["ok"] or container_probe["value"] is None:
        raise ExecutionContractError("API_UNSUPPORTED", "the parent does not expose feature()")
    container = container_probe["value"]
    tag = str(canonical["segments"][-1]["tag"])
    before = _list_tags(container)
    if tag not in before:
        raise ExecutionContractError("NODE_NOT_FOUND", f"feature {tag!r} does not exist under this parent")
    type_readback = node_type(node)
    try:
        _engine_call(container, "remove", tag)
    except ExecutionContractError as exc:
        # A feature COMSOL protects (for example the interface's default
        # domain feature) must surface as a truthful failure, not a retry loop.
        raise ExecutionContractError(
            exc.code,
            f"the engine refused to remove feature {tag!r} ({type_readback!r}): {exc}. A default/protected "
            f"child feature cannot be removed by this layer",
        ) from exc
    after = _list_tags(container)
    if tag in after:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN", f"feature {tag!r} was removed but the readback still lists it"
        )
    applied = [{
        "action": "remove",
        "method": "feature().remove",
        "tag": tag,
        "type_id": type_readback,
        "readback": {"tags_before": before, "tags_after": after},
    }]
    return {
        "path": canonical,
        "tag": tag,
        "applied": applied,
        "failed": [],
        "not_executed": [],
        **_status(applied, [], [], False),
    }


def physics_selection_set(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path", "selection", "level"), ("path", "selection"))
    level = args.get("level")
    if level is None:
        level = "auto"
    if level not in {"auto", "interface", "feature"}:
        raise ExecutionContractError("INVALID_REQUEST", "level must be one of ['auto', 'interface', 'feature']")
    node, canonical = _physics_lookup(worker, model_tag, args["path"])
    last_collection = canonical["segments"][-1]["collection"]
    if level == "interface" and last_collection != "physics":
        raise ExecutionContractError(
            "INVALID_NODE_PATH", "level 'interface' requires a path that ends at a physics interface"
        )
    if level == "feature" and last_collection != "feature":
        raise ExecutionContractError(
            "INVALID_NODE_PATH", "level 'feature' requires a path that ends at a physics feature"
        )
    selection = validate_selection_spec(args["selection"])
    _require_explicit_dimension(selection, "selection")
    selection_probe = call_probe(node, "selection")
    if not selection_probe["ok"] or selection_probe["value"] is None:
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            f"the node at {canonical} does not expose selection(); level={level!r} cannot be verified here",
        )
    local = selection_probe["value"]
    component = (canonical["segments"][0]["tag"]
                 if canonical["segments"] and canonical["segments"][0].get("collection") == "component" else None)

    applied: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    not_executed: list[dict[str, Any]] = []
    execution_state_unknown = False
    before = _selection_summary(local)
    try:
        # ``component`` is deliberately *not* forwarded into the local
        # selection: ``ModelEntity.model(String)`` is documented for the
        # model-global expression containers, while the installed build gives
        # no verified ``model(String)`` on a physics/physics-feature
        # ``XDLocalSelection``.  A component-scoped selection needs no prefix,
        # and calling an unverified setter would be a guess with a side effect.
        binding = apply_local_selection(local, worker, model_tag, None, selection)
        after = _selection_summary(local)
        # An inherited selection cannot be written: COMSOL reports the write as
        # a failure and this layer keeps that failure visible instead of
        # reporting success for a value it never set.
        if str(selection["kind"]) == "inherited":
            status = "INHERIT_REQUESTED"
        else:
            status = "BOUND"
        applied.append({
            "action": "bind_selection",
            "level": level,
            "level_source": f"path segment collection {last_collection!r}",
            "status": status,
            **binding,
            "selection_before": before,
            "selection_after": after,
        })
    except ExecutionContractError as exc:
        execution_state_unknown = execution_state_unknown or exc.code == "EXECUTION_STATE_UNKNOWN"
        failed.append({
            "action": "bind_selection",
            "level": level,
            "error": {"code": exc.code, "message": str(exc)},
            "partial_change": bool(applied),
            "execution_state_unknown": exc.code == "EXECUTION_STATE_UNKNOWN",
        })
    return {
        "path": canonical,
        "level": level,
        "path_collection": last_collection,
        "selection_requested": selection,
        "selection_readback": _selection_summary(node),
        "applied": applied,
        "failed": failed,
        "not_executed": not_executed,
        "inheritance_note": "COMSOL refuses an entity write on a selection that inherits from a parent/physics "
                            "selection; such a refusal is reported in failed[] with its code instead of being "
                            "retried as an override",
        **_status(applied, failed, not_executed, execution_state_unknown),
    }


def _multiphysics_parent(worker: Any, model_tag: str, component: str | None) -> tuple[dict[str, Any], Any, str]:
    if component is None:
        return {"segments": []}, bound_model(worker, model_tag), "model"
    component = validate_tag(component, "component")
    return _component_path(component), _require_component(worker, model_tag, component), component


def physics_multiphysics_manage(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(
        arguments, ("action", "path", "definition", "component", "geometry", "space_dimension"),
        ("action",),
    )
    action = require_string(args["action"], "action", max_length=32)
    if action not in {"create", "inspect", "update", "remove"}:
        raise ExecutionContractError(
            "INVALID_REQUEST", "action must be one of ['create', 'inspect', 'update', 'remove']"
        )
    definition = args.get("definition")
    if definition is not None:
        definition = require_mapping(definition, "definition")
    component = args.get("component")
    if component is not None:
        component = validate_tag(component, "component")

    applied: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    not_executed: list[dict[str, Any]] = []
    execution_state_unknown = False
    dispatch_started = False
    verified: dict[str, Any] | None = None
    try:
        if action == "create":
            if definition is None:
                raise ExecutionContractError(
                    "INVALID_REQUEST", "multiphysics_manage action 'create' requires definition"
                )
            reject_unknown_keys(definition, ("tag", "type_id", "geometry", "space_dimension"),
                                "definition")
            tag = validate_tag(definition.get("tag"), "definition.tag")
            verified = _verified_type(
                definition.get("type_id"), MULTIPHYSICS_COUPLING_IDS, "multiphysics coupling type",
                "Programming Reference model.multiphysics() page / LiveLink for MATLAB documentation",
            )
            geometry = definition.get("geometry") or args.get("geometry")
            if geometry is None:
                raise ExecutionContractError("INVALID_REQUEST", "definition.geometry is required")
            geometry = validate_tag(geometry, "definition.geometry")
            space_dimension = definition.get("space_dimension", args.get("space_dimension"))
            if space_dimension is not None:
                space_dimension = require_int(space_dimension, "space_dimension", minimum=-1, maximum=3)
            parent_path, parent, _ = _multiphysics_parent(worker, model_tag, component)
            if component is not None:
                _require_geometry(worker, model_tag, component, geometry)
            container = _call(parent, "multiphysics")
            existing = _list_tags(container)
            if tag in existing:
                current = node_type(_call(container, "get", tag))
                if current is not None and current != verified["type_id"]:
                    raise ExecutionContractError(
                        "TYPE_CONFLICT",
                        f"multiphysics coupling {tag!r} already exists with type {current!r} "
                        f"(requested {verified['type_id']!r})",
                    )
                raise ExecutionContractError("TAG_CONFLICT", f"multiphysics coupling {tag!r} already exists")
            dispatch_started = True
            if space_dimension is None:
                _engine_call(container, "create", tag, verified["type_id"], geometry)
            else:
                _engine_call(container, "create", tag, verified["type_id"], geometry, space_dimension)
            after = _list_tags(container)
            if tag not in after:
                raise ExecutionContractError(
                    "EXECUTION_STATE_UNKNOWN",
                    f"multiphysics coupling {tag!r} was created but the readback does not list it",
                )
            node = _call(container, "get", tag)
            readback_type = node_type(node)
            if readback_type is not None and readback_type != verified["type_id"]:
                raise ExecutionContractError(
                    "EXECUTION_STATE_UNKNOWN",
                    f"multiphysics coupling {tag!r} readback type {readback_type!r} does not match the requested "
                    f"type {verified['type_id']!r}",
                )
            applied.append({
                "action": "create",
                "method": "multiphysics().create(tag, type_id, geometry[, sdim])",
                "tag": tag,
                "type_id": verified["type_id"],
                "geometry": geometry,
                "space_dimension": space_dimension,
                "readback": {"tags": after, "type": readback_type},
                "path": path_with_segment(parent_path, "multiphysics", tag),
            })
        elif action in {"inspect", "update", "remove"}:
            path = args.get("path")
            if path is None:
                raise ExecutionContractError("INVALID_REQUEST", f"action {action!r} requires path")
            node, canonical = _multiphysics_lookup(worker, model_tag, path)
            tag = str(canonical["segments"][-1]["tag"])
            parent_path, _, _ = split_parent_path(canonical, label="path")
            _, parent_node = resolve_path(worker, model_tag, parent_path, label="path")
            container = _call(parent_node, "multiphysics")
            before = _list_tags(container)
            if tag not in before:
                raise ExecutionContractError("NODE_NOT_FOUND", f"multiphysics coupling {tag!r} does not exist")
            if action == "inspect":
                probe = call_probe(node, "properties")
                names = [str(item) for item in probe["value"]] if probe["ok"] and probe["value"] else []
                read = _read_properties(node, names, limit=80)
                applied.append({
                    "action": "inspect",
                    "path": canonical,
                    "tag": tag,
                    "type_id": node_type(node),
                    "properties": read["properties"],
                    "errors": read["errors"],
                })
            elif action == "update":
                if definition is None:
                    raise ExecutionContractError(
                        "INVALID_REQUEST", "multiphysics_manage action 'update' requires definition"
                    )
                reject_unknown_keys(definition, ("properties",), "definition")
                properties = definition.get("properties")
                if properties is None:
                    raise ExecutionContractError(
                        "INVALID_REQUEST", "definition.properties is required for an update"
                    )
                properties = property_definition(properties, "definition.properties")
                payload = _property_payload(node, properties, label="definition.properties")
                dispatch_started = True
                envelope = _property_write(canonical, worker, model_tag, payload)
                applied.append({"action": "update", "path": canonical, "tag": tag,
                                "type_id": node_type(node), "properties": envelope["applied"],
                                "readback_values": envelope["readback_values"]})
                failed.extend(envelope["failed"])
                not_executed.extend(envelope["not_executed"])
                execution_state_unknown = execution_state_unknown or envelope["execution_state_unknown"]
            else:
                dispatch_started = True
                _engine_call(container, "remove", tag)
                after = _list_tags(container)
                if tag in after:
                    raise ExecutionContractError(
                        "EXECUTION_STATE_UNKNOWN",
                        f"multiphysics coupling {tag!r} was removed but the readback still lists it",
                    )
                applied.append({"action": "remove", "path": canonical, "tag": tag,
                                "readback": {"tags_before": before, "tags_after": after}})
        else:  # pragma: no cover - guarded above
            raise ExecutionContractError("INVALID_REQUEST", f"unsupported action {action!r}")
    except ExecutionContractError as exc:
        if not dispatch_started:
            # Nothing was sent to the engine: this is the contract's
            # "refuse before the write" case, so it propagates as a typed error
            # instead of being reported as a partially applied change.
            raise
        execution_state_unknown = execution_state_unknown or exc.code == "EXECUTION_STATE_UNKNOWN"
        failed.append({
            "action": action,
            "error": {"code": exc.code, "message": str(exc)},
            "partial_change": bool(applied),
            "execution_state_unknown": exc.code == "EXECUTION_STATE_UNKNOWN",
        })

    result: dict[str, Any] = {
        "action": action,
        "component": component,
        "applied": applied,
        "failed": failed,
        "not_executed": not_executed,
        "coupling_vocabulary": sorted(MULTIPHYSICS_COUPLING_IDS),
        "coupling_vocabulary_source": "Programming Reference model.multiphysics() page and the documented "
                                      "LiveLink/help create calls; an unknown token is refused before the write",
        **_status(applied, failed, not_executed, execution_state_unknown),
    }
    if verified is not None:
        result["license_hint"] = {
            "products": verified["license_products"],
            "source": "coupling type vocabulary entry",
            "note": "the coupling's own product requirement is product specific; see physics.validate for the "
                    "model's used-product readback",
        }
    return result


def physics_initial_values_set(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(
        arguments, ("path", "definition", "tag", "create_missing"), ("path", "definition")
    )
    definition = require_mapping(args["definition"], "definition")
    reject_unknown_keys(definition, ("properties", "type_id", "tag"), "definition")
    properties = definition.get("properties")
    if properties is None:
        raise ExecutionContractError("INVALID_REQUEST", "definition.properties is required")
    properties = property_definition(properties, "definition.properties")
    node, canonical = _physics_lookup(worker, model_tag, args["path"])
    last_collection = canonical["segments"][-1]["collection"]
    type_id = definition.get("type_id") or "InitialValues"
    type_id = require_string(type_id, "definition.type_id", max_length=64)

    applied: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    not_executed: list[dict[str, Any]] = []
    execution_state_unknown = False
    try:
        if last_collection == "physics":
            feature_probe = call_probe(node, "feature")
            if not feature_probe["ok"] or feature_probe["value"] is None:
                raise ExecutionContractError("API_UNSUPPORTED", "the physics interface does not expose feature()")
            container = feature_probe["value"]
            tags = _list_tags(container)
            tag = definition.get("tag") or args.get("tag")
            if tag is not None:
                tag = validate_tag(tag, "tag")
            else:
                candidates = [name for name in tags if _feature_type(container, name) == type_id]
                tag = candidates[0] if candidates else None
            create_missing = args.get("create_missing")
            if tag is None:
                if not (create_missing is None or create_missing is True):
                    raise ExecutionContractError(
                        "NODE_NOT_FOUND",
                        f"no {type_id!r} feature exists on this interface and create_missing is false",
                    )
                tag = _next_free_tag(tags, "init")
                _call(container, "create", tag, type_id)
                readback = _list_tags(container)
                if tag not in readback:
                    raise ExecutionContractError(
                        "EXECUTION_STATE_UNKNOWN",
                        f"{type_id!r} feature {tag!r} was created but the readback does not list it",
                    )
                applied.append({"action": "create_feature", "tag": tag, "type_id": type_id,
                                "readback": {"tags": readback}})
            else:
                actual = _feature_type(container, tag)
                if actual is None:
                    raise ExecutionContractError(
                        "NODE_NOT_FOUND", f"feature {tag!r} does not exist on this physics interface"
                    )
                if actual != type_id:
                    raise ExecutionContractError(
                        "TYPE_CONFLICT",
                        f"feature {tag!r} exists with type {actual!r}, not the requested {type_id!r}",
                    )
            target = _call(container, "get", tag)
            path = path_with_segment(canonical, "feature", tag)
        elif last_collection in {"feature", "field", "prop"}:
            target = node
            path = canonical
            tag = canonical["segments"][-1]["tag"]
            actual = node_type(target)
            if actual is not None and actual != type_id:
                raise ExecutionContractError(
                    "TYPE_CONFLICT",
                    f"node {tag!r} has type {actual!r}, not the requested initial-values type {type_id!r}",
                )
        else:
            raise ExecutionContractError(
                "INVALID_NODE_PATH", "path must point at a physics interface or one of its features"
            )

        payload = _property_payload(target, properties, label="definition.properties")
        envelope = _property_write(path, worker, model_tag, payload)
        applied.append({
            "action": "set_initial_values",
            "path": path,
            "tag": tag,
            "type_id": type_id,
            "properties": envelope["applied"],
            "readback_values": envelope["readback_values"],
        })
        failed.extend(envelope["failed"])
        not_executed.extend(envelope["not_executed"])
        execution_state_unknown = execution_state_unknown or envelope["execution_state_unknown"]
    except ExecutionContractError as exc:
        execution_state_unknown = execution_state_unknown or exc.code == "EXECUTION_STATE_UNKNOWN"
        failed.append({
            "action": "set_initial_values",
            "error": {"code": exc.code, "message": str(exc)},
            "partial_change": bool(applied),
            "execution_state_unknown": exc.code == "EXECUTION_STATE_UNKNOWN",
        })
    return {
        "path": canonical,
        "applied": applied,
        "failed": failed,
        "not_executed": not_executed,
        "note": "only the InitialValues feature (and an explicit feature path) is written here; a previous-solution "
                "source is a property of that feature and is written through its own verified property names",
        **_status(applied, failed, not_executed, execution_state_unknown),
    }


def _feature_type(container: Any, tag: str) -> str | None:
    try:
        node = _call(container, "get", tag)
    except ExecutionContractError:
        return None
    return node_type(node)


def _next_free_tag(tags: Sequence[str], prefix: str) -> str:
    index = 1
    while f"{prefix}{index}" in tags:
        index += 1
    return f"{prefix}{index}"


def physics_validate(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("scope", "checks", "component"))
    checks = args.get("checks")
    if checks is not None:
        checks = require_mapping(checks, "checks")
        reject_unknown_keys(checks, ("required_products",), "checks")
    scope = args.get("scope")
    component = args.get("component")
    if component is not None:
        component = validate_tag(component, "component")

    if scope is not None:
        node, canonical = _physics_lookup(worker, model_tag, scope)
        interface_path, tag = _physics_interface_of(canonical)
        if len(canonical["segments"]) != len(interface_path["segments"]):
            raise ExecutionContractError(
                "INVALID_NODE_PATH", "physics.validate scope must be a physics interface, not one of its features"
            )
        facts = [{
            "component": (canonical["segments"][0]["tag"]
                          if canonical["segments"][0].get("collection") == "component" else None),
            "tag": tag,
            "type_id": node_type(node),
            "node": node,
        }]
        scope_path: dict[str, Any] = {"segments": []}
    else:
        raw_facts = _physics_interface_facts(worker, model_tag, component)
        facts = []
        for row in raw_facts:
            comp = row.get("component")
            node = _call(_require_component(worker, model_tag, comp), "physics", row["tag"]) if comp else None
            facts.append({**row, "node": node})
        scope_path = {"segments": []}

    violations: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    for row in facts:
        node = row.get("node")
        type_id = row.get("type_id")
        entry: dict[str, Any] = {
            "component": row.get("component"),
            "tag": row.get("tag"),
            "type_id": type_id,
            "geometry": row.get("geometry") if "geometry" in row else (call_probe(node, "geom")["value"] if node is not None else None),
        }
        if node is None:
            unknown.append({"rule": "physics_interface_reachable", "physics": row.get("tag"),
                            "reason": "the interface node could not be resolved"})
            observations.append(entry)
            continue
        entry["selection"] = _selection_summary(node)
        feature_probe = call_probe(node, "feature")
        features: list[dict[str, Any]] = []
        if feature_probe["ok"] and feature_probe["value"] is not None:
            container = feature_probe["value"]
            for child_tag in _list_tags(container):
                child = _call(container, "get", child_tag)
                child_selection = _selection_summary(child)
                features.append({"tag": child_tag, "type_id": node_type(child), "selection": child_selection})
                if child_selection.get("dimension") == 0 and child_selection.get("entities"):
                    # A point feature carrying a domain entity id is a
                    # dimension mismatch that COMSOL would not silently fix.
                    pass
        entry["features"] = features
        entry["feature_count"] = len(features)
        observations.append(entry)

        if type_id is not None and str(type_id) not in PHYSICS_INTERFACE_IDS:
            unknown.append({
                "rule": "physics_type_vocabulary",
                "physics": row.get("tag"),
                "type_id": type_id,
                "reason": "the interface type is outside this layer's local 6.4 interface vocabulary",
            })
        if not features:
            violations.append({
                "rule": "physics_feature_lifecycle",
                "physics": row.get("tag"),
                "type_id": type_id,
                "detail": "the interface exposes no physics feature at all; a created interface always carries at "
                          "least its default domain feature",
            })
        for feature in features:
            dimension = feature["selection"].get("dimension")
            entities = feature["selection"].get("entities")
            if isinstance(entities, list) and not entities:
                unknown.append({
                    "rule": "feature_selection_non_empty",
                    "physics": row.get("tag"),
                    "feature": feature["tag"],
                    "reason": "the feature reports an explicitly empty entity list; whether that is valid depends "
                              "on the interface, so it is not counted as a violation",
                })
            if feature["selection"].get("is_inheriting") is True and isinstance(entities, list) and entities:
                unknown.append({
                    "rule": "feature_selection_inheritance",
                    "physics": row.get("tag"),
                    "feature": feature["tag"],
                    "reason": "the feature inherits its selection and still reports entities; the parent link is "
                              "reported rather than asserted",
                })
            geometry = feature["selection"].get("geometry")
            if geometry is not None and entry.get("geometry") is not None and geometry != entry.get("geometry"):
                violations.append({
                    "rule": "feature_geometry_mismatch",
                    "physics": row.get("tag"),
                    "feature": feature["tag"],
                    "detail": f"the feature selection points at geometry {geometry!r} while the interface is bound "
                              f"to {entry['geometry']!r}",
                })
            if dimension is not None and dimension > 3:
                violations.append({
                    "rule": "feature_dimension_range",
                    "physics": row.get("tag"),
                    "feature": feature["tag"],
                    "detail": f"selection dimension {dimension} is outside 0..3",
                })

    license_info = license_probe(
        worker, model_tag, required_products=(checks or {}).get("required_products") or (),
        source="physics.validate checks.required_products" if checks else "caller",
    )
    if license_info["status"] == "BLOCKED_LICENSE":
        violations.append({
            "rule": "license_required_products",
            "detail": f"required product(s) {license_info['missing_products']} are not in the model's "
                      f"used-product list; dependent actions are blocked rather than simulated",
            "missing_products": license_info["missing_products"],
        })
    return {
        "scope": scope_path,
        "component": component,
        "interfaces": observations,
        "violations": violations,
        "unknown": unknown,
        "checked_rules": [
            "physics_interface_reachable",
            "physics_feature_lifecycle",
            "feature_selection_non_empty",
            "feature_selection_inheritance",
            "feature_geometry_mismatch",
            "feature_dimension_range",
            "license_required_products (only when checks.required_products is given)",
        ],
        "coverage_note": "this validation covers only the rules listed in checked_rules; it is not a statement "
                         "about physical correctness or solver convergence",
        "license_probe": license_info,
        "verdict": "VIOLATION" if violations else ("UNKNOWN" if unknown else "NO_VIOLATION_DETECTED"),
    }


# ---------------------------------------------------------------------------
# publishing table
# ---------------------------------------------------------------------------

OPERATIONS: dict[str, Callable[[Any, str, dict], dict]] = {
    "material.list": material_list,
    "material.create": material_create,
    "material.inspect": material_inspect,
    "material.set_properties": material_set_properties,
    "material.group_manage": material_group_manage,
    "material.selection_set": material_selection_set,
    "material.remove": material_remove,
    "material.validate": material_validate,
    "physics.list": physics_list,
    "physics.create": physics_create,
    "physics.inspect": physics_inspect,
    "physics.remove": physics_remove,
    "physics.feature_create": physics_feature_create,
    "physics.feature_update": physics_feature_update,
    "physics.feature_remove": physics_feature_remove,
    "physics.selection_set": physics_selection_set,
    "physics.multiphysics_manage": physics_multiphysics_manage,
    "physics.initial_values_set": physics_initial_values_set,
    "physics.validate": physics_validate,
}

__all__ = [
    "MATERIAL_CONTAINER_TYPES",
    "MATERIAL_DEF_GROUP_PROPERTIES",
    "MATERIAL_REQUIRED_PROPERTIES",
    "MATERIAL_TYPE_IDS",
    "MULTIPHYSICS_COUPLING_IDS",
    "OPERATIONS",
    "PHYSICS_FEATURE_TOKENS",
    "PHYSICS_INTERFACE_IDS",
    "UNVERIFIED_PATHS",
    "license_probe",
    "used_products",
]
