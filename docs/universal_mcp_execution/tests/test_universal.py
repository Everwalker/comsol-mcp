from __future__ import annotations
import hashlib,json,os,shutil,tempfile,unittest
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import restore_candidates as cas
import init_state as st
import verify_package as vp

def digest(b):return hashlib.sha256(b).hexdigest()

def make_cas(root,rows=None):
    repo=root/'repo';base=repo/'checkpoint';base.mkdir(parents=True)
    original=rows or [('lane/source.py',b'raise RuntimeError("DO NOT EXECUTE")\n'),('lane2/source.py',b'raise RuntimeError("DO NOT EXECUTE")\n')]
    records=[]
    for name,data in original:
        h=digest(data);obj=base/'objects'/h;obj.parent.mkdir(exist_ok=True);obj.write_bytes(data)
        records.append({'path':name,'object':'objects/'+h,'bytes':len(data),'sha256':h,'lane':name.split('/')[0]})
    m={'schema':'CAS_TEST','files':records,'tools':{}};raw=json.dumps(m).encode();(base/'M.json').write_bytes(raw)
    pin={'commit':'a'*40,'portable_candidates':{'path':'checkpoint','manifest':'M.json','manifest_sha256':digest(raw),'schema':'CAS_TEST','expected_files':len(records),'expected_unique_objects':len(set(r['sha256'] for r in records))}}
    return repo,base,pin,m

def change_manifest(base,pin,m):
    raw=json.dumps(m).encode();(base/'M.json').write_bytes(raw);pin['portable_candidates']['manifest_sha256']=digest(raw)

class CandidateTests(unittest.TestCase):
    def test_exact_restore_no_execution(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);repo,base,pin,m=make_cas(root);out=root/'新目录 空格';r=cas.restore(repo,out,pin)
            self.assertEqual(r['file_count'],2);self.assertEqual(r['unique_objects'],1);self.assertFalse(r['source_executed']);self.assertFalse(r['runtime_authority_restored'])
            self.assertEqual((out/'lane/source.py').read_bytes(),b'raise RuntimeError("DO NOT EXECUTE")\n')
    def test_manifest_tamper_rejected_before_target(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);repo,b,p,m=make_cas(root);(b/'M.json').write_text('{}')
            with self.assertRaises(ValueError):cas.restore(repo,root/'out',p)
            self.assertFalse((root/'out').exists())
    def test_object_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);repo,b,p,m=make_cas(root);(b/m['files'][0]['object']).write_bytes(b'bad')
            with self.assertRaises(ValueError):cas.restore(repo,root/'out',p)
            self.assertFalse((root/'out').exists())
    def test_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);repo,b,p,m=make_cas(root);m['files'][0]['path']='../outside';change_manifest(b,p,m)
            with self.assertRaises(ValueError):cas.restore(repo,root/'out',p)
            self.assertFalse((root/'outside').exists())
    def test_case_collision(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);repo,b,p,m=make_cas(root);m['files'][1]['path']='LANE/source.py';change_manifest(b,p,m)
            with self.assertRaises(ValueError):cas.restore(repo,root/'out',p)
    def test_object_count_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);repo,b,p,m=make_cas(root);p['portable_candidates']['expected_unique_objects']=2
            with self.assertRaises(ValueError):cas.restore(repo,root/'out',p)
    def test_existing_target_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);repo,b,p,m=make_cas(root);out=root/'out';out.mkdir();(out/'keep').write_text('important')
            with self.assertRaises(ValueError):cas.restore(repo,out,p)
            self.assertEqual((out/'keep').read_text(),'important')
    def test_bound_helper_tamper(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);repo,b,p,m=make_cas(root);(b/'helper.py').write_text('not executed');m['tools']={'helper.py':{'sha256':'0'*64}};change_manifest(b,p,m)
            with self.assertRaises(ValueError):cas.restore(repo,root/'out',p)

class StateTests(unittest.TestCase):
    def fixture(self,root):
        pin=json.loads((ROOT/'PIN.json').read_text());repo=root/'repo';overlay=repo/pin['handoff_directory'];overlay.mkdir(parents=True)
        (overlay/'RESTORE_RECEIPT.json').write_text(json.dumps({'commit':pin['commit'],'tree':pin['tree'],'status':'PINNED_SOURCE_RESTORED'}))
        catalog=repo/'docs/comsol_mcp_design_v1/02_ACTION_CATALOG.json';catalog.parent.mkdir(parents=True,exist_ok=True)
        catalog.write_text(json.dumps({'operation_count':2,'operations':[{'operation_id':'node.inspect','domain':'node'},{'operation_id':'code.execute_java','domain':'code'}]}))
        old=repo/'docs/full_project_continuation_v3/state';old.mkdir(parents=True);(old/'RESUME.json').write_text('{"do_not_replace":true}')
        return repo,overlay,catalog,old
    def test_new_state_not_pass_and_old_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            repo,overlay,cat,old=self.fixture(Path(d));before=(old/'RESUME.json').read_bytes();r=st.initialize(repo)
            self.assertEqual(r['actions'],2);self.assertEqual(r['groups'],12);self.assertEqual(before,(old/'RESUME.json').read_bytes())
            state=overlay/'state';rows=json.loads((state/'DOD_STATUS.json').read_text())['rows'];self.assertTrue(all(x['status']=='NOT_RUN' for x in rows))
            self.assertFalse(json.loads((state/'RESUME.json').read_text())['native_execution_claimed'])
    def test_reinit_preserves_progress(self):
        with tempfile.TemporaryDirectory() as d:
            repo,overlay,cat,old=self.fixture(Path(d));st.initialize(repo);p=overlay/'state/RESUME.json';data=json.loads(p.read_text());data['next_action']='custom next';p.write_text(json.dumps(data));before=p.read_bytes()
            r=st.initialize(repo);self.assertEqual(r['status'],'EXISTING_STATE_PRESERVED');self.assertEqual(before,p.read_bytes())
    def test_catalog_count_disagreement_refused(self):
        with tempfile.TemporaryDirectory() as d:
            repo,overlay,cat,old=self.fixture(Path(d));v=json.loads(cat.read_text());v['operation_count']=272;cat.write_text(json.dumps(v))
            with self.assertRaises(ValueError):st.initialize(repo)
            self.assertFalse((overlay/'state').exists())
    def test_duplicate_catalog_refused(self):
        with tempfile.TemporaryDirectory() as d:
            repo,overlay,cat,old=self.fixture(Path(d));v=json.loads(cat.read_text());v['operations'][1]['operation_id']=v['operations'][0]['operation_id'];cat.write_text(json.dumps(v))
            with self.assertRaises(ValueError):st.initialize(repo)
    def test_wrong_baseline_refused(self):
        with tempfile.TemporaryDirectory() as d:
            repo,overlay,cat,old=self.fixture(Path(d));p=overlay/'RESTORE_RECEIPT.json';v=json.loads(p.read_text());v['commit']='0'*40;p.write_text(json.dumps(v))
            with self.assertRaises(ValueError):st.initialize(repo)
    def test_frozen_scope_hashes_match(self):
        lock=st.validate_contract(ROOT);self.assertEqual(len(lock['files']),5)
    def test_contract_tamper_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'CONTRACT_LOCK.json').write_text(json.dumps({'files':[{'path':'scope.md','sha256':'0'*64}]}));(root/'scope.md').write_text('changed')
            with self.assertRaises(ValueError):st.validate_contract(root)

class ContractTests(unittest.TestCase):
    def test_goal_short(self):
        goal=(ROOT/'GOAL.txt').read_text().strip();self.assertTrue(goal.startswith('/goal '));self.assertLessEqual(len(goal),100)
    def test_finite_dod_and_heldout(self):
        d=json.loads((ROOT/'config/UNIVERSAL_DOD.json').read_text());self.assertEqual(len(d['groups']),12);self.assertEqual(len({x['id'] for x in d['groups']}),12);self.assertEqual(d['heldout_task_families'],2);self.assertFalse(d['domain_science_blocks_core_release'])
    def test_required_platforms_and_skips(self):
        t=json.loads((ROOT/'config/TARGETS.json').read_text())['targets'];self.assertEqual(len(t),6);self.assertEqual(sum(r['native']=='REQUIRED' for r in t),3)
        self.assertTrue(all(r['distribution'] for r in t))
    def test_not_independent_developer_requirement(self):
        self.assertFalse((ROOT/'roles/DEVELOPER.md').exists());self.assertTrue((ROOT/'roles/MAIN.md').exists());self.assertTrue((ROOT/'roles/REVIEWER.md').exists())
    def test_manifest_good_and_bad(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);raw=b'fixture';(root/'x').write_bytes(raw);(root/'PACKAGE_MANIFEST.json').write_text(json.dumps({'files':[{'path':'x','bytes':len(raw),'sha256':digest(raw)}]}))
            self.assertEqual(vp.verify(root)['status'],'WORKPACK_INTEGRITY_PASS');(root/'x').write_bytes(b'tamper');self.assertEqual(vp.verify(root)['status'],'FAIL')
    def test_no_actual_runtime_fields_in_template(self):
        text=(ROOT/'config/UNIVERSAL_DOD.json').read_text();self.assertNotIn('"status": "PASS"',text)

if __name__=='__main__':unittest.main()
