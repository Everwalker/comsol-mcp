# Acceptance assembly draft blockers

The populated draft is intentionally non-accepting (`overall_acceptance=false`). The current runtime source identity is `a3f39b3022b417e16c3087b8b35f1daa06f30b8a` / `5a3e1b72e95a9df8892def2ca0994a8737508612`.

- The final6 native batch binds all eight run families to the a3 source and reports PASS. `C02` still records native cleanup-fault `NOT_RUN` with a current-a3 JUnit control mapping `PASS`; `C10` still records native `PARTIAL` with a separate current-a3 JUnit control mapping `PASS`.
- The scoped current-a3 JUnit control receipt binds the actual PASS nodeids for C02 (11 cases), C09 (23 cases including missing-imaginary, getter-failure, shape, and exact-real-zero controls), and C10 (19 cases). It is software-control evidence only and does not promote those tests to native COMSOL acceptance.
- `C11` and `C14` preserve the native node `NOT_RUN` rows. Their independent final6 CutPlane and transient runs provide the separate PASS coverage required by the mapping.
- `C16` uses the current full pytest result (1824 passed, 0 failed, 1 skipped) and current wheel/resource-hash evidence. These are software/package gates and do not certify COMSOL recovery or physical acceptance.
- `C00` and `C01` remain recovered-PIN historical restoration/provenance evidence only. They are not bound to the current runtime source.
- Current-source recovery is pending a committed archive plus fresh extraction and rebuild receipt. The historical PIN restore receipt cannot satisfy that proof.
- `C17` current-source teardown and cleanup is `PASS`: the fresh post-cleanup inventory has no task-owned matching processes and all 11 quiescence receipts have empty pending-job lists; configuration integrity is also `PASS`. The earlier `final_process_inventory.json` `REVIEW_REQUIRED` result remains attached as historical context. This cleanup receipt does not satisfy the independent current-source recovery proof.
- The recovery proof is deliberately fail-closed: its `/status` assertion requires `PASS`, while `recovery_pending.json` is still `NOT_RUN`. Closure needs a committed current-source archive plus fresh extraction and rebuild receipt with source commit/tree and explicit PASS status.
- Remote synchronization is pending: `remote_identity` is null and `verified` is false. The eventual proof must compare the delivery commit identity; it must not rebind runtime evidence to a publication commit.

The assembler currently reports three deliberate errors: one for the required recovery status still being `NOT_RUN`, and two for unverified remote synchronization. Historical `table_regression_032c0a5.json` is retained as context only. No production, test, tool, engine, or top-level acceptance ledger was changed in this scoped draft.
