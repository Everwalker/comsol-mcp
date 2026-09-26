# W22 VCSEL native delivery

Status: **W22_SCOPED_APPROVED**, independently signed in `review/W22_FINAL_REVIEW.md`. Both versions pass D1–D6 with no unresolved A1/A2. Each version passed886 combined evidence checks and122 independent fresh/reopen checks. W21 scoped approval is inherited. W23 is excluded.

## Benchmark and result

This is the frozen synthetic non-axisymmetric VCSEL benchmark, not calibrated device data. The 19-position array has 18 active emitters; the disabled outer-ring emitter retains angular asymmetry. A normalized 2D Gaussian table supplies incident W/m² at each distance; absorption is applied once at α=0.6. There is no table renormalization or fitted thermal response.

The native model is a 40 × 40 × 1 mm slab (k=20 W/(m·K), ρ=3000 kg/m³, Cp=700 J/(kg·K)), initial/ambient 300 K, with top absorbed flux, bottom h=500 W/(m²·K), insulated sides, and circular top ROI radius15 mm. Results are stored at 0,1,5,20,60 s. Geometry, material, boundary expressions, parameter values/units, source-function settings and dataset/solution bindings are read from the actual model.

Each Windows version evaluated all 27 frozen candidates across L=10,20,30 mm. The best verified feasible candidate among these 27 is:

| Quantity | Result |
|---|---:|
| L | 30 mm |
| Center emitter | 0.6 W |
| First ring, each of6 | 0.65 W |
| Outer ring, each of11 active | 0.7227272727 W |
| Total emitted | 12.45 W |
| ROI mean temperature at60 s | ≈313.510892 K |
| ROI temperature standard deviation | ≈2.582998 K |
| Temperature-rise CV | ≈0.191178982 |
| CV reduction versus frozen20 mm baseline | ≈15.1920% |
| CV reduction versus30 mm baseline powers | ≈13.4960% |
| Incident power on slab | ≈12.407610 W |
| Absorbed power on slab | ≈7.444566 W |
| Absorbed power inside ROI | ≈6.330966 W |
| Optical power not intercepted | ≈0.042390 W |
| Unabsorbed incident power | ≈4.963044 W |
| Absorbed outside ROI | ≈1.113600 W |

Every emitter remains within0–1 W and mean ROI rise exceeds3 K. This is the best of the bounded candidate set; no global optimum is claimed. The20 W absorbed-ROI target is mathematically impossible because even perfect interception gives at most0.6×18×1=10.8 W. This proof is separate from `NO_FEASIBLE_FOUND` under a finite search budget.

## Evidence and failure history

Both `full63_01` and `full64_01` completed27 cases but terminated `FAILED` at the same-source reuse assertion. Independent Reviewer audited each set against raw MCP responses and separately implemented 3D spectral references:844 case-only checks passed per version. The original failed records remain unchanged.

The cause was COMSOL Java export extracting imported interpolation tables to new random temporary paths on every export. The targeted cache correction hashes actual extracted bytes, retains actual live filename bindings and all other exported configuration, and keeps declared input artifact hashes. Unreadable imports receive non-reusable identities. It does not discard arbitrary paths or replace native results.31 affected software regressions passed.

`controls63_01`/`controls64_01` demonstrate one same-source cache hit with zero solves, changed bytes at the same path causing zero cache hits and one real solve with changed absorbed power, and restoration plus a fresh best solve matching the original result. These runs then failed at an output-setting reader that queried `filename` on COMSOL-generated Analytic functions. Zero-solve diagnostics identified the exact property; the reader now handles the9 actual Interpolation sources. Both original failures are preserved.

`delivery63_01`/`delivery64_01` independently solve the selected best once and complete raw data sampling, actual settings readback, native source/temperature rendering, and MPH saving. Each version used5 post-scan main solves total (one failed repeat, three controls, one delivery), within the frozen allowance6. Reviewer independently completed one fresh solve per version and directly reopened each Main delivery MPH in a new path/new Worker with zero solves; all frozen comparisons passed. Diagnostic reopens do not count as scientific recomputation.

The source wheel used for27-case scans has SHA-256 `673e3623c94780e92492401916e83d24484597bdf1ccf6c1ef63565ec3378571`. The final wheel is `c1d82034e19e1f0fd897f3ec76911a3ccad229ed35f613148633bfb56f923ae6`. Its scoped differences are source-input validation and content-bound imported-file cache identity; the frozen physical generator, geometry and solver remain unchanged. Cross-platform regeneration is numerically equivalent (maximum observed relative table difference2.16e-14), not byte-identical because line endings/libm differ. Both Windows versions use the same wheel and source files.

## Deliverables

Paths below are relative to the repository root:

- `evidence/w22_vcsel/windows/full{63,64}_01/`: raw27-case summaries/transcripts, configuration exports, source tables, derived `case_table.csv` and `reviewed_comparison.json`.
- `evidence/w22_vcsel/windows/controls{63,64}_01/`: raw cache/source-change/fresh controls, retaining original failure status.
- `evidence/w22_vcsel/windows/delivery{63,64}_01/project/`: `w22_final.mph`, `sourceplot.png`, `temperatureplot.png`, frozen spec and external source files. PNGs are original COMSOL renders; plot expressions/time/dataset are bound by raw settings and data, not inferred from color.
- `evidence/w22_vcsel/delivery/`: final ordinary-install wheel, source build receipts, relevant test and clean public-stdio installation evidence.
- `docs/handoff_w22_vcsel/review/`: independent implementations, converged reference caches, audit and final review records.
- `docs/handoff_w22_vcsel/REPRODUCE.md`: source build, ordinary installation, isolated Windows native execution and fresh Worker reopen commands.

The frozen benchmark SHA-256 remains `d94322296feff1cd7678e345bf798616a704c638c8c108d3c8f78f4128f4b1fe`. Hash-verified imports append evidence and never overwrite existing files. Private runtime credentials and control stores are excluded.

## Scope limits and source publication

Physical calibration is **UNVERIFIED**. No measured device optical/thermal data, Mac native W22, GUI or cloud Host acceptance is claimed. W21 is inherited except the directly affected cache regression. W23 is not started.

GitHub read-only sync checks failed with TLS/connection resets. No remote push is claimed and no network/security settings were changed. The contract-permitted local reviewed complete source snapshot is delivered with a hash receipt; repository source and append-only evidence remain available locally.
