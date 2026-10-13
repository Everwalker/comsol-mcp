# Definition routes candidate handoff r1

Candidate `definition-routes-20261012-r1` freezes exactly the standalone helper and its focused tests in `FREEZE_MANIFEST-r1.json`. The focused definition tests plus existing function, selection, and `result.at_points` adapter regressions passed **250 tests, 0 skips, 0 failures** under the strict network guard.

`function.validate` supports unary Interpolation with literal configured ranges, exact declared units, real sample availability, configured extrapolation, boundary-neighbor observations, and optional caller-selected local continuity probes. It reports documented kernel continuity order separately; data/global continuity remains `UNVERIFIED`.

`selection.rebind` previews or writes an existing named Box/Ball rule only after geometry tag and length-unit readback. A successful write is followed by entity/dimension readback; numeric entity identity across geometry revisions is explicitly not promised.

`definition.mapping_validate` keeps a labeled arithmetic preview as partial evidence. With `checks.evaluation`, it calls the existing `result_at_points` adapter with the explicit dataset, solution tag, outer/inner pair, spatial frame, coordinate unit, and destination points to sample `cpltag(x/y/z)`, then checks dataset, coordinate, axis, expression-unit, cleanup and numerical readbacks. Even when sample values match, `ok` remains false and overall status is `PARTIAL_UNVERIFIED`: destination-frame property and source-mesh coverage are not proved. The adapter also does not select time/frequency/parameter axes.

The candidate is not production-integrated because the shared route/registry/Worker files are currently owned by the model-delivery agent. Native COMSOL, GUI, and scientific acceptance are `NOT_RUN`/`NOT_EVALUATED`; independent review is pending. The full continuation goal remains open.
