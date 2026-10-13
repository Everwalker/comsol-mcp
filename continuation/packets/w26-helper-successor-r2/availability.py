"""Finite lock/metadata availability census, no import/install of any wheel."""
import json,tomllib,io,zipfile,ast,hashlib,os
from email import policy
from email.parser import BytesParser
from pathlib import Path
from packaging.markers import Marker,default_environment
from packaging.requirements import Requirement
from strict_derived import read_regular
R=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');D=Path(__file__).parent
E=R/'repository/docs/full_project_continuation_v2/evidence';W=E/'w25-native-delivery-preparation-r2';B=E/'intel-builder-execution-r1'
P=Path('/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-intel-inputs-r1-obneuyy4')
env=default_environment();env.update(sys_platform='darwin',platform_system='Darwin',platform_machine='arm64',os_name='posix',python_version='3.12',python_full_version='3.12.13',implementation_version='3.12.13',implementation_name='cpython',platform_python_implementation='CPython',extra='')
def marker(s,extra=''):return not s or Marker(s).evaluate({**env,'extra':extra})
def obj(p):return json.loads(read_regular(p)[0])
lock=tomllib.loads(read_regular(R/'repository/uv.lock')[0].decode());project=tomllib.loads(read_regular(R/'repository/pyproject.toml')[0].decode())
by={}
for q in lock['package']:
 if not q.get('resolution-markers') or any(marker(m) for m in q['resolution-markers']):by.setdefault(q['name'],[]).append(q)
def select(name):
 rows=by.get(name,[])
 if len(rows)!=1:raise RuntimeError(f'active lock record not unique: {name}')
 return rows[0]
root=select('comsol-mcp');pending=[(q['name'],tuple(q.get('extra',[]))) for q in root['dependencies']];seen=set();selected={}
while pending:
 name,extras=pending.pop()
 if (name,extras) in seen:continue
 seen.add((name,extras));q=select(name);selected[name]=q
 deps=list(q.get('dependencies',[]))
 for extra in extras:deps+=q.get('optional-dependencies',{}).get(extra,[])
 for dep in deps:
  if marker(dep.get('marker','')):pending.append((dep['name'],tuple(dep.get('extra',[]))))
plan=obj(E/'release_data_lifecycle/release-delivery-plan-r1/download-plan.json')
arm={q['name']:q for q in plan['targets']['macos_arm64']['artifacts']}
manifest=obj(W/'INTEL_X86_ARCHIVE_ACQUISITION_MANIFEST_R1.json')
manifestrows=next(v for v in manifest.values() if isinstance(v,list) and v and isinstance(v[0],dict) and 'artifact_id' in v[0])
known={q['filename']:{'path':str(P/'archives'/q['filename']),'url':q['url'],'sha256':q['expected_sha256'],'bytes':q['expected_bytes']} for q in manifestrows if q['filename'].endswith('.whl')}
desktop=obj(W/'ARM64_RUNTIME_INSTALL_RECEIPT_R2.json');dp=obj(W/'ISOLATED_ARM64_RUNTIME_PLAN_R2.json');wheelurls={q['filename']:q['url'] for q in dp['wheelhouse']['wheels']}
for name,row in desktop['verified_wheels'].items():known[name]={'path':str(Path(desktop['task_root'])/'wheelhouse'/name),'url':wheelurls.get(name),'sha256':row['sha256'],'bytes':row['size_bytes']}
observed={};metadata_refs=[]
for filename,k in known.items():
 raw,rec=read_regular(Path(k['path']))
 if rec['sha256']!=k['sha256'] or rec['bytes']!=k['bytes']:raise RuntimeError('original available wheel differs from receipt')
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  names=[n for n in z.namelist() if n.count('/')==1 and n.endswith('.dist-info/METADATA')]
  if len(names)!=1:raise RuntimeError('top-level wheel metadata not unique')
  meta=BytesParser(policy=policy.default).parsebytes(z.read(names[0]));name=meta['Name'].lower().replace('_','-');version=meta['Version']
  if meta.defects:raise RuntimeError('metadata defects')
  observed[name]={'filename':filename,'version':version,'source_url':k['url'],'bytes':rec['bytes'],'sha256':rec['sha256'],'available':True,'actual_original_record':rec,'Requires-Dist':meta.get_all('Requires-Dist',[]),'METADATA_sha256':hashlib.sha256(z.read(names[0])).hexdigest()}
  metadata_refs.append(rec)
rows=[]
for name,q in sorted(selected.items()):
 a=arm[name]
 if a['version']!=q['version']:raise RuntimeError('old target plan differs from current active lock')
 uv=[w for w in q.get('wheels',[]) if w['url']==a['url'] and w['hash']=='sha256:'+a['sha256'] and w['size']==a['size_bytes']]
 if len(uv)!=1:raise RuntimeError('target wheel not exact current lock')
 found=observed.get(name)
 if found and (found['filename']!=a['filename'] or found['sha256']!=a['sha256']):raise RuntimeError('available original differs from selected lock')
 rows.append({'name':name,'version':q['version'],'filename':a['filename'],'source_url':a['url'],'bytes':a['size_bytes'],'sha256':a['sha256'],'available':bool(found),'metadata':'ACTUAL_WHEEL_METADATA' if found else 'NOT_AVAILABLE_WHEEL_NOT_FETCHED','record':found})
# Desktop supplement remains exact external overlay, never pretend uv.lock has it.
drows=[]
for name in desktop['distribution_versions']:
 a=observed[name];drows.append({'name':name,**a,'scope':'EXTERNAL_ROOT_REVIEWED_DESKTOP_OVERLAY_NOT_IN_UV_LOCK'})
for a in drows:
 for text in a['Requires-Dist']:
  req=Requirement(text)
  if req.marker and not req.marker.evaluate(env):continue
  dep=observed.get(req.name.lower().replace('_','-'))
  if not dep or dep['version'] not in req.specifier:raise RuntimeError('desktop available metadata dependency closure failed '+text)
# Explicit pyproject consumer requirements must accept this external fixed overlay.
for text in project['project']['optional-dependencies']['desktop']:
 req=Requirement(text)
 if req.marker and not req.marker.evaluate(env):continue
 dep=observed.get(req.name.lower().replace('_','-'))
 if not dep or dep['version'] not in req.specifier:raise RuntimeError('desktop constraint unsatisfied')
backend=[{'name':'setuptools','declared_requirement':'setuptools>=68',**observed['setuptools']},{'name':'wheel','declared_requirement':'wheel','version_selection':'0.48.0 PROPOSED_FROM_PRIOR_EXTRACTED_CACHE_NOT_APPROVED_ARTIFACT','filename':'wheel-0.48.0-py3-none-any.whl PROPOSED_REQUIRES_OFFICIAL_METADATA','source_url':None,'sha256':None,'bytes':None,'available':False,'metadata':'ORIGINAL_WHEEL_NOT_BOUND; DEPENDENCIES_UNRESOLVED'}]
missing=[r for r in rows if not r['available']]
result={'schema':'comsol-w26-finite-availability/1','status':'PREPARATION_NOT_COMPLETE_INSTALL_CLOSURE','marker_environment':env,'base_runtime':rows,'desktop_external_overlay':drows,'backend':backend,'counts':{'base_runtime':len(rows),'base_original_available':len(rows)-len(missing),'base_missing':len(missing),'desktop_original_available':len(drows),'backend_available':1,'backend_unresolved':1},'missing_base_exact_acquisition':missing,'precise_acquisition_plan':{'status':'NOT_EXECUTED_NOT_AUTHORIZED_BY_THIS_SOURCE_PACKET','runtime':'Exactly missing_base_exact_acquisition URLs/bytes/SHA, no dependencies/floating resolver; original acquire_exact_archives policy privateprefix/noclobber/HTTPSfirstparty/durable receipts first mismatch stop; manifest whole review before network.','backend':'First one public GET https://pypi.org/pypi/wheel/0.48.0/json capped2MiB to fixed private raw; verify Name/version/file identity and canonical files.pythonhosted URL/digest/size/RequiresDist. Then submit exact backend file+any missing locked packaging RequiresDist input to Root before acquisition. No unknown metadata filled or third-party mirror.','max_known_runtime_GETs':len(missing),'known_runtime_aggregate_bytes':sum(r['bytes'] for r in missing)},'limited_scope':'Active lock base-runtime graph and fixed desktop original-wheel RequiresDist only. Dev pytest intentionally excluded from runtime graph; old base+dev target list is cross-check, not new lock. No transitive stdlib completeness claim; old gdbm/tkinter diagnostics unchanged.','effects':{'network':0,'target_python':0,'pip':0,'native':0,'unpack':0},'original_wheel_records':metadata_refs}
p=D/'AVAILABILITY_R2.json'
with p.open('xb') as f:f.write((json.dumps(result,indent=2,sort_keys=True)+'\n').encode());f.flush();os.fsync(f.fileno())
print(result['counts']);print('missing exact bytes',result['precise_acquisition_plan']['known_runtime_aggregate_bytes'])
