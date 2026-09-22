"""Read-only, fail-closed resolution of COMSOL result-dataset bindings.

The resolver only reads native tags, types and properties.  It never calls a
mutation or result-generation method.  Every return value is JSON-shaped and a
caller must use ``binding_complete is True`` as the success predicate.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_DERIVED_TYPES = frozenset({
    "CutPoint1D", "CutPoint2D", "CutPoint3D",
    "CutLine1D", "CutLine2D", "CutLine3D", "CutPlane", "Join",
})


def _err(code: str, message: str, path: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"code": str(code), "message": str(message)[:500]}
    if path:
        row["path"] = path
    return row


def _tag(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _call(node: Any, method: str, *args: Any) -> tuple[bool, Any, dict[str, Any] | None]:
    try:
        fn = getattr(node, method)
    except Exception as exc:  # noqa: BLE001 - read boundary
        return False, None, _err("API_UNSUPPORTED", f"{method} accessor is unavailable: {type(exc).__name__}: {exc}")
    if not callable(fn):
        return False, None, _err("API_UNSUPPORTED", f"{method} accessor is not callable")
    try:
        return True, fn(*args), None
    except Exception as exc:  # noqa: BLE001 - retain the native read failure
        code = getattr(exc, "code", None)
        if not isinstance(code, str) or not code:
            code = "ENGINE_READ_FAILED"
        return False, None, _err(code, f"{method} read failed: {type(exc).__name__}: {exc}")


def _add(errors: list[dict[str, Any]], value: Mapping[str, Any] | None,
         path: str | None = None) -> None:
    if not isinstance(value, Mapping):
        return
    row = dict(value)
    if path and "path" not in row:
        row["path"] = path
    if row not in errors:
        errors.append(row)


def _tags(node: Any, path: str, errors: list[dict[str, Any]]) -> list[str] | None:
    ok, raw, detail = _call(node, "tags")
    if not ok:
        _add(errors, detail, path)
        return None
    if not isinstance(raw, (list, tuple)) or not all(isinstance(item, str) for item in raw):
        _add(errors, _err("ENGINE_READ_FAILED", "tags() did not return an array of strings", path))
        return None
    return [str(item) for item in raw]


def _properties(node: Any) -> set[str] | None:
    ok, raw, _detail = _call(node, "properties")
    if not ok:
        # The targeted getter remains authoritative for wrappers without
        # properties(); a failed targeted getter is recorded by _string.
        return None
    if not isinstance(raw, (list, tuple)) or not all(isinstance(item, str) for item in raw):
        return set()
    return {str(item) for item in raw}


def _string(node: Any, name: str, path: str, errors: list[dict[str, Any]], *, required: bool) -> str | None:
    names = _properties(node)
    if names is not None and name not in names:
        if required:
            _add(errors, _err("PROPERTY_MISSING", f"required property {name!r} is not published", f"{path}.{name}"))
        return None
    ok, raw, detail = _call(node, "getString", name)
    if not ok:
        _add(errors, detail, f"{path}.{name}")
        return None
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if required:
            _add(errors, _err("PROPERTY_MISSING", f"required property {name!r} is empty", f"{path}.{name}"))
        return None
    if not isinstance(raw, str):
        _add(errors, _err("PROPERTY_TYPE_MISMATCH", f"property {name!r} did not return a string", f"{path}.{name}"))
        return None
    return raw.strip()


def _type(node: Any, path: str, errors: list[dict[str, Any]]) -> str | None:
    ok, raw, detail = _call(node, "getType")
    if not ok:
        _add(errors, detail, f"{path}.type")
        return None
    value = _tag(raw)
    if value is None:
        _add(errors, _err("PROPERTY_MISSING", "getType() returned an empty value", f"{path}.type"))
    return value


def _base(tag: str, dtype: str | None = None) -> dict[str, Any]:
    return {
        "dataset": tag, "dataset_type": dtype, "solution": None,
        "component": None, "geometry": None, "dataset_chain": [],
        "dataset_edges": [], "binding_complete": False, "sources": {},
        "properties": {}, "read_errors": [], "error": None,
    }


def resolve_dataset_binding(model: Any, dataset_tag: str,
                            requested_solution: str | None = None) -> dict[str, Any]:
    """Resolve ``dataset_tag`` to a real solution/component/geometry binding.

    Missing nodes, wrong tags, cycles, incompatible Join branches and every
    native read failure are returned as an explicit non-complete result.  The
    function is read-only and does not manufacture defaults except for the
    documented unique single-component/single-geometry derivation.
    """
    if not isinstance(dataset_tag, str) or not dataset_tag.strip():
        result = _base(str(dataset_tag))
        result["error"] = _err("INVALID_REQUEST", "dataset_tag must be a non-empty string")
        result["read_errors"] = [result["error"]]
        return result
    dataset_tag = dataset_tag.strip()
    if requested_solution is not None and (not isinstance(requested_solution, str) or not requested_solution.strip()):
        result = _base(dataset_tag)
        result["error"] = _err("INVALID_REQUEST", "requested_solution must be a non-empty string when supplied")
        result["read_errors"] = [result["error"]]
        return result
    requested_solution = requested_solution.strip() if isinstance(requested_solution, str) else None

    root_errors: list[dict[str, Any]] = []
    ok, results, detail = _call(model, "result")
    if not ok:
        result = _base(dataset_tag)
        _add(result["read_errors"], detail, "model.result")
        result["error"] = result["read_errors"][0]
        return result
    ok, datasets, detail = _call(results, "dataset")
    if not ok:
        result = _base(dataset_tag)
        _add(result["read_errors"], detail, "model.result.dataset")
        result["error"] = result["read_errors"][0]
        return result
    dataset_tags = _tags(datasets, "model.result.dataset.tags", root_errors)
    if dataset_tags is None:
        result = _base(dataset_tag)
        result["read_errors"] = root_errors
        result["error"] = root_errors[0] if root_errors else _err("ENGINE_READ_FAILED", "dataset tags unavailable")
        return result
    if dataset_tag not in dataset_tags:
        result = _base(dataset_tag)
        result["available_dataset_tags"] = list(dataset_tags)
        result["error"] = _err("DATASET_NOT_FOUND", f"dataset {dataset_tag!r} is not in result.dataset.tags()")
        result["read_errors"] = [result["error"]]
        return result

    ok, sol_container, detail = _call(model, "sol")
    if not ok:
        _add(root_errors, detail, "model.sol")
        sol_tags = None
    else:
        sol_tags = _tags(sol_container, "model.sol.tags", root_errors)

    # Component tags are read lazily.  A binding with explicit comp/geom does
    # not need the model-node uniqueness fallback; a failed optional read is
    # therefore not allowed to poison a proven explicit binding.
    model_component_tags: list[str] | None = None
    cache: dict[str, dict[str, Any]] = {}
    active: list[str] = []

    def component_node(comp: str, errors: list[dict[str, Any]]) -> Any | None:
        nonlocal model_component_tags
        if model_component_tags is None:
            ok_node, model_node, d = _call(model, "modelNode")
            if ok_node:
                model_component_tags = _tags(model_node, "model.modelNode.tags", errors)
            else:
                _add(errors, d, "model.modelNode")
        if model_component_tags is not None and comp not in model_component_tags:
            _add(errors, _err("COMPONENT_NOT_FOUND", f"component {comp!r} is not in modelNode.tags()", f"component:{comp}"))
            return None
        ok_node, node, d = _call(model, "component", comp)
        if not ok_node or node is None:
            _add(errors, d or _err("COMPONENT_NOT_FOUND", f"model.component({comp!r}) returned null"), f"component:{comp}")
            return None
        return node

    def geometry_tags(comp_node: Any, comp: str, errors: list[dict[str, Any]]) -> tuple[Any | None, list[str] | None, dict[str, Any] | None]:
        ok_geom, geoms, d = _call(comp_node, "geom")
        if not ok_geom:
            return None, None, d
        return geoms, _tags(geoms, f"component:{comp}.geom.tags", errors), None

    def geometry_node(comp_node: Any, comp: str, geom: str, errors: list[dict[str, Any]]) -> Any | None:
        geoms, tags, first_error = geometry_tags(comp_node, comp, errors)
        if tags is not None:
            if geom not in tags:
                _add(errors, _err("GEOMETRY_NOT_FOUND", f"geometry {geom!r} is not in component {comp!r}.geom.tags()",
                                  f"component:{comp}.geom:{geom}"))
                return None
            ok_node, node, d = _call(geoms, "get", geom)
            if not ok_node or node is None:
                _add(errors, d or _err("GEOMETRY_NOT_FOUND", f"geometry {geom!r} getter returned null"),
                     f"component:{comp}.geom:{geom}")
                return None
            return node
        # Some wrappers expose geom(tag) but no geom().tags().  A successful
        # getter is a native membership proof; retain the failed no-arg read
        # only if this fallback also fails.
        ok_node, node, d = _call(comp_node, "geom", geom)
        if ok_node and node is not None:
            return node
        _add(errors, d or first_error or _err("GEOMETRY_NOT_FOUND", f"geometry {geom!r} is unavailable"),
             f"component:{comp}.geom:{geom}")
        return None

    def unique_binding(errors: list[dict[str, Any]]) -> tuple[str | None, str | None]:
        nonlocal model_component_tags
        if model_component_tags is None:
            ok_node, model_node, d = _call(model, "modelNode")
            if not ok_node:
                _add(errors, d, "model.modelNode")
                return None, None
            model_component_tags = _tags(model_node, "model.modelNode.tags", errors)
        if model_component_tags is None or len(model_component_tags) != 1:
            return None, None
        comp = model_component_tags[0]
        comp_node = component_node(comp, errors)
        if comp_node is None:
            return None, None
        _geoms, tags, d = geometry_tags(comp_node, comp, errors)
        if tags is None or len(tags) != 1:
            if d:
                _add(errors, d, f"component:{comp}.geom")
            return None, None
        return comp, tags[0]

    def resolve(tag: str) -> dict[str, Any]:
        if tag in cache:
            return cache[tag]
        result = _base(tag)
        if tag in active:
            cycle = active[active.index(tag):] + [tag]
            result["dataset_chain"] = list(dict.fromkeys(cycle))
            result["error"] = _err("DATASET_CYCLE_DETECTED", f"dataset reference cycle detected: {' -> '.join(cycle)}", f"dataset:{tag}")
            result["read_errors"] = [result["error"]]
            return result
        if tag not in dataset_tags:
            result["error"] = _err("DATASET_REFERENCE_MISSING", f"dataset reference {tag!r} is not in result.dataset.tags()", f"dataset:{tag}")
            result["read_errors"] = [result["error"]]
            return result
        active.append(tag)
        ok_node, node, d = _call(datasets, "get", tag)
        if not ok_node or node is None:
            _add(result["read_errors"], d or _err("DATASET_NOT_FOUND", f"dataset {tag!r} getter returned null"), f"dataset:{tag}")
            result["error"] = result["read_errors"][0]
            active.pop()
            cache[tag] = result
            return result
        dtype = _type(node, f"dataset:{tag}", result["read_errors"])
        result["dataset_type"] = dtype
        if dtype is None:
            result["error"] = result["read_errors"][0]
            active.pop()
            cache[tag] = result
            return result

        props: dict[str, str] = {}
        required_data = dtype in _DERIVED_TYPES and dtype != "Solution"
        required_data2 = dtype == "Join"
        for name in ("solution", "data", "data2", "comp", "geom"):
            value = _string(node, name, f"dataset:{tag}", result["read_errors"],
                            required=(dtype == "Solution" and name == "solution" and requested_solution is None)
                            or (name == "data" and required_data)
                            or (name == "data2" and required_data2))
            if value is not None:
                props[name] = value
        result["properties"] = dict(props)

        children: list[dict[str, Any]] = []
        refs: list[tuple[str, str]] = []
        if dtype == "Solution":
            solution = props.get("solution")
            if solution is None:
                _add(result["read_errors"], _err("SOLUTION_NOT_FOUND", f"Solution dataset {tag!r} has no readable solution property", f"dataset:{tag}.solution"))
            elif sol_tags is None or solution not in sol_tags:
                _add(result["read_errors"], _err("SOLUTION_NOT_FOUND", f"solution {solution!r} is not in model.sol().tags()", f"dataset:{tag}.solution"))
            else:
                result["solution"] = solution
                result["sources"]["solution"] = "dataset.solution -> model.sol().tags()"
        else:
            if required_data:
                if "data" in props:
                    refs.append(("data", props["data"]))
                else:
                    _add(result["read_errors"], _err("DATASET_REFERENCE_MISSING", f"dataset {tag!r} has no readable data reference", f"dataset:{tag}.data"))
            if dtype == "Join":
                if "data2" in props:
                    refs.append(("data2", props["data2"]))
                else:
                    _add(result["read_errors"], _err("DATASET_REFERENCE_MISSING", f"Join dataset {tag!r} has no readable data2 reference", f"dataset:{tag}.data2"))
            for property_name, ref in refs:
                edge = {"property": property_name, "from": tag, "to": ref}
                result["dataset_edges"].append(edge)
                children.append(resolve(ref))
            if children and all(child.get("binding_complete") is True for child in children):
                solutions = {child.get("solution") for child in children}
                components = {child.get("component") for child in children}
                geometries = {child.get("geometry") for child in children}
                if len(solutions) != 1 or None in solutions:
                    _add(result["read_errors"], _err("INCOMPATIBLE_DATASET_BINDING", f"dataset {tag!r} references incompatible solutions", f"dataset:{tag}"))
                elif len(components) != 1 or None in components:
                    _add(result["read_errors"], _err("INCOMPATIBLE_DATASET_BINDING", f"dataset {tag!r} references incompatible components", f"dataset:{tag}"))
                elif len(geometries) != 1 or None in geometries:
                    _add(result["read_errors"], _err("INCOMPATIBLE_DATASET_BINDING", f"dataset {tag!r} references incompatible geometries", f"dataset:{tag}"))
                else:
                    result["solution"] = next(iter(solutions))
                    result["component"] = next(iter(components))
                    result["geometry"] = next(iter(geometries))
                    result["sources"].update({field: f"upstream dataset reference(s): {[child['dataset'] for child in children]}"
                                               for field in ("solution", "component", "geometry")})

        explicit_comp, explicit_geom = props.get("comp"), props.get("geom")
        if explicit_comp is not None:
            comp_node = component_node(explicit_comp, result["read_errors"])
            if comp_node is not None:
                if result["component"] is not None and result["component"] != explicit_comp:
                    _add(result["read_errors"], _err("INCOMPATIBLE_DATASET_BINDING", f"dataset {tag!r} component conflicts with upstream data", f"dataset:{tag}.comp"))
                result["component"] = explicit_comp
                result["sources"]["component"] = "dataset.comp -> modelNode.tags()/model.component()"
                if explicit_geom is not None and geometry_node(comp_node, explicit_comp, explicit_geom, result["read_errors"]) is not None:
                    if result["geometry"] is not None and result["geometry"] != explicit_geom:
                        _add(result["read_errors"], _err("INCOMPATIBLE_DATASET_BINDING", f"dataset {tag!r} geometry conflicts with upstream data", f"dataset:{tag}.geom"))
                    result["geometry"] = explicit_geom
                    result["sources"]["geometry"] = "dataset.geom -> component.geom.tags()/getter"
        elif explicit_geom is not None:
            # An explicit geometry can use a child component or a uniquely
            # identified model component, but never an arbitrary default.
            comp = result.get("component")
            if comp is None:
                comp, _unique_geom = unique_binding(result["read_errors"])
            comp_node = component_node(comp, result["read_errors"]) if comp else None
            if comp_node is not None and geometry_node(comp_node, comp, explicit_geom, result["read_errors"]) is not None:
                result["component"] = comp
                result["geometry"] = explicit_geom
                result["sources"]["component"] = result["sources"].get("component", "component owning dataset.geom")
                result["sources"]["geometry"] = "dataset.geom -> component.geom.tags()/getter"
        elif result.get("component") is not None and result.get("geometry") is None:
            comp = result["component"]
            comp_node = component_node(comp, result["read_errors"])
            if comp_node is not None:
                _geoms, geom_tags, d = geometry_tags(comp_node, comp, result["read_errors"])
                if geom_tags is not None and len(geom_tags) == 1:
                    result["geometry"] = geom_tags[0]
                    result["sources"]["geometry"] = "unique component geometry"
                elif geom_tags is not None:
                    _add(result["read_errors"], _err("AMBIGUOUS_DATASET_BINDING", f"component {comp!r} has multiple geometries and dataset.geom is absent", f"dataset:{tag}.geom"))
                elif d:
                    _add(result["read_errors"], d, f"component:{comp}.geom")
        elif result.get("component") is None and result.get("geometry") is None:
            comp, geom = unique_binding(result["read_errors"])
            if comp is not None and geom is not None:
                result["component"], result["geometry"] = comp, geom
                result["sources"]["component"] = "unique modelNode.tags() component"
                result["sources"]["geometry"] = "unique component.geom().tags() geometry"

        chain: list[str] = []
        for child in children:
            for item in child.get("dataset_chain", []):
                if item not in chain:
                    chain.append(item)
            for edge in child.get("dataset_edges", []):
                if edge not in result["dataset_edges"]:
                    result["dataset_edges"].append(edge)
            for child_error in child.get("read_errors", []):
                _add(result["read_errors"], child_error)
        if tag not in chain:
            chain.append(tag)
        result["dataset_chain"] = chain
        result["binding_complete"] = bool(
            result.get("solution") and result.get("component") and result.get("geometry")
            and not result["read_errors"]
            and all(child.get("binding_complete") is True for child in children)
        )
        if not result["binding_complete"]:
            result["error"] = result["read_errors"][0] if result["read_errors"] else _err(
                "DATASET_BINDING_INCOMPLETE", f"dataset {tag!r} did not resolve to a complete native binding", f"dataset:{tag}")
        active.pop()
        cache[tag] = result
        return result

    resolved = resolve(dataset_tag)
    if (
        requested_solution is not None
        and resolved.get("solution") is not None
        and resolved.get("solution") != requested_solution
    ):
        mismatch = _err("SOLUTION_MISMATCH", f"requested solution {requested_solution!r} does not match resolved solution {resolved.get('solution')!r}", f"dataset:{dataset_tag}.solution")
        _add(resolved["read_errors"], mismatch)
        resolved["error"] = mismatch
        resolved["binding_complete"] = False
    resolved["requested_solution"] = requested_solution
    resolved["provenance"] = {
        "dataset_tags": list(dataset_tags),
        "solution_tags": list(sol_tags) if sol_tags is not None else None,
        "read_only": True,
        "mutation_methods_called": [],
    }
    for item in root_errors:
        _add(resolved["read_errors"], item)
    if resolved["read_errors"]:
        resolved["binding_complete"] = False
        resolved["error"] = resolved["error"] or resolved["read_errors"][0]
    return resolved


__all__ = ["resolve_dataset_binding"]
