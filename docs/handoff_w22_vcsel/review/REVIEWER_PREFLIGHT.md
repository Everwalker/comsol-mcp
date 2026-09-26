# W22 independent Reviewer preflight

Status: PREFLIGHT_ONLY — no implementation, native result or candidate is approved.

Benchmark freeze confirmed before any native scientific execution: `benchmark_spec.json` SHA-256 `d94322296feff1cd7678e345bf798616a704c638c8c108d3c8f78f4128f4b1fe`. Main Agent and this independent Reviewer agreed the parameters, thresholds, nonzero heating constraint and finite budgets. The JSON is authoritative where this proposal uses tentative wording. Its historical fixture.workpiece_radius_m is not the execution geometry; geometry.shape=square_slab is authoritative. Independent reference calculations may now run. This freeze is not W22 approval.

Reviewer context: actual child agent `/root/reviewer`, created by the host for the W22 task. Scope is D1–D6 and directly affected regressions. W20/W21 scoped approval is inherited; W23 is excluded.

Read: AGENTS.md, NEXT_GOAL.md, TEAM_PROTOCOL.md, frozen/W22_CONTRACT.md, frozen/ORIGINAL_W22_REQUIREMENTS.md, W22_DESIGN.md, reference/fixture.json, reference/make_source.py and example_L20mm/reference_values.json. The example is analytical synthetic input, not COMSOL evidence.

## Proposed pre-run choices

Keep the supplied emitter construction: 19 nominal, 18 active; outer-ring index 0 disabled; base per-emitter powers 0.8/0.75/0.65 W, totaling 12.45 W. Keep alpha=0.6, sigma=0.002+0.05*L in metres, L=0.010/0.020/0.030 m, and the 101 by 101 source table on [-0.025,0.025] m. Input columns and interpolation units must explicitly distinguish incident from absorbed W/m²; apply alpha exactly once. Default extrapolation is zero, without clipping renormalization.

Prefer transient physics with stored final time 60 s, so existing W21 time-axis semantics remain truthful. Suggested outputs include 0, 1, 5, 20 and 60 s; these are not necessarily solver internal steps. Material constants follow the supplied proposal: k=20 W/(m K), rho=3000 kg/m³, Cp=700 J/(kg K), initial and ambient temperature 300 K, thickness 0.001 m, bottom h=500 W/(m² K), other faces adiabatic apart from top absorbed heat flux. Radiation disabled. Source is static in space and time.

Main/Reviewer selected a 40 by 40 mm rectangular plate with the same thickness and circular top ROI radius 15 mm, before native scientific execution. The rectangular plate allows an independent separable heat-equation reference (in-plane cosine modes with adiabatic sides, thickness Robin modes). This is a permitted synthetic benchmark adjustment, not a device claim. The independent review/spectral_reference.py implements the full thickness expansion; it does not use a lumped-temperature approximation. Its proposed convergence check compares 48/64 lateral modes and 64/96 thickness modes, with independently increased source and ROI quadrature, requiring <=0.2% change in mean, standard deviation and source integrals. The finite sampled reference min/max are labeled as samples, not exact extrema.

For limited power optimization, a compact reproducible set is p_center in {0.6,0.8,1.0} W and p_ring1 in {0.65,0.75,0.85} W, with p_ring2=(12.45-p_center-6*p_ring1)/11 W. Every active emitter remains between 0 and 1 W. Scan all three distances: 27 unique candidates, containing the three base cases. Set a separate explicit allowance for cache controls and fresh Reviewer solves; do not silently charge or conceal them as optimization cases. Compare the minimum area-weighted CV of DeltaT at 60 s among cases meeting the frozen ROI heating and power constraints. Suggested mean DeltaT lower bound is 3 K. Report best verified feasible or no feasible case found, never global optimality.

## Proposed acceptance tolerances

Freeze these in benchmark_spec.json before native scientific output is inspected. Values below are proposals until main/Reviewer agreement is recorded.

- Imported nonzero source samples at (+12,0), (-12,0), (0,+12), (0,-12) mm: abs error <= max(1 W/m², 2% of independent analytic value). At least +x/-x must retain their analytic angular distinction. Also sample explicitly beyond table support and require zero within 1 W/m². Verify m/mm representations at identical physical points.
- Native workpiece and ROI source integrals: relative error <=1% against an independently integrated source, with an explicit absolute floor 1e-6 W. Integrate the actual interpolated source as a separate reference to distinguish interpolation error from native integration error; retain analytic Gaussian integrals too.
- Thermal energy balance: abs(P_abs-P_bottom_out-dU/dt) <=2% of P_abs. All quantities come from actual native boundary/domain integrations; for finite-difference stored energy, use a sufficiently fine independent time interval and document it. Do not omit storage merely because 60 s is near equilibrium. Alternatively integrate energy over time with an independently checked quadrature bound.
- Independent temperature reference: area-mean DeltaT and area standard deviation error <=1% with absolute floors 0.05 K for mean and 0.02 K for standard deviation; maximum/minimum DeltaT error <=2% with 0.05 K floor. Freeze reference discretization convergence requirements before comparison. Native fresh best-candidate reproduction must agree within 1% or 0.02 K for scalar temperature metrics. Max/min must be actual selection extrema, not sparse-point extrema.
- CV uses std(DeltaT)/mean(DeltaT); mean near zero is undefined and cannot rank as a perfect zero score. The 3 K lower bound prevents off-power optima.
- Native input, ROI input, reflected/unabsorbed and outgoing optical power each use explicit nonnegative accounting with no double absorption. Global emitted energy is not assumed equal to finite-workpiece incident energy.

## Six-delivery evidence review plan

D1: inspect source recipe and units, check disabled emitter and angular probes independently, compare source hashes and actual readbacks. D2: inspect actual selections and physical conditions, independently evaluate integrals, temperature reference and energy balance. D3: inspect public MCP -> ManagedBackend -> Worker provenance for all returned candidates, L-to-source identity, fixed budget, same-input reuse and same-path changed-byte cache invalidation. D4: select the returned best/representative candidate and freshly solve it through the public installed interface on Windows 6.3 and 6.4; independently calculate metrics. The impossible ROI absorbed-power target 20 W is bounded above by alpha*18*1 W=10.8 W, regardless of L; a normal exhausted search remains only no feasible case found. D5: verify native source/temperature images and datasets correspond to that case, and a fresh same-version Worker opens a new-path saved MPH and packaged inputs. D6: compare each version against the independent reference and frozen tolerances, not merely against each other; run only affected inherited regressions.

Native logs, raw MCP request/response, build/version, candidate/source hashes, model and solution identity must be accessible to this Reviewer. A production summary alone cannot support approval. Physical calibration remains UNVERIFIED without measurement; Mac native and GUI status must be separately stated. Source code or synthetic source checks cannot stand in for the two Windows native chains.

Await main Agent's benchmark_spec for one pre-run freeze, then await the actual candidate for independent review. This note neither widens the six deliveries nor authorizes changing frozen tolerances after seeing a result.
