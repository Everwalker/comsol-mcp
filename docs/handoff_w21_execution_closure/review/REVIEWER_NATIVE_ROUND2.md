# Independent native review round 2

Candidate `1099bde57e23a6b65c8749fcfccaae2fd7d5a9e09c79765023a0555293ab2c54`; 74 source files independently match its receipt. Installed Windows module path and budget function were read back before running. Prior candidate evidence is inherited only for unchanged behavior; original failed run remains FAILED.

## Closed findings / independently executed results

- R1-BUDGET-REVISION closed: reviewer64_budget02 on actual COMSOL 6.4 build 293 returned COMPLETE/BUDGET_EXHAUSTED for two successive input-file changes; each had exactly one COMPLETED and one NOT_RUN, one computation and zero cache hits. Second call succeeded, no revision conflict. Native failed-expression optimization produced no best candidate. Exit 0, REVIEW_PROBES_PASS.
- reviewer63_01 on actual COMSOL 6.3 build 290 passed all independently selected normal and negative calls. Two-case max errors .001452692256748378/.0014992971416631917 K; stage continuation error .0017068326600337969 K. All below predeclared .03 K; stage initial check .001 K passed. Independently forged unregistered reference, replaced values, stale first-case ref, and wrong stage mapping rejected. Cache repeat two hits/zero computations; bounded real optimization feasible; two budget/input-change runs and failed-best negative passed.
- reviewer63_reopen01 fresh owned server/Worker loaded same SHA `a5665edc5c68b886378c0dfa02d1441983fe0666c9472e329efd5ccc938ea04f`, with exactly equal stored results, new references, numerical PASS, physical UNVERIFIED, no solves. Missing file and missing solution negatives rejected. Exit 0.
- Scientific MPH files are copied locally with verified SHA; source/launcher snapshots match each run's recorded hashes.

## R2-T11-PROVENANCE (A1, still CHANGES_REQUIRED)

- Original frozen T11: actual refinement and traceable producers/case refs; fixed lists, repeated grids and fabricated errors rejected.
- Fact: production `validate_convergence` still takes caller error/mesh arrays directly with no backend provenance. Independent `REVIEWER_T11_FIXED_ARRAY_PROBE.json` calls it with worker=None, nonexistent model and historical fixed [.065,.028,.009] errors; returns numerical PASS. Current native refinement runner sends its measured errors as ordinary caller arrays, so the normal data now exist but the original required negative guard is still absent.
- Impact: an unregistered invented convergence list can still certify numerical convergence; real new measurements alone do not fix the public validator.
- Minimal closure: require registered backend producer/case references and derive or verify refinement settings/error against those original records, reject fixed unregistered lists/repeated mesh/fabricated caller error. Reuse OperationStore and existing original operations. Retest normal+original negatives through public calls in each version. Do not rerun the six actual refinements just to change validation provenance if their backend records remain available and applicable.

Main 6.4 refinement records were independently recomputed: mesh DOF 375/711/1383, segments 8/16/32, fixed rtol1e-8; time DOF1383/segments32 fixed, rtol1e-3/1e-5/1e-7; output tlist unchanged. All six recomputed nine-point errors match recorded errors and satisfy original .1 K. Details in REVIEWER_T11_64_NUMERICAL_RECOMPUTE.json. This closes the *measurement* part for 6.4, not the provenance negative above. Main 6.3 six-level evidence remains pending at this point.

No final approval until the remaining original T11 gap and 6.3 refinement evidence close. No W22.
