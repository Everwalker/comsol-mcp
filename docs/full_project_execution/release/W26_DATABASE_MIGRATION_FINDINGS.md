# W26 database migration and backup findings

## Current behavior

The control daemon opens its durable ledger at `home/operations.sqlite3` through `OperationStore` in `_control_daemon.py`. `_operation_store.py` sets `SCHEMA_VERSION = 1` and applies table/index changes inside `BEGIN IMMEDIATE`.

Opening a populated historical v1 ledger now validates the version metadata, refuses ambiguous, unknown-future, and nonempty-unversioned schemas, preserves the old operations/jobs/events and metadata rows, and adds `resume_claims` transactionally. This is a **compatible schema extension**: the schema version stays 1 and existing rows are not transformed. The test fixture is pinned to source commit `a3418bd4546ecc33e26448e7f2a1982727a37153`, file `comsol_mcp/_operation_store.py`, blob `525c8897bd626d1e1d30255fcbaa0473e154f727`; the test resolves that commit:path with Git and checks the blob identity.

There is no populated-database version transformation implemented or covered. The existing schema-0 handling initializes the version marker; it does not constitute a demonstrated migration of populated data. Runtime-state JSON accepts only schema version 1 and refuses unsupported versions. Artifact registration reads record schemas 1 and 2, with scope-specific compatibility behavior, rather than transforming stored records.

The W26 release helper's `transition-check` exercises package install and rollback using wheelhouses and full installed-distribution inventories. The archived run used identical `comsol-mcp==0.1.9` wheels and locks for old and candidate, so it demonstrates a same-version reinstall/rollback only. It explicitly leaves application data/schema migration `UNVERIFIED`.

## Verification performed

`tests/test_operation_store_migrations.py` constructs a populated SQLite database matching the pinned historical v1 schema. It checks preservation of operations, jobs, events, sessions, runtimes, revisions, artifacts, checkpoints and idempotent replay; addition and repeated opening of `resume_claims`; rejection without logical changes for future, ambiguous, unversioned and malformed layouts; rollback after an injected DDL failure; and a real SQLite online backup while WAL is enabled with committed data, followed by restore to a separate file and independent reopen/migration.

The migration guard additionally requires unique indexes to contain exactly one named column with a nonnegative column id. A composite expression index cannot masquerade as uniqueness on its first column. Failed constructor migration closes the SQLite handle, while a failing `close()` does not replace the original migration error.

These are fixture-level SQLite and software checks. No user database was opened, modified, backed up, or restored. The fixture backup test does not establish a supported operator backup/restore command.

## Minimum production command proposal

Add explicit `state backup` and `state restore` subcommands, separate from automatic daemon startup:

* `state backup --home <private-control-home> --destination <new-file>` requires the daemon to be stopped by acquiring the same `control.lock`, rejects symlinked/ambiguous paths and an existing destination, uses SQLite's online backup API so committed WAL pages are included, runs `PRAGMA integrity_check` on the source and destination, and writes an atomic manifest with database SHA-256, byte count, schema version, package build identity, and verification results. The destination must be outside the live control home and its evidence must not contain database rows or secrets.
* `state restore --home <private-control-home> --backup <file> --manifest <file> --confirm` verifies the manifest digest and backup integrity, stages the restored database on the same filesystem, opens and validates the staged copy with the target `OperationStore` migration code, and only then swaps it into a stopped control home. It preserves the pre-restore database and any sidecars in a dated recovery directory and reports failure without deleting the original. The command must refuse active/unknown daemon ownership and must not overwrite an existing recovery copy.

The command implementation should be tested end-to-end only against disposable fixture homes first. Migration failure must retain both the original live database and the verified backup; no direct repair or overwrite of a user's control database is implied by the fixture evidence above.
