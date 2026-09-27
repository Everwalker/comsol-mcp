# W24 static glue shape: offline implementation draft

Status: `DRAFT_NOT_FROZEN`; no native model build, phase initialization, or study solve has been run for this shape route. The staged-cure coupon is separate and does not cover it. `PhaseInitialization` is a real solver step and must be charged to a separately approved native science budget when executed.

## Fixture currently implemented

`tools/java/W24StaticShapeFixture.java` builds one `flat` or `step` 2-D axisymmetric model. It creates a glue polygon and a non-overlapping gas polygon, preserves their internal boundary, and checks that the final geometry contains exactly two distinct domains. The step case uses one connected glue region that covers the raised mesa top, the mesa side, and the lower substrate. There is no artificial wall across the liquid. Wetting selections cover the full substrate exposed to the fluid, out to the outer box radius; each physical surface may map to multiple COMSOL boundary IDs. The builder requires each expected segment to be found by interior probes, rejects overlapping IDs between distinct surfaces, and excludes the symmetry axis from wetted boundaries.

Both cases use the same synthetic two-fluid material, contact angle, outer box, phase-field interface width, mesh settings, and study schedule. The current baseline is:

| Quantity | Value |
| --- | ---: |
| Drop radius `R` | 500 µm |
| Flat glue height `h` | 100 µm |
| Mesa radius `Rs` | 300 µm |
| Mesa height `Hs` | 40 µm |
| Step-case top height `zTop = h + (Rs/R)^2 Hs` | 114.4 µm |
| Outer axisymmetric box | 1.25 mm × 0.75 mm |
| Glue / gas density | 1200 / 1.2 kg m⁻³ |
| Glue / gas dynamic viscosity | 1 / 0.018 Pa s |
| Surface tension | 0.03 N m⁻¹ |
| Solid-surface contact angle | π/3 rad, direct input |
| Phase-field interface width `ε` / `χ` | 8 µm / 50 |
| Baseline mesh `hmax` / `hmin` | 4 µm / 2 µm |
| Capillary time `μGlue R / σ` | 1/60 s |
| Requested time list | 0 to 20 capillary times at 0.5 capillary-time spacing, 41 requested values |

The axisymmetric liquid volumes are

`Vflat = π R² h`

and

`Vstep = π (R² zTop − Rs² Hs) = Vflat`.

For these dimensions both are approximately `7.853981633974e-11 m³`. This is an analytic geometry check, not a COMSOL numerical result.

## Model mapping and readback gates

The builder creates incompressible `LaminarFlow (spf)`, `PhaseFieldInFluids (pf)`, and `TwoPhaseFlowPhaseField (tpf1)`. Since the fluid box is closed and the exterior walls do not prescribe a pressure level, it adds an explicit `PressurePointConstraint` with `p0=0[Pa]` at the outer top corner. It links phase 1 to the glue material and phase 2 to the gas material, sets a user-defined surface tension, disables gravity, and assigns Phase Field initial values `Fluid1phipf` to the glue domain and `Fluid2phipf` to the gas domain. Flat and step wetting nodes use the same direct contact-angle expression; the lower base remains wetted beyond the initial drop footprint.

The builder adds phase-volume, glue-mass, bulk Phase Field energy, kinetic-energy, and maximum-speed expressions through an axisymmetric integration coupling. The bulk energy omits a wall surface-energy contribution; it is a diagnostic only and is not used as a total-energy or stationarity gate. It creates a Phase Initialization step followed by a transient step, requests the fixed time list with strict BDF output times, and builds the mesh and solver sequence. The generated solver tree must contain one attached Time feature; the fixture configures constant BDF maximum step `0.10 Tc` (`1.6666667 ms`) and reads back the step constraint, strict output mode, and all-step storage policy. The material readback includes evaluated SI value and unit for both densities, both viscosities, and surface tension, alongside the two multiphase material links and the explicit Phase Field initial-value domains. It never calls `Study.run`; its returned status is `BUILT_NOT_SOLVED` and its native acceptance state remains `NOT_RUN`.

Same-version evidence used to choose the fixture route:

- COMSOL 6.4 *Capillary Filling — Phase Field Method*, documentation `doc_id 14902`, chunk `50758`, SHA-256 `ad6d03b7f34e38323d022d802a029c564d4dd4de28b12fa77f0872eca441a45e`. It documents 2-D axisymmetry, Phase Initialization followed by transient calculation, Phase Field in Fluids with Laminar Flow, and contact-angle refinement behavior.
- The same model's modeling instructions, `doc_id 14905`, pages 14–17, SHA-256 `b865fc0764281575b6f52cbbab845d2890badd8df443718c503acb11d78d67cb`, document the native `PhaseInitialization`/`Transient` steps, Fluid 1/Fluid 2 initial-value nodes, linked phase materials, `WettedWall`, and contact angle in radians.
- COMSOL 6.4 *Wetted Wall*, documentation `doc_id 3033`, chunk `7975`, SHA-256 `4db1f4b0ab9f95169ca60e39c6f51db6a2b655f7140e676b887b743dfa1809f5`, documents direct contact-angle input separately from Young-equation inputs using phase-1/phase-2 solid surface-energy densities. Because the fixture uses direct angle and does not configure or read back those surface energies, its bulk-energy expression is not treated as total energy.
- COMSOL 6.4 *Free Convection in a Water Glass*, documentation `doc_id 16801`, chunk `64154`, SHA-256 `72d3bc2643c3213a0a544870a95d3776771d82a22fecab6ed11946b88a115068`, sets Laminar Flow to incompressible, calls out pressure locking in a closed cavity, and adds a Pressure Point Constraint. COMSOL 6.4 *Pressure Point Constraint*, documentation `doc_id 5436`, chunk `18638`, SHA-256 `1992d3295c380ea376566db23da8691c74c923d2a89f8e1425703a4914b11e62`, identifies the pressure input as `p0`. The installed `cold_water_glass.mph` index independently lists feature tag `prpc` and type `pressurepointconstraint`; the exact setter/readback still requires the authorized native setup check.
- COMSOL 6.4 *Two-Phase Flow, Phase Field Coupling Feature*, `doc_id 2498`, chunk `7345`, SHA-256 `0d59e2ae92cee89641f49e0a84e5abe359a2e4047e6ebc3f00668abace78854c`, documents a user-defined surface-tension coefficient with SI unit N/m.
- COMSOL 6.4 Polygon API, `doc_id 4265`, chunk `17000`, SHA-256 `1cc215c39d3399229183fbc4a4a7d27d09e614d8c52a93f7972d6c4b6713ce49`, documents 2-D solid polygons from x/y coordinate arrays.
- The installed `capillary_filling_pf.mph` is a COMSOL-authored 6.4.0.257 example. Its metadata confirms the exact `TwoPhaseFlowPhaseField` property token `userdef` and phase-material link property `link`. It was inspected as local API precedent only; it is not redistributed, and its presence does not establish a licensed module or validated behavior in 6.4.0.293.

## Proposed native stationarity and sensitivity criteria

These criteria are preregistered proposals and remain unfrozen until the 6.4 setup/readback path verifies the stored times, field extraction, contact-line mapping, and solver maximum-step setting.

- Retain every native solution time and the raw `pf.phipf` field. The 41 requested output times do not assert that the solver accepted only 41 time steps.
- On all stored times, require glue-volume drift from the post-initialization value to remain at or below `1e-3`; also compare the post-initialization volume with the analytic geometry volume at `1e-3` relative tolerance.
- Use the last four capillary times as the stable window. Require each adjacent upper `φ=0` interface profile displacement to remain below `0.25 ε`, substrate contact-line travel below `0.25 ε`, volume drift below `1e-3`, and maximum speed below `1e-3 σ/μGlue`. Bulk free energy is retained as a diagnostic only; do not use it to assert total-energy decrease or stationarity while the wall-energy contribution is absent.
- Extract the upper `φ=0` branch at fixed radial coordinates for `0 ≤ r < R`; use the zero crossing on the lower substrate boundary for the contact line. Ambiguous or missing crossings fail the gate. Do not substitute a spherical-cap estimate for the native interface.
- Compare the same capillary-time plateau for both shapes and report the flat-versus-step profile, contact-line, volume, and bulk-energy diagnostic without tuning parameters after seeing the result. Bulk-energy differences are descriptive, not an energy-minimization acceptance gate.
- Test one factor at a time for each shape with seven distinct configurations including baseline: mesh `hmax/ε = 1/2` (baseline), `1/3`, and `1`; interface width `ε = 6, 8 µm` (baseline), and `10 µm`; and solver maximum step `Δtmax/Tc = 0.05, 0.10` (baseline), and `0.20`. For mesh variants, hold `ε=8 µm`; for interface-width variants, hold `hmax/ε=1/2`; for time-step variants, hold the baseline mesh and width. Each sensitivity result must first meet the stable-window criteria.
- Compare converged profiles between sensitivity variants on the same fixed radial coordinates. Proposed acceptance is maximum height difference at most `0.02 R`, contact-line difference at most `0.5 ε`, and relative final glue-volume difference at most `1e-3`. Record bulk-energy differences normalized by `σ π R²` only as diagnostics.

The current Java builder configures the baseline mesh, requested times, and baseline maximum solver step. It does not execute either shape, capture the native field/time series, or evaluate any stationarity/sensitivity criterion. Those are explicit remaining implementation steps before this route can be frozen or run.

## Review corrections in this offline checkpoint

The first geometry draft wetted only the base through the initial drop radius and assumed one boundary per physical surface. That would leave the substrate dry after contact-line advance and could omit a base segment split at the glue/gas junction. The corrected selection reaches the outer box radius, allows multiple boundary IDs per named surface, checks disjoint IDs, and probes nine interior locations in each analytic segment. Compile-only validation does not prove the COMSOL box selections return those IDs; the native setup check must read them back before acceptance.

The earlier bulk-only Phase Field energy metric was described too broadly. It remains named `phasefield_bulk_free_energy` and is not part of the stationarity or sensitivity gates. A total-energy calculation would require an explicitly supported wall-energy term with the correct phase convention and axisymmetric surface measure; that formulation remains open.

The model is a closed incompressible cavity. The fixture now explicitly selects incompressible flow and adds a zero-gauge pressure point constraint at the outer box corner, based on the cited COMSOL 6.4 example and reference page. Java compilation succeeds, but feature creation and its value/selection readback have not run natively.

## Validation boundary

Offline validation currently performed: Corretto 11 compiled `W24StaticShapeFixture.java` against the installed COMSOL 6.4 API jars after the full-substrate wetting, energy-scope, incompressible-pressure-reference, and max-step changes. This checks Java API signatures only. It does not validate geometry construction, property setters/readbacks, mesh quality, licensing, phase initialization, convergence, contact angle, or physical shape. No COMSOL server or Worker was started and no solver step was run.
