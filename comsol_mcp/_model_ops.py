#!/usr/bin/env python3
"""Pure model operation helpers: parameters, expressions, metrics, tree, geometry."""

from __future__ import annotations

import contextlib
import contextvars
import json
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Mapping, Sequence


# ---------------------------------------------------------------------------
# Parameter helpers
# ---------------------------------------------------------------------------
def _parameter_rows(model: Any) -> list[dict[str, str]]:
    names = list(model.java.param().varnames())
    rows = []
    for name in names:
        key = str(name)
        rows.append(
            {
                "name": key,
                "expression": str(model.java.param().get(key)),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Expression evaluation helpers
# ---------------------------------------------------------------------------
def _coerce_eval_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _coerce_eval_value(item) for key, item in value.items()}
    if hasattr(value, "tolist"):
        try:
            return value.tolist()
        except Exception:
            pass
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_coerce_eval_value(item) for item in value]
    # JPype Java arrays are iterable but are neither Python lists nor tuples.
    # Keep strings/scalars above so an arbitrary Java object is not mistaken
    # for a collection merely because it has an iterator.
    try:
        return [_coerce_eval_value(item) for item in value]
    except (TypeError, AttributeError):
        pass
    return str(value)


def _last_scalar(value: Any) -> Any:
    current = value
    while isinstance(current, list) and current:
        current = current[-1]
    return current


class NumericalCleanupError(RuntimeError):
    """Evaluation must stop after incomplete temporary-node cleanup."""


#: Per-request inventory of the temporary nodes this layer owned.  A contextvar
#: (not a module global) keeps concurrent requests from mixing their records.
_TEMPORARY_NODES: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar(
    "comsol_mcp_temporary_nodes", default=None
)
_TEMPORARY_OWNER: contextvars.ContextVar[Mapping[str, Any] | None] = contextvars.ContextVar(
    "comsol_mcp_temporary_node_owner", default=None
)


@contextlib.contextmanager
def temporary_node_inventory(*, owner: Mapping[str, Any] | None = None) -> Iterator[list[dict[str, Any]]]:
    """Collect the ownership/cleanup inventory of this request's temporary nodes.

    Nothing here is a second evaluation path: the records are written by the one
    owned-node context manager (:func:`_temporary_numerical_feature`) that every
    evaluation already goes through, and the caller only publishes them.
    """
    records: list[dict[str, Any]] = []
    node_token = _TEMPORARY_NODES.set(records)
    owner_token = _TEMPORARY_OWNER.set(owner)
    try:
        yield records
    finally:
        _TEMPORARY_NODES.reset(node_token)
        _TEMPORARY_OWNER.reset(owner_token)


def _record_temporary_node(record: dict[str, Any]) -> None:
    records = _TEMPORARY_NODES.get()
    if records is not None:
        records.append(record)


def _temporary_owner_descriptor(owner: Mapping[str, Any] | None) -> dict[str, Any]:
    """Owner of a temporary node: explicit caller, else the request context."""
    source = owner if owner is not None else _TEMPORARY_OWNER.get()
    source = source or {}
    descriptor = {
        "operation": str(source.get("operation") or "evaluation"),
        "policy": str(source.get("policy") or "ephemeral_mutation"),
        "scope": "mcp_owned_result_numerical_node",
        "tag_allocated_from": "random uuid4 hex, never reused from the model",
    }
    for key in ("request", "request_id", "tool", "context"):
        if source.get(key) is not None:
            descriptor[key] = source[key]
    return descriptor


def temporary_node_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate an inventory into the ``cleanup`` evidence block of a reply.

    ``created``/``removed``/``verified_removed``/``cleanup_failed`` are the
    booleans the shared C01 contract reads (``_domain_outcome``); the
    ``*_count`` keys are the evidence behind them.
    """
    created = [row for row in records if row.get("created")]
    unremoved = [row["tag"] for row in created if row.get("verified_removed") is not True]
    summary: dict[str, Any] = {
        "policy": "ephemeral_mutation",
        "nodes": len(records),
        "created": bool(created),
        "removed": all(row.get("removed") for row in created) if created else True,
        "verified_removed": not unremoved,
        "cleanup_failed": bool(unremoved),
        "created_count": len(created),
        "removed_count": sum(1 for row in created if row.get("removed")),
        "verified_removed_count": sum(1 for row in created if row.get("verified_removed") is True),
        "unremoved_tags": unremoved,
        "owner_operations": sorted({str(row.get("owner", {}).get("operation")) for row in records}),
        "note": "every tag is a random uuid4 hex owned by this request; cleanup removes that tag only and "
                "the removal is verified against the engine's own numerical().tags() readback",
    }
    if unremoved:
        summary["error"] = {
            "code": "EXECUTION_STATE_UNKNOWN",
            "message": f"temporary numerical cleanup did not verify removal of {unremoved}",
            "safe_retry": False,
            "tags": unremoved,
        }
    return summary


def _raise_with_cleanup_error(primary: BaseException | None, cleanup: BaseException | None) -> None:
    """Never turn a failed cleanup into a successful evaluation."""
    if primary is not None and cleanup is not None:
        raise NumericalCleanupError(f"{primary}; additionally, temporary numerical cleanup failed: {cleanup}") from primary
    if primary is not None:
        raise primary
    if cleanup is not None:
        raise NumericalCleanupError(f"Temporary numerical cleanup failed: {cleanup}") from cleanup


@contextmanager
def _temporary_numerical_feature(model: Any, feature_type: str, *,
                                 owner: Mapping[str, Any] | None = None):
    """Create and remove exactly one MCP-owned numerical feature.

    Result numerical collections can contain user Derived Values, tables, and
    plots.  Tags are deliberately random, and cleanup removes only this tag.

    Every temporary node is recorded in the active :func:`temporary_node_inventory`
    (when one is installed) with its tag, owner operation, the engine's tag list
    before and after, and whether the removal was *verified* by re-reading that
    list.  Live evidence
    ``evidence/phase4_1/runs/20260920T235502Z-g3_1-m1/cases/GUARD_T033``: the
    evaluation reply reported ``ephemeral_mutation: true`` but no inventory at
    all, so "the evaluation cleaned only its own nodes" could not be established.
    """
    numerical = model.java.result().numerical()
    existing = {str(item) for item in list(numerical.tags())}
    tag = ""
    for _ in range(8):
        candidate = f"mcp_eval_{uuid.uuid4().hex}"
        if candidate not in existing:
            tag = candidate
            break
    if not tag:
        raise RuntimeError("Could not allocate an unused MCP temporary numerical tag.")
    record: dict[str, Any] = {
        "tag": tag,
        "feature_type": feature_type,
        "owner": _temporary_owner_descriptor(owner),
        "created": False,
        "removed": False,
        "verified_removed": None,
        "tags_before": sorted(existing),
        "tags_after": None,
    }
    _record_temporary_node(record)
    primary: BaseException | None = None
    created = False
    try:
        numerical.create(tag, feature_type)
        created = True
        record["created"] = True
        yield model.java.result().numerical(tag)
    except BaseException as exc:
        primary = exc
    cleanup: BaseException | None = None
    if created:
        try:
            numerical.remove(tag)
            record["removed"] = True
        except BaseException as exc:
            cleanup = exc
            record["cleanup_error"] = {"type": type(exc).__name__, "message": str(exc)[:300]}
    if created:
        # The removal claim is verified against the engine's own tag list, not
        # against the absence of an exception.
        try:
            remaining = sorted(str(item) for item in list(numerical.tags()))
            record["tags_after"] = remaining
            record["verified_removed"] = tag not in remaining
            if not record["verified_removed"] and cleanup is None:
                record["cleanup_error"] = {
                    "type": "VerificationFailed",
                    "message": f"the temporary node {tag!r} is still listed after its removal",
                }
                cleanup = NumericalCleanupError(
                    f"the temporary numerical node {tag!r} is still listed after its removal"
                )
        except BaseException as exc:
            record["tags_after"] = None
            record["verified_removed"] = None
            record["verification_error"] = {"type": type(exc).__name__, "message": str(exc)[:300]}
            if cleanup is None:
                cleanup = NumericalCleanupError(
                    f"the temporary numerical node {tag!r} removal could not be verified: {exc}"
                )
    _raise_with_cleanup_error(primary, cleanup)


def _feature_value(feature: Any, *, data_fallback: bool = False) -> Any:
    """Return a non-lossy real/imag structure when the API identifies complex data."""
    getter = getattr(feature, "getData", None) if data_fallback else getattr(feature, "getReal", None)
    if not callable(getter):
        raise RuntimeError("Numerical feature does not expose the requested result accessor.")
    real = _coerce_eval_value(getter())
    complex_flag = False
    checker = getattr(feature, "isComplex", None)
    if callable(checker):
        complex_flag = bool(checker())
    if complex_flag:
        imag_getter = getattr(feature, "getImagData" if data_fallback else "getImag", None)
        if not callable(imag_getter):
            raise RuntimeError("Complex numerical result cannot be returned safely: getImag is unavailable (W17 typed complex support pending).")
        try:
            return {"real": real, "imag": _coerce_eval_value(imag_getter())}
        except Exception as exc:
            raise RuntimeError(f"Complex numerical result cannot be returned safely: getImag failed: {exc}") from exc
    return real


def _select_inner(values: Any, time_point: str) -> Any:
    """Select the solution axis, retaining expression and spatial dimensions."""
    if isinstance(values, dict):
        return {key: _select_inner(value, time_point) for key,value in values.items()}
    if time_point in ("", "all"):
        return values
    if time_point in ("first", "last"):
        index = 0 if time_point == "first" else -1
    elif time_point.isdigit() and int(time_point)>0:
        index = int(time_point)-1
    else:
        raise ValueError("time_point must be first, last, all, or a positive solution index.")
    return [[row[index]] for row in values]


def _value_shape(value: Any) -> list[int]:
    """The dimension shape of an evaluated structure (C04 records the result shape)."""
    shape: list[int] = []
    current = value
    while isinstance(current, (list, tuple)):
        shape.append(len(current))
        current = current[0] if current else None
    return shape


def _value_is_empty(value: Any) -> bool:
    """True when the engine published no value at all (no scalar leaf anywhere).

    ``[]`` from ``getReal()`` is *not* a value: on a model whose results route has no
    compatible dataset, the engine answers an empty matrix and no error, so the reply has to
    report that instead of publishing ``ok: true`` with nothing in it (m1d W13_T006:
    ``q1_probe: expected 2.0, observed None`` next to ``"value": [], "ok": true``).
    """
    if isinstance(value, Mapping):
        return all(_value_is_empty(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return all(_value_is_empty(item) for item in value)
    return value is None


def _engine_probe(node: Any, method: str, *args: Any) -> tuple[Any, str | None]:
    """Call an optional engine accessor, reporting - never hiding - a failure."""
    getter = getattr(node, method, None)
    if not callable(getter):
        return None, f"{method}() is not exposed by this engine handle"
    try:
        return getter(*args), None
    except BaseException as exc:  # noqa: BLE001 - a probe reports, it never raises
        return None, f"{type(exc).__name__}: {str(exc)[:200]}"


def _engine_owner(model: Any) -> tuple[Any, str | None]:
    """The engine's own ``result()`` accessor of a model handle, or why it is unavailable."""
    java = getattr(model, "java", None)
    if java is None:
        return None, "the model handle exposes no java accessor"
    return _engine_probe(java, "result")


def _dataset_inventory(model: Any) -> dict[str, Any]:
    """Read the model's datasets and stored solutions from the engine, without inferring any.

    The evaluation's data binding is engine state, so it is read back through the documented
    collections (``model.result().dataset().tags()`` and the dataset's own ``solution``/``getType``
    properties, ``model.sol().tags()``).  Nothing here picks a dataset from a name convention: when
    the collection cannot be read the inventory says so and no binding is claimed.
    """
    inventory: dict[str, Any] = {
        "datasets": [], "solutions": [], "readback": {},
        "source": "model.result().dataset().tags() + dataset getType()/getString('solution'); model.sol().tags()",
    }
    results, result_error = _engine_owner(model)
    if results is None:
        inventory["reason"] = f"the model's result collection is not readable ({result_error})"
        return inventory
    collection, collection_error = _engine_probe(results, "dataset")
    if collection is None:
        inventory["reason"] = f"the model publishes no dataset collection ({collection_error})"
        return inventory
    tags, tags_error = _engine_probe(collection, "tags")
    if tags is None:
        inventory["reason"] = f"the dataset tag list is not readable ({tags_error})"
        return inventory
    inventory["readback"]["dataset_tags"] = [str(tag) for tag in list(tags)]
    for tag in inventory["readback"]["dataset_tags"]:
        node, node_error = _engine_probe(collection, "__call__", tag)
        row: dict[str, Any] = {"tag": tag, "type": None, "solution": None}
        if node is None:
            row["error"] = node_error
        else:
            node_type, _type_error = _engine_probe(node, "getType")
            row["type"] = node_type if isinstance(node_type, str) else None
            solution, _solution_error = _engine_probe(node, "getString", "solution")
            row["solution"] = solution if isinstance(solution, str) and solution else None
        inventory["datasets"].append(row)
    solutions, solutions_error = _engine_probe(getattr(model, "java", None), "sol")
    if solutions is not None:
        tags, _solution_tags_error = _engine_probe(solutions, "tags")
        if tags is not None:
            inventory["readback"]["solution_tags"] = [str(tag) for tag in list(tags)]
    inventory["solutions"] = list(inventory["readback"].get("solution_tags") or [])
    if not inventory["datasets"]:
        inventory["reason"] = ("the engine lists no dataset at all for this model; a results-node "
                              "evaluation has no data to read")
    return inventory


def _evaluation_binding(model: Any) -> dict[str, Any]:
    """The dataset/solution an evaluation is bound to, read back from the engine (C04).

    The documented default of an evaluation feature's ``data`` property is *First compatible
    dataset*; when this model publishes one, it is named explicitly so the reply can say which
    dataset/solution a value belongs to, and so a model that carries a solution is never left to
    the engine's implicit choice.  When the engine's own dataset readback proves that the model
    publishes no dataset at all, the feature is bound to the documented value ``none`` instead
    (doc 4454) - the default would resolve to nothing there; :func:`_bind_evaluation_dataset`
    performs and verifies whichever of the two states applies.
    """
    inventory = _dataset_inventory(model)
    chosen: dict[str, Any] | None = None
    stored = set(inventory["solutions"])
    for row in inventory["datasets"]:
        if row.get("solution") and (not stored or row["solution"] in stored):
            chosen = row
            break
    if chosen is None:
        for row in inventory["datasets"]:
            if row.get("solution"):
                chosen = row
                break
    if chosen is None and inventory["datasets"]:
        chosen = inventory["datasets"][0]
    binding: dict[str, Any] = {
        "dataset": (chosen or {}).get("tag"),
        "solution": (chosen or {}).get("solution"),
        "dataset_type": (chosen or {}).get("type"),
        "inventory": inventory,
        "policy": ("the evaluation feature's documented ``data`` property: a named dataset is set and "
                   "read back when the model publishes one; when the engine's own readback proves that "
                   "the model publishes no dataset, the documented value ``none`` is set and read back "
                   "instead of leaving the *First compatible dataset* default in place"),
    }
    if chosen is None:
        binding["reason"] = inventory.get("reason") or "the model publishes no dataset to bind"
    return binding


# ---------------------------------------------------------------------------
# The evaluation feature's documented ``data`` property (m1e, W13_T006)
# ---------------------------------------------------------------------------
#: The property that names the dataset an evaluation feature reads.
_EVALUATION_DATA_PROPERTY = "data"

#: The documented value of that property that states the evaluation needs no solution data.
_EVALUATION_DATA_NONE = "none"

#: Local-corpus citations for the data-independent results route.  ``data`` has a *finite documented
#: value set*, and ``none`` is part of it on every evaluation feature this module creates - so an
#: evaluation on a model that publishes no dataset is a documented configuration, not an empty read
#: waiting to happen (m1e W13_T006 read ``value_shape [0]`` next to ``ok: true`` because the property
#: was left at its *First compatible dataset* default on a model that has no dataset).
_EVALUATION_DATA_CITATIONS: tuple[dict[str, str], ...] = (
    {
        "claim": ("an evaluation feature's ``data`` property takes ``none | parent | dataset name`` and "
                  "defaults to *First compatible dataset*; ``none`` is the documented value for an "
                  "evaluation that refers to no dataset"),
        "doc_id": "4454",
        "title": "COMSOL 6.4 - EvalGlobal",
        "path": "doc/help/wtpwebapps/ROOT/doc/com.comsol.help.comsol/comsol_api_results.52.051.html",
        "sha256": "17429de155e56bb06014875e959117db215db23bb12baf7bb95daffa7e77d445",
    },
    {
        "claim": ("the API-only ``Global`` numerical feature publishes the same ``data`` values "
                  "(``none | parent | dataset name``, default *First compatible dataset*)"),
        "doc_id": "4469",
        "title": "COMSOL 6.4 - Global (Numerical)",
        "path": "doc/help/wtpwebapps/ROOT/doc/com.comsol.help.comsol/comsol_api_results.52.066.html",
        "sha256": "4365366d59e13e3d52a13995a3c219065673d88d4ccf5150413e400331926d77",
    },
    {
        "claim": "the ``Eval`` feature publishes the same ``none`` value for ``data`` (``none | dataset name``)",
        "doc_id": "4452",
        "title": "COMSOL 6.4 - Eval",
        "path": "doc/help/wtpwebapps/ROOT/doc/com.comsol.help.comsol/comsol_api_results.52.049.html",
        "sha256": "0b8b0aa399980893752fbe8f8b7d5085f81204f037f9cdcab9ac4f12da6db3ec",
    },
    {
        "claim": ("``String[] getAllowedPropertyValues(String name)`` returns the allowed values of a named "
                  "property when it is a finite set, so the engine itself can be asked which ``data`` values "
                  "are legal on this build"),
        "doc_id": "4080",
        "title": "COMSOL 6.4 - Methods Associated to Set, SetIndex, and the Various Get Methods",
        "path": "doc/help/wtpwebapps/ROOT/doc/com.comsol.help.comsol/comsol_api_general.47.09.html",
        "sha256": "36e103782ccbc0c0306644743cbd480542379dd20bd36220db5db78f2133c151",
    },
    {
        "claim": ("a shipped application example selects ``None`` in a results node's *Dataset* list - used "
                  "there to evaluate expressions that the selected dataset cannot resolve"),
        "doc_id": "17625",
        "title": "COMSOL 6.4 model example - Double-Pendulum Dynamics",
        "path": ("doc/help/wtpwebapps/ROOT/doc/com.comsol.help.models.mbd.double_pendulum/"
                 "models.mbd.double_pendulum.pdf"),
        "sha256": "01bbc440e99422fc85ad47e79cfe119f0ab620d8e0bf74035f0730b591ffbd25",
    },
)

#: Local-corpus citations for the *other* documented evaluator, ``model.param().evaluate``: it resolves
#: the collection of **global** model parameters, so it can never answer for a component variable
#: (m1e W13_T006 ``q1``: ``FlException: Unknown_model_parameter`` on both ``q1`` and ``comp1.q1``).
_GLOBAL_PARAMETER_EVALUATOR_CITATIONS: tuple[dict[str, str], ...] = (
    {
        "claim": ("``model.param()`` is a collection of *global* model parameters and "
                  "``model.param().evaluate(<param>)`` evaluates the value of the parameter; a component "
                  "variable is not a parameter of that collection"),
        "doc_id": "4121",
        "title": "COMSOL 6.4 - model.param() and model.result().param()",
        "path": "doc/help/wtpwebapps/ROOT/doc/com.comsol.help.comsol/comsol_api_general.47.50.html",
        "sha256": "6af88c1366f47cb021e76d1dad4ae72ff407adfac74b9e91180912060f57e99a",
    },
    {
        "claim": ("the Application Programming Guide's *Accessing a Global Parameter* section evaluates a "
                  "global parameter with ``model.param().evaluate(\"L\")``; variables are a separate "
                  "collection, accessed through ``model.variable(<tag>)``"),
        "doc_id": "3961",
        "title": "COMSOL 6.4 - Parameters and Variables",
        "path": "doc/help/wtpwebapps/ROOT/doc/com.comsol.help.comsol/application_programming_guide.15.21.html",
        "sha256": "b1c830ce7d4511c3a28a19a1ae041e4e737242fbc414f9bd6a062421b9412b11",
    },
)

#: ``chunk`` ids of the two property tables these records quote, for a reader of the corpus.
_EVALUATION_DATA_CHUNKS = {"4454": 17235, "4452": 17232, "4469": 17261, "4080": 16690, "4121": 9676, "3961": 16481}

#: ``javap -cp plugins/com.comsol.api_1.0.0.jar com.comsol.model.PropFeature`` (installed COMSOL
#: 6.4.0.293 API jar): the property accessors the route below uses are on the feature handle itself.
#: ``set(String,String)`` / ``getString(String)`` / ``getAllowedPropertyValues(String)``.
_EVALUATION_PROPERTY_ACCESSORS = ("set(String,String)", "getString(String)",
                                  "getAllowedPropertyValues(String)")


def _citation_rows(citations: Sequence[Mapping[str, str]]) -> list[dict[str, Any]]:
    """A fresh copy of a citation tuple, so a published record never shares mutable state."""
    rows: list[dict[str, Any]] = []
    for row in citations:
        copied: dict[str, Any] = dict(row)
        chunk = _EVALUATION_DATA_CHUNKS.get(str(copied.get("doc_id")))
        if chunk is not None:
            copied["chunk_id"] = chunk
        rows.append(copied)
    return rows


def _documented_data_state(binding: Mapping[str, Any] | None) -> str | None:
    """``none`` when the engine's own readback *proves* the model publishes no dataset.

    ``None`` means "not proven here" - no inventory at all, or a dataset collection that could not be
    read.  The feature's ``data`` property is then left at its documented *First compatible dataset*
    default instead of being pinned to ``none`` on an unread state.
    """
    if not isinstance(binding, Mapping) or binding.get("dataset"):
        return None
    inventory = binding.get("inventory")
    if not isinstance(inventory, Mapping):
        return None
    readback = inventory.get("readback")
    if not isinstance(readback, Mapping):
        return None
    tags = readback.get("dataset_tags")
    return _EVALUATION_DATA_NONE if isinstance(tags, list) and not tags else None


def _engine_property_metadata(feature: Any, name: str) -> dict[str, Any]:
    """The engine's own metadata for one property of a feature (doc 4080 / the G2 vocabulary).

    ``getValueType`` and ``getAllowedPropertyValues`` are the documented metadata accessors of a
    property; the shared reader of :mod:`comsol_mcp._g2_contract` is used so the ``data`` property of
    this route is described exactly as every other property write point in the package describes one
    (value type, getter, Java signature, allowed values).  When the build publishes no allowed-value
    set, the engine's own message is read directly and kept - a missing metadata is recorded, never
    guessed at.
    """
    probe: dict[str, Any] = {"name": name}
    try:
        from ._g2_contract import property_schema_from_engine

        probe = dict(property_schema_from_engine(feature, name))
    except BaseException as exc:  # noqa: BLE001 - metadata is evidence, never a hard dependency
        probe["metadata_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    values = probe.get("allowed_values")
    if values is not None and not isinstance(values, list):
        values = [str(item) for item in list(values)]
    if values is None:
        _raw, raw_error = _engine_probe(feature, "getAllowedPropertyValues", name)
        probe["allowed_values_error"] = raw_error or "the engine published no allowed-value set for this property"
    else:
        probe["allowed_values_error"] = None
    probe["allowed_values"] = values
    return probe


def _bind_evaluation_dataset(feature: Any, binding: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Set the feature's documented ``data`` property and verify the readback.

    Two documented states are set explicitly, and both are read back:

    * a **named dataset** whenever the model publishes one (``data`` = the dataset tag);
    * ``none`` - the documented data-independent value - when the engine's own dataset readback
      *proves* the model publishes no dataset at all.  The documented default is *First compatible
      dataset*; on a model without a dataset that default resolves to nothing, so the engine answers
      an empty matrix and no error (m1e W13_T006: ``value_shape [0]`` next to ``ok: true``).  This
      route sets ``none`` *instead of* that default, before the feature runs and before the engine's
      global-parameter evaluator is asked.

    Nothing is claimed without a readback: the record carries the state the route asked for
    (``data_mode``), what the engine read back, whether that matched (``bound_verified``) and, when
    it did not, the engine's own message (``bind_error``) - plus, from the engine itself
    (``getAllowedPropertyValues(String)``), which values this build allows for the property.  A
    refusal to set the property is recorded, never fatal: the documented default may still apply.
    """
    if not isinstance(binding, Mapping):
        return None
    record = dict(binding)
    record.pop("inventory", None)
    tag = binding.get("dataset")
    wanted = str(tag) if tag else _documented_data_state(binding)
    record["data_mode"] = "dataset" if tag else wanted
    if not wanted:
        # Nothing was set: either the model publishes a dataset and the tag was set above, or its
        # dataset collection could not be read - and an unproven state is never pinned to ``none``.
        record["bound"] = None
        record["bound_verified"] = None
        if not tag:
            record["data_reason"] = (
                "the engine's dataset readback does not prove that this model publishes no dataset, so "
                "the documented *First compatible dataset* default is left in place")
        return record
    setter = getattr(feature, "set", None)
    if not callable(setter):
        record["bound"] = None
        record["bound_verified"] = None
        record["bind_error"] = "the evaluation feature exposes no setter for the data property"
        return record
    if not tag:
        record["data_citation"] = _citation_rows(_EVALUATION_DATA_CITATIONS)
    try:
        setter(_EVALUATION_DATA_PROPERTY, wanted)
    except BaseException as exc:  # noqa: BLE001 - reported, never fatal: the default may still apply
        record["bound"] = None
        record["bound_verified"] = False
        record["bind_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        return record
    readback, readback_error = _engine_probe(feature, "getString", _EVALUATION_DATA_PROPERTY)
    record["bound"] = readback if isinstance(readback, str) and readback else wanted
    record["bound_verified"] = bool(isinstance(readback, str) and readback == wanted)
    record["bind_error"] = None if record["bound_verified"] else (
        readback_error or f"the engine read back {readback!r} instead of the requested value {wanted!r}")
    if not tag:
        # The engine's own answer to "is ``none`` a legal ``data`` value on this build?" - read with
        # the shared property-metadata vocabulary, so the record also says which getter/Java signature
        # this property has.
        probe = _engine_property_metadata(feature, _EVALUATION_DATA_PROPERTY)
        record["data_property"] = probe
        values = probe["allowed_values"]
        record["allowed_values"] = values
        record["allowed_values_error"] = probe["allowed_values_error"]
        record["allowed_values_include_none"] = (
            _EVALUATION_DATA_NONE in values) if values is not None else None
    return record


def _expression_evaluator_candidates(model: Any, expression: str) -> list[str]:
    """The expression texts to try at the model expression evaluator, in order.

    A component variable is addressed with its component qualifier in the model expression scope,
    so the qualified spelling is tried *after* the expression as given - never instead of it, and
    only when the model has exactly one component (otherwise no single qualification is defined).
    """
    candidates = [expression]
    java = getattr(model, "java", None)
    components = None
    if java is not None:
        components, _error = _engine_probe(java, "component")
    if components is not None:
        tags, _tags_error = _engine_probe(components, "tags")
        tags = [str(tag) for tag in list(tags)] if tags is not None else []
        if len(tags) == 1 and not expression.startswith(f"{tags[0]}."):
            candidates.append(f"{tags[0]}.{expression}")
    return candidates


def _global_parameter_scope(model: Any, container: Any, expression: str) -> dict[str, Any]:
    """What the engine's ``model.param()`` evaluator can resolve, read from the engine itself.

    ``model.param()`` is the collection of **global** model parameters (doc 4121): the evaluator
    documented for it answers for a parameter, never for a component variable, which is why every
    spelling of ``q1`` was refused with ``FlException: Unknown_model_parameter`` in the m1e run.  The
    parameter names are read back so the reply states whether the requested expression could ever be
    resolved by this leg - and the citations say which document says so.
    """
    names, names_error = _engine_probe(container, "varnames") if container is not None else (
        None, "the model expression evaluator is not reachable")
    parameter_names = [str(name) for name in list(names)] if names is not None else None
    return {
        "evaluator": ("model.param() - the collection of *global* model parameters; "
                      "model.param().evaluate(<param>) evaluates the value of the parameter"),
        "resolves": "global model parameters (and expressions over them) only",
        "global_parameters": parameter_names,
        "global_parameters_error": None if parameter_names is not None else names_error,
        "expression_is_global_parameter": (
            None if parameter_names is None else expression in parameter_names),
        "note": ("a component variable is not a parameter of this collection: the documented accessor "
                 "for variables is model.variable(<tag>), and this leg is therefore tried only *after* "
                 "the data-independent results route (data='none')"),
        "citations": _citation_rows(_GLOBAL_PARAMETER_EVALUATOR_CITATIONS),
    }


def _evaluate_engine_expression(model: Any, expression: str) -> tuple[Any, dict[str, Any]]:
    """Evaluate a data-independent expression with the engine's own documented evaluator.

    ``ParamBase`` documents ``double evaluate(String expression)`` ("Evaluates an expression,
    including functions, parameters and units") together with ``evaluateComplex``; the Application
    Programming Guide's "Accessing a Global Parameter" section uses exactly
    ``model.param().evaluate("L")``.

    Scope (m1e W13_T006): that collection holds the **global model parameters** (doc 4121), so this
    leg can only ever answer for a global parameter or an expression over them - a component variable
    such as ``q1`` is refused by the engine itself (``FlException: Unknown_model_parameter``).  It is
    therefore the *last* leg of the chain: the results route is asked first, with the documented
    ``data`` state set explicitly, and this leg only when that route read no values at all.  Whatever
    the engine answers - the value or its own refusal message - is recorded per attempt, together with
    the read-back scope above; nothing is computed in Python.
    """
    record: dict[str, Any] = {
        "route": "engine:model.param().evaluate",
        "reason": ("the results-node route published no value; the model expression evaluator is the "
                   "engine's own documented evaluator for the expressions that need no solution data, "
                   "and it resolves the *global* model parameters (doc 4121)"),
        "attempts": [],
        "dataset": None,
        "solution": None,
    }
    java = getattr(model, "java", None)
    container = None
    container_error = "the model handle exposes no java accessor"
    if java is not None:
        container, container_error = _engine_probe(java, "param")
    record["scope"] = _global_parameter_scope(model, container, expression)
    if container is None:
        record["refused"] = f"the model expression evaluator is not reachable ({container_error})"
        for text in _expression_evaluator_candidates(model, expression):
            record["attempts"].append({"expression": text, "ok": False, "error": record["refused"]})
        return None, record
    value: Any = None
    for text in _expression_evaluator_candidates(model, expression):
        real, real_error = _engine_probe(container, "evaluate", text)
        if real_error is None and isinstance(real, (int, float)) and not isinstance(real, bool):
            record["attempts"].append({"expression": text, "call": "evaluate", "ok": True,
                                       "value": float(real)})
            record["evaluated_expression"] = text
            record["engine_call"] = "model.param().evaluate(String)"
            return float(real), record
        complex_value, complex_error = _engine_probe(container, "evaluateComplex", text)
        if (complex_error is None and isinstance(complex_value, (list, tuple))
                and len(complex_value) == 2):
            real_part, imag_part = complex_value
            record["attempts"].append({"expression": text, "call": "evaluateComplex", "ok": True,
                                       "value": {"real": float(real_part), "imag": float(imag_part)}})
            record["evaluated_expression"] = text
            record["engine_call"] = "model.param().evaluateComplex(String)"
            return {"real": float(real_part), "imag": float(imag_part)}, record
        record["attempts"].append({"expression": text, "ok": False, "evaluate_error": real_error,
                                   "evaluateComplex_error": complex_error})
        if value is None:
            value = real if real is not None else complex_value
    record["refused"] = ("the engine's expression evaluator refused every spelling of this expression: "
                         "it resolves the collection of *global* model parameters, so a component "
                         "variable is never resolvable here (see scope.expression_is_global_parameter); "
                         "its own message is recorded per attempt")
    return None, record


def _configure_eval(feature: Any, expression: str, time_point: str,
                    binding: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    feature.set("expr", [expression])
    # Eval lacks looplevelinput; stationary datasets have no loop level.
    # Fetch the API's complete shape and select the solution axis explicitly.
    # C04: when the model publishes a dataset, the feature is bound to it *by name* and the
    # readback is recorded, so the reply can say which dataset/solution the value belongs to.
    return _bind_evaluation_dataset(feature, binding)


def _eval_engine_calls(data_value: Any = None, *, reader: str = "getReal") -> list[str]:
    """The engine calls one evaluation leg makes, including its ``data`` property write.

    ``data_value`` is what the feature's documented ``data`` property carries when the leg runs
    (a dataset tag, ``none``, or ``None`` when nothing was set).
    """
    calls = ["result().numerical(<owned tag>).set('expr', ...)"]
    if data_value:
        calls.append(f"result().numerical(<owned tag>).set('{_EVALUATION_DATA_PROPERTY}', '{data_value}')")
    calls.append("result().numerical(<owned tag>).run()")
    calls.append(f"result().numerical(<owned tag>).{reader}()")
    return calls


def _evaluate_expression_safely(model: Any, expression: str, time_point: str = "last",
                                provenance: dict[str, Any] | None = None) -> Any:
    """Evaluate via owned result nodes, never MPh ``model.evaluate``.

    MPh 1.4 can leak result nodes on both successful and failing evaluation.
    The returned Java result is deliberately kept intact (rather than sliced)
    so this safety fix does not silently discard time/result dimensions.

    ``provenance`` is the C04 sink: when a caller passes one, this function records which engine
    route produced the value, the dataset/solution it was bound to and how the binding was
    verified.  That sink selects the diagnosing chain, which has three legs:

    1. the owned ``EvalGlobal`` node, with its documented ``data`` property **set explicitly** - to
       the named dataset when the model publishes one, otherwise to the documented ``none`` value
       (doc 4454).  On a model that publishes no dataset the documented default (*First compatible
       dataset*) resolves to nothing and the read is empty without an error (m1e W13_T006:
       ``value_shape [0]``), so the data-independent state is stated instead of assumed;
    2. the engine's own global-parameter evaluator (``model.param().evaluate``) - only when leg 1
       read no value at all, and only meaningful for the *global* model parameters (doc 4121): a
       component variable is refused by the engine there and the refusal is kept verbatim;
    3. a truthful failure - an empty read is reported with the route it took, the data state it ran
       in and every engine message, and is never published as a value.

    A caller that passes no sink keeps the engine's documented default (the results feature is left
    to *First compatible dataset*), exactly as before.
    """
    record = provenance if isinstance(provenance, dict) else None
    try:
        with _temporary_numerical_feature(model, "EvalGlobal") as feature:
            binding = _evaluation_binding(model) if record is not None else None
            inventory = (binding or {}).get("inventory")
            bound = _configure_eval(feature, expression, time_point, binding)
            if isinstance(bound, Mapping):
                binding = bound
            feature.run()
            value = _select_inner(_feature_value(feature), time_point)
            datum = binding if isinstance(binding, Mapping) else {}
            data_value = datum.get("bound")
            data_mode = datum.get("data_mode")
            if record is not None:
                record["route"] = "results:EvalGlobal"
                record["result_source"] = "getReal"
                record["data_mode"] = data_mode
                record["engine_calls"] = _eval_engine_calls(data_value)
                record["attempts"] = [{"route": "results:EvalGlobal", "data": data_value, "ok": True,
                                       "value_shape": _value_shape(value)}]
                if binding is not None:
                    record["dataset"] = binding.get("dataset")
                    record["solution"] = binding.get("solution")
                    record["binding"] = {key: value_ for key, value_ in binding.items()
                                         if key != "inventory"}
                    record["dataset_inventory"] = inventory or binding.get("inventory")
            if record is None or not _value_is_empty(value):
                return value
            if _value_is_empty(value):
                for candidate in _expression_evaluator_candidates(model, expression)[1:]:
                    try:
                        feature.set("expr", [candidate])
                        feature.run()
                        candidate_value = _select_inner(_feature_value(feature), time_point)
                        if not _value_is_empty(candidate_value):
                            value = candidate_value
                            if record is not None:
                                record["evaluated_expression"] = candidate
                                record["attempts"].append({
                                    "route": "results:EvalGlobal",
                                    "expression": candidate,
                                    "data": data_value,
                                    "ok": True,
                                    "value_shape": _value_shape(value),
                                })
                            return value
                    except Exception:
                        pass
            # The results route answered with no values at all (an empty matrix, no error) although its
            # documented ``data`` property was set *before* it ran - to the named dataset, or to the
            # documented ``none`` state when the model publishes no dataset (the state m1e W13_T006 was
            # missing: the feature kept the *First compatible dataset* default and read nothing).  A
            # constant, a parameter or a variable does not need solution data, so the engine's own
            # documented evaluator is asked next; its scope says it resolves the *global* model
            # parameters only, and the results-route attempt is kept either way.
            fallback_value, fallback = _evaluate_engine_expression(model, expression)
            record["attempts"].append({key: value_ for key, value_ in fallback.items()
                                       if key not in {"attempts", "reason", "scope", "citations"}})
            if isinstance(fallback.get("attempts"), list) and fallback["attempts"]:
                record["expression_evaluator_attempts"] = fallback["attempts"]
                first = fallback["attempts"][0]
                record["attempts"][-1].setdefault("error", first.get("evaluate_error") or first.get("error"))
            record["expression_evaluator"] = fallback
            if fallback_value is not None:
                record["route"] = fallback["route"]
                record["result_source"] = fallback.get("engine_call")
                record["dataset"] = None
                record["solution"] = None
                record["shape"] = _value_shape(fallback_value)
                record["evaluated_expression"] = fallback.get("evaluated_expression")
                return fallback_value
            record["empty_results_read"] = {
                "reason": ("the results-node route returned no value at all for this expression in its "
                           "documented data state (see ``data``), and the model expression evaluator - "
                           "which resolves the *global* model parameters only - did not answer either; "
                           "see expression_evaluator"),
                "route": "results:EvalGlobal",
                "data": data_value,
                "data_mode": data_mode,
                "dataset": datum.get("dataset"),
                "solution": datum.get("solution"),
                "result_shape": _value_shape(value),
                "citations": datum.get("data_citation") or _citation_rows(_EVALUATION_DATA_CITATIONS),
            }
            return value
    except NumericalCleanupError:
        raise
    except Exception as global_exc:
        # EvalGlobal is insufficient for spatial fields. Eval/getData is an
        # owned fallback and its independent failure is retained for diagnosis.
        datum: dict[str, Any] = {}
        try:
            with _temporary_numerical_feature(model, "Eval") as feature:
                binding = _evaluation_binding(model) if record is not None else None
                inventory = (binding or {}).get("inventory")
                bound = _configure_eval(feature, expression, time_point, binding)
                if isinstance(bound, Mapping):
                    binding = bound
                feature.run()
                value = _select_inner(_feature_value(feature, data_fallback=True), time_point)
                datum = binding if isinstance(binding, Mapping) else {}
                if record is not None:
                    record["route"] = "results:Eval(getData)"
                    record["result_source"] = "getData"
                    record["data_mode"] = datum.get("data_mode")
                    record["engine_calls"] = _eval_engine_calls(datum.get("bound"), reader="getData")
                    record.setdefault("attempts", []).append(
                        {"route": "results:Eval(getData)", "data": datum.get("bound"), "ok": True,
                         "value_shape": _value_shape(value)})
                    if binding is not None:
                        record["dataset"] = binding.get("dataset")
                        record["solution"] = binding.get("solution")
                        record["binding"] = {key: value_ for key, value_ in binding.items()
                                             if key != "inventory"}
                        record["dataset_inventory"] = inventory or binding.get("inventory")
                return value
        except Exception as eval_exc:
            if record is not None:
                data_value = datum.get("bound") if isinstance(datum, Mapping) else None
                record.setdefault("attempts", []).append(
                    {"route": "results:EvalGlobal", "data": data_value, "ok": False,
                     "error": str(global_exc)[:400]})
                record.setdefault("attempts", []).append(
                    {"route": "results:Eval(getData)", "ok": False, "error": str(eval_exc)[:400]})
            raise RuntimeError(f"EvalGlobal failed: {global_exc}; Eval/getData fallback failed: {eval_exc}") from eval_exc


def _evaluate_named_expressions(model: Any, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each expression entry must be an object.")
        name = str(item.get("name", "")).strip()
        expression = str(item.get("expression", "")).strip()
        if not name:
            raise ValueError("Expression name is required.")
        if not expression:
            raise ValueError(f'Expression is required for "{name}".')
        row: dict[str, Any] = {"name": name, "expression": expression}
        try:
            raw = _evaluate_expression_safely(model, expression)
            value = _coerce_eval_value(raw)
            row["value"] = value
            row["last_value"] = _coerce_eval_value(_last_scalar(value))
            row["ok"] = True
        except Exception as exc:
            row["ok"] = False
            row["error"] = str(exc)
        results.append(row)
    return results


def _numeric_result(name: str, expression: str, value: Any = None, *, ok: bool = True, error: str = "") -> dict[str, Any]:
    row: dict[str, Any] = {"name": name, "expression": expression, "ok": ok}
    if ok:
        row["value"] = value
        row["last_value"] = _coerce_eval_value(_last_scalar(value))
    else:
        row["error"] = error or "NA"
    return row


# ---------------------------------------------------------------------------
# Core metrics helpers
# ---------------------------------------------------------------------------
def _find_initialized_solution_tag(model: Any) -> str:
    for tag in list(model.java.sol().tags()):
        tag_str = str(tag)
        try:
            if model.java.sol(tag_str).isInitialized():
                return tag_str
        except Exception:
            continue
    return ""


def _read_last_time_day(model: Any, sol_tag: str) -> float | None:
    if not sol_tag:
        return None
    try:
        values = model.java.sol(sol_tag).getPVals()
        if values is not None and len(values) > 0:
            last_value = float(values[-1])
            return last_value / 86400.0 if abs(last_value) > 1e3 else last_value
    except Exception:
        return None
    return None


def _eval_global_last(model: Any, expression: str) -> float | None:
    try:
        return float(_last_scalar(_evaluate_expression_safely(model, expression, "last")))
    except Exception:
        return None


def _eval_domain_average_last(model: Any, expression: str, domains: list[int]) -> float | None:
    try:
        with _temporary_numerical_feature(model, "IntSurface") as feature:
            feature.selection().geom("geom1", 2)
            feature.selection().set(domains)
            feature.set("intvolume", True)
            feature.set("expr", [expression, "1"])
            feature.set("looplevelinput", ["last"])
            feature.run()
            values = feature.getReal()
            return float(values[0][0] / values[1][0])
    except Exception:
        return None


def _eval_boundary_average_last(model: Any, expression: str, boundaries: list[int]) -> float | None:
    try:
        with _temporary_numerical_feature(model, "IntLine") as feature:
            feature.selection().geom("geom1", 1)
            feature.selection().set(boundaries)
            feature.set("intsurface", True)
            feature.set("expr", [expression, "1"])
            feature.set("looplevelinput", ["last"])
            feature.run()
            values = feature.getReal()
            return float(values[0][0] / values[1][0])
    except Exception:
        return None


def _eval_extremum_last(model: Any, feature_type: str, expression: str, domains: list[int]) -> float | None:
    try:
        with _temporary_numerical_feature(model, feature_type) as feature:
            feature.selection().geom("geom1", 2)
            feature.selection().set(domains)
            feature.set("expr", [expression])
            feature.set("looplevelinput", ["last"])
            feature.run()
            return float(feature.getReal()[0][0])
    except Exception:
        return None


def _get_model_dimension(model: Any) -> int:
    """Get the configured or auto-detected spatial dimension of the model.

    Priority:
    1. Workflow state `model_dimension` (set by configure_single_main_workflow)
    2. Auto-detect from existing physics interfaces
    3. Default to 0 (unknown)
    """
    # Check workflow state first
    try:
        from comsol_mcp._state import _read_workflow_state
        state = _read_workflow_state()
        dim = int(state.get("model_dimension", 0))
        if dim in (1, 2, 3):
            return dim
    except Exception:
        pass

    # Auto-detect from existing physics interfaces
    try:
        comps = list(model.java.component().tags())
        if comps:
            comp = model.java.component(comps[0])
            phys_tags = list(comp.physics().tags())
            if phys_tags:
                phys = comp.physics(phys_tags[0])
                # Try common API methods for getting spatial dimension from physics
                for method_name in ("getNDim", "getSpatialDim", "sdim", "getDim"):
                    try:
                        val = getattr(phys, method_name)
                        if callable(val):
                            d = int(val())
                            if d in (1, 2, 3):
                                return d
                        else:
                            d = int(val)
                            if d in (1, 2, 3):
                                return d
                    except Exception:
                        continue
                # Try reading the physics selection dimension
                try:
                    sel = phys.feature().tags()
                    if sel:
                        feat = phys.feature(sel[0])
                        # The selection geom dimension reveals spatial dim
                        for attr_name in ("_geomDim", "dim"):
                            try:
                                d = int(getattr(feat.selection(), attr_name))
                                if d in (1, 2, 3):
                                    return d
                            except Exception:
                                continue
                except Exception:
                    pass
    except Exception:
        pass

    return 0


def _evaluate_aggregate(
    model: Any,
    expression: str,
    aggregate: str,
    domains: list[int] | None = None,
    boundaries: list[int] | None = None,
    time_point: str = "last",
) -> Any:
    """Evaluate an expression with aggregation. Returns scalar for aggregated, raw for 'none'.

    Automatically selects correct COMSOL result feature type based on model dimension:
    - 3D: domains→Volume, boundaries→Surface
    - 2D: domains→Surface, boundaries→Line
    - 1D: domains→Line, boundaries→Point
    """
    aggregate = (aggregate or "none").lower().strip()
    if aggregate == "none":
        return _evaluate_expression_safely(model, expression, time_point)

    # Get spatial dimension from workflow config or auto-detect
    dim = _get_model_dimension(model)
    if dim == 0:
        raise ValueError("Cannot aggregate without an explicit model dimension; configure the workflow or model geometry.")

    # Map: (dim, entity) → suffix for max/min/int
    # 3D: domains=Volume, boundaries=Surface
    # 2D: domains=Surface, boundaries=Line
    # 1D: domains=Line, boundaries=Point (rare)
    if boundaries:
        geom_dim = max(dim - 1, 1)  # boundary is (dim-1) entity
    elif domains:
        geom_dim = dim
    else:
        geom_dim = dim

    dim_suffix = {3: "Volume", 2: "Surface", 1: "Line"}.get(geom_dim, "Surface")

    # Choose correct COMSOL result feature type
    if aggregate == "max":
        if domains or boundaries:
            ftype = f"Max{dim_suffix}"
        else:
            ftype = f"Max{dim_suffix}"
    elif aggregate == "min":
        if domains or boundaries:
            ftype = f"Min{dim_suffix}"
        else:
            ftype = f"Min{dim_suffix}"
    elif aggregate == "avg":
        if domains or boundaries:
            ftype = f"Int{dim_suffix}"
        else:
            ftype = "EvalGlobal"
    elif aggregate == "integral":
        ftype = f"Int{dim_suffix}"
    else:
        raise ValueError(f'Unknown aggregate "{aggregate}". Use max, min, avg, integral, or none.')

    with _temporary_numerical_feature(model, ftype) as feature:
        if domains:
            feature.selection().geom("geom1", dim)
            feature.selection().set(domains)
        elif boundaries:
            feature.selection().geom("geom1", max(dim - 1, 1))
            feature.selection().set(boundaries)
        feature.set("expr", [expression, "1"] if aggregate == "avg" and (domains or boundaries) else [expression])
        feature.run()
        values = _select_inner(_feature_value(feature), time_point)
        if isinstance(values, dict):
            raise RuntimeError("Complex aggregation is not supported by this legacy metric path (W17 typed complex support pending).")
        if aggregate == "avg" and (domains or boundaries):
            denom = values[1][0]
            return float("nan") if denom == 0 else float(values[0][0] / denom)
        return values if time_point == "all" else float(values[0][0])


# ---------------------------------------------------------------------------
# Model tree
# ---------------------------------------------------------------------------
def _model_tree_data(model: Any) -> dict[str, Any]:
    from comsol_mcp._state import _safe_model_label, _safe_model_path
    java = model.java
    components = [str(tag) for tag in java.component().tags()]
    component_details = []
    for component in components:
        comp = java.component(component)
        component_details.append(
            {
                "tag": component,
                "geometries": [str(tag) for tag in comp.geom().tags()],
                "meshes": [str(tag) for tag in comp.mesh().tags()],
                "physics": [str(tag) for tag in comp.physics().tags()],
                "materials": [str(tag) for tag in comp.material().tags()],
            }
        )
    return {
        "label": _safe_model_label(model),
        "file_path": _safe_model_path(model),
        "components": components,
        "component_details": component_details,
        "parameters": [row["name"] for row in _parameter_rows(model)],
        "studies": [str(tag) for tag in java.study().tags()],
        "solutions": [str(tag) for tag in java.sol().tags()],
        "datasets": [str(tag) for tag in java.result().dataset().tags()],
        "results": [str(tag) for tag in java.result().tags()],
    }


# ---------------------------------------------------------------------------
# Geometry feature helpers
# ---------------------------------------------------------------------------
def _normalize_properties(properties_json: str) -> list[tuple[str, list[str]]]:
    raw = str(properties_json or "").strip()
    if not raw:
        return []
    parsed = json.loads(raw)
    items: list[dict[str, Any]]
    if isinstance(parsed, dict):
        items = []
        for name, value in parsed.items():
            if isinstance(value, list):
                items.append({"name": name, "values": [str(item) for item in value]})
            else:
                items.append({"name": name, "value": str(value)})
    elif isinstance(parsed, list):
        items = parsed
    else:
        raise ValueError("properties_json must be a JSON object or array.")

    normalized: list[tuple[str, list[str]]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each properties_json entry must be an object.")
        name = str(item.get("name", "")).strip()
        if not name:
            raise ValueError("Property name is required.")
        if "values" in item:
            values = item.get("values")
            if not isinstance(values, list):
                raise ValueError(f'Property "{name}" values must be an array.')
            normalized.append((name, [str(value) for value in values]))
        elif "value" in item:
            normalized.append((name, [str(item.get("value", ""))]))
        else:
            raise ValueError(f'Property "{name}" must include value or values.')
    return normalized


def _ensure_component_java(model: Any, component: str, dimension: int) -> dict[str, Any]:
    java = model.java
    if component not in list(java.component().tags()):
        java.component().create(component, True)
    return {"component": component, "dimension": dimension}


def _ensure_geometry_java(model: Any, component: str, geometry: str, dimension: int) -> dict[str, Any]:
    java = model.java
    if component not in list(java.component().tags()):
        java.component().create(component, True)
    if dimension <= 0:
        dimension = 2
    if geometry not in list(java.component(component).geom().tags()):
        java.component(component).geom().create(geometry, dimension)
    return {"component": component, "geometry": geometry, "dimension": dimension}


def _ensure_mesh_java(model: Any, component: str, mesh: str) -> dict[str, Any]:
    java = model.java
    if component not in list(java.component().tags()):
        java.component().create(component, True)
    if mesh not in list(java.component(component).mesh().tags()):
        java.component(component).mesh().create(mesh)
    return {"component": component, "mesh": mesh}


def _apply_feature_properties(feature: Any, properties: list[tuple[str, list[str]]]) -> list[dict[str, Any]]:
    """Apply prevalidated properties without claiming setter failures are atomic."""
    from comsol_mcp._state import ToolExecutionError

    applied: list[dict[str, Any]] = []
    for index, (name, values) in enumerate(properties):
        entry = {"name": name, "value": values[0] if values else ""} if len(values) <= 1 else {"name": name, "values": values}
        try:
            feature.set(name, (values[0] if values else "") if len(values) <= 1 else values)
        except Exception as exc:
            raise ToolExecutionError(
                "Property batch was partially applied; the failing setter may have changed engine state.",
                data={
                    "applied": applied,
                    "failed": {**entry, "error": str(exc)},
                    "not_executed": [
                        {"name": later_name, "value": later_values[0] if len(later_values) <= 1 and later_values else ""}
                        if len(later_values) <= 1 else {"name": later_name, "values": later_values}
                        for later_name, later_values in properties[index + 1 :]
                    ],
                    "partial_change": bool(applied),
                    "failed_item_may_have_changed": True,
                    "safe_retry": False,
                },
            ) from exc
        applied.append(entry)
    return applied


def _assert_existing_feature_type(feature: Any, requested_type: str, *, kind: str = "feature") -> None:
    """Allow idempotent same-type creation, never silently reuse another type."""
    try:
        actual_type = str(feature.getType())
    except Exception as exc:
        raise RuntimeError(f"Cannot verify existing {kind} type before reuse: {exc}") from exc
    if actual_type != str(requested_type):
        raise ValueError(
            f'Existing {kind} type conflict: requested "{requested_type}", found "{actual_type}".'
        )
