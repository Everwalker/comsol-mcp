"""Fixed, no-engine data profile for disposable release transitions.

This module does not install packages or open an application ledger in the
supervisor. The release driver owns installation/rollback; fixed isolated
children use only the explicitly verified installed package and synthetic home.
"""
from __future__ import annotations

import email
import hashlib
import json
import pathlib
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
  assert logical(store.db)==seed['logical'], 'original data changed before replay/reconciliation'
  for key,expected in seed['operations'].items():
   op,replay=store.begin(request_id='new-request-'+key,idempotency_key='w26-'+key,request_hash='hash-'+key,operation='synthetic.release.'+key,metadata={'synthetic_only':True})
   assert replay is True and op['operation_id']==expected['operation_id'] and op['job_id']==expected['job_id']
   assert op['result']==expected['result']
   try: store.begin(request_id='conflict',idempotency_key='w26-'+key,request_hash='wrong-hash',operation='synthetic.release.'+key)
   except IdempotencyConflict: pass
   else: raise AssertionError('idempotency conflict not refused')
  assert logical(store.db)==seed['logical'], 'idempotency replay changed original rows'
  terminal=seed['operations']['terminal']; unknown=seed['operations']['unknown']
  old_terminal=store.get_operation(terminal['operation_id'])
  assert old_terminal['status']=='SUCCEEDED'
  observation=store.get_operation(unknown['operation_id'])
  assert observation['status']=='UNKNOWN' and observation['finished_at'] is None
  assert observation['result']['safe_retry'] is False and observation['result']['requires_model_reconciliation'] is True
  reconciled=[]
  if phase=='candidate':
   reconciled=store.reconcile_after_restart()
   assert reconciled==[unknown['job_id']]
   after=store.get_operation(unknown['operation_id'])
   assert after['status']=='RECONCILING' and after['finished_at'] is None and after['result']==observation['result']
   assert store.job(unknown['job_id'])['status']=='RECONCILING'
   assert store.get_operation(terminal['operation_id'])==old_terminal
   assert store.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0]==2
   assert store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==2
  elif phase!='restored': raise AssertionError('unknown data phase')
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
        self.seed = evidence / "old-generated-seed.json"
        self.backup = evidence / "before-candidate-open.sqlite3"
        self.backup_manifest = pathlib.Path(str(self.backup) + ".manifest.json")
        self.backup_verified: dict[str, Any] | None = None
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
        self.checked("production-backup", py, BACKUP_SCRIPT, ["backup", "--home", str(self.home), "--destination", str(self.backup)])
        verified = self.checked("verify-backup-before-open", py, BACKUP_VERIFY_SCRIPT, [str(self.backup), str(self.backup_manifest)])
        seed = json.loads(self.seed.read_text())
        if verified["legacy_data_sha256"] != digest(seed["logical"]):
            self.failures.append("backup-old-data-mismatch")
            raise self.release.ReleaseError("backup does not preserve old-generated data")
        self.backup_verified = verified
        self.backup_manifest_sha256 = self.release.sha256_file(self.backup_manifest)
        self.checked("candidate-code-reopen", py, DATA_SCRIPT, ["candidate", str(self.home), str(self.seed)])
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
        before = self.checked("verify-backup-before-restore", py, BACKUP_VERIFY_SCRIPT, [str(self.backup), str(self.backup_manifest)])
        if before != self.backup_verified:
            self.failures.append("backup-changed")
            raise self.release.ReleaseError("verified backup changed")
        self.checked("production-restore", py, BACKUP_SCRIPT, ["restore", "--home", str(self.home), "--backup", str(self.backup), "--manifest", str(self.backup_manifest), "--confirm"])
        after = self.checked("verify-backup-after-restore", py, BACKUP_VERIFY_SCRIPT, [str(self.backup), str(self.backup_manifest)])
        if after != before or self.release.sha256_file(self.backup_manifest) != self.backup_manifest_sha256:
            self.failures.append("backup-changed-during-restore")
            raise self.release.ReleaseError("backup evidence changed during restore")
        self.restore_pass = True

    def finish(self, py: pathlib.Path, package_pass: bool, identity_pass: bool) -> dict[str, Any]:
        if identity_pass and self.restore_pass:
            try:
                self.verify_seed()
                restored = self.checked("old-independent-reopen", py, DATA_SCRIPT, ["restored", str(self.home), str(self.seed)])
                self.restored_pass = True
                self.restored_storage = restored["before_storage"]
            except Exception as exc:
                self.failures.append("old-independent-reopen-error:" + type(exc).__name__)
                pass
        passed = package_pass and identity_pass and self.candidate_pass and self.restore_pass and self.restored_pass and not self.failures
        storage = getattr(self, "restored_storage", None)
        differences = None
        if self.backup_verified is not None and storage is not None:
            differences = {"bytes_identical": self.backup_verified["sha256"] == storage["sha256"],
                           "schema_identical": self.backup_verified["schema"] == storage["schema"],
                           "schema_version_identical": self.backup_verified["schema_version"] == storage["schema_version"]}
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
                "backup_storage": self.backup_verified,
                "restored_storage_before_old_open": getattr(self, "restored_storage", None),
                "backup_vs_restored_storage": differences,
                "restore_semantics": "production restore migrates a verified backup copy; original data compatibility is tested separately; restored bytes/schema need not equal the old backup"}
