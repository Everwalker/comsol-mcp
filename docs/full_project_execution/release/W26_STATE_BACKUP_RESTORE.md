# W26 control-ledger backup and restore

The new `comsol-mcp-state` entry point implements `backup`, `restore`, and `recover` for the private SQLite control ledger. It handles `operations.sqlite3` plus its `-wal` and `-shm` files; it does not include the `.mph` models or artifact files referenced by ledger rows. A provisional offline wheel was built from a source copy and installed into a fresh disposable venv with no system site packages using a hash-locked local wheelhouse. The installed CLI then ran backup, restore, and recovery against synthetic SQLite homes; `pip check` passed and recovery restored the exact prior database hash after an injected rollback failure. This validates the command entry point and package imports outside the source tree, but it is not a final integrated release build, target-platform matrix acceptance, or COMSOL/native acceptance.

Every operation takes the same OS-backed `control.lock` held by `_control_daemon.serve()` and keeps it through database inspection, snapshot/staging, and replacement. A stale lock file alone does not indicate an active owner. The source ledger is inspected through a read-only SQLite connection; backup does not construct `OperationStore` or run migration against the live source. Backup files and their side-by-side JSON manifests are exclusive new files outside the control home. The manifest records size, SHA-256, schema version, and integrity result without copying database rows into evidence.

Restore requires `--confirm`. It first rejects input WAL/SHM/journal sidecars, copies the main backup file exclusively into private control-home staging, and checks that copy against the manifest. Integrity/schema checks and subsequent migration read only that verified copy, so later changes to the external path cannot affect the restore. It then runs `OperationStore` migration against a separate candidate stage. Before touching the live path it writes byte-verified copies of the prior main/WAL/SHM set into a new recovery directory and creates a pending journal. It moves the old file set aside and installs the staged database one file at a time; the code does not describe those renames as a multi-file atomic operation. It reopens and checks the installed database, archives the journal, and leaves the recovery copy in place.

If any step after journal creation fails, restore attempts to put the exact prior file bytes back while still holding the same `control.lock`; an independent process cannot acquire the lock during rollback. If rollback cannot be verified, the journal and recovery directory remain and `recover --confirm` retries rollback. `assert_no_pending_restore()` is the fail-closed daemon startup gate: it refuses a home with any pending journal and points to the recovery command. The reviewed minimal `serve()` patch is preserved in `W26_CONTROL_DAEMON_STARTUP_GUARD.patch` and is now integrated into `_control_daemon.serve()`: the daemon acquires `control.lock`, checks the journal, and only then constructs `ControlDaemon` and opens `OperationStore`. The integration test exercises this production code from an isolated copy and proves that a pending journal prevents database setup and that a clean gate allows daemon construction.

## Operator recipe

Run the commands from the environment where the package is installed. The daemon must be stopped before backup, restore, or recovery: the daemon holds `control.lock` for its lifetime, and the CLI does not stop or restart it. Use the exact private control-home path from the daemon configuration. Backups go to a new directory outside that home; the command also creates `<database>.manifest.json` beside the backup.

```sh
comsol-mcp-state backup \
  --home /path/to/private-control-home \
  --destination /path/to/recovery/operations.sqlite3
```

Restore is explicit and replaces only the control ledger database file set. Review and retain the backup and manifest before running it:

```sh
comsol-mcp-state restore \
  --home /path/to/private-control-home \
  --backup /path/to/recovery/operations.sqlite3 \
  --manifest /path/to/recovery/operations.sqlite3.manifest.json \
  --confirm
```

If restore reports an incomplete rollback and leaves a pending journal, keep the daemon stopped and run the recovery command. It restores the exact pre-restore database/WAL/SHM bytes from the journaled recovery copy; it does not start the daemon:

```sh
comsol-mcp-state recover --home /path/to/private-control-home --confirm
```

Do not manually remove a pending journal or recovery directory. Production daemon startup now refuses any pending restore journal after acquiring the same control lock and before opening the database. If a restore fails, keep the daemon stopped and run `recover --confirm`; after successful recovery the journal is removed only after the exact pre-restore DB/WAL/SHM set has been restored and verified.

The command never starts or reconnects a COMSOL server. Subsequent normal daemon startup runs its existing `reconcile_after_restart()` path: terminal jobs stay terminal, nonterminal jobs become `RECONCILING`, and no operation is dispatched from the restored ledger. When a Worker connects with a different runtime/connection epoch, the existing `restore_runtime_state()` path advances model generations and invalidates old `ModelRef`s. Fixture tests cover those behaviors; native engine behavior is not part of this command's acceptance.

The implementation and tests run only against disposable directories. They cover WAL-inclusive SQLite backup, prior DB/WAL/SHM preservation, restore staging/migration, lock contention through rollback, stale lock files, symlink/path-traversal refusal, tamper and unmanifested-sidecar rejection, external-source mutation after private-copy verification, install failure rollback, failed rollback followed by explicit recovery, startup-journal refusal, terminal/UNKNOWN job recovery, and runtime identity invalidation. A separate integration test exercises the integrated production startup guard from an isolated source copy and verifies that the guard holds the daemon lock, rejects a pending restore journal before database setup, and allows the clean path to reach daemon construction. A freshly installed current-source wheel ran successful CLI backup/restore/recover operations against synthetic control homes, including byte-exact recovery after an injected incomplete rollback. No user control home or COMSOL engine was used.

## Remaining acceptance boundary

The root-reviewed narrow patch is now integrated after W23's exact-owned process cleanup released the source freeze. A dedicated `_publish_new_manifest_atomic()` is used only by `create_backup`: it fsyncs a same-directory temporary JSON file, publishes with no-clobber `os.link`, and fails closed when the filesystem cannot provide that operation. The manifest records the installed package version and SHA-256 fingerprints for `_state_backup.py` and `_operation_store.py`, explicitly labeled as component fingerprints rather than a complete wheel identity. Directory-fsync policy is explicit: required on POSIX and skipped by this implementation on Windows. The integrated `_state_backup.py` SHA-256 is `a1c33f6696f70ef085f39627fd582f6a898b3bbc92ab04fd1c0aacd12d936488`; the focused backup/startup suite reports 20 passed under Python 3.12.13 and pytest 9.1.1. Its portable evidence manifest is `evidence/luna_w26_release_foundation/manifest_atomic_integrated_20260927T021918Z/portable_manifest.json`. The earlier installed-wheel CLI test is still provisional for its prior source snapshot; an integrated offline wheel rebuild and installed-CLI verification remain before release. No live user control home or COMSOL engine has been used for backup or restore.
