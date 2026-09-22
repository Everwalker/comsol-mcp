# G3.3 independent acceptance plan

Current status: **IN_PROGRESS — final acceptance is not granted**. This document replaces the previously asserted 18/18 PASS summary. Its original bytes are preserved under the independent run's `prior_claims/`. The current authority is `NEXT_GOAL.md`; historical local PASS labels are not acceptance evidence.

Scope: W17/G3.3 on Apple Silicon macOS with the legally installed COMSOL 6.4 and JDK 11. Do not enter W18. Windows, Linux, Intel Mac, COMSOL 6.3 and GUI remain UNVERIFIED. Only fresh, identified, task-owned processes are used and cleaned; never adopt an old PID or runtime receipt.

## Execution gates

- M0: network restore of pinned commit `2cb4627924d1a3240818ea7cd00453d4bd2d2da8` / tree `dd3095e89c640c19aeb151cd0e8efe4af3c55802`; 3,580 source files checked; fresh software baseline 1,648 passed / 1 skipped. The 600 protected historical files match the freshly fetched PIN. Full semantic review remains in progress.
- M1: shared measure, selection, solution binding, four-axis FieldArray, complex transform, coordinate and artifact services. Native constant/non-unit-volume tests have passed in new runs; final source freeze remains required.
- M2: independent analytical oracles for dimensions 0–3, subsets, weighted and centered statistics, native axisymmetric measure readback, multiple inner/outer axes, complex modes and coordinates. Development numerical run `public_numeric_20260922T0445Z` passed its assertions but changed source prevents final certification. Typed nodes and export negative checks still have pending corrections.
- M3: public MCP saved-file reopen of all three new artifacts, real mutations for negative controls, original-job reconciliation and no replay. Development run `public_m3_20260922T0503Z` passed A/B/C plus seven negative controls without solving after reload. Its test-source drift still requires a frozen-source rerun.
- M4: affected G2/G3 regression matrix, final full suite, final wheel outside source, Python 3.14 ARM64 lock verification, semantic coverage, historical byte preservation, public evidence scrub, complete recovery archive and ordinary non-force synchronization. Pending.

Production fixes must retain typed paths, stop-on-first-write-failure outcomes, actual native property readbacks, fail-closed solution provenance and public MCP dispatch. A FieldArray has `[expression][outer][inner][point]`; native getter layouts are normalized explicitly. Raw numeric payload budget and engine internal memory are separate claims; engine cache remains UNMEASURED.

## Acceptance mapping

Mappings identify required checks, not PASS claims. Final status must come from hash-bound fresh evidence in `evidence/phase4_3_acceptance.json`.

| Case | Original task / requirement | Required scope |
|---|---|---|
| C00 | T001 clean restore | Network PIN recovery and file hashes; final package recovery |
| C01 | Historical ledgers | Byte preservation, separate correction ledgers and substantive semantic review |
| C02 | T033, T038 | Genuine UNKNOWN/error propagation; cleanup fault injection distinguished as CONTROL |
| C03 | Gate A, T019 | A/B/C same-file fresh-worker stored-solution read, seven negative controls |
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
| C16 | Packaging/schema | Fresh Python lock, pip check, full suite, final wheel installed outside source |
| C17 | Teardown/scope | Quiescent original jobs, owned process identity cleanup, recovery and final ledger |

`tests/test_g3_gate_a2_f02_reopen.py` is CONTROL_UNIT; `tests/test_g3_w17.py` is UNIT_CONTRACT. Neither is engine evidence. API probes establish only the probed API behavior. A source-changing run is OBSERVATIONS_ONLY_SOURCE_CHANGED even when all assertions pass.

## Review and delivery

Semantic coverage is recorded per module with actual findings, source hashes and limits under `audit/`; AST inventory alone is not semantic review. `audit/semantic_review.json` from historical references remains MISSING rather than fabricated. Final required documents are this plan, G3_3_FINDINGS, G3_3_OPERATIONS and RECOVERY; final ledgers preserve all previous failure evidence. Generated source archives exclude credentials, vendor jars, documentation corpora, virtual environments and disposable staging copies.

Only after all required local gates and synchronization pass may the status become `G3_3_MAC_W17_VERIFIED_SCOPED`. The current Mac run demonstrates development progress, not universal platform certification.
