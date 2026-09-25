# Independent final W21 review — 2026-09-25

**Verdict: W21_SCOPED_APPROVED.** Reviewer `/root/reviewer_final` independently executed the final native follow-up. No remaining A1/A2 finding in the frozen W21 scope. This approval stops at W21; it is not whole-G5, all-platform, or W24 domain-physics acceptance.

Candidate production commit: `07a44329e96d922a0955e944a8a65ba732894278`.
Candidate wheel SHA-256: `8511dbc550792fbe538646897c951649a49de668030e66bd9306868801b94e5d`.
All 73 receipt-listed source hashes match. The installed Windows convergence store, W20 validation, and domain-outcome modules independently match those source hashes. Each fresh native run records the same wheel hash, installed package location, and executed script hash. No production files were edited by this reviewer.

## Closed remaining finding: R2-T11-PROVENANCE

I reviewed the backend registration/resolution and public validation paths, then copied the existing native launcher and added independent counterexamples. I ran separate owned public-MCP sessions on actual Windows COMSOL 6.4 build 293 and 6.3 build 290, with unchanged B2 threshold 0.1 K, mesh levels 8/16/32, tolerance levels 1e-3/1e-5/1e-7, and stored times 0/.01/.03/.1. The three mesh levels have actual DOF 375/711/1383. The time sequence uses fixed mesh/DOF and three actual solver tolerances.

Both runs completed successfully. I separately recomputed all twelve nine-point error sets from returned temperature fields. Every value matches the registered backend error; the maximum is 0.005506084 K, below 0.1 K. Registered cases retain producer, observation, model, actual mesh/solver settings and version provenance.

Seven negative categories were executed for each mesh/time sequence on both versions: unregistered fabricated arrays, repeated reference, caller error override, caller settings override, missing ID, tampered hash, and a separately registered observation with repeated actual settings. All 28 calls refused numerical PASS. Every refusal retained `dirty=false` and physical UNVERIFIED; a valid registered convergence call after the negatives passed again without revision poisoning. Fixed arrays remain EXTERNAL_DATA_ONLY and cannot validate the model. This closes the original T11 provenance gap, including the known fixed-array counterexample.

Evidence: `evidence/w21_closure/windows/reviewer_t11_final_63/` and `reviewer_t11_final_64/` contain public transcripts, summaries, executed scripts, origins and saved MPH files. `REVIEWER_T11_FINAL_RECOMPUTE.json` and `reviewer_t11_recompute.py` provide the independent recomputation. All saved model hashes were checked after transfer.

## Independent returned-candidate recomputation

In two further fresh owned public-MCP runs I rebuilt the W21 fixture and solved the final reported best candidate, k=390 W/(m K), rhoCp=3400000 J/(m3 K), as a single-case sweep. This was a new actual solve in each version, not a cache lookup or arithmetic reuse of the optimizer output.

| Version | Independent T_mid, K | Independent objective, K | Maximum oracle error, K |
|---|---:|---:|---:|
| 6.3 build 290 | 304.0411699842873 | 0.0411699842873 | 0.0014934794607 |
| 6.4 build 293 | 304.0411704712080 | 0.0411704712080 | 0.0014929925400 |

Both satisfy the original 300–310 K constraint and .03 K oracle criterion. Objectives agree with the original reported candidates to about 1e-12 K. Evidence is under `reviewer_best_final_63/` and `reviewer_best_final_64/`; the comparison is in `REVIEWER_BEST_FINAL_COMPARISON.json`. This confirms a verified feasible bounded-search candidate, not a global optimum.

## Frozen scope disposition

- W21-1 real parameter/transient cases: prior independent native Round 1/2 evidence retained for unchanged behavior; final main native 6.3/6.4 summaries independently inspected and both complete.
- W21-2 cache and budget: R1-BUDGET-REVISION was independently closed in Round 2, including successive input changes and failed-candidate exclusion; final summaries retain bounded execution and cache behavior. T11 negative/refusal changes have fresh dirty-state and recovery regression above.
- W21-3 bounded optimization: prior native evidence plus the fresh independent best-candidate solves above.
- W21-4 stage transfer: prior independent actual initial-field/continuation tests retained; final main results have continuation errors 0.001984069/0.001983661 K. This is only the generic T047 transfer sub-item; full domain T047 remains PARTIAL/NOT_RUN for W24.
- W21-5 same-source dual-version public entry and saved results: current independent runs verify actual builds and public dispatch. Prior independent fresh-Worker reopen evidence in Round 1/2 remains valid for unchanged reopen behavior; current four saved model files were transferred and hash-verified. No new Mac or other-platform certification is claimed.

I also ran affected local tests: `test_w21_closure.py` and `test_g3_7_w20_validation.py`, **66 passed**. These are software regression evidence; native and numerical claims above depend on the real engine runs. Physical validation remains UNVERIFIED, not promoted from numerical acceptance.

Existing Round 1/2 failed records are preserved; their findings are closed by subsequent evidence, not rewritten. Nonblocking backlog does not delay this scoped approval. No W22 work is authorized or performed by this review.

Signed: independent reviewer `/root/reviewer_final`, 2026-09-25.
