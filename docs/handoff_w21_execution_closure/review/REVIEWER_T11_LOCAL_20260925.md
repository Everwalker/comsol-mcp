# Independent T11 local review — 2026-09-25

## Verdict

`LOCAL_PASS_NATIVE_PENDING` for candidate wheel `fd761c84dfbb69e2d01dfb62b9de6051028eacf566ef46c0a99f3889bd1bcfed`. This is a local source/provenance check only; it does not approve the new native T11 path or close W21.

## Independent checks

- Recomputed SHA-256 for all 73 files listed in `CANDIDATE_T11_SOURCE.json`; all matched the receipt.
- Replayed the prior fixed-list counterexample through production `validate_convergence` with no Worker and a nonexistent model. The trend sub-analysis remains `PASS`, but the public result is `status=UNVERIFIED`, `numerical_verification_status=UNVERIFIED`, `scope=EXTERNAL_DATA_ONLY`, and `model_validated=false`. It cannot certify native convergence.
- Read the candidate registration path. It resolves a registered W17 observation, derives the nine B2 point/time errors from its stored field values, reads actual mesh/DOF and solver tolerance/output times, and stores a hashed convergence record with producer and observation references. The validation entry accepts only the exact `{convergence_id, sha256}` reference shape; caller-supplied errors/settings are not part of that reference. The resolver rejects duplicate native settings tuples and checks the stored benchmark, build, time list, and sample coordinates across levels.

No local A1/A2 remains in the reviewed T11 source path. The direct counterexample was independently executed; pytest was unavailable in this shell (`python3 -m pytest` reported `No module named pytest`). The 84 Python checks and 30 Java checks/1 skip reported by the main Agent are recorded as main-Agent test evidence, not as my independent test run.

## Required independent native follow-up

After the main Agent finishes the current 6.4 run, review raw public `tools/call` requests and responses for the actual registered convergence flow, then use an independently owned model/Worker for the prescribed normal and negative checks. Repeat on 6.3. Confirm each version has three distinct actual mesh settings and three distinct actual tolerance settings with the frozen B2 output times, stored field samples, traceable producers, and the original 0.1 K criterion. Negative calls must cover unregistered fixed arrays, repeated references, caller error/settings overrides, duplicate mesh/tolerance settings, and missing or tampered references; none may return numerical PASS. Preserve `physical_validation_status=UNVERIFIED`.

Do not overlap the main Agent's active 6.4 engine run. Existing Round 1/2 Windows evidence is inherited only for unchanged behavior; it predates this candidate's T11 provenance changes and cannot substitute for the follow-up above. No W22 scope is added.
