# Managed integration contract

## Exact software call

The pure numerical entry point is:

```python
from comsol_mcp._mode_overlap import compute_mode_overlap

result: dict[str, object] = compute_mode_overlap(hydrated_kernel_definition)
```

`hydrated_kernel_definition` must conform to `definition.schema.json` version `1.1.0`. The returned mapping conforms to `result.schema.json` version `1.2.0` and carries `evidence_status.scope = SOFTWARE_ONLY`, `native_status = NOT_RUN`, and `input_boundary = CALLER_SUPPLIED_ARRAYS_NOT_NATIVE_EVIDENCE`. The result also echoes the accepted phasor convention and the exact planarity-bound policy ID, fixed safety factor, and maximum per-sample ULP bound used.

The frozen MCP wrapper remains `result.mode_overlap` with top-level `project_id`, `session_id`, `model_ref`, `idempotency_key`, optional `request_id`, and a `definition` object. This task adds no route or public array-ingestion API. A future public `definition` should select registered model fields, solutions, plane, mode, and incident reference by identity. It must not let an MCP caller submit `E`, `H`, arrays, `model_revision`, or other claimed provenance and have those values treated as engine evidence.

## Required adapter work before any native claim

The managed backend must bind the request to its authenticated project/session/ModelRef and existing G5 permission/revision/effect checks, then obtain observations through the versioned COMSOL Worker path. It must read and retain the actual model revision, dataset, solution/inner/outer selection, frequency, field expressions, mode ID/eigenvalue, registered plane, coordinate frame, quadrature method/weights, orientation, units, and incident-reference power. E and H must be exported as complete complex components at identical points. The adapter must reconstruct physical phase when the COMSOL data are beam envelopes, populate the explicit supported time-dependence sign, and mark the arrays as full physical complex phasors. Signal, reference mode, incident reference, and optional capture reference must use exactly matching conventions; this version has no conversion path. An envelope-only value without its physical phase reconstruction is insufficient.

The adapter should select each result from the existing `FieldArray` axes (`expression, outer, inner, point` in `comsol_mcp/_solution_binding.py`) and explicitly select/record the intended indices before flattening point data into the kernel's `N×3` component arrays. The current result path publishes spatial coordinates through its `FieldArray` coordinate metadata; the adapter must verify that the returned coordinates correspond to the same registered plane and quadrature order for both fields. The kernel computes signed distance using `(r_i-r_0)·n` and checks it against a fixed 64-ULP bound derived from both absolute coordinate ULPs, local-difference ULPs, and dot-product rounding terms. It rejects nonfinite differences/bounds and does not project a warped or curved dataset. Because the bound grows when absolute coordinates have coarser representable precision, the native adapter must separately validate the observed coordinate precision and physical geometry at the model scale; passing the kernel check is not a physical planarity acceptance. It must also reject stale revision, missing imaginary components, incomplete selector mapping, mismatched point arrays, unverified units, unknown or inconsistent phasor declarations, and an incident reference not bound to an explicit input plane.

Only backend observations may populate the hydrated definition's source identity. The kernel echoes those fields and performs structural cross-checks; it is not an identity, permission, revision, path, or provenance verifier. Keep raw field arrays, quadrature weights, and normalization metadata available for independent recomputation. The selected `reciprocal_lossless_forward_nondegenerate_planar_v1` profile is an applicability assumption, not a user-verifiable assertion: before any native claim, the adapter must retain backend observations that demonstrate reciprocal, lossless, forward-propagating, nondegenerate mode applicability for the selected model/solution/mode, or route the request to a separately specified profile. Do not publish the kernel result as `IMPLEMENTED`/native merely because the Python call succeeds.

For `eta_mode`, compute or read back a separately bound incident reference. Never self-normalize it with outgoing `P_signal`. For `eta_capture`, bind a separate aperture region and denominator and report each explicitly. Preserve the raw efficiency if a preregistered tolerance is exceeded; record the diagnostic and let the owning acceptance gate decide.

## Later native acceptance, not performed here

Before W23 can close, the owning executor still needs same-version API evidence for native complex field extraction and result-to-plane/mode binding; a real identical-mode case; a nonzero offset case; independent recomputation of one normal and one offset case; mesh/quadrature sensitivity and power-balance checks; a saved same-version model reopened in a fresh Worker; and an independent Reviewer. W23 plans remain `PROPOSED`/`PLANNED`; this software package provides none of that evidence and changes no T014/T046 threshold.
