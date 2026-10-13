"""Exactly one authorized public metadata GET; no wheel download/install."""
import urllib.request,json,os,time,hashlib,sys
from pathlib import Path
URL='https://pypi.org/pypi/wheel/0.48.0/json';CAP=2097152
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args):raise RuntimeError('unexpected metadata redirect refused')
root=Path(sys.argv[1]);s=root.stat();assert s.st_uid==os.getuid() and s.st_mode&0o077==0
request={'url':URL,'method':'GET','body':None,'byte_cap':CAP,'timeout_seconds':60,'retry_count':0,'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'argv':sys.argv,'root_identity':{'dev':s.st_dev,'inode':s.st_ino,'uid':s.st_uid,'mode':oct(s.st_mode&0o777)},'started_unix':time.time()}
def write(name,value):
 with (root/name).open('x') as f:json.dump(value,f,indent=2,sort_keys=True);f.write('\n');f.flush();os.fsync(f.fileno())
write('REQUEST.json',request);result={'url':URL,'status':'UNKNOWN','network_calls':0};rc=1
try:
 with (root/'metadata.raw').open('xb') as f:
  result['network_calls']=1
  with urllib.request.build_opener(NoRedirect).open(urllib.request.Request(URL,headers={'Accept':'application/json','User-Agent':'COMSOL-MCP-W26-source-preparation/1'}),timeout=60) as response:
   result['http_status']=response.status;result['response_headers']=dict(response.headers);result['final_url']=response.url
   write('HTTP.json',{k:result[k] for k in ['http_status','response_headers','final_url']})
   if response.status!=200 or response.url!=URL:raise RuntimeError('unexpected HTTP identity/status')
   total=0;h=hashlib.sha256()
   while b:=response.read(min(65536,CAP+1-total)):
    f.write(b);total+=len(b);h.update(b)
    if total>CAP:raise RuntimeError('metadata exceeds authorized cap')
   f.flush();os.fsync(f.fileno());result.update(bytes=total,sha256=h.hexdigest())
 x=json.loads((root/'metadata.raw').read_bytes())
 if x['info']['name'].lower()!='wheel' or x['info']['version']!='0.48.0':raise RuntimeError('metadata name/version differs')
 write('PARSED.json',{'source_url':URL,'Name':x['info']['name'],'Version':x['info']['version'],'Requires-Dist':x['info'].get('requires_dist'), 'Requires-Python':x['info'].get('requires_python'),'files':[{k:q.get(k) for k in ['filename','url','size','digests','packagetype','python_version','requires_python','yanked']} for q in x['urls']]})
 result['status']='PASS_PUBLIC_METADATA_ONLY';rc=0
except BaseException as exc:
 result['exception']={'type':type(exc).__name__,'message':str(exc)};result['status']='FAILED_FIRST_NO_RETRY'
finally:
 result['finished_unix']=time.time();result['rc']=rc;write('RESULT.json',result);print(json.dumps({k:result.get(k) for k in ['status','rc','bytes','sha256','network_calls']}))
sys.exit(rc)
