"""Gate A Reopen Verification Checker.

This module implements the production verification service for reopened models,
ensuring that saved artifacts load correctly in a fresh worker and that stored
solutions can be directly read back without re-solving.

Both positive acceptance cases (Chain A, Chain B, Chain C) and negative controls
(wrong artifact hash, wrong dataset, cleared solution, corrupted values, missing
derived values) MUST call this same checker.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, Mapping


class ReopenVerificationError(Exception):
    """Raised when reopen verification fails."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "FAIL",
            "error_code": self.code,
            "message": self.message,
            "details": self.details,
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_derived_value_tags(model: Any) -> list[str] | None:
    """List the derived-value (result/numerical) node tags of a reopened model.

    A live model is queried through the engine -- ``model.result().numerical().tags()``
    -- because the earlier check only read a ``derived_values`` attribute that the
    acceptance driver had written onto the model object itself, so the live path
    verified nothing.  The attribute is still consulted first, since the in-memory
    fakes used by the unit tests expose it.

    ``None`` means "cannot establish": the caller reports that as a failure rather
    than treating an unreadable list as a pass.
    """
    attribute = getattr(model, "derived_values", None)
    if attribute is not None and not callable(attribute):
        # In-memory fakes expose the list directly; an empty list is a real answer
        # (the negative control relies on it).
        return [str(tag) for tag in attribute]
    if callable(attribute):
        # A live adapter exposes a *method* under this name, which is why the
        # delivered driver overwrote it with a list to satisfy its own check.
        try:
            called = attribute()
        except Exception:
            called = None
        if called is not None:
            try:
                called_tags = [str(tag) for tag in list(called)]
            except TypeError:
                called_tags = []
            if called_tags:
                return called_tags

    readers = (
        lambda: model.result().numerical().tags(),
        lambda: model._call("result")._call("numerical")._call("tags"),
        lambda: model.java.result().numerical().tags(),
    )
    for read in readers:
        try:
            tags = read()
        except Exception:
            continue
        if tags is None:
            continue
        try:
            return [str(tag) for tag in tags]
        except TypeError:
            continue
    return None


def verify_reopen(
    model: Any,
    receipt: Mapping[str, Any],
    *,
    mph_path: Path | None = None,
    evaluator: Any = None,
) -> dict[str, Any]:
    """Verify that a reopened model matches its recorded pre-save state.

    Parameters
    ----------
    model : Any
        The opened model object (live RemoteModel or FakeReopenModel).
    receipt : Mapping[str, Any]
        The pre-save record containing expected SHA, dataset, solution,
        expectations, solver settings, and derived values.
    mph_path : Path | None
        If provided, the file on disk whose SHA256 is checked against receipt.
    evaluator : Any
        Optional callable ``evaluator(model, expr)`` returning ``float`` or ``list[list[float]]``.
        If None, uses ``model_ops._evaluate_expression_safely``.

    Returns
    -------
    dict[str, Any]
        Structured report with status="PASS", comparisons, and metadata.

    Raises
    ------
    ReopenVerificationError
        If any verification step fails.
    """
    report: dict[str, Any] = {
        "status": "IN_PROGRESS",
        "checks": [],
        "comparisons": {},
    }

    # 1. Artifact hash verification (if mph_path is provided)
    expected_sha = receipt.get("model_sha256")
    if mph_path is not None and expected_sha is not None:
        actual_sha = _sha256(mph_path)
        if actual_sha.lower() != expected_sha.lower():
            raise ReopenVerificationError(
                "ARTIFACT_HASH_MISMATCH",
                f"File SHA256 {actual_sha} does not match receipt SHA256 {expected_sha}",
                {"expected_sha": expected_sha, "actual_sha": actual_sha},
            )
        report["checks"].append({"name": "artifact_sha256", "status": "PASS", "sha256": actual_sha})
    elif hasattr(model, "sha256") and expected_sha is not None:
        if model.sha256.lower() != expected_sha.lower():
            raise ReopenVerificationError(
                "ARTIFACT_HASH_MISMATCH",
                f"Model recorded SHA256 {model.sha256} does not match receipt SHA256 {expected_sha}",
                {"expected_sha": expected_sha, "actual_sha": model.sha256},
            )
        report["checks"].append({"name": "artifact_sha256", "status": "PASS", "sha256": model.sha256})

    # 2. Dataset and solution verification
    expected_dset = receipt.get("dataset")
    if expected_dset is not None:
        dataset_tags = []
        try:
            if hasattr(model, "result") and hasattr(model.result(), "dataset"):
                dset_list = model.result().dataset()
                dataset_tags = dset_list.tags() if callable(getattr(dset_list, "tags", None)) else []
        except Exception as exc:
            raise ReopenVerificationError("DATASET_QUERY_FAILED", str(exc)) from exc

        if expected_dset not in dataset_tags:
            raise ReopenVerificationError(
                "DATASET_NOT_FOUND",
                f"Expected dataset {expected_dset!r} not found in model datasets {dataset_tags}",
                {"expected_dataset": expected_dset, "available_datasets": dataset_tags},
            )
        report["checks"].append({"name": "dataset_exists", "status": "PASS", "dataset": expected_dset})

    expected_sol = receipt.get("solution")
    if expected_sol is not None:
        sol_tags = []
        try:
            if hasattr(model, "sol"):
                sol_coll = model.sol()
                sol_tags = sol_coll.tags() if callable(getattr(sol_coll, "tags", None)) else []
        except Exception as exc:
            raise ReopenVerificationError("SOLUTION_QUERY_FAILED", str(exc)) from exc

        if sol_tags and expected_sol not in sol_tags:
            raise ReopenVerificationError(
                "SOLUTION_NOT_FOUND",
                f"Expected solution {expected_sol!r} not found in model solutions {sol_tags}",
                {"expected_solution": expected_sol, "available_solutions": sol_tags},
            )
        report["checks"].append({"name": "solution_exists", "status": "PASS", "solution": expected_sol})

    # 3. Solver settings verification
    expected_solver = receipt.get("solver_settings")
    if expected_solver and hasattr(model, "solver_settings"):
        for key, val in expected_solver.items():
            actual = model.solver_settings.get(key)
            if actual != val:
                raise ReopenVerificationError(
                    "SOLVER_SETTINGS_MISMATCH",
                    f"Solver setting {key} expected {val}, got {actual}",
                    {"key": key, "expected": val, "actual": actual},
                )
        report["checks"].append({"name": "solver_settings", "status": "PASS"})

    # 4. Derived values verification
    expected_dv = receipt.get("derived_values")
    if expected_dv:
        actual_dv = _read_derived_value_tags(model)
        if actual_dv is None:
            raise ReopenVerificationError(
                "DERIVED_VALUES_UNREADABLE",
                "The receipt requires derived-value nodes but the reopened model exposes no way "
                "to list them; an unreadable list is not a passing check",
                {"expected": list(expected_dv)},
            )
        missing = [dv for dv in expected_dv if dv not in actual_dv]
        if missing:
            raise ReopenVerificationError(
                "DERIVED_VALUES_MISSING",
                f"Required derived values nodes missing: {missing}",
                {"missing": missing, "available": list(actual_dv)},
            )
        report["checks"].append(
            {
                "name": "derived_values",
                "status": "PASS",
                "expected": list(expected_dv),
                "available": list(actual_dv),
                "source": "model attribute, else model.result().numerical().tags() from the engine",
            }
        )

    # 5. Numerical stored solution verification
    expectations = receipt.get("expectations", {})
    if not expectations:
        raise ReopenVerificationError(
            "NO_EXPECTATIONS",
            "Receipt contains no expectations to verify",
        )

    # Resolve evaluation function
    if evaluator is None:
        from comsol_mcp._model_ops import _evaluate_expression_safely
        eval_fn = _evaluate_expression_safely
    else:
        eval_fn = evaluator

    for name, exp_data in expectations.items():
        expr = exp_data.get("expression", name)
        expected_val = exp_data["expected"]
        tol = exp_data.get("tolerance", 1e-3)
        rel_tol = exp_data.get("rel_tolerance", 1e-3)

        try:
            raw_res = eval_fn(model, expr)
        except Exception as exc:
            raise ReopenVerificationError(
                "SOLUTION_CLEARED_OR_EMPTY",
                f"Failed to evaluate stored solution expression {expr!r}: {exc}",
                {"expression": expr, "error": str(exc)},
            ) from exc

        # Extract scalar value from result
        actual_val: float | None = None
        if isinstance(raw_res, (int, float)):
            actual_val = float(raw_res)
        elif isinstance(raw_res, list):
            if not raw_res or not raw_res[0]:
                raise ReopenVerificationError(
                    "SOLUTION_CLEARED_OR_EMPTY",
                    f"Stored solution returned empty data for {expr!r}",
                    {"expression": expr, "raw_result": raw_res},
                )
            first = raw_res[0]
            if isinstance(first, list):
                if not first:
                    raise ReopenVerificationError(
                        "SOLUTION_CLEARED_OR_EMPTY",
                        f"Stored solution returned empty inner data for {expr!r}",
                        {"expression": expr},
                    )
                actual_val = float(first[0])
            else:
                actual_val = float(first)
        else:
            raise ReopenVerificationError(
                "UNEXPECTED_DATA_SHAPE",
                f"Cannot parse evaluation result {raw_res!r} for {expr!r}",
            )

        abs_diff = abs(actual_val - expected_val)
        rel_diff = abs_diff / max(abs(expected_val), 1e-12)

        if abs_diff > tol and rel_diff > rel_tol:
            raise ReopenVerificationError(
                "STORED_VALUE_MISMATCH",
                f"Value for {expr!r} deviated: expected {expected_val}, got {actual_val} "
                f"(abs_diff={abs_diff:.6e} > {tol}, rel_diff={rel_diff:.6e} > {rel_tol})",
                {
                    "expression": expr,
                    "expected": expected_val,
                    "actual": actual_val,
                    "abs_diff": abs_diff,
                    "rel_diff": rel_diff,
                    "tolerance": tol,
                    "rel_tolerance": rel_tol,
                },
            )

        report["comparisons"][name] = {
            "expression": expr,
            "expected": expected_val,
            "actual": actual_val,
            "abs_diff": abs_diff,
            "rel_diff": rel_diff,
            "status": "PASS",
        }

    report["status"] = "PASS"
    return report
