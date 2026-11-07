from __future__ import annotations
import hashlib,io,json,os,shutil,stat,subprocess,sys,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import bootstrap as b

def blob(v):return hashlib.sha1(b'blob '+str(len(v)).encode()+b'\0'+v).hexdigest()

class RecoveryTests(unittest.TestCase):
    def test_empty_tree(self):self.assertEqual(b.tree_digest([]),'4b825dc642cb6eb9a060e54bf8d69288fbee4904')
    def test_path_rejection(self):
        for n in ('../x','/x','.git/a','a\\b','x:ads','CON','NUL.txt','a//b','a.','a ','a/./b'):
            with self.subTest(n=n),self.assertRaises(ValueError):b.valid_path(n)
    def test_unicode_path(self):self.assertEqual(b.valid_path('目录 空格/测试.txt'),('目录 空格','测试.txt'))
    def test_case_collisions(self):
        for paths in (['A','a'],['d/x','D/y'],['a','a/b'],['é','e\u0301']):
            with self.subTest(paths=paths),self.assertRaises(ValueError):b.validate_paths([{'path':p} for p in paths])
    def test_no_collision_siblings(self):b.validate_paths([{'path':'d/a'},{'path':'d/b'}])
    def test_pin_validation(self):
        p=json.loads((ROOT/'PIN.json').read_text());b.validate_pin(p)
        for k,v in [('commit','main'),('tree','0'),('remote','https://example.invalid/repo.git'),('archive_url','https://example.invalid/x.zip')]:
            with self.subTest(k=k),self.assertRaises(ValueError):b.validate_pin({**p,k:v})
    def test_blob_content(self):
        with tempfile.TemporaryDirectory() as t:
            v=b'test content\n';r=b.copy_blob(io.BytesIO(v),len(v),Path(t)/'x')
            self.assertEqual(r['blob'],'d670460b4b4aece5915caf5c68d12f560a9fe3e4');self.assertEqual(r['sha256'],hashlib.sha256(v).hexdigest())
    def test_truncated_blob(self):
        with tempfile.TemporaryDirectory() as t,self.assertRaises(ValueError):b.copy_blob(io.BytesIO(b'x'),5,Path(t)/'x')
    def test_blob_budget_and_existing_file(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);existing=p/'existing';existing.write_bytes(b'preserve')
            with patch.object(b,'MAX_FILE',1),self.assertRaisesRegex(ValueError,'individual file exceeds budget'):
                b.copy_blob(io.BytesIO(b'xx'),2,p/'too-large')
            self.assertFalse((p/'too-large').exists())
            with self.assertRaises(FileExistsError):b.copy_blob(io.BytesIO(b'x'),1,existing)
            self.assertEqual(existing.read_bytes(),b'preserve')
    def test_lfs_pointer_disclosed(self):
        with tempfile.TemporaryDirectory() as t:
            v=b'version https://git-lfs.github.com/spec/v1\noid sha256:abc\n';r=b.copy_blob(io.BytesIO(v),len(v),Path(t)/'x');self.assertTrue(r['lfs_pointer'])
    def archive(self,p,items):
        with zipfile.ZipFile(p,'w') as z:
            for name,data,mode in items:
                info=zipfile.ZipInfo('root/'+name);info.create_system=3;info.external_attr=mode<<16;z.writestr(info,data)
    def test_archive_tree_and_inert_symlink(self):
        rows=[('a',b'a',stat.S_IFREG|0o644),('run',b'echo x',stat.S_IFREG|0o755),('link',b'/old/private',stat.S_IFLNK|0o777)]
        entries=[{'path':n,'mode':'120000' if stat.S_ISLNK(m) else '100755' if m&0o111 else '100644','blob':blob(v)} for n,v,m in rows]
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);stage=p/'stage';stage.mkdir();self.archive(p/'a.zip',rows)
            actual=b.extract_archive(p/'a.zip',stage,b.tree_digest(entries))
            self.assertEqual(len(actual),3);self.assertFalse((stage/'link').is_symlink());self.assertEqual((stage/'link').read_bytes(),b'/old/private')
    def test_bad_archive_tree(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);(p/'stage').mkdir();self.archive(p/'a.zip',[('x',b'x',stat.S_IFREG|0o644)])
            with self.assertRaises(ValueError):b.extract_archive(p/'a.zip',p/'stage','0'*40)
    def test_zip_traversal(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);(p/'stage').mkdir();self.archive(p/'a.zip',[('../escape',b'x',stat.S_IFREG|0o644)])
            with self.assertRaises(ValueError):b.extract_archive(p/'a.zip',p/'stage','0'*40)
            self.assertFalse((p/'escape').exists())
    @unittest.skipUnless(shutil.which('git'),'git not installed')
    def test_real_local_git_restore(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);origin=p/'origin';stage=p/'stage';origin.mkdir();stage.mkdir()
            (origin/'目录 空格').mkdir();(origin/'目录 空格/x.txt').write_text('fixture\n')
            (origin/'a').write_bytes(b'a')
            for cmd in (['git','init','--template='],['git','add','.'],['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid','-c','commit.gpgsign=false','commit','-m','fixture'],['git','config','uploadpack.allowAnySHA1InWant','true']):
                subprocess.run(cmd,cwd=origin,check=True,capture_output=True)
            sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=origin).decode().strip()
            tree=subprocess.check_output(['git','rev-parse','HEAD^{tree}'],cwd=origin).decode().strip()
            original=b.git
            def local(args,cwd,**kw):
                if args[0]=='fetch':return subprocess.run(['git','-c','protocol.file.allow=always',*args],cwd=cwd,check=True,timeout=30,**kw)
                return original(args,cwd,**kw)
            with patch.object(b,'git',local):
                result=b.restore_git(stage,{'remote':origin.as_uri(),'commit':sha,'tree':tree,'working_branch':'handoff/test'})
            self.assertEqual(len(result),2);self.assertEqual((stage/'目录 空格/x.txt').read_bytes(),b'fixture\n')
            self.assertEqual(subprocess.check_output(['git','rev-parse','HEAD'],cwd=stage).decode().strip(),sha)
            self.assertEqual(subprocess.check_output(['git','status','--porcelain'],cwd=stage).strip(),b'')
            limited=p/'limited';limited.mkdir()
            with patch.object(b,'git',local),patch.object(b,'MAX_TOTAL',1),self.assertRaisesRegex(ValueError,'total bytes budget'):
                b.restore_git(limited,{'remote':origin.as_uri(),'commit':sha,'tree':tree,'working_branch':'handoff/test'})
            self.assertEqual((limited/'a').read_bytes(),b'a')
            self.assertFalse((limited/'目录 空格/x.txt').exists())

    @unittest.skipUnless(shutil.which('git'),'git not installed')
    def test_active_restore_defers_history_without_deleting_index_entries(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);origin=p/'origin';stage=p/'stage';origin.mkdir();stage.mkdir()
            for name,raw in [('README.md',b'current root'),('comsol_mcp/code.py',b'current code'),('history/large.dat',b'historical data')]:
                path=origin/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
            for cmd in (['git','init','--template='],['git','add','.'],['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid','-c','commit.gpgsign=false','commit','-m','fixture'],['git','config','uploadpack.allowAnySHA1InWant','true']):
                subprocess.run(cmd,cwd=origin,check=True,capture_output=True)
            sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=origin).decode().strip()
            tree=subprocess.check_output(['git','rev-parse','HEAD^{tree}'],cwd=origin).decode().strip()
            original=b.git
            def local(args,cwd,**kw):
                if args[0]=='fetch':return subprocess.run(['git','-c','protocol.file.allow=always',*args],cwd=cwd,check=True,timeout=30,**kw)
                return original(args,cwd,**kw)
            pin={'remote':origin.as_uri(),'commit':sha,'tree':tree,'working_branch':'handoff/active-test',
                 'active_paths':['comsol_mcp'],'active_root_files':True,'active_root_excluded':[]}
            with patch.object(b,'git',local):rows=b.restore_git(stage,pin,scope='active')
            self.assertEqual(len(rows),2)
            self.assertEqual(len(rows.tree_entries),3)
            self.assertEqual(b.tree_digest(rows.tree_entries),tree)
            self.assertFalse((stage/'history/large.dat').exists())
            self.assertEqual((stage/'comsol_mcp/code.py').read_bytes(),b'current code')
            self.assertEqual(subprocess.check_output(['git','status','--porcelain'],cwd=stage).strip(),b'')
            self.assertIn(b'S history/large.dat',subprocess.check_output(['git','ls-files','-t'],cwd=stage))

    @unittest.skipUnless(shutil.which('git'),'git not installed')
    def test_active_backfill_uses_batched_pinned_blob_ids(self):
        # Protocol v2 makes the fixture origin actually apply blob:none, so the
        # selected blobs are missing and must come back in one batched fetch.
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);origin=p/'origin';stage=p/'stage';origin.mkdir();stage.mkdir()
            for name,raw in [('README.md',b'current root'),('comsol_mcp/code.py',b'current code'),('history/large.dat',b'historical data')]:
                path=origin/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
            for cmd in (['git','init','--template='],['git','add','.'],['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid','-c','commit.gpgsign=false','commit','-m','fixture'],['git','config','uploadpack.allowAnySHA1InWant','true'],['git','config','uploadpack.allowFilter','true']):
                subprocess.run(cmd,cwd=origin,check=True,capture_output=True)
            sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=origin).decode().strip()
            tree=subprocess.check_output(['git','rev-parse','HEAD^{tree}'],cwd=origin).decode().strip()
            def blob_of(name):
                return subprocess.check_output(['git','ls-tree',sha,name],cwd=origin).decode().split()[2]
            selected={blob_of('README.md'),blob_of('comsol_mcp/code.py')};deferred=blob_of('history/large.dat')
            original=b.git;original_env=b.git_env;fetches=[]
            def local(args,cwd,**kw):
                if args[0]=='fetch':
                    fetches.append(list(args))
                    return subprocess.run(['git','-c','protocol.file.allow=always','-c','protocol.version=2',*args],cwd=cwd,check=True,timeout=30,**kw)
                return original(args,cwd,**kw)
            def old_git_env():
                env=original_env();env.pop('GIT_NO_LAZY_FETCH',None)
                return env
            pin={'remote':origin.as_uri(),'commit':sha,'tree':tree,'working_branch':'handoff/backfill-test',
                 'active_paths':['comsol_mcp'],'active_root_files':True,'active_root_excluded':[]}
            trace=p/'git-trace'
            with patch.dict(os.environ,{'GIT_TRACE':str(trace),'GIT_ALLOW_PROTOCOL':'file'}), \
                    patch.object(b,'git',local),patch.object(b,'git_env',old_git_env):
                rows=b.restore_git(stage,pin,scope='active')
            self.assertEqual(len(rows),2)
            self.assertEqual((stage/'README.md').read_bytes(),b'current root')
            self.assertEqual((stage/'comsol_mcp/code.py').read_bytes(),b'current code')
            self.assertEqual(len(fetches),2,'exactly the pinned commit fetch plus one batched blob backfill')
            self.assertEqual(fetches[1][:3],['fetch','--no-tags','origin'])
            self.assertEqual(set(fetches[1][3:]),selected,'backfill requests exactly the missing pinned blob ids in one request')
            self.assertEqual(trace.read_text().count('built-in: git fetch '),2,
                             'Git must not launch hidden per-blob fetches when GIT_NO_LAZY_FETCH is absent')
            local_objects=subprocess.check_output(['git','cat-file','--batch-all-objects','--batch-check=%(objectname)'],cwd=stage).splitlines()
            self.assertNotIn(deferred.encode(),local_objects,'deferred historical blob must remain absent locally')
            self.assertEqual(subprocess.check_output(['git','status','--porcelain'],cwd=stage).strip(),b'')

    @unittest.skipUnless(shutil.which('git'),'git not installed')
    def test_incomplete_backfill_stops_before_materializing(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);origin=p/'origin';stage=p/'stage';origin.mkdir();stage.mkdir()
            (origin/'README.md').write_bytes(b'fixture')
            for cmd in (['git','init','--template='],['git','add','.'],['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid','-c','commit.gpgsign=false','commit','-m','fixture'],['git','config','uploadpack.allowFilter','true']):
                subprocess.run(cmd,cwd=origin,check=True,capture_output=True)
            sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=origin).decode().strip()
            tree=subprocess.check_output(['git','rev-parse','HEAD^{tree}'],cwd=origin).decode().strip()
            blob_id=subprocess.check_output(['git','rev-parse','HEAD:README.md'],cwd=origin).decode().strip()
            original=b.git;original_env=b.git_env
            def local(args,cwd,**kw):
                if args[0]=='fetch':
                    return subprocess.run(['git','-c','protocol.file.allow=always','-c','protocol.version=2',*args],cwd=cwd,check=True,timeout=30,**kw)
                return original(args,cwd,**kw)
            def old_git_env():
                env=original_env();env.pop('GIT_NO_LAZY_FETCH',None)
                return env
            pin={'remote':origin.as_uri(),'commit':sha,'tree':tree,'working_branch':'handoff/incomplete-test'}
            with patch.dict(os.environ,{'GIT_ALLOW_PROTOCOL':'file'}),patch.object(b,'git',local), \
                    patch.object(b,'git_env',old_git_env),patch.object(b,'backfill_blobs') as backfill, \
                    patch.object(b,'materialize_batch') as materialize:
                with self.assertRaisesRegex(ValueError,blob_id):b.restore_git(stage,pin,scope='active')
            backfill.assert_called_once_with(stage,[blob_id])
            materialize.assert_not_called()
            self.assertFalse((stage/'README.md').exists())

    def test_backfill_batches_pinned_ids_within_one_arg_budget(self):
        calls=[]
        def fake_git(argv,cwd,**kw):calls.append(list(argv))
        with patch.object(b,'git',fake_git):b.backfill_blobs(Path('unused'),['%040x'%i for i in range(950)])
        self.assertEqual([len(c)-3 for c in calls],[b.BATCH_FETCH_IDS,b.BATCH_FETCH_IDS,150])
        self.assertTrue(all(c[:3]==['fetch','--no-tags','origin'] for c in calls))
        self.assertEqual([c[3] for c in calls],[('%040x'%0),('%040x'%400),('%040x'%800)])

    @unittest.skipUnless(shutil.which('git'),'git not installed')
    def test_probe_reports_missing_without_network(self):
        with tempfile.TemporaryDirectory() as t:
            repo=Path(t)/'repo';repo.mkdir()
            for cmd in (['git','init','--template='],):subprocess.run(cmd,cwd=repo,check=True,capture_output=True)
            (repo/'x').write_text('x\n')
            for cmd in (['git','add','.'],['git','-c','user.name=F','-c','user.email=f@example.invalid','-c','commit.gpgsign=false','commit','-m','f']):
                subprocess.run(cmd,cwd=repo,check=True,capture_output=True)
            real=subprocess.check_output(['git','rev-parse','HEAD:x'],cwd=repo).decode().strip()
            self.assertEqual(b.missing_blobs(repo,[real,'0'*40]),['0'*40])
            self.assertEqual(b.missing_blobs(repo,[real]),[])

    @unittest.skipUnless(shutil.which('git'),'git not installed')
    def test_git_failure_message_retains_child_stderr(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(ValueError) as ctx:b.git(['status'],Path(t))
        message=str(ctx.exception)
        self.assertIn('git status exited',message)
        self.assertIn('not a git repository',message)

    @unittest.skipUnless(shutil.which('git'),'git not installed')
    def test_local_object_enumeration_failure_retains_stderr(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(ValueError) as ctx:b.missing_blobs(Path(t),['0'*40])
        self.assertIn('local object enumeration exited',str(ctx.exception))
        self.assertIn('not a git repository',str(ctx.exception))

    def test_wrong_batch_object_reports_row_context_and_child_stderr(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);stage=p/'stage';stage.mkdir();batch=p/'ids'
            rows=[{'path':'a.txt','mode':'100644','blob':'0'*40}]
            batch.write_text('0'*40+'\n',encoding='ascii')
            class FakePopen:
                def __init__(self,argv,cwd=None,env=None,stdin=None,stdout=None,stderr=None,**kw):
                    os.write(stderr.fileno(),b'remote: error: pack-objects died of signal 13')
                    self.stdout=io.BytesIO(b'0'*40+b' missing\n')
                    self.killed=False
                def wait(self,timeout=None):return 0
                def kill(self):self.killed=True
            with tempfile.TemporaryFile() as errors,patch.object(b.subprocess,'Popen',FakePopen):
                with self.assertRaises(ValueError) as ctx:b.materialize_batch(stage,rows,batch,errors)
            message=str(ctx.exception)
            self.assertIn('wrong batch object at 1/1',message)
            self.assertIn('path=a.txt',message);self.assertIn('expected_blob='+'0'*40,message)
            self.assertIn('pack-objects died',message,'child stderr must be retained in the failure')

if __name__=='__main__':unittest.main()
