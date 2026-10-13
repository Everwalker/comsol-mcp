from pathlib import Path
import ast, copy, datetime, hashlib, json, os, struct, subprocess, sys
ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT.parents[1]
sys.path.insert(0, str(ROOT))
from tools import run_native_w23_saved_mph_diagnostic as runner
OLD = BASE / 'local-preservation/w23-saved-mph-science-readback-20261012-candidate20-r8-complete1'
OLD_E = OLD / 'docs/full_project_execution/w23_overlap/evidence/native-run-20261012-017-saved-mph-zero-solve-candidate20-r8-final1'
FAILED = OLD_E.with_name(OLD_E.name.replace('final1', 'final2'))
E = runner.EVIDENCE_ROOT / 'native-run-20261013-018-saved-mph-zero-solve-candidate20-r10-final1'
PACKET = ROOT / 'root-review-packet'
sha = runner.sha256_file
ref = lambda p: {'path': str(p), 'bytes': p.stat().st_size, 'sha256': sha(p)}
old = json.loads((FAILED / 'freeze.json').read_text())
assert runner.ACTIVITY_ID.endswith('20261013-018') and runner.CANDIDATE_REVISION == 'candidate20-r10'
assert not E.exists()
old_sources = old['source_files']
current = runner._source_files()
modified = [key for key in old_sources if current[key]['sha256'] != old_sources[key]['sha256']]
added = sorted(set(current) - set(old_sources))
assert set(modified) == {'tools/run_native_w23_saved_mph_diagnostic.py','tests/test_native_w23_saved_mph_diagnostic.py'}, modified
assert added == ['tests/test_saved_mph_offline_materialization.py'], added
assert len(current) == 404
for row in current.values():
    p = Path(row['path'])
    if p.suffix == '.py': ast.parse(p.read_bytes(), filename=str(p))
old_compile = old['offline_preflight']['compile']
for record in old_compile['compiled_sources'].values():
    assert sha(Path(record['source'])) == record['source_sha256']
    relative = Path(record['source']).relative_to(OLD)
    assert sha(ROOT / relative) == record['source_sha256']

injection = {'boundary': 'Only _compile_and_test orchestration replaced for this source-only preparation.',
    'real_prepare': True, 'real_check_offline_receipts_not_mocked': True,
    'new_actual_SDK_compile': True, 'new_actual_control_count':33, 'old466_all_rerun': False, 'original31_affected_rerun': True, 'original435_unaffected_inherited': True,
    'inherited435_scope': '435 unaffected cases from original466; exact old raw JUnit/guard and original test source region fingerprints. Actual31 affected originals plus2 material controls rerun now.',
    'modified_sources': modified, 'added_sources': added, 'unchanged_original_sources': 401,
    'no_native_execution': True}


def source_only_gates(evidence):
    offline = evidence / 'offline_gates'
    offline.mkdir(exist_ok=False)
    compile_receipt = runner.science._compile_sources(offline / 'compile')
    runner.write_json(offline / 'compile.json', compile_receipt)
    comparison = []
    for label, record in compile_receipt['compiled_sources'].items():
        old_record = old_compile['compiled_sources'][label]
        assert record['source_sha256'] == old_record['source_sha256']
        assert record['classes'] == old_record['classes'], label
        for rel, expected in record['classes'].items():
            original = OLD_E / 'offline_gates/compile' / label / 'classes' / rel
            actual = offline / 'compile' / label / 'classes' / rel
            assert actual.read_bytes() == original.read_bytes()
            assert struct.unpack('>H', actual.read_bytes()[6:8])[0] == 55
            comparison.append({'label': label, 'relative': rel, 'original': ref(original),
                               'new': ref(actual), 'exact_original_bytes': True, 'major': 55})
    assert len(comparison) == 19
    runner.write_json(offline / 'EXACT_19_ORIGINAL_CLASS_COMPARISON.json', comparison)
    env = runner._strict_test_environment(offline)
    env['W23_OFFLINE_CLASS_TEST_SEED'] = str(offline / 'compile')
    env['W23_OFFLINE_CLASS_TEST_RECEIPT'] = str(offline / 'compile.json')
    env['W23_OFFLINE_CLASS_TEST_EVIDENCE'] = str(evidence / 'materialization-controls')
    log = Path(env['CODEX_NETWORK_GUARD_LOG'])
    log.touch(exist_ok=False)
    command = [sys.executable, '-m', 'pytest', '-q', str(ROOT / 'tests/test_native_w23_saved_mph_diagnostic.py'), str(ROOT / added[0]), '-k', 'production_execute_prebirth or production_cli or real_prepare or metadata_only_successor',
               '--junitxml', str(offline / 'affected_materialization_junit.xml')]
    result = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, timeout=180)
    (offline / 'affected_materialization.stdout.txt').write_text(result.stdout)
    (offline / 'affected_materialization.stderr.txt').write_text(result.stderr)
    controls = {'status': 'PASS_AFFECTED_REAL_PREPARE_PREFLIGHT_CONTROLS' if result.returncode == 0 else 'FAIL',
        'command': command, 'returncode': result.returncode,
        'environment_boundary': {k: env[k] for k in ('PYTHONPATH','CODEX_NETWORK_GUARD_LOG','W23_OFFLINE_CLASS_TEST_SEED','W23_OFFLINE_CLASS_TEST_RECEIPT','W23_OFFLINE_CLASS_TEST_EVIDENCE')},
        'stdout': ref(offline / 'affected_materialization.stdout.txt'),
        'stderr': ref(offline / 'affected_materialization.stderr.txt'),
        'junit': ref(offline / 'affected_materialization_junit.xml'), 'guard': ref(log),
        'real_path_gate_mocked': False, 'native_server_worker_started': False}
    runner.write_json(offline / 'AFFECTED_CONTROLS_RECEIPT.json', controls)
    assert result.returncode == 0, result.stdout + result.stderr
    rows = [json.loads(line) for line in log.read_text().splitlines() if line]
    assert any(row['event'] == 'GUARD_ACTIVE_BEFORE_CHILD_ENTRYPOINT' for row in rows)
    assert not any('DENY' in row['event'] for row in rows)
    inherited = copy.deepcopy(old['offline_preflight'])
    focused = inherited['focused_tests']
    test_source_inheritance = []
    for path in focused['test_paths']:
        original = Path(path)
        current_path = ROOT / original.relative_to(OLD)
        if original.name == 'test_native_w23_saved_mph_diagnostic.py':
            old_region = original.read_bytes().split(b'def _actual_execute_prebirth_fixture')[0]
            new_region = current_path.read_bytes().split(b'def _actual_execute_prebirth_fixture')[0]
            assert old_region == new_region
            test_source_inheritance.append({'original':ref(original),'current':ref(current_path),'scope':'Unaffected22cases before prebirth fixture boundary are byte-identical;31 affected cases explicitly rerun','unaffected_region_sha256':hashlib.sha256(old_region).hexdigest()})
        else:
            assert sha(original) == sha(current_path)
            test_source_inheritance.append({'original':ref(original),'current':ref(current_path),'scope':'Exact whole test source unchanged'})
    import xml.etree.ElementTree as ET
    cases = list(ET.parse(focused['junit_path']).iter('testcase'))
    affected = [c for c in cases if c.attrib['name'].startswith(('test_production_execute_prebirth','test_production_cli'))]
    assert len(cases)==466 and len(affected)==31
    unaffected = [c.attrib for c in cases if c not in affected]
    assert len(unaffected)==435
    inheritance={'original_cases':466,'inherited_unaffected':435,'original_affected_rerun':31,'added_material_controls':2,'test_source_bindings':test_source_inheritance,'unaffected_case_identities':unaffected,'original_junit':ref(Path(focused['junit_path']))}
    runner.write_json(offline/'PRECISE_435_UNAFFECTED_INHERITANCE.json',inheritance)
    assert sha(Path(focused['junit_path'])) == focused['junit_sha256']
    assert sha(Path(focused['network_guard']['path'])) == focused['network_guard']['sha256']
    inherited['compile'] = compile_receipt
    inherited['focused_tests']['execution_provenance'] = 'INHERITED_435_UNAFFECTED_FROM_OLD466_ORIGINAL31_RERUN_CURRENT_PLUS2_SEPARATE'
    inherited['focused_tests']['inherited_unaffected_count']=435
    inherited['focused_tests']['original_affected_rerun_count']=31
    inherited['precise_inheritance']=ref(offline/'PRECISE_435_UNAFFECTED_INHERITANCE.json')
    inherited['preparation_orchestration_boundary'] = injection
    inherited['affected_controls'] = controls
    runner.write_json(offline / 'SOURCE_ONLY_GATE_INHERITANCE_BOUNDARY.json', injection)
    return inherited


original_gates = runner._compile_and_test
try:
    runner._compile_and_test = source_only_gates
    prepared = runner.prepare(E,
        work=Path('/private/tmp/comsol-mcp-w23-te-saved-mph-zero-solve-readback-20261013-018-candidate20-r10'),
        execution_evidence=E / 'native-execute')
finally:
    runner._compile_and_test = original_gates
freeze = Path(prepared['freeze_path'])
validated = runner._validate_freeze(freeze, prepared['freeze_sha256'])
material = runner.science._check_offline_receipts(freeze, validated)
assert all(Path(row['class_dir']).is_relative_to(E) for row in material['compiled_class_outputs'].values())
summary = {'schema': 'ACTIVITY018_SOURCE_SDK_MATERIAL_READY_REVIEW/1',
    'status': 'PASS_SOURCE_SDK_AND_REAL_RELATIVE_PATH_PREFLIGHT_ONLY_NO_NATIVE_ADMISSION',
    'created_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'activity_id': runner.ACTIVITY_ID, 'candidate_revision': runner.CANDIDATE_REVISION,
    'prepare_result': prepared, 'boundary_injection': injection,
    'actual_production_preflight': material, 'own_freeze': ref(freeze),
    'own_compile_receipt': ref(E / 'offline_gates/compile.json'),
    'new19_byte_comparison': ref(E / 'offline_gates/EXACT_19_ORIGINAL_CLASS_COMPARISON.json'),
    'affected_controls': ref(E / 'offline_gates/AFFECTED_CONTROLS_RECEIPT.json'),
    'parent_failed_freeze': ref(FAILED / 'freeze.json'),
    'old017_once_consumed': True, 'old017_not_retried': True, 'native': 'NOT_RUN',
    'science': 'NOT_ACCEPTED', 'native_approval': 'NOT_ISSUED', 'RESUME': 'NOT_MODIFIED'}
runner.write_json(PACKET / 'SOURCE_SDK_PREPARE_RECEIPT.json', summary)
print(json.dumps(ref(PACKET / 'SOURCE_SDK_PREPARE_RECEIPT.json')))
