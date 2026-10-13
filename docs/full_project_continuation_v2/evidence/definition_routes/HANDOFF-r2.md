# Definition validation r2 handoff

**Candidate:** `definition-routes-20261012-r2`  
**State:** candidate source + software evidence, awaiting Root review.  
**Current checkout at freeze:** `799bf7a0fa95313e7659f44d8ff40ff1678314d5` (the inherited/base commit recorded by r1 is separately preserved as `60464c85b429e649f4f80484473999e5365ad96a`).

The r1 implementation and its 250-pass JUnit remain unchanged. Before editing, exact r1 copies of `_g3_definition_validation.py` and `test_g3_definition_validation.py` were saved under `source-r1/` and SHA-256 checked against r1's manifest. This r2 candidate changes only those two code/test files; evidence additions are in this directory.

## Changes made for Root review findings D1–D5

- Cleanup uncertainty now reaches the top-level `partial_change` and `execution_state_unknown` receipt fields. A result produced after an unconfirmed temporary Interp cleanup cannot look like an ordinary completed sample.
- `test_points` remains optional. A path-only request reads mapping/operator/search metadata and marks arithmetic, operator samples and bounded coverage `NOT_RUN` without invoking `result_at_points`.
- The helper reads the actual `opname` property and uses `root.<component>.<opname>(x/y/z)`. It no longer assumes the coupling node tag is the expression name; tests include a different tag/name and the same operator name in another component.
- Coordinate residuals are compared only for `srcframe=spatial`. Mesh/material frames preserve finite-value and method observations but return `FRAME_CONVERSION_UNVERIFIED`, with no residual field.
- `method`, `exttol` and `usenan` are read back and reported. Bounded counts distinguish finite values, NaN/nonfinite values, point-level error markers, invalid results, and unclassified batch failures. The report states `source_mesh_inclusion=NOT_INFERRED` and keeps global source-mesh coverage `UNVERIFIED`. COMSOL documents `closest` as defined everywhere through a nearest source-selection point and says `exttol` is unused for that method.

Official COMSOL 6.4.0.293 facts and source digests are recorded in `API_EVIDENCE-r2.json`; the source diff from the preserved r1 bytes is `R2_SOURCE.patch`.

## Software evidence

The strict-network-guarded combined regression ran with the preserved Python 3.12.13 runtime. It reports **256 passed, 0 failed, 0 errors, 0 skipped** across:

- `tests/test_g3_definition_validation.py`
- `tests/test_g3_w17.py`
- `tests/test_g3_w13.py`
- `tests/test_function_evaluate_contract.py`

The JUnit and invocation record are `software-r2/junit-final.xml` and `software-r2/invocation.json`. The guard log contains one activation record and zero blocked network events. An initial focused run's path-only guard failure is preserved in `software-r2/junit-focused.xml`; the fix was applied, then the focused helper suite passed 28/28, and the final combined suite passed 256/256.

## Limits and next step

This is not production-dispatch integration. The shared dispatcher/registry/Worker source is still owned by the model-delivery agent; wire these three operations only after that owner freezes and hands off the shared files. Native COMSOL, GUI, and scientific acceptance are `NOT_RUN`/`NOT_EVALUATED`. Destination-frame property evidence is absent; mesh/material frame conversion and global source-mesh coverage remain `UNVERIFIED`. Root's independent acceptance review is pending.

See `FREEZE_MANIFEST-r2.json` for exact source/evidence sizes and hashes.
