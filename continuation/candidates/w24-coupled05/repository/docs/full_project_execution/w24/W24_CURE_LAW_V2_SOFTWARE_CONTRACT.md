# W24 cure-law v2 software contract

This additive contract introduces spatial relative UV exposure, gel activation, and full Generalized Maxwell material inputs while preserving the historical v1 `build`/`readback` path and its evidence. The new Java actions are build/readback only. This file does not authorize or report a COMSOL solve.

## Spatial relative exposure

The adhesive occupies `0 <= r <= 100 µm` and `510 µm <= z <= 550 µm`. The v2 readback requires the actual adhesive domain bounding box top and `zUVSurface` to both equal `550 µm`. The estimated, dimensionless relative intensity is defined only on the adhesive:

```text
S(t)         = 1 for 0 <= t < 120 s, else 0
mu           = mu_scale / (40 µm), mu_scale in {0.5, 1, 2}
Irel(z,t)    = S(t) * exp(-mu * (550 µm - z))
Duv_rel(z,t)= integral_0^t Irel(z,s) ds
             = min(t,120 s) * exp(-mu * (550 µm - z))
```

`Duv_rel` has units of seconds; its Domain ODE dependent-variable quantity is `time` and its source is dimensionless. `Irel` is a relative estimate, not an absolute irradiance measurement; the fixture explicitly reports `absolute_irradiance=false`. The cure rate becomes `(kUV*Irel+kT)*(1-alpha)`. The existing uniform `alpha_iso` reference remains unchanged and independent. At the top surface, the v2 isothermal analytic conversion reduces to the existing uniform-UV `alpha_iso` equation.

The three attenuation values are build-time variants. The fixture accepts only `mu_scale` equal to `0.5`, `1.0`, or `2.0`, reads back the actual scale and effective `muUV`, and retains the same mesh, geometry, and exposure window for all three. This unit does not execute those variants.

## Gel and viscoelastic material

The adhesive-only Activation expression is `alpha>=alpha_gel || solid.wasactive`, with `alpha_gel=0.5`. The fixture does not set `actfac`; it reads back the model default and requires `1e-5`. Activation locking and its stress-free reference semantics remain unverified until native evidence is collected.

The adhesive has long-term `E∞=0.5 GPa`, one branch `E1=1.5 GPa`, `ν=0.35`, and `τ1=300 s`. For each modulus, `K=E/[3(1-2ν)]` and `G=E/[2(1+ν)]`; therefore `K∞=555.5555556 MPa`, `G∞=185.1851852 MPa`, `K1=1666.6666667 MPa`, and `G1=555.5555556 MPa`. `Kvm_v`, `Gvm`, and `tauvm` each contain one element at index 0, and readback verifies the ordered binding. The base adhesive material supplies the long-term modulus; its full Generalized Maxwell child contains the one volumetric/deviatoric branch.

The `build_maxwell_ramp_hold` control builds a fresh 100 µm cube with no heat-transfer or chemical physics, no activation gate, no eigenstrain, zero initial displacement and velocity, and prescribed boundary displacement `u=epsFinal*min(t/Tramp,1)*x`, `v=w=0`. `Tramp=1 s` and `epsFinal=1e-3`. The model is active from the initial study time because this independent constitutive control has no Activation feature. A fresh unsolved model is required; zero internal branch memory and the Maxwell reference state still require native verification.

For the ramp and hold, the analytical normal-stress coefficients are:

```text
Cxx = K + 4G/3
Cyy = K - 2G/3
sigma_xx(t) = Cxx_inf * epsilon(t) + Cxx_1 * epsilon_final * tau/Tramp
               * (1-exp(-t/tau))                       for 0 <= t <= Tramp
sigma_xx(t) = Cxx_inf * epsilon_final + Cxx_1 * epsilon_final * tau/Tramp
               * (1-exp(-Tramp/tau))*exp(-(t-Tramp)/tau) for t > Tramp
```

`sigma_yy` uses the corresponding `Cyy` coefficients. The validator checks absolute native `sigma_xx` and `sigma_yy` values in Pa at `1, 301, 601, 901 s`, including the long-term baseline, branch amplitude, and 300 s decay. It does not fit an exponential to a measured initial stress. Numerical error tolerances must be supplied explicitly by the later frozen native candidate; this software unit does not invent a pass threshold.

The separate `build_gel_stress_free` control ramps a 0.1% affine deformation while activation is false, holds it until `tGel=2 s`, and uses `t>=tGel || solid.wasactive`. It adds no thermal/chemical strain, eigenstrain, or Maxwell branch. Its validator independently requires the complete exact six-component set `solid.sx`, `solid.sy`, `solid.sz`, `solid.sxy`, `solid.sxz`, and `solid.syz` in Pa at exactly `2 s` and `3 s`, native `solid.wasactive=1` at `2.5 s` and `3 s`, and all post-gel samples below an explicitly supplied maximum-stress threshold. It does not use the Maxwell decay fit. Activation and reference-state semantics are still unverified.

## History continuity and stop gates

`compare_v2_history_handoff` consumes full XmeshInfoDofs frames and requires identical complete DOF keys, exact dose/conversion/post-gel fields, an observed Maxwell-branch DOF name supplied by native evidence, and native `solid.wasactive` values at identical coordinates. A post-gel handoff source must include at least one active DOF. A reset of Duv, alpha, qpost, any other full DOF, branch history, or `wasactive` fails. The helper does not guess internal Maxwell state variable names from metadata and always reports native semantics as unverified.

These validators consume caller-supplied receipts. A `native_metrics_readback=true` input field is only a caller assertion: the Python parser does not authenticate the model, request, solution, units, producer, or source hash. Its comparison result is explicitly marked `*_SOURCE_AUTH_REQUIRED` with `source_identity_authenticated=false`. The later production native runner must authenticate the exact public route, producer, model/request/solution identity, units, and source hashes before it exports data to these validators. A synthetic fixture passing these tests is software evidence only.

## Local COMSOL 6.4 API evidence

Read-only evidence on this machine:

| Evidence | Path and SHA-256 | Relevant observation |
|---|---|---|
| Completion metadata | `/Applications/COMSOL64/Multiphysics/data/completion/physics.xml` — `efb2d0e44a5ea4d2f72aa3b22ba15026f7eaf8a4e39b267c6e036c1701d47d28` | Solid Displacement metadata declares `Direction` as a string array with `free/prescribed/limited`, and `U0` as a string array. Solid Viscoelasticity metadata declares `MaterialModel=GeneralizedMaxwell`, allows `deformationModel=full`, and types `Kvm_v`, `Gvm`, and `tauvm` as string arrays. Domain ODE unit metadata includes `time` and `dimensionless`. |
| Generalized-Maxwell example | `/Applications/COMSOL64/Multiphysics/applications/Structural_Mechanics_Module/Material_Models/viscoelastic_tube.mph` — `d6452b711c2e536433080b71c58ebf04d87823d6ad48cbc304db29cd77f1bce0` | Its local `dmodel.xml` contains a Solid Mechanics `Viscoelasticity` child, `MaterialModel=GeneralizedMaxwell`, `Kvm_v`, `Gvm`, and `tauvm` arrays. Its model uses the deviatoric option, so it does not establish the full-model native branch/reference semantics used here. |
| Activation default example | `/Applications/COMSOL64/Multiphysics/applications/Structural_Mechanics_Module/Thermal-Structure_Interaction/layered_plate.mph` — `241297256a3d96547e2471a445966353336696718a9d04e38ce9c7fc28f9b313` | Its local `dmodel.xml` records Activation `actfac=1e-5`. The v2 fixture leaves this property unset and requires readback. |
| ODE unit evidence | COMSOL 6.4 Reference Manual, “The ODE and DAE Interfaces,” p.1487, source SHA-256 `3dacc33243911a98cbcac19c6b83c9b662dd0b7c56b880b3a9e6a2ab602e7106`; and “Aquifer Characterization,” p.26, source SHA-256 `1bc80b7be988ab7c2706b750f64198205e47e531829c6ceee7e59592e9d46455` | The manual defines source-term quantity as the units of the equation right-hand side and says its default dimensionless unit is `1`; the Domain ODE example sets a custom source unit `1`. The local completion metadata exposes Domain ODE source/dependent quantity properties as strings. The exact live acceptance and stored-field semantics of this fixture remain unverified until a native build/readback. |

Only local paths, hashes, and relevant property observations are recorded here. Commercial model/JAR bytes are not copied into the repository. Installed completion metadata and example models are API-configuration evidence, not proof of module licensing or native Activation, branch-history, or handoff semantics.

## Software verification boundary

The Python unit tests exercise the equations, fixed checkpoints, units/identity gates, and history-reset negative controls. Java compilation against the installed COMSOL 6.4 API checks source references and signatures. Neither operation starts COMSOL or runs a study. Native execution, license availability, the live dose-unit behavior, gel event timing, full-Maxwell reference/branch semantics, persisted/reopened history, and all physical/scientific gates remain `NOT_RUN` or `UNVERIFIED` until a separately frozen native campaign.
