# W24 staged-cure coupon: freeze proposal

Status: `SETUP_PREFLIGHT_FAILED_UNKNOWN_PROPERTY_CLEANUP_VERIFIED`. The
2026-09-27 setup-only native attempt reached COMSOL 6.4.0.293, then failed while
building geometry because `intbnd` was set on the `fin` finalization feature.
The durable build job remains `UNKNOWN`; the Worker request was terminal
`FAILED` with execution state unknown. Its owned Worker/server identities were
reconciled and cleaned up, and the exact failure is preserved in
`evidence/setup_only_candidate_20260927T0117Z/native_run_20260927T0121Z/`.
An offline correction now sets the documented `intbnd` property on an explicit
`Union` geometry feature and retains the four-domain, entity-count, and
axisymmetric-volume gates. This change has not had a native retry. The setup
birth budget is consumed; no new W24 server may start without a separately
authorized campaign. No numerical or physical acceptance is claimed. The
independent W24 static glue-shape route remains required and is not covered by
this coupon.

## Current09 mesh-count finding and current10 contract

The 2026-10-12 current09 setup-only attempt failed in the Java coupon mesh-count predicate. Its durable job remains `UNKNOWN` with `safe_retry=false`; the predicate failure was `IllegalStateException: mesh element type counts do not bind a nonempty triangle mesh`. The actual type-count values and mesh arrays were not captured, so current09 does not establish which getter value caused the predicate to fail. Its exact failure, cleanup, and preservation records remain immutable; current10 is a separate offline source candidate.

The preserved COMSOL 6.4 MeshSequence API review (`ROOT_MESH_COUNT_PRIMARY_API.json`, SHA-256 `5519ca116cd32f1d4e6f24412d1eb9469a5877729ebf980917f9070aa3f306bf`; source doc7637/chunk22946, SHA-256 `0f46188b7694be0f1b7d188790218075aa6c831b58080f1d27ae04f3a141df23`) documents no-argument `getNumElem()` and type-specific `getNumElem(type)` queries separately, without documenting that their values must sum across dimensions. The fixture therefore records `mesh_num_elem_noarg` independently, requires it to be positive and unchanged across the read-only capture, and independently requires a positive `tri` type count equal to the actual triangle connectivity/entity column count. It does not infer a dimension-sum relation or assign an undocumented meaning to the no-argument value. Every reported type count remains nonnegative and is re-read after capture; unsupported nonempty types, malformed arrays, changed counts/types, invalid geometry/entity bindings, and any volume error above the existing `1e-6` limit still fail closed. The initial undeformed straight-edge coupon scope and all physical comparisons remain unchanged.

## Native route and API evidence

The initial route uses the common physics family `HeatTransferInSolids`,
`SolidMechanics`, and `DomainODE` for conversion and cure-history fields. It
does not depend on Polymer Flow Module's Curing Reaction interface. COMSOL 6.4
local completion data and the existing W15 capability map expose these
interface tokens; this is an API-availability clue only. It is not evidence of
module entitlement, successful physics creation, or a completed native solve.

The offline 6.4 API checks found:

- `GeomSequence.axisymmetric(boolean)` and `PhysicsList.create(tag, type,
  geometry, String[])` in the installed 6.4 API. This supports an axisymmetric
  2D geometry and explicitly named distributed-ODE dependent variables.
- The COMSOL 6.4 geometry API documents `Union` as a geometry feature whose
  input objects are selected with `feature(<ftag>).selection("input")`, and
  lists `intbnd` (`on|off`) among Union/Boolean properties. The installed
  `data/completion/geom.xml` assigns `intbnd` to `Union`, not the `fin`
  finalization feature. The corrected fixture creates `uni1`, selects the four
  rectangle objects, and sets `uni1.intbnd="on"`; `fin` only retains its
  documented union action. Native geometry still must prove four domains and
  the analytic axisymmetric measures before this API path is accepted.
- The local 6.4 reference defines Domain ODEs/DAEs and the Distributed ODE
  source term. The planned conversion and post-gel history are therefore
  solver fields in COMSOL, not Python-side integration.
- The 6.4 Solid Mechanics reference permits Initial Stress and Strain to vary
  with time/solution parameters and defines initial strain as an offset
  subtracted from total strain. Its External Strain subnode supports a
  user-defined volumetric-strain input. The proposed coupon uses the latter
  for the additive small-strain thermal and cure contributions.
- Existing real W21 6.3 and 6.4 configuration source sets
  `useinitsol=true`, `initmethod="sol"`, `initstudy=<previous study>`, and
  `solnum="last"` for a second transient stage. The proposed stages use that
  established form on the same model, mesh, and physics fields; W24 will still
  read back the actual source/target solution identities and state values.

The exact Java property keys and resulting native readbacks for the volumetric
External Strain node, convection feature, domain selections, and history-field
initial values must be established before the first solve. A failed/unknown
pre-solve API request stops dependent setup; no solver call follows an
unobserved or unknown worker request. Module availability checks are not
license checks. If native physics creation or solve reports a license error,
retain the exact error and block only the affected route.

## Version-specific External Strain scope

The current coupon implementation and its next setup-only candidate are
COMSOL 6.4 only. COMSOL 6.4 release notes say that the `Volumetric strain`
option was added to External Strain (COMSOL Release Notes, page 148, SHA-256
`76c82fa67e346fae699e9eb41304abd8abc665467bc2cb5f94938a6ed4f8515e`). The
6.4 user guide places External Strain under Solid Mechanics > Linear Elastic
Material and says the volumetric-strain mode takes a value or expression
(doc `sme_ug_solid.07.038.html`, chunk 110732, SHA-256
`a37129502952231632d743988bba9e3843bda3553d5adf6e8768b8070c2ed8c1`). These
sources do not establish the Java property token
`StrainInput="VolumetricStrain"`; the 6.4 fixture must keep its exact native
property/getter readback gate and must not claim it is verified until that
readback succeeds.

COMSOL 6.3 requires a separately compiled and exercised compatibility path;
changing only the requested engine-version label is not sufficient. Do not
send the 6.4 volumetric-input token to a 6.3 model. First establish from the
6.3 API/docs and a saved 6.3 model which External Strain tensor-input mode and
property keys are accepted. If the 6.3 interface supports a symmetric
small-strain tensor, the equivalent isotropic volumetric eigenstrain is
`diag(epsVol/3, epsVol/3, epsVol/3)` in radial, hoop, and axial directions,
with zero off-diagonal components, since its trace is `epsVol`. This mapping
is a derivation for the proposal's small-strain formulation, not proof that a
particular 6.3 property accepts those values. Implement it behind an explicit
version-specific adapter only after exact 6.3 setter/getter and unit readback
are verified. Validate it with the same two native mechanics checks: free
uniform expansion gives strain `epsVol/3` and near-zero stress; full restraint
gives normal stress `-K*epsVol` and near-zero shear. For the frozen coupon
values `E=2 GPa`, `nu=0.35`, and `epsVol=3e-4`, the analytic reference is
`-666666.6667 Pa`. If the 6.3 tensor mode cannot be established or either
benchmark fails, leave that version route unaccepted. The user's macOS 6.3
arm64/x86_64 checks remain `USER_REQUESTED_SKIP`; this note does not convert
them to PASS. It records the still-required Windows 6.3 branch separately from
the current COMSOL 6.4 setup candidate.

The raw local evidence basis is:

- COMSOL 6.4 `physics.xml`, SHA-256
  `efb2d0e44a5ea4d2f72aa3b22ba15026f7eaf8a4e39b267c6e036c1701d47d28`;
  `com.comsol.api_1.0.0.jar`,
  `9bdc47a9e320be5721956336f44f5afa4cb06a20cfa887bc7d32d1a837483a67`;
  `com.comsol.heat_1.0.0.jar`,
  `ecf7f5c7f9b15dd2cc97ac90228e792eca9a9a9407b217245d8de292f7c6065e`;
  `com.comsol.sme_1.0.0.jar`,
  `6fbd338c3ffa52ef85f40c8ea48135a7902978973bb5415126253908eb9db190`.
- COMSOL 6.4 Domain ODE interface documentation, doc 5355/chunk 18548,
  SHA-256 `4825b29f161edca5ceeb378a985704c19dc2812477c0936212f2a2b9d9585b32`;
  Distributed ODE source-term documentation, doc 5356/chunk 18549,
  `3f034e1efe5217cc0a38aa9c0150f45b90bfc909c34d0c6d8b39a16dd0e2ad43`.
- COMSOL 6.4 Initial Stress and Strain theory, doc 25982/chunk 110959,
  `ae4e1cabde6af4e7f317a1bef98672d3ec6c6c6811f4bea528c89b8ca649b4ea`;
  External Strain user documentation at
  `doc/help/wtpwebapps/ROOT/doc/com.comsol.help.sme/sme_ug_solid.07.038.html`,
  `a37129502952231632d743988bba9e3843bda3553d5adf6e8768b8070c2ed8c1`.
- COMSOL 6.4 `Using a Solution from Previous Study Steps`, doc
  6261/chunk 19889, `18715a0e9a14a14691400f883813fc0cb96e07ea3333f8b9767036ce85d00b47`.
- COMSOL 6.4 Time-Dependent Solver documentation, doc 6387/chunks 20098 and
  20100, `c8536a26490b167e03a6a5b3bdbc26986d69691e5eccd8dfb16a878be5fbe6c9`,
  and Time API documentation, doc 4646/chunks 17600, 17602, 17603,
  `952793e73990d64af9163e1c3f21b8f50c0b43bbc9b4b0b728be06cdb889c819`;
  these define BDF maximum-step, strict-step, output-storage, and
  relative/absolute-tolerance properties. In particular, `tstepsbdf="strict"`
  ends steps at requested times, `tout="tsteps"` with `tstepsstore=1` stores
  every accepted solver step, and `atolmethod` is a space-separated field/method
  list. These are documentation support, not native readback.
- COMSOL 6.4 Solution Data documentation, doc 4604/chunk 17527,
  `551d3f05317ab35fd2a6aeb37390c7d74979209137d25347b601602ba6b3ba45`, and
  Programming Reference doc 3626/chunk 11404, page 649,
  `5b7f23ad2eae77f59d71935f4f6c9b6b9ede380dc34da065181841e512bbc105`;
  these document `getU(solnum,"Sol")` and the exact `XmeshInfo.dofs()` arrays
  used by the proposed all-DOF comparison.
- The two-version initialization property form is present in existing W21
  configuration sources under
  `evidence/w21_closure/windows/closure_w21_63_final/project/w21/config/` and
  `evidence/w21_closure/windows/smoke64_11/project/w21/config/`. Those are
  stage-transfer API evidence, not cure-history acceptance.
- The local build currently reports COMSOL 6.4.0.293 in the prior read-only
  launcher evidence. External Corretto Java 11.0.31 is installed. No engine
  identity or module license result is implied here.

## Candidate model definition (proposal; not frozen)

Candidate ID: `W24-CURE-HIST-AXISYM-01`.

Use a 2D axisymmetric, bonded, four-domain coupon with radial coordinate `r`
and axial coordinate `z`; all dimensions are SI. Preserve interior boundaries
between material domains. The solids are perfectly bonded with no contact
slip. This is a synthetic end-bonded fiber coupon and does not represent a
calibrated adhesive or the required static meniscus shape.

| Domain | Radial interval | Axial interval | Material properties |
|---|---:|---:|---|
| Alumina platform | 0–250 µm | 0–500 µm | `k=25 W/(m K)`, `rho=3900 kg/m^3`, `Cp=880 J/(kg K)`, `E=370 GPa`, `nu=0.22`, `alphaT=7.5e-6 1/K` |
| Gold pad | 0–100 µm | 500–510 µm | `k=318 W/(m K)`, `rho=19300 kg/m^3`, `Cp=129 J/(kg K)`, `E=78 GPa`, `nu=0.44`, `alphaT=14.2e-6 1/K` |
| Synthetic adhesive | 0–100 µm | 510–550 µm | `k=0.25 W/(m K)`, `rho=1200 kg/m^3`, `Cp=1000 J/(kg K)`, `E=2 GPa`, `nu=0.35`, `alphaT=50e-6 1/K` |
| Fused-silica fiber | 0–62.5 µm | 550–1050 µm | `k=1.38 W/(m K)`, `rho=2200 kg/m^3`, `Cp=703 J/(kg K)`, `E=72 GPa`, `nu=0.17`, `alphaT=0.55e-6 1/K` |

Every material value above is synthetic and frozen for numerical testing; it
must not be described as measured device or commercial adhesive data. Expected
axisymmetric domain volumes, using `V=2*pi*integral(r dr dz)`, are:

- alumina: `9.81747704247e-11 m^3`;
- gold: `3.14159265359e-13 m^3`;
- adhesive: `1.25663706144e-12 m^3`;
- fiber: `6.13592315154e-12 m^3`.

All adhesive volume averages and energy integrals use the axisymmetric physical
measure. Before a solve, verify the native integration operator against all
four analytic volumes above; use the operator's documented axisymmetric weight
once, not a second manually applied `2*pi*r` factor. If its result does not
match these volumes within `1e-6` relative, stop before solving.

The model starts stress-free at `T0=298.15 K`, `alpha0=0.20`, and zero
post-gel shrink-history state. The synthetic adhesive has constant
`E=2 GPa` and is mechanically active from `t=0`. Thermal eigenstrain
`3*alphaT*(T-T0)` is active throughout every stage. Since the adhesive reaction
source is nonzero and can raise temperature before `alpha` reaches `0.50`, this
model can develop pre-gel thermal strain and stress. The earlier claim that it
had no pre-gel thermal strain is withdrawn. `alpha_gel=0.50` gates only the
post-gel chemical shrink-history variable `qpost`; this model has no gel-time
stress-free reference update, modulus transition, or viscoelastic relaxation.
It is a limited solid-from-start numerical mechanism fixture, not a physically
complete adhesive cure law. The full gel-dependent reference-state/viscoelastic
law, calibrated adhesive, and static glue shape remain open W24 work.

## Equations and loading schedule

Solve the actual conversion field `alpha(r,z,t)` with a Domain ODE on the
adhesive domain only:

```text
kT(T) = A * exp(-Ea/(R*T))
rate = (kUV*IUV(t) + kT(ht.T)) * (1-alpha)
d(alpha,t) = rate
Qrxn = rho_adh * Hrxn * rate
```

Use `A=1.0e5 1/s`, `Ea=55.0 kJ/mol`, `R=8.31446261815324 J/(mol K)`,
`kUV=1.0e-2 1/s`, and `Hrxn=50.0 kJ/kg`. Add `Qrxn` as a volumetric source
only in the adhesive. The thermal problem is the installed Heat Transfer in
Solids interface with `T0` initial values and a convection boundary on every
external surface, `h=250 W/(m^2 K)`, with the following ambient schedule:

- `0–120 s`: `Tenv=298.15 K`, `IUV=1`;
- `120–240 s`: linear ramp `298.15 K -> 393.15 K`, `IUV=0`;
- `240–960 s`: `Tenv=393.15 K`, `IUV=0`;
- `960–1500 s`: linear ramp `393.15 K -> 298.15 K`, `IUV=0`.

Solve a second Domain ODE field `alpha_iso` on the adhesive at fixed `T0`
with the same `IUV(t)` and initial value `alpha0`. It is a native solved-field
check against the independent analytic first-order solution

```text
alpha_analytic(t) = 1 - (1-alpha0) * exp(-kT(T0)*t - kUV*min(t,120 s))
```

This reference remains separate from `alpha`; it never substitutes for the
heat-dependent cure solution. The preregistered analytic values at `t=`
`0, 10, 30, 47, 60, 120, 240, 960, 1500 s` are respectively
`0.200000000000, 0.276297570337, 0.407756752167, 0.500541748700,
0.561559919245, 0.759712869484, 0.760379255278, 0.764338939231,
0.767265702370`.

Solve an additional adhesive-domain history state `qpost` with initial value
zero and source

```text
d(qpost,t) = if(alpha >= alpha_gel, rate/(1-alpha_gel), 0)
epsVolExternal = 3*alphaT*(ht.T-T0) - 0.015*qpost
```

The gel gate is the hard, unsmoothed predicate `alpha >= 0.50`; the solver
does not locate a separate event or reset a material reference at crossing.
Use a constant BDF maximum step of `1 s` in every baseline/cure control, so
the gate is resolved by at most a one-second integration interval. The native
output schedule stores every second from `0` through `120 s`, which records
the spatial conversion and `qpost` onset around the analytic isothermal crossing
near `47 s`. The tightened temporal control uses `0.5 s` maximum steps and
half-second outputs on `0–120 s`. No post-observation gel smoothing or threshold
change is allowed.

Apply the isotropic small-strain thermal plus cure contribution as the
COMSOL-documented **volumetric strain** input to the appropriate
Solid Mechanics External Strain subnode: the expression above in the adhesive
and `3*alphaT_i*(ht.T-T0)` in the three nonadhesive domains. Keep the volumetric
shrink cap at `-0.015` and the gel threshold at `0.50` for all cases. Do not
replace this with caller-generated arrays or scalar field dot products. Exact
External Strain feature selection/property keys and readback are a pre-solve
API check; if they cannot be proved, report the specific API failure and do
not solve.

Use the Solid Mechanics interface with a fixed bottom face on the alumina
platform, axisymmetry on the `r=0` line, and traction-free remaining external
surfaces. Retain shared interfaces between alumina, gold, adhesive, and fiber.
The output includes full `T`, `alpha`, `alpha_iso`, `qpost`, displacement, and
stress fields. Do not use a singular peak stress at a material junction as an
acceptance metric; compare volume averages and preregistered interior samples.
Solid Mechanics is **quasistatic** at each thermal/cure time: inertia is
disabled and displacement/stress are algebraic outputs, not persistent
mechanical history states. Before the first solve, read back the actual native
inertia setting and the small-strain formulation. If either cannot be shown to
match this definition, stop before solving. The only transferred internal
history states are `T`, `alpha`, `alpha_iso`, and `qpost`.

## Studies, controls, and acceptance limits

The staged path uses three transient studies on the same geometry, mesh, and
physics, with absolute time throughout. Outputs are every `1 s` on `0–120 s`
and every `10 s` on `120–1500 s`; `47 s` is explicitly present in both the
analytic-check list and each relevant native output schedule:

- `stdUV`: `0–120 s`, outputs every `1 s` (including `47 s`);
- `stdBake`: `120–960 s`, outputs every `10 s`, initialized from the last
  native solution of `stdUV`;
- `stdCool`: `960–1500 s`, outputs every `10 s`, initialized from the last
  native solution of `stdBake`.

Both handoffs explicitly set the known `useinitsol/initmethod/initstudy/solnum`
settings. The test must record actual source and target solution tags and
read back all dependent fields; matching study metadata alone is insufficient.

The native solver policy is frozen for this proposal and must be read back
before the first run. Use BDF with `maxstepconstraintbdf="const"`,
`maxstepbdf=1[s]`, and `rtol=1e-5` in the baseline, staged/continuous,
reset, coarse-mesh, and dose-sensitivity runs. Use `maxstepbdf=0.5[s]` in the
tight-time control. The frozen six-entry strings are logical component-input
and method requirements, not a native Time-solver entry table:
`atolmethod="T unscaled alpha unscaled alpha_iso unscaled qpost unscaled u unscaled w unscaled"`,
with `atolglobalmethod="unscaled"`, `atolglobal=1e-8`, and logical absolute
component-input values
`"T 1e-4 alpha 1e-8 alpha_iso 1e-8 qpost 1e-8 u 1e-12 w 1e-12"`.
Before mapping the logical displacement inputs, read back a two-dimensional
axisymmetric geometry and both actual boolean getters
`solid.prop("Mode2Daxi").getBoolean("includeTwist")` and
`solid.prop("Mode2Daxi").getBoolean("ModeExtension")`. Both must be false,
without setters, defaults or fallback. Preserve their exact property/getter
provenance across all stages. The unique Solid Mechanics `PhysicsField` with
`field()="u"` may have distinct raw `component()` values exactly `{u,w}` or
`{u,v,w}`; retain its original names, length and order. Raw descriptor metadata
does not establish active solved DOFs. The configured no-twist/no-mode-extension
formulation maps only logical `u,w` to the actual `comp1_u` Time entry.
Effective solved-DOF validation and tolerance conversion remain `UNVERIFIED`.
Enumerate the complete `solid.field().tags()` list and read every field name
and component array; uniqueness applies to the `field()="u"` selection, not
the total list length. Retain full inventory/count and exact selected row.
Other descriptors are observed metadata without an active/inactive claim.
No nonselected descriptor may share any selected raw component, including
`v` when present; unrelated auxiliary sharing has no global uniqueness rule.
Missing, duplicate, incomplete or failed getters remain fail-closed. Sequential
geometry, full fields, Time keys and mode getter returns are saved before
validation; failure diagnostics retain all actual earlier observations and
mark unobserved later returns explicitly.
Current07 retained seven descriptors and selected raw `[u,v,w]`, but did not
read either mode value: its actual configuration remains `UNOBSERVED`.
Current06 raw tags/count were not retained. Official shipped build257 XML/UI
history establishes the property mapping and boolean setters; the official
6.4 API and installed JAR confirm the typed getter. These are mapping evidence,
not build293 model-mode readback or scientific acceptance. Candidate19 is a
software configuration/inventory correction; no new native attempt is accepted.
The generated
Time solver has the actual native keys `comp1_T`, `comp1_alpha`,
`comp1_alpha_iso`, `comp1_qpost`, and `comp1_u`; both logical displacement
inputs `u` and `w` bind to the observed `comp1_u` entry. Apply setters only to
the actual keys returned by that Time solver after validating the geometry,
PhysicsField descriptor, and component-to-entry binding. Do not issue a setter
for `comp1_w` or invent a native `comp1_w` row. For every actual solver entry,
set `atolvaluemethod="manual"`, the 6.4 solver's explicit manual-value mode,
so its absolute tolerance is applied rather than treated as a scale factor.
Keep the actual entry table and its method and value readbacks separate from
the descriptor/binding and the six derived logical component rows. Preserve
and independently configure/read back the dose entry `comp1_Duv_rel` at
`1e-8` wherever it is present; never infer tolerance keys from the PhysicsField
component list. The installed COMSOL 6.4
`data/completion/sol.xml` identifies `atolvaluemethod` as a `StringArray` with
`factor|manual` values and `atol` as a field-keyed tolerance array. If COMSOL
exposes a missing or ambiguous descriptor, a different actual entry table, or
rejects any property, stop before solving and revise/freeze a new proposal.
The derived displacement values remain frozen component-comparison inputs;
effective serendipity field-to-DOF tolerance conversion remains `UNVERIFIED`.
These settings target
one-second resolution of the hard gel gate; they do not claim exact event
localization. Set `tstepsbdf="strict"` so each BDF run takes a step ending at
every requested `tlist` time; set `tout="tsteps"` and `tstepsstore=1` to store
every accepted BDF step. Read back all three properties. The exact requested
times (including `47 s`) must be present in the stored-time vector; comparisons
and analytic checks use only exact requested-time matches within `1e-12 s`,
never the closest neighboring step. Verify that consecutive stored accepted
steps do not exceed `1 s` (or `0.5 s` in the tightened control).

The setup-only candidate also captures the read-only Solid Mechanics Equation
View descriptor table by enumerating actual physics and feature tags and
calling the documented `featureInfo("info").getInfoTable("Expression",
"recursive","all")` getter on every Solid Mechanics feature. Save every native
row and returned column in a separate JSON artifact with the observed tag list,
row counts, candidate-filter rule, byte count, and SHA-256. Any failed feature
read or incomplete/malformed row makes this inventory incomplete. Do not call
`set`, `clearLocks`, or `removeLock` while collecting the table. Candidate
filtering is only a discovery aid; it does not show that an expression can be
evaluated or that a variable is a particular stress component. The pre-solve
campaign freeze must bind the complete raw inventory and its exact hash.

The future solve campaign is capped at ten native solve submissions in this
fixed order: (1) free-expansion mechanics benchmark; (2) fully-fixed mechanics
benchmark; (3–5) the three staged runs; (6) the same-equation continuous
comparator; (7) the deliberately reset stage-2 negative control; (8) the
coarse-mesh run; (9) the tight-time run; and (10) the dose-sensitivity run.
The mechanics benchmarks run first, before any cure-history solve, so their
native expression evaluation, descriptor metadata, units, and analytic
mechanics results gate the remaining eight slots. All cure controls start
from separate copies of a saved immutable
configured template, so reset/coarse/dose/time controls cannot mutate the
staged baseline. The setup-only preflight saves `cure_template.mph` after native
geometry/physics/mesh/solver readback and before any solve, then reloads that
MPH in the same sequential Worker session. Require the managed structural
signature, all geometry/physics/solver readbacks, and the complete raw
Equation View feature tables to match across save/reopen. After reviewing the
preflight and full Equation View readbacks, freeze the complete solve
executor, controls, inputs, descriptor artifact, and criteria before starting
a separate campaign. Save `staged_baseline.mph`
immediately when the third staged solve reaches terminal success, before
derived checks. The continuous comparator uses the same fine mesh, inputs,
equations, output times, tolerances, and `1 s` maximum step. The reset control
initializes stage 2 from declared initial values instead of the previous
terminal solution; it must be detected as a negative control and never counted
as a passed trajectory.

The dose case changes only `kUV=1.1e-2 1/s`. Its adhesive mean conversion at
`120 s` must be at least `0.01` above the baseline, with the observed difference
reported. The coarse mesh uses adhesive maximum size `20 µm`; the fine baseline
uses `8 µm`; both use `50 µm` in platform/gold and `25 µm` in fiber. The
tight-time case uses `0.5 s` maximum steps and outputs every `0.5 s` on
`0–120 s`, then every `10 s`, and is compared at every common output time.

The setup-only preflight has a separate budget: one task-owned server, at most
`15 min` from exact process birth (including setup, readback, template
save/reopen, and cleanup), zero solve submissions, and one sequential Worker
session. A later solve campaign
has its own separately recorded server birth and `60 min` budget, at most ten
solve submissions, and at most two sequential Worker sessions (never
concurrently) so the staged baseline can be reopened in a fresh Worker. The
solve campaign remains `NOT_FROZEN` until the preflight readbacks have been
reviewed and its complete executor, controls, and exact inputs are frozen.
Neither campaign may reset its budget or silently retry after failure. Stop
dependent setup if an API request is unknown or has no observed terminal result.

The two additional mechanics benchmarks use an isolated axisymmetric adhesive
cylinder (`r=0–100 µm`, `z=0–200 µm`, `E=2 GPa`, `nu=0.35`) and a uniform
volumetric external strain `epsVol=3e-4` with no thermal or cure source. In the
free case, fix only the axial displacement at the bottom-axis point to remove
rigid translation; axisymmetry supplies the radial gauge. Expected expansion
is `u_r(R)=10 nm`, `u_z(H)=20 nm` (uniform linear strain `1e-4`) and stress
approximately zero. Require both displacement values within `0.1%` and every
normal/shear stress component's domain maximum absolute value below `100 Pa`.
In the fully fixed case, constrain all exterior displacement components to
zero. Require maximum displacement below `1e-12 m`, each domain mean normal
stress within `0.1%` of `-K*epsVol=-666666.6667 Pa`, and maximum absolute shear
stress below `100 Pa`, where `K=E/[3*(1-2*nu)]`.

The setup-only Equation View capture is a read-only descriptor discovery, not
an evaluation claim. Before the first mechanics solve, use the frozen native
table rows to map exactly one expression each to radial normal, hoop normal,
axial normal, and r-z shear stress. Require the native descriptions to
distinguish those four physical components and every selected unit to be `Pa`;
do not infer component identity from variable spelling or the isotropic
benchmark values. If any mapping is absent or ambiguous, stop before solving.
Then evaluate the selected expressions natively in both mechanics benchmark
models and require finite values, `Pa` units, and the analytic displacement,
normal-stress, and shear-stress checks above. Both benchmarks run before any
cure-history solve; a failed value, unit, or analytic gate stops the remaining
campaign. Never guess a variable name or derive stress from a custom
postprocessing constitutive formula.

The frozen numerical checks are:

- Build four axisymmetric domains with the above exact bounds; each native
  readback volume must differ from its analytic volume by at most `1e-6`
  relative.
- At the nine listed analytic checkpoints, the native adhesive-domain
  `alpha_iso` volume average must be within `1e-5` absolute conversion of the
  independent formula. It must remain finite and in `[0.20,1]`.
- In the actual cure solution, `alpha` must be monotone and finite, `qpost`
  must remain monotone and in `[0,1]`, the final adhesive mean conversion must
  be at least `0.95`, and final `qpost` must be at least `0.90`.
- At both positive stage boundaries (`120` and `960 s`), compare the source
  terminal native solution with the target's first stored solution on the same
  mesh. Maximum absolute jumps must be at most `1e-4 K` for `T`, `1e-5` for
  `alpha` and `qpost`, and `1e-10 m` for either displacement component. For
  stress, compare all four axisymmetric components at the fixed adhesive
  interior probes `(r,z)=(25,520),(50,530),(75,540) µm`; each component jump
  must be at most `1 Pa`, and the Frobenius norm of the adhesive volume-mean
  stress-tensor jump, divided by `max(source mean-stress Frobenius norm, 1 kPa)`,
  must be at most `1e-3`. Do not use material-junction peak stress.
- For the staged/continuous all-field comparison, use the **same immutable fine
  mesh** and exact native solution DOF mapping. Read `XmeshInfo.dofs()` arrays
  `geomNums`, `nodes`, `coords`, `dofNames`, `nameInds`, and `solVectorInds`; map
  values by exact `(geomNum,node,coordinate,dofName,nameIndex)` keys and reject
  any missing, duplicate, nonfinite, or unmatched key. Do not interpolate or
  use nearest-node matching. At every common output time, use the continuous
  solution as reference `b` and calculate
  `||a-b||_2 / max(||b||_2, floor*sqrt(N))`. Require at most `1e-3` for `T-T0`
  and the combined displacement vector, with denominator floors `1 K` and
  `1e-8 m` respectively;
  require maximum absolute errors at most `1e-4` for `alpha` and `qpost` over
  all mapped DOFs. Use `getU(solnum,"Sol")` and the exact stored time readback;
  summary scalars alone do not pass this gate.
- The negative control must be detected at the `120 s` stage handoff by
  `|alpha_reset-alpha_terminal| >= 0.20` or
  `|qpost_reset-qpost_terminal| >= 0.20`; it is recorded as an expected
  failure-control result, not a passed trajectory.
- The coarser mesh and tighter time-resolution cases must keep final adhesive
  mean conversion within `1%` of the fine baseline. Their adhesive volume-mean
  stress Frobenius difference divided by `max(fine-baseline norm, 1 kPa)` and
  fiber-end displacement difference divided by `max(abs(fine-baseline value),
  10 nm)` must each be at most `5%`. The sensitivity case must produce the
  directionally expected increase in conversion at `120 s` and report the
  measured change.
- At each accepted native time `t`, calculate
  `E(t)=sum_domains integral(rho*Cp*(T-T0) dV)` and the signed outward
  `Qout(t)=integral_time integral_external_boundary(h*(T-Tenv) dA dt)`, which
  is positive when heat leaves the coupon. Calculate
  `Qrxn(t)=integral_time integral_adhesive(rho_adh*Hrxn*rate dV dt)`.
  Use the single axisymmetric physical measure verified against the four domain
  volumes, retain accepted BDF steps (`tout="tsteps"`, `tstepsstore=1`) and
  force exact output times (`tstepsbdf="strict"`), and apply composite
  trapezoidal integration to the native spatial-integral values at each
  consecutive accepted time pair; the maximum time gap must be `1 s` (or
  `0.5 s` for the tightened control). The signed residual is
  `R(t)=E(t)-E(0)-Qrxn(t)+Qout(t)`. Require
  `abs(R(t))/max(abs(E(t)-E(0))+abs(Qrxn(t))+abs(Qout(t)), 1e-12 J) <= 0.03`
  at every stored accepted time. Do not use absolute boundary flux before the
  signed time integration. Missing native boundary-flux values, unobserved
  solver steps, or a gap beyond the frozen maximum step is `NOT_RUN`/failure of
  this criterion, not a waiver.
- Save the solved staged baseline and reopen it in the second sequential Worker
  on the same owned server, without a study run.
  Read back engine/build identity, model/solution/dataset tags, `T`, `alpha`,
  `alpha_iso`, `qpost`, displacement, stress, and the two stage handoff states.
  The fresh-worker fields must meet the same checks against the saved native
  solution.

The independent solve campaign budget is at most one task-owned COMSOL 6.4
server and `60 min` wall time from its separate process birth, including setup,
at most ten solve submissions, readbacks, save/reopen, and cleanup. At most two
sequential Worker sessions may connect to that server. This budget is not
shared with or granted by the setup-only preflight. No shared server, existing
process, user model, system firewall, or global license configuration may be
changed. No GUI or Computer Use is involved. A timeout requires checking the
original worker request and owned process before any further action; no
solver retry is implicit. Save the staged baseline immediately after the final
staged solve reaches terminal success, before derived-field checks.

## W24 scope still open after this first coupon

Even if this limited cure coupon passes, it does not close W24. The separate
static capillary/phase-field or level-set glue-shape route still needs
equal-volume stepped/unstepped comparisons, material-specific wetting/contact
angle partitions, stable-shape and volume-conservation checks, and mesh / time
step sensitivity. The coupon also does not model viscoelastic relaxation,
real glue calibration, imperfect fiber/gold/ceramic interfaces, or complete
device geometry. These remain explicit W24 implementation and validation
work; neither a module-availability result nor this synthetic fixture can
close those requirements.


## Candidate20 software physical-volume observation correction

Current08 failed before a successful build readback: native geometry
`getVolume()` on its 2-D domain returned an observed cross-section area,
`1.2500000823638413e-7 m^2`. It was compared with the frozen physical
volume `9.81747704247e-11 m^3`; that observation is not an accepted volume.
All UNKNOWN requests and raw evidence remain protected.

The coupon preserves this native area as `cross_section_area_m2` and observes
`volume_m3` through a temporary Mesh dataset bound explicitly to already-built
`mesh1`, with IntSurface, `data` bound to that dataset, geom1 dimension 2 and
the exact singleton domain, expression `1`, unit `m^3`, and `intvolume=on`.
Actual geometry, mesh, dataset type/binding, numerical type/data, selection,
expression/unit/axisymmetric integration getters must match before `getReal()`.
Only an exact 1x1 finite positive result is eligible for the unchanged four
expected volumes and relative tolerance `1e-6`. There is no solution dataset,
solnum, default dataset, mesh rebuild, analytical multiplier, or fallback.
Only newly owned temporary numerical/dataset nodes are removed, in that order;
actual post-cleanup tags must preserve the preexisting inventory. Getter,
evaluation and cleanup failures retain actual partial observations and fail
closed; cleanup failure cannot hide the primary failure.

The COMSOL 6.4 primary evidence is preserved in current08's
`physical-volume-readonly-diagnosis001/candidate20-readonly-evidence-and-minimal-plan001.json`
(SHA256 `18dd78f136521dda7e3a1f9d96135e8805b3ddb64d35601c94b27f8330c171ce`):
Mesh API doc4508/chunk17341, Mesh UI doc5995/chunk19453, official mesh
visualization example doc11792/chunk34338, IntSurface integration properties
doc4487/chunks17297-17299, and GeomMeasureBase doc7588/chunk22875.
The documented Mesh/IntSurface composition is runtime UNVERIFIED until a
separately authorized fresh setup attempt. Proxy controls prove software
getter, binding, shape, threshold and owned-cleanup policy only. Candidate19
mode/field/Time/tolerance contracts and Science source remain unchanged;
effective solved DOF and numerical/scientific acceptance remain UNVERIFIED/NOT_RUN.


## Candidate21 software mesh-generation composition repair

Current09 setup failed before area/integral observation: its actual retained
failure reports geom1 dimension 2 and axisymmetric, domain [1], mesh1 bound to
geom1 with dimension 2, `isEmpty=true`, and `getNumElem=0`. The retained build
readback is empty; full field/mode/Time readback, Equation View, save/reopen and
science were not reached. Eight UNKNOWN attempts remain protected, without replay.

The coupon's existing mesh sequence had a default Size and four local Size
attributes, followed by `mesh.run()`, but no element-generating feature.
Candidate21 adds only `mesh.create("ftri1", "FreeTri")` after those attributes
and immediately before that existing run. COMSOL 6.4 FreeTri doc4379/chunk17131,
SHA256 `a47a086e293788556ca136312089ba1aee148566543a316ef7e4c55cd5809b7b`,
documents triangular generation in 2D and default selection of remaining entities.
All four domain selections and sizes, geometry, physics, studies, field/mode
checks, physical-volume getter, four expected volumes and `1e-6` tolerance remain
unchanged. No meshing or mode setter was added to an observation getter.

The existing Java proxy invokes the production mesh builder, checks frozen
size/selection inputs and generator/run order, and contrasts a synthetic
Size-only empty sequence with default remaining-domain generation. These are
software composition controls, not actual COMSOL meshing or accepted volume.
Actual FreeTri execution and Mesh/IntSurface physical-volume integration remain
UNVERIFIED; science and effective solved DOF are NOT_RUN/UNVERIFIED. This repair
does not modify the separately deferred Science/mechanics mesh configuration.

## 2026-10-02 candidate22 successor: documented spatial-frame physical measurement

Current10 remains an immutable failed setup attempt (terminal exit 2, one Server/Worker, zero RUN). The retained failure observed a nonempty 660-element mesh and positive cross-section area, but IntSurface/intvolume=on failed at getReal with undefined material-coordinate R in 2*pi*R. This is no accepted physical volume; the raw geometry/mesh/node bindings and owned temporary-node cleanup were observed only in the failure inventory. Nine UNKNOWN attempts remain protected against replay.

Candidate22 changes only the existing coupon physical-measure configuration to documented MeasureSurface with Mesh/mesh1, frametype=spatial, intvolume=on and unit m^3. Its actual type and spatial-frame getters must pass before getReal; the undocumented expr setter/getter is removed. The physical source is recorded as NATIVE_MESH_MEASURESURFACE_SPATIAL_INTVOLUME_ON. Exact geometry dimension/axisymmetry/domain selection, positive already-built mesh, 1x1 finite-positive result, four expected volumes, relative tolerance 1e-6, unique owned temporary nodes and fail-closed cleanup are unchanged. FreeTri, all dimensions, materials, modes, raw fields, Time/tolerances and physics are unchanged. There is no analytic Jacobian, area multiplication, fallback, Solution dataset, solnum, rebuild or solve.

Primary evidence: current10/root-measurement-frame-primary-read001.json (SHA256 e200837105573090675070f673ed37be6bd8a3cf6602fd35858e92dc7251a5ef); root-candidate22-measurement-frame-repair-plan001.md (SHA256 d478c2cbada3e5ab8cd6d15fe828afb253e6bf8b60bef98f30fe1a99a6a5ef47). Same-version Measurement4506/ch17336–17338 documents MeasureSurface/data/frametype/spatial/intvolume/unit/getReal; Mesh4508/ch17341 documents spatial coordinates; Frames5160/ch18291 distinguishes r from R. Documentation and proxy checks support software configuration only; this composition on the actual Mesh dataset remains runtime UNVERIFIED. Science applicability repair is deferred to a separate unit; effective solved DOF UNVERIFIED, native GO false, Science NOT_RUN. Existing production-helper proxy controls additionally reject old IntSurface type, type-getter failure, material-frame mismatch and frame-getter failure, enforce only documented setters and no expr, and retain previous binding/matrix/threshold/cleanup negative controls. Actual counts and immutable execution evidence belong to the candidate22 software handoff.

## 2026-10-02 candidate23 successor: Science measure applicability (software only)

After candidate22 root SOFTWARE_ONLY acceptance, the separately deferred Science unit corrects dimension-bound numerical types: IntSurface/intvolume for 2D-domain integrals, IntLine/intsurface for 1D boundary integrals, AvSurface/intvolume for 2D revolved-volume averages, and pointwise MaxSurface for 2D maxima without integration flags. Both numerical helpers and both Interp readback sites verify actual getType before evaluation and record that observed type. Mechanics mesh1 adds documented FreeTri immediately before its existing run, retaining Size hmax/hmin and all model selections/settings. Python consumers accept these types and reject obsolete 3D types, wrong bindings/dimensions/physical flags/units and pointwise maxima that claim integration weighting.

Primary scope is root-candidate23-science-implementation-unit001.md (SHA256 e82a568f6929f57a8d48cd421cf98c60ecf8d4b838f0a95985a9d12d33c2a5f0), the preserved deferred plans and their same-version Int/Av/Max/2D example evidence. Expressions, units, Solution data/solnum=all, sampling times, geometry, physical/material/constitutive/stress mappings, acceptance thresholds and existing temporary-result cleanup remain unchanged. Candidate22 Coupon Java and setup runner are unchanged. Nine historical UNKNOWNs and all prior artifacts are preserved. Existing Java private-helper proxies plus focused Python positive/negative consumers are software fixtures only; actual native/scientific execution and effective solved DOF remain NOT_RUN/UNVERIFIED. No new native GO or engine birth belongs to this unit. Actual frozen test counts and hashes are recorded separately in the candidate23 handoff.


### Candidate24 revision001 — actual measurement unit value-type dispatch (software only)

Current11 retained the native MeasureSurface type, frametype=spatial, mesh1 with 660 elements and two-dimensional cross-section area. Its numerical unit getter failed because the unit property could not convert to a string array. getReal was not reached, no physical volume was accepted, and the actual unit value type/representation was unobserved. The failed attempt and its new UNKNOWN job remain immutable; no replay or native GO is implied.

The Coupon physical-volume helper now reads getValueType("unit") first, retaining the actual type. Only exact String dispatches to getString("unit") and exact StringArray dispatches to getStringArray("unit"). The actual scalar or vector is retained as unit_raw, and a single scalar is normalized to one entry solely for the unchanged exact singleton m^3 acceptance check. Unknown/null types, null/empty/wrong values, extra vector entries and getter errors fail closed before getReal; there is no catch-based getter fallback. Same-version PropFeature documentation explicitly maps these two types to these getters; the MeasureSurface table's String array label is not a runtime type observation.

The unit setter, MeasureSurface/spatial frame, Mesh/mesh1 bindings, selections, four frozen expected volumes, 1e-6 tolerance, positive 1x1 getReal contract, FreeTri, physics and owned-node finally cleanup are unchanged. Science and production setup runner are byte unchanged. Existing getter proxies exercise scalar/vector positives and type/shape/unit/getter negatives with partial-observation and no-fallback assertions; affected Coupon tests plus the original twelve required regressions are the bounded software selection. Native and scientific acceptance remain NOT_RUN/UNVERIFIED until a separately authorized fresh attempt; ten UNKNOWN histories are protected.

## Current09 successor: initial volume from the actual built triangle mesh (offline candidate)

The current08 setup-only native attempt remains a preserved failure: MeasureSurface's spatial-frame `getReal()` failed on its internally formed `2*pi*R`; the original durable outcome is not replayed or reclassified. Its native receipt and scoped preservation/cleanup check remain authoritative for that activity.

This source candidate computes only the initial, undeformed straight-edged coupon's axisymmetric volume from read-only getters on its already-built `comp1/mesh1`. It captures the actual geometry dimension/axisymmetry/length unit, mesh geometry/dimension/empty flag, total and per-type counts, vertex matrix, triangular connectivity matrix, and geometric-entity vector. It preserves the API-native shapes: `getVertex` and `getElem("tri")` return matrices with vertices/elements in columns, while `getElemEntity("tri")` returns a flat `int[]` with one entry per triangle. COMSOL 6.4 Accessing Mesh Data doc4332/chunk17080 (source SHA256 `61381c7186fee5f54a11364f5717b2dd4869872bdede6b7f24f8802ad9ed8cfc`) documents those respective shapes; installed API `javap` output is retained with the candidate's offline Java compile evidence.

The observer rejects malformed or changing counts, unsupported nonempty 2-D element types, invalid indices, nonfinite coordinates, negative radii, degenerate triangles, missing selected domains, and any mismatch in the frozen four-domain mapping. For each actual selected-domain triangle it calculates planar area `A` from native `r,z` coordinates and centroid radius `rbar`, then sums `2*pi*A*rbar`. Since radius is linear on each straight triangle, this is the exact integral of `2*pi*r` over that triangle. The independently retained geometry `getVolume()` result is labeled cross-section area only; it is not multiplied or accepted as the physical volume. Frozen analytic volumes remain comparators only, with the unchanged `1e-6` relative tolerance. Unsupported quadrilateral/top-dimensional content fails closed.

The raw arrays and derived proof are bound to project, ModelRef, revision, domain subsets, and exact bytes, with separate no-clobber project/evidence artifacts for build and reopen. Reopen requires identical mesh bytes, per-domain triangle subsets, and resulting domain values. This is an offline source/proxy candidate: the native mesh integral, saved/reopened identity, and physical acceptance have not run for current09. It does not validate deformed material volume, solved history, or the deferred static-shape/cure campaign. COMSOL 6.4 Java compilation and proxy tests are software evidence only; a fresh source review and exact native admission remain required.

### current11 exact configuration projection and validated derived mesh separation (offline only)

The single executed current10 activity remains terminal CLI FAIL_OR_INCOMPLETE. Its first failure was configuration comparison added_keys=[mesh_volume_observation], with no missing or changed physical keys and an independently matching built/reopened mesh hash. The same run's zero-solve template, actual arrays/domain volumes, all 44 submitted/observed Worker request pairs and nine successful durable jobs/operations are preserved. They do not change the CLI result, establish setup acceptance, or establish cure-history science. Current11 is a separate private offline source candidate; it has no native admission or engine birth.

The root cause was asymmetric projection: the configuration helper excluded the derived observation from its build-side expected fields but compared against the complete reopen-side dictionary. Current11 removes that one derived field on both sides only after a strict, separately bound native mesh witness has passed. All 21 actual physical configuration fields remain mandatory and exact; every unknown, missing, added or changed physical field fails. Missing, extra or unverifiable derived metadata cannot be filtered out or manufactured to complete the build side.

The internal mesh artifact V2 preserves the actual managed native operation response, original native domain/area readbacks, complete actual raw mesh, recomputed proof, and exact project/ModelRef/revision/stage/Worker epoch binding. Worker epoch is observed from matching pre/post managed Worker audits (instance_id, generation and server), not inferred from the ModelRef generation. The two actual artifact copies must match regular-file identity, size, SHA and bytes. The origin response must bind the same exact project/ModelRef/revision and the same Worker epoch, and its native raw mesh/domain rows must equal the artifact inputs. The existing triangle/domain/hash/units/1e-6 volume proof is recomputed and compared exactly. Build and reopen must retain project, model tag/session/server, one sequential Worker epoch, one newly adopted model generation, identical raw mesh hash, triangle assignments and domain volumes. Only then may the derived stage/path metadata be separated from physical configuration comparison. No numerical, material, geometry, solver, model save/reopen, Equation View or history gate is relaxed.

Regression fixtures are exact UTF-8 bytes extracted from preserved current10 immutable SQLite operations.result: build operation 3b18a33b-f053-48cb-8ebb-48f6118c2da5 (SHA 5f23cadc69408dda8e2b8f32921b5d161251413fadcb7963e02542f42827c7a0) and reopened readback operation 11495efe-80c2-40b9-ab20-42ba0a3e4dc1 (SHA 42643eaa45f2258269af669bfed563844db4d29f314d86f7cfaabe3fe684f0c6). Unlike the postprocessed view, those raw rows retain native_mesh_raw and original NATIVE_GEOMETRY_MEASURE_GETVOLUME_2D area evidence. Tests call the frozen original current10 production comparer to reproduce its actual added-key failure, then the current11 production attachment/verification/comparer. They reject missing/extra derived fields, changed model/Worker epoch/revision/stage/hash/domain values, missing or extra physical fields even when an unknown field is added on both sides, and re-signed artifact mutations that disagree with native origin or recomputed proof. This is offline regression evidence, not a new COMSOL observation or native acceptance.

The source for Java fixture, material/physics configuration and numerical mesh integration is unchanged from current10. Initial undeformed mesh volume remains separate from actual gel-shape UV→bake→cool history and sensitivity acceptance. A new current11 native activity requires a new exact Root source/packet review and fresh admission; current10 and all prior UNKNOWN/FAIL activities are never replayed.

## Coupled zero-solve successor02 source candidate

This private successor implements the Root decision `7201fb134afa884c8de2217b892d361bfda6005ffa6124c1e432423975047911`. The independent proposed setup contract is one Server and one sequential Worker, 1800 seconds from actual Server birth including a 600-second cleanup reserve. The existing v1 setup defaults remain 900 seconds including 45 seconds cleanup. The coupled budget requires the complete frozen candidate and a separate exact Root admission.

The new unit builds a fresh cure `build_v2` configuration with `mu_scale=1.0`, reads actual geometric-nonlinearity properties on all three Time solvers, captures the complete fresh Equation View, saves an MPH, loads/adopts it in the same managed project, and repeats the strict physical/v2/mesh/Equation View checks. It then uses the existing managed static runner and route fence to build, save and reopen the flat and step baseline configurations serially. There are zero Study, solver or Phase Initialization submissions throughout this unit.

The first actual cure-v2 Java result must be byte-equivalent to its terminal OperationStore result before it can produce an internal Worker epoch capability. Every subsequent actual Java result is checked against its own exact managed project/ModelRef/revision origin and actual before/after Worker audit. Worker instance, Worker generation and server epoch must remain equal to the fresh cure witness. Public JSON cannot supply this capability, and ModelRef generation is not treated as Worker generation. Missing audit, origin mismatch, Worker change, any failed/unknown call or insufficient cleanup budget stops all later routes and cases while retaining the actual response and managed job state.

Historical v1 evidence authenticates only its original setup and historical spatial Cauchy rows. Four historical spatial Cauchy roles retain the actual raw `N/m^2` unit and separate requested `Pa` intent. A fresh nonlinear-v2 Equation View is required before any evaluated stress mapping claim. Full-DOF history remains unverified. The setup models are unsolved configurations, not accepted stable gel shapes or UV/bake/cool histories.

The original 14 static sensitivity slots and full native stability, volume, wetting/profile and convergence criteria remain required. The stable-gel-shape adapter is a typed requirements contract; missing native shape/history evidence is not replaced by an analytic shape. Numerical, physical and experimental-calibration acceptance remain separate and are not granted by this source candidate or by setup-only success.

## Coupled03 independent-review remediation

Coupled02 independent review `a86d71184ea6d46cb2d58cb3ec5cdc90a4ae55129cc80e0bae3dca66738534cc` required two source corrections. Coupled03 resolves the actual Store `runtime_binding.kind` through the existing Controller authority for registered and default backends. An internal original receiver anchor is captured before the first actual cure Java call. The initial capability must retain that exact backend, Worker object and connection identity. Native raw server fields bind to the backend endpoint; ModelRef compatibility server fields bind to the original backend compatibility identity, recomputed using the existing implementation inputs. Original raw values and ModelRef/Worker generation distinctions are preserved.

One internal owner starts the existing sampled RSS monitor at the original Server Popen birth and covers listener, Worker compilation/startup/connect/version, cure and both static cases. The original Worker birth is bound to that owner. The same 1800-second deadline and 600-second cleanup reserve cap native/direct/managed waits, including queue, execution and RPC waits; owned descendants are included in the sampled observation. A sampled RSS fence is not a hard memory cap. Old v1 optional paths keep their 900/45 defaults and the original fourteen scientific sensitivity slots remain required.

On the first failed or unknown effect, no further model/numerical route may be dispatched. Original compiler/Server/Worker Popen handles are retained. The CLI's production finally boundary must observe and reap those same handles and verify original private process-group absence before returning. If ownership remains uncertain, the same CLI/tool remains in `UNKNOWN_RETAINED_OWNER_OBSERVE_ONLY`, including beyond the admission budget; no new IPC, takeover, retry, synthetic cancellation or automatic compiler TERM/KILL is introduced. Exceeding the budget is permanently non-PASS. The existing authorized exact Server/Worker cleanup remains subject to its original identity and ledger gates.


### coupled04 consolidated lifecycle remediation (2026-10-13)

Independent coupled03 review aeb97b5726bb985253bb5a107f620574c500669266ff9ccaec88ce96d91d31d0 identified four source residuals despite the preserved 730 scoped tests: Worker log-close before registration; diagnostic/daemonNone ledger exceptions escaping retention; wall rollback extending admission; retired compiler groups missing from sampled RSS. All historical03 files and failures remain immutable.

Coupled04 registers the original Worker Popen before ancillary close/probe/record. The original handle remains strong even on identity binding failure. An unavoidable outer finally spans all prior cleanup/ledger/summary writes; DiagnosticErrors retains first cause and secondary I/O failures while the same CLI observes original handles. Diagnostic failures can never convert actual UNKNOWN ownership into closure or PASS.

The single conservative monotonic start is captured before the earliest Server Popen, never reset after birth, and owns all startup/cure/flat/step effect admission and dynamic waits. Raw wall birth and elapsed remain evidence facts. Existing exact_owned_cleanup uses only the same internal owner to cap each original OS probe, identity read, wait, sleep and authorized signal precondition; defaults None preserve other lanes. Budget exhaustion stops all work/cleanup effects. Independent short bounded observe-only reconciliation after budget expiry holds the original owner until exact same child reaping and original PGID/SID absence, permanently nonPASS, without signal/retry/takeover.

Sampled RSS retains original compiler PGID/SID after terminal leader retirement. Fresh full PS plus native getsid/birth witnesses cover reparented descendants and descendants in new PGIDs within the original session. Group/session identity uncertainty or >=6GiB blocks subsequent routes; a retired group is released from RSS only after fresh complete absence. This is sampled admission coverage, not a hard memory quota.

Production controls exercise actual Worker.start, owner/monitor/finalizer, actual runner.run with daemonNone/ledgerIO failure, and actual exact-owned cleanup delayed OS probe followed by postcap ownership closure. Only prerequisite evidence/SDK/OS/native birth boundaries are software fixtures; source admission and native efficacy remain separate. Existing default cleanup regression uses actual endpoint/client parsers and typed lsof fixture responses. Original v1 900/45 and fourteen scientific slots/physics criteria remain unchanged. All Java sources and six exact major55 classes are inherited without rerunning compilation.


### Coupled05 nested probe propagation (source/software only)

Coupled05 inherits coupled04's exact original Server/Worker ownership, monotonic
1800-second unit deadline and 600-second cleanup reserve. The original typed
internal owner reaches each actual macOS ps/lsof call in listener identity,
start_worker isolation, main Worker identity, and ManagedBackend G2 verification.
Each individual SDK call obtains a fresh remaining cap immediately before
subprocess.run; the previous probe's timeout is never reused for a later probe.
A late return or timeout fences all next routes and retains original handles.

After cleanup or expiry, postcleanup inventory uses that original owner only for
pure read-only reconciliation. Each ps/lsof call is independently bounded to at
most five seconds; no workload budget is reopened and no signal, native dispatch,
PASS, or synthetic closure follows observation. Legacy owner=None semantics and
v1 900/45 defaults remain. Java and the original fourteen science slots are
unchanged. This candidate has no native admission, native execution, CAS update,
or scientific acceptance. Coupled04's review residual07 and the intermediate
software fixture failures remain preserved; 03–06 are inherited scoped closure.
