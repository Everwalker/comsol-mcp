"""Typed solution metadata and result-array indexing for G3.3.

COMSOL exposes two different indices for stored solutions. ``outer`` names the
parameter/sweep level and ``inner`` names the solution within that level; the
engine ``solnum`` is a third, opaque index. This module keeps those axes
separate. Result data from a numerical feature is first interpreted using the
documented ``[expression][solnum][point]`` layout and only then mapped to the
canonical ``[expression][outer][inner][point]`` representation.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from ._execution_contract import ExecutionContractError


_AXES = ("expression", "outer", "inner", "point")


def _sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _as_list(value: Any, *, what: str) -> list[Any]:
    if value is None:
        return []
    if _sequence(value):
        return list(value)
    if not isinstance(value, (str, bytes, bytearray)):
        try:
            return list(value)
        except TypeError:
            pass
    raise ExecutionContractError("SOLUTION_METADATA_ERROR", f"{what} must be an array")


def _shape(value: Any) -> tuple[int, ...]:
    if not _sequence(value):
        return ()
    value_list = list(value)
    if not value_list:
        return (0,)
    child_shapes = [_shape(child) for child in value_list]
    if any(child != child_shapes[0] for child in child_shapes[1:]):
        raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", "field array is ragged")
    return (len(value_list),) + child_shapes[0]


def _invoke(node: Any, method: str, *args: Any) -> Any:
    fn = getattr(node, method, None)
    if not callable(fn):
        raise ExecutionContractError("API_UNSUPPORTED", f"selected node does not expose {method}()")
    try:
        return fn(*args)
    except ExecutionContractError:
        raise
    except Exception as exc:
        raise ExecutionContractError(
            "SOLUTION_METADATA_ERROR",
            f"{method}() failed: {type(exc).__name__}: {str(exc)[:500]}",
        ) from exc


def _normalise_ints(value: Any, *, what: str, positive: bool = True) -> list[int]:
    values = _as_list(value, what=what)
    out: list[int] = []
    for item in values:
        if isinstance(item, bool):
            raise ExecutionContractError("SOLUTION_METADATA_ERROR", f"{what} contains boolean {item!r}")
        if not isinstance(item, int):
            # Do not turn a fractional/string value into a plausible index.
            # Java integer arrays arrive as Python ints through the worker.
            raise ExecutionContractError("SOLUTION_METADATA_ERROR", f"{what} contains non-integer {item!r}")
        number = item
        if positive and number < 1:
            raise ExecutionContractError("SOLUTION_METADATA_ERROR", f"{what} contains invalid index {number}")
        out.append(number)
    return out


def _unique_ints(values: Sequence[int], *, what: str) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for value in values:
        if value in seen:
            raise ExecutionContractError("SOLUTION_METADATA_ERROR", f"{what} contains duplicate {value}")
        seen.add(value)
        out.append(value)
    return out


class SolutionBinding:
    """Resolve dataset chains and bind typed SolutionInfo axes."""

    @staticmethod
    def resolve_dataset_chain(dset_container: Any, start_tag: str, max_depth: int = 16) -> list[str]:
        """Traverse upstream dataset references and reject reference cycles."""
        if not isinstance(start_tag, str) or not start_tag:
            raise ExecutionContractError("INVALID_REQUEST", "dataset tag must be a non-empty string")
        try:
            tags = set(str(tag) for tag in _as_list(_invoke(dset_container, "tags"), what="dataset tags"))
        except ExecutionContractError as exc:
            raise ExecutionContractError("DATASET_BINDING_INCOMPLETE", "dataset tag collection could not be read") from exc
        chain: list[str] = [start_tag]
        visited: set[str] = {start_tag}
        current = start_tag
        for _ in range(max_depth):
            try:
                node = _invoke(dset_container, "get", current)
            except ExecutionContractError as exc:
                raise ExecutionContractError("DATASET_BINDING_INCOMPLETE", f"dataset {current!r} could not be read") from exc
            upstream: str | None = None
            getter = getattr(node, "getString", None)
            if not callable(getter):
                raise ExecutionContractError("DATASET_BINDING_INCOMPLETE", f"dataset {current!r} has no readable reference properties")
            for prop in ("data", "dataset", "solution"):
                if not callable(getter):
                    break
                try:
                    value = getter(prop)
                except Exception:
                    continue
                if isinstance(value, str) and value and (not tags or value in tags):
                    upstream = value
                    break
            if not upstream:
                return chain
            if upstream in visited:
                raise ExecutionContractError(
                    "DATASET_CYCLE_DETECTED",
                    f"Dataset reference cycle detected: {' -> '.join(chain)} -> {upstream}",
                )
            visited.add(upstream)
            chain.append(upstream)
            current = upstream
        raise ExecutionContractError("DATASET_CHAIN_TOO_DEEP", f"dataset chain exceeds {max_depth} nodes")

    @staticmethod
    def _outer_labels(solution_info: Any, outer_indices: Sequence[int] | None, outer_count: int | None) -> tuple[list[int], list[dict[str, Any]]]:
        errors: list[dict[str, Any]] = []
        if outer_indices is not None:
            labels = _unique_ints(_normalise_ints(outer_indices, what="outer_indices"), what="outer_indices")
            return labels, errors
        if outer_count is not None:
            if isinstance(outer_count, bool) or not isinstance(outer_count, int) or outer_count < 1:
                raise ExecutionContractError("INVALID_REQUEST", "outer_count must be a positive integer")
            return list(range(1, outer_count + 1)), errors
        getter = getattr(solution_info, "getOuterSolnum", None)
        if not callable(getter):
            errors.append({"method": "getOuterSolnum", "code": "API_UNSUPPORTED", "message": "outer labels are unavailable"})
            return [], errors
        try:
            raw = getter()
        except Exception as exc:
            errors.append({"method": "getOuterSolnum", "code": "SOLUTION_METADATA_ERROR", "message": str(exc)[:500]})
            return [], errors
        if raw is None:
            errors.append({"method": "getOuterSolnum", "code": "SOLUTION_AXIS_METADATA_UNAVAILABLE", "message": "engine returned no outer labels"})
            return [], errors
        try:
            labels = _unique_ints(_normalise_ints(raw, what="SolutionInfo.getOuterSolnum()"), what="outer labels")
        except ExecutionContractError as exc:
            errors.append({"method": "getOuterSolnum", "code": exc.code, "message": str(exc)})
            return [], errors
        if not labels:
            # COMSOL may publish no labels through getOuterSolnum().  The
            # documented getSolnums(String[]) route can still return the real
            # (outer, inner) tuples; use it when available instead of inventing
            # outer=1.
            get_all = getattr(solution_info, "getSolnums", None)
            diagnostic_errors: list[dict[str, Any]] = []
            if callable(get_all):
                try:
                    raw_pairs = get_all([])
                    parsed: list[int] = []
                    for row in _as_list(raw_pairs, what="getSolnums([])"):
                        if not _sequence(row) or len(row) < 2:
                            raise ExecutionContractError("SOLUTION_METADATA_ERROR", "getSolnums([]) returned a non-pair")
                        outer = row[0]
                        if not isinstance(outer, int) or isinstance(outer, bool) or outer < 1:
                            raise ExecutionContractError("SOLUTION_METADATA_ERROR", "getSolnums([]) returned an invalid outer index")
                        if outer not in parsed:
                            parsed.append(outer)
                    if parsed:
                        return parsed, errors
                except Exception as exc:
                    # COMSOL 6.4 can reject getSolnums([]) for a perfectly
                    # valid single stationary/transient solution.  Keep this
                    # as a diagnostic only while the strict per-outer route
                    # is still being attempted; a successful fallback must
                    # not be reported as an incomplete binding.
                    diagnostic_errors.append({"method": "getSolnums", "code": "SOLUTION_METADATA_ERROR", "message": str(exc)[:500]})
            # For stationary/transient solutions COMSOL 6.4 may return an
            # empty getOuterSolnum() even though the documented strict query
            # for outer=1 succeeds.  This is an engine-confirmed single outer,
            # not a fabricated default: only accept it after getSolnum(1,true)
            # returns at least one real inner number.
            get_one = getattr(solution_info, "getSolnum", None)
            if callable(get_one):
                try:
                    probe = get_one(1, True)
                    if _normalise_ints(probe, what="getSolnum(1, true)"):
                        return [1], errors
                except Exception as exc:
                    errors.append({"method": "getSolnum", "outer": 1, "code": "SOLUTION_AXIS_METADATA_UNAVAILABLE", "message": str(exc)[:500]})
            errors.extend(diagnostic_errors)
            errors.append({"method": "getOuterSolnum", "code": "SOLUTION_AXIS_METADATA_UNAVAILABLE", "message": "engine returned an empty outer axis and getSolnums([]) did not expose tuples"})
        return labels, errors

    @staticmethod
    def resolve_solution_info(
        solution_info: Any,
        *,
        outer_indices: Sequence[int] | None = None,
        outer_count: int | None = None,
        strict: bool = True,
    ) -> dict[str, Any]:
        """Read real ``(outer, inner) -> solnum`` and parameter metadata.

        ``getMaxInner`` is deliberately not used: inner counts may differ by
        outer level. A caller may supply outer labels when COMSOL's
        ``getOuterSolnum()`` is empty; without labels this reports an
        unavailable mapping instead of inventing one.
        """
        if solution_info is None:
            raise ExecutionContractError("SOLUTION_METADATA_ERROR", "SolutionInfo is required")
        labels, errors = SolutionBinding._outer_labels(solution_info, outer_indices, outer_count)
        pairs: list[dict[str, int]] = []
        inner_by_outer: dict[int, list[int]] = {}
        solnum_by_pair: dict[tuple[int, int], int] = {}
        get_solnum = getattr(solution_info, "getSolnum", None)
        mapping_complete = bool(labels and callable(get_solnum))
        if not callable(get_solnum):
            errors.append({"method": "getSolnum", "code": "API_UNSUPPORTED", "message": "per-outer solution mapping is unavailable"})
        if labels and callable(get_solnum):
            for outer in labels:
                try:
                    try:
                        raw_solnums = get_solnum(outer, bool(strict))
                    except TypeError:
                        raise ExecutionContractError("API_UNSUPPORTED", "getSolnum(outer, strict) is required")
                    solnums = _normalise_ints(raw_solnums, what=f"getSolnum({outer}, strict)")
                    if not solnums:
                        raise ExecutionContractError("SOLUTION_AXIS_METADATA_UNAVAILABLE", f"outer {outer} has no inner solutions")
                    # getSolnum returns this outer level's actual one-based
                    # inner solution numbers.  They may be non-contiguous and
                    # must not be replaced by enumerate(range(...)).
                    inner_values = list(solnums)
                    inner_by_outer[outer] = inner_values
                    for inner, solnum in zip(inner_values, solnums):
                        row = {"outer": outer, "inner": inner, "solnum": solnum}
                        pairs.append(row)
                        solnum_by_pair[(outer, inner)] = solnum
                except ExecutionContractError as exc:
                    mapping_complete = False
                    errors.append({"method": "getSolnum", "outer": outer, "code": exc.code, "message": str(exc)})
                except Exception as exc:
                    mapping_complete = False
                    errors.append({"method": "getSolnum", "outer": outer, "code": "SOLUTION_METADATA_ERROR", "message": str(exc)[:500]})
        else:
            mapping_complete = False

        pair_arg = [[row["outer"], row["inner"]] for row in pairs]
        names_by_pair: dict[tuple[int, int], list[str]] = {}
        values_by_pair: dict[tuple[int, int], list[Any]] = {}
        units_by_pair: dict[tuple[int, int], list[str]] = {}

        def _metadata(method: str, converter: Any, target: dict[tuple[int, int], list[Any]]) -> None:
            if not pair_arg:
                return
            fn = getattr(solution_info, method, None)
            if not callable(fn):
                errors.append({"method": method, "code": "API_UNSUPPORTED", "message": f"{method}(int[][]) is unavailable"})
                return
            try:
                result = fn(pair_arg)
                rows = list(result) if _sequence(result) else [result]
                if len(rows) != len(pair_arg):
                    if len(pair_arg) == 1:
                        rows = [result]
                    else:
                        raise ExecutionContractError("SOLUTION_METADATA_ERROR", f"{method} returned {len(rows)} rows for {len(pair_arg)} pairs")
                for pair, row in zip(pair_arg, rows):
                    key = (int(pair[0]), int(pair[1]))
                    target[key] = [converter(item) for item in row] if _sequence(row) else [converter(row)]
            except ExecutionContractError as exc:
                errors.append({"method": method, "code": exc.code, "message": str(exc)})
            except Exception as exc:
                errors.append({"method": method, "code": "SOLUTION_METADATA_ERROR", "message": str(exc)[:500]})

        _metadata("getPNames", str, names_by_pair)
        _metadata("getPvals", lambda value: value, values_by_pair)
        # Java null means a dimensionless parameter.  Converting it with
        # str() would publish the misleading literal unit "None".
        _metadata("getUnits", lambda value: None if value is None or value == "" else str(value), units_by_pair)

        level_names: list[str] = []
        get_levels = getattr(solution_info, "getLevelNames", None)
        if callable(get_levels):
            try:
                level_names = [str(item) for item in _as_list(get_levels(), what="getLevelNames()")]
            except Exception as exc:
                errors.append({"method": "getLevelNames", "code": "SOLUTION_METADATA_ERROR", "message": str(exc)[:500]})

        parameter_names: list[str] = []
        for row in names_by_pair.values():
            for name in row:
                if name not in parameter_names:
                    parameter_names.append(name)

        inner_indices: list[int] = []
        if inner_by_outer:
            first = next(iter(inner_by_outer.values()))
            inner_indices = list(first) if all(indices == first for indices in inner_by_outer.values()) else sorted({item for indices in inner_by_outer.values() for item in indices})
        axis_complete = bool(labels and inner_by_outer and all(inner_by_outer.get(o) for o in labels))
        return {
            "outer_indices": labels,
            "inner_indices": inner_indices,
            "inner_indices_by_outer": inner_by_outer,
            "solnum_pairs": pairs,
            "solnum_by_pair": solnum_by_pair,
            "parameter_names": parameter_names,
            "parameter_names_by_pair": names_by_pair,
            "parameter_values_by_pair": values_by_pair,
            "parameter_units_by_pair": units_by_pair,
            "level_names": level_names,
            "axis_metadata_complete": axis_complete,
            "pair_mapping_complete": bool(mapping_complete and len(pairs) == sum(len(v) for v in inner_by_outer.values())),
            "read_errors": errors,
            "binding_source": "SolutionInfo.getSolnum(outer, strict)",
        }

    read_solution_info = resolve_solution_info
    resolve_solution_axes = resolve_solution_info

    @staticmethod
    def _selection_indices(spec: Any, *, size: int, axis: str, coords: Sequence[Any] | None = None) -> list[int]:
        if spec is None or spec == "all":
            return list(range(size))
        if isinstance(spec, str):
            if spec == "first":
                if not size:
                    raise ExecutionContractError("INVALID_REQUEST", f"{axis} axis is empty")
                return [0]
            if spec == "last":
                if not size:
                    raise ExecutionContractError("INVALID_REQUEST", f"{axis} axis is empty")
                return [size - 1]
            raise ExecutionContractError("INVALID_REQUEST", f"unsupported {axis} selection {spec!r}")
        raw = list(spec) if _sequence(spec) else [spec]
        if not raw:
            raise ExecutionContractError("INVALID_REQUEST", f"{axis} selection cannot be empty")
        out: list[int] = []
        for item in raw:
            if isinstance(item, bool) or not isinstance(item, int):
                raise ExecutionContractError("INVALID_REQUEST", f"{axis} selection must contain integer indices")
            if coords is not None and item in coords:
                index = list(coords).index(item)
            elif 1 <= item <= size:
                index = item - 1
            else:
                raise ExecutionContractError("INVALID_REQUEST", f"{axis} index {item} out of range")
            if index in out:
                raise ExecutionContractError("INVALID_REQUEST", f"{axis} selection contains duplicate index {item}")
            out.append(index)
        return out

    @staticmethod
    def field_array_from_engine(
        raw_data: Any,
        binding: Mapping[str, Any],
        *,
        num_expressions: int,
        layout: str = "expression,solnum,point",
        coords: Mapping[str, Sequence[Any]] | None = None,
        units: Mapping[str, Any] | Sequence[Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        is_complex: bool | None = False,
        selected_outer: int | None = None,
    ) -> "FieldArray":
        """Map a documented engine layout into a canonical FieldArray."""
        if layout not in {"expression,outer,inner,point", "expression,outer,inner", "expression,solnum,point", "expression,solnum"}:
            raise ExecutionContractError("API_UNSUPPORTED", f"unsupported numerical data layout {layout!r}")
        pairs = list(binding.get("solnum_pairs") or [])
        outer_labels = list(binding.get("outer_indices") or [])
        inner_by_outer = {int(k): list(v) for k, v in (binding.get("inner_indices_by_outer") or {}).items()}
        if not pairs or not outer_labels or not inner_by_outer:
            raise ExecutionContractError("SOLUTION_AXIS_METADATA_UNAVAILABLE", "outer/inner metadata is incomplete")
        counts = {len(inner_by_outer[o]) for o in outer_labels}
        if len(counts) != 1:
            raise ExecutionContractError("SOLUTION_AXIS_ERROR", "ragged inner axes cannot be represented by this FieldArray")
        inner_count = next(iter(counts))
        if inner_count < 1:
            raise ExecutionContractError("SOLUTION_AXIS_METADATA_UNAVAILABLE", "inner axis is empty")
        raw = list(raw_data) if _sequence(raw_data) else [raw_data]
        if len(raw) != num_expressions:
            raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", f"expected {num_expressions} expression rows, got {len(raw)}")

        # A numerical feature's [expr][solnum][point] data is scoped to the
        # selected outer level.  It is not a global solnum axis: getSolnum()
        # returns inner numbers, which commonly repeat for every outer.  Only
        # an explicit all-outer layout may be mapped in one call.
        if layout.startswith("expression,solnum"):
            if selected_outer is None:
                if len(outer_labels) != 1:
                    raise ExecutionContractError(
                        "SOLUTION_AXIS_ERROR",
                        "expression,solnum data is scoped to one outer; selected_outer is required for multiple outers",
                    )
                selected_outer = int(outer_labels[0])
            if selected_outer not in inner_by_outer:
                raise ExecutionContractError("INVALID_REQUEST", f"selected outer {selected_outer} is not in SolutionInfo metadata")
            expected_inner = list(inner_by_outer[selected_outer])
            outer_labels_for_data = [selected_outer]
        else:
            outer_labels_for_data = outer_labels
            expected_inner = []

        canonical_outer_labels = list(outer_labels_for_data)
        data: list[list[list[list[Any]]]] = []
        point_count: int | None = None
        for expr_index, expr_row in enumerate(raw):
            outer_rows = list(expr_row) if _sequence(expr_row) else [expr_row]
            if layout.startswith("expression,outer"):
                if len(outer_rows) != len(outer_labels):
                    raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", f"expression {expr_index} has {len(outer_rows)} outer rows, expected {len(outer_labels)}")
            else:
                if len(outer_rows) != len(expected_inner):
                    raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", f"expression {expr_index} has {len(outer_rows)} inner rows, expected {len(expected_inner)}")
            expr_data: list[list[list[Any]]] = []
            for outer in canonical_outer_labels:
                outer_position = outer_labels.index(outer)
                outer_data: list[list[Any]] = []
                inner_values = list(inner_by_outer[outer])
                inner_rows = outer_rows[outer_position] if layout.startswith("expression,outer") else outer_rows
                inner_rows = list(inner_rows) if _sequence(inner_rows) else [inner_rows]
                if len(inner_rows) != len(inner_values):
                    raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", f"outer {outer} has {len(inner_rows)} rows, expected {len(inner_values)}")
                for inner_position, inner in enumerate(inner_values):
                    value = inner_rows[inner_position]
                    point_values = list(value) if layout.endswith("point") and _sequence(value) else [value]
                    if point_count is None:
                        point_count = len(point_values)
                    elif len(point_values) != point_count:
                        raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", "point axis length differs between solution rows")
                    outer_data.append(point_values)
                expr_data.append(outer_data)
            data.append(expr_data)
        if point_count is None:
            point_count = 0
        axis_coords = dict(coords or {})
        axis_coords.setdefault("outer", canonical_outer_labels)
        axis_coords.setdefault("inner", list(inner_by_outer[canonical_outer_labels[0]]))
        axis_coords.setdefault("point", list(range(1, point_count + 1)))
        return FieldArray(data, axes=_AXES, coords=axis_coords, units=units, metadata=metadata, is_complex=is_complex)

    @staticmethod
    def slice_solution_axis(data: Any, inner_spec: int | Sequence[int] | str | None, *, num_expressions: int = 1) -> Any:
        """Backward-compatible slice of a legacy ``[expr][solnum][point]`` array."""
        if inner_spec is None or inner_spec == "all":
            return data
        if not isinstance(data, list):
            raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", "solution data must be a list")

        def _slice_one_expr(expr_sol_list: Any) -> Any:
            if not isinstance(expr_sol_list, list) or not expr_sol_list:
                return expr_sol_list
            sol_count = len(expr_sol_list)
            if isinstance(inner_spec, int):
                choices, scalar = [inner_spec], True
            elif isinstance(inner_spec, (list, tuple)):
                choices, scalar = list(inner_spec), False
            elif inner_spec == "first":
                choices, scalar = [1], True
            elif inner_spec == "last":
                choices, scalar = [sol_count], True
            else:
                raise ExecutionContractError("INVALID_REQUEST", f"unsupported inner spec: {inner_spec!r}")
            for index in choices:
                if isinstance(index, bool) or not isinstance(index, int) or index < 1 or index > sol_count:
                    raise ExecutionContractError("INVALID_REQUEST", f"inner index {index!r} out of range [1, {sol_count}]")
            selected = [expr_sol_list[index - 1] for index in choices]
            return selected[0] if scalar else selected

        if num_expressions > 1:
            if len(data) != num_expressions:
                raise ExecutionContractError("INVALID_REQUEST", f"the value array carries {len(data)} expression rows but {num_expressions} expressions were declared")
            return [_slice_one_expr(row) for row in data]
        if num_expressions == 1:
            if len(data) == 1 and isinstance(data[0], list) and data[0] and isinstance(data[0][0], list):
                return [_slice_one_expr(data[0])]
            return _slice_one_expr(data)
        return [_slice_one_expr(row) for row in data]


class FieldArray:
    """Rectangular canonical ``[expression, outer, inner, point]`` data."""

    def __init__(
        self,
        values: Any,
        *,
        axes: Sequence[str] = _AXES,
        shape: tuple[int, ...] | None = None,
        units: Mapping[str, Any] | Sequence[Any] | None = None,
        coords: Mapping[str, Sequence[Any]] | None = None,
        metadata: Mapping[str, Any] | None = None,
        is_complex: bool | None = False,
    ) -> None:
        self.values = values
        self.data = values
        self.axes = list(axes)
        if len(set(self.axes)) != len(self.axes):
            raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", "field axes must be unique")
        actual_shape = _shape(values)
        if shape is not None and tuple(shape) != actual_shape:
            raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", f"declared shape {tuple(shape)} != actual shape {actual_shape}")
        if len(actual_shape) != len(self.axes):
            raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", f"{len(self.axes)} axes declared for rank {len(actual_shape)} data")
        self.shape = actual_shape
        self.coords = {str(key): list(value) for key, value in (coords or {}).items()}
        for axis, size in zip(self.axes, self.shape):
            if axis in self.coords and len(self.coords[axis]) != size:
                raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", f"coordinate count for {axis} does not match axis length")
        if isinstance(units, Mapping):
            self.units = dict(units)
        elif units is None:
            self.units = {}
        else:
            if len(units) != len(self.axes):
                raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", "units must have one entry per axis")
            self.units = dict(zip(self.axes, units))
        self.metadata = deepcopy(dict(metadata or {}))
        self.is_complex = is_complex

    def _indices_for(self, axis: str, spec: Any) -> list[int]:
        if axis not in self.axes:
            raise ExecutionContractError("INVALID_REQUEST", f"field array has no {axis!r} axis")
        pos = self.axes.index(axis)
        return SolutionBinding._selection_indices(spec, size=self.shape[pos], axis=axis, coords=self.coords.get(axis))

    @staticmethod
    def _take(value: Any, axis: int, indices: Sequence[int]) -> Any:
        if axis == 0:
            row = list(value) if _sequence(value) else []
            return [deepcopy(row[index]) for index in indices]
        return [FieldArray._take(child, axis - 1, indices) for child in value]

    def select(self, **selectors: Any) -> "FieldArray":
        unknown = set(selectors) - set(self.axes)
        if unknown:
            raise ExecutionContractError("INVALID_REQUEST", f"unknown field axes: {', '.join(sorted(unknown))}")
        result = deepcopy(self.values)
        new_coords = deepcopy(self.coords)
        for axis in self.axes:
            if axis not in selectors:
                continue
            indices = self._indices_for(axis, selectors[axis])
            position = self.axes.index(axis)
            result = self._take(result, position, indices)
            if axis in new_coords:
                new_coords[axis] = [new_coords[axis][index] for index in indices]
        return FieldArray(result, axes=self.axes, coords=new_coords, units=self.units, metadata=self.metadata, is_complex=self.is_complex)

    def to_dict(self) -> dict[str, Any]:
        return {
            "values": self.values,
            "data": self.values,
            "axes": list(self.axes),
            "shape": list(self.shape),
            "coords": deepcopy(self.coords),
            "units": deepcopy(self.units),
            "metadata": deepcopy(self.metadata),
            "is_complex": self.is_complex,
        }
