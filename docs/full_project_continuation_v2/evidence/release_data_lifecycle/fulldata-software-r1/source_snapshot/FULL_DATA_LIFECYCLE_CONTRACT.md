# Full-data release lifecycle contract

`tools/release_transition_lifecycle.py` keeps the existing SQLite-only backup
profile available to historical tests, but `--with-data-lifecycle` now uses the
production full-state path. It creates a disposable operation ledger using the
old package, registers a ProjectAuthority project and a real saved-model MPH
through ArtifactStore using the candidate package, and streams both the ledger
and registered project file through `_state_backup` full backup.

The fixed model input is the existing reviewed COMSOL 6.4 reopen fixture at
`evidence/w21_closure/windows/reviewer64_reopen01/project/review_reopen.mph`
(SHA-256 `ee71658d66a92efc19c78de7742eb24c1ef3b16f1fb33b1d6814b1e79c96b9d6`,
10,725,566 bytes). The candidate checks the synthetic idempotency keys and
marks the disposable source UNKNOWN operation `RECONCILING` only after the
pre-candidate archive is complete. Full restore targets a new, empty control
home and workspace and restores that operation to its original `UNKNOWN`
state; candidate ProjectAuthority and
ArtifactStore readback verifies the restored model bytes and new project root.
The old package then reopens the restored ledger and confirms the original
terminal and UNKNOWN operation rows remain intact.

If a restore process leaves the durable full-restore journal, the lifecycle
records and invokes the production `full-recover` command. A successful
recovery does not turn the failed transition into PASS. Recovery artifacts and
failure receipts are retained in the transition work root.

The software-prepared `zero-solve-reopen-entry.json` binds the restored
project, artifact ID/SHA/size, relative path, and exact model path for the
exclusive native lane. Its status remains `NOT_RUN_SOFTWARE_PREPARED`; the
entry requests only opening and readback under COMSOL 6.4.0.293, with
`solve_requested: false`. Software fixtures do not prove that native open or
readback succeeded. Release, multi-platform, numerical, scientific, and native
acceptance remain independent.
