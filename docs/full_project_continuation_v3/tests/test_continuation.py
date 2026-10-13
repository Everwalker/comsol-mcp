from pathlib import Path
import collections,copy,hashlib,importlib.util,json,shutil,subprocess,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import init_resume as ir
import checkpoint as cp
import preclean_inventory as pc

class ContinuationTests(unittest.TestCase):
    def fixture(self,root):
        repo=Path(root)/'repo';base=repo/'docs/original';up=repo/'docs/full_project_execution';base.mkdir(parents=True);(up/'state').mkdir(parents=True)
        originals={
            '02_ACTION_CATALOG.json':json.dumps({'operations':[{'operation_id':'x.read'}]}).encode(),
            '03_ACCEPTANCE.json':json.dumps({'tests':[{'id':'T001'}],'matrix':['windows-x64/comsol6.3','macos-arm64/comsol6.3']}).encode(),
            '04_IMPLEMENTATION_PLAN.md':b'| W01 test | G0 | none | T001 | original |\n'}
        spec={'base_dir':'docs/original','files':[],'expected_counts':{'work_packages':1,'operations':1,'tests':1,'platform_version_targets':2}}
        for n,data in originals.items():
            (base/n).write_bytes(data);spec['files'].append({'path':n,'git_blob':ir.blob(data)})
        data={'decisions':[{'decision':'USER_REQUESTED_SKIP','targets':['macos-arm64/comsol6.3']}]}
        raw=json.dumps(data).encode();(up/'USER_SCOPE_DECISIONS.json').write_bytes(raw)
        snapshot={'source_git_blob':ir.blob(raw),'decisions':data['decisions']}
        current={
            'RESUME.json':{'status':'PAUSED','native':'UNKNOWN','safe_retry':False,'previous_secret_ref':'local-only-not-loaded'},
            'TASKS.json':{'tasks':[{'id':'W01','title':'test','status':'PARTIAL_IMPLEMENTATION','next_action':'continue','blockers':['old']} ]},
            'ACTION_COVERAGE.json':{'actions':[{'operation_id':'x.read','targets':{'windows-x64/comsol6.3':{'status':'SOFTWARE_PASS'},'macos-arm64/comsol6.3':{'status':'USER_REQUESTED_SKIP'}}}]},
            'ACCEPTANCE_MATRIX.json':{'tests':[{'id':'T001','targets':{'windows-x64/comsol6.3':{'status':'NOT_RUN'},'macos-arm64/comsol6.3':{'status':'USER_REQUESTED_SKIP'}}}]}}
        for name,obj in current.items():(up/'state'/name).write_text(json.dumps(obj),encoding='utf-8')
        pin={'commit':'a'*40,'handoff_directory':'docs/full_project_continuation_v3','upstream_execution_directory':'docs/full_project_execution'}
        return repo,up,pin,spec,snapshot
    def run_prepare(self,repo,pin,spec,snapshot):return ir.prepare(repo,pin,spec,snapshot)
    def test_preserves_all_upstream_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            repo,up,pin,spec,snap=self.fixture(td);before={str(p):p.read_bytes() for p in up.rglob('*') if p.is_file()}
            r=self.run_prepare(repo,pin,spec,snap)
            self.assertFalse(r['upstream_state_mutated']);self.assertFalse(r['native_execution_certified'])
            self.assertEqual(before,{str(p):p.read_bytes() for p in up.rglob('*') if p.is_file()})
    def test_skip_is_not_pass(self):
        with tempfile.TemporaryDirectory() as td:
            repo,up,pin,spec,snap=self.fixture(td);self.run_prepare(repo,pin,spec,snap)
            idx=ir.load(repo/pin['handoff_directory']/'state/CURRENT_INDEX.json')
            self.assertFalse(idx['scope']['waived_is_pass']);self.assertEqual(idx['scope']['enabled_native_target_labels'],['windows-x64/comsol6.3'])
    def test_existing_new_progress_not_reset(self):
        with tempfile.TemporaryDirectory() as td:
            repo,up,pin,spec,snap=self.fixture(td);self.run_prepare(repo,pin,spec,snap)
            resume=repo/pin['handoff_directory']/'state/RESUME.json'
            cp.update(resume,'W23','fix-native','perform one bounded readback')
            before=resume.read_bytes();r=self.run_prepare(repo,pin,spec,snap)
            self.assertEqual(r['status'],'EXISTING_CONTINUATION_PRESERVED');self.assertEqual(resume.read_bytes(),before)
    def test_empty_existing_source_not_treated_as_new(self):
        with tempfile.TemporaryDirectory() as td:
            repo,up,pin,spec,snap=self.fixture(td);(up/'state/RESUME.json').write_text('')
            with self.assertRaises(ValueError):self.run_prepare(repo,pin,spec,snap)
            self.assertFalse((repo/pin['handoff_directory']/'state/INITIALIZED.json').exists())
    def test_missing_state_fails_without_reset(self):
        with tempfile.TemporaryDirectory() as td:
            repo,up,pin,spec,snap=self.fixture(td);(up/'state/TASKS.json').unlink()
            with self.assertRaises(ValueError):self.run_prepare(repo,pin,spec,snap)
    def test_unknown_schema_rejected_not_replaced(self):
        with tempfile.TemporaryDirectory() as td:
            repo,up,pin,spec,snap=self.fixture(td);f=up/'state/TASKS.json';f.write_text('{"another_schema": []}');raw=f.read_bytes()
            with self.assertRaises(ValueError):self.run_prepare(repo,pin,spec,snap)
            self.assertEqual(raw,f.read_bytes())
    def test_original_contract_change_detected(self):
        with tempfile.TemporaryDirectory() as td:
            repo,up,pin,spec,snap=self.fixture(td);(repo/'docs/original/04_IMPLEMENTATION_PLAN.md').write_text('changed')
            with self.assertRaises(ValueError):self.run_prepare(repo,pin,spec,snap)
    def test_exact_frozen_contract_indexes_without_mutating_production(self):
        with tempfile.TemporaryDirectory() as td:
            repo,up,pin,spec,snap=self.fixture(td)
            production=repo/'docs/original/02_ACTION_CATALOG.json';original=production.read_bytes()
            frozen=repo/'frozen';frozen.mkdir();(frozen/production.name).write_bytes(original)
            production.write_text('{"operations": [{"operation_id": "x.read"}, {"operation_id": "new.action"}]}')
            before={str(p):p.read_bytes() for p in up.rglob('*') if p.is_file()};evolved=production.read_bytes()
            ir.prepare(repo,pin,spec,snap,frozen)
            self.assertEqual(production.read_bytes(),evolved)
            self.assertEqual(before,{str(p):p.read_bytes() for p in up.rglob('*') if p.is_file()})
            idx=ir.load(repo/pin['handoff_directory']/'state/CURRENT_INDEX.json')
            self.assertEqual(idx['counts']['operations'],1)
            ref=idx['original_contract_hashes'][production.name]
            self.assertEqual(ref['sha256'],ir.digest(original));self.assertEqual(ref['working_source_sha256'],ir.digest(evolved))
            self.assertEqual(ref['frozen_basis'],'EXACT_LOCKED_LOCAL_FROZEN_COPY')
    def test_bad_frozen_copy_rejected_before_any_state_write(self):
        with tempfile.TemporaryDirectory() as td:
            repo,up,pin,spec,snap=self.fixture(td)
            production=repo/'docs/original/02_ACTION_CATALOG.json';production.write_text('changed')
            frozen=repo/'frozen';frozen.mkdir();(frozen/production.name).write_text('wrong frozen bytes')
            with self.assertRaisesRegex(ValueError,'blob mismatch'):ir.prepare(repo,pin,spec,snap,frozen)
            self.assertFalse((repo/pin['handoff_directory']/'state').exists())
    def test_missing_or_external_frozen_copy_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            repo,up,pin,spec,snap=self.fixture(td)
            production=repo/'docs/original/02_ACTION_CATALOG.json';raw=production.read_bytes();production.write_text('changed')
            frozen=repo/'frozen';frozen.mkdir()
            with self.assertRaisesRegex(ValueError,'missing'):ir.prepare(repo,pin,spec,snap,frozen)
            external=Path(td)/'external';external.mkdir();(external/production.name).write_bytes(raw)
            with self.assertRaises(ValueError):ir.prepare(repo,pin,spec,snap,external)
            self.assertFalse((repo/pin['handoff_directory']/'state').exists())
    def test_scope_change_requires_reconcile(self):
        with tempfile.TemporaryDirectory() as td:
            repo,up,pin,spec,snap=self.fixture(td);(up/'USER_SCOPE_DECISIONS.json').write_text('{"decisions": []}')
            with self.assertRaises(ValueError):self.run_prepare(repo,pin,spec,snap)
    def test_original_ids_cannot_disappear(self):
        with tempfile.TemporaryDirectory() as td:
            repo,up,pin,spec,snap=self.fixture(td);(up/'state/ACTION_COVERAGE.json').write_text('{"actions": []}')
            with self.assertRaises(ValueError):self.run_prepare(repo,pin,spec,snap)
    def test_partial_overlay_not_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            repo,up,pin,spec,snap=self.fixture(td);st=repo/pin['handoff_directory']/'state';st.mkdir(parents=True);(st/'RESUME.json').write_text('protected')
            with self.assertRaises(ValueError):self.run_prepare(repo,pin,spec,snap)
            self.assertEqual((st/'RESUME.json').read_text(),'protected')
    def test_unknown_job_pointer_preserved_on_checkpoint(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'resume.json';p.write_text(json.dumps({'active_job_refs':[{'job_id':'fake-j','status':'UNKNOWN','safe_retry':False}], 'blockers':['example']}))
            r=cp.update(p,'phase','task','next')
            self.assertEqual(r['active_job_refs'][0]['status'],'UNKNOWN');self.assertFalse(r['active_job_refs'][0]['safe_retry']);self.assertEqual(r['blockers'],['example'])
    def test_inherited_contract_bytes_from_original_package(self):
        lock=ir.load(ROOT/'INHERITED_CONTRACT_LOCK.json')
        # Original format is itself retained; verify every locked entry.
        rows=lock.get('files',[])
        self.assertEqual(len(rows),6)
        for row in rows:
            p=ROOT/row['path'];self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),row['sha256'])
    def test_goal_short_and_full_scope(self):
        goal=(ROOT/'GOAL.txt').read_text().strip();self.assertLessEqual(len(goal),100);self.assertIn('W01',goal);self.assertIn('W26',goal);self.assertIn('Reviewer',goal)
    def test_no_old_zero_initialization_script(self):
        self.assertFalse((ROOT/'tools/init_completion.py').exists())
        self.assertIn('init_resume.py',(ROOT/'tools/bootstrap.py').read_text())
    def test_recovery_overlay_different_from_existing_state(self):
        pin=ir.load(ROOT/'PIN.json');self.assertNotEqual(pin['handoff_directory'],pin['upstream_execution_directory'])
        self.assertIn('docs/full_project_continuation_v3',pin['handoff_directory'])
    @unittest.skipUnless(shutil.which('git'),'git unavailable')
    def test_preclean_only_metadata_never_authorizes_delete(self):
        with tempfile.TemporaryDirectory() as td:
            r=Path(td)
            for cmd in (['git','init','--template='],['git','config','user.name','Fixture'],['git','config','user.email','fixture@example.invalid']):subprocess.run(cmd,cwd=r,check=True,capture_output=True)
            (r/'tracked.txt').write_text('initial');(r/'.gitignore').write_text('ignored/\n');subprocess.run(['git','add','.'],cwd=r,check=True,capture_output=True)
            subprocess.run(['git','-c','commit.gpgsign=false','commit','-m','fixture'],cwd=r,check=True,capture_output=True)
            (r/'tracked.txt').write_text('modified');(r/'untracked.mph').write_bytes(b'not-a-real-model');(r/'ignored').mkdir();(r/'ignored/secret.txt').write_text('synthetic')
            result=pc.collect(r)
            self.assertFalse(result['safe_to_delete']);self.assertFalse(result['backup_created']);self.assertFalse(result['file_contents_read'])
            self.assertIn('tracked.txt',result['modified_or_staged_paths']);self.assertIn('untracked.mph',result['untracked_paths_or_directories']);self.assertTrue((r/'ignored/secret.txt').exists())

if __name__=='__main__':unittest.main()
