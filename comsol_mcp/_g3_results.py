"""``result.sample_path``: the minimal result-sampling adapter (W16 acceptance
infrastructure).

Why this module exists
----------------------
The W16 acceptance chains of ``docs/comsol_mcp_design_v1/NEXT_GOAL_MAC_G3.md``
(§8, T018/T019 chains A and B) drive a public MCP action named
``result.sample_path``: ``tools/phase4_run_mcp.py`` samples the temperature
along a line through the solved block and compares it with a pre-registered
analytic reference.  The action sits in the ``result`` domain behind the G4
implementation gate.  §8 authorises exactly this much and no more:

    "使用现有求值/导出能力提供这些测试所需的最小结果。若需要一个最小采样或结果
     绑定适配器，记录为 W16 验收基础设施；不顺势展开整个 W17 结果系统或 W18
     绘图平台。"

This module is therefore *not* the W17 results system: it is one line-sampling
adapter built on one documented ephemeral COMSOL feature, and it refuses
everything it did not verify offline.  It records the operations it wanted but
could not use in ``ALLOWLIST_ADDITIONS`` / ``UNVERIFIED_PATHS`` and in the
returned data.

API provenance (COMSOL 6.4.0.293, installed under ``/Applications/COMSOL64``)
-----------------------------------------------------------------------------
Every engine call below is verified against one of two local sources:

* ``javap -cp apiplugins/com.comsol.api_1.0.0.jar <class>`` of the installed
  public API jar:
  ``Model.result() -> Results``; ``Results.numerical() -> NumericalFeatureList``
  (``create(String,String)``), ``Results.dataset() -> DatasetFeatureList``,
  ``Results.tags()/remove(String)``; ``NumericalFeature.getData() ->
  double[][][]``, ``getReal() -> double[][]``, ``isComplex()``,
  ``setInterpolationCoordinates(double[][])``; ``PropFeature.set(String,String)``,
  ``set(String,double[][])``, ``setIndex(String,String,int)``,
  ``getString/getStringArray/getDoubleArray/getType``; ``Model.sol(String) ->
  SolverSequence`` with ``getPVals() -> double[]`` and ``study() -> String``;
  ``Study.feature()``; ``StudyFeature.type()``/``PropFeature.getType()``.
* The local COMSOL 6.4 documentation corpus
  (``/Users/everwalker/Documents/KnowledgeBases/COMSOL-6.4-KB/kb.py``):
  * results API page **Interp** (``comsol_api_results.52.082.html``, sha256
    ``156412ab29631518953b57d6948b88a0181bf5312984b1a0f9e840a6ad848421``):
    ``model.result().numerical().create(<ftag>,"Interp")`` +
    ``set(property,<value>)`` + ``getData()`` + ``getCoordinates()`` +
    ``run()`` + ``isComplex()``; ``getData()`` "returns the real part of the
    result, recomputing the feature if necessary ... ordered
    ``result[expression][solnum][coordinates]``"; property table: ``coord``
    ("the columns of the coord property are the coordinates for the evaluation
    points"; rows = space dimension means global coordinates), ``data``,
    ``expr``, ``unit``, ``solnum``, ``t`` ("the times to use, available when
    the underlying solution is transient").
  * results API page **Solution** (``comsol_api_results.52.156.html``, Table
    7-146 "Valid Property/Value Pairs for Solution Datasets"): a Solution
    dataset publishes ``solution`` ("The solution this dataset refers to",
    default "First compatible solution") plus ``comp`` and ``geom``; ``geom`` is
    "only applicable for components with more than one geometry", so the adapter
    falls back to the single component/geometry of the model when the readback
    is empty.  A dataset that publishes no ``solution`` property is refused
    unless ``spec.solution`` names one explicitly.
  * Programming Reference / Application Programming Guide (pages 275-281, 547,
    876, 1032): ``setIndex("expr", ...)`` / ``setIndex("unit", ...)``,
    ``model.sol(<tag>).getPVals()``, ``model.study(<tag>).feature(<ftag>)
    .getString("tunit")``, and the SolutionInfo route for stored output times.
  * The canonical stored-time route is ``SolutionInfo``
    (``model.sol(<tag>).getSolutioninfo()`` -> ``getLevelNames()/getSolnum()/
    getVals()/getOuterSolnum()``, documented in
    ``comsol_api_solver.51.10.html``).  The G3.3 §4 (F04) fix publishes the four
    accessors this module actually calls (``getSolutioninfo``,
    ``getOuterSolnum``, ``getMaxInner``, ``getLevelNames`` - see
    ``ALLOWLIST_ADDITIONS_PUBLISHED``) and ``dataset.solution_indices`` reads the
    outer/inner axes from them instead of inventing an index; the remaining
    SolutionInfo accessors and the EvaluationGroup route stay reported in
    ``ALLOWLIST_ADDITIONS``.  ``result.sample_path`` keeps using the verified
    substitutes it *can* call - the numerical feature's own transient ``t``
    property, ``SolverSequence.getPVals()`` and the associated study step's
    type/``tlist``/``tunit`` - and reports which one it used in
    ``solution_axis.source``.  Nothing is ever invented: an axis that cannot be
    read is reported as unavailable and the ``t`` column is omitted.

Result contract (drives ``tools/phase4_run_mcp.py`` ``_check_chain_a`` /
``_check_chain_b``)
------------------------------------------------------------------------------------
``data["samples"]`` is a list of row objects; ``data["time_values"]`` is the
stored time list for a time-dependent dataset.

* steady dataset -> one row per path point: ``x``, ``y``, ``z``, ``solnum``,
  then one key per requested expression;
* time-dependent dataset -> one row per (stored solution) x (path point), with
  ``time`` and ``t`` carrying the solution time and ``time_values`` carrying the
  same list once.

The sample table publishes **one** time key, ``time``: the former ``t`` alias was
removed because ``t`` and the temperature expression ``T`` differ only in case,
so any case-insensitive reader (``_sample_series()`` in the driver resolves row
keys that way) would resolve whichever key came first in the JSON object - i.e.
the meaning of the table depended on field order.  Instead of ordering keys, the
payload now carries ``columns``/``roles``: an unambiguous, order-independent
column contract that names each key's role.  The coordinate columns are the
requested path points (in the model length unit, echoed in ``data["units"]``);
the engine's own ``getCoordinates()`` readback is attempted and reported in
``coordinate_readback`` (``VERIFIED``/``MISMATCH``/``UNAVAILABLE``), and a
mismatch is a failed *verification* (``verification_status = FAILED``) rather
than a difference to ignore.  No unit conversion happens anywhere in this
module.

Expression names that would collide *exactly* with a column this adapter owns
(``x``/``y``/``z``/``solnum``/``time``/``t``) are refused with
``INVALID_REQUEST`` before the first engine call, as are two expressions that
differ only in case, and an expression whose name differs only in case from a
*published* column (e.g. ``Time`` or ``X``).  The rule compares against the
published columns only: chain B samples the expression ``"T"`` on a transient
dataset, and ``"t"`` is not ``"time"``, so that call stays legal and the table
stays unambiguous under any key order.

Mutation discipline (§4): the ephemeral ``Interp`` node is created, read and
removed in this module; a failure after the create reports
``status.execution_state_unknown`` / ``cleanup.cleanup_failed`` and never claims
that nothing happened.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
import uuid

from ._g2_contract import ExecutionContractError
from ._g2_engine import _call
from ._artifact_store import ArtifactStore
from ._g3_common import (
    allowlist_rejected,
    bound_model,
    call_probe,
    error_code_of,
    geometry_length_unit,
    geometry_sdim,
    node_not_found,
    operation_arguments,
    reject_unknown_keys,
    require_int,
    require_mapping,
    require_number,
    require_string,
    require_string_array,
    tag_list,
)

# ---------------------------------------------------------------------------
# vocabulary, limits and provenance
# ---------------------------------------------------------------------------

OPERATION_ID = "result.sample_path"

#: ``model.result().numerical().create(<ftag>,"Interp")`` - the type string is
#: documented on the Interp page of the results API (see the module docstring).
NUMERICAL_FEATURE_TYPE = "Interp"

#: ``spec`` keys this adapter accepts.  Compared with the published
#: ``common.schema.json#/$defs/EvaluationSpec`` (``additionalProperties: false``)
#: the extras are exactly the flat ``dataset`` the W16 driver sends and the
#: per-solution selection keys that ``_refuse_unsupported_options`` refuses with
#: ``API_UNSUPPORTED`` instead of ignoring them.
ACCEPTED_SPEC_KEYS: tuple[str, ...] = (
    "expressions", "dataset", "solution", "units", "aggregate", "complex_mode",
    "selection", "storage", "coordinate_frame", "weight_expression",
    "inner", "outer", "time", "frequency", "parameters",
)

#: Accepted but not implemented: refused explicitly (§ "never approximate").
REFUSED_SPEC_KEYS: tuple[str, ...] = ("inner", "outer", "time", "frequency", "parameters")

#: ``storage="auto"`` keeps samples inline up to this element count and publishes a
#: project-scoped artifact above it.  The delivered adapter hardcoded 1000 here; it
#: is named now because the boundary is part of the answer the caller gets back.
AUTO_ARTIFACT_ELEMENT_LIMIT = 1000

#: ``spec.solution`` as an object is the published ``SolutionSpec``.
ACCEPTED_SOLUTION_SPEC_KEYS: tuple[str, ...] = (
    "dataset", "solution", "inner", "outer", "time", "frequency", "parameters",
)

#: Path kinds this adapter implements.  ``path_definition.kind`` is refused for
#: anything else rather than silently reinterpreted.
PATH_KINDS = ("line",)

MIN_SAMPLE_POINTS = 2
MAX_SAMPLE_POINTS = 10_000
MAX_EXPRESSIONS = 32

COORDINATE_COLUMNS = ("x", "y", "z")
SOLUTION_INDEX_COLUMN = "solnum"
TIME_COLUMN = "time"
TIME_COLUMN_ALIAS = "t"

EPHEMERAL_TAG_STEM = "cmssp"
MAX_EPHEMERAL_TAG_ATTEMPTS = 1000

#: Study steps that produce a time-dependent solution.  Source: the verified
#: COMSOL 6.4 study-step table restated in ``_g3_w16.STUDY_STEP_TYPE_SOURCES``
#: (``comsol_api_solver.51.81`` "Transient" = Time Dependent,
#: ``comsol_api_solver.51.82`` "TimeDiscrete"); repeated here so this adapter
#: does not import another workstream module for two names.
TIME_DEPENDENT_STUDY_STEPS = frozenset({"Transient", "TimeDiscrete"})

#: Encoding of a ``double[][]`` argument on the worker wire.  The worker reads
#: ``java_signature`` (``_g2_contract`` TypedValue contract) to select the
#: ``set(String,double[][])`` overload; the nested list alone would be ambiguous
#: between the ``double[][]``/``int[][]``/``String[][]`` overloads.
DOUBLE_MATRIX_SIGNATURE = "double[][]"
DOUBLE_MATRIX_KIND = "float64"

#: Worker allow-list entries this adapter would use if the Java worker published
#: them.  They are *not* used (the implementations below never call them); they
#: are reported so the gap is actionable.  See ``ALLOWLIST_ADDITIONS``.
ALLOWLIST_ADDITIONS: tuple[str, ...] = (
    # SolutionInfo (comsol_api_solver.51.10): the canonical stored output-time /
    # level-name route; needs the accessor plus its methods.
    "getLevels",
    "getSolnum",
    "getVals",
    # Study.getSolverSequences(String) would attribute a solver sequence to a
    # study *step* exactly, instead of the study-level association used here.
    "getSolverSequences",
    # EvaluationGroup (Application Programming Guide p.280): the documented
    # "looplevelinput first/last + getReal()" route for output times.
    "evaluationGroup",
    "looplevelinput",
)

#: SolutionInfo route entries that were *published* by the G3.3 §4 (F04) fix and
#: are now actually called by ``read_solution_binding``.  They are listed here
#: (and kept out of ``ALLOWLIST_ADDITIONS``) because the Java worker allow-list
#: and this adapter have to agree: a name reported as "missing" while the code
#: dispatches it is exactly the silent-hole class the allow-list test forbids.
#: javap -cp apiplugins/com.comsol.api_1.0.0.jar (COMSOL 6.4.0.293):
#:   SolverSequence.getSolutioninfo() -> SolutionInfo
#:   SolutionInfo.getOuterSolnum() -> int[]
#:   SolutionInfo.getMaxInner(int[]) -> int
#:   SolutionInfo.getLevelNames() -> String[]
ALLOWLIST_ADDITIONS_PUBLISHED: tuple[str, ...] = (
    "getSolutioninfo",
    "getOuterSolnum",
    "getMaxInner",
    "getLevelNames",
)

#: Solver-sequence introspection that the worker *does* publish but that this
#: adapter deliberately does not call (it would only corroborate what the study
#: step type already decides).  Recorded so the capability gap is not confused
#: with the additions above.
ALLOWLIST_AVAILABLE_UNUSED: tuple[str, ...] = (
    "getSequenceType",
    "getPNames",
    "getParamVals",
    "getParamNames",
    "isAttached",
)

#: Paths deliberately not implemented by this adapter (W16 infrastructure only,
#: per §8).  Recorded in the result so the boundary is explicit.
UNVERIFIED_PATHS: tuple[dict[str, str], ...] = (
    {"path": "spec.selection / spec.aggregate / spec.weight_expression",
     "reason": "outside the minimal sampler; refused with API_UNSUPPORTED instead of approximated"},
    {"path": "spec.solution.inner / .outer / .time / .frequency / .parameters",
     "reason": "per-solution selection is not implemented; every stored solution is returned and "
               "indexed by the 'solnum' column"},
    {"path": "spec.complex_mode other than 'real'",
     "reason": "NumericalFeature.getData() returns the real part (documented); no silent complex reshuffle"},
    {"path": "path_definition kinds other than 'line'",
     "reason": "only the line kind the W16 chains need is implemented"},
    {"path": "coordinate readback via getCoordinates()",
     "reason": "documented but not on the worker allow-list; the row coordinates are the requested path points"},
)
 
SUPPORTED_DATASET_TYPES: frozenset[str] = frozenset({
    "Solution", "CutPoint3D", "CutPoint2D", "CutPoint1D",
    "CutLine3D", "CutLine2D", "CutLine1D", "CutPlane",
    "Join", "Revolution2D", "Revolution1D", "Mirror3D", "Mirror2D",
    "Grid3D", "Grid2D", "Grid1D", "Edge3D", "Surface", "Volume",
    "Parametric", "Receiver", "Average", "Integral",
})

SUPPORTED_NUMERICAL_TYPES: frozenset[str] = frozenset({
    "EvalGlobal", "EvalPoint", "Eval",
    "IntVolume", "IntSurface", "IntLine", "IntPoint",
    "AvVolume", "AvSurface", "AvLine", "AvPoint",
    "MaxVolume", "MaxSurface", "MaxLine", "MaxPoint",
    "MinVolume", "MinSurface", "MinLine", "MinPoint",
    "Interp",
})

COMPLEX_MODES: frozenset[str] = frozenset({"preserve", "real", "imag", "abs", "phase"})
AGGREGATE_MODES: frozenset[str] = frozenset({"none", "global", "integral", "average", "minimum", "maximum", "std", "rms"})


# ---------------------------------------------------------------------------
# argument validation (all of it happens before the first engine call)
# ---------------------------------------------------------------------------


def _finite(value: Any, label: str) -> float:
    number = require_number(value, label)
    return float(number)


def _point(value: Any, label: str) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, Mapping)):
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must be an array of numbers")
    items = list(value)
    if not items:
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must not be empty")
    if len(items) > len(COORDINATE_COLUMNS):
        raise ExecutionContractError(
            "INVALID_REQUEST", f"{label} has {len(items)} coordinates; at most x/y/z are supported"
        )
    return [_finite(item, f"{label}[{index}]") for index, item in enumerate(items)]


def _refuse_unsupported_options(spec: Mapping[str, Any]) -> None:
    """Refuse the EvaluationSpec options this minimal sampler does not honour."""
    aggregate = spec.get("aggregate")
    if aggregate is not None and aggregate != "none":
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            f"spec.aggregate={aggregate!r} is not implemented by this minimal sampling adapter "
            f"(it returns raw point samples; see UNVERIFIED_PATHS)",
        )
    complex_mode = spec.get("complex_mode")
    if complex_mode is not None and complex_mode != "real":
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            f"spec.complex_mode={complex_mode!r} is not implemented; NumericalFeature.getData() returns "
            f"the real part (documented) and this adapter never silently reshapes complex data",
        )
    storage = spec.get("storage")
    if storage is not None and storage not in {"inline", "auto"}:
        raise ExecutionContractError(
            "API_UNSUPPORTED", f"spec.storage={storage!r} is not implemented by this adapter (samples are inline)"
        )
    for name in REFUSED_SPEC_KEYS + ("selection", "weight_expression", "coordinate_frame"):
        if spec.get(name) is not None:
            raise ExecutionContractError(
                "API_UNSUPPORTED",
                f"spec.{name} is not implemented by this minimal sampling adapter; "
                f"the adapter always reads every stored solution of the dataset",
            )


def _validate_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the operation body; refusal happens before any engine call.

    The accepted shape is exactly what the W16 driver sends
    (``spec.expressions``/``spec.dataset``/``spec.solution`` plus
    ``path_definition``); ``spec.solution`` may also be the published
    ``common.schema.json#/$defs/SolutionSpec`` object, whose unimplemented
    per-solution fields (``inner``/``outer``/``time``/``frequency``/
    ``parameters``) are refused with ``API_UNSUPPORTED`` rather than ignored.
    ``ACCEPTED_SPEC_KEYS`` records the resulting vocabulary against the schema.
    """
    args = operation_arguments(arguments, ("spec", "path_definition"), ("spec", "path_definition"))
    spec = require_mapping(args["spec"], "spec")
    reject_unknown_keys(spec, ACCEPTED_SPEC_KEYS, "spec")
    expressions = require_string_array(spec.get("expressions"), "spec.expressions")
    if len(expressions) > MAX_EXPRESSIONS:
        raise ExecutionContractError(
            "INVALID_REQUEST", f"spec.expressions accepts at most {MAX_EXPRESSIONS} expressions"
        )
    _check_row_key_collisions(expressions)
    _refuse_unsupported_options(spec)

    dataset = spec.get("dataset")
    solution = spec.get("solution")
    if solution is not None and isinstance(solution, Mapping):
        solution_spec = require_mapping(solution, "spec.solution")
        reject_unknown_keys(solution_spec, ACCEPTED_SOLUTION_SPEC_KEYS, "spec.solution")
        if dataset is None:
            dataset = solution_spec.get("dataset")
        solution = solution_spec.get("solution")
        _refuse_unsupported_options({key: value for key, value in solution_spec.items() if key != "dataset"})
    dataset_tag = require_string(dataset, "spec.dataset") if dataset is not None else None
    if dataset_tag is None:
        raise ExecutionContractError(
            "INVALID_REQUEST",
            "spec.dataset is required: this adapter binds the ephemeral Interp feature to a named dataset "
            "(EvaluationSpec.SolutionSpec requires 'dataset')",
        )
    solution_tag = require_string(solution, "spec.solution") if solution is not None else None

    units = spec.get("units")
    expression_units: list[str] | None = None
    if units is not None:
        expression_units = require_string_array(units, "spec.units")
        if len(expression_units) != len(expressions):
            raise ExecutionContractError(
                "INVALID_REQUEST",
                f"spec.units has {len(expression_units)} entries for {len(expressions)} expressions",
            )

    path = require_mapping(args["path_definition"], "path_definition")
    reject_unknown_keys(path, ("kind", "start", "end", "samples"), "path_definition")
    kind = require_string(path.get("kind"), "path_definition.kind")
    if kind not in PATH_KINDS:
        raise ExecutionContractError(
            "INVALID_REQUEST",
            f"path_definition.kind {kind!r} is not supported; this adapter implements {list(PATH_KINDS)}",
        )
    start = _point(path.get("start"), "path_definition.start")
    end = _point(path.get("end"), "path_definition.end")
    if len(start) != len(end):
        raise ExecutionContractError(
            "INVALID_REQUEST",
            f"path_definition.start has {len(start)} coordinates but end has {len(end)}",
        )
    samples = require_int(path.get("samples"), "path_definition.samples",
                          minimum=MIN_SAMPLE_POINTS, maximum=MAX_SAMPLE_POINTS)
    if start == end:
        raise ExecutionContractError(
            "INVALID_REQUEST", "path_definition.start and end are identical; the line is degenerate"
        )
    return {
        "expressions": expressions,
        "expression_units": expression_units,
        "dataset": dataset_tag,
        "solution": solution_tag,
        "kind": kind,
        "start": start,
        "end": end,
        "samples": samples,
    }


def _path_points(request: Mapping[str, Any]) -> list[list[float]]:
    """Evenly spaced points from ``start`` to ``end`` (both endpoints included)."""
    start, end, count = request["start"], request["end"], int(request["samples"])
    points: list[list[float]] = []
    for index in range(count):
        fraction = index / (count - 1)
        points.append([value + (end[axis] - value) * fraction for axis, value in enumerate(start)])
    return points


def _check_row_key_collisions(expressions: Sequence[str]) -> None:
    """Refuse expression names that would collide with a column this adapter owns.

    Two rules, both **order-independent**:

    * an expression whose *exact* name is one of the keys this adapter writes
      with a different value (``x``/``y``/``z``/``solnum``/``time``/``t``) is
      refused - that would be one JSON key with two meanings;
    * an expression whose name differs only in case from a *published* column
      (``x``, ``y``, ``z``, ``solnum``, ``time``) is refused as well, because any
      case-insensitive reader (the phase-4 driver's ``_sample_series`` resolves
      row keys that way) would then pick whichever of the two keys comes first
      in the JSON object - i.e. the meaning of the table would depend on field
      order.  The comparison is deliberately against the *published* columns
      only: the W16 chain-B call samples the expression ``T`` on a transient
      dataset, and ``"t" != "time"`` case-insensitively, so ``T`` stays legal.

    The time axis is published once, as ``time`` (see :data:`TIME_COLUMN`); the
    former ``t`` alias was removed precisely because it made the table
    order-dependent: ``t`` and the expression ``T`` differ only in case.
    """
    published = {*COORDINATE_COLUMNS, SOLUTION_INDEX_COLUMN, TIME_COLUMN}
    reserved = {*published, TIME_COLUMN_ALIAS}
    seen: dict[str, str] = {}
    for expression in expressions:
        key = expression.strip()
        if key in reserved:
            raise ExecutionContractError(
                "INVALID_REQUEST",
                f"expression name {expression!r} collides with a column this adapter publishes "
                f"({sorted(reserved)}); rename it or request it through a dedicated evaluation action",
            )
        folded = key.lower()
        if folded in {column.lower() for column in published}:
            raise ExecutionContractError(
                "INVALID_REQUEST",
                f"expression name {expression!r} differs only in case from the published column "
                f"{folded!r}; a case-insensitive reader of the sample table would resolve whichever "
                f"key comes first, so the table's meaning would depend on JSON field order",
            )
        if folded in seen:
            raise ExecutionContractError(
                "INVALID_REQUEST",
                f"expressions {seen[folded]!r} and {expression!r} differ only in case; the sample table "
                f"cannot publish both (the driver resolves row keys case-insensitively)",
            )
        seen[folded] = expression


# ---------------------------------------------------------------------------
# engine helpers
# ---------------------------------------------------------------------------


def _record(node: Any, method: str, *args: Any, errors: list[dict[str, Any]]) -> Any:
    """Call an optional accessor and *record* a refusal instead of hiding it."""
    result = call_probe(node, method, *args)
    if result["ok"]:
        return result["value"]
    detail = result.get("error") or {}
    entry = {
        "method": method,
        "code": detail.get("code"),
        "message": detail.get("message"),
        "allowlist_entry_required": detail.get("allowlist_entry_required"),
    }
    if entry not in errors:
        errors.append(entry)
    return None


def _string_or_none(node: Any, property_name: str, errors: list[dict[str, Any]]) -> str | None:
    value = _record(node, "getString", property_name, errors=errors)
    return value if isinstance(value, str) and value else None


def _node_type(node: Any, errors: list[dict[str, Any]] | None = None) -> str | None:
    val = _record(node, "getType", errors=errors if errors is not None else [])
    return str(val) if isinstance(val, str) and val else None


def _prop_value(node: Any, property_name: str) -> Any:
    for method in ("getString", "getDouble", "getInt", "getBoolean", "getStringArray", "getDoubleArray"):
        probe = call_probe(node, method, property_name)
        if probe["ok"] and probe["value"] is not None:
            return probe["value"]
    return None




def _call_recorded(node: Any, method: str, *args: Any, errors: list[dict[str, Any]]) -> Any:
    """``_call`` for a step whose failure must carry its allow-list status."""
    try:
        return _call(node, method, *args)
    except ExecutionContractError as exc:
        entry = {
            "method": method,
            "code": exc.code,
            "message": str(exc),
            "allowlist_entry_required": method if allowlist_rejected(exc) else None,
        }
        if entry not in errors:
            errors.append(entry)
        raise


def _unique_tag(existing: Sequence[str]) -> str:
    taken = {str(item) for item in existing}
    for index in range(1, MAX_EPHEMERAL_TAG_ATTEMPTS):
        candidate = f"{EPHEMERAL_TAG_STEM}{index}"
        if candidate not in taken:
            return candidate
    raise ExecutionContractError(
        "EXECUTION_STATE_UNKNOWN",
        f"no free ephemeral tag ({EPHEMERAL_TAG_STEM}1..{EPHEMERAL_TAG_STEM}{MAX_EPHEMERAL_TAG_ATTEMPTS - 1}) "
        f"is available in the results tree",
    )


def _axis_values(value: Any, count: int) -> list[float] | None:
    """Return ``count`` finite numbers, or ``None`` when the read is unusable."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, Mapping)):
        return None
    items = list(value)
    if len(items) != count:
        return None
    numbers: list[float] = []
    for item in items:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        number = float(item)
        if not math.isfinite(number):
            return None
        numbers.append(number)
    return numbers


def _normalise_samples(raw: Any, expressions: Sequence[str], point_count: int) -> tuple[list[list[list[float]]], int]:
    """Validate ``getData()`` -> ``[expression][solnum][coordinates]``.

    The documented return type is a three-dimensional matrix
    (``comsol_api_results.52.082.html``).  A two-dimensional ``[expression]
    [coordinates]`` readback (single stored solution) is accepted as
    ``solnum == 1`` instead of being misparsed as a missing dimension.
    """
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, Mapping)):
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            f"the Interp feature returned {type(raw).__name__} instead of the documented "
            f"result[expression][solnum][coordinates] matrix",
        )
    series = list(raw)
    if len(series) != len(expressions):
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            f"the Interp feature returned {len(series)} expression series for {len(expressions)} requested "
            f"expressions",
        )
    rows: list[list[list[float]]] = []
    solution_count: int | None = None
    for expression_index, expression in enumerate(expressions):
        block = series[expression_index]
        if not isinstance(block, Sequence) or isinstance(block, (str, bytes, Mapping)):
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN",
                f"the Interp feature returned a non-matrix block for expression {expression!r}",
            )
        block_items = list(block)
        # rank-2 readback: [coordinates] for a single stored solution.
        if block_items and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in block_items):
            block_items = [block_items]
        if not block_items:
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN",
                f"the Interp feature returned no samples for expression {expression!r}",
            )
        if solution_count is None:
            solution_count = len(block_items)
        elif solution_count != len(block_items):
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN",
                f"expression {expression!r} returned {len(block_items)} stored solutions while the first "
                f"expression returned {solution_count}",
            )
        per_solution: list[list[float]] = []
        for solnum_index, solution_block in enumerate(block_items):
            if not isinstance(solution_block, Sequence) or isinstance(solution_block, (str, bytes, Mapping)):
                raise ExecutionContractError(
                    "EXECUTION_STATE_UNKNOWN",
                    f"expression {expression!r} solution {solnum_index + 1} is not a coordinate vector",
                )
            values = list(solution_block)
            if len(values) != point_count:
                raise ExecutionContractError(
                    "EXECUTION_STATE_UNKNOWN",
                    f"expression {expression!r} solution {solnum_index + 1} returned {len(values)} "
                    f"coordinates for {point_count} requested path points",
                )
            numbers: list[float] = []
            for point_index, value in enumerate(values):
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ExecutionContractError(
                        "EXECUTION_STATE_UNKNOWN",
                        f"expression {expression!r} solution {solnum_index + 1} point {point_index} is not "
                        f"a number ({type(value).__name__})",
                    )
                number = float(value)
                if not math.isfinite(number):
                    raise ExecutionContractError(
                        "EXECUTION_STATE_UNKNOWN",
                        f"expression {expression!r} solution {solnum_index + 1} point {point_index} is not "
                        f"finite ({number!r}); the path may leave the geometry",
                    )
                numbers.append(number)
            per_solution.append(numbers)
        rows.append(per_solution)
    if solution_count is None:
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "the Interp feature returned no stored solutions")
    return rows, solution_count


# ---------------------------------------------------------------------------
# context resolution
# ---------------------------------------------------------------------------


def _normalise_coordinate_readback(raw: Any, points: Sequence[Sequence[float]], dimension: int,
                                   errors: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare the engine's coordinate readback with the requested path points.

    The documented ``getCoordinates()`` readback is the same
    ``[space_dimension][points]`` matrix as the ``coord`` property.  A refusal
    (the Worker does not publish the method yet - see ``ALLOWLIST_ADDITIONS``) is
    reported as ``UNAVAILABLE`` with the recorded reason; a readback that does
    not match the requested points is a **failed verification**, not a
    difference to ignore, because it would mean the samples belong to different
    coordinates than the caller asked for.
    """
    result: dict[str, Any] = {
        "attempted": True,
        "status": "UNAVAILABLE",
        "method": "result.numerical(<tag>).getCoordinates()",
        "coordinates": None,
        "max_deviation": None,
        "allowlist_entry_required": "getCoordinates",
        "reason": None,
    }
    if raw is None:
        recorded = [row for row in errors if row.get("method") == "getCoordinates"]
        result["reason"] = (
            recorded[-1].get("message") if recorded
            else "the numerical feature did not publish getCoordinates()"
        )
        result["error_code"] = recorded[-1].get("code") if recorded else None
        return result
    try:
        matrix = _coordinate_matrix(raw, dimension, len(points))
    except ExecutionContractError as exc:
        result.update(status="MISMATCH", reason=str(exc), error_code=exc.code)
        return result
    deviation = 0.0
    for row_index, row in enumerate(matrix):
        for point_index, value in enumerate(row):
            deviation = max(deviation, abs(value - float(points[point_index][row_index])))
    result["coordinates"] = matrix
    result["max_deviation"] = deviation
    tolerance = 1e-12 * max(1.0, max(abs(value) for row in matrix for value in row) if matrix else 1.0)
    if deviation > tolerance:
        result.update(status="MISMATCH", allowlist_entry_required=None,
                      reason=(f"the engine readback differs from the requested path points by up to "
                              f"{deviation!r}; the samples would not belong to the requested coordinates"))
    else:
        result.update(status="VERIFIED", allowlist_entry_required=None, reason=None)
    return result


def _coordinate_matrix(raw: Any, dimension: int, point_count: int) -> list[list[float]]:
    """Validate the ``getCoordinates()`` readback as ``[dimension][points]``."""
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, Mapping)):
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            f"getCoordinates() returned {type(raw).__name__} instead of the documented matrix",
        )
    rows = list(raw)
    if len(rows) != dimension:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            f"getCoordinates() returned {len(rows)} coordinate rows for a {dimension}D dataset",
        )
    matrix: list[list[float]] = []
    for row in rows:
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes, Mapping)):
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN", "getCoordinates() returned a non-vector coordinate row"
            )
        values = list(row)
        if len(values) != point_count:
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN",
                f"getCoordinates() returned {len(values)} coordinates for {point_count} path points",
            )
        numbers: list[float] = []
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ExecutionContractError(
                    "EXECUTION_STATE_UNKNOWN", f"getCoordinates() returned a non-finite coordinate ({value!r})"
                )
            numbers.append(float(value))
        matrix.append(numbers)
    return matrix


def _require_dataset(results: Any, dataset_tag: str) -> Any:
    container = call_probe(results, "dataset")
    if not container["ok"]:
        raise ExecutionContractError(
            "API_UNSUPPORTED", "this results tree does not expose model.result().dataset()"
        )
    dataset_list = container["value"]
    tags = tag_list(dataset_list)
    if dataset_tag not in tags:
        raise node_not_found(
            f"solution dataset {dataset_tag!r} does not exist (available: {sorted(tags)})"
        )
    return _call(dataset_list, "get", dataset_tag)


def _coordinate_context(model: Any, dataset_node: Any, errors: list[dict[str, Any]]) -> dict[str, Any]:
    """Resolve the dataset's geometry, its length unit and its space dimension."""
    context: dict[str, Any] = {"component": None, "geometry": None, "length_unit": None,
                               "space_dimension": None, "source": None}
    component = _string_or_none(dataset_node, "comp", errors)
    geometry = _string_or_none(dataset_node, "geom", errors)
    if component and geometry:
        context.update(component=component, geometry=geometry, source="dataset.comp/dataset.geom")
    else:
        # A dataset that does not publish comp/geom still belongs to a component:
        # use it only when the model has exactly one component and one geometry.
        components = _record(model, "component", errors=errors)
        try:
            component_tags = tag_list(components) if components is not None else []
        except ExecutionContractError:
            component_tags = []
        if len(component_tags) == 1:
            comp_node = _call(model, "component", component_tags[0])
            geometries = _record(comp_node, "geom", errors=errors)
            try:
                geometry_tags = tag_list(geometries) if geometries is not None else []
            except ExecutionContractError:
                geometry_tags = []
            if len(geometry_tags) == 1:
                context.update(component=component_tags[0], geometry=geometry_tags[0],
                               source="single component/geometry of the model")
    if context["component"] and context["geometry"]:
        geometry_node = _call(_call(model, "component", context["component"]), "geom", context["geometry"])
        context["length_unit"] = geometry_length_unit(geometry_node)
        context["space_dimension"] = geometry_sdim(geometry_node)
        is_axi = None
        if hasattr(geometry_node, "isAxisymmetric") and callable(getattr(geometry_node, "isAxisymmetric")):
            try:
                is_axi = geometry_node.isAxisymmetric()
            except Exception:
                is_axi = None
        if not isinstance(is_axi, bool):
            try:
                comp_obj = _call(model, "component", context["component"])
                coords = comp_obj.spatialCoord()
                if coords and any(c in ("r", "phi") for c in coords) and not any(c in ("x", "y") for c in coords):
                    is_axi = True
            except Exception:
                pass
        if not isinstance(is_axi, bool):
            axi_prop = call_probe(geometry_node, "getBoolean", "axisymmetric")
            if axi_prop["ok"] and isinstance(axi_prop["value"], bool):
                is_axi = axi_prop["value"]
            else:
                is_axi = False
        context["axisymmetric"] = is_axi
    return context


def _check_point_dimension(request: Mapping[str, Any], context: Mapping[str, Any]) -> None:
    """A global-coordinate ``coord`` matrix must have one row per space dimension.

    The documented rule (Interp page): rows == space dimension means global
    coordinates; fewer rows means the values are *parameter values on a face or
    edge*.  Silently accepting a shorter vector would therefore change the
    meaning of the sampling, so a mismatch is refused.
    """
    space_dimension = context.get("space_dimension")
    if space_dimension is None:
        return
    if len(request["start"]) != space_dimension:
        raise ExecutionContractError(
            "INVALID_REQUEST",
            f"path_definition.start has {len(request['start'])} coordinates but the dataset's geometry "
            f"({context.get('component')}/{context.get('geometry')}) is {space_dimension}D; the Interp coord "
            f"property only means global coordinates when its row count equals the space dimension",
        )


# ---------------------------------------------------------------------------
# the solution axis (time steps)
# ---------------------------------------------------------------------------


def _require_solution(model: Any, solution_tag: str, errors: list[dict[str, Any]]) -> None:
    """Resolve ``spec.solution`` before anything is written (identity only)."""
    solutions = _record(model, "sol", errors=errors)
    try:
        tags = tag_list(solutions) if solutions is not None else []
    except ExecutionContractError:
        tags = []
    if solution_tag not in tags:
        raise node_not_found(
            f"solution {solution_tag!r} does not exist (available: {sorted(tags)}); the ephemeral Interp "
            f"feature is not created for an unknown solution",
        )


def _study_steps(model: Any, study_tag: str, errors: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str | None]:
    """Read the associated study's steps (tag, type, declared times and unit)."""
    studies = _record(model, "study", errors=errors)
    try:
        study_tags = tag_list(studies) if studies is not None else []
    except ExecutionContractError:
        study_tags = []
    if study_tag not in study_tags:
        return [], "the associated study does not exist in this model"
    study_node = _call(model, "study", study_tag)
    features = _record(study_node, "feature", errors=errors)
    if features is None:
        return [], "the study does not expose feature()"
    steps: list[dict[str, Any]] = []
    for step_tag in tag_list(features):
        step = _call(features, "get", step_tag)
        step_type = _record(step, "getType", errors=errors)
        declared = _record(step, "getDoubleArray", "tlist", errors=errors)
        steps.append({
            "tag": step_tag,
            "type": str(step_type) if isinstance(step_type, str) else None,
            "declared_output_times": list(declared) if isinstance(declared, Sequence)
            and not isinstance(declared, (str, bytes)) else None,
            "declared_time_unit": _string_or_none(step, "tunit", errors),
        })
    return steps, None


def _resolve_solution_axis(model: Any, feature: Any, request: Mapping[str, Any], solution_count: int,
                           errors: list[dict[str, Any]]) -> dict[str, Any]:
    """Resolve the per-stored-solution time axis without inventing values.

    Sources, in the order the adapter trusts them:

    1. the associated study step's type (``SolverSequence.study()`` ->
       ``Study.feature().getType()``) decides whether the dataset is
       time-dependent at all;
    2. the stored values themselves - the Interp feature's documented ``t``
       ("the times to use, available when the underlying solution is
       transient") or ``SolverSequence.getPVals()``;
    3. as a clearly labelled fallback the transient step's declared output
       ``tlist`` (COMSOL stores the output times by interpolation from the
       solver's time steps unless configured otherwise).

    An axis that cannot be read is reported as unavailable; the ``t`` column is
    then omitted rather than filled with a guess.
    """
    axis: dict[str, Any] = {
        "time_dependent": None,
        "status": "unavailable",
        "source": None,
        "values": None,
        "unit": None,
        "stored_solutions": solution_count,
        "solution": request.get("solution"),
        "study": None,
        "study_steps": [],
        "feature_times": None,
        "solver_values": None,
        "conflicts": [],
        "reason": None,
    }
    solution_tag = request.get("solution")
    if solution_tag is None:
        axis["reason"] = ("spec.solution was not provided and the dataset publishes no 'solution' property; "
                         "the stored solution axis cannot be attributed")
        return axis

    solutions = _record(model, "sol", errors=errors)
    try:
        solution_tags = tag_list(solutions) if solutions is not None else []
    except ExecutionContractError:
        solution_tags = []
    if solution_tag not in solution_tags:
        raise node_not_found(
            f"solution {solution_tag!r} does not exist (available: {sorted(solution_tags)})"
        )
    solution_node = _call(model, "sol", solution_tag)

    feature_times = _axis_values(_record(feature, "getDoubleArray", "t", errors=errors), solution_count)
    solver_values = _axis_values(_record(solution_node, "getPVals", errors=errors), solution_count)
    axis["feature_times"] = feature_times
    axis["solver_values"] = solver_values

    study_tag = _record(solution_node, "study", errors=errors)
    steps: list[dict[str, Any]] = []
    study_error: str | None = None
    if isinstance(study_tag, str) and study_tag:
        axis["study"] = study_tag
        steps, study_error = _study_steps(model, study_tag, errors)
        axis["study_steps"] = [{"tag": step["tag"], "type": step["type"]} for step in steps]
    else:
        study_error = "model.sol(<tag>).study() did not report an associated study"

    decided: bool | None = None
    if len(steps) == 1 and steps[0]["type"] is not None:
        decided = steps[0]["type"] in TIME_DEPENDENT_STUDY_STEPS
        if steps[0]["declared_time_unit"]:
            axis["unit"] = steps[0]["declared_time_unit"]
    elif len(steps) > 1:
        study_error = (f"the associated study {study_tag!r} has {len(steps)} steps; this adapter cannot "
                       f"attribute the dataset's solution to a single step")
    elif not steps:
        study_error = study_error or f"the associated study {study_tag!r} reports no steps"

    if decided is False:
        axis.update(time_dependent=False, status="not_time_dependent",
                    reason=None if steps else study_error)
        if feature_times is not None:
            axis["conflicts"].append(
                "the numerical feature published a 't' readback although the associated study step is not "
                "time-dependent; the readback was not used as a time axis"
            )
        return axis

    if decided is None and feature_times is not None:
        # The feature's own transient-only property answered directly.
        axis.update(time_dependent=True, status="verified", source="numerical_feature.t", values=feature_times)
        if steps and steps[0]["declared_time_unit"]:
            axis["unit"] = steps[0]["declared_time_unit"]
        return axis

    if decided is True:
        if feature_times is not None:
            axis.update(time_dependent=True, status="verified", source="numerical_feature.t", values=feature_times)
            return axis
        if solver_values is not None:
            axis.update(time_dependent=True, status="verified", source="solver_sequence.getPVals",
                        values=solver_values)
            return axis
        declared = steps[0]["declared_output_times"] if steps else None
        declared_values = _axis_values(declared, solution_count)
        if declared_values is not None:
            axis.update(time_dependent=True, status="declared", source="study_step.tlist",
                        values=declared_values,
                        reason="the stored time values could not be read; the transient step's declared "
                               "output times are used as the axis (COMSOL stores the output times by "
                               "interpolation from the solver's time steps by default)")
            return axis
        axis.update(time_dependent=True, status="unavailable",
                    reason="the dataset is time-dependent but neither the feature's 't' readback, "
                           "getPVals() nor the step's tlist yielded one value per stored solution")
        return axis

    axis["reason"] = (study_error or "the stored solution axis could not be attributed") + \
        "; no time column is published rather than a guessed one"
    return axis


# ---------------------------------------------------------------------------
# the operation
# ---------------------------------------------------------------------------


def _remove_ephemeral(container: Any, tag: str, cleanup: dict[str, Any], errors: list[dict[str, Any]]) -> None:
    """Remove the ephemeral node and verify the removal (never leave it behind silently)."""
    try:
        _call(container, "remove", tag)
    except Exception as exc:  # noqa: BLE001 - the cleanup result is the evidence
        cleanup["cleanup_failed"] = True
        cleanup["error"] = {
            "code": error_code_of(exc),
            "message": f"the ephemeral numerical node {tag!r} could not be removed ({type(exc).__name__})",
        }
        errors.append({"method": "remove", "code": cleanup["error"]["code"],
                       "message": cleanup["error"]["message"], "allowlist_entry_required": None})
        return
    try:
        remaining = tag_list(container)
    except Exception as exc:  # noqa: BLE001
        cleanup["removed"] = True
        cleanup["cleanup_failed"] = True
        cleanup["error"] = {
            "code": error_code_of(exc),
            "message": f"{tag!r} was removed but the numerical feature list could not be re-read",
        }
        return
    cleanup["removed"] = True
    cleanup["verified_removed"] = tag not in remaining
    if not cleanup["verified_removed"]:
        cleanup["cleanup_failed"] = True
        cleanup["error"] = {
            "code": "EXECUTION_STATE_UNKNOWN",
            "message": f"{tag!r} was removed but the numerical feature list still lists it",
        }


def _sample_with_feature(feature: Any, request: Mapping[str, Any], dataset_node: Any,
                         context: Mapping[str, Any], model: Any,
                         read_errors: list[dict[str, Any]]) -> dict[str, Any]:
    """Configure the ephemeral Interp feature, read it and build the sample table."""
    expressions = list(request["expressions"])
    points = _path_points(request)
    dimension = len(request["start"])
    dataset_tag = request["dataset"]

    _call(feature, "set", "data", dataset_tag)
    data_readback = _record(feature, "getString", "data", errors=read_errors)
    if isinstance(data_readback, str) and data_readback and data_readback != dataset_tag:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            f"the ephemeral Interp feature reads back data={data_readback!r} instead of the requested "
            f"dataset {dataset_tag!r}",
        )
    feature_type = _record(feature, "getType", errors=read_errors)
    if isinstance(feature_type, str) and feature_type and feature_type != NUMERICAL_FEATURE_TYPE:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            f"the created numerical feature reports type {feature_type!r} instead of {NUMERICAL_FEATURE_TYPE!r}",
        )
    for index, expression in enumerate(expressions):
        _call(feature, "setIndex", "expr", expression, index)
    expression_units = request.get("expression_units")
    if expression_units:
        for index, unit in enumerate(expression_units):
            _call(feature, "setIndex", "unit", unit, index)

    # coord: rows = space dimension, columns = evaluation points (Interp page).
    coordinate_rows = [[point[axis] for point in points] for axis in range(dimension)]
    coord_payload = {
        "kind": DOUBLE_MATRIX_KIND,
        "shape": [dimension, len(points)],
        "data": coordinate_rows,
        "java_signature": DOUBLE_MATRIX_SIGNATURE,
    }
    try:
        _call(feature, "setInterpolationCoordinates", coord_payload)
    except Exception:
        _call(feature, "set", "coord", coord_payload)



    try:
        _call(feature, "run")
    except Exception:
        pass

    data_readback_error = None
    raw = _call_recorded(feature, "getData", errors=read_errors)
    try:
        series, solution_count = _normalise_samples(raw, expressions, len(points))
    except ExecutionContractError as exc:
        data_readback_error = exc
        raise
    finally:
        if data_readback_error is not None:
            # make the readback state of the feature part of the failure record
            read_errors.append({"method": "getData", "code": data_readback_error.code,
                                "message": str(data_readback_error), "allowlist_entry_required": None})

    complex_readback = _record(feature, "isComplex", errors=read_errors)
    unit_readback = _record(feature, "getStringArray", "unit", errors=read_errors)
    expr_readback = _record(feature, "getStringArray", "expr", errors=read_errors)
    property_names = _record(feature, "properties", errors=read_errors)
    # C07a: read the coordinates back from the engine instead of only echoing the
    # requested points.  ``getCoordinates()`` is the documented readback; when the
    # Worker does not publish it the refusal is recorded (never guessed) and the
    # requested points stay the published coordinates, with the reason attached.
    coordinates_readback = _record(feature, "getCoordinates", errors=read_errors)
    coordinate_readback = _normalise_coordinate_readback(
        coordinates_readback, points, dimension, read_errors
    )

    axis = _resolve_solution_axis(model, feature, request, solution_count, read_errors)
    times: list[float] | None = axis["values"]
    time_dependent = axis["time_dependent"]

    declared_units = list(unit_readback) if isinstance(unit_readback, Sequence) \
        and not isinstance(unit_readback, (str, bytes)) else []
    expression_unit_map: dict[str, Any] = {}
    for index, expression in enumerate(expressions):
        value = declared_units[index] if index < len(declared_units) else None
        expression_unit_map[expression] = str(value) if isinstance(value, str) and value else None

    length_unit = context.get("length_unit")
    units: dict[str, Any] = {column: length_unit for column in COORDINATE_COLUMNS[:dimension]}
    units.update(expression_unit_map)
    units[SOLUTION_INDEX_COLUMN] = "one-based index"

    samples: list[dict[str, Any]] = []
    for solnum_index in range(solution_count):
        time_value = times[solnum_index] if times is not None else None
        for point_index, point in enumerate(points):
            row: dict[str, Any] = {}
            for axis_index, column in enumerate(COORDINATE_COLUMNS[:dimension]):
                row[column] = point[axis_index]
            row[SOLUTION_INDEX_COLUMN] = solnum_index + 1
            if time_value is not None:
                row[TIME_COLUMN] = time_value
            for expression_index, expression in enumerate(expressions):
                row[expression] = series[expression_index][solnum_index][point_index]
            samples.append(row)

    if times is not None:
        units[TIME_COLUMN] = axis["unit"]

    # The column contract: role, unit and (for expression columns) the
    # expression each key carries.  A reader resolves a column through this map
    # instead of folding case, because the requested expression ``T`` and the
    # time column ``time`` differ only in case for a case-insensitive scan.
    columns: list[dict[str, Any]] = []
    for column in COORDINATE_COLUMNS[:dimension]:
        columns.append({"name": column, "role": "coordinate", "unit": length_unit})
    columns.append({"name": SOLUTION_INDEX_COLUMN, "role": "solution_index", "unit": "one-based index"})
    if times is not None:
        columns.append({"name": TIME_COLUMN, "role": "time", "unit": axis["unit"]})
    for expression in expressions:
        columns.append({"name": expression, "role": "expression", "unit": expression_unit_map.get(expression)})
    roles: dict[str, Any] = {
        "coordinates": list(COORDINATE_COLUMNS[:dimension]),
        "solution_index": SOLUTION_INDEX_COLUMN,
        "time": TIME_COLUMN if times is not None else None,
        "expressions": {expression: expression for expression in expressions},
        "lookup": "row keys are exact: resolve a column through this map or through 'columns'",
    }
    case_only_pairs = sorted(
        (left, right) for left in roles["coordinates"] + [SOLUTION_INDEX_COLUMN, TIME_COLUMN]
        for right in expressions if left.lower() == right.lower()
    )
    if case_only_pairs:
        # Refused by _check_row_key_collisions; kept as an explicit invariant so
        # a future edit cannot publish a table whose meaning depends on key order.
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            f"the published row keys would be ambiguous under a case-insensitive lookup: {case_only_pairs}",
        )

    notes = [
        "coordinates are the requested path points, expressed in the model length unit "
        "(GeomSequence.lengthUnit()); this adapter never converts units",
        "the sample table index 'solnum' is the one-based stored-solution index of the dataset, matching "
        "COMSOL's one-based solution numbering",
    ]
    if times is not None:
        notes.append(
            f"rows are ordered by stored solution first and path point second; the time axis comes from "
            f"{axis['source']} ({axis['status']})"
        )
    else:
        notes.append(
            "no time column is published for this dataset: "
            + str(axis.get("reason") or "the dataset is not time-dependent")
        )

    return {
        "samples": samples,
        "sample_count": len(samples),
        "points": {
            "kind": request["kind"],
            "start": list(request["start"]),
            "end": list(request["end"]),
            "count": len(points),
            "dimension": dimension,
            "coordinate_unit": length_unit,
            "space_dimension": context.get("space_dimension"),
        },
        "expressions": expressions,
        "expression_units": expression_unit_map,
        "columns": columns,
        "roles": roles,
        "dataset": dataset_tag,
        "solution": request.get("solution"),
        "binding": {
            "dataset": dataset_tag,
            "solution": request.get("solution"),
            "component": context.get("component"),
            "geometry": context.get("geometry"),
            "length_unit": length_unit,
            "space_dimension": context.get("space_dimension"),
            "content_context": context.get("source"),
            "stored_solutions": solution_count,
            "time_axis": {
                "time_dependent": axis["time_dependent"],
                "status": axis["status"],
                "source": axis["source"],
                "unit": axis["unit"],
                "values": list(times) if times is not None else None,
            },
            "revision_required": False,
        },
        "unit_readback": {
            "expression_units": expression_unit_map,
            "coordinate_unit": length_unit,
            "time_unit": axis["unit"],
            "source": "model.result().numerical(<tag>).getStringArray(\"unit\") and GeomSequence.lengthUnit()",
        },
        "coordinate_readback": coordinate_readback,
        "units": units,
        "complex": bool(complex_readback) if isinstance(complex_readback, bool) else None,
        "complex_mode": "real",
        "time_values": list(times) if times is not None else None,
        "time_steps": len(times) if times is not None else None,
        "solution_axis": axis,
        "ephemeral_feature": {
            "tag": None,  # filled in by the caller
            "type_id": NUMERICAL_FEATURE_TYPE,
            "property_readback": {
                "data": data_readback,
                "expr": list(expr_readback) if isinstance(expr_readback, Sequence)
                and not isinstance(expr_readback, (str, bytes)) else None,
                "unit": declared_units or None,
                "observed_properties": list(property_names) if isinstance(property_names, Sequence)
                and not isinstance(property_names, (str, bytes)) else None,
            },
        },
        "coordinate_source": "the requested path_definition points (coord property); a getCoordinates() "
                             "readback needs the worker allow-list entry recorded in ALLOWLIST_ADDITIONS",
        "unverified_paths": [dict(item) for item in UNVERIFIED_PATHS],
        "notes": notes,
    }


def _readback_verification_status(readback_state: Any) -> str:
    """Map the coordinate readback to the published verification vocabulary.

    ``PASSED``/``FAILED``/``NOT_RUN`` are the same tokens the transaction and
    mesh validators use, so one consumer rule reads every operation's
    verification axis.  A readback that could not be attempted (the Worker does
    not publish ``getCoordinates()``) is ``NOT_RUN``: no claim is made either
    way, and it is never silently reported as a pass.
    """
    if not isinstance(readback_state, Mapping):
        return "NOT_RUN"
    status = readback_state.get("status")
    if status == "VERIFIED":
        return "PASSED"
    if status == "MISMATCH":
        return "FAILED"
    return "NOT_RUN"


def _status(payload: dict[str, Any] | None, cleanup: Mapping[str, Any],
            engine_error: Mapping[str, Any] | None, created: bool, solution_count: int | None,
            time_steps: int | None, readback_state: Mapping[str, Any] | None = None) -> dict[str, Any]:
    applied: list[str] = []
    failed: list[dict[str, Any]] = []
    not_executed: list[str] = []
    if created:
        applied.append("result.numerical.create(Interp)")
    else:
        not_executed.append("result.numerical.create(Interp)")
    if engine_error is not None:
        failed.append(dict(engine_error))
    unknown = bool(cleanup.get("cleanup_failed"))
    readback_status = (readback_state or {}).get("status")
    readback_match: bool | None = None
    if readback_status == "VERIFIED":
        readback_match = True
        applied.append("result.numerical.getCoordinates(verified)")
    elif readback_status == "MISMATCH":
        # The engine's own coordinates disagree with the requested path points:
        # the samples would belong to a different geometry path, so the
        # readback verification is recorded as failed rather than ignored.
        readback_match = False
        failed.append({
            "code": "VERIFICATION_FAILED",
            "message": str((readback_state or {}).get("reason")
                           or "the coordinate readback did not confirm the requested path points"),
        })
    if payload is not None:
        applied.extend(["result.numerical.set(coord)", "result.numerical.getData",
                        "result.numerical.remove"])
    elif created:
        not_executed.append("result.numerical.getData")
    if cleanup.get("verified_removed"):
        applied.append("result.numerical.remove(verified)")
    elif cleanup.get("created"):
        failed.append({"code": cleanup.get("error", {}).get("code") if isinstance(cleanup.get("error"), Mapping)
                       else "EXECUTION_STATE_UNKNOWN",
                       "message": "the ephemeral numerical node was not verifiably removed"})
        unknown = True

    if unknown:
        status = "EXECUTION_STATE_UNKNOWN"
    elif failed:
        status = "PARTIAL_FAILURE" if payload is not None else "FAILED"
    elif payload is None:
        status = "FAILED"
        unknown = True
    else:
        status = "APPLIED"
    return {
        "ok": status == "APPLIED",
        "status": status,
        "partial_change": bool(created) or unknown or readback_match is False,
        "execution_state_unknown": unknown,
        "applied": applied,
        "failed": failed,
        "not_executed": not_executed,
        "readback_match": readback_match,
        "readback": {
            "readable": payload is not None,
            "sample_count": payload.get("sample_count") if payload else 0,
            "stored_solutions": solution_count,
            "time_steps": time_steps,
        },
        "engine_error": dict(engine_error) if engine_error is not None else None,
    }


def sample_path(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Sample a line through a solved dataset; see the module docstring.

    Returns the operation's ``data`` dictionary (the control plane owns the
    ``{"success": ...}`` envelope).  Input and identity problems raise
    ``ExecutionContractError`` before the first engine call; a failure *after*
    the ephemeral node was created returns data carrying
    ``status.execution_state_unknown`` / ``cleanup.cleanup_failed`` instead of
    pretending nothing happened.
    """
    request = _validate_arguments(arguments)
    read_errors: list[dict[str, Any]] = []
    model = bound_model(worker, model_tag)
    results = _call(model, "result")
    dataset_node = _require_dataset(results, request["dataset"])

    dataset_solution = _string_or_none(dataset_node, "solution", read_errors)
    if request["solution"] and dataset_solution and request["solution"] != dataset_solution:
        raise ExecutionContractError(
            "INVALID_REQUEST",
            f"spec.solution {request['solution']!r} does not match dataset {request['dataset']!r}'s "
            f"solution {dataset_solution!r}",
        )
    if request["solution"] is None and dataset_solution:
        request["solution"] = dataset_solution
    solution_tag = request.get("solution")
    if solution_tag is None:
        raise ExecutionContractError(
            "INVALID_REQUEST",
            f"the dataset {request['dataset']!r} does not publish a 'solution' property and spec.solution "
            f"was not provided; the stored solution axis cannot be resolved",
        )
    context = _coordinate_context(model, dataset_node, read_errors)
    _check_point_dimension(request, context)
    _require_solution(model, solution_tag, read_errors)

    numerical_list = _call(results, "numerical")
    ephemeral_tag = _unique_tag(tag_list(numerical_list))
    cleanup: dict[str, Any] = {
        "tag": ephemeral_tag,
        "type_id": NUMERICAL_FEATURE_TYPE,
        "created": False,
        "removed": False,
        "verified_removed": False,
        "cleanup_failed": False,
        "error": None,
    }
    payload: dict[str, Any] | None = None
    engine_error: dict[str, Any] | None = None
    solution_count: int | None = None
    time_steps: int | None = None

    try:
        _call(numerical_list, "create", ephemeral_tag, NUMERICAL_FEATURE_TYPE)
        cleanup["created"] = True
        feature = _call(numerical_list, "get", ephemeral_tag)
        payload = _sample_with_feature(feature, request, dataset_node, context, model, read_errors)
        payload["ephemeral_feature"]["tag"] = ephemeral_tag
        solution_count = int(payload.get("solution_axis", {}).get("stored_solutions") or 0) or None
        time_steps = payload.get("time_steps")
    except ExecutionContractError as exc:
        engine_error = {"code": exc.code, "message": str(exc), "safe_retry": exc.safe_retry}
        if not cleanup["created"]:
            # The create call failed: reconcile whether a node appeared anyway.
            try:
                if ephemeral_tag in tag_list(numerical_list):
                    cleanup["created"] = True
                    cleanup["reconciled"] = True
            except ExecutionContractError:
                cleanup["reconciled"] = None
    finally:
        if cleanup["created"]:
            _remove_ephemeral(numerical_list, ephemeral_tag, cleanup, read_errors)

    readback_state = (payload or {}).get("coordinate_readback")
    status = _status(payload, cleanup, engine_error, cleanup["created"], solution_count, time_steps,
                     readback_state if isinstance(readback_state, Mapping) else None)
    data: dict[str, Any] = {
        "cleanup": cleanup,
        "status": status,
        # The verification axis is separate from the execution axis: the samples
        # may have been read correctly while the coordinate readback did not
        # confirm them (or could not be attempted at all).
        "verification_status": _readback_verification_status(readback_state),
        "verification": {
            "axis": "coordinate_readback",
            "status": _readback_verification_status(readback_state),
            "detail": readback_state if isinstance(readback_state, Mapping) else None,
        },
        "engine_error": engine_error,
        "read_errors": read_errors,
        "allowlist_entry_required": sorted({
            str(item["allowlist_entry_required"]) for item in read_errors
            if item.get("allowlist_entry_required")
        }),
        "allowlist_additions": list(ALLOWLIST_ADDITIONS),
        "allowlist_available_unused": list(ALLOWLIST_AVAILABLE_UNUSED),
        "unverified_paths": [dict(item) for item in UNVERIFIED_PATHS],
        "solution": solution_tag,
        "dataset": request["dataset"],
    }
    if payload is not None:
        merged = dict(payload)
        merged.pop("unverified_paths", None)
        data = {**merged, **data, "notes": payload.get("notes") or []}
    else:
        data.update({
            "samples": [],
            "sample_count": 0,
            "expressions": request["expressions"],
            "points": {
                "kind": request["kind"],
                "start": list(request["start"]),
                "end": list(request["end"]),
                "count": int(request["samples"]),
                "dimension": len(request["start"]),
                "coordinate_unit": context.get("length_unit"),
                "space_dimension": context.get("space_dimension"),
            },
            "time_values": None,
            "solution_axis": None,
            "notes": [
                "no sample table was produced; see status.engine_error and cleanup for what happened and "
                "whether an ephemeral node was left behind",
            ],
        })
    return data


# ---------------------------------------------------------------------------
# W17 Dataset operations
# ---------------------------------------------------------------------------

def _dataset_tag(path: Any) -> str:
    """Extract a dataset tag from a path argument (NodePath dict, string, or tag)."""
    if isinstance(path, str):
        if not path:
            raise ExecutionContractError("INVALID_NODE_PATH", "dataset path cannot be empty")
        return path
    if isinstance(path, Mapping):
        if "tag" in path and isinstance(path["tag"], str):
            return path["tag"]
        if "segments" in path and isinstance(path["segments"], Sequence) and path["segments"]:
            for seg in reversed(path["segments"]):
                if isinstance(seg, Mapping) and "tag" in seg:
                    coll = seg.get("collection")
                    if coll is not None and coll != "dataset":
                        continue
                    return str(seg["tag"])
    raise ExecutionContractError("INVALID_NODE_PATH", f"cannot resolve dataset tag from {path!r}")


def _dataset_container(model: Any) -> Any:
    results_node = _call(model, "result")
    return _call(results_node, "dataset")


def dataset_list(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """List all datasets in the model, along with their solutions, components and geometries."""
    model = bound_model(worker, model_tag)
    container = _dataset_container(model)
    tags = tag_list(container)
    fltr = arguments.get("filter")
    filter_dict = require_mapping(fltr, "filter") if fltr is not None else {}
    type_filter = filter_dict.get("type_id") or filter_dict.get("type")

    items: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for tag in tags:
        dset = _call(container, "get", tag)
        type_id = _node_type(dset, errors)
        sol = _string_or_none(dset, "solution", errors) or _string_or_none(dset, "data", errors)
        comp = _string_or_none(dset, "comp", errors)
        geom = _string_or_none(dset, "geom", errors)

        if type_filter and type_id != type_filter:
            continue

        items.append({
            "tag": tag,
            "type_id": type_id,
            "solution": sol,
            "component": comp,
            "geometry": geom,
            "path": {"segments": [{"accessor": "result"}, {"collection": "dataset", "tag": tag}]},
        })

    return {
        "datasets": items,
        "count": len(items),
        "tags": [item["tag"] for item in items],
        "read_errors": errors,
    }


def dataset_create(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Create a dataset node (e.g. Solution, CutPoint3D, CutLine3D, CutPlane, Join)."""
    tag = require_string(arguments.get("tag"), "tag")
    type_id = require_string(arguments.get("type_id"), "type_id")
    definition = require_mapping(arguments.get("definition", {}), "definition")

    if type_id not in SUPPORTED_DATASET_TYPES:
        raise ExecutionContractError("API_UNSUPPORTED", f"dataset type {type_id!r} is not supported")

    model = bound_model(worker, model_tag)
    container = _dataset_container(model)
    tags = tag_list(container)

    if tag in tags:
        raise ExecutionContractError("TAG_CONFLICT", f"dataset {tag!r} already exists")

    dset = _call(container, "create", tag, type_id)

    applied: list[str] = []
    failed: list[dict[str, Any]] = []
    for k, v in definition.items():
        try:
            _call(dset, "set", k, v)
            applied.append(f"set({k})")
        except Exception as exc:
            failed.append({"property": k, "error": str(exc)})

    updated_tags = tag_list(container)
    created = tag in updated_tags
    type_readback = _node_type(dset, [])

    return {
        "tag": tag,
        "type_id": type_id,
        "path": {"segments": [{"accessor": "result"}, {"collection": "dataset", "tag": tag}]},
        "created": created,
        "applied": applied,
        "failed": failed,
        "readback": {
            "tags": updated_tags,
            "type_id": type_readback,
        },
        "definition": definition,
    }


def dataset_inspect(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Inspect dataset node properties, bound solution, and nested configuration."""
    path = arguments.get("path")
    tag = _dataset_tag(path)
    model = bound_model(worker, model_tag)
    container = _dataset_container(model)
    tags = tag_list(container)
    if tag not in tags:
        raise node_not_found(f"dataset {tag!r} does not exist; existing datasets: {tags}")

    dset = _call(container, "get", tag)
    type_id = _node_type(dset, [])
    sol = _string_or_none(dset, "solution", []) or _string_or_none(dset, "data", [])
    comp = _string_or_none(dset, "comp", [])
    geom = _string_or_none(dset, "geom", [])

    props: dict[str, Any] = {}
    try:
        prop_names = _call(dset, "properties")
        if isinstance(prop_names, (list, tuple)):
            for name in prop_names[:50]:
                val = _prop_value(dset, name)
                if val is not None:
                    props[name] = val
    except Exception:
        pass

    return {
        "path": {"segments": [{"accessor": "result"}, {"collection": "dataset", "tag": tag}]},
        "tag": tag,
        "type_id": type_id,
        "solution": sol,
        "component": comp,
        "geometry": geom,
        "properties": props,
    }


def dataset_update(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Update dataset properties and bound solution."""
    path = arguments.get("path")
    tag = _dataset_tag(path)
    definition = require_mapping(arguments.get("definition", {}), "definition")

    model = bound_model(worker, model_tag)
    container = _dataset_container(model)
    tags = tag_list(container)
    if tag not in tags:
        raise node_not_found(f"dataset {tag!r} does not exist; existing datasets: {tags}")

    dset = _call(container, "get", tag)
    applied: list[str] = []
    failed: list[dict[str, Any]] = []
    readback: dict[str, Any] = {}

    for k, v in definition.items():
        try:
            _call(dset, "set", k, v)
            applied.append(f"set({k})")
            readback[k] = _prop_value(dset, k)
        except Exception as exc:
            failed.append({"property": k, "error": str(exc)})

    return {
        "path": {"segments": [{"accessor": "result"}, {"collection": "dataset", "tag": tag}]},
        "tag": tag,
        "applied": applied,
        "failed": failed,
        "not_executed": [],
        "readback": readback,
    }


def dataset_remove(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Remove a dataset node and verify removal."""
    path = arguments.get("path")
    tag = _dataset_tag(path)
    model = bound_model(worker, model_tag)
    container = _dataset_container(model)
    tags = tag_list(container)
    if tag not in tags:
        raise node_not_found(f"dataset {tag!r} does not exist; existing datasets: {tags}")

    _call(container, "remove", tag)
    remaining_tags = tag_list(container)
    verified = (tag not in remaining_tags)

    return {
        "path": {"segments": [{"accessor": "result"}, {"collection": "dataset", "tag": tag}]},
        "tag": tag,
        "removed": True,
        "verified_removed": verified,
        "remaining_datasets": remaining_tags,
    }


def dataset_solution_indices(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """List available inner/outer solution indices, time steps and parameter combinations."""
    path = arguments.get("path")
    tag = _dataset_tag(path)
    model = bound_model(worker, model_tag)
    container = _dataset_container(model)
    tags = tag_list(container)
    if tag not in tags:
        raise node_not_found(f"dataset {tag!r} does not exist; existing datasets: {tags}")

    dset = _call(container, "get", tag)
    solution_tag = _string_or_none(dset, "solution", []) or _string_or_none(dset, "data", [])

    sol_list = _call(model, "sol")
    all_sols = tag_list(sol_list)

    if not solution_tag and len(all_sols) == 1:
        solution_tag = all_sols[0]

    if not solution_tag or solution_tag not in all_sols:
        return {
            "dataset": tag,
            "solution": solution_tag,
            "binding_complete": False,
            "time_values": [],
            "inner_indices": [],
            "outer_indices": [],
            "parameters": {},
            "solution_count": 0,
            "note": "no bound solution found for dataset",
        }

    sol_node = _call(sol_list, "get", solution_tag) if hasattr(sol_list, "get") else _call(model, "sol", solution_tag)

    # §4: every metadata read below is either used or *reported*.  A failed read
    # is never converted into a value: the earlier revision fabricated
    # ``outer_indices = [1]`` and reported ``binding_complete = True`` while its
    # two SolutionInfo calls were refused by the worker allow-list and the
    # exception was swallowed, so the response claimed metadata it had never
    # read.
    read_errors: list[dict[str, Any]] = []

    def _read(node: Any, method: str, *args: Any) -> Any:
        try:
            return _call_recorded(node, method, *args, errors=read_errors)
        except ExecutionContractError:
            return None

    pvals = _read(sol_node, "getPVals")

    study_tag = _string_or_none(sol_node, "study", [])
    steps, _ = _study_steps(model, study_tag, []) if study_tag else ([], None)
    is_transient = any(step.get("type") in TIME_DEPENDENT_STUDY_STEPS for step in steps)

    time_values: list[float] = []
    if is_transient and pvals:
        time_values = [float(v) for v in pvals]
    if time_values:
        time_axis_source = "stored output times from SolverSequence.getPVals()"
    elif is_transient:
        time_axis_source = "unavailable: transient solution but getPVals() returned no values"
    else:
        time_axis_source = "steady: no time axis"

    sol_info = _read(sol_node, "getSolutioninfo")
    outer_indices: list[int] = []
    inner_indices: list[int] = []
    level_names: list[str] = []
    if sol_info is not None:
        outers = _read(sol_info, "getOuterSolnum")
        if outers:
            outer_indices = [int(x) for x in outers]
        levels = _read(sol_info, "getLevelNames")
        if levels:
            level_names = [str(x) for x in levels]
        if outer_indices:
            max_inner = _read(sol_info, "getMaxInner", outer_indices)
            if max_inner is not None:
                inner_indices = list(range(1, int(max_inner) + 1))

    axis_metadata_complete = bool(outer_indices and inner_indices)
    if not axis_metadata_complete:
        read_errors.append({
            "method": "SolutionInfo",
            "code": "SOLUTION_AXIS_METADATA_UNAVAILABLE",
            "message": "outer/inner solution axes could not be read from the engine; "
                       "no default index is invented and operations that select a "
                       "solution axis stay refused",
            "allowlist_entry_required": None,
        })

    parameters: dict[str, Any] = {}
    pnames = _read(sol_node, "getParamNames")
    param_vals = _read(sol_node, "getParamVals")
    if pnames and param_vals:
        for n, v in zip(pnames, param_vals):
            parameters[str(n)] = list(v) if isinstance(v, (list, tuple)) else v

    return {
        "dataset": tag,
        "solution": solution_tag,
        "study": study_tag,
        "binding_complete": True,
        "binding_source": "dataset property 'solution'/'data' resolved against model.sol().tags()",
        "axis_metadata_complete": axis_metadata_complete,
        "axis_metadata_source": "SolverSequence.getSolutioninfo() + SolutionInfo.getOuterSolnum()/"
                                "getMaxInner()/getLevelNames()",
        "time_values": time_values,
        "time_axis_source": time_axis_source,
        "inner_indices": inner_indices,
        "outer_indices": outer_indices,
        "level_names": level_names,
        "parameters": parameters,
        # §4: index-pairing two arrays is not proof of the full parameter
        # combination, so completeness is reported instead of asserted (the
        # combination map would need SolutionInfo.mapToSolnum()).
        "parameters_complete": False,
        "parameters_source": "SolverSequence.getParamNames()/getParamVals() (index-paired)",
        "solution_count": len(pvals) if pvals else len(inner_indices),
        "read_errors": read_errors,
    }


# ---------------------------------------------------------------------------
# Complex Math and Evaluation Transformations
# ---------------------------------------------------------------------------

def _transform_complex_value(real_val: float, imag_val: float, mode: str) -> Any:
    r = float(real_val)
    i = float(imag_val)
    if mode == "preserve":
        return {"real": r, "imag": i}
    if mode == "real":
        return r
    if mode == "imag":
        return i
    if mode == "abs":
        return math.hypot(r, i)
    if mode == "phase":
        if r == 0.0 and i == 0.0:
            return 0.0
        return math.atan2(i, r)
    raise ExecutionContractError("API_UNSUPPORTED", f"unsupported complex_mode {mode!r}")


def _transform_complex_data(real_data: Any, imag_data: Any, mode: str, is_complex: bool | None = None) -> Any:
    """Recursively transform real/imag arrays into the requested complex_mode representation."""
    if mode not in COMPLEX_MODES:
        raise ExecutionContractError("API_UNSUPPORTED", f"unsupported complex_mode {mode!r}")

    # If is_complex is True or unspecified with missing imag_data, refuse silent zero padding
    if is_complex is not False and imag_data is None and mode in ("preserve", "imag"):
        return None

    if isinstance(real_data, (int, float)):
        imag_v = float(imag_data) if isinstance(imag_data, (int, float)) else 0.0
        return _transform_complex_value(float(real_data), imag_v, mode)
    if isinstance(real_data, Sequence) and not isinstance(real_data, (str, bytes)):
        if imag_data is None:
            if is_complex is not False and mode in ("preserve", "imag"):
                return None
            return [_transform_complex_data(r, None, mode, is_complex=is_complex) for r in real_data]
        if isinstance(imag_data, Sequence) and not isinstance(imag_data, (str, bytes)) and len(imag_data) == len(real_data):
            return [_transform_complex_data(r, i, mode, is_complex=is_complex) for r, i in zip(real_data, imag_data)]
        else:
            return None
    return real_data



def _count_elements(val: Any) -> int:
    if val is None:
        return 0
    if isinstance(val, (int, float, str, bool)):
        return 1
    if isinstance(val, Mapping):
        return sum(_count_elements(v) for v in val.values())
    if isinstance(val, Sequence) and not isinstance(val, (str, bytes)):
        return sum(_count_elements(item) for item in val)
    return 1


def _export_to_artifact(
    data: Any,
    dataset_tag: str,
    *,
    eval_context: Mapping[str, Any] | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Publish a large result payload as a project-scoped artifact.

    The delivered version wrote straight into ``Path.cwd()/g2_artifacts/results``
    with ``write_text``: no containment gate, so a host request could create files
    wherever the process could write, and no atomic publish, so an interrupted
    write left a truncated file that had already been announced.  It now goes
    through the artifact store, which resolves the destination against the project
    root (refusing escapes before any file exists) and publishes via a temporary
    file plus ``os.replace``.
    """
    store = ArtifactStore(project_root=project_root)
    file_name = f"result_{dataset_tag}_{uuid.uuid4().hex[:8]}.json"
    export = store.export_field_data(
        f"g2_artifacts/results/{file_name}",
        {"values": data, **(dict(eval_context or {}))},
        "json",
    )
    return {
        "artifact_ref": export["file_path"],
        "sha256": export["sha256"],
        "byte_size": export["byte_size"],
        "total_elements": _count_elements(data),
        "storage": "artifact",
        "chunk_info": export["chunk_info"],
    }


def verify_artifact_chunks(file_path: str, chunk_size: int = 1024 * 64) -> tuple[bool, str]:
    """Verify that reading a file in chunks reproduces the full file and SHA256 (T049)."""
    p = Path(file_path)
    if not p.is_file():
        return False, f"file not found: {file_path}"
    full_bytes = p.read_bytes()
    expected_hash = hashlib.sha256(full_bytes).hexdigest()

    chunks: list[bytes] = []
    with p.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            chunks.append(chunk)

    reconstructed = b"".join(chunks)
    actual_hash = hashlib.sha256(reconstructed).hexdigest()
    return (actual_hash == expected_hash and len(reconstructed) == len(full_bytes)), actual_hash


# ---------------------------------------------------------------------------
# W17 Result Evaluation (result.evaluate & result.at_points)
# ---------------------------------------------------------------------------

def result_evaluate(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Global, point, line, surface, and volume evaluation with complex modes and statistics."""
    spec = require_mapping(arguments.get("spec", {}), "spec")
    expressions = require_string_array(spec.get("expressions"), "spec.expressions")
    if not expressions:
        raise ExecutionContractError("INVALID_REQUEST", "spec.expressions must contain at least one expression")

    solution_spec = require_mapping(spec.get("solution", {}), "spec.solution")
    dataset_tag = solution_spec.get("dataset")
    if not dataset_tag:
        raise ExecutionContractError("INVALID_REQUEST", "spec.solution.dataset is required")

    aggregate = spec.get("aggregate", "none")
    if aggregate not in AGGREGATE_MODES:
        raise ExecutionContractError("API_UNSUPPORTED", f"aggregate mode {aggregate!r} is not supported; valid: {sorted(AGGREGATE_MODES)}")

    complex_mode = spec.get("complex_mode", "preserve")
    if complex_mode not in COMPLEX_MODES:
        raise ExecutionContractError("API_UNSUPPORTED", f"complex_mode {complex_mode!r} is not supported; valid: {sorted(COMPLEX_MODES)}")

    # §4: unimplemented per-solution selections are refused explicitly, never
    # silently ignored.  ``inner`` *is* implemented (see the SolutionBinding
    # slice below), the other four are not, so they get a contract error.
    for name in ("outer", "time", "frequency", "parameters"):
        if solution_spec.get(name) is not None or spec.get(name) is not None:
            raise ExecutionContractError(
                "API_UNSUPPORTED",
                f"spec.solution.{name} selection is not implemented by this operation; "
                f"the request was refused instead of being ignored (implemented: "
                f"'inner' plus the dataset/solution tag)",
            )

    # §3: the denominator is M = ∫w dμ read from the engine.  A caller-supplied
    # constant is refused outright: it is exactly the "hardcode the denominator"
    # route the measure requirement exists to prevent.
    if spec.get("denominator_measure") is not None:
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            "spec.denominator_measure is not accepted; the denominator M = ∫w dμ is read from the "
            "engine over the same dataset/selection/solution axes as the numerator",
        )

    weight_expression = spec.get("weight_expression")
    if weight_expression is not None and not isinstance(weight_expression, str):
        raise ExecutionContractError(
            "INVALID_REQUEST", "spec.weight_expression must be a COMSOL expression string"
        )
    if isinstance(weight_expression, str) and not weight_expression.strip():
        raise ExecutionContractError("INVALID_REQUEST", "spec.weight_expression must not be empty")

    storage = spec.get("storage", "auto")

    model = bound_model(worker, model_tag)
    results = _call(model, "result")
    numerical_list = _call(results, "numerical")

    # Verify dataset exists
    dset_list = _call(results, "dataset")
    if dataset_tag not in tag_list(dset_list):
        raise node_not_found(f"dataset {dataset_tag!r} does not exist")

    dset_node = _call(dset_list, "get", dataset_tag)
    solution_tag = solution_spec.get("solution") or _string_or_none(dset_node, "solution", []) or _string_or_none(dset_node, "data", [])

    # Check spatial dimension & axisymmetry
    context = _coordinate_context(model, dset_node, [])
    is_axisymmetric = bool(context.get("axisymmetric", False))

    # Resolve MeasureSpec and feature type (F02, F03)
    try:
        from ._measure_spec import MeasureSpec
    except Exception:
        import sys
        from pathlib import Path
        repo_dir = str(Path(__file__).resolve().parent.parent) if "__file__" in globals() else "repository"
        if repo_dir not in sys.path:
            sys.path.insert(0, repo_dir)
        from comsol_mcp._measure_spec import MeasureSpec

    entity_dim = spec.get("entity_dim")
    if entity_dim is None:
        try:
            dset_type = _call(dset_node, "getType")
            if dset_type in ("CutPoint2D", "CutPoint3D"):
                entity_dim = 0
            elif dset_type in ("CutLine2D", "CutLine3D"):
                entity_dim = 1
            elif dset_type in ("CutPlane",):
                entity_dim = 2
        except Exception:
            pass

    ms = MeasureSpec(
        aggregate=aggregate,
        entity_dim=entity_dim,
        space_dim=int(context.get("space_dimension", 3)),
        is_axisymmetric=is_axisymmetric,
        selection=spec.get("selection"),
    )
    feat_type = ms.feature_type

    ephemeral_tag = _unique_tag(tag_list(numerical_list))
    cleanup: dict[str, Any] = {
        "tag": ephemeral_tag,
        "type_id": feat_type,
        "created": False,
        "removed": False,
        "cleanup_failed": False,
        "error": None,
    }

    feature = None
    engine_error = None
    transformed = None
    is_complex = False
    # §3: reported in every outcome, including the failure path, so the response
    # never depends on how far the aggregate block got before an error.
    denominator_measure = None
    cross_section_measure = None
    denominator_source = None

    try:
        feature = _call(numerical_list, "create", ephemeral_tag, feat_type)
        cleanup["created"] = True

        _call(feature, "set", "data", dataset_tag)
        _call(feature, "set", "expr", expressions)
        if spec.get("units"):
            _call(feature, "set", "unit", spec["units"])
        ms.apply_selection(feature)

        try:
            feat_props = list(_call(feature, "properties"))
            if "dataisaxisym" in feat_props:
                if _call(feature, "getString", "dataisaxisym") == "on":
                    is_axisymmetric = True
        except Exception:
            pass

        if is_axisymmetric:
            try:
                props = list(_call(feature, "properties"))
                if "intvolume" in props:
                    _call(feature, "set", "intvolume", "on")
                elif "intsurface" in props:
                    _call(feature, "set", "intsurface", "on")
            except Exception:
                pass

        _call(feature, "run")

        try:
            is_complex = bool(_call(feature, "isComplex"))
        except Exception:
            pass

        raw_real = None
        try:
            raw_real = _call(feature, "getData")
        except Exception:
            try:
                raw_real = _call(feature, "getReal")
            except Exception as exc:
                engine_error = str(exc)

        raw_imag = None
        if is_complex:
            try:
                raw_imag = _call(feature, "getImagData")
            except Exception:
                try:
                    raw_imag = _call(feature, "getImag")
                except Exception:
                    raw_imag = None
            if raw_imag is None:
                raise ExecutionContractError("COMPLEX_DATA_ERROR", "imaginary data missing for complex field")

        transformed = _transform_complex_data(raw_real, raw_imag, complex_mode, is_complex=is_complex)
        if transformed is None and raw_real is not None and complex_mode in ("preserve", "imag"):
            raise ExecutionContractError("COMPLEX_DATA_ERROR", "complex transformation failed due to missing imaginary data")

        # Denominator measure and statistical calculation (F02, F03)
        def _to_float(v: Any) -> float:
            while isinstance(v, (list, tuple)) and len(v) > 0:
                v = v[0]
            if isinstance(v, Mapping):
                v = v.get("real", 0.0)
            return float(v) if v is not None else 0.0

        if aggregate in ("average", "std", "rms") or weight_expression:
            meas_tag = f"{ephemeral_tag}_meas"
            meas_feat = None
            m_raw = None
            m_read_error: str | None = None
            # §3: the measure integrand is w for a weighted aggregate, 1 otherwise.
            measure_expr = [weight_expression] if weight_expression else ["1"]
            try:
                meas_feat = _call(numerical_list, "create", meas_tag, ms.integral_feature_type)
                _call(meas_feat, "set", "data", dataset_tag)
                _call(meas_feat, "set", "expr", measure_expr)
                ms.apply_selection(meas_feat)
                if is_axisymmetric:
                    try:
                        props = list(_call(meas_feat, "properties"))
                        if "intvolume" in props:
                            _call(meas_feat, "set", "intvolume", "on")
                        elif "intsurface" in props:
                            _call(meas_feat, "set", "intsurface", "on")
                    except Exception:
                        pass
                _call(meas_feat, "run")
                try:
                    m_raw = _call(meas_feat, "getData")
                except Exception:
                    m_raw = _call(meas_feat, "getReal")
            except Exception as exc:
                m_raw = None
                m_read_error = f"{type(exc).__name__}: {exc}"

            if is_axisymmetric and meas_feat is not None:
                try:
                    props = list(_call(meas_feat, "properties"))
                    if "intvolume" in props:
                        _call(meas_feat, "set", "intvolume", "off")
                        _call(meas_feat, "run")
                        try:
                            cs_raw = _call(meas_feat, "getData")
                        except Exception:
                            cs_raw = _call(meas_feat, "getReal")
                        cross_section_measure = _to_float(cs_raw)
                        _call(meas_feat, "set", "intvolume", "on")
                        _call(meas_feat, "run")
                except Exception:
                    pass

            m_val = _to_float(m_raw) if m_raw is not None else None
            if m_val is None or not math.isfinite(m_val) or m_val <= 0.0:
                # §3: the denominator is M = ∫w dμ read from the engine.  A failed
                # read is reported and the operation fails; it is never replaced
                # by 1.0 or by a caller-supplied constant.
                if meas_feat is not None:
                    try:
                        _call(numerical_list, "remove", meas_tag)
                    except Exception:
                        pass
                raise ExecutionContractError(
                    "ZERO_OR_INVALID_MEASURE",
                    f"the spatial measure M = ∫w dμ could not be read from the engine "
                    f"(value {m_val!r}, read error {m_read_error!r}); a hardcoded or "
                    f"caller-supplied denominator is not accepted",
                )
            denominator_measure = m_val
            denominator_source = (
                f"engine integral of w={weight_expression!r} over the selection"
                if weight_expression
                else "engine integral of 1 over the selection"
            )

            # §3: a weighted aggregate uses the numerator ∫w·f dμ over the *same*
            # dataset/selection/solution axes as the measure M = ∫w dμ.
            num_tag = f"{ephemeral_tag}_num"
            num_feat = None
            if weight_expression:
                try:
                    num_feat = _call(numerical_list, "create", num_tag, ms.integral_feature_type)
                    _call(num_feat, "set", "data", dataset_tag)
                    _call(num_feat, "set", "expr", [f"({weight_expression})*({e})" for e in expressions])
                    ms.apply_selection(num_feat)
                    _call(num_feat, "run")
                    try:
                        num_raw = _call(num_feat, "getData")
                    except Exception:
                        num_raw = _call(num_feat, "getReal")
                except Exception as exc:
                    raise ExecutionContractError(
                        "ENGINE_CALL_FAILED",
                        f"the weighted numerator ∫w·f dμ could not be evaluated: "
                        f"{type(exc).__name__}: {exc}",
                    )
                transformed = num_raw

            def _divide_by_measure(value: Any) -> Any:
                if isinstance(value, (int, float)):
                    return float(value) / denominator_measure
                if isinstance(value, list):
                    return [
                        _divide_by_measure(item) if isinstance(item, (int, float, list)) else item
                        for item in value
                    ]
                return value

            # The aggregation feature used for the variance/square integrals: the
            # weighted numerator when a weight is given, the measure feature
            # otherwise.  The measure feature's ``expr`` is restored afterwards so
            # the reported measure stays M = ∫w dμ.
            aggregate_feature = num_feat if weight_expression else meas_feat

            if aggregate == "average":
                if weight_expression:
                    transformed = _divide_by_measure(transformed)
                elif feat_type.startswith("Av"):
                    # The Av* feature already returns the mean over the selection;
                    # dividing again would divide by the measure twice.  The rule
                    # is the feature type, not a value comparison: the earlier
                    # ``m_val != _to_float(raw_real)`` heuristic silently skipped
                    # the division whenever the mean happened to equal the
                    # measure (e.g. a constant field f ≡ V).
                    pass
                else:
                    transformed = _divide_by_measure(transformed)

            elif aggregate == "std":
                base_val = _to_float(transformed)
                if feat_type.startswith("Av") and not weight_expression:
                    mean_val = base_val
                else:
                    mean_val = base_val / denominator_measure
                if aggregate_feature is None:
                    raise ExecutionContractError(
                        "ZERO_OR_INVALID_MEASURE",
                        "the variance integral ∫w(f-mean)^2 dμ needs the measure feature, which is "
                        "not available",
                    )
                if weight_expression:
                    var_exprs = [f"({weight_expression})*(({e}) - ({mean_val}))^2" for e in expressions]
                else:
                    var_exprs = [f"({e} - ({mean_val}))^2" for e in expressions]
                try:
                    _call(aggregate_feature, "set", "expr", var_exprs)
                    _call(aggregate_feature, "run")
                    try:
                        v_raw = _call(aggregate_feature, "getData")
                    except Exception:
                        v_raw = _call(aggregate_feature, "getReal")
                    var_int = _to_float(v_raw)
                except Exception as exc:
                    # §3: no silent degradation to std = 0.0.
                    raise ExecutionContractError(
                        "ENGINE_CALL_FAILED",
                        f"the variance integral ∫w(f-mean)^2 dμ could not be evaluated: "
                        f"{type(exc).__name__}: {exc}",
                    )
                var_val = var_int / denominator_measure
                if -1e-12 < var_val < 0.0:
                    var_val = 0.0  # float cancellation only
                if var_val < 0.0 or not math.isfinite(var_val):
                    raise ExecutionContractError(
                        "INVALID_RESULT",
                        f"variance {var_val!r} is not a finite non-negative number",
                    )
                transformed = math.sqrt(var_val)

            elif aggregate == "rms":
                if aggregate_feature is None:
                    raise ExecutionContractError(
                        "ZERO_OR_INVALID_MEASURE",
                        "the square integral ∫w|f|² dμ needs the measure feature, which is not available",
                    )
                if weight_expression:
                    sq_exprs = [f"({weight_expression})*(({e})^2)" for e in expressions]
                else:
                    sq_exprs = [f"({e})^2" for e in expressions]
                try:
                    _call(aggregate_feature, "set", "expr", sq_exprs)
                    _call(aggregate_feature, "run")
                    try:
                        s_raw = _call(aggregate_feature, "getData")
                    except Exception:
                        s_raw = _call(aggregate_feature, "getReal")
                    sq_int = _to_float(s_raw)
                except Exception as exc:
                    # §3: no silent degradation to rms = |mean|.
                    raise ExecutionContractError(
                        "ENGINE_CALL_FAILED",
                        f"the square integral ∫w|f|² dμ could not be evaluated: "
                        f"{type(exc).__name__}: {exc}",
                    )
                ms_val = sq_int / denominator_measure
                if -1e-12 < ms_val < 0.0:
                    ms_val = 0.0  # float cancellation only
                if ms_val < 0.0 or not math.isfinite(ms_val):
                    raise ExecutionContractError(
                        "INVALID_RESULT",
                        f"mean square {ms_val!r} is not a finite non-negative number",
                    )
                transformed = math.sqrt(ms_val)

            if meas_feat is not None:
                try:
                    _call(numerical_list, "remove", meas_tag)
                except Exception:
                    pass
            if num_feat is not None:
                try:
                    _call(numerical_list, "remove", num_tag)
                except Exception:
                    pass

        # SolutionSpec index filtering (T021 / F04)
        inner_spec = solution_spec.get("inner")
        if inner_spec is not None:
            try:
                from ._solution_binding import SolutionBinding
            except Exception:
                from comsol_mcp._solution_binding import SolutionBinding

            transformed = SolutionBinding.slice_solution_axis(
                transformed, inner_spec, num_expressions=len(expressions)
            )

    except Exception as exc:
        if isinstance(exc, ExecutionContractError):
            raise
        engine_error = str(exc)
        transformed = None
    finally:
        if cleanup["created"]:
            _remove_ephemeral(numerical_list, ephemeral_tag, cleanup, [])

    total_elements = _count_elements(transformed)
    artifact_meta = None
    if storage == "artifact" or (storage == "auto" and total_elements > AUTO_ARTIFACT_ELEMENT_LIMIT):
        artifact_meta = _export_to_artifact(
            transformed,
            dataset_tag,
            eval_context={
                "expressions": expressions,
                "dataset": dataset_tag,
                "solution": solution_tag,
                "complex_mode": complex_mode,
                "is_complex": is_complex,
            },
        )
        artifact_meta["auto_artifact_element_limit"] = AUTO_ARTIFACT_ELEMENT_LIMIT
        artifact_meta["requested_storage"] = storage
        result_payload = {
            "artifact": artifact_meta,
            "storage": "artifact",
        }
    else:
        result_payload = {
            "values": transformed,
            "storage": "inline",
        }

    return {
        **result_payload,
        "expressions": expressions,
        "dataset": dataset_tag,
        "solution": solution_tag,
        "aggregate": aggregate,
        "complex_mode": complex_mode,
        "is_complex": is_complex,
        "axisymmetric": is_axisymmetric,
        "axisymmetric_factor_applied": is_axisymmetric and aggregate in ("integral", "average", "std", "rms"),
        "axisymmetric_applied_count": 1 if (is_axisymmetric and aggregate in ("integral", "average", "std", "rms")) else 0,
        "revolved_measure": denominator_measure if is_axisymmetric else None,
        "cross_section_measure": cross_section_measure,
        "denominator_measure": denominator_measure if aggregate in ("average", "std", "rms") else None,
        "denominator_source": denominator_source,
        "total_elements": total_elements,
        "cleanup": cleanup,
        "status": {
            "ok": engine_error is None and not cleanup["cleanup_failed"],
            "engine_error": engine_error,
            "cleanup_failed": cleanup["cleanup_failed"],
            "execution_state_unknown": cleanup["cleanup_failed"],
        },
    }



def result_at_points(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate expressions at explicit spatial points with coordinate readback."""
    spec = require_mapping(arguments.get("spec", {}), "spec")
    points = arguments.get("points")
    if not isinstance(points, Sequence) or not points:
        raise ExecutionContractError("INVALID_REQUEST", "points must be a non-empty sequence")
    coordinate_unit = arguments.get("coordinate_unit") or "m"
    frame = arguments.get("frame") or "spatial"

    expressions = require_string_array(spec.get("expressions"), "spec.expressions")
    solution_spec = require_mapping(spec.get("solution", {}), "spec.solution")
    dataset_tag = solution_spec.get("dataset")
    if not dataset_tag:
        raise ExecutionContractError("INVALID_REQUEST", "spec.solution.dataset is required")

    complex_mode = spec.get("complex_mode", "real")

    dim = len(points[0]) if isinstance(points[0], Sequence) else len(points[0].keys())
    point_count = len(points)
    coord_matrix: list[list[float]] = [[] for _ in range(dim)]
    for idx, pt in enumerate(points):
        pt_dim = len(pt) if isinstance(pt, Sequence) else len(pt.keys())
        if pt_dim != dim:
            raise ExecutionContractError(
                "COORDINATE_ERROR",
                f"Point index {idx} has dimension {pt_dim}, expected {dim}",
            )
        if isinstance(pt, Sequence):
            for d in range(dim):
                v = float(pt[d])
                if not math.isfinite(v):
                    raise ExecutionContractError("INVALID_REQUEST", f"point coordinate must be finite, got {v}")
                coord_matrix[d].append(v)
        elif isinstance(pt, Mapping):
            for d, k in enumerate(("x", "y", "z")[:dim]):
                v = float(pt[k])
                if not math.isfinite(v):
                    raise ExecutionContractError("INVALID_REQUEST", f"point coordinate must be finite, got {v}")
                coord_matrix[d].append(v)

    model = bound_model(worker, model_tag)
    results = _call(model, "result")
    numerical_list = _call(results, "numerical")
    dataset_list = _call(results, "dataset")

    if dataset_tag in tag_list(dataset_list):
        dset_node = _call(dataset_list, "get", dataset_tag)
        errs: list[dict[str, Any]] = []
        ctx = _coordinate_context(model, dset_node, errs)
        sdim = ctx.get("space_dimension")
        if sdim is not None and dim != sdim:
            raise ExecutionContractError(
                "DIMENSION_MISMATCH",
                f"Point space dimension {dim} does not match model space dimension {sdim}",
            )

    ephemeral_tag = _unique_tag(tag_list(numerical_list))
    cleanup: dict[str, Any] = {
        "tag": ephemeral_tag,
        "type_id": "Interp",
        "created": False,
        "removed": False,
        "cleanup_failed": False,
        "error": None,
    }

    feature = None
    engine_error = None
    transformed = None
    readback_status = "UNAVAILABLE"
    readback_coords = None

    try:
        scale = 1.0
        if coordinate_unit == "mm":
            scale = 0.001
        elif coordinate_unit == "cm":
            scale = 0.01
        elif coordinate_unit == "um":
            scale = 1e-6
        elif coordinate_unit != "m":
            raise ExecutionContractError("API_UNSUPPORTED", f"coordinate_unit {coordinate_unit!r} not supported")

        if frame != "spatial":
            raise ExecutionContractError("API_UNSUPPORTED", f"coordinate frame {frame!r} not supported; only 'spatial' supported")

        scaled_coord_matrix = [[x * scale for x in row] for row in coord_matrix]

        feature = _call(numerical_list, "create", ephemeral_tag, "Interp")
        cleanup["created"] = True

        _call(feature, "set", "data", dataset_tag)
        _call(feature, "set", "expr", expressions)
        _call(feature, "setInterpolationCoordinates", scaled_coord_matrix)
        _call(feature, "run")

        is_complex = False
        try:
            is_complex = bool(_call(feature, "isComplex"))
        except Exception:
            pass

        raw_real = _call(feature, "getData")
        raw_imag = None
        if is_complex:
            try:
                raw_imag = _call(feature, "getImagData")
            except Exception:
                try:
                    raw_imag = _call(feature, "getImag")
                except Exception:
                    raw_imag = None

        transformed = _transform_complex_data(raw_real, raw_imag, complex_mode)

        try:
            readback_coords = _call(feature, "getCoordinates")
            if readback_coords is not None:
                if isinstance(readback_coords, Sequence) and len(readback_coords) == len(scaled_coord_matrix):
                    match = True
                    for r_row, s_row in zip(readback_coords, scaled_coord_matrix):
                        if not isinstance(r_row, Sequence) or len(r_row) != len(s_row):
                            match = False
                            break
                        if any(not math.isclose(float(r), float(s), rel_tol=1e-4, abs_tol=1e-5) for r, s in zip(r_row, s_row)):
                            match = False
                            break
                    readback_status = "VERIFIED" if match else "MISMATCH"
                else:
                    readback_status = "MISMATCH"
        except Exception:
            readback_status = "UNAVAILABLE"


    except Exception as exc:
        if isinstance(exc, ExecutionContractError):
            raise
        engine_error = str(exc)
        transformed = None
    finally:
        if cleanup["created"]:
            _remove_ephemeral(numerical_list, ephemeral_tag, cleanup, [])

    return {
        "values": transformed,
        "points": points,
        "point_count": point_count,
        "expressions": expressions,
        "coordinate_unit": coordinate_unit,
        "frame": frame,
        "coordinate_readback": {
            "status": readback_status,
            "coordinates": readback_coords,
        },
        "complex_mode": complex_mode,
        "cleanup": cleanup,
        "status": {
            "ok": engine_error is None and not cleanup["cleanup_failed"],
            "engine_error": engine_error,
            "cleanup_failed": cleanup["cleanup_failed"],
        },
    }


# ---------------------------------------------------------------------------
# W17 Probe / Derived Values & Table Management
# ---------------------------------------------------------------------------

def result_numerical_manage(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Manage user-visible Numerical and Probe features."""
    action = require_string(arguments.get("action"), "action")
    model = bound_model(worker, model_tag)
    results = _call(model, "result")
    numerical_list = _call(results, "numerical")
    tags = tag_list(numerical_list)

    if action == "list":
        features: list[dict[str, Any]] = []
        for t in tags:
            node = _call(numerical_list, "get", t)
            type_id = _node_type(node, [])
            expr = _string_or_none(node, "expr", [])
            dset = _string_or_none(node, "data", [])
            features.append({
                "tag": t,
                "type_id": type_id,
                "expr": expr,
                "dataset": dset,
            })
        return {"action": "list", "features": features, "count": len(features), "tags": tags}

    path = arguments.get("path")
    definition = arguments.get("definition") or {}

    if action == "create":
        tag = None
        if path:
            tag = _dataset_tag(path)
        if not tag:
            tag = definition.get("tag") or _unique_tag(tags)
        type_id = definition.get("type_id", "EvalGlobal")
        if tag in tags:
            raise ExecutionContractError("TAG_CONFLICT", f"numerical feature {tag!r} already exists")
        node = _call(numerical_list, "create", tag, type_id)
        applied: list[str] = []
        for k, v in definition.items():
            if k in ("tag", "type_id"):
                continue
            _call(node, "set", k, v)
            applied.append(f"set({k})")
        return {
            "action": "create",
            "tag": tag,
            "type_id": type_id,
            "created": True,
            "applied": applied,
        }

    tag = _dataset_tag(path)
    if tag not in tags:
        raise node_not_found(f"numerical feature {tag!r} not found; existing: {tags}")
    node = _call(numerical_list, "get", tag)

    if action in ("get", "inspect"):
        type_id = _node_type(node, [])
        return {
            "action": action,
            "tag": tag,
            "type_id": type_id,
        }
    elif action in ("update", "set"):
        applied = []
        for k, v in definition.items():
            _call(node, "set", k, v)
            applied.append(f"set({k})")
        return {"action": action, "tag": tag, "applied": applied}
    elif action in ("run", "evaluate"):
        _call(node, "run")
        data = _call(node, "getData")
        return {"action": action, "tag": tag, "data": data}
    elif action == "remove":
        _call(numerical_list, "remove", tag)
        return {"action": "remove", "tag": tag, "removed": True, "verified_removed": tag not in tag_list(numerical_list)}
    else:
        raise ExecutionContractError("INVALID_REQUEST", f"unknown numerical_manage action: {action!r}")


def result_table_manage(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Manage user-visible Table features."""
    action = require_string(arguments.get("action"), "action")
    model = bound_model(worker, model_tag)
    results = _call(model, "result")
    table_list = _call(results, "table")
    tags = tag_list(table_list)

    if action == "list":
        tables: list[dict[str, Any]] = []
        for t in tags:
            node = _call(table_list, "get", t)
            headers = None
            try:
                headers = _call(node, "getColumnHeaders")
            except Exception:
                pass
            tables.append({"tag": t, "headers": headers})
        return {"action": "list", "tables": tables, "count": len(tables), "tags": tags}

    path = arguments.get("path")
    definition = arguments.get("definition") or {}

    if action == "create":
        tag = None
        if path:
            tag = _dataset_tag(path)
        if not tag:
            tag = definition.get("tag") or _unique_tag(tags)
        type_id = definition.get("type_id", "Table")
        if tag in tags:
            raise ExecutionContractError("TAG_CONFLICT", f"table {tag!r} already exists")
        node = _call(table_list, "create", tag, type_id)
        if "data" in definition:
            data = definition["data"]
            if isinstance(data, list):
                try:
                    _call(node, "setTableData", data, None)
                except Exception:
                    try:
                        imag = [[0.0 for _ in row] for row in data] if data and isinstance(data[0], list) else None
                        _call(node, "setTableData", data, imag)
                    except Exception:
                        _call(node, "setTableData", data)
        return {"action": "create", "tag": tag, "type_id": type_id, "created": True}

    tag = _dataset_tag(path)
    if tag not in tags:
        raise node_not_found(f"table {tag!r} not found; existing: {tags}")
    node = _call(table_list, "get", tag)

    if action in ("get", "inspect"):
        headers = None
        try:
            headers = _call(node, "getColumnHeaders")
        except Exception:
            pass
        data = None
        try:
            data = _call(node, "getReal")
        except Exception:
            try:
                data = _call(node, "getTableData", True)
            except Exception:
                try:
                    data = _call(node, "getTableData")
                except Exception:
                    pass
        return {"action": action, "tag": tag, "headers": headers, "data": data}
    elif action == "set":
        data = definition.get("data")
        if data is not None and isinstance(data, list):
            try:
                _call(node, "setTableData", data, None)
            except Exception:
                try:
                    imag = [[0.0 for _ in row] for row in data] if data and isinstance(data[0], list) else None
                    _call(node, "setTableData", data, imag)
                except Exception:
                    _call(node, "setTableData", data)
        return {"action": "set", "tag": tag, "applied": True}
    elif action == "clear":
        try:
            _call(node, "clearTableData", True)
        except Exception:
            _call(node, "clearTableData")
        return {"action": "clear", "tag": tag, "cleared": True}
    elif action == "remove":
        _call(table_list, "remove", tag)
        return {"action": "remove", "tag": tag, "removed": True, "verified_removed": tag not in tag_list(table_list)}
    else:
        raise ExecutionContractError("INVALID_REQUEST", f"unknown table_manage action: {action!r}")


# ---------------------------------------------------------------------------
# W17 Field Export (result.field_export)
# ---------------------------------------------------------------------------

def result_field_export(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Export field data / solutions to local files with chunk pagination (T049)."""
    spec = require_mapping(arguments.get("spec", {}), "spec")
    fmt = require_string(arguments.get("format", "json"), "format").lower()
    dest = require_string(arguments.get("destination"), "destination")

    export_spec = dict(spec)
    export_spec["storage"] = "inline"
    eval_res = result_evaluate(worker, model_tag, {"spec": export_spec})

    status = eval_res.get("status", {})
    if not status.get("ok", True) or status.get("engine_error") or status.get("cleanup_failed"):
        raise ExecutionContractError(
            "EXPORT_FAILED",
            f"Cannot export field data because evaluation failed: {status}",
        )

    try:
        from ._artifact_store import ArtifactStore
    except Exception:
        from comsol_mcp._artifact_store import ArtifactStore

    store = ArtifactStore()
    return store.export_field_data(dest, eval_res, fmt=fmt)



OPERATIONS: dict[str, Any] = {
    "dataset.list": dataset_list,
    "dataset.create": dataset_create,
    "dataset.inspect": dataset_inspect,
    "dataset.update": dataset_update,
    "dataset.remove": dataset_remove,
    "dataset.solution_indices": dataset_solution_indices,
    "result.evaluate": result_evaluate,
    "result.at_points": result_at_points,
    "result.sample_path": sample_path,
    "result.numerical_manage": result_numerical_manage,
    "result.table_manage": result_table_manage,
    "result.field_export": result_field_export,
}

#: Operation id -> the argument names this layer accepts (envelope fields such as
#: ``model_ref``/``session_id`` pass through ``_g3_common.ENVELOPE_FIELDS``).
OPERATION_ARGUMENTS: dict[str, tuple[str, ...]] = {
    "dataset.list": ("filter",),
    "dataset.create": ("tag", "type_id", "definition"),
    "dataset.inspect": ("path",),
    "dataset.update": ("path", "definition"),
    "dataset.remove": ("path",),
    "dataset.solution_indices": ("path",),
    "result.evaluate": ("spec",),
    "result.at_points": ("spec", "points", "coordinate_unit", "frame"),
    "result.sample_path": ("spec", "path_definition"),
    "result.numerical_manage": ("action", "path", "definition"),
    "result.table_manage": ("action", "path", "definition"),
    "result.field_export": ("spec", "format", "destination"),
}

#: Operation id -> arguments that must be present for the call to be meaningful.
OPERATION_REQUIRED: dict[str, tuple[str, ...]] = {
    "dataset.list": (),
    "dataset.create": ("tag", "type_id", "definition"),
    "dataset.inspect": ("path",),
    "dataset.update": ("path", "definition"),
    "dataset.remove": ("path",),
    "dataset.solution_indices": ("path",),
    "result.evaluate": ("spec",),
    "result.at_points": ("spec", "points", "coordinate_unit", "frame"),
    "result.sample_path": ("spec", "path_definition"),
    "result.numerical_manage": ("action",),
    "result.table_manage": ("action",),
    "result.field_export": ("spec", "format", "destination"),
}

__all__ = [
    "ACCEPTED_SPEC_KEYS",
    "ACCEPTED_SOLUTION_SPEC_KEYS",
    "AGGREGATE_MODES",
    "ALLOWLIST_ADDITIONS",
    "ALLOWLIST_AVAILABLE_UNUSED",
    "COMPLEX_MODES",
    "EPHEMERAL_TAG_STEM",
    "MAX_EXPRESSIONS",
    "MAX_SAMPLE_POINTS",
    "MIN_SAMPLE_POINTS",
    "NUMERICAL_FEATURE_TYPE",
    "OPERATIONS",
    "OPERATION_ARGUMENTS",
    "OPERATION_ID",
    "OPERATION_REQUIRED",
    "PATH_KINDS",
    "REFUSED_SPEC_KEYS",
    "SUPPORTED_DATASET_TYPES",
    "SUPPORTED_NUMERICAL_TYPES",
    "TIME_DEPENDENT_STUDY_STEPS",
    "UNVERIFIED_PATHS",
    "dataset_create",
    "dataset_inspect",
    "dataset_list",
    "dataset_remove",
    "dataset_solution_indices",
    "dataset_update",
    "result_at_points",
    "result_evaluate",
    "result_field_export",
    "result_numerical_manage",
    "result_table_manage",
    "sample_path",
    "verify_artifact_chunks",
]
