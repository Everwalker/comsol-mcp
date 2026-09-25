# W21 execution closure

Status: W21_SCOPED_APPROVED. Independent final decision: `review/REVIEWER_FINAL_W21_SCOPED.md`. W22 has not been started.

Source commit: `07a44329e96d922a0955e944a8a65ba732894278`. Final wheel SHA-256: `8511dbc550792fbe538646897c951649a49de668030e66bd9306868801b94e5d`. Original workpack and frozen contract passed integrity checks; the original 16 cases / 27 targets and numerical thresholds were not changed.

## Frozen five deliveries

| Requirement | Windows 6.3 build 290 | Windows 6.4 build 293 | Evidence |
|---|---|---|---|
| Parameter cases and real two-parameter transient scan | 4 actual cases | 4 actual cases | `closure_w21_63_final`, `closure_w21_64_final`: parameter readback, native solution/time index tables, W17 fields and registered W20 results |
| Budget and reuse | repeat 4 cache hits / 0 computations; two changed-input runs each 1 computed + next NOT_RUN | same | same runs: original producers, input identities, no solve in NOT_RUN case |
| Bounded real optimization | 9 native computations; feasible best returned; failed candidate has no best | same | same runs: all candidates and constraints, raw actual objectives; independent fresh returned-candidate solves passed on both versions |
| Native stage transfer | initial error 7.2380538e-06 K; continuation error 0.0019840684 K | initial error 7.23805329e-06 K; continuation error 0.00198366074 K | actual source terminal/target initial fields, settings readback, stored continued solution; limits .001 K / .03 K |
| Same-source dual-version public delivery | actual 6.3.0.290 | actual 6.4.0.293 | wheel provenance + raw stdio, saved MPH SHA, inherited independently executed same-version fresh Worker reopens |

The four-case scan maxima are 0.00153145436 K and 0.00153129584 K, below the preregistered .03 K limit. Both versions return the bounded candidate `k=390 W/(m*K)`, `rhoCp=3400000 J/(m^3*K)`. Actual objective values (absolute middle-point difference from 304 K at 2 s) are 0.0411699842881 K and 0.041170471207 K. This is the best verified feasible candidate found by the finite search, not a global optimum claim.

## Required dependency corrections and review

- T06/T09/T12: backend-owned observations, exact live binding and successful producers; callers cannot replace values; numerical verification never promotes physical validation. See `W20_CORRECTION.md` and independent Round1/2 reports.
- T11: each version has three actual grids and three actual solver-tolerance settings, with element/DOF readback, fixed B2 stored output times and all nine errors below the original .1 K threshold. Main runs `closure_t11_63_01` and `closure_t11_64_03` use backend registered references. Fixed caller lists, duplicate refs/settings and error overrides are rejected without freezing later valid calls. Independent final T11 review passed on both versions: 28 negative calls, clean dirty state, successful positive recovery and twelve independently recomputed field-error sets.
- T14: `reviewer63_reopen01` and `reviewer64_reopen01` independently started fresh same-version server/Worker processes, loaded exact saved SHA, sampled without recomputing, issued new observation references and matched saved fields exactly. Their missing-file/missing-solution negatives passed. The final T11-only source delta does not alter the reopened range-validation path; inherited scope is stated in the review.
- Original failed runs and old signoffs remain intact. This appendix corrects the effective scope rather than rewriting historical results. No full W20 re-audit was added.

Final software regressions: 84 Python checks passed; Java Worker 30 passed / 1 skipped. These software checks are separate from the real Windows results. Final independent Reviewer `/root/reviewer_final` signed W21_SCOPED_APPROVED with no open A1/A2.

## Delivery and boundaries

- Current source: this repository at the source commit above.
- Apply-tested code patch, wheel and receipt: `evidence/w21_closure/delivery/`.
- Raw requests/results, engine identities, Java fixtures, parameter snapshots, operation/artifact records and synthetic MPH files: `evidence/w21_closure/windows/`. Exports were SHA-verified and checked for sensitive credential fields before append-only import.
- Restore instructions: `RESTORE_CURRENT.md`; machine-readable measured facts: `MAIN_NATIVE_FACTS.json`; history: `PROGRESS.md`.
- Deferred items: `DEFERRED_BACKLOG.md`. Physical calibration remains UNVERIFIED. T047 generic transfer only is covered; full gel/UV/stress physics remains NOT_RUN for W24. Mac native W21 and additional platforms are not newly certified.
- Source/evidence publication follows the standing non-force synchronization instruction in repository AGENTS.md; the final remote SHA is checked separately after push. W21 is complete and stopped; no W22 work.
