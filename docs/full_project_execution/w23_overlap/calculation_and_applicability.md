# Calculation and applicability

## Supported data

The kernel consumes full complex electric and magnetic vectors at the same ordered quadrature coordinates. Vector components must be `x,y,z`; E and H units must be explicitly `V/m` and `A/m`; coordinates must be `m`. Both real and imaginary arrays are required. A genuinely real field is represented by an explicit all-zero imaginary array. The kernel never infers a missing imaginary part to be zero.

Schema version `1.1.0` requires a phasor convention at the definition, both field records, and every incident-reference power record. The accepted time factors are `exp(-i omega t)` and `exp(+i omega t)`. The complex fields must be declared as `full_physical_complex_phasor_including_reconstructed_envelope_phase`; an unreconstructed beam envelope is rejected. Signal, reference mode, and incident-reference conventions must match exactly. This version performs no automatic conversion between conventions. The declaration is a contract check, not proof that the arrays came from the claimed solver or model.

The plane carries a unit normal oriented in the declared forward direction and strictly positive quadrature weights. A 3-D geometry integrates area weights in `m^2` and returns power in `W`. A 2-D geometry integrates line weights in `m`, assumes invariant out-of-plane depth, and returns power in `W/m`. The module does not multiply by a guessed depth. It checks the declared `power_floor` before using signal, reference-mode, or incident-reference power for normalization.

Every coordinate is checked against the same plane perpendicular to the registered unit normal. For each sample, `d_j = r_ij - r_0j` and `p_j = d_j n_j`; the signed distance is `math.fsum(p_j)`. The per-sample floating-point bound is

```text
B_i = 64 × [ Σ_j |n_j| (ulp(r_ij) + ulp(r_0j) + ulp(d_j))
             + Σ_j ulp(p_j)
             + ulp(Σ_j |p_j|) ]
```

The sample is accepted only when `abs(math.fsum(p_j)) <= B_i`. This version fixes the safety factor at 64 and uses policy ID `coordinate_local_difference_dot_ulp_64_v1`; callers cannot widen the bound. The result records the policy ID and maximum per-sample bound. Distances use local coordinate differences, while ULPs of both absolute coordinates are included because their representation precision limits those differences. Any nonfinite difference, projection, or bound is rejected. A 0.5 nm warp on a micrometre-sized surface is far above this ULP bound and is rejected. The kernel does not fit a plane or project/resample coordinates.

This is only a floating-point consistency check, not a physical flatness tolerance. Translating a plane to coordinates with coarser representable precision necessarily increases the bound. The native adapter must separately assess coordinate precision and physical geometry at the model's actual scale; passing this kernel's ULP check does not certify a physically planar port or make the profile applicable.

Both fields must refer to the same model revision, frequency, registered plane identifier, coordinates, units, and component order. Their dataset and solution identities may differ and are reported separately. The incident reference is a separate positive power record with its own `reference_id`, `input_plane_id`, source identity, and compatible `W` or `W/m` unit.

## Equations

For either sampled field `i`, the signed forward time-average power is

```text
P_i = 0.5 Re Σ_k w_k [(E_i,k × conjugate(H_i,k)) · n]
```

where `w_k` has units `m^2` for 3-D area or `m` for 2-D line-per-unit-depth integration. The full reciprocal vector overlap numerator and complex amplitude are

```text
N = Σ_k w_k [
      (E_signal,k × conjugate(H_mode,k))
    + (conjugate(E_mode,k) × H_signal,k)
    ] · n
a = N / (4 sqrt(P_signal P_mode))
```

The result preserves `N` and `a` as `{real, imag, unit}`. `N` has `W` or `W/m`; `a` is dimensionless. The modal shape overlap and projected power are

```text
normalized_overlap = |a|^2
projected_mode_power = normalized_overlap × P_signal
eta_mode = projected_mode_power / P_incident_reference
```

`normalized_overlap` uses signal power for self-normalization. It is not automatically input-to-output coupling efficiency. For equal mode shapes with the signal field amplitude reduced by one half, `normalized_overlap = 1`, while `projected_mode_power` is one quarter and `eta_mode = 0.25` when the independently specified incident reference power remains fixed. The code never substitutes `P_signal` for that reference.

`eta_capture` is a different calculation. When an aperture is supplied, the kernel integrates signed signal Poynting flux over its explicit sample-index region and divides by that aperture record's independently declared incident reference power. The result contains the aperture ID, plane ID, numerator, and denominator. Capture is not inferred from modal overlap.

An optional `eta_mode_upper_tolerance` only produces a diagnostic against `1 + tolerance`. It is not an acceptance threshold, does not change the value, and never clamps it. Without the field, the range diagnostic is `NOT_ASSESSED`. Values beyond a declared bound are returned raw with `EXCEEDS_DECLARED_UPPER_BOUND`.

## Refusal boundaries

The core rejects non-unit normals instead of normalizing them, reversed normals that produce non-positive forward signal/mode power, non-planar coordinates beyond the ULP bound, nonfinite geometry differences/bounds, powers at or below the declared floor, mismatched planes/coordinates/units/shapes/conventions, malformed component order, missing imaginary arrays, nonfinite values, and unsupported schema/profile versions. Field-coordinate equality is exact; interpolation or resampling belongs upstream and must be bound to a registered common plane before invoking this core.

The module validates structure and arithmetic only. The profile's lossless, reciprocal, forward-propagating, and nondegenerate conditions remain assumptions; the caller cannot prove them by selecting a profile string. Before native acceptance, the managed adapter must provide backend-observed model/solution/mode/field evidence that justifies those assumptions and bind it to the actual selected COMSOL entities. Phasor declarations and caller-provided IDs likewise do not prove source authenticity. The kernel does not interpret S-parameters, perform mesh/quadrature convergence, choose physical acceptance thresholds, resolve arbitrary degenerate mode bases, or calibrate an optical model. Those remain separate native and independent-review gates.
