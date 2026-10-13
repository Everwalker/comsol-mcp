# W23 main-agent model/acceptance proposal

Status: PROPOSED_NOT_FROZEN. No solve, license checkout, numerical result or acceptance is asserted. Executor must confirm same-version API mappings, finalize the fixture and preregister numerical tolerances/resources before dispatch. This proposal supplements, not replaces, original architecture section 7.2 and T014/T046.

## Small real-port fixture

A deliberately reduced 2-D, fixed-polarization dielectric-waveguide coupling fixture is an appropriate initial native optics/port numerical test, provided it is labelled as a planar surrogate (power per unit out-of-plane length, W/m). It is not a physical validation of a circular 3-D fiber or complete lens assembly. Full original fiber/lens/PML/polarization and x/y/z/angle/thermal-structural mapping capabilities still need explicit managed routes and applicability limits in ACTION_COVERAGE.

Use a uniform lossless reciprocal guide with actual Numeric input and output ports and Boundary Mode Analysis followed by Frequency Domain. First verify the identical aligned guide; then use an actual shifted receiver-core geometry or explicitly registered coordinate correspondence for transverse offsets. Keep the receiving reference mode and capture aperture distinct. A receiver on an interior slit port backed by a homogeneous PML region avoids reflecting all field content orthogonal to the target port mode. Merely placing a single-mode absorbing boundary at the exit is not an adequate radiation treatment for offset coupling.

An initial TE slab variant can use an independent dispersion relation as a mode-index reference and avoids ambiguous mixing of degenerate vector polarizations. If a circular-fiber vector fixture is used instead, preserve the selected polarization/basis and mode identity; do not mistake two arbitrary members of a degenerate fundamental subspace for a wrong-mode negative control. API names and feature/property values must be obtained from installed same-version model/API inspection rather than invented from GUI labels.

## Physical/data contract

Store native complex E and H, coordinates and orientation, geometry dimension, units, physical frequency, selected mode/eigenvalue, reference plane, dataset/solution/inner/outer identity and source revision. Reconstruct actual fields when using beam envelopes; an envelope without its phase function is not the physical field.

Compute signed time-average Poynting flow in a declared forward normal. Normalize reference/input power consistently. Use the reciprocal-mode power overlap appropriate to the selected lossless propagating-mode fixture; a simple arbitrary scalar dot product is not the general vector formula. Restrict the formula's applicability explicitly for lossy, leaky, evanescent or degenerate modes. Reject non-positive/near-zero normalization power. In 2-D integrate along the port and report W/m; do not silently call that W.

Report eta_mode for the specified receiving mode separately from eta_capture for the declared geometrical receiving aperture/region, with an explicit denominator for each. Capture cannot be inferred from eta_mode or a field-amplitude norm. Export both unnormalized native fields and the normalization metadata so an independent reviewer can recompute them without the implementation's scalar summaries.

## Preregistered controls to finalize before solves

- Identical normalized-mode overlap near unity and independent native input/output port power/S-parameter readback; self-overlap alone is algebraic consistency, not a convergence study.
- Global phase rotation leaves power/coupling efficiency unchanged while complex amplitude changes appropriately.
- Wrong units, mismatched solution/reference plane/mode, missing imaginary part and unsupported polarization fail truthfully; negative controls must fail the actual data contract rather than merely a hand-written summary.
- Actual offset cases with nonzero offsets; choose a physically justified monotonic near-alignment region, freeze that range and tolerances before computing results.
- Mesh and quadrature sensitivity and power balance with the appropriate port/PML loss terms. Freeze numerical tolerances from the benchmark's accuracy target, not observed results.
- Native COMSOL image bound to the same dataset/solution, saved MPH, and fresh same-version Worker reopen with field/efficiency recomputation.
- One normal and one offset case independently recomputed by the final Reviewer.

Prefer reusing existing W17 complex-field, W18 native-image and W21 budget/cache identities. Every model change and export must go through the same managed backend and execution identity. Commercial example MPH/manual bytes remain external local reference material and must not enter the public distribution.

## Same-version official reference evidence consulted

1. COMSOL 6.4, *Single Mode Fiber-to-Fiber Coupling*, PDF pages 2-3 and 7, local KB doc_id 22480; `doc/help/wtpwebapps/ROOT/doc/com.comsol.help.models.woptics.single_mode_fiber_coupling/models.woptics.single_mode_fiber_coupling.pdf`; SHA256 `5ee494ec5a96e3528218baeae0e778bee4be022bf1e3565f8745ebf109ed6b72`. The tutorial uses beam envelopes, Numeric ports, Boundary Mode Analysis, Frequency Domain and a PML-backed interior receiver port. These are documented concepts, not current native evidence.
2. COMSOL 6.4, *Port*, chunk 112842, doc_id 26701; `doc/help/wtpwebapps/ROOT/doc/com.comsol.help.woptics/woptics_ug_optics.6.20.html`; SHA256 `2a4e234a561895fc1626543dee2843d4ce061906fc92bd491185ead96cfe4924`. Direction, outgoing-wave mode convention, homogeneous backing region, polarization and phase restrictions must be respected.
3. COMSOL 6.4, *S-Parameter Calculations*, chunk 112781, doc_id 26656; `doc/help/wtpwebapps/ROOT/doc/com.comsol.help.woptics/woptics_ug_modeling.5.27.html`; SHA256 `de441294154ec14acac2e3bab2406b640ff988bd0aaeb1810fff1505759f3bfb`. Supports equal-power normalization, reciprocal-mode applicability and the below-cutoff zero-power limitation. Exact formula images require an accessible render before taking their notation as implementation authority; this note does not claim that unreadable image text was verified.

Installed same-version examples exist at `Wave_Optics_Module/Couplers_Filters_and_Mirrors/single_mode_fiber_coupling.mph` and `Wave_Optics_Module/Verification_Examples/dielectric_slab_waveguide.mph` under the local COMSOL 6.4 applications tree. Existence does not establish module licensing. Inspect private copies only; publish newly authored replay recipes and owned outputs with appropriate rights.

## Required continuation beyond the first port benchmark

The planar benchmark is an implementation checkpoint, not the W23 deliverable boundary. Continue the same executor into these original section 7.2 requirements after the first real-port chain is working:

1. Provide a parameterized lens/core/cladding/air model recipe and managed geometry/physics routes, retaining the actual dimension, vector polarization and PML conventions. Transverse x/y offsets and angular misalignment that break rotational symmetry need an appropriate full spatial model; an axisymmetric or planar result alone cannot certify those cases. Any scale-separated or envelope route must export the phase reconstruction used to recover physical complex fields.
2. Bind x/y/z, angular and manufacturing-tolerance cases to the existing experiment/case identity and budget mechanisms. Freeze specific numerical cases and tolerances before dispatch; record which dimensions are implemented versus actually tested. Preserve the selected mode or degenerate-mode basis across geometry revisions.
3. Demonstrate a native thermal/structural deformation input and its explicit mapping into the optical model, including geometry frame, units, source solution/time and mapping error. Caller-supplied displacement arrays or an unexplained scalar offset cannot replace this requirement. Keep any limited rigid-body approximation and its residual criterion explicit.
4. Deliver replayable owned inputs, native images, complex E/H and quadrature/normalization metadata, saved/reopened models and all applicable acceptance evidence. Reuse approved infrastructure, but leave every missing original route visible in ACTION_COVERAGE until actually implemented and checked.

These are the original full-scope continuation obligations, not optional improvements after a planar numerical pass. Scientific fixture dimensions/materials may be declared estimated; physical calibration must remain separate.


## Isolated U1 full3D mesh software successor (2026-10-02)

U1 adds exactly one owned `w23tet` FreeTet generator at initial mesh creation; case application reuses that exact tag/type without repair. Actual selection geometry/dimensions/entities must equal fresh positive domain IDs read from native `getUpDown`; domain count only cross-checks cardinality. The initial `geom(geom3d,3).all()` action is recorded; no undocumented isAll getter is assumed. Mesh empty/count/completeness/problem getters and existing Size are validated before downstream dispatch. Isolated proxy and Python controls are software evidence only; native mesh and science remain UNVERIFIED/NOT_RUN. U2–U6, complete integration freeze and separately approved native campaign remain OPEN. Geometry, PML, physics, Size expressions/scales, thresholds and all original RUN/budget profiles are unchanged.
