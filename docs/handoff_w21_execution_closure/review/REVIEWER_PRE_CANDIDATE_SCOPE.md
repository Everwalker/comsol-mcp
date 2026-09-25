# Independent reviewer: limited pre-candidate review

Reviewer Host context: `/root/reviewer`, independently dispatched by `/root` in this task. Date: 2026-09-25. This is not a candidate approval and not native execution evidence.

## W21 benchmark accepted before execution

Main proposed L=.05 m, Tb=300 K, amplitude 10 K, k=[380,400] W/(m K), rhoCp=[3.4e6,3.8e6] J/(m³ K); times [0,.5,1,1.5,2] s and x=[L/4,L/2,3L/4]; four sweep cases. Bounded optimization: 3x3 grid, at most nine computed candidates, objective abs(T_mid(2)-304), constraint 300<=T_mid<=310. Absolute numerical and continuation-to-4s tolerance .03 K; stage initial-state tolerance .001 K. These are reasonable for the small smooth diffusion problem and meet the original W21 scope. They do not amend W20 B1/B2/B3 or its tolerances. Grid search need not prove a global optimum. All observed values must originate from the actual engine.

## Limited evidence applicability findings

Read source before relying on old verdict: `tools/run_g3_9_acceptance.py`, then both `evidence/g3_10_windows_w20/win{63,64}/mcp_transcript_C{12,13,19}.json`, corresponding observation records, and old `docs/handoff_w20_closure_then_w21/INDEPENDENT_REVIEW_VERDICT.md`.

### R-T11 (A1)

- Frozen requirement: T11, each version three actual meshes and three time accuracies, actual settings/element or DOF/solutions, traceable producers; fixed lists rejected.
- Fact: runner lines 1222-1252 supplies fixed mesh errors [.065,.028,.009], DOFs [240,960,3840], and time errors [.058,.024,.008]. Both versions' C12/C13 raw requests reproduce those lists and call only validate.convergence. The associated producer is the validator, not a three-level engine computation.
- Impact: old records establish validation of caller arrays, not NATIVE_CONVERGENCE. Old verdict section III.4 is not supported by those records.
- Minimal closure: run original B3 on each Windows version using B2 (L=1, rho=Cp=k=1 and original sampling) or its explicitly permitted preregistered sine-source steady fixture. Three real meshes with fixed fine temporal strategy; three real solver temporal strategies with fixed fine mesh and unchanged output tlist. Read actual mesh/DOF and settings, preserve actual case/producer observations, final .1 K target; separate trend/order/platform from target acceptance. Reject fixed unregistered lists/repeated mesh/fabricated errors. Reuse the new W21 executor, not the new W21 numerical benchmark in place of frozen B2.

### R-T14 (A1)

- Frozen requirement: T14 same-SHA saved model, fresh same-version Worker, no solve, new ObservationRef and sampled values, wrong-file/missing-solution negatives.
- Fact: runner lines 1329-1350 calls model_load in the existing session then validate.solution(criteria.values=[t2]). Both versions' C19 raw transcripts contain that exact two-call sequence, with old 325 K values supplied by caller; no result sample is present.
- Impact: files/load responses remain useful historical artifacts, but are not evidence of new Worker resampling. Old verdict section III.5 overstates those records.
- Minimal closure: W21 save/reopen fixture may satisfy this by recording file SHA, actual old/new Worker identity, same runtime version, successful load without recompute, new backend-registered observation samples compared to original, plus missing-solution/wrong-file negative checks.

### T15 / T16 disposition

The runner's RecordingSendStream/RecordingReceiveStream wrap actual stdio at lines 991-993. Do not discard unrelated legacy transcripts merely because C12/C13/C19 have semantic deficiencies. Preserve old verdict and append correction for affected claims, map new evidence to original T11/T14 and directly affected T06/T09/T12. New normal and negative calls must retain actual raw stdio messages and model/runtime/solution binding. T16 cannot be inherited for changed code: final candidate must identify real commit plus necessary working-tree list and receive this actual independent review. No new signature platform is required. Present status is NOT_RUN for candidate review and Windows reviewer retests; no W21 approval.

## Reviewer retest scope

For each supported Windows version: actual public complete chain and original relevant negatives (wrong index/time, stale cache or changed inputs/version, repeated request/no second dispatch, budget limit, failed/missing/nonfinite candidate exclusion, actual stage initial state/continuation, fresh reopen). Independently create unregistered ObservationRef and caller-value replacement counterexamples for T06; numerical success must remain physical UNVERIFIED for T09/T12. Only A1/A2 blocks. Nonblocking refinements defer. No W22.

## B3 method preregistration review (before revised native run)

Main reports the initial automatic hauto=[7,5,3] mesh study produced DOF=[291,942,1979] and nine-point maximum errors [.000503233,.000802004,.00007808896] K. These values are parent-reported, not independently remeasured at this point. Preserve the original measurements and actual trend-check outcome. All three satisfy the original .1 K error target; nonmonotonic pointwise error must be reported separately from target attainment, as frozen B3 requires. Do not create a new blanket monotonicity requirement.

Main proposes revised controlled spatial discretization: same original 3D B2 physics and cross-section, uniform swept axial mesh with N=[8,16,32] cells, fixed end-face mesh and rtol=1e-8. Temporal study keeps N=32 fixed and uses rtol=[1e-3,1e-5,1e-7]. Both retain original x=[.25,.5,.75], stored output times [0,.01,.03,.1], original nine tested nonzero-time points, and .1 K target. Reviewer confirms before execution that this is allowed method refinement: the frozen B3 never fixed a mesh generator or N list. It does not amend original physics, targets, or tolerance. Keep original records; explain changed method and observed convergence/order/platform rather than select only a pleasing trend. No user scope change is required.
