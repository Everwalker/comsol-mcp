# Independent W22 final decision

**Decision: W22_SCOPED_APPROVED. No unresolved A1/A2 findings within W22 and its directly affected requirements. Stop at W22; W23 is not authorized by this decision.**

Reviewer is the actual host-created independent agent context `/root/reviewer`. Main Agent developed the production change. Reviewer read the frozen contract before native execution, agreed the benchmark once, implemented an independent three-dimensional spectral reference, reviewed source/diffs and raw MCP responses, and personally dispatched the final Windows native fresh/reopen runs. This is not a relabelled Main self-review.

Approved production source: `44b8e1931b47da564edb600d2127b28859d07b36`. Final ordinary-install wheel SHA-256: `c1d82034e19e1f0fd897f3ec76911a3ccad229ed35f613148633bfb56f923ae6`. The final wheel build's production-file hashes independently match the canonical source. Later review records and evidence are append-only documentary additions, not new physics or engine code. Frozen benchmark SHA-256 remains `d94322296feff1cd7678e345bf798616a704c638c8c108d3c8f78f4128f4b1fe`; no numerical threshold was loosened after results.

## Fixed deliverables

| Delivery | Windows 6.3 build 290 | Windows 6.4 build 293 | Evidence and scope |
|---|---|---|---|
| D1 static non-axisymmetric source | PASS | PASS | Actual 18-active/19-position source; independent analytic and delivered-linear-input references; same-radius angular probes, m/mm equivalence, zero extrapolation, units and source hashes. Reviewer fresh runs independently repeated these checks. |
| D2 material, cooling and thermal budget | PASS | PASS | Actual circular ROI integration operators on a partitioned square slab; mean/std/extrema are native selection quantities, not sparse equal-weight averages. Native top/bottom/domain integrals retain transient storage. Source, mean and std meet independent reference tolerances; geometry/material/heat-flow definitions and units were read back after reopen. |
| D3 L scan and limited power search | PASS | PASS | All 27 frozen candidates across three distances were independently checked from raw evidence. Same-source cache reuses with zero solve; changed bytes at the same path cause a real solve and changed absorbed power. Corrected content identity was reviewed narrowly. |
| D4 candidate and impossible target | PASS | PASS | Reviewer freshly solved the independently selected returned best on each version through installed public MCP. The 20 W absorbed-ROI target exceeds the conservative 10.8 W upper bound. No global optimum or globally impossible ordinary search claim is made. |
| D5 native plots, data, MPH and reopen | PASS | PASS | Original COMSOL source/temperature PNGs were visually inspected and match the non-axisymmetric source/thermal pattern. Raw bindings identify the solution and 60 s output. Reviewer directly reopened each Main delivery MPH from a new path using a new same-version Worker, with zero solves, and compared stored results/settings before and after source rebinding. |
| D6 same benchmark, two versions, independent review | PASS | PASS | Both versions meet the same frozen independent references. Actual independent fresh and new-Worker reopen completed. W21 scoped approval is inherited except the targeted cache change; 31 affected software regressions and native cache controls support that change. |

## Independent native runs and comparisons

Reviewer personally launched `reviewer63_02` and `reviewer64_02`, each with exactly one fresh solve of `L=0.03 m, p0=0.6 W, p1=0.65 W`. The outer-ring per-active-emitter power is `(12.45-0.6-6*0.65)/11 = 0.7227272727272727 W`. All emitters satisfy 0–1 W, total emitted power is 12.45 W, and ROI mean temperature rise exceeds 3 K.

| Native fresh readback | 6.3 | 6.4 |
|---|---:|---:|
| ROI mean temperature rise at 60 s, K | 13.510891534847964 | 13.510891517906527 |
| ROI temperature standard deviation, K | 2.582998494289462 | 2.582998493208295 |
| Native study duration, s | 61.820 | 57.722 |

Reviewer then personally launched `reviewer_reopen63_01` and `reviewer_reopen64_01`, directly loading copies of Main's `delivery63_01/project/w22_final.mph` and `delivery64_01/project/w22_final.mph`. Both finished `FRESH_WORKER_REOPEN_COMPLETED`, `solve_count=0`. Main delivery MPH SHA-256 values preserved on copy:

- 6.3: `8640ca262fdabc4a63f1359139d36afe25f6215b141766a1c2cad3930b3e796d`.
- 6.4: `d62f90b1dd46d8144bc312985892317d7e03a75e7ac7039717253b7fcb3a61fb`.

Stored fields were compared with explicit roundoff bounds (`rtol=1e-10`, `atol=1e-12`), not image byte equality. Reopened readbacks include physical constants, geometry, ROI integration selection/expressions, parameters and units, source filenames rebound to each new directory, linear interpolation/zero extrapolation, and dataset-to-solution binding. Fresh scalar units and the actual stored time axis were separately checked.

Per version, `COMBINED63_REVIEW.json` / `COMBINED64_REVIEW.json` contain 886 passing checks covering original search + repaired controls + final delivery. `FRESH_REOPEN63_REVIEW.json` / `FRESH_REOPEN64_REVIEW.json` contain 122 passing checks each, including exact raw-response correspondence, independent angular/upper-bound checks, independent spectral comparisons, and fresh/reopen readbacks. These are assertion counts, not additional native solves.

Independent reference caches contain coarse/fine solutions for every frozen candidate. Maximum relative resolution change is 0.00556% for analytic input and 0.00942% for actual delivered linear-input tables, below the frozen 0.2%. Maximum analytic/linear reference difference is 0.06365%. Each version is compared against those references; agreement between COMSOL versions alone is not the correctness criterion.

## Failure preservation, budgets and inheritance

The original `full63_01` / `full64_01` remain `FAILED`: each finished all 27 candidates and failed on a same-source cache miss caused by COMSOL's random exported interpolation paths. Independent diagnostic normalization grouped 55 of 56 actual exported configurations identically when only those paths were ignored for diagnosis. Production does not ignore input identity: it hashes actual exported table bytes, retains the live binding and all other configuration, and makes unreadable imports non-reusable.

The subsequent `controls63_01` / `controls64_01` remain `FAILED` at output preparation, after their cache/mutation/fresh controls completed. COMSOL-generated Analytic functions lacked a `filename` property; the corrected reader handles only actual Interpolation functions. Zero-solve diagnostics confirmed the cause. `delivery63_01` / `delivery64_01` complete that output/save step. Approval combines explicit completed evidence across these runs; no failed status is overwritten or promoted wholesale.

Acceptance search: 27 unique candidates per version. Post-scan Main control/delivery solves: five per version (one failed repeat, three repaired controls, one delivery), within the frozen allowance of six. Reviewer: one fresh solve per version, within its separate allowance of two; direct Main-MPH reopen uses zero solves. Earlier smoke/debug records are retained separately and are not claimed as additional search candidates or as Reviewer solves. The first Reviewer `_01` dispatch attempts were killed when their SSH launcher returned, before any run directory or native solve existed; their empty logs/dispatch receipts are retained as `NOT_ENTERED_RUNNER_ZERO_SOLVES`. Keeping the SSH process alive corrected the launch mechanism without touching a live solver.

The 27-case scientific results are inherited across the inspected cache and source-validation-only changes: frozen generator values, geometry, physics and solver were unchanged. Mac/Windows regeneration numerical differences are documented rather than falsely claimed byte-identical. W20/W21 unmodified requirements are inherited; this review does not start a global re-audit.

For explicit optical accounting at the selected best, emitted 12.45 W minus native slab incident 12.407609723 W gives outgoing/missed optical power about 0.042390277 W. Incident minus absorbed gives unabsorbed power about 4.963043889 W; slab absorbed minus ROI absorbed gives absorption outside ROI about 1.113600324 W. These are derived from actual native integrals, with alpha applied once. Thermal outward cooling and transient storage remain separate terms.

## Evidence and delivery boundary

All paths below are relative to repository root:

- `evidence/w22_vcsel/windows/full{63,64}_01/`: original search raw requests/responses and retained failures.
- `evidence/w22_vcsel/windows/controls{63,64}_01/`: real cache/source mutation controls and retained output-reader failures.
- `evidence/w22_vcsel/windows/delivery{63,64}_01/`: final Main native plots, data and saved MPH.
- `evidence/w22_vcsel/windows/reviewer{63,64}_02/`: actual independent fresh native evidence.
- `evidence/w22_vcsel/windows/reviewer_reopen{63,64}_01/`: actual independent direct Main-MPH reopen evidence.
- `evidence/w22_vcsel/windows/reviewer_dispatch/`: independent launch commands and preserved pre-run failed dispatches.
- `docs/handoff_w22_vcsel/review/`: independent algorithms, reference caches and machine-readable check results named above.

The complete local source archive `../delivery/W22_SOURCE_44b8e19.tar.gz` was independently SHA-256 checked as `01c574b0482c7b6039c04f48ec0bce4a2110ef1f344269a0a7dce3c12164f96f`. The contract permits this reviewed source snapshot when ordinary remote synchronization is blocked. No GitHub push is claimed.

Physical calibration remains **UNVERIFIED**: these synthetic optical/material assumptions do not certify a measured device or real high-temperature wafer. Mac native W22, GUI and cloud-host acceptance are **UNVERIFIED**. No W23/W24/GUI expansion was performed. These declared scope limits do not block the six frozen W22 deliveries.
