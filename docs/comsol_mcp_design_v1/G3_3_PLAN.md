# G3.3 independent acceptance plan

Current status: **RUNTIME_VALIDATED_DELIVERY_PENDING** at source `a3f39b3022b417e16c3087b8b35f1daa06f30b8a` (tree `5a3e1b72e95a9df8892def2ca0994a8737508612`). The table completion regression is repaired. All eight fresh public-MCP final6 groups passed, full software validation passed with 1824 tests and one Windows-only skip, the source-outside wheel/resource check passed, and the final task-owned process inventory after cleanup passed with zero selected processes remaining. Repaired-source recovery/archive, clean publication, and ordinary remote synchronization remain pending; overall acceptance remains false. W18 is not authorized.

Scope: macOS Apple Silicon, installed COMSOL 6.4.0.293 and JDK 11. Windows, Linux, Intel Mac, COMSOL 6.3 and GUI remain UNVERIFIED. License and commercial module availability are external prerequisites.

The current native batch receipt is `evidence/phase4_3/runs/final_batch_a3f39b3_20260922T0907Z/batch_status.json`, with the public summary in `public_batch_status.json`. The software receipt is `evidence/phase4_3/runs/full_pytest_a3f39b3_20260922T090916Z/run_status.json`; the wheel receipt is `evidence/phase4_3/runs/wheel_a3f39b3_20260922T091026Z/run_status.json`; cleanup is recorded in `evidence/phase4_3/runs/final_batch_a3f39b3_20260922T0907Z/final_process_inventory_after_cleanup.json`. These records establish runtime validation for their stated scopes and do not close delivery.

## Execution gates

- M0: Independently restore the fixed network PIN; verify commit/tree/file hashes, preserve original failure evidence and build a fresh Python environment.
- M1: Verify shared measures, selection, solution binding, complex transforms, coordinates and bounded artifacts against control tests and a native non-unit-volume fixture.
- M2: Verify dimensions 0–3, selected entities, native axisymmetry, four-axis arrays, complex fields, typed nodes and actual Probe history.
- M3: Reopen three newly saved same-hash MPH artifacts in new Workers without solving; seven negative controls use the same production checker. Verify public protocol no-replay and active control response.
- M4: Complete affected regressions and full software tests, source-outside wheel installation, machine lock, substantive semantic review, clean archive restore, public evidence scrub and ordinary non-force remote synchronization.

## Acceptance mapping

Mappings identify required checks, not PASS claims. Final status must come from hash-bound fresh evidence in `evidence/phase4_3_acceptance.json`.

| Case | Original task / requirement | Required scope |
|---|---|---|
| C00 | PIN clean restore | Network PIN recovery and file hashes; final package recovery |
| C01 | Historical ledgers | Byte preservation, separate correction ledgers and substantive semantic review |
| C02 | T033, T038 | Genuine UNKNOWN/error propagation; cleanup fault injection distinguished as CONTROL |
| C03 | Gate A / F02 stored solution | A/B/C same-file fresh-worker stored-solution read, seven negative controls |
| C04 | T013 | Constant field on non-unit volume; integral/mean/std/RMS |
| C05 | T013 | Points/lines/surfaces/volumes, subsets and nondefault component/geometry |
| C06 | T013 | Nonuniform and weighted integral statistics with analytical expectations |
| C07 | T013 | Native axisymmetric measure settings and actual readback, no duplicate radial factor |
| C08 | T021 | Four-axis data, inner/outer/time/parameter mapping and strict selectors |
| C09 | T014 | Real/complex preserve/real/imag/abs/phase and correctly ordered statistics |
| C10 | T015 | Input/geometry coordinate unit conversion, frame and full readback validation |
| C11 | Typed results nodes | Solution/CutPoint/CutLine/CutPlane/Join provenance, CRUD, partial-write outcome |
| C12 | T035, T030 | Prewrite authorization, path safety, atomic export and failed-evaluation no publication |
| C13 | T049 | Public immutable-hash chunk read and bounded numeric payload refusal |
| C14 | Definitions Probe | Actual probe create/read/history/table linkage and preserved user nodes |
| C15 | T010, T012, T027 | Same-key no replay, responsive controls during a real active operation |
| C16 | Packaging/schema; local subset of T001 | Fresh Python lock, pip check, full suite, final wheel installed outside source |
| C17 | Teardown/scope | Quiescent original jobs, owned process identity cleanup, recovery and final ledger |

`tests/test_g3_gate_a2_f02_reopen.py` is CONTROL_UNIT; `tests/test_g3_w17.py` is UNIT_CONTRACT. Neither is engine evidence. API probes establish only the probed API behavior. A source-changing run is OBSERVATIONS_ONLY_SOURCE_CHANGED even when all assertions pass.

## Review and delivery

Current case decisions and exact hashes belong in `evidence/phase4_3_acceptance.json`. Original c051065 documents/ledgers are preserved under `evidence/phase4_3/runs/resumption_review_20260922T0719Z/`; earlier completion claims do not certify this source.

Semantic findings and file hashes under `audit/` are separate from automated all-file inventory. The historical missing `audit/semantic_review.json` remains explicitly MISSING. No source audit, mock test, API probe or source archive is engine or physical validation.

Raw allocation budgets do not measure COMSOL internal cache; its peak memory remains UNMEASURED. Only verified task-owned quiescent processes are cleaned. Installation configuration is read-only. Final status requires synchronization and stops at W17.

The clean publication plan preserves the original development branch and evidence locally. A separate publication snapshot will be created as a child of verified `origin/main` PIN `2cb4627924d1a3240818ea7cd00453d4bd2d2da8`, then checked against the same tested source file hashes and the public evidence allowlist. Historical worker bearer-token blobs remain excluded, and synchronization will use an ordinary non-force push. The future snapshot SHA and archive name are intentionally not asserted here.
