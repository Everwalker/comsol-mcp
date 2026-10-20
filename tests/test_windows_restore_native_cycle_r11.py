"""Executable regression for the R11 task-owned private WAL snapshot path."""
import tempfile
import sqlite3
from pathlib import Path
import unittest

from comsol_mcp import _state_backup as backup
from comsol_mcp._operation_store import OperationStore
from tools import windows_restore_native_cycle_r11 as cycle


class PrivateWalSnapshotCaseTests(unittest.TestCase):
    def test_private_producer_snapshots_owned_copy_and_preserves_source_closure(self):
        with tempfile.TemporaryDirectory(prefix="cv26-r11-wal-", dir="/private/tmp") as temporary:
            root = Path(temporary)
            source = root / "source.sqlite3"
            out = root / "case"
            out.mkdir()
            original_tempdir = backup.tempfile.tempdir
            backup.tempfile.tempdir = str(root)

            store = OperationStore(source)
            store.close()
            writer = sqlite3.connect(source)
            try:
                self.assertEqual(writer.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower(), "wal")
                writer.execute("CREATE TABLE task_wal_probe(value INTEGER)")
                writer.execute("INSERT INTO task_wal_probe VALUES(73)")
                writer.commit()

                result = cycle._private_wal_snapshot_case(source, out, backup)

                self.assertEqual(result["producer"], "production _private_readonly_connection")
                self.assertEqual(result["read_value"], 73)
                self.assertIs(result["private_copy_closed_and_removed"], True)
                self.assertIs(result["source_closure_unchanged"], True)
                self.assertEqual(result["source_closure_before"], result["source_closure_after"])
                self.assertEqual(result["source_sidecar_sha256_before"], result["source_sidecar_sha256_after"])
                self.assertEqual(set(result["source_closure_before"]), {"", "-wal", "-shm"})
                self.assertFalse(Path(result["private_copy_root"]).exists())
                self.assertEqual(result["snapshot"]["integrity_check"], "ok")
                self.assertIs(result["snapshot"]["standalone_sidecar_cleanup"]["WAL_absent"], True)
                with sqlite3.connect(out / "snapshot.sqlite3") as snapshot:
                    self.assertEqual(snapshot.execute("SELECT value FROM task_wal_probe").fetchone(), (73,))
            finally:
                writer.close()
                backup.tempfile.tempdir = original_tempdir


if __name__ == "__main__":
    unittest.main()

