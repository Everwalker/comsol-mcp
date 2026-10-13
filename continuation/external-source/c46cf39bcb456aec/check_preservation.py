import collections
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2')
REPO = ROOT / 'repository'
E = REPO / 'docs/full_project_continuation_v2/evidence/w23_native_run_20261012_007'
RAW = E / 'private-control-store/activity007-raw'
RUN = RAW / 'runner-evidence'
SOURCE = ROOT / 'local-preservation/w23-planar-te-source-20261012-007-candidate15'
OP = '2b35fd09-1a92-4c93-9612-c7ecf591fa5d'
JOB = '97c6cdeb-044a-43cb-ab78-8775b1c4b757'
REQUEST = 'w23-science-req-fba579ad-6acd-4368-9bf6-da98f889e44c'
RPC = 'wrk-9f5a195e-c14f-4165-829b-4c90b8673756'

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def read(path):
    return json.loads(Path(path).read_text())

def pinned(path, expected):
    actual = sha(path)
    assert actual == expected, (str(path), actual, expected)
    return actual

manifest_path = RAW / 'PRESERVATION_MANIFEST007.json'
pinned(manifest_path, 'e58e9daa7853bd9cc5688b59232e9cd9676070ab338418c9cdf12df919f0e4a0')
manifest = read(manifest_path)
supplement_path = E / 'ACTIVITY007_PRESERVATION_SUPPLEMENT.json'
pinned(supplement_path, 'aa1e73abdc0ff0db49a369c8cb16d89464bedd007f5b6c77a658f0be7a14b9fe')
supplement = read(supplement_path)
reconciliation_path = E / 'ACTIVITY007_RECONCILIATION.json'
pinned(reconciliation_path, '5f727beb7b019ae75219650b153a9e37f23a49307c460251d8b8a4678dba71a3')
assert Path(manifest['destination_root']) == RAW
assert len(manifest['files']) == manifest['file_count'] == 82
seen = set()
bytes_checked = 0
category = collections.Counter()
for row in manifest['files']:
    rel = Path(row['destination_relative_path'])
    assert not rel.is_absolute() and '..' not in rel.parts
    assert str(rel) not in seen
    seen.add(str(rel))
    dest = RAW / rel
    source = Path(row['source_path'])
    assert dest.is_file() and source.is_file() and not dest.is_symlink() and not source.is_symlink()
    assert dest.stat().st_size == source.stat().st_size == row['bytes']
    assert sha(dest) == sha(source) == row['source_sha256'] == row['copy_sha256']
    assert row['verified_equal'] is True
    bytes_checked += row['bytes']
    category[row['category']] += 1
assert bytes_checked == manifest['total_bytes'] == 6538429
assert dict(category) == {'runner_evidence': 56, 'owned_work_diagnostic': 26}, category
sidecars = supplement['sqlite_sidecars']['runner_snapshot_and_work_control_sidecars_created_or_seen_after_readonly_inspection']
assert len(sidecars) == 2
for row in sidecars:
    dest, source = Path(row['destination_path']), Path(row['source_path'])
    assert dest.is_relative_to(RAW / 'observer-created-sidecars')
    assert dest.is_file() and source.is_file()
    assert dest.stat().st_size == source.stat().st_size == row['bytes']
    assert sha(dest) == sha(source) == row['sha256'] == row['copy_sha256']
    assert row['equal'] is True and 'side effect' in row['provenance']
bytes_checked += sum(row['bytes'] for row in sidecars)
assert bytes_checked == 6571197
assert not any((RAW / rel).suffix == '.mph' for rel in seen)

database_checks = []
presend = observed = None
for rel, expected_hash in [
    ('runner-evidence/raw_operations.sqlite3', 'ec1a74f7acb0a961018c36d4dcbf2162bbbc290b0e36e89a8c5366a6939b21ba'),
    ('owned-work/control/operations.sqlite3', 'f4018f06500620df9601b256fa16a67ec97dee16daa62bfefc58ac39bd970b96'),
]:
    path = RAW / rel
    pinned(path, expected_hash)
    connection = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
    connection.row_factory = sqlite3.Row
    assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    counts = {}
    for table in ('operations', 'jobs'):
        counts[table] = dict(connection.execute('SELECT status,count(*) FROM ' + table + ' GROUP BY status'))
        assert counts[table] == {'FAILED': 1, 'SUCCEEDED': 8, 'UNKNOWN': 1}
    op = dict(connection.execute('SELECT * FROM operations WHERE operation_id=?', (OP,)).fetchone())
    job = dict(connection.execute('SELECT * FROM jobs WHERE job_id=?', (JOB,)).fetchone())
    assert op['status'] == job['status'] == 'UNKNOWN'
    assert op['finished_at'] is None and job['finished_at'] is None
    assert op['request_id'] == REQUEST
    assert op['idempotency_key'] == 'w23-science-idem-a8fcfcc1-57a7-4741-871a-d4bbb72e3e52'
    assert op['request_hash'] == '1549b132a08e00b5528495bf214aad40246380214ec8e768bab320854f5cc0cf'
    assert op['operation'] == 'operation_call'
    assert json.loads(op['metadata'])['arguments']['operation_id'] == 'study.run'
    result = json.loads(op['result'])
    assert result['success'] is False and result['error']['safe_retry'] is False
    data = result['data']
    assert data['dispatch_stage'] == 'post_dispatch' and data['execution_state_unknown'] is True
    assert data['applied_count'] == 1 and data['applied'][0]['method'] == 'study.run'
    witness = data['domain_outcome']['witness']
    mutations = [item for item in witness['dispatches'] if item.get('is_mutation')]
    assert len(mutations) == 1 and mutations[0]['method'] == 'run' and mutations[0]['command'] == 'call'
    assert witness['mutation_issued'] is True and witness['mutation_method'] == 'run'
    events = [dict(row) for row in connection.execute('SELECT * FROM job_events WHERE job_id=? ORDER BY id', (JOB,))]
    submissions = []
    observations = []
    for row in events:
        md = json.loads(row['metadata'])
        if row['event'] == 'worker_request' and md.get('metadata', {}).get('method') == 'run' and md.get('kind') == 'call':
            if md.get('phase') == 'submitted': submissions.append((row, md))
            if md.get('phase') == 'observed': observations.append((row, md))
    assert len(submissions) == len(observations) == 1
    presend, observed = submissions[0], observations[0]
    assert presend[0]['id'] == 178 and observed[0]['id'] == 179
    assert presend[1]['request_id'] == observed[1]['request_id'] == RPC
    assert presend[1]['request_hash'] == observed[1]['request_hash'] == '13425a77fd29e02c4a1727dee96014f6156ebd4a20dc9d50a3559132a7920d39'
    assert presend[1]['metadata']['handle'] == mutations[0]['receiver']
    reply = observed[1]['reply']
    assert observed[1]['status'] == reply['status'] == 'FAILED'
    assert reply['failure']['execution_state_unknown'] is True
    assert 'sigma' in reply['failure']['message'] and 'sol1/e1' in reply['failure']['message']
    connection.close()
    pinned(path, expected_hash)
    database_checks.append({'path': str(path), 'sha256': expected_hash, 'integrity_check': 'ok', 'status_counts': counts,
                            'actual_study_run_presend_count': len(submissions), 'study_run_status': 'UNKNOWN', 'safe_retry': False})

sys.path.insert(0, str(SOURCE))
spec = importlib.util.spec_from_file_location('root_frozen_w23_007', SOURCE / 'tools/run_native_w23_te_science.py')
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)
build = read(RUN / 'fixture_build.json')['readback']
material_gate = runner.validate_material_tensor_readback(build)
assert material_gate == read(RUN / 'material_tensor_readback_gate.json')
assert read(RUN / 'material_tensor_raw_readback.json')['materials'] == build['materials']
mesh_gate = runner.validate_mesh_readback(build['mesh'], 'coarse')
assert mesh_gate == read(RUN / 'fixture_build_mesh_readback_gate.json')
assert build['mesh']['elements'] == build['mesh']['tri_elements'] == 31496
assert build['mesh']['free_tri_selected_domains'] == [1,2,3,4,5,6]
raw_configured = read(RUN / 'pre_solve_study_configuration_job_terminal.json')['job']['result']['data']['readback']['readback']
study_gate = runner.validate_configured_study_features(raw_configured)
assert study_gate == read(RUN / 'pre_solve_study_configuration.json')['configured_study']

summary = read(RUN / 'summary.json')
assert summary['status'] == 'FAIL_OR_INCOMPLETE' and summary['study_run_submission_count'] == 0
rpc = summary['direct_rpc_reconciliation']
assert rpc['status'] == 'PASS_ALL_DIRECT_RPCS_TERMINAL' and rpc['rpc_count'] == 3 and rpc['event_count'] == 6
assert rpc['safe_for_owned_cleanup'] is True
assert not any(rpc[key] for key in ('ambiguous_rpc_ids','malformed_events','orphan_rpc_ids','pending_rpc_ids','unknown_rpc_ids'))
assert all(row['terminal'] and row['terminal_status'] == 'SUCCEEDED' and row['submitted_count'] == row['observed_count'] == 1 for row in rpc['records'])
gates = read(RUN / 'cleanup_gates.json')
assert gates['combined']['status'] == 'PASS_BOTH_RPC_JOURNALS_QUIESCENT' and gates['combined']['safe_for_owned_cleanup'] is True
idle = gates['daemon_idle_proof']
assert idle['idle'] and idle['durable_unknown_preserved'] and idle['worker_health_quiescent']
assert not any(idle[key] for key in ('malformed_worker_events','other_nonterminal_jobs','queued_or_running_jobs','unknown_job_request_gaps','worker_rpcs_unresolved'))
assert len(idle['worker_requests']) == 93
assert all(row['terminal'] and row['submitted_count'] == row['observed_count'] == 1 for row in idle['worker_requests'])
disconnect = read(RUN / 'cleanup_worker_disconnect.json')
before, decoded, after = [disconnect[key] for key in ('health_before_disconnect','decoded_remote_client_result','post_disconnect_health')]
assert before['connected'] is True and before['generation'] == 2
assert decoded['connected'] is False and after['connected'] is False and decoded['generation'] == after['generation'] == 3
assert before['instance_id'] == decoded['instance_id'] == after['instance_id'] == '19004245-c69b-422e-87d9-874b2bc360ea'
assert after['queued_or_running'] == 0 and after['status'] == 'HEALTHY'
cleanup = read(RUN / 'exact_owned_cleanup_receipt.json')
assert cleanup['status'] == 'CLEANUP_VERIFIED_EXACT_OWNED_PIDS_AND_PORTS_ABSENT'
assert cleanup['durable_unknown_preserved'] is True and cleanup['ledger_safe_for_owned_cleanup'] is True
assert cleanup['fresh_endpoint_identity_gate']['safe'] is True and not cleanup['fresh_endpoint_identity_gate']['foreign_client_pids']
assert not cleanup['ambiguous_descendants']
for prefix, pid, birth, image_sha in (
    ('server',38989,'Mon Oct 12 03:13:45 2026','cf66227c76edfc79735e84292b1d8563e3b058be1f029b6a550a33b76f2718eb'),
    ('worker',39087,'Mon Oct 12 03:13:49 2026','cc9937ae8ae3db3cc8ea26526bb6bdfca2bcf20901075f6acee7693d31ef4ec2'),
):
    current, expected = cleanup['identities'][prefix+'_current'], cleanup['identities'][prefix+'_expected']
    assert current['pid'] == expected['pid'] == pid and current['birth'] == expected['birth'] == birth
    assert current['command_sha256'] == expected['command_sha256'] == image_sha
    assert hashlib.sha256(current['command'].encode()).hexdigest() == image_sha
    assert cleanup['postcheck']['pids_absent'][prefix]['original_identity_absent'] is True
assert cleanup['postcheck']['pids_absent']['descendant_0']['pid'] == 39058
assert cleanup['postcheck']['pids_absent']['descendant_0']['original_identity_absent'] is True
assert all(cleanup['postcheck'][key]['absent'] is True for key in ('server_port','worker_port'))
budget = summary['birth_inclusive_budget']
assert budget['max_seconds'] == 1800 and budget['cleanup_reserve_seconds'] == 120 and budget['deadline_was_not_reset'] is True
assert budget['budget_exceeded'] is False and 0 < budget['elapsed_seconds_includes_cleanup'] < 1800
inventory_path = Path(supplement['cleanup']['fresh_postcleanup_inventory_path'])
pinned(inventory_path, '720d7296ac271061a998910b8ab66f99f4539dcd523f9baa2b9b6741e5f01e94')
inventory = read(inventory_path)
assert inventory['probes_ok'] is True and inventory['task_owned_pids'] == {'38989':False,'39058':False,'39087':False}
assert inventory['task_owned_ports'] == [63329,63341]
assert not inventory['task_owned_listener_hits'] and not inventory['engine_or_worker_process_hits']
for key in ('ps','lsof'):
    pinned(inventory['raw_private_paths'][key], inventory['raw_sha256'][key])
ps_text = Path(inventory['raw_private_paths']['ps']).read_text()
assert not re.search(r'/Applications/COMSOL(?:63|64)/|comsol_mcp\.worker_java\.PersistentComsolWorker|\bmphserver\b', ps_text)
for line in ps_text.splitlines():
    fields = line.split()
    if fields and fields[0].isdigit(): assert int(fields[0]) not in (38989,39058,39087)

receipt = {
    'schema_version':1, 'activity_id':'w23-native-run-20261012-007',
    'created_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'status':'PASS_ROOT_PRESERVATION_AND_CAPTURED_CLEANUP_EVIDENCE_CHECK',
    'checker_path':str(Path(__file__).resolve()), 'checker_sha256':sha(__file__),
    'inputs':{str(path):sha(path) for path in (manifest_path,supplement_path,reconciliation_path,inventory_path)},
    'copy_verification':{'base_files':82,'base_bytes':6538429,'separately_preserved_observer_created_sidecars':2,'total_files':84,'total_bytes':bytes_checked,
                         'source_and_copy_all_bytes_and_hashes_rechecked':True,'model_mph_count':0},
    'database_checks':database_checks,
    'study_run':{'operation_id':OP,'job_id':JOB,'request_id':REQUEST,'worker_request_id':RPC,
                 'presend_event_id':178,'observed_event_id':179,'actual_native_dispatch_count':1,
                 'witness_mutating_dispatch_count':1,'worker_terminal_status':'FAILED','durable_domain_status':'UNKNOWN',
                 'safe_retry':False,'raw_worker_error':observed[1]['reply']['failure']['message'],
                 'internal_solver_execution_count':'UNKNOWN','original_summary_submission_count':0,
                 'summary_zero_count_is_incorrect':True,'correction_scope':'append-only; original evidence remains unchanged'},
    'native_gates':{'material':material_gate['status'],'mesh':mesh_gate['status'],'pre_solve_study':study_gate['status'],
                    'mesh_triangles':31496,'validators_replayed_from_frozen_candidate15':True},
    'cleanup':{'journalled_worker_requests_reconciled':93,'direct_rpcs_reconciled':3,
               'real_worker_client_disconnect_generation_before':2,'generation_after':3,
               'verified_task_pids':[38989,39058,39087],'verified_task_ports':[63329,63341],
               'proof_scope':'Root rechecks immutable captured executor inventories and receipts; no new Root live ps/lsof probe in this check',
               'captured_postcleanup_inventory_sha256':sha(inventory_path),'birth_inclusive_elapsed_seconds':budget['elapsed_seconds_includes_cleanup'],
               'durable_unknown_was_not_changed':True},
    'native_scientific_acceptance':'NOT_ACCEPTED','overall_master_goal':'OPEN',
    'limitations':['No automatic replay of UNKNOWN Study.run','No completed solution or science acceptance','Gate PASS applies only to recorded Activity007 candidate15',
                   'Observer-created SQLite sidecars are preserved with explicit provenance; they are not original runner WAL evidence'],
}
dest = E / 'ROOT_PRESERVATION_CLEANUP_CHECK007.json'
with dest.open('x') as stream:
    json.dump(receipt, stream, ensure_ascii=False, indent=2); stream.write('\n')
print(json.dumps({'path':str(dest),'sha256':sha(dest),'status':receipt['status'],'files':84,'bytes':bytes_checked,'actual_study_run_count':1}))
