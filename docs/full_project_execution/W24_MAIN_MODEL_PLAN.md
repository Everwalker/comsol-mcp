# W24 main-agent model/history proposal

Status: PROPOSED_NOT_FROZEN. No native result or material calibration claimed. Final fixture equations, parameter sources, tolerances and budgets must be registered before a solve. The static shape and staged cure tests are independent required routes, not substitutes for one another.

## Static shape route

Use a small real capillary/free-surface problem with equal initial glue quantity in stepped and unstepped branches. A 2-D axisymmetric fixture is a useful resource-bounded option when the chosen substrate/fiber/mesa geometry is truly axisymmetric: report actual volume with its radial measure, not planar area. Explicitly map material-specific wetting surfaces, including step top, vertical side and the lower substrate. The step edge is geometry, not an artificial impermeable wall extending into the fluid. State pinning/hysteresis assumptions and do not tune them after observing the requested trend.

Phase-field or level-set multiphysics is a documented route, subject to actual installed module/API capability. Use real initialization followed by time-dependent relaxation. Define a convergence window for speed/kinetic energy or free-energy/shape changes, retain transient data, quantify volume drift, and test mesh/interface-width/time-step sensitivity. A time-series endpoint alone does not prove stable equilibrium. Keep the same physical parameters when refining mesh/interface representation. Contact angle, viscosity, density and surface tension may be synthetic fixtures, with sensitivity runs; label them accordingly.

## UV -> thermal cure -> cooling route

Prefer a common mesh and explicit persistent dependent variables for the first numerical fixture. Define UV absorption/dose, cure kinetics, heat transfer/reaction heat, pre/post-gel mechanical response and chemical/thermal eigenstrain. A purpose-built cure interface is optional: supported heat/solid plus managed domain ODE/PDE equations can form a legitimate common-version route if their actual equations and coupling are verified. Do not equate an ODE in Python with a solved COMSOL multiphysics model.

Track temperature, conversion, displacement, mechanical strain/stress and every history/reference internal variable required by the chosen constitutive law. Stage intervals must use consistent absolute time and pass the actual terminal native solution to the next stage. Retain source dataset/solution/time and read back the complete target initial state. Continue conversion and any viscoelastic variables; changing the loading schedule is not permission to set conversion, strain or stress-free reference back to initial values.

One explicit synthetic model option is a post-gel eigenstrain history: accumulate the thermal and chemical strain increments that occur after a declared gel criterion, with the accumulation itself represented by solved history variables. Then elastic strain is total strain minus those accumulated eigenstrains. This is a proposed limited constitutive fixture, not a generally validated adhesive law. Freeze the gel criterion, regularization and pre-gel stiffness treatment before solving. If instead using a native material Activation feature, verify exactly which reference/history it resets and do not apply activation anew at each stage boundary. Varying material stiffness without declaring the evolving reference law is not sufficient evidence of cure stress history.

## Independent checks to preregister

- Positive continuous three-stage run versus a one-study run of the identical schedule and equations; compare full final fields, not only metadata.
- Stage boundary continuity for T, conversion and each chosen history state; fresh saved-model reopening must retain them.
- A deliberate reset of conversion/reference history must be detected by the comparison, rather than accepted because final temperature matches.
- Simple constant-rate/isothermal cure limit with an independent analytical or separately integrated ODE reference; this reference checks the native solve and is not its replacement.
- Free-expansion mechanics limit (near-zero stress under compatible uniform eigenstrain) and an appropriately constrained analytical stress limit; declare dimensional/plane-stress/plane-strain assumptions.
- Energy/volume-or-mass accounting with explicit sources; no undocumented global geometry rescaling to repair conservation.
- Synthetic-material and contact-angle/dose/kinetics sensitivity; no physical claim for a commercial glue without independently supplied data.

Three stage reports, source/target solution identities, all persistent fields, raw request/results, native images and saved/reopened MPH are required. Existing W21 temperature transfer approval does not establish the cure/reference-state mechanism.

## Official same-version references inspected

- COMSOL 6.4, *Capillary Filling — Phase Field Method*, doc_id 14902, chunk 50758; `doc/help/wtpwebapps/ROOT/doc/com.comsol.help.models.cfd.capillary_filling_pf/capillary_filling_pf.html`; SHA256 `ad6d03b7f34e38323d022d802a029c564d4dd4de28b12fa77f0872eca441a45e`. Documents axisymmetric phase initialization/time-dependent workflow and sensitivity of measured wetting angle to refinement. Installed examples exist under CFD_Module/Multiphase_Flow/capillary_filling_pf.mph and Microfluidics_Module/Two-Phase_Flow/capillary_filling_pf.mph; do not redistribute commercial example bytes.
- COMSOL 6.4, *The Polymer Flow Module Physics Interface Guide*, doc_id 23511, chunk 102693; `doc/help/wtpwebapps/ROOT/doc/com.comsol.help.polymer/polymer_introduction.02.03.html`; SHA256 `d78d0ef5141b55b1b3dded6829cdc534ef51fadb6c1bc90a3ebd38f21a7c7660`. Documents curing and conversion/temperature-dependent chemoviscosity; not proof of 6.3 compatibility or a licensed module.
- COMSOL 6.4, *Thermal Stresses in a Layered Plate*, doc_id 21627, chunk 93361; `doc/help/wtpwebapps/ROOT/doc/com.comsol.help.models.sme.layered_plate/layered_plate.html`; SHA256 `c68c07e5e5a0d468f6888aca5986224ec81ff1d865353c5c83af0e3ed263372e`. Documents stress-free activation and reference-temperature/deformation limitations. This supports checking history semantics; it is not an adhesive cure validation.
