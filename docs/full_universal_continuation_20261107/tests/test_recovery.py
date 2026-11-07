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
            for cmd in (['git','init','--template='],['git','add','.'],['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid','-c','commit.gpgsign=false','commit','-m','fixture']):
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

    @unittest.skipUnless(shutil.which('git'),'git not installed')
    def test_active_restore_defers_history_without_deleting_index_entries(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);origin=p/'origin';stage=p/'stage';origin.mkdir();stage.mkdir()
            for name,raw in [('README.md',b'current root'),('comsol_mcp/code.py',b'current code'),('history/large.dat',b'historical data')]:
                path=origin/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
            for cmd in (['git','init','--template='],['git','add','.'],['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid','-c','commit.gpgsign=false','commit','-m','fixture']):
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

if __name__=='__main__':unittest.main()
