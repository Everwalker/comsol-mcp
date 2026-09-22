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

from ._execution_contract import PreWriteRefusal
from ._g2_contract import ExecutionContractError, NodePath
from ._g2_engine import _call
from ._artifact_store import ArtifactStore, trusted_project_root
from ._result_budget import (
    ResultBudgetRefused,
    guarded_getdata,
    plan_result_budget,
)
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
    require_bool,
    require_int,
    require_mapping,
    require_number,
    require_string,
    require_string_array,
    resolve_path,
    tag_list,
)
from ._probe_manage import (
    _completion as _probe_completion,
    _read_property as _probe_read_property,
    _unwrap_property_value as _probe_unwrap_property_value,
    _write_properties as _probe_write_properties,
)

# ---------------------------------------------------------------------------
# vocabulary, limits and provenance
# ---------------------------------------------------------------------------

# These are limits on the numeric scalar payload admitted by the client-side
# result adapter.  They are deliberately not presented as COMSOL/Worker peak
# memory limits: JSON, Python object overhead, transport buffers, and the
# engine's internal cache are outside this estimate and remain unmeasured.
RESULT_NUMERIC_PAYLOAD_MAX_ELEMENTS = 1_000_000
RESULT_NUMERIC_PAYLOAD_MAX_BYTES = 64 * 1024 * 1024

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


_DERIVED_DATASET_TYPES = frozenset({
    "CutPoint1D", "CutPoint2D", "CutPoint3D",
    "CutLine1D", "CutLine2D", "CutLine3D", "CutPlane", "Join",
})


def _resolve_dataset_binding(
    model: Any,
    dataset_tag: str,
    *,
    requested_solution: str | None = None,
) -> dict[str, Any] | None:
    """Read the shared dataset graph before using solution/geometry metadata."""
    try:
        from ._dataset_binding import resolve_dataset_binding
        binding = resolve_dataset_binding(model, dataset_tag, requested_solution=requested_solution)
    except Exception as exc:
        # Every dataset type requires a resolved binding. A derived dataset's
        # ``data`` property may name an upstream dataset, not a solution;
        # neither that string nor requested solution membership proves binding.
        return {
            "dataset": dataset_tag,
            "dataset_type": None,
            "binding_complete": False,
            "read_errors": [{"code": "DATASET_BINDING_UNAVAILABLE", "message": f"{type(exc).__name__}: {exc}"}],
            "error": {"code": "DATASET_BINDING_UNAVAILABLE", "message": f"{type(exc).__name__}: {exc}"},
        }
    if not isinstance(binding, Mapping):
        return {
            "dataset": dataset_tag,
            "dataset_type": None,
            "binding_complete": False,
            "read_errors": [{"code": "DATASET_BINDING_UNAVAILABLE", "message": "dataset resolver returned a non-mapping result"}],
            "error": {"code": "DATASET_BINDING_UNAVAILABLE", "message": "dataset resolver returned a non-mapping result"},
        }
    return dict(binding)


def _coordinate_context(
    model: Any,
    dataset_node: Any,
    errors: list[dict[str, Any]],
    *,
    dataset_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve the dataset's geometry, its length unit and its space dimension."""
    context: dict[str, Any] = {"component": None, "geometry": None, "length_unit": None,
                               "space_dimension": None, "source": None}
    binding_type = dataset_binding.get("dataset_type") if isinstance(dataset_binding, Mapping) else None
    if binding_type is None:
        try:
            binding_type = _call(dataset_node, "getType")
        except Exception:
            binding_type = None
    if isinstance(dataset_binding, Mapping) and dataset_binding.get("binding_complete") is not True:
        # The resolver is authoritative for every dataset type.  Falling back
        # to a direct ``comp``/``geom`` read after it reports a failed Solution
        # binding can evaluate the wrong component or geometry while claiming
        # success.  Derived datasets were already protected here; the same
        # fail-closed rule applies to base Solution datasets and resolver
        # failures with an unknown type.
        detail = dataset_binding.get("error") or (
            dataset_binding.get("read_errors")
            or [{"code": "DATASET_BINDING_INCOMPLETE", "message": "dataset binding is incomplete"}]
        )[0]
        detail_message = detail.get("message", detail) if isinstance(detail, Mapping) else str(detail)
        binding_errors = dataset_binding.get("read_errors") or []
        if (
            isinstance(detail, Mapping) and detail.get("code") == "SOLUTION_NOT_FOUND"
        ) or any(
            isinstance(item, Mapping) and item.get("code") == "SOLUTION_NOT_FOUND"
            for item in binding_errors
        ):
            missing = next(
                (item for item in binding_errors if isinstance(item, Mapping) and item.get("code") == "SOLUTION_NOT_FOUND"),
                detail if isinstance(detail, Mapping) else None,
            )
            raise node_not_found(
                missing.get("message", detail_message) if isinstance(missing, Mapping) else detail_message
            )
        raise ExecutionContractError(
            "DATASET_BINDING_INCOMPLETE",
            f"dataset binding is incomplete for type {binding_type!r}: {detail_message}",
            details={"dataset_binding": dict(dataset_binding)},
        )
    component = None
    geometry = None
    if isinstance(dataset_binding, Mapping) and dataset_binding.get("binding_complete") is True:
        component = dataset_binding.get("component")
        geometry = dataset_binding.get("geometry")
        if not component or not geometry:
            raise ExecutionContractError(
                "DATASET_BINDING_INCOMPLETE",
                "complete dataset binding did not publish component and geometry",
                details={"dataset_binding": dict(dataset_binding)},
            )
    elif dataset_binding is None:
        # Callers that do not have a resolver witness cannot safely infer the
        # spatial owner from direct properties.  Keep this explicit so a
        # future adapter cannot accidentally reintroduce the old fallback.
        raise ExecutionContractError(
            "DATASET_BINDING_UNAVAILABLE",
            "dataset binding resolver did not return a read-only binding witness",
        )
    if component and geometry:
        binding_sources = dataset_binding.get("sources", {}) if isinstance(dataset_binding, Mapping) else {}
        if (
            isinstance(binding_sources, Mapping)
            and isinstance(binding_sources.get("component"), str)
            and "dataset.comp" in binding_sources.get("component", "")
            and isinstance(binding_sources.get("geometry"), str)
            and "dataset.geom" in binding_sources.get("geometry", "")
        ):
            context_source = "dataset.comp/dataset.geom"
        elif (
            isinstance(binding_sources, Mapping)
            and isinstance(binding_sources.get("component"), str)
            and "unique" in binding_sources.get("component", "")
            and isinstance(binding_sources.get("geometry"), str)
            and "unique" in binding_sources.get("geometry", "")
        ):
            context_source = "single component/geometry of the model"
        else:
            context_source = "resolved dataset binding graph"
        context.update(
            component=component,
            geometry=geometry,
            source=context_source,
        )
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
        # COMSOL 6.4 exposes the geometry truth through GeomSequence's
        # isAxisymmetric().  Coordinate names and arbitrary boolean
        # properties are not equivalent metadata: treating a failed read as
        # ``False`` would silently omit the 2*pi*r measure factor.  Refuse the
        # evaluation until the real accessor is available and returns a bool.
        is_axi_getter = getattr(geometry_node, "isAxisymmetric", None)
        if not callable(is_axi_getter):
            raise ExecutionContractError(
                "AXISYMMETRY_STATUS_UNAVAILABLE",
                "geometry.isAxisymmetric() is required to determine the spatial measure",
            )
        try:
            is_axi = is_axi_getter()
        except Exception as exc:
            raise ExecutionContractError(
                "AXISYMMETRY_STATUS_UNAVAILABLE",
                f"geometry.isAxisymmetric() failed: {type(exc).__name__}: {str(exc)[:500]}",
            ) from exc
        if not isinstance(is_axi, bool):
            raise ExecutionContractError(
                "AXISYMMETRY_STATUS_UNAVAILABLE",
                "geometry.isAxisymmetric() did not return a boolean",
            )
        context["axisymmetric"] = is_axi
    if isinstance(dataset_binding, Mapping):
        context["dataset_binding"] = dict(dataset_binding)
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
        # The declared output times are optional for this adapter (the stored
        # values are preferred) and a ``tlist`` written as a range expression is
        # legitimately not a double array, so its refusal is reported with the
        # step instead of being mixed into the caller's hard failures.
        declared_probe: list[dict[str, Any]] = []
        declared = _record(step, "getDoubleArray", "tlist", errors=declared_probe)
        steps.append({
            "tag": step_tag,
            "type": str(step_type) if isinstance(step_type, str) else None,
            "declared_output_times": list(declared) if isinstance(declared, Sequence)
            and not isinstance(declared, (str, bytes)) else None,
            "declared_output_times_error": declared_probe[0] if declared_probe else None,
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

    dataset_binding = _resolve_dataset_binding(
        model,
        str(request["dataset"]),
        requested_solution=request.get("solution") if isinstance(request.get("solution"), str) else None,
    )
    dataset_solution = (
        dataset_binding.get("solution")
        if isinstance(dataset_binding, Mapping) and dataset_binding.get("solution")
        else _string_or_none(dataset_node, "solution", read_errors)
    )
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
    context = _coordinate_context(model, dataset_node, read_errors, dataset_binding=dataset_binding)
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


def _dataset_reference_value(node: Any, property_name: str) -> str | None:
    """Read one dataset reference without treating an unreadable property as a tag."""
    try:
        value = _call(node, "getString", property_name)
    except Exception:
        return None
    return str(value) if isinstance(value, str) and value.strip() else None


def _validate_dataset_graph(model: Any, *, candidate_tag: str,
                            candidate_properties: Mapping[str, Any],
                            existing_node: Any | None = None,
                            candidate_type: str | None = None) -> None:
    """Validate dataset references before the first create/set mutation.

    ``data`` and Join's ``data2`` are directed upstream edges.  A candidate
    update is evaluated together with the engine's current dataset tags, so a
    missing reference or a cycle is rejected before COMSOL sees a setter.
    """
    container = _dataset_container(model)
    tags = tag_list(container)
    tag_set = set(tags)
    edges: dict[str, list[str]] = {}
    for tag in tags:
        node = _call(container, "get", tag)
        refs: list[str] = []
        for prop in ("data", "data2"):
            value = _dataset_reference_value(node, prop)
            if value is not None:
                refs.append(value)
        edges[tag] = refs
    candidate_refs: list[str] = []
    for prop in ("data", "data2"):
        raw = candidate_properties.get(prop)
        if isinstance(raw, str) and raw.strip():
            candidate_refs.append(raw.strip())
    if existing_node is not None:
        for prop in ("data", "data2"):
            if prop not in candidate_properties:
                value = _dataset_reference_value(existing_node, prop)
                if value is not None:
                    candidate_refs.append(value)
    for ref in candidate_refs:
        # A Solution dataset may expose its solver sequence through ``data``;
        # that is not a dataset edge.  All derived dataset types must reference
        # an actual dataset tag (or the candidate itself, which is rejected by
        # the cycle walk below).
        if ref not in tag_set and ref != candidate_tag and candidate_type != "Solution":
            raise node_not_found(f"dataset reference {ref!r} does not exist; existing datasets: {tags}")
    edges[candidate_tag] = candidate_refs

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(tag: str, chain: list[str]) -> None:
        if tag in visiting:
            start = chain.index(tag) if tag in chain else 0
            cycle = chain[start:] + [tag]
            raise PreWriteRefusal(
                "DATASET_CYCLE_DETECTED",
                f"dataset reference cycle detected: {' -> '.join(cycle)}",
                details={"cycle": cycle},
            )
        if tag in visited:
            return
        visiting.add(tag)
        for ref in edges.get(tag, []):
            if ref in edges:
                visit(ref, chain + [ref])
        visiting.remove(tag)
        visited.add(tag)

    for tag in edges:
        visit(tag, [tag])


def _validate_dataset_component_refs(model: Any, properties: Mapping[str, Any],
                                     *, existing_node: Any | None = None) -> None:
    """Reject a missing component/geometry binding before a dataset mutation."""
    component = properties.get("comp")
    geometry = properties.get("geom")
    if component is None and existing_node is not None:
        component = _dataset_reference_value(existing_node, "comp")
    if geometry is None and existing_node is not None:
        geometry = _dataset_reference_value(existing_node, "geom")
    if component is None:
        return
    if not isinstance(component, str) or not component.strip():
        raise PreWriteRefusal("INVALID_REQUEST", "dataset comp must be a non-empty component tag")
    component_tag = component.strip()
    # Read the native component tag list before resolving the requested node.
    # Calling model.component(missing_tag) first makes a pure validation error
    # look like an unknown engine state in the managed worker.
    component_container = _call(model, "component")
    component_tags = tag_list(component_container)
    if component_tag not in component_tags:
        raise node_not_found(
            f"component {component_tag!r} does not exist; existing components: {component_tags}",
            details={"component": component_tag, "existing_components": component_tags},
        )
    component_node = _call(model, "component", component_tag)
    if geometry is not None:
        if not isinstance(geometry, str) or not geometry.strip():
            raise PreWriteRefusal("INVALID_REQUEST", "dataset geom must be a non-empty geometry tag")
        geometry_tag = geometry.strip()
        geometry_container = _call(component_node, "geom")
        geometry_tags = tag_list(geometry_container)
        if geometry_tag not in geometry_tags:
            raise node_not_found(
                f"geometry {geometry_tag!r} does not exist in component {component_tag!r}; "
                f"existing geometries: {geometry_tags}",
                details={"component": component_tag, "geometry": geometry_tag,
                         "existing_geometries": geometry_tags},
            )
        _call(component_node, "geom", geometry_tag)


# The W17 catalogue declares dataset edit addresses as NodePath.  Keep the
# resolver local to this module so a path ending in geometry/physics/feature is
# never reduced to its last tag by accident.  Bare strings remain accepted by
# ``_dataset_tag`` for the older result-evaluation helpers, but CRUD calls use
# this typed resolver exclusively.
_DATASET_PROPERTY_NAMES = frozenset({
    "solution", "data", "comp", "geom", "pointx", "pointy", "pointz",
    "x", "y", "z", "coord", "coords", "coordinates", "point", "expr",
    "unit", "t", "tmin", "tmax", "numelem", "resolution", "method", "data2",
    "solutions", "solutions2", "genmethod", "genpnpoint", "genpnvec", "planetype",
    "quickplane", "quickx", "quicky", "quickz", "bounded", "pddir", "pdpoint",
    "bndsnap", "snapping", "linevar", "normal", "tangent", "spacevars",
    "selection", "genpoints", "genparaactive", "genparadist", "axis", "r", "phi", "plane",
    "gridx", "gridy", "gridz", "filename", "localzphys", "localzrel", "locdef", "pointvar",
    "regulargridx", "regulargridy", "regulargridz",
    "posx", "posy", "posz", "xmin", "xmax", "ymin", "ymax", "zmin", "zmax",
    "dataset", "join", "par1", "par2", "parameter", "filter", "frame",
})

# Dataset property names are not interchangeable across native dataset types.
# In COMSOL 6.4 a CutPlane inherits its component/geometry binding through the
# upstream ``data`` dataset; ``comp`` and ``geom`` are not CutPlane setters.
# Keep this narrow type guard beside the general property allow-list so a
# caller cannot partially create a plane and discover the invalid fields only
# after the native setters have already run.
_DATASET_TYPE_FORBIDDEN_PROPERTIES: dict[str, frozenset[str]] = {
    "CutPlane": frozenset({"comp", "geom"}),
}


def _validate_dataset_type_properties(type_id: str, properties: Mapping[str, Any]) -> None:
    forbidden = sorted(set(properties) & _DATASET_TYPE_FORBIDDEN_PROPERTIES.get(type_id, frozenset()))
    if forbidden:
        raise PreWriteRefusal(
            "INVALID_REQUEST",
            f"dataset type {type_id!r} does not expose native properties {forbidden}; "
            "bind the dataset through its upstream data reference",
            details={"type_id": type_id, "unsupported_properties": forbidden},
        )


def _dataset_path_target(worker: Any, model_tag: str, path: Any) -> tuple[dict[str, Any], str, Any, Any]:
    """Resolve exactly ``model.result().dataset(<tag>)`` through NodePath."""
    if not isinstance(path, Mapping):
        raise PreWriteRefusal("INVALID_NODE_PATH", "dataset CRUD requires a NodePath object")
    parsed = NodePath.from_wire(path, allow_empty=False)
    if not parsed.segments or parsed.segments[0].accessor != "result":
        raise PreWriteRefusal("INVALID_NODE_PATH", "dataset path must begin with the result accessor")
    if any(segment.accessor is not None for segment in parsed.segments[1:]):
        raise PreWriteRefusal("INVALID_NODE_PATH", "dataset path has an unsupported accessor after result")
    final = parsed.segments[-1]
    if final.collection != "dataset" or final.tag is None:
        raise PreWriteRefusal("INVALID_NODE_PATH", "dataset path must end with collection='dataset' and a tag")
    canonical = parsed.as_dict()
    _canonical_from_engine, node = resolve_path(worker, model_tag, canonical, label="path")
    model = bound_model(worker, model_tag)
    container = _dataset_container(model)
    tags = tag_list(container)
    tag = str(final.tag)
    if tag not in tags:
        raise node_not_found(f"dataset {tag!r} does not exist; existing datasets: {tags}")
    # Resolve through the owning collection as a second identity/readback
    # check.  It also keeps fake and remote engines on the same path.
    actual = _call(container, "get", tag)
    if node is None:
        node = actual
    return canonical, tag, container, actual


def _dataset_create_path(path: Any) -> tuple[dict[str, Any], str]:
    """Validate a typed path for a not-yet-created dataset."""
    if not isinstance(path, Mapping):
        raise PreWriteRefusal("INVALID_NODE_PATH", "dataset create path must be a NodePath object")
    parsed = NodePath.from_wire(path, allow_empty=False)
    if not parsed.segments or parsed.segments[0].accessor != "result":
        raise PreWriteRefusal("INVALID_NODE_PATH", "dataset path must begin with the result accessor")
    if any(segment.accessor is not None for segment in parsed.segments[1:]):
        raise PreWriteRefusal("INVALID_NODE_PATH", "dataset path has an unsupported accessor after result")
    final = parsed.segments[-1]
    if final.collection != "dataset" or final.tag is None:
        raise PreWriteRefusal("INVALID_NODE_PATH", "dataset create path must end in dataset:<tag>")
    return parsed.as_dict(), str(final.tag)


def _normalise_result_definition(definition: Any, *, allowed: frozenset[str] = _DATASET_PROPERTY_NAMES) -> list[tuple[str, Any]]:
    raw = require_mapping(definition, "definition")
    if "properties" in raw:
        if set(raw) != {"properties"}:
            raise ExecutionContractError("INVALID_REQUEST", "definition.properties cannot be mixed with direct fields")
        # Importing property_definition would permit a PropertySet row form;
        # preserve that contract here as well without sending raw TypedValue
        # wrappers to COMSOL.
        from ._g3_common import property_definition
        raw = property_definition(raw["properties"], "definition.properties")
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise PreWriteRefusal(
            "INVALID_REQUEST",
            f"definition has unsupported properties: {unknown}",
            details={"unsupported_properties": unknown},
        )
    return [(str(name), _probe_unwrap_property_value(value, f"definition.{name}")) for name, value in raw.items()]


def _result_readback(node: Any, properties: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    readable = True
    match = True
    for name, requested in properties:
        row = _probe_read_property(node, name, requested)
        rows.append({"property": name, **row})
        readable = readable and bool(row.get("readable"))
        match = match and bool(row.get("match"))
    return {"readable": readable, "match": match, "properties": rows, "type_id": _node_type(node, [])}


def _result_path(collection: str, tag: str) -> dict[str, Any]:
    return {"segments": [{"accessor": "result"}, {"collection": collection, "tag": tag}]}


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
    if type_id not in SUPPORTED_DATASET_TYPES:
        raise ExecutionContractError("API_UNSUPPORTED", f"dataset type {type_id!r} is not supported")
    properties = _normalise_result_definition(arguments.get("definition", {}))

    model = bound_model(worker, model_tag)
    container = _dataset_container(model)
    tags = tag_list(container)

    if tag in tags:
        raise ExecutionContractError("TAG_CONFLICT", f"dataset {tag!r} already exists")

    candidate_definition = {name: value for name, value in properties}
    _validate_dataset_type_properties(type_id, candidate_definition)
    _validate_dataset_graph(model, candidate_tag=tag, candidate_properties=candidate_definition,
                            candidate_type=type_id)
    _validate_dataset_component_refs(model, candidate_definition)

    applied: list[Any] = []
    failed: list[Any] = []
    not_executed: list[Any] = []
    try:
        dset = _call(container, "create", tag, type_id)
    except Exception as exc:
        failed.append({"step": "create", "error": {"code": getattr(exc, "code", "ENGINE_CALL_FAILED"), "message": str(exc)}})
        not_executed.extend({"step": "property", "property": name} for name, _ in properties)
        return {
            "tag": tag, "type_id": type_id, "path": _result_path("dataset", tag), "created": False,
            **_probe_completion(applied=applied, failed=failed, not_executed=not_executed,
                                readback={"readable": False, "match": False}, execution_state_unknown=True),
        }
    applied.append({"step": "create", "method": "result.dataset.create", "requested": {"tag": tag, "type_id": type_id}})
    updated_tags = tag_list(container)
    if tag not in updated_tags or dset is None:
        failed.append({"step": "create", "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": "post-create dataset tag/node readback did not confirm the node"}})
        not_executed.extend({"step": "property", "property": name} for name, _ in properties)
        return {
            "tag": tag, "type_id": type_id, "path": _result_path("dataset", tag), "created": True,
            **_probe_completion(applied=applied, failed=failed, not_executed=not_executed,
                                readback={"readable": False, "match": False, "tags": updated_tags}, execution_state_unknown=True),
        }
    prop_applied, prop_failed, prop_not_executed, prop_unknown = _probe_write_properties(dset, properties)
    applied.extend(prop_applied)
    failed.extend(prop_failed)
    not_executed.extend(prop_not_executed)
    readback = _result_readback(dset, properties)
    readback["tags"] = updated_tags
    type_readback = readback.get("type_id")
    if type_readback is not None and type_readback != type_id:
        failed.append({"step": "create", "error": {"code": "TYPE_CONFLICT", "message": f"created dataset type readback is {type_readback!r}, requested {type_id!r}"}})
    result = {
        "tag": tag, "type_id": type_id, "path": _result_path("dataset", tag), "created": True,
        "definition": {name: value for name, value in properties},
        **_probe_completion(applied=applied, failed=failed, not_executed=not_executed,
                            readback=readback, execution_state_unknown=prop_unknown),
    }
    return result


def dataset_inspect(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Inspect dataset node properties, bound solution, and nested configuration."""
    path = arguments.get("path")
    canonical, tag, _container, dset = _dataset_path_target(worker, model_tag, path)
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
        "path": canonical,
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
    properties = _normalise_result_definition(arguments.get("definition", {}))
    canonical, tag, _container, dset = _dataset_path_target(worker, model_tag, path)
    model = bound_model(worker, model_tag)
    candidate_definition = {name: value for name, value in properties}
    _validate_dataset_type_properties(_node_type(dset, []), candidate_definition)
    _validate_dataset_graph(model, candidate_tag=tag, candidate_properties=candidate_definition,
                            existing_node=dset, candidate_type=_node_type(dset, []))
    _validate_dataset_component_refs(model, candidate_definition, existing_node=dset)
    applied, failed, not_executed, execution_unknown = _probe_write_properties(dset, properties)
    readback = _result_readback(dset, properties)
    return {
        "path": canonical, "tag": tag, "updated": True,
        **_probe_completion(applied=applied, failed=failed, not_executed=not_executed,
                            readback=readback, execution_state_unknown=execution_unknown),
    }


def dataset_remove(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Remove a dataset node and verify removal."""
    path = arguments.get("path")
    canonical, tag, container, _dset = _dataset_path_target(worker, model_tag, path)
    try:
        _call(container, "remove", tag)
    except Exception as exc:
        return {"path": canonical, "tag": tag, "removed": False,
                **_probe_completion(applied=[], failed=[{"step": "remove", "error": {"code": getattr(exc, "code", "ENGINE_CALL_FAILED"), "message": str(exc)}}], not_executed=[], readback={"readable": False, "match": False}, execution_state_unknown=True)}
    remaining_tags = tag_list(container)
    verified = tag not in remaining_tags
    if not verified:
        return {"path": canonical, "tag": tag, "removed": True,
                **_probe_completion(applied=[{"step": "remove", "tag": tag}], failed=[{"step": "remove", "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": "post-remove tag readback still contains the dataset"}}], not_executed=[], readback={"readable": False, "tags": remaining_tags, "match": False}, execution_state_unknown=True)}
    return {"path": canonical, "tag": tag, "removed": True, "verified_removed": True,
            "remaining_datasets": remaining_tags,
            **_probe_completion(applied=[{"step": "remove", "tag": tag}], failed=[], not_executed=[], readback={"readable": True, "tags": remaining_tags, "match": True})}


def dataset_solution_indices(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """List available inner/outer solution indices, time steps and parameter combinations."""
    path = arguments.get("path")
    # Solution-axis inspection belongs to the existing numeric adapter.  Keep
    # its historical tag/path acceptance here; typed CRUD validation is scoped
    # to dataset create/inspect/update/remove above.
    tag = _dataset_tag(path)
    model = bound_model(worker, model_tag)
    container = _dataset_container(model)
    tags = tag_list(container)
    if tag not in tags:
        raise node_not_found(f"dataset {tag!r} does not exist; existing datasets: {tags}")

    dset = _call(container, "get", tag)
    dataset_binding = _resolve_dataset_binding(model, str(tag))
    if not isinstance(dataset_binding, Mapping) or dataset_binding.get("binding_complete") is not True:
        binding_errors = list(dataset_binding.get("read_errors") or []) if isinstance(dataset_binding, Mapping) else []
        binding_error = dataset_binding.get("error") if isinstance(dataset_binding, Mapping) else None
        if isinstance(binding_error, Mapping) and binding_error not in binding_errors:
            binding_errors.insert(0, dict(binding_error))
        return {
            "dataset": tag,
            "solution": dataset_binding.get("solution") if isinstance(dataset_binding, Mapping) else None,
            "binding_complete": False,
            "binding_source": "resolve_dataset_binding(model, dataset_tag)",
            "time_values": [],
            "inner_indices": [],
            "outer_indices": [],
            "parameters": {},
            "solution_count": 0,
            "dataset_binding": dict(dataset_binding) if isinstance(dataset_binding, Mapping) else None,
            "read_errors": binding_errors,
            "error": dict(binding_error) if isinstance(binding_error, Mapping) else {
                "code": "DATASET_BINDING_INCOMPLETE",
                "message": "dataset binding is incomplete; solution axes were not guessed from dataset properties",
            },
            "note": "dataset binding is incomplete; solution axes were not guessed from dataset.data or dataset.solution",
        }
    solution_tag = (
        dataset_binding.get("solution")
        if isinstance(dataset_binding, Mapping) and dataset_binding.get("solution")
        else None
    )

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

    # ``SolverSequence.study()`` is a *method*, not a string property: reading it
    # with ``getString("study")`` always failed, so every dataset was classified
    # as steady and a solved transient solution published no time axis at all.
    # Verified live (isolated probe, then this op): ``model.sol("sol1").study()``
    # is ``"std1"``, whose step type is ``Transient``.
    study_tag = _record(sol_node, "study", errors=read_errors)
    study_tag = study_tag if isinstance(study_tag, str) and study_tag else None
    steps, study_error = _study_steps(model, study_tag, read_errors) if study_tag else ([], None)
    is_transient = any(step.get("type") in TIME_DEPENDENT_STUDY_STEPS for step in steps)

    time_values: list[float] = []
    if is_transient and pvals:
        time_values = [float(v) for v in pvals]
    if time_values:
        time_axis_source = "stored output times from SolverSequence.getPVals()"
    elif is_transient:
        time_axis_source = "unavailable: transient solution but getPVals() returned no values"
    elif study_tag:
        time_axis_source = "steady: no time axis"
    else:
        time_axis_source = ("unavailable: SolverSequence.study() did not name an associated study, so "
                            "getPVals() is not published as a time axis"
                            + (f" ({study_error})" if study_error else ""))

    sol_info = _read(sol_node, "getSolutioninfo")
    outer_indices: list[int] = []
    inner_indices: list[int] = []
    level_names: list[str] = []
    typed_binding: dict[str, Any] | None = None
    typed_mapping_available = bool(sol_info is not None and callable(getattr(sol_info, "getSolnum", None)))
    if typed_mapping_available:
        # SolutionInfo.getMaxInner is not a mapping: different outer levels can
        # contain different inner counts.  Resolve every pair through the
        # public getSolnum(outer, strict) route and retain its actual solnum.
        try:
            from ._solution_binding import SolutionBinding
            typed_binding = SolutionBinding.resolve_solution_info(sol_info)
            outer_indices = list(typed_binding.get("outer_indices", []))
            inner_indices = list(typed_binding.get("inner_indices", []))
            level_names = list(typed_binding.get("level_names", []))
            read_errors.extend(list(typed_binding.get("read_errors", [])))
        except ExecutionContractError as exc:
            read_errors.append({"method": "SolutionInfo", "code": exc.code, "message": str(exc)})
    elif sol_info is not None:
        # Legacy workers expose only getOuterSolnum/getMaxInner.  Keep this
        # compatibility response for inspection, but mark it explicitly as a
        # legacy shape and never use it as a complete four-axis binding.
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
    pair_mapping_complete = bool(typed_binding and typed_binding.get("pair_mapping_complete"))
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
    if typed_binding:
        # Preserve pair identity in a nested response while providing a small
        # name-oriented summary for clients that only need parameter columns.
        parameters_by_pair: dict[str, Any] = {}
        for pair in typed_binding.get("solnum_pairs", []):
            key = f"{pair['outer']}:{pair['inner']}"
            pair_key = (int(pair["outer"]), int(pair["inner"]))
            names = typed_binding.get("parameter_names_by_pair", {}).get(pair_key, [])
            values = typed_binding.get("parameter_values_by_pair", {}).get(pair_key, [])
            units = typed_binding.get("parameter_units_by_pair", {}).get(pair_key, [])
            row = {"names": list(names), "values": list(values), "units": list(units), "solnum": pair["solnum"]}
            parameters_by_pair[key] = row
            for name, value in zip(names, values):
                parameters.setdefault(str(name), []).append(value)
        parameters["by_pair"] = parameters_by_pair
    else:
        pnames = _read(sol_node, "getParamNames")
        param_vals = _read(sol_node, "getParamVals")
        if pnames and param_vals:
            for n, v in zip(pnames, param_vals):
                parameters[str(n)] = list(v) if isinstance(v, (list, tuple)) else v

    return {
        "dataset": tag,
        "solution": solution_tag,
        "study": study_tag,
        "binding_complete": bool(solution_tag and (pair_mapping_complete if typed_mapping_available else True)),
        "binding_source": (
            "dataset property 'solution'/'data' resolved against model.sol().tags(); "
            + ("SolutionInfo.getSolnum(outer, strict)" if typed_mapping_available else "legacy getOuterSolnum()/getMaxInner")
        ),
        "axis_metadata_complete": axis_metadata_complete,
        "pair_mapping_complete": pair_mapping_complete,
        "axis_metadata_source": (
            "SolutionInfo.getSolnum(outer, strict) + getPNames/getPvals/getUnits"
            if typed_mapping_available else
            "legacy SolverSequence.getSolutioninfo() + SolutionInfo.getOuterSolnum()/getMaxInner()/getLevelNames()"
        ),
        "time_values": time_values,
        "time_axis_source": time_axis_source,
        "inner_indices": inner_indices,
        "outer_indices": outer_indices,
        "level_names": level_names,
        "parameters": parameters,
        # §4: index-pairing two arrays is not proof of the full parameter
        # combination, so completeness is reported instead of asserted (the
        # combination map would need SolutionInfo.mapToSolnum()).
        "parameters_complete": bool(typed_binding and typed_binding.get("parameter_values_by_pair")) if typed_mapping_available else False,
        "parameters_source": (
            "SolutionInfo.getPNames(int[][])/getPvals(int[][])/getUnits(int[][])"
            if typed_mapping_available else "SolverSequence.getParamNames()/getParamVals() (index-paired)"
        ),
        "solution_count": len(typed_binding.get("solnum_pairs", [])) if typed_binding else (len(pvals) if pvals else len(inner_indices)),
        "solnum_pairs": list(typed_binding.get("solnum_pairs", [])) if typed_binding else [],
        "read_errors": read_errors,
        "dataset_binding": dict(dataset_binding) if isinstance(dataset_binding, Mapping) else None,
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
    """Use the shared strict complex transformer for all result operations."""
    from ._complex_transform import transform_complex_data
    return transform_complex_data(real_data, imag_data, mode, is_complex=is_complex)


def _effective_engine_expressions(
    expressions: Sequence[str],
    mode: str,
    *,
    before_statistics: bool,
) -> list[str]:
    """Bind one consistent field expression to every aggregate feature.

    The result payload still names the caller's expressions.  These are the
    engine expressions used to obtain the requested comparison field when the
    transform is explicitly before statistics.
    """
    if not before_statistics:
        return [str(expression) for expression in expressions]
    from ._complex_transform import effective_engine_expression
    return [effective_engine_expression(str(expression), mode) for expression in expressions]


def _feature_complex_status(
    feature: Any,
    *,
    outer: int | None = None,
    use_outer_getters: bool = False,
) -> bool:
    """Read the complex flag before admitting any numeric getter.

    Spatial aggregate NumericalFeatures expose an outer-aware overload.  A
    no-argument ``isComplex()``/``getReal()`` read is scoped to the first outer
    solution on COMSOL 6.4, even after ``outersolnum`` is selected, so aggregate
    callers must opt into the verified overload explicitly.
    """
    try:
        if use_outer_getters and outer is not None:
            status = _call(feature, "isComplex", int(outer))
        else:
            status = _call(feature, "isComplex")
    except Exception as exc:
        raise ExecutionContractError("COMPLEX_STATUS_UNAVAILABLE", "NumericalFeature.isComplex() could not be read") from exc
    if not isinstance(status, bool):
        raise ExecutionContractError("COMPLEX_STATUS_UNAVAILABLE", "NumericalFeature.isComplex() did not return a boolean")
    return status


def _feature_coordinates_shape(feature: Any) -> dict[str, Any]:
    """Read the shape-only Eval witness without transferring coordinate values."""
    try:
        witness = _call(feature, "getCoordinatesShape")
    except Exception as exc:
        raise ExecutionContractError(
            "RAW_POINT_SHAPE_UNAVAILABLE",
            "Eval numerical feature did not provide the verified getCoordinatesShape() witness",
        ) from exc
    if not isinstance(witness, Mapping):
        raise ExecutionContractError(
            "RAW_POINT_SHAPE_UNAVAILABLE",
            "getCoordinatesShape() returned a non-mapping shape witness",
        )
    point_count = witness.get("point_count")
    if isinstance(point_count, bool) or not isinstance(point_count, int) or point_count <= 0:
        raise ExecutionContractError(
            "RAW_POINT_SHAPE_UNAVAILABLE",
            "getCoordinatesShape() did not return a positive integer point_count",
        )
    shape = witness.get("shape")
    if not isinstance(shape, Sequence) or isinstance(shape, (str, bytes)) or len(shape) != 2:
        raise ExecutionContractError(
            "RAW_POINT_SHAPE_UNAVAILABLE",
            "getCoordinatesShape() did not return a [dimension, point_count] shape",
        )
    if (
        isinstance(shape[0], bool)
        or not isinstance(shape[0], int)
        or shape[0] <= 0
        or isinstance(shape[1], bool)
        or not isinstance(shape[1], int)
        or shape[1] != point_count
    ):
        raise ExecutionContractError(
            "RAW_POINT_SHAPE_UNAVAILABLE",
            "getCoordinatesShape() returned an inconsistent dimension/point_count pair",
        )
    return dict(witness)


def _feature_components(
    feature: Any,
    *,
    budget_decision: Mapping[str, Any] | None = None,
    outer: int | None = None,
    use_outer_getters: bool = False,
) -> tuple[Any, Any, bool, str]:
    """Read a numerical feature with an explicit documented data layout.

    When ``budget_decision`` is present, both raw array getters are guarded by
    the C13 pre-getData admission.  A refused decision is deliberately not
    treated as an ordinary ``getData`` API miss, since falling back to a raw
    aggregate getter would bypass the numeric payload budget.
    """
    status = _feature_complex_status(
        feature,
        outer=outer,
        use_outer_getters=use_outer_getters,
    )

    def _read_raw(method: str) -> Any:
        if budget_decision is None:
            return _call(feature, method)
        try:
            value = guarded_getdata(feature, budget_decision, method=method)
        except ResultBudgetRefused:
            raise
        # Keep the decision auditable without pretending that this records a
        # process or engine peak.  The planner's byte estimate remains only a
        # numeric scalar payload estimate.
        if isinstance(budget_decision, dict):
            if method == "getData":
                budget_decision["getdata_called"] = True
            elif method == "getImagData":
                budget_decision["imag_getdata_called"] = True
        return value

    real: Any
    layout: str
    if use_outer_getters and outer is not None and budget_decision is None:
        try:
            # Verified COMSOL 6.4 overload: getReal(false, outer).  The first
            # boolean selects the native column-wise layout and the explicit
            # outer label avoids the no-argument getter's silent outer-1 default.
            real = _call(feature, "getReal", False, int(outer))
            layout = "expression,solnum"
        except Exception as exc:
            raise ExecutionContractError(
                "ENGINE_CALL_FAILED",
                f"outer-aware numerical feature real data could not be read for outer {outer}",
            ) from exc
    else:
        try:
            real = _read_raw("getData")
            layout = "expression,solnum,point"
        except ResultBudgetRefused:
            raise
        except Exception:
            try:
                real = _call(feature, "getReal")
                layout = "expression,solnum"
            except Exception as exc:
                raise ExecutionContractError("ENGINE_CALL_FAILED", "numerical feature real data could not be read") from exc
    imag = None
    if status:
        try:
            if use_outer_getters and outer is not None and budget_decision is None:
                # Verified COMSOL 6.4 overload: getImag(outer).
                imag = _call(feature, "getImag", int(outer))
            else:
                imag = _read_raw("getImagData")
        except ResultBudgetRefused:
            raise
        except Exception:
            if use_outer_getters and outer is not None and budget_decision is None:
                raise ExecutionContractError(
                    "COMPLEX_DATA_ERROR",
                    f"outer-aware numerical feature imaginary data could not be read for outer {outer}",
                )
            try:
                imag = _call(feature, "getImag")
            except Exception as exc:
                raise ExecutionContractError("COMPLEX_DATA_ERROR", "imaginary data missing for complex field") from exc
    return real, imag, status, layout


def _budget_refusal(
    *,
    operation: str,
    feature_kind: str,
    expression_count: int,
    inner_count: int,
    max_elements: int,
    max_bytes: int,
    reason_code: str,
    reason: str,
    point_count: int = 1,
    point_count_verified: bool = False,
    point_count_source: str | None = None,
    solution_axes_verified: bool = True,
    solution_axes_source: str | None = "validated-SolutionBinding",
) -> dict[str, Any]:
    """Build an auditable refusal when a metadata witness cannot be read."""
    return {
        "status": "BLOCKED",
        "allowed": False,
        "verification": "UNVERIFIED",
        "reason_code": reason_code,
        "reason": reason,
        "operation": operation,
        "feature_kind": feature_kind,
        "raw_getter": "getData",
        "raw_layout": "expression,solnum,vertex",
        "pre_getdata": True,
        "getdata_called": False,
        "publish_allowed": False,
        "budget_scope": "numeric_payload_only",
        "limits": {
            "max_elements": max_elements,
            "max_bytes": max_bytes,
            "max_numeric_payload_bytes": max_bytes,
        },
        "requested": {
            "expression_count": expression_count,
            "point_count": point_count,
            "outer_count": 1,
            "inner_count": inner_count,
            "complex_components": None,
            "scalar_bytes": 8,
            "max_elements": max_elements,
            "max_bytes": max_bytes,
            "raw_point_upper_bound": None,
            "raw_upper_bound_verified": False,
            "raw_upper_bound_source": None,
        },
        "computed": None,
        "metadata_sources": {
            "expression": "validated-request-expressions",
            "point_count": point_count_source,
            "solution_axes": solution_axes_source if solution_axes_verified else None,
            "raw_point_upper_bound": None,
        },
        "data_vector_count": {
            "value": None,
            "used_for_point_bound": False,
            "interpretation": "observation only; never treated as coordinate count",
        },
    }


def _make_result_budget_guard(
    *,
    operation: str,
    feature_kind: str,
    expressions: Sequence[str],
    binding: Mapping[str, Any] | None,
    point_count: int | None = None,
    max_elements: int = RESULT_NUMERIC_PAYLOAD_MAX_ELEMENTS,
    max_bytes: int = RESULT_NUMERIC_PAYLOAD_MAX_BYTES,
    records: list[dict[str, Any]] | None = None,
) -> Any:
    """Create a per-outer raw-array admission callback.

    Each outer read is planned independently, while the remaining limits are
    reduced after every successful read.  This keeps a later outer from
    exceeding a whole-request limit even though COMSOL's inner ``solnum`` axis
    is scoped to the selected outer.
    """
    consumed_elements = 0
    consumed_bytes = 0
    is_interp = feature_kind.strip().lower() in {"interp", "interpolation"}

    def _append(decision: dict[str, Any]) -> dict[str, Any]:
        if records is not None:
            records.append(decision)
        return decision

    def _guard(feature: Any, outer: int | None) -> Mapping[str, Any]:
        nonlocal consumed_elements, consumed_bytes

        outer_indices = list(binding.get("outer_indices") or []) if isinstance(binding, Mapping) else []
        inner_by_outer = binding.get("inner_indices_by_outer") if isinstance(binding, Mapping) else None
        binding_verified = bool(
            isinstance(binding, Mapping)
            and binding.get("pair_mapping_complete") is True
            and outer_indices
            and isinstance(inner_by_outer, Mapping)
        )
        inner_values: list[Any] = []
        if binding_verified and outer is not None:
            try:
                inner_values = list(inner_by_outer.get(int(outer), []))
            except (TypeError, ValueError):
                inner_values = []
            if not inner_values:
                binding_verified = False

        # Do not ask the engine for a raw point witness when the solution axes
        # themselves are incomplete.  The refusal remains pre-getData and
        # identifies which metadata contract failed.
        if not binding_verified:
            decision = _budget_refusal(
                operation=operation,
                feature_kind=feature_kind,
                expression_count=len(expressions),
                inner_count=1,
                max_elements=max_elements,
                max_bytes=max_bytes,
                reason_code="SOLUTION_AXES_UNVERIFIED",
                reason="outer/inner counts must come from a complete SolutionBinding before getData",
                point_count=point_count or 1,
                point_count_verified=point_count is not None,
                point_count_source="validated-Interp-request-coordinates" if is_interp else None,
                solution_axes_verified=False,
                solution_axes_source=None,
            )
            raise ResultBudgetRefused(_append(decision))

        complex_components = 2 if _feature_complex_status(feature) else 1
        raw_point_count: int | None = None
        raw_point_source: str | None = None
        raw_bound_verified = False
        shape_witness: dict[str, Any] | None = None
        if is_interp:
            if isinstance(point_count, bool) or not isinstance(point_count, int) or point_count <= 0:
                decision = _budget_refusal(
                    operation=operation,
                    feature_kind=feature_kind,
                    expression_count=len(expressions),
                    inner_count=len(inner_values),
                    max_elements=max_elements,
                    max_bytes=max_bytes,
                    reason_code="POINT_COUNT_UNVERIFIED",
                    reason="Interp point count must be a positive validated request count",
                    point_count=point_count or 1,
                    point_count_source=None,
                )
                raise ResultBudgetRefused(_append(decision))
            raw_point_source = "validated-Interp-request-coordinates"
        else:
            try:
                shape_witness = _feature_coordinates_shape(feature)
                raw_point_count = int(shape_witness["point_count"])
                raw_point_source = "native NumericalFeature.getCoordinatesShape()"
                raw_bound_verified = True
            except ExecutionContractError as exc:
                decision = _budget_refusal(
                    operation=operation,
                    feature_kind=feature_kind,
                    expression_count=len(expressions),
                    inner_count=len(inner_values),
                    max_elements=max_elements,
                    max_bytes=max_bytes,
                    reason_code="RAW_POINT_SHAPE_UNAVAILABLE",
                    reason=str(exc),
                    point_count=1,
                    point_count_source=None,
                )
                raise ResultBudgetRefused(_append(decision)) from exc

        remaining_elements = max(1, max_elements - consumed_elements)
        remaining_bytes = max(1, max_bytes - consumed_bytes)
        decision = plan_result_budget(
            expression_count=len(expressions),
            point_count=raw_point_count if raw_point_count is not None else point_count,
            outer_count=1,
            inner_count=len(inner_values),
            max_elements=remaining_elements,
            max_bytes=remaining_bytes,
            operation=operation,
            feature_kind=feature_kind,
            raw_getter="getData",
            raw_layout="expression,solnum,vertex",
            expression_count_verified=True,
            point_count_verified=True,
            solution_axes_verified=True,
            expression_source="validated-request-expressions",
            point_count_source=raw_point_source,
            solution_axes_source="validated-SolutionBinding.outer/inner-pairs",
            raw_point_upper_bound=raw_point_count,
            raw_upper_bound_verified=raw_bound_verified,
            raw_upper_bound_source=raw_point_source if raw_bound_verified else None,
            complex_components=complex_components,
        )
        decision = _append(decision)
        if shape_witness is not None:
            decision["raw_point_shape_witness"] = shape_witness
        if decision.get("status") != "PASS" or decision.get("allowed") is not True:
            raise ResultBudgetRefused(decision)
        computed = decision.get("computed") or {}
        consumed_elements += int(computed.get("elements", 0))
        consumed_bytes += int(computed.get("bytes", 0))
        decision["request_progress"] = {
            "consumed_numeric_payload_elements": consumed_elements,
            "consumed_numeric_payload_bytes": consumed_bytes,
            "max_elements": max_elements,
            "max_bytes": max_bytes,
        }
        return decision

    return _guard


def _normalise_evalpoint_components(
    real: Any,
    imag: Any,
    *,
    num_expressions: int,
) -> tuple[Any, Any, int]:
    """Normalise EvalPoint's ``[expression*point][inner]`` getter result.

    COMSOL 6.4 does not provide IntPoint/AvPoint.  EvalPoint returns one row
    per expression/selected point and one column per stored inner solution;
    rows are grouped by expression.  Convert that documented native shape to
    the normal ``[expression][inner][point]`` layout before any statistics.
    """
    if isinstance(real, Sequence) and not isinstance(real, (str, bytes)):
        rows = list(real)
    else:
        rows = [real]
    if not rows or len(rows) % num_expressions:
        raise ExecutionContractError(
            "FIELD_ARRAY_SHAPE_MISMATCH",
            f"EvalPoint returned {len(rows)} rows for {num_expressions} expressions",
        )
    point_count = len(rows) // num_expressions

    def _columns(row: Any) -> list[Any]:
        if isinstance(row, Sequence) and not isinstance(row, (str, bytes)):
            return list(row)
        return [row]

    columns = [_columns(row) for row in rows]
    inner_count = len(columns[0])
    if inner_count < 1 or any(len(row) != inner_count for row in columns):
        raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", "EvalPoint inner rows have inconsistent lengths")
    real_out: list[list[list[Any]]] = []
    for expression_index in range(num_expressions):
        block = columns[expression_index * point_count : (expression_index + 1) * point_count]
        real_out.append([
            [block[point_index][inner_index] for point_index in range(point_count)]
            for inner_index in range(inner_count)
        ])

    imag_out = None
    if imag is not None:
        imag_rows = list(imag) if isinstance(imag, Sequence) and not isinstance(imag, (str, bytes)) else [imag]
        if len(imag_rows) != len(rows):
            raise ExecutionContractError("COMPLEX_DATA_ERROR", "EvalPoint imaginary row count differs from real data")
        imag_columns = [_columns(row) for row in imag_rows]
        if any(len(row) != inner_count for row in imag_columns):
            raise ExecutionContractError("COMPLEX_DATA_ERROR", "EvalPoint imaginary inner rows differ from real data")
        imag_out = []
        for expression_index in range(num_expressions):
            block = imag_columns[expression_index * point_count : (expression_index + 1) * point_count]
            imag_out.append([
                [block[point_index][inner_index] for point_index in range(point_count)]
                for inner_index in range(inner_count)
            ])
    return real_out, imag_out, point_count


def _point_reduce(data: Any, operation: str) -> Any:
    """Reduce the final point axis while retaining a singleton point axis."""
    if isinstance(data, Mapping):
        if set(data) >= {"real", "imag"}:
            # A mapping at this level is one already-reduced complex scalar.
            # Lists of such mappings are handled by the leaf branch below.
            return {"real": float(data["real"]), "imag": float(data["imag"])}
        raise ExecutionContractError("COMPLEX_DATA_ERROR", "complex point value lacks real/imag components")
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
        values = list(data)
        if not values:
            raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", "point axis is empty")
        # A leaf here is the point axis.  Higher axes contain nested lists.
        if all(
            (isinstance(item, Mapping) and set(item) >= {"real", "imag"})
            or (not isinstance(item, (Sequence, Mapping)) and not isinstance(item, (str, bytes)))
            for item in values
        ):
            if any(isinstance(item, Mapping) for item in values):
                if not all(isinstance(item, Mapping) and set(item) >= {"real", "imag"} for item in values):
                    raise ExecutionContractError("COMPLEX_DATA_ERROR", "point axis mixes real and complex values")
                if operation in {"minimum", "maximum"}:
                    raise ExecutionContractError(
                        "COMPLEX_ORDER_UNDEFINED",
                        "complex point extrema require complex_mode=real or abs",
                    )
                real_values = [float(item["real"]) for item in values]
                imag_values = [float(item["imag"]) for item in values]
                if operation == "sum":
                    reduced = {"real": sum(real_values), "imag": sum(imag_values)}
                elif operation == "count":
                    reduced = {"real": float(len(values)), "imag": 0.0}
                else:
                    raise ExecutionContractError("API_UNSUPPORTED", f"unsupported complex point reduction {operation!r}")
                return [reduced]
            if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in values):
                raise ExecutionContractError("INVALID_RESULT", "point values must be finite real numbers")
            numbers = [float(item) for item in values]
            if not all(math.isfinite(item) for item in numbers):
                raise ExecutionContractError("INVALID_RESULT", "point values must be finite")
            if operation == "sum":
                reduced = sum(numbers)
            elif operation == "minimum":
                reduced = min(numbers)
            elif operation == "maximum":
                reduced = max(numbers)
            elif operation == "count":
                reduced = float(len(numbers))
            else:
                raise ExecutionContractError("API_UNSUPPORTED", f"unsupported point reduction {operation!r}")
            return [reduced]
        return [_point_reduce(item, operation) for item in values]
    if isinstance(data, (int, float)) and not isinstance(data, bool):
        return [float(data)]
    raise ExecutionContractError("INVALID_RESULT", f"cannot reduce point data of type {type(data).__name__}")


def _strict_singleton_value(value: Any, *, label: str) -> Any:
    """Unwrap a result cell only when every remaining axis is singleton."""
    if isinstance(value, Mapping):
        if not {"real", "imag"}.issubset(set(value)):
            raise ExecutionContractError("COMPLEX_DATA_ERROR", f"{label} complex value lacks real/imag components")
        real = float(value["real"])
        imag = float(value["imag"])
        if not math.isfinite(real) or not math.isfinite(imag):
            raise ExecutionContractError("INVALID_RESULT", f"{label} is not finite")
        return {"real": real, "imag": imag}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        values = list(value)
        if len(values) != 1:
            raise ExecutionContractError(
                "FIELD_ARRAY_SHAPE_MISMATCH",
                f"{label} has a non-singleton axis of length {len(values)}",
            )
        return _strict_singleton_value(values[0], label=label)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExecutionContractError("INVALID_RESULT", f"{label} is not numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ExecutionContractError("INVALID_RESULT", f"{label} is not finite")
    return number


def _numeric_literal(value: Any, *, unit: Any = None) -> str:
    """Format a finite scalar for an engine expression, retaining its unit."""
    if isinstance(value, Mapping):
        raise ExecutionContractError("COMPLEX_DATA_ERROR", "a complex value needs the complex center expression")
    number = float(value)
    if not math.isfinite(number):
        raise ExecutionContractError("INVALID_RESULT", "a centered mean is not finite")
    literal = format(number, ".17g")
    if unit not in (None, "", "1", "dimensionless"):
        literal = f"({literal}[{unit}])"
    return literal


def _centered_square_expression(
    expression: str,
    mean_value: Any,
    *,
    unit: Any = None,
) -> str:
    """Construct ``|f-mean|^2`` with a dimensionally matched center."""
    if isinstance(mean_value, Mapping):
        if set(mean_value) < {"real", "imag"}:
            raise ExecutionContractError("COMPLEX_DATA_ERROR", "complex mean lacks real/imag components")
        real_literal = _numeric_literal(mean_value["real"], unit=unit)
        imag_literal = _numeric_literal(mean_value["imag"], unit=unit)
        centered = (
            f"(real(({expression}))-{real_literal})"
            f"+i*(imag(({expression}))-{imag_literal})"
        )
    else:
        centered = f"(({expression})-{_numeric_literal(mean_value, unit=unit)})"
    return f"abs(({centered}))^2"


def _single_expression_cells(data: Any, *, num_expressions: int, label: str) -> list[Any]:
    """Read one scalar cell per expression from a selected numerical result."""
    rows = list(data) if isinstance(data, Sequence) and not isinstance(data, (str, bytes)) else [data]
    if len(rows) != num_expressions:
        raise ExecutionContractError(
            "FIELD_ARRAY_SHAPE_MISMATCH",
            f"{label} returned {len(rows)} expression rows, expected {num_expressions}",
        )
    return [
        _strict_singleton_value(row, label=f"{label}[expression={index}]")
        for index, row in enumerate(rows)
    ]


def _selected_inner_expression_cells(
    data: Any,
    *,
    inner_labels: Sequence[Any],
    inner: int,
    num_expressions: int,
    label: str,
) -> list[Any]:
    """Select one real inner solution from an outer-aware aggregate read.

    COMSOL's ``getReal(false, outer)`` still returns the complete inner axis;
    setting ``solnum`` on the feature does not guarantee that the getter
    shrinks it.  The only safe scalarisation is therefore to locate the
    requested actual inner label in the verified ``SolutionInfo`` axis and
    select that column explicitly.
    """
    labels = [int(value) for value in inner_labels]
    try:
        position = labels.index(int(inner))
    except (ValueError, TypeError) as exc:
        raise ExecutionContractError(
            "SOLUTION_AXIS_METADATA_UNAVAILABLE",
            f"inner solution {inner!r} is not present in the verified outer axis {labels!r}",
        ) from exc
    rows = list(data) if isinstance(data, Sequence) and not isinstance(data, (str, bytes)) else [data]
    if len(rows) != num_expressions:
        raise ExecutionContractError(
            "FIELD_ARRAY_SHAPE_MISMATCH",
            f"{label} returned {len(rows)} expression rows, expected {num_expressions}",
        )
    selected: list[Any] = []
    for expression_index, row in enumerate(rows):
        if isinstance(row, Sequence) and not isinstance(row, (str, bytes)):
            values = list(row)
            if len(values) != len(labels):
                raise ExecutionContractError(
                    "FIELD_ARRAY_SHAPE_MISMATCH",
                    f"{label}[expression={expression_index}] inner axis has length {len(values)}, expected {len(labels)}",
                )
            selected.append(_strict_singleton_value(
                values[position],
                label=f"{label}[expression={expression_index},inner={inner}]",
            ))
        elif len(labels) == 1:
            selected.append(_strict_singleton_value(
                row,
                label=f"{label}[expression={expression_index},inner={inner}]",
            ))
        else:
            raise ExecutionContractError(
                "FIELD_ARRAY_SHAPE_MISMATCH",
                f"{label}[expression={expression_index}] has no explicit inner axis",
            )
    return selected


def _feature_units(feature: Any, expressions: Sequence[str]) -> dict[str, Any]:
    """Read expression units when the numerical feature publishes them."""
    raw: Any = None
    try:
        raw = _call(feature, "getStringArray", "unit")
    except Exception:
        try:
            raw = _call(feature, "getString", "unit")
        except Exception:
            return {}
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, Sequence) and not isinstance(raw, (bytes, str)):
        values = list(raw)
    else:
        return {}
    if len(values) == 1 and len(expressions) > 1:
        values = values * len(expressions)
    if len(values) != len(expressions):
        return {str(expression): None for expression in expressions}
    return {
        str(expression): (None if value is None or value == "" else str(value))
        for expression, value in zip(expressions, values)
    }


def _field_array_context(
    binding: Mapping[str, Any],
    expressions: Sequence[str],
    *,
    expression_units: Mapping[str, Any] | None = None,
    length_unit: Any = None,
    point_count: int = 1,
    point_coordinates: Any = None,
    point_coordinate_source: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Build JSON-safe FieldArray coordinates, units, and source metadata.

    SolutionInfo's parameter metadata belongs to each ``(outer, inner)``
    pair.  It therefore cannot be represented by inventing a fifth numeric
    axis.  The canonical four axes remain rectangular while the pair records
    are published in ``coords.parameters`` and ``metadata.solution_pairs``.
    """
    expressions_list = [str(item) for item in expressions]
    outer_indices = [int(item) for item in (binding.get("outer_indices") or [])]
    inner_indices = [int(item) for item in (binding.get("inner_indices") or [])]
    names_by_pair = binding.get("parameter_names_by_pair") or {}
    values_by_pair = binding.get("parameter_values_by_pair") or {}
    units_by_pair = binding.get("parameter_units_by_pair") or {}
    pair_records: list[dict[str, Any]] = []
    time_values: list[Any] = []
    frequency_values: list[Any] = []
    time_unit: Any = None
    frequency_unit: Any = None
    for pair in binding.get("solnum_pairs") or []:
        outer = int(pair["outer"])
        inner = int(pair["inner"])
        key = (outer, inner)
        names = [str(name) for name in (names_by_pair.get(key) or []) if str(name)]
        values = list(values_by_pair.get(key) or [])
        raw_units = list(units_by_pair.get(key) or [])
        parameter_values: dict[str, Any] = {}
        parameter_units: dict[str, Any] = {}
        for index, name in enumerate(names):
            parameter_values[name] = values[index] if index < len(values) else None
            unit = raw_units[index] if index < len(raw_units) else None
            parameter_units[name] = None if unit in (None, "") else str(unit)
            lower = name.lower()
            if lower in {"t", "time"}:
                time_values.append(parameter_values[name])
                if time_unit is None:
                    time_unit = parameter_units[name]
            if lower in {"f", "freq", "frequency"}:
                frequency_values.append(parameter_values[name])
                if frequency_unit is None:
                    frequency_unit = parameter_units[name]
        pair_records.append({
            "outer": outer,
            "inner": inner,
            "solnum": int(pair.get("solnum", inner)),
            "parameters": parameter_values,
            "parameter_units": parameter_units,
        })

    coords: dict[str, Any] = {
        "expression": expressions_list,
        "outer": outer_indices,
        "inner": inner_indices,
        "point": list(range(1, int(point_count) + 1)),
        # These are metadata coordinate columns over the pair records, not
        # additional data axes.  Keeping them explicit prevents callers from
        # mistaking a parameter sweep for a globally enumerated solnum axis.
        "parameters": [record["parameters"] for record in pair_records],
        "params": [record["parameters"] for record in pair_records],
    }
    if time_values:
        coords["time"] = time_values
    if frequency_values:
        coords["frequency"] = frequency_values
    if point_coordinates is not None:
        # FieldArray coordinates use one record per point, with the spatial
        # components inside that record.  The COMSOL setter/readback API uses
        # the opposite ``[dimension][point]`` matrix, so callers must transpose
        # that engine-facing representation before publishing it here.
        coords["spatial"] = point_coordinates

    expression_unit_map = {
        str(name): (None if value in (None, "") else str(value))
        for name, value in (expression_units or {}).items()
    }
    units: dict[str, Any] = {
        "expression": expression_unit_map,
        "outer": "index",
        "inner": "index",
        "point": length_unit,
        "parameters": {
            name: unit
            for record in pair_records
            for name, unit in record["parameter_units"].items()
            if unit is not None
        },
    }
    if time_values:
        units["time"] = time_unit
    if frequency_values:
        units["frequency"] = frequency_unit
    metadata = {
        "binding_source": binding.get("binding_source"),
        "pair_mapping_complete": bool(binding.get("pair_mapping_complete")),
        "solution_pairs": pair_records,
        "parameter_names": list(binding.get("parameter_names") or []),
        "expression_units": expression_unit_map,
        "unit_readback_status": "VERIFIED" if expression_unit_map and all(value is not None for value in expression_unit_map.values()) else "UNVERIFIED",
        "point_coordinate_source": point_coordinate_source or (
            "request" if point_coordinates is not None else "numerical-feature"
        ),
    }
    if time_values:
        metadata["time_unit"] = time_unit
    if frequency_values:
        metadata["frequency_unit"] = frequency_unit
    return coords, units, metadata


def _field_array_with_values(
    template: Any,
    values: Any,
    *,
    is_complex: bool | None,
    selectors: Mapping[str, Any] | None = None,
) -> Any:
    """Rebuild a typed field from final values, then apply solution selectors.

    ``result.evaluate`` keeps the raw FieldArray as an axis/metadata template,
    while aggregate branches replace its values with mean, variance, or RMS
    data.  Reusing ``template.select`` would select the stale raw values and
    overwrite those statistics.  This helper makes the ordering explicit and
    keeps the four axes and their metadata attached to the final calculation.
    """
    from ._solution_binding import FieldArray

    result = FieldArray(
        values,
        axes=template.axes,
        coords=template.coords,
        units=template.units,
        metadata=template.metadata,
        is_complex=is_complex,
    )
    if selectors:
        result = result.select(**dict(selectors))
    return result


def _field_array_is_complex(
    engine_is_complex: bool | None,
    complex_mode: str,
    aggregate: str = "none",
) -> bool:
    """Describe the published FieldArray value type, separate from raw status."""
    if engine_is_complex is not True or complex_mode != "preserve":
        return False
    # Population standard deviation and RMS are real by definition after the
    # modulus-square integration, even when the source field is complex.
    return aggregate not in {"std", "rms"}


def _axisymmetric_measure_readback(feature: Any, *, role: str) -> dict[str, Any]:
    """Enable COMSOL's native axisymmetric measure and verify its readback.

    A geometry ``isAxisymmetric()`` flag alone does not prove that a numerical
    feature is integrating the revolved measure.  The operation is therefore
    refused when the native property cannot be set and read back as ``on``.
    """
    try:
        properties = {str(item) for item in _call(feature, "properties")}
    except Exception as exc:
        raise ExecutionContractError(
            "AXISYMMETRY_READBACK_UNAVAILABLE",
            f"{role} numerical feature properties could not be read",
        ) from exc
    property_name = next(
        (name for name in ("intvolume", "intsurface") if name in properties),
        None,
    )
    if property_name is None:
        raise ExecutionContractError(
            "AXISYMMETRY_READBACK_UNAVAILABLE",
            f"{role} numerical feature exposes neither intvolume nor intsurface",
        )
    try:
        _call(feature, "set", property_name, "on")
    except Exception as exc:
        raise ExecutionContractError(
            "AXISYMMETRY_READBACK_UNAVAILABLE",
            f"{role} could not enable native {property_name}=on",
        ) from exc
    try:
        readback = _call(feature, "getString", property_name)
    except Exception as exc:
        raise ExecutionContractError(
            "AXISYMMETRY_READBACK_UNAVAILABLE",
            f"{role} native {property_name} readback is unavailable",
        ) from exc
    enabled = (
        readback is True
        or str(readback).strip().lower() in {"on", "true", "1"}
    )
    if not enabled:
        raise ExecutionContractError(
            "AXISYMMETRY_READBACK_MISMATCH",
            f"{role} native {property_name} readback is {readback!r}, expected 'on'",
        )
    return {
        "role": role,
        "native_property": property_name,
        "requested_value": "on",
        "readback_value": readback,
        "readback_verified": True,
        "manual_radial_weighting": False,
        "source": "NumericalFeature.set + getString",
    }


def _length_scale_to_geometry(input_unit: str, geometry_unit: Any) -> float:
    """Convert an input coordinate magnitude into COMSOL geometry units."""
    factors = {
        "m": 1.0,
        "cm": 1.0e-2,
        "mm": 1.0e-3,
        "um": 1.0e-6,
        "µm": 1.0e-6,
        "nm": 1.0e-9,
    }
    source = str(input_unit).strip()
    target = str(geometry_unit or "").strip()
    if source not in factors:
        raise ExecutionContractError("API_UNSUPPORTED", f"coordinate_unit {input_unit!r} not supported")
    if target not in factors:
        raise ExecutionContractError(
            "AXIS_METADATA_UNAVAILABLE",
            f"geometry length unit {geometry_unit!r} is unavailable or unsupported",
        )
    return factors[source] / factors[target]


def _feature_point_count(data: Any, layout: str) -> int:
    """Read the point axis length from a documented numerical result layout."""
    if not layout.endswith("point"):
        return 1
    rows = list(data) if isinstance(data, Sequence) and not isinstance(data, (str, bytes)) else [data]
    if not rows:
        raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", "numerical feature returned no expression rows")
    row = rows[0]
    if layout.startswith("expression,outer"):
        outer_rows = list(row) if isinstance(row, Sequence) and not isinstance(row, (str, bytes)) else [row]
        if not outer_rows:
            raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", "numerical feature returned no outer rows")
        inner_rows = list(outer_rows[0]) if isinstance(outer_rows[0], Sequence) and not isinstance(outer_rows[0], (str, bytes)) else [outer_rows[0]]
        if not inner_rows:
            raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", "numerical feature returned no inner rows")
        point_values = inner_rows[0]
    else:
        inner_rows = list(row) if isinstance(row, Sequence) and not isinstance(row, (str, bytes)) else [row]
        if not inner_rows:
            raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", "numerical feature returned no solution rows")
        point_values = inner_rows[0]
    if not isinstance(point_values, Sequence) or isinstance(point_values, (str, bytes)):
        return 1
    return len(point_values)


def _result_solution_binding(model: Any, solution_tag: Any) -> dict[str, Any] | None:
    """Return a typed SolutionInfo binding when the selected solution publishes it."""
    if not isinstance(solution_tag, str) or not solution_tag:
        return None
    try:
        from ._solution_binding import SolutionBinding
        sol_list = _call(model, "sol")
        if solution_tag not in tag_list(sol_list):
            raise ExecutionContractError("NODE_NOT_FOUND", f"solution {solution_tag!r} is not present in model.sol()")
        sol_node = _call(sol_list, "get", solution_tag)
        info = _call(sol_node, "getSolutioninfo")
        if not callable(getattr(info, "getSolnum", None)):
            return None
        return SolutionBinding.resolve_solution_info(info)
    except ExecutionContractError as exc:
        if exc.code in {"API_UNSUPPORTED", "NODE_NOT_FOUND"}:
            return None
        raise


def _merge_outer_feature_data(
    rows: Sequence[tuple[Any, Any, str]],
    binding: Mapping[str, Any],
    *,
    num_expressions: int,
    layout: str,
) -> tuple[Any, Any]:
    """Combine one explicitly selected numerical result per outer level.

    The engine solnum axis is scoped to the outer selected through
    ``outersolnum``.  This routine therefore indexes rows by their position in
    each outer's inner list, never by a global solnum dictionary.
    """
    outer_labels = list(binding.get("outer_indices") or [])
    inner_by_outer = binding.get("inner_indices_by_outer") or {}
    if len(rows) != len(outer_labels):
        raise ExecutionContractError("SOLUTION_AXIS_ERROR", "one numerical read is required for every outer level")
    real_out: list[list[list[list[Any]]]] = [[] for _ in range(num_expressions)]
    imag_out: list[list[list[list[Any]]]] | None = None
    if any(item[1] is not None for item in rows):
        imag_out = [[] for _ in range(num_expressions)]
    for outer_index, (real, imag, row_layout) in enumerate(rows):
        if row_layout != layout:
            raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", "feature layout changed between outer reads")
        raw = list(real) if isinstance(real, Sequence) and not isinstance(real, (str, bytes)) else [real]
        if len(raw) != num_expressions:
            raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", f"expected {num_expressions} expression rows, got {len(raw)}")
        raw_imag = None
        if imag is not None:
            raw_imag = list(imag) if isinstance(imag, Sequence) and not isinstance(imag, (str, bytes)) else [imag]
            if len(raw_imag) != num_expressions:
                raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", "imaginary expression count differs from real data")
        outer = int(outer_labels[outer_index])
        inner_values = list(inner_by_outer.get(outer, []))
        if not inner_values:
            raise ExecutionContractError("SOLUTION_AXIS_METADATA_UNAVAILABLE", f"outer {outer} has no inner metadata")
        for expr_index in range(num_expressions):
            expr_rows = list(raw[expr_index]) if isinstance(raw[expr_index], Sequence) and not isinstance(raw[expr_index], (str, bytes)) else [raw[expr_index]]
            if len(expr_rows) != len(inner_values):
                raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", f"outer {outer} expression {expr_index} has {len(expr_rows)} inner rows, expected {len(inner_values)}")
            points = [list(value) if row_layout.endswith("point") and isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else [value] for value in expr_rows]
            real_out[expr_index].append(points)
            if imag_out is not None:
                if raw_imag is None:
                    raise ExecutionContractError("COMPLEX_DATA_ERROR", "imaginary data missing for one outer level")
                imag_rows = list(raw_imag[expr_index]) if isinstance(raw_imag[expr_index], Sequence) and not isinstance(raw_imag[expr_index], (str, bytes)) else [raw_imag[expr_index]]
                if len(imag_rows) != len(inner_values):
                    raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", "imaginary inner rows differ from real rows")
                imag_out[expr_index].append([
                    list(value) if row_layout.endswith("point") and isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else [value]
                    for value in imag_rows
                ])
    return real_out, imag_out


def _nested_divide(value: Any, denominator: Any) -> Any:
    """Elementwise division with explicit scalar/array broadcasting."""
    if isinstance(value, Mapping):
        return {key: _nested_divide(item, denominator) for key, item in value.items()}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(denominator, (int, float)) and not isinstance(denominator, bool):
            return float(value) / float(denominator)
        raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", "scalar numerator cannot be divided by an array denominator")
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if isinstance(denominator, Sequence) and not isinstance(denominator, (str, bytes)):
            if len(denominator) == 1 and len(value) != 1:
                # A measure feature is intentionally evaluated with one
                # expression (the weight/constant 1); broadcast that explicit
                # expression axis across every requested field expression.
                return [_nested_divide(item, denominator[0]) for item in value]
            if len(value) != len(denominator):
                raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", "numerator and denominator axes differ")
            return [_nested_divide(v, d) for v, d in zip(value, denominator)]
        return [_nested_divide(v, denominator) for v in value]
    raise ExecutionContractError("INVALID_RESULT", f"cannot divide value of type {type(value).__name__}")


def _nested_magnitude_squared(value: Any) -> Any:
    if isinstance(value, Mapping):
        if "real" not in value or "imag" not in value:
            raise ExecutionContractError("COMPLEX_DATA_ERROR", "preserved complex value lacks real/imag components")
        return float(value["real"]) ** 2 + float(value["imag"]) ** 2
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) ** 2
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_nested_magnitude_squared(item) for item in value]
    raise ExecutionContractError("INVALID_RESULT", f"cannot compute magnitude of {type(value).__name__}")


def _nested_subtract(a: Any, b: Any) -> Any:
    if isinstance(a, Mapping):
        if isinstance(b, Mapping):
            return {key: _nested_subtract(a[key], b[key]) for key in ("real", "imag")}
        return {"real": _nested_subtract(a["real"], b), "imag": a["imag"]}
    if isinstance(a, (int, float)) and not isinstance(a, bool):
        if isinstance(b, (int, float)) and not isinstance(b, bool):
            return float(a) - float(b)
        raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", "mean and field values have different types")
    if isinstance(a, Sequence) and not isinstance(a, (str, bytes)):
        if isinstance(b, Sequence) and not isinstance(b, (str, bytes)):
            if len(a) != len(b):
                raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", "mean and field axes differ")
            return [_nested_subtract(x, y) for x, y in zip(a, b)]
        return [_nested_subtract(x, b) for x in a]
    raise ExecutionContractError("INVALID_RESULT", "cannot subtract non-numeric result")


def _nested_binary(a: Any, b: Any, operation: Any) -> Any:
    if isinstance(a, Mapping) or isinstance(b, Mapping):
        return operation(a, b)
    if isinstance(a, (int, float)) and not isinstance(a, bool):
        if isinstance(b, (int, float)) and not isinstance(b, bool):
            return operation(float(a), float(b))
        raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", "numeric axes have different types")
    if isinstance(a, Sequence) and not isinstance(a, (str, bytes)):
        if isinstance(b, Sequence) and not isinstance(b, (str, bytes)):
            if len(a) != len(b):
                raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", "numeric axes have different lengths")
            return [_nested_binary(x, y, operation) for x, y in zip(a, b)]
        return [_nested_binary(x, b, operation) for x in a]
    raise ExecutionContractError("INVALID_RESULT", "non-numeric array value")


def _nested_sqrt(value: Any) -> Any:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if not math.isfinite(number) or number < -1e-12:
            raise ExecutionContractError("INVALID_RESULT", f"square-root argument is invalid: {number!r}")
        return math.sqrt(0.0 if number < 0.0 else number)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_nested_sqrt(item) for item in value]
    raise ExecutionContractError("INVALID_RESULT", "square-root argument is not numeric")


def _nested_positive(value: Any) -> bool:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return math.isfinite(float(value)) and float(value) > 0.0
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return bool(value) and all(_nested_positive(item) for item in value)
    return False


def _run_bound_feature(
    feature: Any,
    binding: Mapping[str, Any] | None,
    *,
    num_expressions: int,
    point_feature: bool = False,
    budget_guard: Any | None = None,
    outer_getters: bool = False,
) -> tuple[Any, Any, bool, str]:
    """Run a feature once per typed outer and return explicit layout data."""
    if binding and len(binding.get("outer_indices", [])) > 1:
        rows: list[tuple[Any, Any, str]] = []
        statuses: list[bool] = []
        for outer in binding["outer_indices"]:
            try:
                _call(feature, "set", "outersolnum", int(outer))
            except Exception as exc:
                raise ExecutionContractError("SOLUTION_SELECTION_FAILED", f"could not select outer solution {outer}") from exc
            _call(feature, "run")
            budget_decision = budget_guard(feature, int(outer)) if budget_guard is not None else None
            real, imag, status, layout = _feature_components(
                feature,
                budget_decision=budget_decision,
                outer=int(outer),
                use_outer_getters=outer_getters,
            )
            if point_feature:
                real, imag, _ = _normalise_evalpoint_components(
                    real, imag, num_expressions=num_expressions
                )
                layout = "expression,solnum,point"
            rows.append((real, imag, layout))
            statuses.append(status)
        layout = rows[0][2]
        real, imag = _merge_outer_feature_data(rows, binding, num_expressions=num_expressions, layout=layout)
        if len(set(statuses)) != 1:
            raise ExecutionContractError("COMPLEX_STATUS_UNAVAILABLE", "complex status changed between outer solutions")
        return real, imag, bool(statuses[0]), "expression,outer,inner,point"
    if binding and binding.get("outer_indices"):
        outer = int(binding["outer_indices"][0])
        if outer != 1:
            try:
                _call(feature, "set", "outersolnum", outer)
            except Exception as exc:
                raise ExecutionContractError("SOLUTION_SELECTION_FAILED", f"could not select outer solution {outer}") from exc
    _call(feature, "run")
    outer_label = None
    if binding and binding.get("outer_indices"):
        outer_label = int(binding["outer_indices"][0])
    budget_decision = budget_guard(feature, outer_label) if budget_guard is not None else None
    real, imag, status, layout = _feature_components(
        feature,
        budget_decision=budget_decision,
        outer=outer_label,
        use_outer_getters=outer_getters,
    )
    if point_feature:
        real, imag, _ = _normalise_evalpoint_components(
            real, imag, num_expressions=num_expressions
        )
        layout = "expression,solnum,point"
    return real, imag, status, layout



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
    allow_overwrite: bool = False,
) -> dict[str, Any]:
    """Publish a large result payload as a project-scoped artifact.

    The destination root is an explicit trusted backend/Worker context.  A dataset
    label is retained as metadata, while the generated filename is opaque so a
    label can never introduce path separators.  The store refuses failed/unknown
    outcomes and publishes a completed, hashed temporary file atomically.
    """
    store = ArtifactStore(project_root=project_root)
    file_name = f"result_{uuid.uuid4().hex}.json"
    export = store.export_field_data(
        f"g2_artifacts/results/{file_name}",
        {"values": data, **(dict(eval_context or {}))},
        "json",
        allow_overwrite=allow_overwrite,
    )
    return {
        "artifact_ref": export["file_path"],
        "sha256": export["sha256"],
        "byte_size": export["byte_size"],
        "total_elements": _count_elements(data),
        "storage": "artifact",
        "chunk_info": export["chunk_info"],
    }


def verify_artifact_chunks(
    file_path: str,
    chunk_size: int = 1024 * 64,
    expected_sha256: str | None = None,
) -> tuple[bool, str]:
    """Verify a file by streaming it in chunks, without ever holding the whole file.

    The delivered version read the file twice with ``read_bytes`` and then joined a
    list of all chunks in memory -- exactly the shape NEXT_GOAL refuses to call
    chunked.  It now folds chunks into a rolling digest with a buffer of one chunk,
    and it compares against a *pinned* digest when the caller supplies one; the
    ``expected_sha256`` argument is the difference between a real verification and
    hashing a file twice and noticing the two hashes agree.
    """
    p = Path(file_path)
    if not p.is_file():
        return False, f"file not found: {file_path}"
    size = p.stat().st_size

    digest = hashlib.sha256()
    total = 0
    with p.open("rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digest.update(block)
            total += len(block)
    actual_hash = digest.hexdigest()

    if total != size:
        return False, actual_hash
    if expected_sha256 is not None:
        return (actual_hash == str(expected_sha256).lower()), actual_hash
    return True, actual_hash


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

    requested_transform_order = spec.get("complex_transform_order")
    if requested_transform_order is None:
        # A non-aggregated field is transformed after the engine read.  Every
        # aggregate, including a weighted aggregate, defaults to the native
        # scalar comparison field so mean/variance/RMS never mix arg(f) with
        # f or abs(f) with abs(mean(f)).
        requested_transform_order = (
            "before"
            if aggregate != "none" or spec.get("weight_expression") is not None
            else "after"
        )
    if requested_transform_order not in {"before", "after"}:
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            f"complex_transform_order {requested_transform_order!r} is not supported; valid: ['before', 'after']",
        )
    if aggregate in {"minimum", "maximum"} and complex_mode == "preserve":
        raise ExecutionContractError(
            "COMPLEX_ORDER_UNDEFINED",
            "complex extrema require complex_mode=real, imag, abs, or phase so the comparison quantity is explicit",
        )
    if (
        requested_transform_order == "after"
        and complex_mode != "preserve"
        and aggregate in {"minimum", "maximum", "std", "rms"}
    ):
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            f"complex_transform_order='after' is undefined for aggregate={aggregate!r} and complex_mode={complex_mode!r}; "
            "use the default before-statistics order",
        )
    transform_before_statistics = requested_transform_order == "before"
    effective_expressions = _effective_engine_expressions(
        expressions,
        complex_mode,
        before_statistics=transform_before_statistics,
    )

    # Outer/inner are real axis selectors.  Time/frequency/parameter matching
    # stays an explicit refusal until the corresponding SolutionInfo metadata
    # is available; silently ignoring one of these fields would evaluate a
    # different solution than the request names.
    for name in ("time", "frequency", "parameters"):
        if solution_spec.get(name) is not None or spec.get(name) is not None:
            raise ExecutionContractError(
                "API_UNSUPPORTED",
                f"spec.solution.{name} matching is unavailable for this operation; request refused",
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
    requested_solution = solution_spec.get("solution") if isinstance(solution_spec.get("solution"), str) else None
    dataset_binding = _resolve_dataset_binding(
        model,
        str(dataset_tag),
        requested_solution=requested_solution,
    )
    if (
        isinstance(dataset_binding, Mapping)
        and isinstance(dataset_binding.get("error"), Mapping)
        and dataset_binding["error"].get("code") == "SOLUTION_MISMATCH"
    ):
        raise ExecutionContractError(
            "SOLUTION_MISMATCH",
            str(dataset_binding["error"].get("message", "requested solution does not match dataset")),
            details={"dataset_binding": dict(dataset_binding)},
        )
    # COMSOL 6.4 rejects a raw Eval numerical feature when its data dataset is
    # Join.  Refuse before creating any numerical node; the supported Join
    # paths are Interp point evaluation and the native Av*/Int* aggregate
    # features.  Falling back to an upstream dataset would silently change the
    # meaning of the requested difference/average.
    dataset_type = dataset_binding.get("dataset_type") if isinstance(dataset_binding, Mapping) else None
    if dataset_type is None:
        try:
            dataset_type = _call(dset_node, "getType")
        except Exception:
            dataset_type = None
    if dataset_type == "Join" and aggregate == "none":
        raise PreWriteRefusal(
            "API_UNSUPPORTED",
            "raw result.evaluate with aggregate='none' is unsupported for Join datasets; "
            "use result.at_points or a supported spatial aggregate",
            details={"dataset": dataset_tag, "dataset_type": "Join", "mutation_issued": False},
        )
    solution_tag = (
        solution_spec.get("solution")
        or (dataset_binding.get("solution") if isinstance(dataset_binding, Mapping) else None)
        or _string_or_none(dset_node, "solution", [])
        or _string_or_none(dset_node, "data", [])
    )
    solution_binding = _result_solution_binding(model, solution_tag)
    if solution_spec.get("outer") is not None and not solution_binding:
        raise ExecutionContractError(
            "SOLUTION_AXIS_METADATA_UNAVAILABLE",
            "outer selection requires SolutionInfo.getSolnum(outer, strict) metadata",
        )
    if solution_binding and not solution_binding.get("pair_mapping_complete") and (
        solution_spec.get("outer") is not None or solution_spec.get("inner") is not None
    ):
        raise ExecutionContractError(
            "SOLUTION_AXIS_METADATA_UNAVAILABLE",
            "outer/inner selection requires a complete real SolutionInfo pair mapping",
        )

    # Check spatial dimension & axisymmetry
    context = _coordinate_context(model, dset_node, [], dataset_binding=dataset_binding)
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
    cleanup_records: list[dict[str, Any]] = [cleanup]

    def _new_cleanup_record(tag: str, type_id: str) -> dict[str, Any]:
        record = {
            "tag": tag,
            "type_id": type_id,
            "created": False,
            "removed": False,
            "cleanup_failed": False,
            "error": None,
        }
        cleanup_records.append(record)
        return record

    feature = None
    engine_error = None
    transformed = None
    is_complex: bool | None = None
    field_array_payload: Any = None
    pending_field_selectors: dict[str, Any] = {}
    raw_layout = "expression,solnum,point"
    expression_units: dict[str, Any] = {}
    result_budget_records: list[dict[str, Any]] = []
    result_budget_guard = None
    field_array_is_complex = False
    axisymmetric_measure_evidence: list[dict[str, Any]] = []
    # §3: reported in every outcome, including the failure path, so the response
    # never depends on how far the aggregate block got before an error.
    denominator_measure = None
    cross_section_measure = None
    denominator_source = None
    # §3/C05: measured by the empty-selection guard below; pre-initialized because the
    # response is built on the failure path too.
    selection_measure = None
    selection_measure_source = None
    selection_measure_error = None

    try:
        feature = _call(numerical_list, "create", ephemeral_tag, feat_type)
        cleanup["created"] = True

        _call(feature, "set", "data", dataset_tag)
        _call(feature, "set", "expr", effective_expressions)
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

        axisymmetric_measure_required = is_axisymmetric and aggregate in (
            "integral", "average", "std", "rms"
        )
        if axisymmetric_measure_required:
            axisymmetric_measure_evidence.append(
                _axisymmetric_measure_readback(feature, role="aggregate")
            )

        # Raw aggregate=none Eval reads are the only result path here that can
        # expose the full [expression][solnum][vertex] payload.  Admit each
        # outer read only after the typed solution axes and the native
        # shape-only point witness have been verified.  Aggregate features and
        # EvalPoint remain on their scalar/counting paths.
        if aggregate == "none" and feat_type == "Eval":
            result_budget_guard = _make_result_budget_guard(
                operation="result.evaluate",
                feature_kind="Eval",
                expressions=expressions,
                binding=solution_binding,
                records=result_budget_records,
            )

        # A numerical feature is scoped to one outer solution.  The helper
        # selects each real outer label and preserves the feature's documented
        # local inner axis; EvalPoint additionally normalises its native
        # expression*point rows.
        raw_real, raw_imag, is_complex, raw_layout = _run_bound_feature(
            feature,
            solution_binding,
            num_expressions=len(expressions),
            point_feature=ms.entity_dim == 0,
            budget_guard=result_budget_guard,
            outer_getters=feat_type not in {"Eval", "Interp", "EvalGlobal"},
        )
        expression_units = _feature_units(feature, expressions)

        if transform_before_statistics and complex_mode != "preserve":
            if is_complex:
                raise ExecutionContractError(
                    "COMPLEX_DATA_ERROR",
                    "a pre-statistics real/imag/abs/phase expression still reported complex data",
                )
            transformed = raw_real
        else:
            transformed = _transform_complex_data(raw_real, raw_imag, complex_mode, is_complex=is_complex)
        if ms.entity_dim == 0:
            point_operation = None
            if aggregate in ("integral", "average", "std", "rms") or weight_expression:
                point_operation = "sum"
            elif aggregate in ("minimum", "maximum"):
                point_operation = aggregate
            if point_operation is not None:
                transformed = _point_reduce(transformed, point_operation)
        if solution_binding:
            from ._solution_binding import FieldArray
            point_axis_count = _feature_point_count(transformed, raw_layout)
            aggregate_point_axis = aggregate != "none" or bool(weight_expression)
            field_coords, field_units, field_metadata = _field_array_context(
                solution_binding,
                expressions,
                expression_units=expression_units,
                length_unit=("index" if aggregate_point_axis else context.get("length_unit")),
                point_count=point_axis_count,
                point_coordinate_source=("aggregate" if aggregate_point_axis else "numerical-feature"),
            )
            field_metadata.update({
                "requested_expressions": list(expressions),
                "evaluated_expressions": list(effective_expressions),
                "complex_transform_order": requested_transform_order,
            })
            field_array_is_complex = _field_array_is_complex(
                is_complex, complex_mode, aggregate
            )
            if raw_layout.startswith("expression,outer"):
                field_array_payload = FieldArray(
                    transformed,
                    axes=("expression", "outer", "inner", "point"),
                    coords=field_coords,
                    units=field_units,
                    metadata=field_metadata,
                    is_complex=field_array_is_complex,
                )
            else:
                # One-outer engine reads retain a scoped solnum axis.  Map it
                # with the selected outer label; never flatten duplicate inner
                # numbers from another outer into a global dictionary.
                from ._solution_binding import SolutionBinding
                selected_outer = (solution_binding.get("outer_indices") or [None])[0]
                field_array_payload = SolutionBinding.field_array_from_engine(
                    transformed,
                    solution_binding,
                    num_expressions=len(expressions),
                    layout=raw_layout,
                    selected_outer=selected_outer,
                    coords=field_coords,
                    units=field_units,
                    metadata=field_metadata,
                    is_complex=field_array_is_complex,
                )

            if field_array_payload is not None:
                selectors: dict[str, Any] = {}
                if solution_spec.get("outer") is not None:
                    selectors["outer"] = solution_spec["outer"]
                if solution_spec.get("inner") is not None:
                    selectors["inner"] = solution_spec["inner"]
                # Keep the full pair grid through measure/statistics.  In
                # particular, centered variance must use the mean belonging
                # to each real (outer, inner) pair before a response slice is
                # applied.  The selectors are applied after all aggregates.
                pending_field_selectors = selectors
                transformed = field_array_payload.values

        # Denominator measure and statistical calculation (F02, F03)
        def _to_float(v: Any) -> float:
            if isinstance(v, Mapping):
                if set(v) >= {"real", "imag"} and float(v.get("imag", 0.0)) != 0.0:
                    raise ExecutionContractError("INVALID_RESULT", "a complex aggregate cannot be reduced to one real measure")
                v = v.get("real")
            if isinstance(v, (list, tuple)):
                if len(v) != 1:
                    raise ExecutionContractError(
                        "FIELD_ARRAY_SHAPE_MISMATCH",
                        "a multi-expression or multi-solution result requires per-axis handling; only a fully singleton measure may be scalarised",
                    )
                # This is a proof of a singleton expression/solution/point
                # result, not a first-element fallback.  Any non-singleton
                # axis remains an explicit shape error above.
                return _to_float(v[0])
            if v is None:
                raise ExecutionContractError("INVALID_RESULT", "numeric result is missing")
            try:
                number = float(v)
            except (TypeError, ValueError) as exc:
                raise ExecutionContractError("INVALID_RESULT", f"numeric result is not real: {v!r}") from exc
            if not math.isfinite(number):
                raise ExecutionContractError("INVALID_RESULT", f"numeric result is not finite: {number!r}")
            return number

        # ------------------------------------------------------------------
        # §3/C05: an explicitly pinned selection that matches no entity is refused.
        # COMSOL answers an out-of-range entity index with a healthy status and an empty
        # aggregate, so an integral over domain 99 of a two-interval geometry came back as
        # 0.0 with ``ok: true`` -- a vacuous success instead of a measure, and exactly the
        # class of silent fallback this contract refuses.  The measure of the pinned
        # selection is read from the engine and a zero measure fails the call; the
        # statistical aggregates below already read their own measure and refuse zero.
        selection_measure: float | None = None
        selection_measure_source: str | None = None
        selection_measure_error: str | None = None
        selection = ms.selection
        _pinned_selection = selection not in (None, "all") and not (
            isinstance(selection, Mapping)
            and bool(selection.get("all"))
            and selection.get("kind") in (None, "all")
            and "entities" not in selection
        ) and not (
            isinstance(selection, Mapping)
            and selection.get("kind") == "all"
            and "entities" not in selection
        )
        if _pinned_selection and ms.entity_dim >= 0 and aggregate in ("integral", "maximum", "minimum"):
            guard_tag = f"{ephemeral_tag}_selmeasure"
            guard_feat = None
            guard_cleanup = _new_cleanup_record(guard_tag, ms.integral_feature_type)
            try:
                guard_feat = _call(numerical_list, "create", guard_tag, ms.integral_feature_type)
                guard_cleanup["created"] = True
                _call(guard_feat, "set", "data", dataset_tag)
                _call(guard_feat, "set", "expr", ["1"])
                ms.apply_selection(guard_feat)
                if axisymmetric_measure_required:
                    axisymmetric_measure_evidence.append(
                        _axisymmetric_measure_readback(
                            guard_feat, role="selection_guard"
                        )
                    )
                if ms.entity_dim == 0:
                    guard_real, guard_imag, guard_status, guard_layout = _run_bound_feature(
                        guard_feat,
                        solution_binding,
                        num_expressions=1,
                        point_feature=True,
                        outer_getters=True,
                    )
                    if guard_status:
                        raise ExecutionContractError("COMPLEX_DATA_ERROR", "point counting measure returned complex data")
                    guard_field_array = None
                    if solution_binding:
                        from ._solution_binding import SolutionBinding
                        guard_field_array = SolutionBinding.field_array_from_engine(
                            _point_reduce(guard_real, "sum"),
                            solution_binding,
                            num_expressions=1,
                            layout=guard_layout,
                            selected_outer=(solution_binding.get("outer_indices") or [None])[0],
                            is_complex=False,
                        )
                        selection_measure = guard_field_array.values
                    else:
                        selection_measure = _to_float(_point_reduce(guard_real, "sum"))
                else:
                    guard_real, guard_imag, guard_status, guard_layout = _run_bound_feature(
                        guard_feat,
                        solution_binding,
                        num_expressions=1,
                        outer_getters=True,
                    )
                    if guard_status:
                        raise ExecutionContractError("COMPLEX_DATA_ERROR", "selection measure returned complex data")
                    guard_field_array = None
                    if solution_binding:
                        from ._solution_binding import SolutionBinding
                        guard_field_array = SolutionBinding.field_array_from_engine(
                            guard_real,
                            solution_binding,
                            num_expressions=1,
                            layout=guard_layout,
                            selected_outer=(solution_binding.get("outer_indices") or [None])[0],
                            is_complex=False,
                        )
                        selection_measure = guard_field_array.values
                    else:
                        selection_measure = _to_float(guard_real)
                if guard_field_array is not None:
                    guard_selectors: dict[str, Any] = {}
                    if solution_spec.get("outer") is not None:
                        guard_selectors["outer"] = solution_spec["outer"]
                    if solution_spec.get("inner") is not None:
                        guard_selectors["inner"] = solution_spec["inner"]
                    if guard_selectors:
                        selection_measure = guard_field_array.select(**guard_selectors).values
                selection_measure_source = (
                    "engine integral of 1 over the pinned selection "
                    f"({ms.integral_feature_type})"
                )
            except Exception as exc:
                selection_measure_error = f"{type(exc).__name__}: {exc}"
            finally:
                if guard_cleanup["created"] and not guard_cleanup["removed"]:
                    _remove_ephemeral(numerical_list, guard_tag, guard_cleanup, [])
            if (
                selection_measure is None
                or not _nested_positive(selection_measure)
            ):
                raise ExecutionContractError(
                    "SELECTION_MATCHED_NO_ENTITIES",
                    f"selection {selection!r} matches no entity of the dataset's geometry: the "
                    f"engine measure of that selection is {selection_measure!r} "
                    f"(read error {selection_measure_error!r}), so the aggregate would be a "
                    f"vacuous 0 rather than a measure",
                    details={
                        "selection": selection,
                        "aggregate": aggregate,
                        "entity_dim": ms.entity_dim,
                        "selection_measure": selection_measure,
                        "selection_measure_error": selection_measure_error,
                    },
                )

        if aggregate in ("average", "std", "rms") or weight_expression:
            meas_tag = f"{ephemeral_tag}_meas"
            meas_feat = None
            meas_cleanup = _new_cleanup_record(meas_tag, ms.integral_feature_type)
            m_raw = None
            m_imag = None
            m_status: bool | None = None
            m_layout = "expression,solnum,point"
            m_value: Any = None
            m_read_error: str | None = None
            # §3: the measure integrand is w for a weighted aggregate, 1 otherwise.
            measure_expr = [weight_expression] if weight_expression else ["1"]
            try:
                meas_feat = _call(numerical_list, "create", meas_tag, ms.integral_feature_type)
                meas_cleanup["created"] = True
                _call(meas_feat, "set", "data", dataset_tag)
                _call(meas_feat, "set", "expr", measure_expr)
                ms.apply_selection(meas_feat)
                if axisymmetric_measure_required:
                    axisymmetric_measure_evidence.append(
                        _axisymmetric_measure_readback(
                            meas_feat, role="denominator_measure"
                        )
                    )
                m_raw, m_imag, m_status, m_layout = _run_bound_feature(
                    meas_feat,
                    solution_binding,
                    num_expressions=1,
                    point_feature=ms.entity_dim == 0,
                    outer_getters=True,
                )
                if m_status:
                    raise ExecutionContractError("COMPLEX_DATA_ERROR", "measure denominator is unexpectedly complex")
                if solution_binding:
                    from ._solution_binding import SolutionBinding
                    m_value = SolutionBinding.field_array_from_engine(
                        m_raw,
                        solution_binding,
                        num_expressions=1,
                        layout=m_layout,
                        selected_outer=(solution_binding.get("outer_indices") or [None])[0],
                        is_complex=False,
                    ).values
                else:
                    m_value = m_raw
                if ms.entity_dim == 0:
                    m_value = _point_reduce(m_value, "sum")
            except Exception as exc:
                m_raw = None
                m_value = None
                m_read_error = f"{type(exc).__name__}: {exc}"

            if is_axisymmetric and meas_feat is not None:
                try:
                    props = list(_call(meas_feat, "properties"))
                    native_property = next(
                        (name for name in ("intvolume", "intsurface") if name in props),
                        None,
                    )
                    if native_property is not None:
                        _call(meas_feat, "set", native_property, "off")
                        _call(meas_feat, "run")
                        try:
                            cs_raw = _call(meas_feat, "getData")
                        except Exception:
                            cs_raw = _call(meas_feat, "getReal")
                        cross_section_measure = _to_float(cs_raw)
                        _call(meas_feat, "set", native_property, "on")
                        axisymmetric_measure_evidence.append(
                            _axisymmetric_measure_readback(
                                meas_feat, role="denominator_measure_restore"
                            )
                        )
                        _call(meas_feat, "run")
                except Exception:
                    pass

            m_val = m_value
            if m_val is None or not _nested_positive(m_val):
                # §3: the denominator is M = ∫w dμ read from the engine.  A failed
                # read is reported and the operation fails; it is never replaced
                # by 1.0 or by a caller-supplied constant.
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
            num_cleanup = _new_cleanup_record(num_tag, ms.integral_feature_type)
            num_layout = "expression,solnum,point"
            if weight_expression:
                try:
                    num_feat = _call(numerical_list, "create", num_tag, ms.integral_feature_type)
                    num_cleanup["created"] = True
                    _call(num_feat, "set", "data", dataset_tag)
                    _call(
                        num_feat,
                        "set",
                        "expr",
                        [f"({weight_expression})*({e})" for e in effective_expressions],
                    )
                    ms.apply_selection(num_feat)
                    if axisymmetric_measure_required:
                        axisymmetric_measure_evidence.append(
                            _axisymmetric_measure_readback(
                                num_feat, role="weighted_numerator"
                            )
                        )
                    num_real, num_imag, num_status, num_layout = _run_bound_feature(
                        num_feat,
                        solution_binding,
                        num_expressions=len(expressions),
                        point_feature=ms.entity_dim == 0,
                        outer_getters=True,
                    )
                    if transform_before_statistics and complex_mode != "preserve":
                        if num_status:
                            raise ExecutionContractError(
                                "COMPLEX_DATA_ERROR",
                                "a weighted pre-statistics transform still reported complex data",
                            )
                        num_transformed = num_real
                    else:
                        num_transformed = _transform_complex_data(
                            num_real, num_imag, complex_mode, is_complex=num_status
                        )
                    if ms.entity_dim == 0:
                        num_transformed = _point_reduce(num_transformed, "sum")
                    if solution_binding:
                        from ._solution_binding import SolutionBinding
                        transformed = SolutionBinding.field_array_from_engine(
                            num_transformed,
                            solution_binding,
                            num_expressions=len(expressions),
                            layout=num_layout,
                            selected_outer=(solution_binding.get("outer_indices") or [None])[0],
                            is_complex=num_status,
                        ).values
                    else:
                        transformed = num_transformed
                except Exception as exc:
                    raise ExecutionContractError(
                        "ENGINE_CALL_FAILED",
                        f"the weighted numerator ∫w·f dμ could not be evaluated: "
                        f"{type(exc).__name__}: {exc}",
                    )

            def _divide_by_measure(value: Any) -> Any:
                return _nested_divide(value, denominator_measure)

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
                if aggregate_feature is None:
                    raise ExecutionContractError(
                        "ZERO_OR_INVALID_MEASURE",
                        "the centered variance integral needs the measure feature, which is "
                        "not available",
                    )
                if feat_type.startswith("Av") and not weight_expression:
                    mean_value = transformed
                else:
                    mean_value = _divide_by_measure(transformed)

                def _mean_for_cell(expression_index: int, outer_position: int | None, inner_position: int | None) -> Any:
                    if solution_binding is None:
                        rows = list(mean_value) if isinstance(mean_value, Sequence) and not isinstance(mean_value, (str, bytes)) else [mean_value]
                        if len(rows) == len(expressions):
                            return _strict_singleton_value(
                                rows[expression_index],
                                label=f"mean[expression={expression_index}]",
                            )
                        if len(expressions) == 1:
                            return _strict_singleton_value(mean_value, label="mean")
                        raise ExecutionContractError(
                            "FIELD_ARRAY_SHAPE_MISMATCH",
                            "unbound multi-expression mean has no explicit expression axis",
                        )
                    if outer_position is None or inner_position is None:
                        raise ExecutionContractError("SOLUTION_AXIS_ERROR", "a bound mean needs outer and inner positions")
                    try:
                        cell = mean_value[expression_index][outer_position][inner_position]
                    except (IndexError, KeyError, TypeError) as exc:
                        raise ExecutionContractError(
                            "FIELD_ARRAY_SHAPE_MISMATCH",
                            f"mean has no cell for expression={expression_index}, outer={outer_position}, inner={inner_position}",
                        ) from exc
                    return _strict_singleton_value(
                        cell,
                        label=f"mean[expression={expression_index},outer={outer_position},inner={inner_position}]",
                    )

                def _centered_exprs(outer_position: int | None, inner_position: int | None) -> list[str]:
                    result: list[str] = []
                    for expression_index, expression in enumerate(effective_expressions):
                        mean_cell = _mean_for_cell(expression_index, outer_position, inner_position)
                        unit = expression_units.get(expressions[expression_index])
                        centered = _centered_square_expression(expression, mean_cell, unit=unit)
                        if weight_expression:
                            centered = f"({weight_expression})*({centered})"
                        result.append(centered)
                    return result

                try:
                    if solution_binding:
                        outer_labels = list(solution_binding.get("outer_indices") or [])
                        inner_by_outer = solution_binding.get("inner_indices_by_outer") or {}
                        pair_by_key = {
                            (int(pair["outer"]), int(pair["inner"])): pair
                            for pair in solution_binding.get("solnum_pairs") or []
                        }
                        if not outer_labels or not pair_by_key:
                            raise ExecutionContractError(
                                "SOLUTION_AXIS_METADATA_UNAVAILABLE",
                                "centered variance requires complete outer/inner pair metadata",
                            )
                        centered_value = [
                            [
                                [[0.0] for _inner in inner_by_outer.get(int(outer), [])]
                                for outer in outer_labels
                            ]
                            for _expression in expressions
                        ]
                        for outer_position, outer in enumerate(outer_labels):
                            inner_labels = list(inner_by_outer.get(int(outer), []))
                            for inner_position, inner in enumerate(inner_labels):
                                pair = pair_by_key.get((int(outer), int(inner)))
                                if pair is None:
                                    raise ExecutionContractError(
                                        "SOLUTION_AXIS_METADATA_UNAVAILABLE",
                                        f"missing SolutionInfo pair ({outer}, {inner}) for centered variance",
                                    )
                                _call(aggregate_feature, "set", "expr", _centered_exprs(outer_position, inner_position))
                                try:
                                    _call(aggregate_feature, "set", "outersolnum", int(pair["outer"]))
                                    _call(aggregate_feature, "set", "solnum", int(pair["solnum"]))
                                except Exception as exc:
                                    raise ExecutionContractError(
                                        "SOLUTION_SELECTION_FAILED",
                                        f"could not select centered variance solution ({pair['outer']}, {pair['inner']})",
                                    ) from exc
                                _call(aggregate_feature, "run")
                                centered_real, centered_imag, centered_status, _centered_layout = _feature_components(
                                    aggregate_feature,
                                    outer=int(pair["outer"]),
                                    use_outer_getters=True,
                                )
                                if centered_status:
                                    raise ExecutionContractError(
                                        "COMPLEX_DATA_ERROR",
                                        "centered variance integral returned complex data",
                                    )
                                if ms.entity_dim == 0:
                                    centered_real, _unused_imag, _unused_points = _normalise_evalpoint_components(
                                        centered_real,
                                        centered_imag,
                                        num_expressions=len(expressions),
                                    )
                                    centered_real = _point_reduce(centered_real, "sum")
                                cells = _selected_inner_expression_cells(
                                    centered_real,
                                    inner_labels=inner_labels,
                                    inner=int(inner),
                                    num_expressions=len(expressions),
                                    label="centered variance",
                                )
                                for expression_index, cell in enumerate(cells):
                                    if isinstance(cell, Mapping):
                                        raise ExecutionContractError(
                                            "COMPLEX_DATA_ERROR",
                                            "centered variance cell must be real",
                                        )
                                    centered_value[expression_index][outer_position][inner_position] = [float(cell)]
                        sq_value = centered_value
                    else:
                        _call(aggregate_feature, "set", "expr", _centered_exprs(None, None))
                        _call(aggregate_feature, "run")
                        centered_real, centered_imag, centered_status, _centered_layout = _feature_components(aggregate_feature)
                        if centered_status:
                            raise ExecutionContractError(
                                "COMPLEX_DATA_ERROR",
                                "centered variance integral returned complex data",
                            )
                        if ms.entity_dim == 0:
                            centered_real, _unused_imag, _unused_points = _normalise_evalpoint_components(
                                centered_real,
                                centered_imag,
                                num_expressions=len(expressions),
                            )
                            centered_real = _point_reduce(centered_real, "sum")
                        cells = _single_expression_cells(
                            centered_real,
                            num_expressions=len(expressions),
                            label="centered variance",
                        )
                        sq_value = cells[0] if len(cells) == 1 else cells
                except Exception as exc:
                    if isinstance(exc, ExecutionContractError):
                        raise
                    raise ExecutionContractError(
                        "ENGINE_CALL_FAILED",
                        f"the centered variance integral ∫w|f-mean|² dμ could not be evaluated: "
                        f"{type(exc).__name__}: {exc}",
                    )
                second_moment = _divide_by_measure(sq_value)
                # The engine already integrated |f-mean|².  Do not subtract
                # |mean|² from E|f|² here: for large nearly constant fields
                # that cancellation creates a false non-zero std.
                transformed = _nested_sqrt(second_moment)

            elif aggregate == "rms":
                if aggregate_feature is None:
                    raise ExecutionContractError(
                        "ZERO_OR_INVALID_MEASURE",
                        "the square integral ∫w|f|² dμ needs the measure feature, which is not available",
                    )
                if weight_expression:
                    sq_exprs = [f"({weight_expression})*abs(({e}))^2" for e in effective_expressions]
                else:
                    sq_exprs = [f"abs(({e}))^2" for e in effective_expressions]
                try:
                    _call(aggregate_feature, "set", "expr", sq_exprs)
                    sq_real, sq_imag, sq_status, sq_layout = _run_bound_feature(
                        aggregate_feature,
                        solution_binding,
                        num_expressions=len(expressions),
                        point_feature=ms.entity_dim == 0,
                        outer_getters=True,
                    )
                    if sq_status:
                        raise ExecutionContractError("COMPLEX_DATA_ERROR", "|f|² second moment returned complex data")
                    if solution_binding:
                        from ._solution_binding import SolutionBinding
                        sq_value = SolutionBinding.field_array_from_engine(
                            sq_real,
                            solution_binding,
                            num_expressions=len(expressions),
                            layout=sq_layout,
                            selected_outer=(solution_binding.get("outer_indices") or [None])[0],
                            is_complex=False,
                        ).values
                    else:
                        sq_value = sq_real
                    if ms.entity_dim == 0:
                        sq_value = _point_reduce(sq_value, "sum")
                except Exception as exc:
                    raise ExecutionContractError(
                        "ENGINE_CALL_FAILED",
                        f"the square integral ∫w|f|² dμ could not be evaluated: "
                        f"{type(exc).__name__}: {exc}",
                    )
                transformed = _nested_sqrt(_divide_by_measure(sq_value))

            if meas_feat is not None:
                if meas_cleanup["created"] and not meas_cleanup["removed"]:
                    _remove_ephemeral(numerical_list, meas_tag, meas_cleanup, [])
            if num_feat is not None:
                if num_cleanup["created"] and not num_cleanup["removed"]:
                    _remove_ephemeral(numerical_list, num_tag, num_cleanup, [])

        # SolutionSpec index filtering (T021 / F04).  The aggregate/statistics
        # branches above replace ``transformed`` with the evaluated mean,
        # centered variance, or RMS.  Apply selectors to a FieldArray rebuilt
        # from that final value; selecting the pre-aggregate payload here would
        # silently put the original field back into a std/rms response.
        if field_array_payload is not None:
            field_array_payload = _field_array_with_values(
                field_array_payload,
                transformed,
                is_complex=field_array_is_complex,
                selectors=pending_field_selectors,
            )
            transformed = field_array_payload.values

        inner_spec = solution_spec.get("inner")
        if inner_spec is not None and field_array_payload is None:
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
        for record in reversed(cleanup_records):
            if record["created"] and not record["removed"]:
                _remove_ephemeral(numerical_list, record["tag"], record, [])
        cleanup["children"] = [record for record in cleanup_records if record is not cleanup]
        cleanup["cleanup_failed"] = any(record.get("cleanup_failed") for record in cleanup_records)
        if cleanup["cleanup_failed"] and cleanup.get("error") is None:
            failed = next((record for record in cleanup_records if record.get("cleanup_failed")), None)
            cleanup["error"] = failed.get("error") if failed else {"code": "EXECUTION_STATE_UNKNOWN", "message": "ephemeral cleanup failed"}

    if field_array_payload is not None and transformed is not None:
        field_array_payload = _field_array_with_values(
            field_array_payload,
            transformed,
            is_complex=field_array_is_complex,
        )

    total_elements = _count_elements(transformed)
    artifact_meta = None
    if (
        engine_error is None
        and not cleanup["cleanup_failed"]
        and (storage == "artifact" or (storage == "auto" and total_elements > AUTO_ARTIFACT_ELEMENT_LIMIT))
    ):
        artifact_meta = _export_to_artifact(
            transformed,
            dataset_tag,
            project_root=trusted_project_root(worker),
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
        "complex_transform_order": requested_transform_order,
        "evaluated_expressions": list(effective_expressions),
        "is_complex": is_complex,
        "field_array": field_array_payload.to_dict() if field_array_payload is not None else None,
        "solution_axes": {
            "outer": list(solution_binding.get("outer_indices", [])) if solution_binding else [],
            "inner": list(solution_binding.get("inner_indices", [])) if solution_binding else [],
            "pair_mapping_complete": bool(solution_binding and solution_binding.get("pair_mapping_complete")),
        },
        "axisymmetric": is_axisymmetric,
        "axisymmetric_measure_evidence": axisymmetric_measure_evidence,
        "axisymmetric_factor_applied": bool(axisymmetric_measure_evidence),
        "axisymmetric_applied_count": 1 if axisymmetric_measure_evidence else 0,
        "revolved_measure": denominator_measure if is_axisymmetric else None,
        "cross_section_measure": cross_section_measure,
        "denominator_measure": denominator_measure if aggregate in ("average", "std", "rms") else None,
        "selection_measure": selection_measure,
        "selection_measure_source": selection_measure_source,
        "denominator_source": denominator_source,
        "total_elements": total_elements,
        "result_budget": (
            {
                "status": (
                    "PASS"
                    if result_budget_records and all(
                        record.get("status") == "PASS" and record.get("allowed") is True
                        for record in result_budget_records
                    ) and engine_error is None
                    else ("BLOCKED" if result_budget_records or engine_error is not None else "UNVERIFIED")
                ),
                "limits": {
                    "max_elements": RESULT_NUMERIC_PAYLOAD_MAX_ELEMENTS,
                    "max_bytes": RESULT_NUMERIC_PAYLOAD_MAX_BYTES,
                    "scope": "numeric_payload_only",
                    "engine_internal_cache": "UNMEASURED",
                },
                "records": result_budget_records,
                "publish_allowed": bool(
                    result_budget_records
                    and all(record.get("publish_allowed") is True for record in result_budget_records)
                    and engine_error is None
                    and not cleanup["cleanup_failed"]
                ),
            }
            if aggregate == "none" and feat_type == "Eval"
            else None
        ),
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
    if frame != "spatial":
        # Frame support is a request contract decision.  Refuse it before
        # binding the model or reading dataset metadata, so the caller has a
        # validation-stage witness that no engine mutation/read was issued.
        raise PreWriteRefusal(
            "API_UNSUPPORTED",
            f"coordinate frame {frame!r} not supported; only 'spatial' supported",
            details={"frame": frame, "mutation_issued": False},
        )

    expressions = require_string_array(spec.get("expressions"), "spec.expressions")
    solution_spec = require_mapping(spec.get("solution", {}), "spec.solution")
    dataset_tag = solution_spec.get("dataset")
    if not dataset_tag:
        raise ExecutionContractError("INVALID_REQUEST", "spec.solution.dataset is required")

    complex_mode = spec.get("complex_mode", "real")
    if complex_mode not in COMPLEX_MODES:
        raise ExecutionContractError("API_UNSUPPORTED", f"complex_mode {complex_mode!r} is not supported")
    for name in ("time", "frequency", "parameters"):
        if solution_spec.get(name) is not None or spec.get(name) is not None:
            raise ExecutionContractError("API_UNSUPPORTED", f"spec.solution.{name} matching is unavailable for result.at_points")

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
    if dataset_tag not in tag_list(dataset_list):
        raise node_not_found(f"dataset {dataset_tag!r} does not exist")
    dset_node = _call(dataset_list, "get", dataset_tag)
    errs: list[dict[str, Any]] = []
    requested_solution = solution_spec.get("solution") if isinstance(solution_spec.get("solution"), str) else None
    dataset_binding = _resolve_dataset_binding(
        model,
        str(dataset_tag),
        requested_solution=requested_solution,
    )
    if (
        isinstance(dataset_binding, Mapping)
        and isinstance(dataset_binding.get("error"), Mapping)
        and dataset_binding["error"].get("code") == "SOLUTION_MISMATCH"
    ):
        raise ExecutionContractError(
            "SOLUTION_MISMATCH",
            str(dataset_binding["error"].get("message", "requested solution does not match dataset")),
            details={"dataset_binding": dict(dataset_binding)},
        )
    ctx = _coordinate_context(model, dset_node, errs, dataset_binding=dataset_binding)
    sdim = ctx.get("space_dimension")
    if sdim is not None and dim != sdim:
        raise ExecutionContractError(
            "DIMENSION_MISMATCH",
            f"Point space dimension {dim} does not match model space dimension {sdim}",
        )
    solution_tag = (
        solution_spec.get("solution")
        or (dataset_binding.get("solution") if isinstance(dataset_binding, Mapping) else None)
        or _string_or_none(dset_node, "solution", errs)
        or _string_or_none(dset_node, "data", errs)
    )
    solution_binding = _result_solution_binding(model, solution_tag)
    if solution_spec.get("outer") is not None and not solution_binding:
        raise ExecutionContractError("SOLUTION_AXIS_METADATA_UNAVAILABLE", "outer selection requires SolutionInfo metadata")
    if solution_spec.get("inner") is not None and (not solution_binding or not solution_binding.get("pair_mapping_complete")):
        raise ExecutionContractError("SOLUTION_AXIS_METADATA_UNAVAILABLE", "inner selection requires complete SolutionInfo metadata")

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
    field_array_payload: Any = None
    is_complex: bool | None = None
    field_array_is_complex = False
    readback_status = "UNAVAILABLE"
    readback_coords = None
    data_readback: Any = None
    data_readback_status = "UNVERIFIED"
    result_budget_records: list[dict[str, Any]] = []
    result_budget_guard = _make_result_budget_guard(
        operation="result.at_points",
        feature_kind="Interp",
        expressions=expressions,
        binding=solution_binding,
        point_count=point_count,
        records=result_budget_records,
    )

    try:
        scale = _length_scale_to_geometry(coordinate_unit, ctx.get("length_unit"))
        scaled_coord_matrix = [[x * scale for x in row] for row in coord_matrix]

        feature = _call(numerical_list, "create", ephemeral_tag, "Interp")
        cleanup["created"] = True

        _call(feature, "set", "data", dataset_tag)
        # A successful setter call is not proof that the native feature now
        # targets this dataset.  Read the property back before requesting any
        # values; an ignored setter or wrong feature property would otherwise
        # publish values from an unrelated dataset while echoing the request.
        try:
            data_readback = _call(feature, "getString", "data")
        except Exception as exc:
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN",
                "Interp data property could not be read back after set('data')",
            ) from exc
        if not isinstance(data_readback, str) or data_readback != dataset_tag:
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN",
                f"Interp data readback {data_readback!r} does not match dataset {dataset_tag!r}",
            )
        data_readback_status = "VERIFIED"
        _call(feature, "set", "expr", expressions)
        _call(feature, "setInterpolationCoordinates", scaled_coord_matrix)
        raw_real, raw_imag, is_complex, raw_layout = _run_bound_feature(
            feature,
            solution_binding,
            num_expressions=len(expressions),
            budget_guard=result_budget_guard,
        )
        expression_units = _feature_units(feature, expressions)
        transformed = _transform_complex_data(raw_real, raw_imag, complex_mode, is_complex=is_complex)
        if solution_binding:
            from ._solution_binding import FieldArray, SolutionBinding
            field_array_is_complex = _field_array_is_complex(
                is_complex, complex_mode, "none"
            )
            # ``setInterpolationCoordinates`` and ``getCoordinates`` use
            # COMSOL's dimension-major matrix.  FieldArray publishes the
            # user-facing point coordinate records as ``[point][dimension]``.
            field_point_coordinates = [
                [scaled_coord_matrix[dimension][point] for dimension in range(dim)]
                for point in range(point_count)
            ]
            # NumericalFeature.getData/getImagData expose a point axis, while
            # getReal/getImag are aggregate ``[expression][solnum]`` reads.
            # The latter cannot carry one value per requested interpolation
            # point.  Do not attach all requested coordinates to a singleton
            # aggregate result: that would manufacture a FieldArray shape and
            # was the source of the public export coordinate-count failure.
            if raw_layout.endswith("point"):
                field_point_count = _feature_point_count(transformed, raw_layout)
                if field_point_count != point_count:
                    raise ExecutionContractError(
                        "FIELD_ARRAY_SHAPE_MISMATCH",
                        f"interpolation returned {field_point_count} points, expected {point_count}",
                    )
                field_point_coordinates_for_result = field_point_coordinates
            else:
                if point_count != 1:
                    raise ExecutionContractError(
                        "FIELD_ARRAY_SHAPE_MISMATCH",
                        "NumericalFeature.getReal/getImag returned aggregate [expression,solnum] data; "
                        "per-point FieldArray output requires getData/getImagData",
                    )
                field_point_count = 1
                field_point_coordinates_for_result = field_point_coordinates[:1]
            field_coords, field_units, field_metadata = _field_array_context(
                solution_binding,
                expressions,
                expression_units=expression_units,
                length_unit=ctx.get("length_unit"),
                point_count=field_point_count,
                point_coordinates=field_point_coordinates_for_result,
                point_coordinate_source="request",
            )
            if raw_layout.startswith("expression,outer"):
                field_array_payload = FieldArray(
                    transformed,
                    axes=("expression", "outer", "inner", "point"),
                    coords=field_coords,
                    units=field_units,
                    metadata=field_metadata,
                    is_complex=field_array_is_complex,
                )
            else:
                field_array_payload = SolutionBinding.field_array_from_engine(
                    transformed,
                    solution_binding,
                    num_expressions=len(expressions),
                    layout=raw_layout,
                    selected_outer=(solution_binding.get("outer_indices") or [None])[0],
                    coords=field_coords,
                    units=field_units,
                    metadata=field_metadata,
                    is_complex=field_array_is_complex,
                )
            selectors: dict[str, Any] = {}
            if solution_spec.get("outer") is not None:
                selectors["outer"] = solution_spec["outer"]
            if solution_spec.get("inner") is not None:
                selectors["inner"] = solution_spec["inner"]
            if selectors:
                field_array_payload = field_array_payload.select(**selectors)
            transformed = field_array_payload.values

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
        # Publish the resolver's read-back witness alongside the requested
        # dataset tag.  Consumers must be able to distinguish a native
        # dataset binding from an echoed request, especially for derived
        # datasets such as Join.
        "dataset": dataset_tag,
        "solution": solution_tag,
        "binding_source": (
            "resolve_dataset_binding(model, dataset_tag)"
            if isinstance(dataset_binding, Mapping) else None
        ),
        "dataset_binding": (
            dict(dataset_binding) if isinstance(dataset_binding, Mapping) else None
        ),
        "feature_readback": {
            "data": data_readback,
            "data_status": data_readback_status,
            "source": "NumericalFeature.getString('data') after set('data')",
        },
        "coordinate_unit": coordinate_unit,
        "frame": frame,
        "coordinate_readback": {
            "status": readback_status,
            "coordinates": readback_coords,
            "unit": ctx.get("length_unit"),
        },
        "complex_mode": complex_mode,
        "is_complex": is_complex,
        "field_array": field_array_payload.to_dict() if field_array_payload is not None else None,
        "result_budget": {
            "status": (
                "PASS"
                if result_budget_records and all(
                    record.get("status") == "PASS" and record.get("allowed") is True
                    for record in result_budget_records
                ) and engine_error is None
                else ("BLOCKED" if result_budget_records or engine_error is not None else "UNVERIFIED")
            ),
            "limits": {
                "max_elements": RESULT_NUMERIC_PAYLOAD_MAX_ELEMENTS,
                "max_bytes": RESULT_NUMERIC_PAYLOAD_MAX_BYTES,
                "scope": "numeric_payload_only",
                "engine_internal_cache": "UNMEASURED",
            },
            "records": result_budget_records,
            "publish_allowed": bool(
                result_budget_records
                and all(record.get("publish_allowed") is True for record in result_budget_records)
                and engine_error is None
                and not cleanup["cleanup_failed"]
            ),
        },
        "cleanup": cleanup,
        "status": {
            "ok": engine_error is None and not cleanup["cleanup_failed"] and readback_status != "MISMATCH",
            "engine_error": engine_error,
            "cleanup_failed": cleanup["cleanup_failed"],
            "verification_status": "PASSED" if readback_status == "VERIFIED" else ("FAILED" if readback_status == "MISMATCH" else "UNVERIFIED"),
        },
        "verification_status": "PASSED" if readback_status == "VERIFIED" else ("FAILED" if readback_status == "MISMATCH" else "UNVERIFIED"),
    }


# ---------------------------------------------------------------------------
# W17 Probe / Derived Values & Table Management
# ---------------------------------------------------------------------------

def _result_feature_target(worker: Any, model_tag: str, path: Any, collection: str) -> tuple[dict[str, Any], str, Any, Any]:
    """Resolve a typed ``result.<collection>(tag)`` path and its owner list."""
    if not isinstance(path, Mapping):
        raise PreWriteRefusal("INVALID_NODE_PATH", f"{collection} CRUD requires a NodePath object")
    parsed = NodePath.from_wire(path, allow_empty=False)
    if not parsed.segments or parsed.segments[0].accessor != "result":
        raise PreWriteRefusal("INVALID_NODE_PATH", f"{collection} path must begin with the result accessor")
    if any(segment.accessor is not None for segment in parsed.segments[1:]):
        raise PreWriteRefusal("INVALID_NODE_PATH", f"{collection} path has an unsupported accessor after result")
    final = parsed.segments[-1]
    if final.collection != collection or final.tag is None:
        raise PreWriteRefusal("INVALID_NODE_PATH", f"path must end with collection={collection!r} and a tag")
    canonical = parsed.as_dict()
    _canonical_from_engine, node = resolve_path(worker, model_tag, canonical, label="path")
    model = bound_model(worker, model_tag)
    owner = _call(_call(model, "result"), collection)
    tags = tag_list(owner)
    tag = str(final.tag)
    if tag not in tags:
        raise node_not_found(f"{collection} feature {tag!r} does not exist; existing: {tags}")
    actual = _call(owner, "get", tag)
    return canonical, tag, owner, actual if actual is not None else node


def _result_feature_create_path(path: Any, collection: str) -> tuple[dict[str, Any], str]:
    if not isinstance(path, Mapping):
        raise PreWriteRefusal("INVALID_NODE_PATH", f"{collection} create path must be a NodePath object")
    parsed = NodePath.from_wire(path, allow_empty=False)
    if not parsed.segments or parsed.segments[0].accessor != "result":
        raise PreWriteRefusal("INVALID_NODE_PATH", f"{collection} path must begin with the result accessor")
    if any(segment.accessor is not None for segment in parsed.segments[1:]):
        raise PreWriteRefusal("INVALID_NODE_PATH", f"{collection} path has an unsupported accessor after result")
    final = parsed.segments[-1]
    if final.collection != collection or final.tag is None:
        raise PreWriteRefusal("INVALID_NODE_PATH", f"create path must end in {collection}:<tag>")
    return parsed.as_dict(), str(final.tag)


def _validate_result_feature_path(path: Any, collection: str) -> tuple[dict[str, Any], str]:
    """Validate a typed result path without touching the engine."""
    if not isinstance(path, Mapping):
        raise PreWriteRefusal("INVALID_NODE_PATH", f"{collection} path must be a NodePath object")
    parsed = NodePath.from_wire(path, allow_empty=False)
    if not parsed.segments or parsed.segments[0].accessor != "result":
        raise PreWriteRefusal("INVALID_NODE_PATH", f"{collection} path must begin with the result accessor")
    if any(segment.accessor is not None for segment in parsed.segments[1:]):
        raise PreWriteRefusal("INVALID_NODE_PATH", f"{collection} path has an unsupported accessor after result")
    final = parsed.segments[-1]
    if final.collection != collection or final.tag is None:
        raise PreWriteRefusal("INVALID_NODE_PATH", f"path must end with collection={collection!r} and a tag")
    return parsed.as_dict(), str(final.tag)


_NUMERICAL_PROPERTY_NAMES = frozenset({
    "expr", "data", "unit", "descr", "table", "solnum", "t", "method", "selection",
    "window", "frame", "dataset", "coord", "edim", "exprs",
})


def _normalise_feature_definition(definition: Any, *, allowed: frozenset[str]) -> list[tuple[str, Any]]:
    raw = require_mapping(definition, "definition")
    if "properties" in raw:
        if set(raw) != {"properties"}:
            raise ExecutionContractError("INVALID_REQUEST", "definition.properties cannot be mixed with direct fields")
        from ._g3_common import property_definition
        raw = property_definition(raw["properties"], "definition.properties")
    unknown = sorted(set(raw) - allowed - {"tag", "type_id"})
    if unknown:
        raise ExecutionContractError("INVALID_REQUEST", f"definition has unsupported properties: {unknown}")
    return [(str(name), _probe_unwrap_property_value(value, f"definition.{name}"))
            for name, value in raw.items() if name not in {"tag", "type_id"}]


def _table_matrix(value: Any, label: str) -> list[list[Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, Mapping)):
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must be a rectangular array")
    rows = list(value)
    if not rows:
        return []
    converted: list[list[Any]] = []
    for r_index, row in enumerate(rows):
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes, Mapping)):
            raise ExecutionContractError("INVALID_REQUEST", f"{label}[{r_index}] must be an array")
        vals = [_probe_unwrap_property_value(item, f"{label}[{r_index}][{c_index}]") for c_index, item in enumerate(row)]
        for c_index, item in enumerate(vals):
            if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)):
                raise ExecutionContractError("INVALID_REQUEST", f"{label}[{r_index}][{c_index}] must be finite numeric data")
        converted.append(vals)
    widths = {len(row) for row in converted}
    if len(widths) > 1:
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must be rectangular")
    return converted


def _table_readback(node: Any) -> dict[str, Any]:
    headers_probe = call_probe(node, "getColumnHeaders")
    headers = list(headers_probe["value"]) if headers_probe["ok"] and isinstance(headers_probe["value"], (list, tuple)) else None
    real_probe = call_probe(node, "getReal")
    if real_probe["ok"]:
        real = real_probe["value"]
    else:
        data_probe = call_probe(node, "getTableData", True)
        if not data_probe["ok"]:
            data_probe = call_probe(node, "getTableData")
        real = data_probe["value"] if data_probe["ok"] else None
    imag_probe = call_probe(node, "getImag")
    imag = imag_probe["value"] if imag_probe["ok"] else None
    if real is None:
        rows: Any = None
    elif imag is not None and isinstance(real, (list, tuple)) and isinstance(imag, (list, tuple)):
        rows = []
        for r_index, row in enumerate(real):
            irow = imag[r_index] if r_index < len(imag) else []
            rows.append([
                {"real": value, "imag": irow[c_index]}
                if c_index < len(irow) else value
                for c_index, value in enumerate(row)
            ])
    else:
        rows = real
    readable = headers_probe["ok"] and real is not None
    return {"readable": readable, "match": True, "headers": headers, "data": rows,
            "imaginary": imag, "header_readback": headers_probe.get("error"),
            "data_readback": real_probe.get("error") if not real_probe["ok"] else None}


def _table_write(node: Any, definition: Mapping[str, Any]) -> tuple[list[Any], list[Any], list[Any], bool]:
    """Apply table data/header mutations once each, stopping on first error."""
    applied: list[Any] = []
    failed: list[Any] = []
    not_executed: list[Any] = []
    unknown = False
    if "data" in definition:
        data = _table_matrix(definition["data"], "definition.data")
        imag = _table_matrix(definition["imaginary"], "definition.imaginary") if "imaginary" in definition else None
        try:
            if imag is None:
                _call(node, "setTableData", data)
            else:
                if len(imag) != len(data) or any(len(a) != len(b) for a, b in zip(imag, data)):
                    raise ExecutionContractError("INVALID_REQUEST", "definition.imaginary shape differs from data")
                _call(node, "setTableData", data, imag)
            applied.append({"step": "data", "requested_rows": len(data), "readback": _table_readback(node)})
        except Exception as exc:
            failed.append({"step": "data", "error": {"code": getattr(exc, "code", "ENGINE_CALL_FAILED"), "message": str(exc)}})
            if "headers" in definition:
                not_executed.append({"step": "headers"})
            return applied, failed, not_executed, True
    if "headers" in definition:
        headers = definition["headers"]
        if not isinstance(headers, Sequence) or isinstance(headers, (str, bytes, Mapping)) or not all(isinstance(v, str) and v for v in headers):
            raise ExecutionContractError("INVALID_REQUEST", "definition.headers must be a non-empty array of strings")
        try:
            _call(node, "setColumnHeaders", list(headers))
            applied.append({"step": "headers", "requested": list(headers), "readback": _table_readback(node).get("headers")})
        except Exception as exc:
            failed.append({"step": "headers", "error": {"code": getattr(exc, "code", "ENGINE_CALL_FAILED"), "message": str(exc)}})
            unknown = True
    return applied, failed, not_executed, unknown

def result_numerical_manage(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Manage user-visible Numerical features with typed paths/readback."""
    action = require_string(arguments.get("action"), "action")
    if action not in {"list", "create", "get", "inspect", "update", "set", "run", "evaluate", "remove"}:
        raise ExecutionContractError("INVALID_REQUEST", f"unknown numerical_manage action: {action!r}")
    # Validate the entire request shape before resolving the model.  A bad
    # property/path must not consume an engine call or create a partial node.
    definition_raw = arguments.get("definition", {})
    definition = require_mapping(definition_raw, "definition") if action != "list" else {}
    prevalidated_properties: list[tuple[str, Any]] | None = None
    prevalidated_create: tuple[dict[str, Any], str, str] | None = None
    if action == "create":
        if arguments.get("path") is not None:
            create_path, create_tag = _result_feature_create_path(arguments["path"], "numerical")
        else:
            create_tag = require_string(definition["tag"], "definition.tag") if definition.get("tag") is not None else ""
            create_path = _result_path("numerical", create_tag) if create_tag else {}
        create_type = require_string(definition.get("type_id", "EvalGlobal"), "definition.type_id")
        if create_type not in SUPPORTED_NUMERICAL_TYPES:
            raise ExecutionContractError("API_UNSUPPORTED", f"numerical feature type {create_type!r} is not supported")
        prevalidated_properties = _normalise_feature_definition(definition, allowed=_NUMERICAL_PROPERTY_NAMES)
        prevalidated_create = (create_path, create_tag, create_type)
    elif action in {"update", "set"}:
        prevalidated_properties = _normalise_feature_definition(definition, allowed=_NUMERICAL_PROPERTY_NAMES)
    elif action in {"get", "inspect", "run", "evaluate", "remove"}:
        _validate_result_feature_path(arguments.get("path"), "numerical")
    model = bound_model(worker, model_tag)
    results = _call(model, "result")
    numerical_list = _call(results, "numerical")
    tags = tag_list(numerical_list)

    if action == "list":
        features: list[dict[str, Any]] = []
        for tag in tags:
            node = _call(numerical_list, "get", tag)
            features.append({"tag": tag, "type_id": _node_type(node, []),
                             "expr": _string_or_none(node, "expr", []),
                             "dataset": _string_or_none(node, "data", []),
                             "path": _result_path("numerical", tag)})
        return {"action": "list", "features": features, "count": len(features), "tags": tags,
                "readback": {"readable": True, "source": "engine.tags/get"}}

    if action == "create":
        if prevalidated_create is not None:
            canonical, tag, type_id = prevalidated_create
        else:  # defensive, the prevalidation branch above always sets this
            tag = _unique_tag(tags)
            canonical, type_id = _result_path("numerical", tag), "EvalGlobal"
        if not tag:
            tag = _unique_tag(tags)
            canonical = _result_path("numerical", tag)
        properties = prevalidated_properties or []
        if tag in tags:
            raise ExecutionContractError("TAG_CONFLICT", f"numerical feature {tag!r} already exists")
        applied: list[Any] = []
        failed: list[Any] = []
        not_executed: list[Any] = []
        try:
            node = _call(numerical_list, "create", tag, type_id)
        except Exception as exc:
            failed.append({"step": "create", "error": {"code": getattr(exc, "code", "ENGINE_CALL_FAILED"), "message": str(exc)}})
            not_executed.extend({"step": "property", "property": name} for name, _ in properties)
            return {"action": action, "tag": tag, "type_id": type_id, "created": False,
                    **_probe_completion(applied=applied, failed=failed, not_executed=not_executed, readback={"readable": False, "match": False}, execution_state_unknown=True)}
        applied.append({"step": "create", "requested": {"tag": tag, "type_id": type_id}})
        if tag not in tag_list(numerical_list):
            failed.append({"step": "create", "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": "post-create numerical tag readback did not confirm the node"}})
            return {"action": action, "tag": tag, "type_id": type_id, "created": True,
                    **_probe_completion(applied=applied, failed=failed, not_executed=[{"step": "property", "property": name} for name, _ in properties], readback={"readable": False, "match": False}, execution_state_unknown=True)}
        prop_applied, prop_failed, prop_not_executed, prop_unknown = _probe_write_properties(node, properties)
        applied.extend(prop_applied)
        failed.extend(prop_failed)
        not_executed.extend(prop_not_executed)
        readback = _result_readback(node, properties)
        return {"action": action, "tag": tag, "type_id": type_id, "created": True, "path": canonical,
                **_probe_completion(applied=applied, failed=failed, not_executed=not_executed, readback=readback, execution_state_unknown=prop_unknown)}

    canonical, tag, owner, node = _result_feature_target(worker, model_tag, arguments.get("path"), "numerical")
    if action in {"get", "inspect"}:
        props: dict[str, Any] = {}
        names_probe = call_probe(node, "properties")
        if names_probe["ok"] and isinstance(names_probe["value"], (list, tuple)):
            for name in list(names_probe["value"])[:100]:
                value = _prop_value(node, str(name))
                if value is not None:
                    props[str(name)] = value
        return {"action": action, "tag": tag, "path": canonical, "type_id": _node_type(node, []), "properties": props,
                "readback": {"readable": True, "properties": props}}
    if action in {"update", "set"}:
        properties = prevalidated_properties or []
        applied, failed, not_executed, execution_unknown = _probe_write_properties(node, properties)
        return {"action": action, "tag": tag, "path": canonical, "updated": True,
                **_probe_completion(applied=applied, failed=failed, not_executed=not_executed,
                                    readback=_result_readback(node, properties), execution_state_unknown=execution_unknown)}
    if action in {"run", "evaluate"}:
        try:
            _call(node, "run")
            data = _call(node, "getData")
            return {"action": action, "tag": tag, "path": canonical, "data": data,
                    "status": {"ok": True, "execution_state_unknown": False}}
        except Exception as exc:
            return {"action": action, "tag": tag, "path": canonical,
                    "status": {"ok": False, "execution_state_unknown": True,
                               "engine_error": {"code": getattr(exc, "code", "ENGINE_CALL_FAILED"), "message": str(exc)}}}
    try:
        _call(owner, "remove", tag)
    except Exception as exc:
        return {"action": action, "tag": tag, "path": canonical, "removed": False,
                **_probe_completion(applied=[], failed=[{"step": "remove", "error": {"code": getattr(exc, "code", "ENGINE_CALL_FAILED"), "message": str(exc)}}], not_executed=[], readback={"readable": False, "match": False}, execution_state_unknown=True)}
    remaining = tag_list(owner)
    if tag in remaining:
        return {"action": action, "tag": tag, "path": canonical, "removed": True,
                **_probe_completion(applied=[{"step": "remove", "tag": tag}], failed=[{"step": "remove", "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": "post-remove numerical tags still contain the feature"}}], not_executed=[], readback={"readable": False, "match": False, "tags": remaining}, execution_state_unknown=True)}
    return {"action": action, "tag": tag, "path": canonical, "removed": True, "verified_removed": True,
            **_probe_completion(applied=[{"step": "remove", "tag": tag}], failed=[], not_executed=[], readback={"readable": True, "match": True, "tags": remaining})}


def result_table_manage(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Manage user-visible tables with one verified mutation path per step."""
    action = require_string(arguments.get("action"), "action")
    if action not in {"list", "create", "get", "inspect", "set", "clear", "remove"}:
        raise ExecutionContractError("INVALID_REQUEST", f"unknown table_manage action: {action!r}")
    raw_definition = require_mapping(arguments.get("definition", {}), "definition") if action != "list" else {}
    prevalidated_table_path: tuple[dict[str, Any], str] | None = None
    if action == "create":
        if arguments.get("path") is not None:
            prevalidated_table_path = _result_feature_create_path(arguments["path"], "table")
        unknown = set(raw_definition) - {"tag", "type_id", "data", "imaginary", "headers"}
        if unknown:
            raise ExecutionContractError("INVALID_REQUEST", f"definition has unsupported table fields: {sorted(unknown)}")
        if "data" in raw_definition:
            _table_matrix(raw_definition["data"], "definition.data")
        if "imaginary" in raw_definition:
            _table_matrix(raw_definition["imaginary"], "definition.imaginary")
        if "headers" in raw_definition:
            headers = raw_definition["headers"]
            if not isinstance(headers, Sequence) or isinstance(headers, (str, bytes, Mapping)) or not all(isinstance(v, str) and v for v in headers):
                raise ExecutionContractError("INVALID_REQUEST", "definition.headers must be an array of strings")
        table_type = require_string(raw_definition.get("type_id", "Table"), "definition.type_id")
        if table_type != "Table":
            raise ExecutionContractError("API_UNSUPPORTED", f"table type {table_type!r} is not supported")
    elif action == "set":
        _validate_result_feature_path(arguments.get("path"), "table")
        unknown = set(raw_definition) - {"data", "imaginary", "headers"}
        if unknown:
            raise ExecutionContractError("INVALID_REQUEST", f"definition has unsupported table fields: {sorted(unknown)}")
        if "data" not in raw_definition and "headers" not in raw_definition:
            raise ExecutionContractError("INVALID_REQUEST", "table.set requires data or headers")
        if "data" in raw_definition:
            _table_matrix(raw_definition["data"], "definition.data")
        if "imaginary" in raw_definition:
            _table_matrix(raw_definition["imaginary"], "definition.imaginary")
    elif action in {"get", "inspect", "clear", "remove"}:
        _validate_result_feature_path(arguments.get("path"), "table")
    model = bound_model(worker, model_tag)
    results = _call(model, "result")
    table_list = _call(results, "table")
    tags = tag_list(table_list)

    if action == "list":
        tables: list[dict[str, Any]] = []
        for tag in tags:
            node = _call(table_list, "get", tag)
            readback = _table_readback(node)
            tables.append({"tag": tag, "path": _result_path("table", tag), "headers": readback.get("headers"), "readback": readback})
        return {"action": action, "tables": tables, "count": len(tables), "tags": tags,
                "readback": {"readable": True, "source": "engine.tags/get"}}

    if action == "create":
        if prevalidated_table_path is not None:
            canonical, tag = prevalidated_table_path
        else:
            tag = require_string(raw_definition.get("tag"), "definition.tag") if raw_definition.get("tag") is not None else _unique_tag(tags)
            canonical = _result_path("table", tag)
        type_id = require_string(raw_definition.get("type_id", "Table"), "definition.type_id")
        if tag in tags:
            raise ExecutionContractError("TAG_CONFLICT", f"table {tag!r} already exists")
        applied: list[Any] = []
        failed: list[Any] = []
        not_executed: list[Any] = []
        try:
            node = _call(table_list, "create", tag, type_id)
        except Exception as exc:
            failed.append({"step": "create", "error": {"code": getattr(exc, "code", "ENGINE_CALL_FAILED"), "message": str(exc)}})
            return {"action": action, "tag": tag, "type_id": type_id, "created": False,
                    **_probe_completion(applied=applied, failed=failed, not_executed=[], readback={"readable": False, "match": False}, execution_state_unknown=True)}
        applied.append({"step": "create", "requested": {"tag": tag, "type_id": type_id}})
        if tag not in tag_list(table_list):
            failed.append({"step": "create", "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": "post-create table tag readback did not confirm the node"}})
            return {"action": action, "tag": tag, "type_id": type_id, "created": True,
                    **_probe_completion(applied=applied, failed=failed, not_executed=[], readback={"readable": False, "match": False}, execution_state_unknown=True)}
        data_applied, data_failed, data_not_executed, data_unknown = _table_write(node, raw_definition)
        applied.extend(data_applied)
        failed.extend(data_failed)
        not_executed.extend(data_not_executed)
        readback = _table_readback(node)
        return {"action": action, "tag": tag, "type_id": type_id, "path": canonical, "created": True,
                **_probe_completion(applied=applied, failed=failed, not_executed=not_executed, readback=readback, execution_state_unknown=data_unknown)}

    canonical, tag, owner, node = _result_feature_target(worker, model_tag, arguments.get("path"), "table")
    if action in {"get", "inspect"}:
        readback = _table_readback(node)
        return {"action": action, "tag": tag, "path": canonical, "headers": readback.get("headers"), "data": readback.get("data"),
                "imaginary": readback.get("imaginary"), "readback": readback}
    if action == "set":
        applied, failed, not_executed, execution_unknown = _table_write(node, raw_definition)
        return {"action": action, "tag": tag, "path": canonical,
                **_probe_completion(applied=applied, failed=failed, not_executed=not_executed,
                                    readback=_table_readback(node), execution_state_unknown=execution_unknown)}
    if action == "clear":
        try:
            _call(node, "clearTableData")
        except Exception as exc:
            return {"action": action, "tag": tag, "path": canonical, "cleared": False,
                    **_probe_completion(applied=[], failed=[{"step": "clear", "error": {"code": getattr(exc, "code", "ENGINE_CALL_FAILED"), "message": str(exc)}}], not_executed=[], readback={"readable": False, "match": False}, execution_state_unknown=True)}
        readback = _table_readback(node)
        cleared = readback.get("data") == []
        if not cleared:
            return {"action": action, "tag": tag, "path": canonical, "cleared": True,
                    **_probe_completion(applied=[{"step": "clear"}], failed=[{"step": "clear", "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": "clear readback still contains table data"}}], not_executed=[], readback=readback, execution_state_unknown=True)}
        return {"action": action, "tag": tag, "path": canonical, "cleared": True,
                **_probe_completion(applied=[{"step": "clear"}], failed=[], not_executed=[], readback=readback)}
    try:
        _call(owner, "remove", tag)
    except Exception as exc:
        return {"action": action, "tag": tag, "path": canonical, "removed": False,
                **_probe_completion(applied=[], failed=[{"step": "remove", "error": {"code": getattr(exc, "code", "ENGINE_CALL_FAILED"), "message": str(exc)}}], not_executed=[], readback={"readable": False, "match": False}, execution_state_unknown=True)}
    remaining = tag_list(owner)
    if tag in remaining:
        return {"action": action, "tag": tag, "path": canonical, "removed": True,
                **_probe_completion(applied=[{"step": "remove", "tag": tag}], failed=[{"step": "remove", "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": "post-remove table tags still contain the table"}}], not_executed=[], readback={"readable": False, "match": False, "tags": remaining}, execution_state_unknown=True)}
    return {"action": action, "tag": tag, "path": canonical, "removed": True, "verified_removed": True,
            **_probe_completion(applied=[{"step": "remove", "tag": tag}], failed=[], not_executed=[], readback={"readable": True, "match": True, "tags": remaining})}


# ---------------------------------------------------------------------------
# W17 Field Export (result.field_export)
# ---------------------------------------------------------------------------

def result_field_export(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Export field data / solutions to local files with chunk pagination (T049)."""
    spec = require_mapping(arguments.get("spec", {}), "spec")
    fmt = require_string(arguments.get("format", "json"), "format").lower()
    dest = require_string(arguments.get("destination"), "destination")
    overwrite = require_bool(arguments.get("overwrite", False), "overwrite")
    if fmt not in {"json", "csv"}:
        # The format is a request-shape decision and is checked before the
        # trusted root is resolved or result_evaluate can dispatch.  Preserve
        # that fact in the managed witness so the public refusal stays a
        # concrete API_UNSUPPORTED response instead of being wrapped as an
        # execution-state UNKNOWN.
        raise PreWriteRefusal(
            "API_UNSUPPORTED",
            f"Unsupported export format {fmt!r}; supported formats are 'json' and 'csv'",
        )

    # Resolve the destination *before* doing any work: the delivered version ran the
    # whole evaluation first and only then discovered that the destination escapes the
    # approved roots, so a refused export had already spent engine time and told the
    # caller nothing until the end.  Containment is checked here, and the resolved path
    # is what the store publishes.
    store = ArtifactStore(project_root=trusted_project_root(worker))
    planned_destination = store.resolve_safe_path(dest, allow_overwrite=overwrite)

    export_spec = dict(spec)
    export_spec["storage"] = "inline"
    eval_res = result_evaluate(worker, model_tag, {"spec": export_spec})

    return store.export_field_data(
        str(planned_destination), eval_res, fmt=fmt, allow_overwrite=overwrite
    )



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
    "result.field_export": ("spec", "format", "destination", "overwrite"),
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
