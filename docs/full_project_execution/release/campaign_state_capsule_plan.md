# Campaign-state capsule scope proposal

This is a read-only inventory and exact proposed scope for the third recovery layer. No state file, SQLite database, private log bundle, or scientific model has been copied. All state payload candidates are `LOCAL_RECOVERY_ONLY`; the source and scientific models remain separate hash-bound dependencies.

Inventory time: 2026-09-26T08:14:18.497721+00:00
Current campaign status: `IN_PROGRESS`; recorded `active_jobs` entries: **0**. Host process quiescence was not checked. The blank top-level `state/RESUME.json` says `NOT_STARTED` and is a bootstrap template, not the current campaign resume.

## Proposed state payload

| Path | Bytes | SHA-256 | Classification / purpose |
|---|---:|---|---|
| `repository/docs/full_project_execution/state/RESUME.json` | 21966 | `c48609da8608b7d81528aeedc405a37ffb5bfb6442585e623f2354a43aca5c44` | CAPSULE_PAYLOAD_CANDIDATE_LOCAL_ONLY — Authoritative mutable campaign resume; raw bytes only after parent scope approval. |
| `repository/docs/full_project_execution/state/TASKS.json` | 20808 | `31ab5a01834f1691c0fde503982fcb257166789ba77b40f4d58efe290dd79832` | CAPSULE_PAYLOAD_CANDIDATE_LOCAL_ONLY — Current task ledger; state-only, not execution authorization. |
| `repository/docs/full_project_execution/state/ACTION_COVERAGE.json` | 428937 | `f191e8a7f5f917a8d6c9250b7c76b7cad6a6ccb8043071c0b1b3a05940274950` | CAPSULE_PAYLOAD_CANDIDATE_LOCAL_ONLY — Current action-to-evidence coverage ledger. |
| `repository/docs/full_project_execution/state/ACCEPTANCE_MATRIX.json` | 127813 | `9a059884c579c9d8de583ed0b221b2374e18c5ea7906d21f262dacf3fefa59fa` | CAPSULE_PAYLOAD_CANDIDATE_LOCAL_ONLY — Current acceptance and result matrix. |
| `repository/docs/full_project_execution/PROGRESS.md` | 569 | `3d0a99a5bb7750ab05b72f2cb6ebddf37805edac03579d800339278fbadc4c2e` | CAPSULE_PAYLOAD_CANDIDATE_LOCAL_ONLY — Canonical campaign progress; root PROGRESS.md currently an identical mirror. |
| `repository/docs/full_project_execution/state/INITIALIZED.json` | 389 | `526261175e95700cbe5aff6a859aa1a7d15bacbb5394d1f4b421d558bb9ca3ba` | CAPSULE_PAYLOAD_CANDIDATE_LOCAL_ONLY — Initialization identity tying inventory/source commit and counts. |
| `repository/docs/full_project_execution/state/ORIGINAL_INVENTORY.json` | 10137 | `1c3914a5c90201cb4db5558d72461c6650280ae05d6bed8d684694590f64dd70` | CAPSULE_PAYLOAD_CANDIDATE_LOCAL_ONLY — Baseline inventory and original contract hash/size references. |
| `repository/docs/full_project_execution/state/REVIEW_DECISION.json` | 378 | `c8c02c38b5526ac32aeecf7900d6cade2829dc92ad4bb89144b1c0875154189e` | CAPSULE_PAYLOAD_CANDIDATE_LOCAL_ONLY_EMPTY_REVIEW_POINTER — Reviewer decision pointer; presently blank/not started. |

## Original contracts and reviewer pointers

All 10 contract files listed by `ORIGINAL_INVENTORY.json` match their recorded byte counts and SHA-256. All 6 W20–W22 contract copies match `INHERITED_CONTRACT_LOCK.json`. These are verification inputs; the source archive already carries the exact source snapshot, so this capsule does not duplicate contract bytes.

| Path | Bytes | SHA-256 | Proposed treatment |
|---|---:|---|---|
| `repository/docs/full_project_execution/contract/00_FULL_SPEC.md` | 119907 | `617b92083fd9950243e9aa6b8d0424393560e7721258dd0567e6dda49f80b2f5` | hash/size verification only; match=True |
| `repository/docs/full_project_execution/contract/01_ARCHITECTURE.md` | 40629 | `c71d1f0d7a78189efa2a60d1f498b064d2cb563332056c64af4e6ec568f26cd3` | hash/size verification only; match=True |
| `repository/docs/full_project_execution/contract/02_ACTION_CATALOG.json` | 535397 | `f912d417a0fccb6b099377306018d824c8d437e4774525e92f77814a49d814de` | hash/size verification only; match=True |
| `repository/docs/full_project_execution/contract/02_ACTION_CATALOG.md` | 39747 | `5ad615c0433de8eab3d1cd71adeff2a7637a9762525eceabb9bb1036091189cd` | hash/size verification only; match=True |
| `repository/docs/full_project_execution/contract/03_ACCEPTANCE.json` | 44576 | `854c0b52bcc3928434aded04520879c83af4f4fb46019b8096f62b8f0bb3c6c2` | hash/size verification only; match=True |
| `repository/docs/full_project_execution/contract/03_ACCEPTANCE.md` | 22015 | `d6de6f68e2f7faf208522ebfb4faa7e0d2b564ee909ec02ff216ea3f1acdfa5e` | hash/size verification only; match=True |
| `repository/docs/full_project_execution/contract/04_IMPLEMENTATION_BACKLOG.json` | 9720 | `57539167e07a117e98041a15f1b0d1dd3d9af0aad0ab5b01542dfcb447d813dc` | hash/size verification only; match=True |
| `repository/docs/full_project_execution/contract/04_IMPLEMENTATION_PLAN.md` | 5677 | `6d90f8f370995ccaa282f9ece1338baddef0ddc31b4408cfd7d4a8e997fe1c17` | hash/size verification only; match=True |
| `repository/docs/full_project_execution/contract/05_DEVELOPER_TASK.md` | 3105 | `3531b939b356f5343ee53814c17373dee62f73fc17987d7f94e0c86238119e9f` | hash/size verification only; match=True |
| `repository/docs/full_project_execution/contract/06_CONTRACT_NOTES.md` | 2515 | `52a48fe065cf53d0ae992183578646ff7a50638811613f7984aa11e08cdc8d5d` | hash/size verification only; match=True |

The current `REVIEW_DECISION.json` is `NOT_STARTED` with no main/reviewer context IDs. `REVIEW.md`, `FINAL_REVIEW_INTAKE.md`, and `roles/REVIEWER.md` are instruction pointers only; no nonblank final reviewer report is selected.

## Source and science dependencies

The current superseding source ZIP is a pointer only: `repository/docs/full_project_execution/release/source_recovery_candidate_20260926_01/comsol-mcp-source-recovery-superseding1.zip`, SHA-256 `931e1afbc5d00ae524d7a4f7dc0e6b195436e86bf10b0ee7f33a3ed61d5a7596`. It is preliminary because the source is not frozen; replace this pointer after final integrated capture. The detached receipt and candidate source plan are also listed in the JSON.

The current W22 delivery model pointers remain outside this capsule:

- `repository/evidence/w22_vcsel/windows/delivery63_01/project/w22_final.mph` — 27499673 bytes, SHA-256 `8640ca262fdabc4a63f1359139d36afe25f6215b141766a1c2cad3930b3e796d`, existing reviewed hash matches=True; pointer only, no copy.
- `repository/evidence/w22_vcsel/windows/delivery64_01/project/w22_final.mph` — 27416942 bytes, SHA-256 `d62f90b1dd46d8144bc312985892317d7e03a75e7ac7039717253b7fcb3a61fb`, existing reviewed hash matches=True; pointer only, no copy.

Focused filename/path search found **0** W23/W24 or overlap/fiber/glue/cure/UV-labelled `.mph` files. The final separate science-bundle manifest and W23/W24 deliverables remain missing from this inventory.

## Restore rules and unresolved inputs

- Preserve every captured source receipt and state file byte-for-byte. Absolute paths are resolved only through a detached overlay mapping root tokens to the chosen restore roots; unresolved prefixes leave the capsule at `RESTORE_PREP_ONLY` and require manual mapping.
- Never relaunch or reassign a job found in a captured snapshot. If a job is listed or owner/process identity cannot be reconciled, report `IN_PROGRESS_RECONCILE_ONLY`; do not kill or launch anything.
- The verifier reads files only. It does not execute bootstrap, Python project commands, Java, COMSOL, network operations, or instructions embedded in a payload.
- No SQLite/database file was found in the three scoped state directories. This says nothing about external stores, which remain out of scope and unverified.
- No capsule capture has been performed. Parent scope decisions are listed in the JSON and are required before creating the actual capsule.
