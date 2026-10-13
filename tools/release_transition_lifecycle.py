"""No-engine, full-project data profile for disposable release transitions.

The release driver owns installation/rollback; fixed isolated children use
only the explicitly verified package, a disposable operation ledger, a real
ProjectAuthority/ArtifactStore registration of the checked-in reviewed MPH,
and the production full-backup/full-restore/full-recover commands. No COMSOL
runtime is started here.
"""
from __future__ import annotations

import email
import hashlib
import json
import pathlib
import re
import stat
import zipfile
from typing import Any


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def wheel_input(release: Any, lock: pathlib.Path, wheelhouse: pathlib.Path,
                expected_sha256: str, environment: dict[str, str]) -> dict[str, Any]:
    """Bind an applicable lock to real wheel metadata and complete app payload."""
    if len(expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in expected_sha256):
        raise release.ReleaseError("lifecycle requires an explicit lowercase application wheel SHA256")
    pins = release.parse_hash_requirements(lock, marker_environment=environment)
    app_pin = pins.get("comsol-mcp")
    if not app_pin or expected_sha256 not in app_pin["hashes"]:
        raise release.ReleaseError("application SHA256 is not bound by the applicable lock")
    candidates: list[pathlib.Path] = []
    available: dict[str, list[dict[str, Any]]] = {}
    wheels, metadata_sidecars = release.flat_wheel_inventory(wheelhouse)
    for path in wheels:
        if path.is_symlink():
            raise release.ReleaseError("symlinked lifecycle wheel input")
        meta = release.wheel_metadata(path)
        name = release.normalized_name(meta["name"])
        sha = release.sha256_file(path)
        pin = pins.get(name)
        if pin and meta["version"] == pin["version"] and sha in pin["hashes"]:
            available.setdefault(name, []).append({"path": str(path.resolve()), "sha256": sha})
        if name == "comsol-mcp" and sha == expected_sha256:
            candidates.append(path)
    if set(pins) - set(available):
        raise release.ReleaseError("applicable lock has missing/mismatched wheels: " + ", ".join(sorted(set(pins) - set(available))))
    if len(candidates) != 1:
        raise release.ReleaseError("expected application wheel must resolve uniquely")
    path = candidates[0]
    members: list[dict[str, Any]] = []
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len({i.filename for i in infos}) != len(infos):
            raise release.ReleaseError("duplicate lifecycle wheel members")
        metadata_paths = [i.filename for i in infos if i.filename.endswith(".dist-info/METADATA")]
        if len(metadata_paths) != 1:
            raise release.ReleaseError("ambiguous application METADATA")
        metadata = email.message_from_bytes(archive.read(metadata_paths[0]))
        if release.normalized_name(metadata["Name"] or "") != "comsol-mcp" or metadata["Version"] != app_pin["version"]:
            raise release.ReleaseError("application METADATA differs from applicable lock")
        for info in infos:
            relative = pathlib.PurePosixPath(info.filename)
            if (relative.is_absolute() or ".." in relative.parts or "\\" in info.filename or
                    stat.S_ISLNK(info.external_attr >> 16)):
                raise release.ReleaseError("unsafe lifecycle wheel member")
            if info.is_dir():
                continue
            payload = archive.read(info)
            members.append({"path": info.filename, "bytes": len(payload),
                            "sha256": hashlib.sha256(payload).hexdigest()})
    members.sort(key=lambda row: row["path"])
    package = [row for row in members if row["path"].startswith("comsol_mcp/")]
    code = [row for row in package if pathlib.PurePosixPath(row["path"]).suffix in {".py", ".java"}]
    if not code or not any(row["path"] == "comsol_mcp/_operation_store.py" for row in code):
        raise release.ReleaseError("application wheel has no operation-store source payload")
    return {"wheel_path": str(path.resolve()), "wheel_sha256": expected_sha256,
            "version": metadata["Version"], "requires_dist": metadata.get_all("Requires-Dist", []),
            "lock_sha256": release.sha256_file(lock),
            "applicable_pins": {name: {"version": pin["version"], "hashes": sorted(pin["hashes"])} for name, pin in sorted(pins.items())},
            "available_locked_wheels": available, "members": members,
            "wheelhouse_metadata_sidecars": metadata_sidecars,
            "package_members": package, "code_payload_sha256": digest(code),
            "metadata_path": metadata_paths[0], "marker_environment": environment}


CAPTURE_SCRIPT = r'''
import hashlib,json,pathlib,re,sys
from importlib import metadata
from packaging.markers import default_environment
d=metadata.distribution('comsol-mcp')
prefix=pathlib.Path(sys.prefix).resolve()
pkg=pathlib.Path(d.locate_file('comsol_mcp')).resolve()
if not pkg.is_relative_to(prefix): raise RuntimeError('installed package outside selected venv')
rows=[]
for p in sorted(pkg.rglob('*')):
 if p.is_symlink(): raise RuntimeError('installed package contains symlink')
 if p.is_file() and '__pycache__' not in p.parts and p.suffix not in {'.pyc','.pyo'} and not p.name.startswith('._'):
  b=p.read_bytes(); rows.append({'path':p.relative_to(pkg).as_posix(),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()})
distributions=[]
for dist in metadata.distributions():
 name=dist.metadata.get('Name'); version=dist.version
 if not name or not version: raise RuntimeError('incomplete installed metadata')
 distributions.append({'name':re.sub(r'[-_.]+','-',name).lower(),'version':version})
distributions.sort(key=lambda row:(row['name'],row['version']))
if len({r['name'] for r in distributions})!=len(distributions): raise RuntimeError('ambiguous installed distribution')
headers=[]
for rel in json.loads(sys.argv[1]):
 p=pathlib.Path(d.locate_file(rel))
 if p.is_symlink() or not p.resolve().is_relative_to(prefix): raise RuntimeError('installed metadata outside venv')
 b=p.read_bytes(); headers.append({'path':rel,'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()})
def dig(rows): return hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest()
print(json.dumps({'installed_version':d.version,'package_root':str(pkg),'package_fingerprint':{'files':rows,'digest':dig(rows)},'distribution_fingerprint':{'distributions':distributions,'digest':dig(distributions)},'metadata_members':headers,'marker_environment':default_environment(),'capture_errors':{}}))
'''


DATA_SCRIPT = r'''
import hashlib,json,pathlib,sqlite3,sys
from comsol_mcp._operation_store import OperationStore,IdempotencyConflict
phase,home_text,seed_text=sys.argv[1:]
home=pathlib.Path(home_text); seed_path=pathlib.Path(seed_text); path=home/'operations.sqlite3'
tables=('operations','jobs','job_events','sessions','runtimes','revisions','artifacts','checkpoints')
def logical(db):
 return {t:sorted([list(row) for row in db.execute('SELECT * FROM '+t)],key=lambda r:json.dumps(r,sort_keys=True)) for t in tables}
def storage():
 db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
 try:
  assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
  return {'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'schema':db.execute('SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE "sqlite_%" ORDER BY type,name').fetchall(),'schema_version':db.execute('SELECT version FROM schema_meta').fetchall()}
 finally: db.close()
if phase=='seed':
 home.mkdir(mode=0o700,exist_ok=False)
 assert not seed_path.exists() and not path.exists()
 store=OperationStore(path); seed={'operations':{}}
 for key,status in [('terminal','SUCCEEDED'),('unknown','UNKNOWN')]:
  op,replay=store.begin(request_id='w26-'+key,idempotency_key='w26-'+key,request_hash='hash-'+key,operation='synthetic.release.'+key,metadata={'synthetic_only':True})
  assert replay is False
  result={'synthetic_only':True,'value':key}
  if key=='unknown': result.update(safe_retry=False,requires_model_reconciliation=True)
  accepted,actual=store.finish(op['operation_id'],status=status,result=result)
  assert accepted is True and actual==status
  seed['operations'][key]={'operation_id':op['operation_id'],'job_id':op['job_id'],'result':result}
 store.put_metadata('sessions','w26-synthetic-session',{'synthetic_only':True,'value':'retained'})
 seed['logical']=logical(store.db)
 store.close(); seed['storage']=storage()
 with seed_path.open('x') as f: json.dump(seed,f,sort_keys=True)
 print(json.dumps({'success':True,'phase':phase,'seed_sha256':hashlib.sha256(seed_path.read_bytes()).hexdigest(),'storage':seed['storage']}))
else:
 seed=json.loads(seed_path.read_text()); before=storage()
 store=OperationStore(path)
 try:
  full_phase=phase in ('candidate-full','restored-full')
  current=logical(store.db)
  if full_phase:
   for table,rows in seed['logical'].items():
    current_rows={json.dumps(row,sort_keys=True,separators=(',',':')) for row in current[table]}
    assert all(json.dumps(row,sort_keys=True,separators=(',',':')) in current_rows for row in rows), 'original data row changed or disappeared before replay/reconciliation: '+table
  else:
   assert current==seed['logical'], 'original data changed before replay/reconciliation'
  for key,expected in seed['operations'].items():
   op,replay=store.begin(request_id='new-request-'+key,idempotency_key='w26-'+key,request_hash='hash-'+key,operation='synthetic.release.'+key,metadata={'synthetic_only':True})
   assert replay is True and op['operation_id']==expected['operation_id'] and op['job_id']==expected['job_id']
   assert op['result']==expected['result']
   try: store.begin(request_id='conflict',idempotency_key='w26-'+key,request_hash='wrong-hash',operation='synthetic.release.'+key)
   except IdempotencyConflict: pass
   else: raise AssertionError('idempotency conflict not refused')
  current=logical(store.db)
  if full_phase:
   for table,rows in seed['logical'].items():
    current_rows={json.dumps(row,sort_keys=True,separators=(',',':')) for row in current[table]}
    assert all(json.dumps(row,sort_keys=True,separators=(',',':')) in current_rows for row in rows), 'idempotency replay changed original rows: '+table
  else:
   assert current==seed['logical'], 'idempotency replay changed original rows'
  terminal=seed['operations']['terminal']; unknown=seed['operations']['unknown']
  old_terminal=store.get_operation(terminal['operation_id'])
  assert old_terminal['status']=='SUCCEEDED'
  observation=store.get_operation(unknown['operation_id'])
  assert observation['status']=='UNKNOWN' and observation['finished_at'] is None
  assert observation['result']['safe_retry'] is False and observation['result']['requires_model_reconciliation'] is True
  reconciled=[]
  if phase in ('candidate','candidate-full'):
   reconciled=store.reconcile_after_restart()
   assert reconciled==[unknown['job_id']]
   after=store.get_operation(unknown['operation_id'])
   assert after['status']=='RECONCILING' and after['finished_at'] is None and after['result']==observation['result']
   assert store.job(unknown['job_id'])['status']=='RECONCILING'
   assert store.get_operation(terminal['operation_id'])==old_terminal
   if not full_phase:
    assert store.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0]==2
    assert store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==2
  elif phase not in ('restored','restored-full'): raise AssertionError('unknown data phase')
 finally: store.close()
 after=storage()
 print(json.dumps({'success':True,'phase':phase,'before_storage':before,'after_storage':after,'reconciled_job_ids':reconciled,'old_data_verified':True,'idempotency_verified':True,'unknown_safe_retry_false_verified':True,'engine_scope':'NOT_INVOKED_FIXED_OPERATION_STORE_PROFILE'}))
'''


BACKUP_SCRIPT = "import sys; from comsol_mcp._state_backup import main; raise SystemExit(main(sys.argv[1:]))"
FAILURE_SCRIPT = "import json,sys; print(json.dumps({'controlled_failure':'w26-after-candidate-reopen'})); print('W26_EXPECTED_FAILURE_AFTER_CANDIDATE_REOPEN',file=sys.stderr); raise SystemExit(73)"
BACKUP_VERIFY_SCRIPT = r'''
import hashlib,json,pathlib,sqlite3,sys
p=pathlib.Path(sys.argv[1]); m=json.loads(pathlib.Path(sys.argv[2]).read_text()); b=p.read_bytes()
assert hashlib.sha256(b).hexdigest()==m['database_sha256'] and len(b)==m['database_size_bytes']
db=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)
try:
 assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
 schema=db.execute('SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE "sqlite_%" ORDER BY type,name').fetchall()
 version=db.execute('SELECT version FROM schema_meta').fetchall()
 legacy={t:sorted([list(r) for r in db.execute('SELECT * FROM '+t)],key=lambda r:json.dumps(r,sort_keys=True)) for t in ('operations','jobs','job_events','sessions','runtimes','revisions','artifacts','checkpoints')}
finally: db.close()
print(json.dumps({'success':True,'sha256':hashlib.sha256(b).hexdigest(),'bytes':len(b),'schema':schema,'schema_version':version,'legacy_data_sha256':hashlib.sha256(json.dumps(legacy,sort_keys=True,separators=(',',':')).encode()).hexdigest()}))
'''


FULL_DATA_SCRIPT = r'''
import hashlib,json,pathlib,shutil,stat,sys,zipfile
from comsol_mcp._artifact_store import (local_artifact_host_identity,local_engine_host_identity,
 register_project_artifact,resolve_registered_artifact)
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._project_authority import ProjectAuthority
phase,home_text,workspace_text,model_text,expected_sha,expected_size_text=sys.argv[1:]
assert phase=='register', 'unsupported full-data phase'
home=pathlib.Path(home_text); workspace_root=pathlib.Path(workspace_text)
model=pathlib.Path(model_text); expected_size=int(expected_size_text)
if model.is_symlink() or not stat.S_ISREG(model.lstat().st_mode): raise RuntimeError('review model source is not a regular file')
before=model.stat(follow_symlinks=False)
digest=hashlib.sha256()
size=0
with model.open('rb') as stream:
 with zipfile.ZipFile(stream) as mph:
  assert mph.testzip() is None and mph.namelist(), 'review model MPH container failed ZIP integrity'
 stream.seek(0)
 while True:
  chunk=stream.read(1024*1024)
  if not chunk: break
  digest.update(chunk); size+=len(chunk)
after=model.stat(follow_symlinks=False)
assert (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns), 'review model source changed during verification'
assert digest.hexdigest()==expected_sha and size==expected_size, 'review model source differs from the fixed reopen fixture'
workspace_root.mkdir(mode=0o700,parents=True,exist_ok=False)
store=OperationStore(home/'operations.sqlite3')
try:
 authority=ProjectAuthority(store,workspace_root=workspace_root,
  permission_provider=lambda:{'inspect','project_write','compute'},
  grant_ceiling={'inspect','project_write','compute'})
 created=authority.dispatch('project.create',{'label':'W26 full-data release lifecycle','workspace':'release-lifecycle/model-project',
  'policy':{'permissions':['inspect','project_write']}})
 project=created['data']['project']; project_root=pathlib.Path(project['workspace'])
 input_dir=project_root/'inputs'; input_dir.mkdir(mode=0o700)
 source=input_dir/'review_reopen.mph'
 copied=hashlib.sha256(); copied_size=0
 with model.open('rb') as incoming, source.open('xb') as outgoing:
  while True:
   chunk=incoming.read(1024*1024)
   if not chunk: break
   outgoing.write(chunk); copied.update(chunk); copied_size+=len(chunk)
  outgoing.flush()
  import os; os.fsync(outgoing.fileno())
 if copied.hexdigest()!=expected_sha or copied_size!=expected_size: raise RuntimeError('project-local MPH copy differs from verified source')
 host=local_artifact_host_identity(); engine=local_engine_host_identity('127.0.0.1:2040')
 artifact=register_project_artifact(project_root,store,'inputs/review_reopen.mph',project_id=project['project_id'],
  role='saved_model',classification='project_local',host_identity=host,engine_host_identity=engine,
  request_id='w26-release-lifecycle-model-register',registering_operation_id='artifact.register',
  artifact_type='application/vnd.comsol.mph',format_version='comsol-mph-savecopy/1',
  provenance_fields={'source_kind':'existing-reviewed-comsol-6.4-model','input_model_sha256':expected_sha,'native_execution':'NOT_RUN'})
 resolved=resolve_registered_artifact(project_root,store,artifact['artifact_id'],project_id=project['project_id'],
  current_host_identity=host,current_engine_host_identity=engine,current_server_instance_id='')
 assert resolved['sha256']==expected_sha and resolved['size']==expected_size
 record={'success':True,'project_id':project['project_id'],'project_workspace':str(project_root),
  'project_workspace_relative':project_root.relative_to(workspace_root).as_posix(),
  'artifact_id':artifact['artifact_id'],'artifact_sha256':resolved['sha256'],'artifact_size_bytes':resolved['size'],
  'artifact_relative_path':resolved['relative_path'],'registered_role':resolved['role'],
  'format_version':'comsol-mph-savecopy/1','source_sha256':expected_sha,'source_size_bytes':expected_size,
  'source_container':'ZIP_INTEGRITY_PASS','host_identity':host,'engine_host_identity':engine}
finally:
 store.close()
print(json.dumps(record,sort_keys=True))
'''


FULL_BACKUP_SCRIPT = "import sys; from comsol_mcp._state_backup import main; raise SystemExit(main(['full-backup', *sys.argv[1:]]))"
FULL_RESTORE_SCRIPT = "import sys; from comsol_mcp._state_backup import main; raise SystemExit(main(['full-restore', *sys.argv[1:]]))"
FULL_RECOVER_SCRIPT = "import sys; from comsol_mcp._state_backup import main; raise SystemExit(main(['full-recover', *sys.argv[1:]]))"


FULL_VERIFY_SCRIPT = r'''
import hashlib,json,pathlib,re,sys,zipfile
from comsol_mcp._artifact_store import local_artifact_host_identity,resolve_registered_artifact
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._project_authority import ProjectAuthority
from comsol_mcp._state_backup import _history_fingerprint,_inspect_schema
home=pathlib.Path(sys.argv[1]); workspace=pathlib.Path(sys.argv[2]); project_id=sys.argv[3]
artifact_id=sys.argv[4]; expected_sha=sys.argv[5]; expected_size=int(sys.argv[6])
seed=json.loads(pathlib.Path(sys.argv[7]).read_text()); entry=pathlib.Path(sys.argv[8])
host=local_artifact_host_identity(); store=OperationStore(home/'operations.sqlite3')
try:
 authority=ProjectAuthority(store,workspace_root=workspace,permission_provider=lambda:set(),grant_ceiling=set())
 project=authority.get_project(project_id); project_root=pathlib.Path(project['workspace'])
 assert project_root.is_relative_to(workspace) and project_root.is_dir(), 'restored project is not bound below the new workspace root'
 resolved=resolve_registered_artifact(project_root,store,artifact_id,project_id=project_id,
  current_host_identity=host,current_engine_host_identity=host,current_server_instance_id='')
 assert resolved['sha256']==expected_sha==artifact_id and resolved['size']==expected_size
 assert resolved['role']=='saved_model' and resolved['relative_path'].startswith('g2_artifacts/registered/')
 with zipfile.ZipFile(resolved['path']) as mph:
  assert mph.testzip() is None and mph.namelist(), 'restored MPH container failed ZIP integrity'
 terminal=seed['operations']['terminal']; unknown=seed['operations']['unknown']
 terminal_now=store.get_operation(terminal['operation_id']); unknown_now=store.get_operation(unknown['operation_id'])
 assert terminal_now['status']=='SUCCEEDED' and terminal_now['result']==terminal['result']
 assert unknown_now['status']=='UNKNOWN' and unknown_now['finished_at'] is None
 assert unknown_now['result']==unknown['result'] and unknown_now['result']['safe_retry'] is False
 unknown_job=store.job(unknown['job_id'])
 assert unknown_job['status']=='UNKNOWN', 'full restore must keep UNKNOWN history without reclassification'
 durable_history_sha256=_history_fingerprint(store.db)
 database_schema_version=_inspect_schema(store.db,allow_legacy_v1=False)
 assert isinstance(durable_history_sha256,str) and re.fullmatch(r'[0-9a-f]{64}',durable_history_sha256)
 assert type(database_schema_version) is int and database_schema_version > 0
 native_entry={'schema':'comsol-mcp.zero-solve-model-reopen-entry/1','status':'NOT_RUN_SOFTWARE_PREPARED',
  'evidence_scope':'SOFTWARE_PROJECT_AUTHORITY_ARTIFACTSTORE_FULL_RESTORE',
  'project_id':project_id,'project_workspace':str(project_root),'workspace_root':str(workspace),
  'artifact_id':artifact_id,'artifact_sha256':resolved['sha256'],'artifact_size_bytes':resolved['size'],
  'artifact_relative_path':resolved['relative_path'],'model_path':str(resolved['path']),
  'model_path_sha256':hashlib.sha256(str(resolved['path']).encode()).hexdigest(),
  'target_runtime':'COMSOL Multiphysics 6.4.0.293','action':'open_registered_saved_model_and_readback_only',
  'solve_requested':False,'native_runtime_started':False,'worker_or_model_bindings_revived':False,
  'durable_unknown_operation_preserved':True,'operation_replay_performed':False}
 with entry.open('x',encoding='utf-8') as stream:
  json.dump(native_entry,stream,sort_keys=True,indent=2); stream.write('\n')
 print(json.dumps({'success':True,'project_id':project_id,'artifact_id':artifact_id,'sha256':resolved['sha256'],
  'size_bytes':resolved['size'],'relative_path':resolved['relative_path'],'resolved_path':str(resolved['path']),
  'unknown_operation_id':unknown['operation_id'],'unknown_status':'UNKNOWN','operation_replay_performed':False,
  'durable_history_sha256':durable_history_sha256,'database_schema_version':database_schema_version,
  'worker_or_model_bindings_revived':False,'native_reopen_entry':str(entry),
  'native_reopen_entry_sha256':hashlib.sha256(entry.read_bytes()).hexdigest(),'native':'NOT_RUN','solve':'NOT_RUN'},sort_keys=True))
finally:
 store.close()
'''


FULL_RESTORE_CRASH_SCRIPT = r'''
import os,sys
from comsol_mcp import _state_backup as state
def crash_after_projects_installed(home,journal):
 original(home,journal)
 if journal.get('phase')=='PROJECTS_INSTALLED': os._exit(82)
original=state._full_journal_write
state._full_journal_write=crash_after_projects_installed
state.restore_full_backup(sys.argv[1],sys.argv[2],sys.argv[3],sys.argv[4],confirm=True)
raise SystemExit('restore interruption hook did not fire')
'''


class Lifecycle:
    def __init__(self, args: Any, release: Any, evidence: pathlib.Path,
                 old_lock: pathlib.Path, new_lock: pathlib.Path, sandbox: str):
        self.release, self.evidence, self.sandbox = release, evidence, sandbox
        self.steps: list[dict[str, Any]] = []
        self.failures: list[str] = []
        self.bootstrap: dict[str, str] | None = None
        self.identities: dict[str, bool] = {}
        self.profile = evidence / "network-deny-transition.sb"
        with self.profile.open("x") as stream:
            stream.write("(version 1)\n(deny network*)\n(allow default)\n")
        environment_record = self.process("marker-environment", args.python,
            "import json; from packaging.markers import default_environment; print(json.dumps(default_environment()))", [], supervised=False)
        if environment_record["exit_code"] != 0:
            raise release.ReleaseError("cannot resolve actual lifecycle marker environment")
        environment = json.loads(environment_record["stdout"])
        if not isinstance(environment, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in environment.items()):
            raise release.ReleaseError("malformed lifecycle marker environment")
        self.old = wheel_input(release, old_lock, args.old_wheelhouse, getattr(args, "old_wheel_sha256", ""), environment)
        self.new = wheel_input(release, new_lock, args.new_wheelhouse, getattr(args, "new_wheel_sha256", ""), environment)
        if self.old["wheel_sha256"] == self.new["wheel_sha256"]:
            raise release.ReleaseError("same application wheel cannot prove a code transition")
        if self.old["code_payload_sha256"] == self.new["code_payload_sha256"]:
            raise release.ReleaseError("metadata-only repack cannot prove a code transition")
        release._json_write(evidence / "old-wheel-input.json", self.old)
        release._json_write(evidence / "candidate-wheel-input.json", self.new)
        self.home = args.work_root / "synthetic-control-home"
        self.workspace_root = args.work_root / "source-projects"
        self.seed = evidence / "old-generated-seed.json"
        self.backup = evidence / "before-candidate-open.full-state.tar"
        self.backup_manifest = pathlib.Path(str(self.backup) + ".manifest.json")
        self.restored_home = args.work_root / "full-restored-control-home"
        self.restored_workspace = args.work_root / "full-restored-workspaces"
        self.native_reopen_entry = evidence / "zero-solve-reopen-entry.json"
        self.model_input = pathlib.Path(__file__).resolve().parents[1] / "evidence/w21_closure/windows/reviewer64_reopen01/project/review_reopen.mph"
        self.model_sha256 = "ee71658d66a92efc19c78de7742eb24c1ef3b16f1fb33b1d6814b1e79c96b9d6"
        self.model_size_bytes = 10_725_566
        if (not self.model_input.is_file() or self.model_input.is_symlink()
                or self.release.sha256_file(self.model_input) != self.model_sha256
                or self.model_input.stat().st_size != self.model_size_bytes):
            raise release.ReleaseError("fixed, reviewed COMSOL 6.4 model input is missing or changed")
        self.backup_verified: dict[str, Any] | None = None
        self.project_artifact: dict[str, Any] | None = None
        self.full_restore_receipt: dict[str, Any] | None = None
        self.candidate_pass = False
        self.restore_pass = False
        self.restored_pass = False

    def process(self, name: str, py: pathlib.Path, code: str, arguments: list[str],
                *, supervised: bool = True) -> dict[str, Any]:
        argv = [str(py), "-I", "-B", "-c", code, *arguments]
        if supervised:
            argv = [self.sandbox, "-f", str(self.profile), *argv]
        try:
            record = self.release.run_record(argv, cwd=py.absolute().parent.parent)
        except OSError as exc:
            record = {"argv": argv, "exit_code": None, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}"}
        self.retain_record(name, record)
        return record

    def retain_record(self, name: str, record: dict[str, Any]) -> None:
        record = {"name": name, **record}
        path = self.evidence / f"lifecycle-{len(self.steps):03d}-{name}.json"
        try:
            self.release._json_write(path, record)
            self.steps.append({"name": name, "record_path": str(path), "sha256": self.release.sha256_file(path), "exit_code": record.get("exit_code")})
        except OSError as exc:
            # Preserve rollback attempts even when the evidence medium fails;
            # an unbound record can never contribute to a full-mode PASS.
            self.failures.append(name + "-evidence-write-failed")
            self.steps.append({"name": name, "exit_code": record.get("exit_code"),
                               "record_error": type(exc).__name__, "sha256": None})

    def checked(self, name: str, py: pathlib.Path, code: str, arguments: list[str]) -> dict[str, Any]:
        record = self.process(name, py, code, arguments)
        try:
            if record.get("exit_code") != 0:
                raise ValueError("nonzero/unknown child exit")
            parsed = json.loads(record["stdout"])
            if parsed.get("success") is not True:
                raise ValueError("missing explicit child success")
            return parsed
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            self.failures.append(name)
            raise self.release.ReleaseError(f"lifecycle {name} failed: {exc}") from exc

    def capture(self, py: pathlib.Path) -> dict[str, Any]:
        # Both inputs can have different dist-info directory names. Capture
        # using the installed version, not whichever install was requested.
        metadata_paths = sorted({r["path"] for item in (self.old, self.new) for r in item["members"]
                                 if r["path"].endswith(".dist-info/METADATA")})
        code = CAPTURE_SCRIPT.replace("for rel in json.loads(sys.argv[1]):", "for rel in json.loads(sys.argv[1]):\n if rel.split('.dist-info/')[0] != 'comsol_mcp-'+d.version: continue")
        try:
            record = self.process("installed-capture", py, code, [json.dumps(metadata_paths)])
            if record.get("exit_code") != 0:
                raise ValueError("nonzero/unknown identity capture")
            state = json.loads(record["stdout"])
            if state.get("capture_errors") != {}:
                raise ValueError("invalid identity capture")
            return state
        except (OSError, ValueError, TypeError, AttributeError):
            self.failures.append("installed-capture-failed-or-unknown")
            return {"capture_errors": {"lifecycle_capture": "UNKNOWN"}, "installed_version": None}

    def verify_installed(self, name: str, py: pathlib.Path, state: dict[str, Any], expected: dict[str, Any]) -> bool:
        try:
            if self.release.sha256_file(pathlib.Path(expected["wheel_path"])) != expected["wheel_sha256"]:
                raise ValueError("wheel input changed")
            expected_rows = [{**r, "path": r["path"].removeprefix("comsol_mcp/")} for r in expected["package_members"]]
            actual_rows = state["package_fingerprint"]["files"]
            if actual_rows != expected_rows or state["installed_version"] != expected["version"]:
                raise ValueError("installed source/version differs from wheel")
            expected_meta = next(r for r in expected["members"] if r["path"] == expected["metadata_path"])
            if state["metadata_members"] != [expected_meta]:
                raise ValueError("installed METADATA differs from wheel")
            if state["marker_environment"] != expected["marker_environment"]:
                raise ValueError("selected/interpreted marker environment changed")
            rows = state["distribution_fingerprint"]["distributions"]
            actual = {r["name"]: r["version"] for r in rows}
            if len(actual) != len(rows):
                raise ValueError("duplicate installed distribution")
            required = {n: row["version"] for n, row in expected["applicable_pins"].items()}
            extras = {n: v for n, v in actual.items() if n not in required}
            if self.bootstrap is None:
                if set(extras) - {"pip", "setuptools", "wheel"}:
                    raise ValueError("unlocked initial distribution")
                self.bootstrap = extras
            else:
                allowed = {n: v for n, v in self.bootstrap.items() if n not in required}
                if extras != allowed:
                    raise ValueError("installed bootstrap exceptions differ")
            if actual != {**self.bootstrap, **required}:
                raise ValueError("installed distribution set differs from applicable lock")
            check = self.process(name + "-pip-check", py, "import subprocess,sys; raise SystemExit(subprocess.call([sys.executable,'-I','-m','pip','check']))", [])
            if check.get("exit_code") != 0:
                raise ValueError("pip check failed or unknown")
            self.identities[name] = True
            return True
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self.identities[name] = False
            self.failures.append(name + "-identity")
            return False

    def seed_old(self, py: pathlib.Path) -> None:
        seed = self.checked("old-code-seed", py, DATA_SCRIPT, ["seed", str(self.home), str(self.seed)])
        self.seed_sha256 = seed["seed_sha256"]
        self.verify_seed()

    def verify_seed(self) -> None:
        if self.release.sha256_file(self.seed) != self.seed_sha256:
            self.failures.append("old-generated-seed-changed")
            raise self.release.ReleaseError("old-generated data evidence changed")

    def candidate_phase(self, py: pathlib.Path) -> None:
        self.verify_seed()
        self.project_artifact = self.checked(
            "project-authority-mph-registration", py, FULL_DATA_SCRIPT,
            ["register", str(self.home), str(self.workspace_root), str(self.model_input),
             self.model_sha256, str(self.model_size_bytes)],
        )
        if (self.project_artifact.get("success") is not True
                or self.project_artifact.get("artifact_sha256") != self.model_sha256
                or self.project_artifact.get("artifact_size_bytes") != self.model_size_bytes
                or self.project_artifact.get("registered_role") != "saved_model"):
            self.failures.append("registered-model-identity-mismatch")
            raise self.release.ReleaseError("ProjectAuthority/ArtifactStore did not register the exact reviewed model")
        verified = self.checked("production-full-backup", py, FULL_BACKUP_SCRIPT,
                     ["--home", str(self.home), "--destination", str(self.backup)])
        manifest = verified.get("manifest")
        if (verified.get("success") is not True or verified.get("project_count") != 1
                or verified.get("file_count") != 1 or not isinstance(manifest, dict)
                or not isinstance(verified.get("durable_history_sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", verified.get("durable_history_sha256", "")) is None
                or manifest.get("durable_history_sha256") != verified.get("durable_history_sha256")
                or type((manifest.get("database") or {}).get("schema_version")) is not int
                or (manifest.get("database") or {}).get("schema_version") <= 0):
            self.failures.append("full-backup-manifest-invalid")
            raise self.release.ReleaseError("full backup did not inventory the expected project and registered MPH")
        file_matches = [item for item in manifest.get("files", [])
                        if item.get("project_id") == self.project_artifact["project_id"]
                        and item.get("sha256") == self.model_sha256
                        and item.get("size_bytes") == self.model_size_bytes
                        and any(reg.get("artifact_key") == self.model_sha256
                                and reg.get("role") == "saved_model"
                                for reg in item.get("registrations", []))]
        if (len(file_matches) != 1
                or self.release.sha256_file(self.backup) != verified.get("archive_sha256")
                or json.loads(self.backup_manifest.read_text()) != manifest):
            self.failures.append("full-backup-mph-not-bound")
            raise self.release.ReleaseError("full backup is not bound to the exact registered MPH bytes")
        self.backup_verified = verified
        self.backup_manifest_sha256 = self.release.sha256_file(self.backup_manifest)
        self.checked("candidate-code-reopen", py, DATA_SCRIPT, ["candidate-full", str(self.home), str(self.seed)])
        failure = self.process("controlled-failure", py, FAILURE_SCRIPT, [])
        if (failure.get("exit_code") != 73 or
                failure.get("stdout", "").strip() != json.dumps({"controlled_failure": "w26-after-candidate-reopen"}) or
                failure.get("stderr", "").strip() != "W26_EXPECTED_FAILURE_AFTER_CANDIDATE_REOPEN"):
            self.failures.append("controlled-failure")
            raise self.release.ReleaseError("controlled lifecycle failure was not observed exactly")
        self.candidate_pass = True

    def restore_candidate(self, py: pathlib.Path) -> None:
        if self.backup_verified is None:
            self.failures.append("restore-without-verified-backup")
            return
        self.verify_seed()
        if self.release.sha256_file(self.backup_manifest) != self.backup_manifest_sha256:
            self.failures.append("backup-manifest-changed")
            raise self.release.ReleaseError("verified backup manifest changed")
        if self.release.sha256_file(self.backup) != self.backup_verified.get("archive_sha256"):
            self.failures.append("backup-changed")
            raise self.release.ReleaseError("verified backup changed")
        self.restored_home.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.restored_workspace.mkdir(mode=0o700, parents=True, exist_ok=False)
        try:
            restored = self.checked("production-full-restore", py, FULL_RESTORE_SCRIPT,
                ["--home", str(self.restored_home), "--workspace-root", str(self.restored_workspace),
                 "--archive", str(self.backup), "--manifest", str(self.backup_manifest), "--confirm"])
        except Exception:
            journal = self.restored_home / "full-state.restore.pending.json"
            if journal.is_file() and not journal.is_symlink():
                try:
                    self.checked("production-full-recover-after-restore-failure", py, FULL_RECOVER_SCRIPT,
                                 ["--home", str(self.restored_home), "--confirm"])
                except Exception as recovery_exc:
                    self.failures.append("full-recover-failed:" + type(recovery_exc).__name__)
            raise
        if (restored.get("success") is not True or restored.get("worker_or_model_bindings_revived") is not False
                or restored.get("operation_replay_performed") is not False):
            self.failures.append("full-restore-receipt-invalid")
            raise self.release.ReleaseError("full restore receipt did not preserve the no-replay/no-rebind contract")
        verified_model = self.checked("restored-project-artifact-readback", py, FULL_VERIFY_SCRIPT,
            [str(self.restored_home), str(self.restored_workspace), self.project_artifact["project_id"],
             self.model_sha256, self.model_sha256, str(self.model_size_bytes), str(self.seed),
             str(self.native_reopen_entry)])
        if (verified_model.get("success") is not True or verified_model.get("sha256") != self.model_sha256
                or verified_model.get("size_bytes") != self.model_size_bytes
                or verified_model.get("native") != "NOT_RUN"
                or verified_model.get("solve") != "NOT_RUN"):
            self.failures.append("restored-model-readback-invalid")
            raise self.release.ReleaseError("restored ArtifactStore did not resolve the exact MPH and preserved history")
        expected_history = self.backup_verified.get("durable_history_sha256")
        observed_history = verified_model.get("durable_history_sha256")
        if (not isinstance(expected_history, str)
                or re.fullmatch(r"[0-9a-f]{64}", expected_history) is None
                or observed_history != expected_history):
            self.failures.append("restored-history-mismatch")
            raise self.release.ReleaseError("restored durable history digest is missing or differs from the verified backup")
        expected_schema = (self.backup_verified.get("manifest") or {}).get("database", {}).get("schema_version")
        observed_schema = verified_model.get("database_schema_version")
        if (type(expected_schema) is not int or expected_schema <= 0
                or type(observed_schema) is not int or observed_schema != expected_schema):
            self.failures.append("restored-schema-version-mismatch")
            raise self.release.ReleaseError("restored database schema version is missing or differs from the verified backup")
        self.full_restore_receipt = {"restore": restored, "verified_model": verified_model}
        if (self.release.sha256_file(self.backup) != self.backup_verified.get("archive_sha256")
                or self.release.sha256_file(self.backup_manifest) != self.backup_manifest_sha256):
            self.failures.append("backup-changed-during-restore")
            raise self.release.ReleaseError("backup evidence changed during restore")
        self.restore_pass = True

    def finish(self, py: pathlib.Path, package_pass: bool, identity_pass: bool) -> dict[str, Any]:
        if identity_pass and self.restore_pass:
            try:
                self.verify_seed()
                restored = self.checked("old-independent-reopen", py, DATA_SCRIPT,
                                        ["restored-full", str(self.restored_home), str(self.seed)])
                self.restored_pass = True
                self.restored_storage = restored["before_storage"]
            except Exception as exc:
                self.failures.append("old-independent-reopen-error:" + type(exc).__name__)
                pass
        lifecycle_ready = package_pass and identity_pass and self.candidate_pass and self.restore_pass and self.restored_pass and not self.failures
        storage = getattr(self, "restored_storage", None)
        differences = None
        history_identical = False
        schema_version_identical = False
        if self.backup_verified is not None and storage is not None:
            readback = (self.full_restore_receipt or {}).get("verified_model", {})
            restored_schema_rows = storage.get("schema_version")
            expected_schema = (self.backup_verified.get("manifest") or {}).get("database", {}).get("schema_version")
            expected_history = self.backup_verified.get("durable_history_sha256")
            observed_history = readback.get("durable_history_sha256")
            observed_schema = readback.get("database_schema_version")
            valid_expected_history = (isinstance(expected_history, str)
                                      and re.fullmatch(r"[0-9a-f]{64}", expected_history) is not None)
            valid_observed_history = (isinstance(observed_history, str)
                                      and re.fullmatch(r"[0-9a-f]{64}", observed_history) is not None)
            valid_expected_schema = type(expected_schema) is int and expected_schema > 0
            valid_observed_schema = type(observed_schema) is int and observed_schema > 0
            history_identical = valid_expected_history and valid_observed_history and expected_history == observed_history
            schema_version_identical = (valid_expected_schema and valid_observed_schema
                                        and expected_schema == observed_schema
                                        and restored_schema_rows == [[expected_schema]])
            differences = {"bytes_identical": None, "schema_identical": None,
                           "schema_version_identical": schema_version_identical,
                           "durable_history_sha256_identical": history_identical,
                           "expected_schema_version": expected_schema if valid_expected_schema else None,
                           "observed_schema_version": observed_schema if valid_observed_schema else None,
                           "expected_durable_history_sha256": expected_history if valid_expected_history else None,
                           "observed_durable_history_sha256": observed_history if valid_observed_history else None,
                           "note": "full restore rebinds project/workspace/host metadata; archive database bytes are not expected to equal the rebound database"}
            if not history_identical and "restored-history-mismatch" not in self.failures:
                self.failures.append("restored-history-mismatch")
            if not schema_version_identical and "restored-schema-version-mismatch" not in self.failures:
                self.failures.append("restored-schema-version-mismatch")
        passed = lifecycle_ready and history_identical and schema_version_identical
        return {"status": "PASS_DISPOSABLE_CODE_DATA_LIFECYCLE" if passed else "FAIL_OR_UNKNOWN_DATA_LIFECYCLE",
                "package": "PASS" if package_pass else "FAIL_OR_UNKNOWN",
                "code_identity": "PASS" if all(self.identities.get(n) is True for n in ("old", "candidate", "restored")) else "FAIL_OR_UNKNOWN",
                "data": "PASS" if passed else "FAIL_OR_UNKNOWN",
                "offline": "PASS_PROCESS_ISOLATED" if passed else "FAIL_OR_UNKNOWN",
                "release_acceptance": "NOT_RUN", "matrix_acceptance": "NOT_RUN",
                "native": "NOT_RUN", "science": "NOT_RUN", "failures": self.failures,
                "same_version_different_build": self.old["version"] == self.new["version"],
                "bootstrap_exceptions": self.bootstrap, "steps": self.steps,
                "installed_identity_checks": self.identities,
                "input_manifests": [{"path": str(self.evidence / name), "sha256": self.release.sha256_file(self.evidence / name)}
                                    for name in ("old-wheel-input.json", "candidate-wheel-input.json")],
                "old_generated_seed_sha256": getattr(self, "seed_sha256", None),
                "registered_model": self.project_artifact,
                "backup_storage": self.backup_verified,
                "full_restore": self.full_restore_receipt,
                "zero_solve_native_reopen_entry": str(self.native_reopen_entry) if self.native_reopen_entry.exists() else None,
                "restored_storage_before_old_open": getattr(self, "restored_storage", None),
                "backup_vs_restored_storage": differences,
                "restore_semantics": "production full restore rebinds a verified ledger plus every registered project file into a new empty home/workspace; candidate ArtifactStore readback and old-code historical-ledger reopen are separate; native model reopen and solve remain NOT_RUN"}
