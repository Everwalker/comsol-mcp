"""Finite actual43 wheel metadata closure; never imports wheel code or installs."""
import hashlib,io,json,os,re,sys,zipfile,tomllib
from email import policy
from email.parser import BytesParser
from pathlib import Path
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name,parse_wheel_filename
from packaging.markers import Marker
R=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');D=Path(__file__).parent;A=D.parent/'w26-helper-successor-r2';X=D.parent/'w26-exact32-wrapper-r3';sys.path.insert(0,str(A));from strict_derived import read_regular
availability=json.loads(read_regular(A/'AVAILABILITY_R2.json')[0]);actual=json.loads(read_regular(X/'ACTUAL_EXECUTION_R3.json')[0]);new={q['record']['path'].rsplit('/',1)[-1]:q['record'] for q in actual['verified_archives']}
expected=[]
for q in availability['base_runtime']:
 record=q['record']['actual_original_record'] if q['available'] else new[q['filename']]
 expected.append({'name':q['name'],'version':q['version'],'record':record,'role':'runtime','source_url':q['source_url']})
for q in availability['desktop_external_overlay']:expected.append({'name':q['name'],'version':q['version'],'record':q['actual_original_record'],'role':'desktop_external_overlay','source_url':q['source_url']})
s=availability['backend'][0];expected.append({'name':'setuptools','version':s['version'],'record':s['actual_original_record'],'role':'backend','source_url':s['source_url']})
wm=next(q for q in json.loads(read_regular(A/'EXACT32_DOWNLOAD_MANIFEST.json')[0])['artifacts'] if q['artifact_id']=='backend-wheel-0.48.0');expected.append({'name':'wheel','version':'0.48.0','record':new[wm['filename']],'role':'backend','source_url':wm['url']})
assert len(expected)==43 and len({r['name'] for r in expected})==43
rows=[];by={};env=availability['marker_environment']
def compatible(tag):
 py=tag.interpreter;abi=tag.abi;platform=tag.platform
 pyok=py in ('py3','py2','py312','cp312') or (abi=='abi3' and py.startswith('cp3') and int(py[3:])<=12)
 if py=='py2':pyok=False
 if not pyok or abi not in ('none','abi3','cp312'):return False
 if platform=='any':return True
 m=re.fullmatch(r'macosx_(\d+)_(\d+)_(arm64|universal2)',platform)
 return bool(m) and (int(m[1]),int(m[2]))<=(12,0)
for e in expected:
 p=Path(e['record']['path']);raw,rec=read_regular(p)
 assert rec==e['record'];name,version,build,tags=parse_wheel_filename(p.name)
 assert canonicalize_name(e['name'])==name and str(version)==e['version'] and any(compatible(t) for t in tags)
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  infos=z.infolist();names=[q.filename for q in infos]
  if len(names)!=len(set(names)) or any(n.startswith('/') or '\\' in n or any(c in ('','..','.') for c in n.rstrip('/').split('/')) for n in names):raise RuntimeError('unsafe wheel member paths')
  if sum(q.file_size for q in infos)>536870912 or any(q.file_size>134217728 for q in infos):raise RuntimeError('wheel expanded metadata envelope exceeds cap')
  meta_paths=[n for n in names if n.count('/')==1 and n.endswith('.dist-info/METADATA')];wheel_paths=[n for n in names if n.count('/')==1 and n.endswith('.dist-info/WHEEL')]
  if len(meta_paths)!=1 or len(wheel_paths)!=1:raise RuntimeError('metadata identity not unique')
  metaraw=z.read(meta_paths[0]);wheelraw=z.read(wheel_paths[0]);m=BytesParser(policy=policy.default).parsebytes(metaraw);w=BytesParser(policy=policy.default).parsebytes(wheelraw)
  if m.defects or w.defects or len(m.get_all('Name',[]))!=1 or len(m.get_all('Version',[]))!=1:raise RuntimeError('malformed metadata')
  assert canonicalize_name(m['Name'])==name and m['Version']==e['version']
  declared=w.get_all('Tag',[]);assert len(declared)==len(set(declared)) and set(declared)=={str(t) for t in tags}
  reqpy=m.get('Requires-Python','');assert '3.12.13' in SpecifierSet(reqpy)
  row={**e,'filename':p.name,'actual_record':rec,'METADATA_sha256':hashlib.sha256(metaraw).hexdigest(),'WHEEL_sha256':hashlib.sha256(wheelraw).hexdigest(),'Requires-Python':reqpy,'Requires-Dist':m.get_all('Requires-Dist',[]),'Provides-Extra':m.get_all('Provides-Extra',[]),'internal_tags':declared,'filename_tags':sorted(str(t) for t in tags),'CP312_ARM_product_macOS12_tag_compatible':True}
  rows.append(row);by[name]=row
# Actual wheel metadata graph with extras propagated; optional unused requirements retained as inactive.
project=tomllib.loads(read_regular(R/'repository/pyproject.toml')[0].decode());pending=[]
for text in project['project']['dependencies']+project['project']['optional-dependencies']['desktop']:
 req=Requirement(text)
 if not req.marker or req.marker.evaluate({**env,'extra':'desktop'}):pending.append((canonicalize_name(req.name),tuple(sorted(req.extras))))
for name in ['setuptools','wheel']:pending.append((name,()))
visited=set();checks=[];active=set()
while pending:
 name,extras=pending.pop()
 if (name,extras) in visited:continue
 visited.add((name,extras));active.add(name);row=by[name]
 for text in row['Requires-Dist']:
  req=Requirement(text);applicable=not req.marker or any(req.marker.evaluate({**env,'extra':extra}) for extra in ('',)+extras)
  r={'producer':name,'extras':extras,'requirement':text,'applicable':applicable}
  if applicable:
   dep=canonicalize_name(req.name)
   if dep not in by:raise RuntimeError('missing actual metadata active dependency '+name+':'+text)
   if by[dep]['version'] not in req.specifier:raise RuntimeError('actual metadata dependency version mismatch '+text)
   r.update(selected=dep,selected_version=by[dep]['version'],status='PASS_ACTUAL_METADATA_AND_AVAILABLE_BYTES')
   pending.append((dep,tuple(sorted(req.extras))))
  else:r['status']='INACTIVE_MARKER_OR_UNUSED_EXTRA_NOT_RUNTIME_ACCEPTANCE'
  checks.append(r)
unused=sorted(set(by)-active)
# The originally reviewed desktop inventory includes CoreMedia/CoreText/SCK needed by Quartz; no arbitrary extra omitted.
result={'schema':'comsol-w26-actual43-input-closure/1','status':'PASS_FINITE_ACTUAL_WHEEL_METADATA_BYTES_MARKERS_NOT_INSTALL_RUNTIME','actual_inputs':rows,'counts':{'unique_wheels':43,'base_runtime':33,'desktop':8,'backend_new_names':2,'active_metadata_reachable':len(active),'dependency_checks':len(checks),'active_dependency_checks':sum(q['applicable'] for q in checks)},'marker_environment':env,'actual_dependency_checks':checks,'inactive_not_reachable_wheels':unused,'original_current_lock_unchanged':read_regular(R/'repository/uv.lock')[1],'actual32_receipt':read_regular(X/'ACTUAL_EXECUTION_R3.json')[1],'effects':{'wheel_code_imports':0,'target_python':0,'pip':0,'install':0,'unpack_to_fs':0,'network':0,'COMSOL':0},'limits':['Finite project base+desktop and backend active RequiresDist closure only; no transitive stdlib acceptance.','Tags/metadata are not target imports/native binary/hardware acceptance.','gdbm/tkinter historical IMPORT_FAILED/UNRESOLVED not edited or waived.','Three-architecture lifecycle/finalpackage/science acceptance remains OPEN.']}
for e in expected:assert read_regular(Path(e['record']['path']))[1]==e['record']
p=D/'ACTUAL43_METADATA_CLOSURE.json'
with p.open('x') as f:json.dump(result,f,indent=2,sort_keys=True);f.write('\n');f.flush();os.fsync(f.fileno())
print(result['counts']);print('unused',unused);print(read_regular(p)[1])
