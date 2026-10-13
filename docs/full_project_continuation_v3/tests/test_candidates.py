from pathlib import Path
import hashlib,json,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import candidate_inventory as ci
import init_resume as ir
import test_continuation as tc

class CandidateInventoryTests(unittest.TestCase):
    def fixture(self,td):
        repo=Path(td)/'repo';(repo/'comsol_mcp').mkdir(parents=True)
        (repo/'comsol_mcp/a.py').write_text('x = 0\n')
        cont=repo/'continuation';(cont/'candidates').mkdir(parents=True);(cont/'checkpoint').mkdir()
        for name,value in [('windows-r19',1),('w23-r15',2)]:
            source=cont/'candidates'/name/('candidate' if name=='windows-r19' else '')
            (source/'comsol_mcp').mkdir(parents=True)
            (source/'comsol_mcp/a.py').write_text(f'x = {value}\n')
        (cont/'INTEGRATION_CONFLICTS.json').write_text(json.dumps([{'candidate':'Windows R19','path':'comsol_mcp/a.py','base_sha256':'not-used-as-proof','candidate_sha256':'not-used-as-proof','integration':'ROOT_DECISION_REQUIRED_NO_OVERLAY'}]))
        (cont/'checkpoint/CURRENT_STATE.json').write_text(json.dumps({'current_status':{'integration':'OPEN'},'active_jobs':[],'old_unknown':'historic UNKNOWN safe_retry=false'}))
        (cont/'LOCAL_ONLY_MANIFEST_R2.json').write_text(json.dumps({'path':'/does/not/exist/model.mph','sha256':'0'*64}))
        return repo,{'commit':'a'*40,'tree':'b'*40}
    def test_no_mutation_and_no_acceptance(self):
        with tempfile.TemporaryDirectory() as td:
            repo,pin=self.fixture(td);before={p.relative_to(repo):p.read_bytes() for p in repo.rglob('*') if p.is_file()}
            out=ci.inspect(repo,pin)
            self.assertFalse(out['source_modified']);self.assertFalse(out['automatic_merge_performed']);self.assertFalse(out['native_execution_certified'])
            self.assertEqual(before,{p.relative_to(repo):p.read_bytes() for p in repo.rglob('*') if p.is_file()})
    def test_actual_r15_difference_not_missing_from_old_map(self):
        with tempfile.TemporaryDirectory() as td:
            repo,pin=self.fixture(td);out=ci.inspect(repo,pin)
            self.assertEqual(out['declared_conflict_count'],1)
            self.assertEqual(len(out['cross_candidate_different_bytes']),1)
            self.assertEqual({r['candidate'] for r in out['actual_source_comparisons']},{'windows-r19','w23-r15'})
    def test_private_paths_not_followed(self):
        with tempfile.TemporaryDirectory() as td:
            repo,pin=self.fixture(td);out=ci.inspect(repo,pin)
            self.assertFalse(out['private_paths_followed']);self.assertTrue(out['maps_only_are_not_file_backups'])
            self.assertEqual(out['current_live_processes'],'NOT_OBSERVED')
    def test_candidate_only_not_automatically_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            repo,pin=self.fixture(td);(repo/'continuation/candidates/w23-r15/comsol_mcp/new.py').write_text('pass')
            out=ci.inspect(repo,pin);row=next(r for r in out['actual_source_comparisons'] if r['relative_path']=='comsol_mcp/new.py')
            self.assertEqual(row['observation'],'CANDIDATE_ONLY');self.assertEqual(row['integration_status'],'NOT_DECIDED_BY_TOOL')
    def test_wrong_map_schema_stops(self):
        with tempfile.TemporaryDirectory() as td:
            repo,pin=self.fixture(td);(repo/'continuation/INTEGRATION_CONFLICTS.json').write_text('{}')
            with self.assertRaises(ValueError):ci.inspect(repo,pin)
    def test_traversal_row_stops(self):
        with tempfile.TemporaryDirectory() as td:
            repo,pin=self.fixture(td);(repo/'continuation/INTEGRATION_CONFLICTS.json').write_text(json.dumps([{'candidate':'X','path':'../outside'}]))
            with self.assertRaises(ValueError):ci.inspect(repo,pin)
    def test_symlink_not_followed(self):
        with tempfile.TemporaryDirectory() as td:
            repo,pin=self.fixture(td);a=repo/'continuation/candidates/w23-r15/comsol_mcp/a.py';a.unlink()
            try:a.symlink_to(repo/'comsol_mcp/a.py')
            except OSError as e:self.skipTest(str(e))
            with self.assertRaises(ValueError):ci.inspect(repo,pin)
    def test_missing_source_tree_not_inferred_empty(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):ci.inspect(Path(td),{'commit':'a'*40,'tree':'b'*40})
    def test_nested_w24_source_compared_against_production(self):
        with tempfile.TemporaryDirectory() as td:
            repo,pin=self.fixture(td)
            nested=repo/'continuation/candidates/w24-coupled05/repository/comsol_mcp';nested.mkdir(parents=True)
            (nested/'a.py').write_text('x = 3\n')
            out=ci.inspect(repo,pin)
            row=next(r for r in out['actual_source_comparisons'] if r['candidate']=='w24-coupled05')
            self.assertEqual(row['relative_path'],'comsol_mcp/a.py')
            self.assertEqual(row['root_sha256'],ci.sha(repo/'comsol_mcp/a.py'))
            self.assertEqual(row['observation'],'DIFFERENT_BYTES')
            self.assertIn('/repository/comsol_mcp/',row['included_path'])
    def test_missing_published_nested_root_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            repo,pin=self.fixture(td)
            root=repo/'continuation/candidates/windows-r19';(root/'candidate').rename(root/'wrong-layout')
            with self.assertRaisesRegex(ValueError,'source root missing'):ci.inspect(repo,pin)

class CurrentCheckpointTests(unittest.TestCase):
    def fixture(self,td):
        repo,up,pin,spec,snapshot=tc.ContinuationTests().fixture(td)
        pin['checkpoint_path']='continuation/checkpoint/CURRENT_STATE.json'
        p=repo/pin['checkpoint_path'];p.parent.mkdir(parents=True)
        p.write_text(json.dumps({'current_status':{'integration':'INDEPENDENT_CANDIDATES_NOT_INTEGRATED'},'active_jobs':[],'old_unknown':'Activity020 UNKNOWN safe_retry=false'}))
        return repo,pin,spec,snapshot
    def test_latest_checkpoint_preserved_and_read(self):
        with tempfile.TemporaryDirectory() as td:
            repo,pin,spec,snapshot=self.fixture(td);p=repo/pin['checkpoint_path'];raw=p.read_bytes()
            ir.prepare(repo,pin,spec,snapshot);resume=ir.load(repo/pin['handoff_directory']/'state/RESUME.json')
            self.assertEqual(resume['latest_checkpoint_path'],pin['checkpoint_path'])
            self.assertFalse(resume['active_jobs_discovered']);self.assertIsNone(resume['active_job_refs'])
            self.assertEqual(resume['historical_unknown_statement'],'Activity020 UNKNOWN safe_retry=false')
            self.assertEqual(raw,p.read_bytes())
    def test_missing_latest_checkpoint_not_reset(self):
        with tempfile.TemporaryDirectory() as td:
            repo,pin,spec,snapshot=self.fixture(td);(repo/pin['checkpoint_path']).unlink()
            with self.assertRaises(ValueError):ir.prepare(repo,pin,spec,snapshot)
            self.assertFalse((repo/pin['handoff_directory']/'state/INITIALIZED.json').exists())
    def test_bad_latest_checkpoint_not_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            repo,pin,spec,snapshot=self.fixture(td);(repo/pin['checkpoint_path']).write_text('{"new_schema":true}')
            with self.assertRaises(ValueError):ir.prepare(repo,pin,spec,snapshot)
    def test_unchanged_original_contract(self):
        expected={'ORIGINAL_REQUIREMENTS.json': 'b74c9ab12a8f390c013f55fb4ef74b50f1d119330279fa0ebf5c1e586c603bb5', 'INHERITED_CONTRACT_LOCK.json': '163b62ae7684c749bfba62b3d007e933354a95e98291a5b093887c73eacbb0a8'}
        for name, digest in expected.items():
            self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(),digest)

if __name__=='__main__':unittest.main()
