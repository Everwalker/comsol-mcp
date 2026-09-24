"""Transaction sidecar is authoritative; checkpoint_id references OperationStore SQLite."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

from comsol_mcp._g2_transactions import TransactionRecord, TransactionStore
from comsol_mcp._operation_store import OperationStore


def test_missing_store_and_record_restart(tmp_path):
    path = tmp_path / 'transactions.json'
    store = TransactionStore(path)
    assert store.list() == []
    record = TransactionRecord(transaction_id='t', model_ref={'model_tag': 'main'}, actions=[], invariants=[], checkpoint_id='sqlite-checkpoint')
    store.put(record)
    assert TransactionStore(path).get('t') == record.as_dict()
    assert TransactionStore(path).get('t')['checkpoint_id'] == 'sqlite-checkpoint'


@pytest.mark.parametrize('payload', [b'{broken', b'[]', b'\xff'])
def test_corrupt_store_blocks_without_overwriting(tmp_path, payload):
    path = tmp_path / 'transactions.json'
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    store = TransactionStore(path)
    for action in (lambda: store.get('t'), store.list, lambda: store.put({'transaction_id': 't'})):
        with pytest.raises(Exception) as exc:
            action()
        assert exc.value.code == 'STORE_CORRUPT'
        assert exc.value.sha256 == digest
        assert str(exc.value.path) == str(path)
        assert path.read_bytes() == payload
    audit = store.recover()
    from pathlib import Path
    assert Path(audit['backup_path']).read_bytes() == payload
    assert audit['sha256'] == digest
    assert audit['recovered_at']
    assert store.list() == []
    store.put({'transaction_id': 'new'})
    assert TransactionStore(path).get('new') == {'transaction_id': 'new'}


def test_unreadable_store_is_blocked(tmp_path):
    if sys.platform == "win32":
        pytest.skip("POSIX chmod(0) read-revocation not supported on Windows NTFS")
    path = tmp_path / 'transactions.json'
    payload = b'{"old": {"transaction_id": "old"}}'
    path.write_bytes(payload)
    path.chmod(0)
    try:
        store = TransactionStore(path)
        with pytest.raises(Exception) as exc:
            store.get('old')
        assert exc.value.code == 'STORE_CORRUPT'
        with pytest.raises(Exception) as exc:
            store.put({'transaction_id': 'new'})
        assert exc.value.code == 'STORE_CORRUPT'
    finally:
        path.chmod(0o600)
    assert path.read_bytes() == payload


def test_out_of_band_corruption_blocks_the_next_put_without_overwriting(tmp_path):
    """A store constructed while the file was healthy must not publish over damage."""
    path = tmp_path / 'transactions.json'
    store = TransactionStore(path)
    store.put({'transaction_id': 'known'})
    path.write_bytes(b'{broken')          # damaged after this instance was loaded
    with pytest.raises(Exception) as exc:
        store.put({'transaction_id': 'next'})
    assert exc.value.code == 'STORE_CORRUPT'
    assert exc.value.sha256 == hashlib.sha256(b'{broken').hexdigest()
    assert path.read_bytes() == b'{broken'   # the damaged history is untouched
    with pytest.raises(Exception) as exc:
        store.list()
    assert exc.value.code == 'STORE_CORRUPT'


def test_put_does_not_drop_records_written_by_another_instance(tmp_path):
    path = tmp_path / 'transactions.json'
    first, second = TransactionStore(path), TransactionStore(path)
    first.put({'transaction_id': 'a'})
    second.put({'transaction_id': 'b'})
    restarted = TransactionStore(path)
    assert sorted(row['transaction_id'] for row in restarted.list()) == ['a', 'b']


def test_publish_leaves_no_temporary_files_behind(tmp_path):
    path = tmp_path / 'transactions.json'
    store = TransactionStore(path)
    store.put({'transaction_id': 'a'})
    store.put({'transaction_id': 'b'})
    assert sorted(item.name for item in tmp_path.iterdir()) == ['transactions.json']
    assert TransactionStore(path).get('a') == {'transaction_id': 'a'}


def test_recover_into_a_chosen_directory_audits_error_and_hash(tmp_path):
    path = tmp_path / 'transactions.json'
    payload = b'{"keep": '
    path.write_bytes(payload)
    audit_dir = tmp_path / 'audit'
    audit_dir.mkdir()
    store = TransactionStore(path)
    with pytest.raises(Exception):
        store.get('keep')
    audit = store.recover(destination_dir=audit_dir)
    backup = Path(audit['backup_path'])
    assert backup.parent == audit_dir
    assert backup.read_bytes() == payload
    assert audit['sha256'] == hashlib.sha256(payload).hexdigest()
    assert audit['bytes'] == len(payload)
    assert 'JSONDecodeError' in audit['error']
    assert audit['recovered_at'].endswith('Z')
    assert audit['source_present'] is True
    assert audit['store_path'] == str(path)
    assert store.list() == []
    store.put({'transaction_id': 'fresh'})
    assert TransactionStore(path).get('fresh') == {'transaction_id': 'fresh'}


def test_recover_reports_a_vanished_source_instead_of_inventing_a_copy(tmp_path):
    path = tmp_path / 'transactions.json'
    path.write_bytes(b'{broken')
    store = TransactionStore(path)
    path.unlink()
    audit = store.recover()
    assert audit['source_present'] is False
    assert audit['backup_path'] is None and audit['sha256'] is None
    store.put({'transaction_id': 'fresh'})
    assert TransactionStore(path).get('fresh') == {'transaction_id': 'fresh'}


def test_recover_is_refused_on_a_healthy_store(tmp_path):
    path = tmp_path / 'transactions.json'
    store = TransactionStore(path)
    store.put({'transaction_id': 'kept'})
    with pytest.raises(Exception) as exc:
        store.recover()
    assert exc.value.code == 'INVALID_REQUEST'
    assert store.get('kept') == {'transaction_id': 'kept'}


def test_transaction_record_references_resolve_into_the_operation_store(tmp_path):
    """Sidecar owns transaction rows; SQLite owns checkpoint/job metadata."""
    operations = OperationStore(tmp_path / 'operations.sqlite3')
    try:
        digest = 'a' * 64
        operations.persist_checkpoint('checkpoint-a', {'checkpoint_id': 'checkpoint-a', 'sha256': digest,
                                                        'path': str(tmp_path / 'a.mph')})
        store = TransactionStore(tmp_path / 'transactions.json')
        store.put(TransactionRecord(transaction_id='txn-1', model_ref={'model_tag': 'main'},
                                    actions=[], invariants=[], checkpoint_id='checkpoint-a'))
        record = TransactionStore(tmp_path / 'transactions.json').get('txn-1')
        rows = operations.list_metadata('checkpoints')
        resolved = {row['checkpoint_id']: row for row in rows if 'checkpoint_id' in row}
        assert record['checkpoint_id'] in resolved          # the cross reference resolves
        assert resolved[record['checkpoint_id']]['sha256'] == digest
        # The sidecar never mirrors checkpoint metadata as a second truth.
        assert 'checkpoint_metadata' not in record
        assert json.loads((tmp_path / 'transactions.json').read_text())['txn-1']['checkpoint_id'] == 'checkpoint-a'
    finally:
        operations.close()
