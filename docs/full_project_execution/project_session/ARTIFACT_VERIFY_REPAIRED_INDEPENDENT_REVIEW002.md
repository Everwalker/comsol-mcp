# `artifact.verify` V01–V06 independent repair re-review

**Verdict: SOFTWARE-SCOPE PASS.** The exact frozen successor passed the production verifier suite, the immutable nine-case reviewer control module, and the added registered-route deep-JSON boundary case. I found no remaining substantive defect in V01–V06 within the stated software scope.

## Frozen subject and isolation

- Successor candidate fingerprint: `817e806f97e39323a8d6906446d2a8402ec8e7746a15ba9115006abe243db46f` (seven scoped files).
- Freeze: `verify-repair-run008/VERIFY_CANDIDATE_FREEZE002.json`, SHA-256 `02625c1afc93cebe84b946f80be5d5059cc0837c1535844e90f9057bcc872529`.
- Handoff: `verify-repair-run008/HANDOFF002.json`, SHA-256 `559b04f0e87f8a46b4114774eb8e43070ac6c532b8e70b6afacfcc2c721bfbf9`.
- Exact two-path patch: `verify-repair-run008/VERIFY_CANDIDATE_DIFF002.patch`, SHA-256 `ff19e1dfa7191ddbef8c4a8b33ba82af3ed663600f571b4022243ab00bdd88b6`.
- Independent APFS copy: `/private/tmp/comsol-artifact-verify-independent-repair-review002`. Its scoped fingerprint and all seven source/test hashes match the freeze; the copy uses distinct inodes from its source.
- Runs used the pinned Python 3.12 runtime and strict network guard. The guard was active before test entry, with zero denied operations and zero setup errors. Each run had a 90-second timeout. No production source changed during the runs.

## Repair coverage

Independent source review and real registered-artifact dispatch controls confirmed these repairs:

1. **V01:** patch-sensitive markers remain `INCOMPLETE` instead of being misclassified through `ValueError` catch ordering.
2. **V02:** supported PEP 440 prerelease, postrelease, and devrelease ordering distinguishes the versions that the original comparison collapsed.
3. **V03:** transitive requested extras are checked against the dependency wheel's declared `Provides-Extra` values.
4. **V04:** non-string targets are validated before membership checks and produce a typed `INVALID` result.
5. **V05:** duplicate object keys and non-finite numbers are rejected by the strict JSON parser at nested bundle layers.
6. **V06:** one shared 512 MiB counter tracks actual streamed nested wheel-member bytes across the whole bundle; exhaustion stops parsing immediately. The 256 MiB individual-wheel bound remains in place.

## Final guarded run

`deep_json_review004` ran 66 tests: 56 product tests, all nine immutable reviewer controls, and one additional real-route JSON boundary control. Result: 66 passed, 0 failed, 0 errors, 0 skipped, return code 0. The existing product tests also registered and parsed the three accepted dev1 archives without changing their hashes. Guard state: active=1, denied=0, setup_errors=0. Stdout reports `66 passed`; stderr is empty.

- Receipt: `deep_json_review004/TEST_RUN_RECEIPT.json`, SHA-256 `8a86d03bb223d7af5e2b3dbf9c860f9489a4e5d807df974964b7272cddbbe4c9`.
- JUnit: `deep_json_review004/junit.xml`, SHA-256 `1d2b3a5ab8488092c2f7aeb342a6a72b261e98d7d722c8a97e7477006489ed71`.
- Guard: `deep_json_review004/guard.jsonl`, SHA-256 `79e18e2425da29e9ad246012245084842e0c1e2b1746bcd37ad1c03b86e6e0c5`.
- Stdout SHA-256 `11448373707f28d7b61155640edbbae054c2a09deb1578a9e0d8d3bc9cbec5bf`; stderr SHA-256 is the empty-file digest `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.

The added control passes a 50,000-level nested JSON value of about 100 KiB through synthetic registration and the actual `artifact.verify` route, within the 4 MiB metadata byte budget. The route returns a typed `INVALID` result with a bounded `ZIP_READ_FAILURE` finding instead of exposing an exception or `EXECUTION_STATE_UNKNOWN`. A 1,200-level version returned `VERIFIED`; the frozen contract defines no independent JSON-depth cap, so that is allowed. The generic typed diagnostic at the deeper boundary is not a new defect under the frozen requirement for typed bounded diagnostics.

## Preserved exploratory failures and prior failures

Two earlier deep-JSON reviewer runs remain in `deep_json_review002/` and `deep_json_review003/`. Each had one reviewer assertion failure, not a product-test failure: the first incorrectly required the 1,200-level value to be rejected; the second over-specified a manifest-specific finding code for the 50,000-level typed rejection. The corrected real-route control is retained as `deep_json_review004`; the earlier runs are not erased or counted as product failures.

The original repair-stage failure evidence also remains unchanged: run007 had 65 tests with one failure (`junit.xml` SHA-256 `3655fbafdaed422f83b7352044b5a42037b5d1a5f42b1995a91ebaed597d4474`), and the previous independent `CHANGES_REQUIRED` report and its evidence remain intact.

## Scope boundary

This is a software-only review of the exact seven-file candidate and its registered-artifact verifier route. It does not establish native compatibility, trusted producer provenance, installation behavior, current-source equality, COMSOL model dependency closure, or scientific acceptance. The C065 assembled exact-allowlist preimage excludes `_g2_artifact_verify.py`; no joint C065 integration run was performed, and this report makes no claim that it was integrated or passed there. No live repository promotion, GitHub action, native/JVM execution, payload import, or real Worker start was performed.
