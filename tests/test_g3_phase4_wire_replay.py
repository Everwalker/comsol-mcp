"""Replay the production driver's real G3 wire payloads against the fake trees.

``tools/phase4_run_mcp.py`` is the acceptance driver that runs against a live
COMSOL Server, so *its* payloads are the real wire form every G3 domain
operation has to accept.  The offline suite never exercised that direction: the
module tests call the operations with their own internal spelling (a property
*mapping*, a ``component/geom1`` path built by hand), while the driver sends the
action catalogue's shape (a ``PropertySet`` row array, ``study``/``geometry``
arguments as ``NodePath`` objects).  This module closes that gap, offline:

* the payloads are read out of the driver's own source (no hand-copied
  fixtures), with the driver's own parser defaults for the dynamic leaves, so a
  payload edit cannot drift away from what is replayed here;
* each payload is dispatched through the *real* operation code against the
  repository's own fake COMSOL node tree, in a flow that mirrors the live case
  (create -> update -> readback), and
* the audit checks that no catalogue ``NodePath`` argument in the driver is
  given a bare tag string.
"""
from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any, Mapping

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
DRIVER_PATH = TOOLS / "phase4_run_mcp.py"
CATALOGUE_PATH = ROOT / "docs" / "comsol_mcp_design_v1" / "02_ACTION_CATALOG.json"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: Operations whose wire form this module replays.
G3_OPS = frozenset({
    "physics.create", "physics.feature_create", "physics.feature_update", "physics.selection_set",
    "geometry.feature_create", "geometry.workplane_edit", "geometry.array_create",
    "geometry.build", "geometry.measure",
    "material.create", "material.set_properties", "material.validate", "material.selection_set",
    "mesh.feature_create", "mesh.feature_update", "mesh.build",
    "study.step_create", "study.step_update", "study.run",
    "solver.feature_update", "node.property_get", "node.find",
    "selection.create", "selection.measure", "selection.validate",
    "variable.set", "variable.get",
})

#: The driver payload sites this replay gates on, addressed by
#: ``(function, operation, occurrence inside that function, namespace)``.  Addressing a site by its
#: enclosing function keeps the mapping stable when helper call sites are added elsewhere in the
#: file (an index among *all* calls of an operation moves as soon as any helper gains a call, which
#: is exactly what happened when the container helper was added).  The namespace is the flow whose
#: local variables the payload closes over (0 = chain A / W13-W15 cases, 1 = chain B, 2 = chain C /
#: T020).
SITES: dict[str, tuple[str, str, int, int]] = {
    # W13_T006 variables: the variable group is a NodePath, never a tag string
    "t006_variable_set": ("_case_w13_t006", "variable.set", 0, 0),
    "t006_variable_get": ("_case_w13_t006", "variable.get", 0, 0),
    # W13_T015 units: the interface create plus the volume source create/update and the wrong-unit probe
    "units_physics_interface": ("_case_w13_t015", "physics.create", 0, 0),
    "units_source_create": ("_case_w13_t015", "physics.feature_create", 0, 0),
    "units_source_update": ("_case_w13_t015", "physics.feature_update", 0, 0),
    "units_wrong_unit_probe": ("_case_w13_t015", "physics.feature_update", 1, 0),
    # W13_T048 selections: the selection reads take a SelectionSpec, the edits a NodePath
    "t048_selection_create": ("_case_w13_t048", "selection.create", 0, 0),
    "t048_selection_measure": ("_case_w13_t048", "selection.measure", 0, 0),
    "t048_selection_validate": ("_case_w13_t048", "selection.validate", 0, 0),
    "t048_boundary_set": ("_case_w13_t048", "physics.selection_set", 0, 0),
    # W14_T009 static geometry: the work-plane actions and the array edit
    "t009_workplane_array": ("_case_w14_t009", "geometry.workplane_edit", 0, 0),
    "t009_workplane_edit": ("_case_w14_t009", "geometry.workplane_edit", 1, 0),
    "t009_array": ("_case_w14_t009", "geometry.array_create", 0, 0),
    "t009_measure": ("_case_w14_t009", "geometry.measure", 1, 0),
    # W15_T007 selections
    "t007_selection_update": ("_case_w15_t007", "physics.feature_update", 0, 0),
    "t007_child_feature": ("_case_w15_t007", "physics.feature_create", 0, 0),
    "t007_selection_set": ("_case_w15_t007", "physics.selection_set", 0, 0),
    # W15 materials
    "material_expressions": ("_case_w15_t017", "material.set_properties", 0, 0),
    "material_tensor": ("_case_w15_t017", "material.set_properties", 1, 0),
    "material_validate": ("_case_w15_t017", "material.validate", 0, 0),
    # W16_T018 mesh
    "t018_mesh_feature": ("_case_w16_t018", "mesh.feature_create", 0, 0),
    "t018_mesh_update": ("_case_w16_t018", "mesh.feature_update", 0, 0),
    # W16_T019 chain A (temp1 #0, temp2 #1, ins1 #2)
    "chain_a_block": ("_case_w16_t019_chain_a", "geometry.feature_create", 0, 0),
    "chain_a_material": ("_case_w16_t019_chain_a", "material.create", 0, 0),
    "chain_a_temp1": ("_case_w16_t019_chain_a", "physics.feature_create", 0, 0),
    "chain_a_temp2": ("_case_w16_t019_chain_a", "physics.feature_create", 1, 0),
    "chain_a_mesh": ("_case_w16_t019_chain_a", "mesh.feature_create", 0, 0),
    "chain_a_step": ("_case_w16_t019_chain_a", "study.step_create", 0, 0),
    # W16_T019 chain B (init1 #0, temp1 #0, ins1 #1)
    "chain_b_block": ("_case_w16_t019_chain_b", "geometry.feature_create", 0, 1),
    "chain_b_material": ("_case_w16_t019_chain_b", "material.create", 0, 1),
    "chain_b_temp1": ("_case_w16_t019_chain_b", "physics.feature_create", 0, 1),
    "chain_b_mesh": ("_case_w16_t019_chain_b", "mesh.feature_create", 0, 1),
    "chain_b_step": ("_case_w16_t019_chain_b", "study.step_create", 0, 1),
    # W16_T019 chain C
    "chain_c_step_update": ("_case_w16_t019_chain_c", "study.step_update", 0, 2),
    # W16_T020 solver
    "t020_solver_update": ("_case_w16_t020", "solver.feature_update", 0, 2),
    "t020_unknown_property": ("_case_w16_t020", "solver.feature_update", 1, 2),
    "t020_noop_probe": ("_case_w16_t020", "solver.feature_update", 2, 2),
}

#: The driver's own subcase outcome for each site: applied, or refused truthfully.
REFUSED = frozenset({"t007_selection_update", "t020_unknown_property", "t020_noop_probe"})
APPLIED = frozenset(name for name in SITES if name not in REFUSED)


# ---------------------------------------------------------------------------
# module loading
# ---------------------------------------------------------------------------


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def driver():
    """The production driver, imported for its parser defaults and constants."""
    return _load("phase4_driver_wire_replay", DRIVER_PATH)


@pytest.fixture(scope="module")
def fakes():
    """The repository's fake COMSOL node trees (published by the G3 test modules)."""
    return {
        "w13": _load("g3_w13_fake_for_replay", ROOT / "tests" / "test_g3_w13.py"),
        "w14": _load("g3_w14_fake_for_replay", ROOT / "tests" / "test_g3_w14.py"),
        "w15": _load("g3_w15_fake_for_replay", ROOT / "tests" / "test_g3_w15.py"),
        "w16": _load("g3_w16_fake_for_replay", ROOT / "tests" / "test_g3_w16.py"),
    }


# ---------------------------------------------------------------------------
# payload extraction from the driver's own source
# ---------------------------------------------------------------------------


def _driver_source() -> str:
    return DRIVER_PATH.read_text(encoding="utf-8")


def _operation_of(node: ast.Call) -> str | None:
    keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
    operation = keywords.get("operation")
    if isinstance(operation, ast.Constant) and isinstance(operation.value, str):
        return operation.value
    if isinstance(node.func, ast.Attribute) and node.func.attr == "action" and node.args:
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    return None


def _enclosing_functions(tree: ast.AST) -> list[tuple[int, int, str]]:
    """(start, end, name) for every function definition, for line -> function lookups."""
    rows: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            rows.append((node.lineno, node.end_lineno or node.lineno, node.name))
    return sorted(rows)


def _call_bodies(source: str) -> list[tuple[int, str, str, ast.AST]]:
    """Every G3 call in the driver, in source order: (line, function, operation, body).

    The enclosing function is the site's stable address: helper call sites come and go, and an
    occurrence index among *all* calls of an operation moves with them.
    """
    tree = ast.parse(source)
    functions = _enclosing_functions(tree)
    out: list[tuple[int, str, str, ast.AST]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        operation = _operation_of(node)
        if operation not in G3_OPS:
            continue
        keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
        body = keywords.get("arguments")
        if body is None and isinstance(node.func, ast.Attribute) and len(node.args) >= 2:
            body = node.args[1]
        if body is None:
            continue
        function = next((name for start, end, name in functions if start <= node.lineno <= end), "?")
        out.append((node.lineno, function, operation, body))
    return sorted(out)


def _evaluate(node: ast.AST, namespace: Mapping[str, Any]) -> Any:
    """Evaluate a payload expression with the driver's own values."""
    expression = ast.Expression(body=node)
    ast.fix_missing_locations(expression)
    code = compile(expression, filename=str(DRIVER_PATH), mode="eval")
    # The expression is the driver's own literal; the builtin surface is the one
    # its payload bodies use to build wire values from the CLI defaults.
    allowed = {"str": str, "float": float, "int": int, "len": len, "bool": bool, "list": list, "dict": dict}
    return eval(code, {"__builtins__": allowed}, dict(namespace))  # noqa: S307


def _namespaces(driver) -> list[dict[str, Any]]:
    """The driver's own local values, per flow: chain A, chain B, chain C/T020."""
    args = driver.build_parser().parse_args([])
    geom_path = {"segments": [{"collection": "component", "tag": "comp1"},
                              {"collection": "geom", "tag": "geom1"}]}
    geometry_path = {"segments": [{"collection": "component", "tag": "comp1"},
                                  {"collection": "geom", "tag": "geom1"}]}
    physics_path = {"segments": [{"collection": "component", "tag": "comp1"},
                                 {"collection": "physics", "tag": "ht"}]}
    mesh_path = {"segments": [{"collection": "component", "tag": "comp1"},
                              {"collection": "mesh", "tag": "mesh1"}]}
    material_path = {"segments": [{"collection": "component", "tag": "comp1"},
                                  {"collection": "material", "tag": "mat1"}]}
    study_path = {"segments": [{"collection": "study", "tag": "std1"}]}
    temperature_path = {"segments": [{"collection": "component", "tag": "comp1"},
                                     {"collection": "physics", "tag": "ht"},
                                     {"collection": "feature", "tag": args.temperature_tag}]}
    array_path = {"segments": [{"collection": "component", "tag": "comp1"},
                               {"collection": "geom", "tag": "geom1"},
                               {"collection": "feature", "tag": args.array_tag}]}
    meshed_path = {"segments": [{"collection": "component", "tag": "comp1"},
                                {"collection": "mesh", "tag": "mesh1"},
                                {"collection": "feature", "tag": args.mesh_size_tag}]}
    solver_path = {"segments": [{"collection": "sol", "tag": "sol1"},
                                {"collection": "feature", "tag": "st1"},
                                {"collection": "feature", "tag": "s1"}]}
    common = {"args": args, "paths": {"geometry": geom_path}, "geometry_path": geometry_path,
              "physics_path": physics_path, "mesh_path": mesh_path, "material_path": material_path,
              "study_path": study_path, "array_path": array_path, "meshed_path": meshed_path,
              "solver_path": solver_path,
              # The driver's own locals of the W13_T048 selection flow: the group the variable calls
              # address (a NodePath, not a tag) and the SelectionSpec the selection reads take.
              "group_path": {"segments": [{"collection": "component", "tag": "comp1"},
                                          {"collection": "variable", "tag": args.variable_group}]},
              "selection_spec": {"kind": "named", "component": "comp1", "tag": args.selection_tag},
              "measure_metrics": ["n_entities", "volume", "bounding_box"],
              # The driver's own locals of the W13_T006 variable flow: the route picks the wire
              # spelling and the two values are what the calls write and re-read.
              "route": "domain", "first_two": "phase4_q1=2", "first_three": "phase4_q2=3",
              # The driver's own local of the W14_T009 work-plane flow.
              "workplane_path": {"segments": [{"collection": "component", "tag": "comp1"},
                                              {"collection": "geom", "tag": "geom1"},
                                              {"collection": "feature", "tag": args.work_plane_tag}]},
              # The driver's own local: the solver *feature* path the update targets.
              "solver_feature_path": {**solver_path, "segments": [*solver_path["segments"],
                                                                  {"collection": "feature",
                                                                   "tag": args.solver_feature_tag}]},
              # The driver's own locals: the writable sub-feature property *derived from* the
              # inspect payload's publish table (the fake's tree publishes ``maxiter`` there, so a
              # hard-coded name/kind/value would be exactly what this replay must catch).
              "solver_update_name": "maxiter", "solver_update_kind": "int32", "solver_update_value": 50,
              # The driver's own material definitions, written by the chains.
              "CHAIN_A_MATERIAL_PROPERTIES": driver.CHAIN_A_MATERIAL_PROPERTIES,
              "CHAIN_B_MATERIAL_PROPERTIES": driver.CHAIN_B_MATERIAL_PROPERTIES,
              # The driver's own time parser (a DoubleArray tlist carries seconds).
              "_seconds_value": driver._seconds_value}
    return [
        {**common, "config": driver.CHAIN_A, "spec": driver.BENCHMARK_A, "feature_path": temperature_path},
        {**common, "config": driver.CHAIN_B, "spec": driver.BENCHMARK_B, "feature_path": temperature_path},
        {**common, "config": driver.CHAIN_A, "spec": driver.BENCHMARK_A, "feature_path": temperature_path},
    ]


_CACHE: dict[str, Any] = {}
_GATED = {(function, operation, index) for function, operation, index, _namespace in SITES.values()}


def _collect(driver) -> tuple[dict[tuple[str, str, int, int], dict[str, Any]], list[str]]:
    """{(function, operation, occurrence, namespace): {line, payload}} plus evaluation failures."""
    if "sites" in _CACHE:
        return _CACHE["sites"], _CACHE["failures"]
    namespaces = _namespaces(driver)
    occurrences: dict[tuple[str, str], int] = {}
    sites: dict[tuple[str, str, int, int], dict[str, Any]] = {}
    failures: list[str] = []
    for line, function, operation, body in _call_bodies(_driver_source()):
        index = occurrences.get((function, operation), 0)
        occurrences[(function, operation)] = index + 1
        values: list[Any] = []
        for namespace in namespaces:
            try:
                values.append(_evaluate(body, namespace))
            except Exception as exc:  # pragma: no cover - surfaced by the gate test
                values.append(exc)
        evaluated = 0
        for namespace_index, payload in enumerate(values):
            if isinstance(payload, dict):
                evaluated += 1
                sites[(function, operation, index, namespace_index)] = {
                    "line": line, "function": function, "operation": operation, "payload": payload}
        if evaluated == 0 and (function, operation, index) in _GATED:
            failures.append(f"{function}: {operation} #{index} (line {line}): {values[0]!r}")
    _CACHE["sites"] = sites
    _CACHE["failures"] = failures
    return sites, failures


def _site(driver, name: str) -> dict[str, Any]:
    function, operation, index, namespace = SITES[name]
    sites, _failures = _collect(driver)
    key = (function, operation, index, namespace)
    assert key in sites, (f"the driver payload for {name} ({operation} #{index} in {function}) "
                          f"was not extracted")
    return sites[key]


def test_every_gated_driver_payload_is_extractable(driver, fakes):
    """Every payload this replay gates on is read out of the driver's source."""
    del fakes
    sites, failures = _collect(driver)
    assert not failures, f"driver payloads could not be evaluated: {failures}"
    missing = [name for name, key in SITES.items() if key not in sites]
    assert not missing, f"the driver payload sites this replay gates on were not found: {sorted(missing)}"
    property_sites = {key: site for key, site in sites.items()
                      if isinstance(site["payload"].get("properties"), list)}
    assert len(property_sites) >= 20, f"expected the driver's PropertySet call sites, found {len(property_sites)}"
    for key, site in property_sites.items():
        for row in site["payload"]["properties"]:
            assert isinstance(row, Mapping) and set(row) == {"name", "value"}, (
                f"the driver sends a {site['operation']} property row that is not a PropertySet row: {row!r}"
            )
            value = row["value"]
            assert isinstance(value, Mapping) and {"kind", "shape", "data"} <= set(value), (
                f"the driver sends a {site['operation']} property value that is not a wire TypedValue: {value!r}"
            )
    json.dumps({f"{key}": site["payload"] for key, site in sites.items()}, default=str)


def test_the_driver_paths_use_the_resolvable_collections(driver, fakes):
    """The path collections in the replayed payloads are the ones the trees resolve.

    ``geom`` (not ``geometry``) is the collection every recorded live payload
    uses; a collection the resolver cannot walk is refused as ``INVALID_NODE_PATH``.
    """
    del fakes
    collections: set[str] = set()
    for name in SITES:
        for value in _walk(_site(driver, name)["payload"]):
            if isinstance(value, Mapping) and set(value) == {"collection", "tag"}:
                collections.add(str(value["collection"]))
    assert collections, "no path collections were found in the replayed payloads"
    assert "geometry" not in collections, "the driver still sends the unresolvable 'geometry' collection"
    assert collections <= {"component", "geom", "material", "physics", "mesh", "study", "sol", "feature",
                           "variable", "func", "selection"}, (
        f"unexpected path collection in the driver payloads: {sorted(collections)}"
    )


def _walk(value: Any):
    if isinstance(value, Mapping):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk(item)


# ---------------------------------------------------------------------------
# the catalogue's NodePath arguments must never be bare tag strings
# ---------------------------------------------------------------------------


def _catalogue_nodepath_arguments() -> dict[str, set[str]]:
    payload = json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))
    rows = payload["operations"] if isinstance(payload, dict) else payload
    out: dict[str, set[str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        operation = row.get("operation_id")
        schema = row.get("input_schema") or {}
        properties = schema.get("properties") or {}
        names = {name for name, spec in properties.items()
                 if isinstance(spec, dict) and str(spec.get("$ref", "")).endswith("#/$defs/NodePath")}
        if operation and names:
            out[str(operation)] = names
    return out


def test_no_string_literal_in_a_nodepath_argument():
    """A NodePath argument is never a JSON string in the driver's G3 payloads.

    The catalogue declares ``study``/``geometry``/``path``/``parent``/... as
    ``NodePath`` objects; a bare ``"std1"`` tag is not a path and the domain
    layer refuses it (``INVALID_NODE_PATH``).  Reading the literal payloads
    catches that class of drift before a live run does.
    """
    declared = _catalogue_nodepath_arguments()
    violations: list[str] = []
    checked = 0
    for line, function, operation, body in _call_bodies(_driver_source()):
        names = declared.get(operation)
        if not names or not isinstance(body, ast.Dict):
            continue
        for key, value in zip(body.keys, body.values):
            if not isinstance(key, ast.Constant) or key.value not in names:
                continue
            checked += 1
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                violations.append(f"line {line} ({function}): {operation}.{key.value} = "
                                  f"{value.value!r} (a tag string, not a NodePath)")
    assert checked >= 20, f"expected the driver's NodePath arguments to be audited, only {checked} were found"
    assert not violations, "NodePath arguments given a bare tag string: " + "; ".join(violations)


def test_every_evaluated_nodepath_argument_is_a_path_object(driver):
    """Every catalogue ``NodePath`` argument *evaluates* to a path object, not a bare tag.

    The literal audit above only sees payload bodies written as dict literals with constant values;
    a payload that computes its path, or a call site a helper evaluates later, is invisible to it.
    This one evaluates the driver's own payload expressions with the driver's own values (the same
    machinery the replay uses) and checks the value that would reach the wire: the live run sent
    ``geometry: 'geom1'`` to ``geometry.array_create`` and a bare ``'var1'`` group to
    ``variable.set``, and both were refused by the domain layer as "must be a NodePath".
    """
    declared = _catalogue_nodepath_arguments()
    namespaces = _namespaces(driver)
    violations: list[str] = []
    checked = 0
    evaluated_sites = 0
    for line, function, operation, body in _call_bodies(_driver_source()):
        names = declared.get(operation)
        if not names:
            continue
        for namespace in namespaces:
            try:
                payload = _evaluate(body, namespace)
            except Exception:
                continue
            if not isinstance(payload, Mapping):
                continue
            evaluated_sites += 1
            for name in sorted(names):
                if name not in payload:
                    continue
                checked += 1
                value = payload[name]
                if isinstance(value, Mapping) and isinstance(value.get("segments"), list):
                    continue
                violations.append(f"line {line} ({function}): {operation}.{name} = {value!r} "
                                  f"is not a NodePath object")
    assert evaluated_sites >= 40, f"expected the driver's payload sites to be evaluated, only {evaluated_sites} were"
    assert checked >= 30, f"expected the driver's NodePath arguments to be audited, only {checked} were found"
    assert not violations, "NodePath arguments that are not path objects: " + "; ".join(violations)


# ---------------------------------------------------------------------------
# replay helpers
# ---------------------------------------------------------------------------


def _dispatch(operation: str, payload: Mapping[str, Any], worker: Any) -> dict[str, Any]:
    from comsol_mcp._g3_ops import DISPATCH

    return DISPATCH[operation](worker, "Model", dict(payload))


def _property_rows_of(result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Every applied property row of an operation envelope, whatever its nesting."""
    rows: list[Mapping[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            if "name" in value and ("readback" in value or "readback_match" in value or "value" in value):
                rows.append(value)
            for item in value.values():
                visit(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item)

    visit(result.get("applied") or [])
    visit({key: value for key, value in result.items() if key in ("properties", "readback_values", "actions")})
    return rows


def _written_value(result: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    """The requested TypedValue of one applied property, from wherever it is carried."""
    for row in _property_rows_of(result):
        if row.get("name") != name:
            continue
        for key in ("value", "readback"):
            value = row.get(key)
            if isinstance(value, Mapping) and "data" in value:
                return value
        if "data" in row:
            return row
    for block in result.get("applied") or []:
        if not isinstance(block, Mapping):
            continue
        for key in ("readback_values", "properties"):
            value = block.get(key)
            if isinstance(value, Mapping) and name in value and isinstance(value[name], Mapping):
                return value[name]
    raise AssertionError(f"no applied property row for {name!r} in {json.dumps(result, default=str)[:600]}")


def _apply(driver, worker, name: str) -> dict[str, Any]:
    site = _site(driver, name)
    result = _dispatch(site["operation"], site["payload"], worker)
    assert isinstance(result, Mapping), f"{name}: {site['operation']} did not return an envelope"
    for row in _property_rows_of(result):
        assert row.get("readback_match") is not False, (
            f"{name} (line {site['line']}): {site['operation']} read back a different value than it wrote: {row}"
        )
    return result


def _apply_ok(driver, worker, name: str, *, writes_properties: bool = True) -> dict[str, Any]:
    """Replay one payload that the driver expects to apply."""
    site = _site(driver, name)
    result = _apply(driver, worker, name)
    assert result.get("status") == "APPLIED", (
        f"{name} (line {site['line']}): {site['operation']} did not apply: {json.dumps(result, default=str)[:1200]}"
    )
    assert not result.get("failed"), f"{name} (line {site['line']}): failed rows: {result['failed']}"
    if writes_properties and site["payload"].get("properties") != []:
        assert _property_rows_of(result), f"{name} (line {site['line']}): {site['operation']} wrote no property value"
    return result


def _apply_report(driver, worker, name: str) -> dict[str, Any]:
    """Replay one report-style payload (``*.validate`` publishes a verdict, not a status)."""
    site = _site(driver, name)
    result = _apply(driver, worker, name)
    assert result.get("verdict") and result.get("missing") is not None, (
        f"{name} (line {site['line']}): {site['operation']} did not publish a validation verdict: "
        f"{json.dumps(result, default=str)[:800]}"
    )
    return result


def _apply_refused(driver, worker, name: str) -> Any:
    """Replay one payload the driver expects to be refused truthfully."""
    from comsol_mcp._execution_contract import ExecutionContractError

    site = _site(driver, name)
    try:
        result = _apply(driver, worker, name)
    except ExecutionContractError as exc:
        assert exc.code not in {"INVALID_NODE_PATH", "NOT_IMPLEMENTED", "UNSUPPORTED_OPERATION"}, (
            f"{name} (line {site['line']}): {site['operation']} refused for a wire-shape reason: {exc.code}: {exc}"
        )
        return exc
    assert result.get("status") != "APPLIED" and (result.get("failed") or result.get("missing")), (
        f"{name} (line {site['line']}): {site['operation']} reported success although the driver expects a truthful "
        f"refusal: {json.dumps(result, default=str)[:800]}"
    )
    return result


def _fresh_study_model(w16) -> Any:
    """A model whose std1 carries no study step yet (a driver chain creates them)."""
    model = w16.build_model()
    model.collections["study"].items["std1"].collections["feature"].items.clear()
    return model


def _fresh_material_model(w15) -> Any:
    """A model whose component carries no material yet (the driver's chain creates mat1)."""
    model = w15.build_model()
    model.components["comp1"].material_list.items.clear()
    return model


# ---------------------------------------------------------------------------
# the flows (one per driver case), replaying the driver's own payloads
# ---------------------------------------------------------------------------


def test_replay_units_flow_physics_features(driver, fakes):
    """W13_T015: volume source create/update and the wrong-unit probe."""
    w15 = fakes["w15"]
    worker = w15.worker_for(w15.build_model())
    created = _apply_ok(driver, worker, "units_source_create")
    assert created["tag"] == "hs1"
    updated = _apply_ok(driver, worker, "units_source_update")
    assert _written_value(updated, "Q0")["data"] == "1e2[W/m^3]"
    # The wrong-unit probe: the write point is documented as ``W/m^3``, so a ``W/m^2`` expression is
    # refused *before* the feature is created or any property is dispatched.  The M1 run recorded
    # the opposite (``1e5[W/m^2]`` accepted into ``HeatSource.Q0`` with no warning) and filed it as
    # an implementation gap; the product now owns that gate, so the executable payload is asserted
    # against the refusal it must produce.
    refused = _apply_refused(driver, worker, "units_wrong_unit_probe")
    assert getattr(refused, "code", None) == "UNIT_DIMENSION_MISMATCH", refused


def test_replay_geometry_flow_array_and_workplane_edit(driver, fakes):
    """W14_T009: the row-array Array create and the local work-plane edit."""
    w14 = fakes["w14"]
    geom = w14.geometry("geom1", features={"wp1": w14.workplane_feature("wp1")})
    geom.feature_list.factory = lambda tag, *args: w14.array_feature(tag)
    worker, _, _ = w14.world(geometries={"geom1": geom})
    created = _apply_ok(driver, worker, "t009_array")
    assert created["inputs"]["applied"][0]["objects"] == ["r1"]
    # The work-plane edit addresses the nested 2D sequence of wp1.
    inner = w14.geometry("wp1_inner", features={"r1": w14.rectangle_feature("r1")})
    nested = w14.geometry("geom1", features={"wp1": w14.workplane_feature("wp1", inner=inner)})
    worker2, _, _ = w14.world(geometries={"geom1": nested})
    edited = _apply_ok(driver, worker2, "t009_workplane_edit")
    assert edited["features_after"] == ["r1"]


def test_replay_selection_flow(driver, fakes):
    """W15_T007: named selections bind; a feature update cannot inherit one."""
    w15 = fakes["w15"]
    worker = w15.worker_for(w15.build_model())
    physics_path = {"segments": [{"collection": "component", "tag": "comp1"},
                                 {"collection": "physics", "tag": "ht"}]}
    temperature = _dispatch("physics.feature_create", {
        "parent": physics_path, "tag": "temp1", "type_id": "TemperatureBoundary", "entity_dimension": 2,
    }, worker)
    assert temperature["status"] == "APPLIED"
    # The driver's next payload writes the inherited selection through
    # physics.feature_update: the property is not a writable feature property,
    # so it has to be refused before the write.
    _apply_refused(driver, worker, "t007_selection_update")
    child = _apply_ok(driver, worker, "t007_child_feature")
    assert isinstance(child["parent_path"], dict)
    assert child["path"]["segments"][-1] == {"collection": "feature", "tag": "phase4_child_probe"}


def test_replay_material_flow(driver, fakes):
    """W15 material case: expressions, the tensor, validate and the readback."""
    w15 = fakes["w15"]
    worker = w15.worker_for(w15.build_model())
    first = _apply_ok(driver, worker, "material_expressions")
    assert _written_value(first, "thermalconductivity")["data"] == [["10[W/(m*K)]", "0", "0"],
                                                                    ["0", "10[W/(m*K)]", "0"],
                                                                    ["0", "0", "10[W/(m*K)]"]]
    tensor = _apply_ok(driver, worker, "material_tensor")
    assert _written_value(tensor, "thermalconductivity")["data"] == [["10", "0", "0"], ["0", "20", "0"], ["0", "0", "30"]]
    report = _apply_report(driver, worker, "material_validate")
    assert report["missing"], "the driver's missing-property probe must be reported as missing"
    assert report["valid"] is False


def test_replay_mesh_flow(driver, fakes):
    """W16_T018: the local Size feature with a named selection, then the update."""
    w16 = fakes["w16"]
    worker = w16.worker_for(w16.build_model())
    created = _apply_ok(driver, worker, "t018_mesh_feature")
    assert created["selection"]["kind"] == "named"
    assert _written_value(created, "hmax")["data"] == 3e-4
    updated = _apply_ok(driver, worker, "t018_mesh_update")
    assert _written_value(updated, "hmax")["data"] == 2e-4


def test_replay_chain_a_flow(driver, fakes):
    """W16_T019 chain A: geometry, material, physics, mesh and the study step."""
    w14, w15, w16 = fakes["w14"], fakes["w15"], fakes["w16"]
    worker14, _, _ = w14.world(geometries={"geom1": w14.geometry("geom1")})
    block = _apply_ok(driver, worker14, "chain_a_block")
    assert _written_value(block, "size")["data"] == [float(driver.CHAIN_A["length_m"]),
                                                     float(driver.CHAIN_A["width_m"]),
                                                     float(driver.CHAIN_A["height_m"])]
    worker15 = w15.worker_for(_fresh_material_model(w15))
    material = _apply_ok(driver, worker15, "chain_a_material")
    assert _written_value(material, "thermalconductivity")["data"] == [["10[W/(m*K)]", "0", "0"],
                                                                       ["0", "10[W/(m*K)]", "0"],
                                                                       ["0", "0", "10[W/(m*K)]"]]
    for name, tag in (("chain_a_temp1", "temp1"), ("chain_a_temp2", "temp2")):
        feature = _apply_ok(driver, worker15, name)
        assert feature["type_id"] == "TemperatureBoundary"
        # The site's identity: an index shift above it must not silently replay
        # another chain's feature (this is how the chain-B ``temp2`` index was lost).
        assert feature["tag"] == tag, f"{name} replayed the driver call for {feature['tag']!r}, not {tag!r}"
    worker16 = w16.worker_for(w16.build_model())
    mesh = _apply_ok(driver, worker16, "chain_a_mesh")
    assert _written_value(mesh, "hauto")["data"] == 4
    worker_study = w16.worker_for(_fresh_study_model(w16))
    step = _apply_ok(driver, worker_study, "chain_a_step")
    assert step["applied"][0]["readback"]["type"] == "Stationary"


def test_replay_chain_b_flow(driver, fakes):
    """W16_T019 chain B: the same chain with a transient tlist."""
    w14, w15, w16 = fakes["w14"], fakes["w15"], fakes["w16"]
    worker14, _, _ = w14.world(geometries={"geom1": w14.geometry("geom1")})
    _apply_ok(driver, worker14, "chain_b_block")
    worker15 = w15.worker_for(_fresh_material_model(w15))
    material = _apply_ok(driver, worker15, "chain_b_material")
    # C07b: the written value is the *specification's* Cp (1000), checked against the spec rather
    # than against whatever the material preset used to say.  Reporting 10 here is what made the
    # transient decay wrong while the analytic expectation said 1000.
    assert _written_value(material, "heatcapacity")["data"] == driver.BENCHMARK_B.cp_expression, (
        "chain B must write the registered heat capacity, not the material preset that used to stand in for it"
    )
    _apply_ok(driver, worker15, "chain_b_temp1")
    assert _site(driver, "chain_b_temp1")["payload"]["tag"] == "temp1", (
        "chain_b_temp1 must replay the chain-B TemperatureBoundary call, not a call that moved into its slot"
    )
    worker16 = w16.worker_for(w16.build_model())
    _apply_ok(driver, worker16, "chain_b_mesh")
    worker_study = w16.worker_for(_fresh_study_model(w16))
    step = _apply_ok(driver, worker_study, "chain_b_step")
    assert _written_value(step, "tlist")["data"] == [float(value) for value in driver.CHAIN_B["time_points_s"]]


def test_replay_chain_c_flow(driver, fakes):
    """Chain C: the continuation update targets study/feature/<step>."""
    w16 = fakes["w16"]
    worker = w16.worker_for(_fresh_study_model(w16))
    created = _dispatch("study.step_create", {
        "study": {"segments": [{"collection": "study", "tag": "std1"}]}, "tag": "time", "type_id": "Transient",
        "properties": [{"name": "tlist", "value": {"kind": "float64", "shape": [3], "data": [0.0, 1.0, 2.0]}}],
    }, worker)
    assert created["status"] == "APPLIED"
    resumed = _apply_ok(driver, worker, "chain_c_step_update")
    assert resumed["path"]["segments"][-1] == {"collection": "feature", "tag": "time"}
    assert _written_value(resumed, "tlist")["data"] == [0.0, 1.0]


def test_replay_solver_flow(driver, fakes):
    """W16_T020: the solver sub-feature update and the two refusal probes."""
    w16 = fakes["w16"]
    worker = w16.worker_for(w16.build_model())
    updated = _apply_ok(driver, worker, "t020_solver_update")
    assert _written_value(updated, "maxiter")["data"] == 50
    for name in ("t020_unknown_property", "t020_noop_probe"):
        _apply_refused(driver, worker, name)
