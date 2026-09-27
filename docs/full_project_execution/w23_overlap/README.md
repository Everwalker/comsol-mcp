# W23 mode-overlap software core

Status is `SOFTWARE_ONLY`; native COMSOL execution, T014/T046 acceptance, and physical calibration are `NOT_RUN`. The module computes from internal complex arrays and does not extract or authenticate fields from a model.

The versioned kernel contract is `definition.schema.json` version `1.1.0` plus `result.schema.json` version `1.2.0`. The implementation is [`comsol_mcp/_mode_overlap.py`](../../../comsol_mcp/_mode_overlap.py), with focused tests in [`tests/test_mode_overlap.py`](../../../tests/test_mode_overlap.py). The public MCP action remains the frozen `result.mode_overlap(definition: object)` contract; this kernel's array-bearing definition is an internal hydrated payload and must not be accepted from an MCP caller as proof of a COMSOL result.

The only implemented applicability profile is `reciprocal_lossless_forward_nondegenerate_planar_v1`: reciprocal, lossless, forward-propagating, nondegenerate modes on one registered planar transverse sampling surface. Lossy, leaky, evanescent, degenerate-subspace, curved-surface, and unmatched-plane cases need separate theory and explicit versions.

The core preserves full complex E and H under an explicit phasor convention, rejects sample coordinates outside an input-scale ULP rounding bound, reports that policy and the maximum bound used, and keeps three quantities distinct: `normalized_overlap`, `eta_mode`, and optional `eta_capture`. This bound is not a physical flatness tolerance. See [`calculation_and_applicability.md`](calculation_and_applicability.md) for equations and units, and [`integration_contract.md`](integration_contract.md) for the managed backend boundary. The regression fixture for the formerly accepted 0.1 m warp is [`evidence/main_overlap_planarity_reproducer.json`](evidence/main_overlap_planarity_reproducer.json).

`software_test_evidence.md` and `software_test_junit.xml` record only the Python software tests. They are not COMSOL, GUI, port-power, convergence, or scientific acceptance evidence.
