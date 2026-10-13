import collections
import datetime
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

REPO = Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository')
E = REPO / 'docs/full_project_continuation_v2/evidence/solver_domain_integration_r1/final'
P = REPO / 'local-preservation/solver-domains-progress-20261012-r1'

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def load(path):
    return json.loads(Path(path).read_text())

freeze_path = E / 'FREEZE_MANIFEST.json'
assert sha(freeze_path) == '920bea6231a01af9b114ba8c345a1c92e4b394f461ebe7430dc0dfe3da7dde22'
freeze = load(freeze_path)
preservation_path = P / 'PRESERVATION.json'
assert sha(preservation_path) == freeze['preservation']['sha256'] == '9bb6a0e79a79ed0dfc4d7780b74c137aecf627c867b999bbc44d0287fd390546'
preservation = load(preservation_path)
assert len(preservation['files']) == 32
records = []
current_matches = []
for row in sorted(preservation['files'], key=lambda row: row['path']):
    rel = Path(row['path'])
    assert not rel.is_absolute() and '..' not in rel.parts
    path = P / 'files' / rel
    assert path.is_file() and not path.is_symlink()
    assert path.stat().st_size == row['size_bytes'] and sha(path) == row['sha256']
    records.append(f"{row['path']}\0{row['size_bytes']}\0{row['sha256']}\n")
    current_matches.append({'path':row['path'], 'matches_frozen_bytes_now':sha(REPO / rel) == row['sha256']})
fingerprint = hashlib.sha256(''.join(records).encode()).hexdigest()
assert fingerprint == preservation['source_scope_fingerprint'] == freeze['preservation']['source_scope_fingerprint'] == '37dd6fbf92f3b19f85dfb7340970a13f835f37f41ccc66ac5b13dc1a55cf9b82'
patch = REPO / freeze['scoped_patch']['path']
assert sha(patch) == 'e37f178f7df4cdd586d8419c4a9c2f23e847b9b023b005df73ae00f4618bc8ff'
test_results = {}
expected = {'owned_integration':(630,0,0,0),'w14_focused':(153,0,0,0),
            'desktop_capture_spawn_suite':(8,0,0,0),'historical_wrapper_combined_attempt':(995,6,0,10)}
for name, row in freeze['test_runs'].items():
    junit_path = REPO / row['junit']['path']
    assert sha(junit_path) == row['junit']['sha256'] and junit_path.stat().st_size == row['junit']['size_bytes']
    xml = ET.parse(junit_path).getroot()
    suites = [xml] if xml.tag == 'testsuite' else list(xml.findall('testsuite'))
    counts = tuple(sum(int(suite.get(key, '0')) for suite in suites) for key in ('tests','failures','errors','skipped'))
    assert counts == expected[name]
    guard_path = REPO / row['guard_log']['path']
    assert sha(guard_path) == row['guard_log']['sha256'] and guard_path.stat().st_size == row['guard_log']['size_bytes']
    events = [json.loads(line) for line in guard_path.read_text().splitlines() if line]
    assert len(events) == row['guard_events'] and events
    assert not any('deny' in str(event.get('event', '')).lower() for event in events)
    test_results[name] = {'counts_tests_failed_errors_skipped':counts, 'junit_sha256':sha(junit_path),
                          'guard_sha256':sha(guard_path), 'guard_event_count':len(events),
                          'evidence_scope':'historical failed attempt' if name.startswith('historical') else 'scoped software test'}
ssh_path = REPO / freeze['windows_readonly_preflight']['receipt']
assert sha(ssh_path) == '6354c26d2819ff35d773f80c3c046908f0c4beb148ffd6f48daac53cd983f4d5'
receipt = {
    'schema_version':1, 'created_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'status':'CHANGES_REQUIRED_ROOT_SOURCE_REVIEW', 'source_scope_fingerprint':fingerprint,
    'root_checked_frozen_files':32, 'preservation_manifest_sha256':sha(preservation_path),
    'freeze_sha256':sha(freeze_path), 'scoped_patch_sha256':sha(patch), 'test_results':test_results,
    'current_working_files_are_not_the_frozen_candidate':current_matches,
    'findings':[{
        'id':'SDP-R1-P1-PROGRESS-PATH-CAPABILITIES', 'priority':'P1',
        'file':'comsol_mcp/_comsol_progress_capture.py',
        'problem':'__enter__ validates the parent path then creates by pathname; _cleanup_scratch checks identities then unlinks/rmdirs by pathname. A parent or leaf replacement between check and mutation can write through a replaced parent or delete an unrelated replacement file/directory.',
        'required_fix':'Reuse pinned directory/handle-relative creation and cleanup capabilities. Refuse a changed parent or leaf, preserve foreign objects, and retain UNKNOWN on unverified post-dispatch cleanup. Windows must use the native handle adapter or fail closed.',
        'required_validation':'Deterministic parent symlink/reparse replacement and scratch/file swap after identity check; prove no foreign writes/deletes and no false cleanup PASS.',
        'owner':'windows_execute', 'original_frozen_evidence_preserved':True,
    }],
    'architectural_review':{
        'existing_serialized_worker_lane':'retained',
        'progress_job_binding':'exact persisted outer/nested operation, project/session/ModelRef/revision/request binding checked',
        'managed_study_and_convergence_capture':'software producer; actual COMSOL emission NOT_RUN',
        'recipe_progress_capture':'NOT_CAPTURED with explicit sink-ownership limitation',
        'disk_budget':'CHANGES_REQUIRED; 1MiB readback/persistence cap is not native temporary-file quota',
        'model_R3_and_R4':'concurrent owner scope excluded; pending stable whole-candidate closure',
        'Windows_readonly_probe':'BLOCKED_CONNECTIVITY; remote query NOT_RUN',
    },
    'independent_reviewer':'PENDING; Root source review is not independent final acceptance',
    'COMSOL_native_scientific_acceptance':'NOT_RUN_FOR_THIS_FROZEN_UNIT',
    'T037_OS_acceptance':'NOT_RUN', 'MASTER_GOAL':'OPEN',
    'checker_path':str(Path(__file__).resolve()), 'checker_sha256':sha(__file__),
}
out = E / 'ROOT_REVIEW_R1.json'
with out.open('x') as stream:
    json.dump(receipt,stream,ensure_ascii=False,indent=2); stream.write('\n')
print(json.dumps({'receipt':str(out),'sha256':sha(out),'status':receipt['status'],'frozen_files':32,'fingerprint':fingerprint}))
