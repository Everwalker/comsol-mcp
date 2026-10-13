import os,sys,json,hashlib,time,urllib.request,uuid
from pathlib import Path
from urllib.parse import urlsplit
D=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository/docs/full_project_continuation_v2/evidence/intel-builder-execution-r1');sys.path.insert(0,str(D))
from acquire_exact_archives import verify_temp_parent,write_json,check_url,Refusal
from security_snapshot._host_scoped_provider import verify_private_path_security
root=Path(__file__).parent;verify_temp_parent(root.parent);security=verify_private_path_security(root,directory=True)
rfd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
class Redirect(urllib.request.HTTPRedirectHandler):
 def __init__(self):self.rows=[]
 def redirect_request(self,req,fp,code,msg,headers,newurl):
  check_url(newurl,{'openssl-library.org','www.openssl-library.org','github.com','release-assets.githubusercontent.com'})
  if len(self.rows)>=5:raise Refusal('redirect cap')
  self.rows.append({'code':code,'scheme':urlsplit(newurl).scheme,'host':urlsplit(newurl).hostname,'path':urlsplit(newurl).path,'query':'NOT_STORED'})
  return super().redirect_request(req,fp,code,msg,headers,newurl)
url='https://github.com/openssl/openssl/releases/download/openssl-3.5.9/openssl-3.5.9.tar.gz'
redirect=Redirect()
request=urllib.request.Request(url,method='HEAD',headers={'User-Agent':'comsol-official-input/1','Accept-Encoding':'identity'})
write_json(rfd,'SOURCE_HEAD_REQUEST.json',{'url':url,'method':'HEAD','body_requested':False})
row={'url':url,'method':'HEAD'}
try:
 with urllib.request.build_opener(redirect).open(request,timeout=90) as response:
  check_url(response.geturl(),{'github.com','release-assets.githubusercontent.com'})
  size=int(response.headers['Content-Length'])
  if response.status!=200 or not 0<size<=67108864:raise Refusal('source HEAD status/size cap mismatch')
  row.update(status='CAPTURED_FIXED_OFFICIAL_SOURCE_LENGTH',http_status=response.status,bytes=size,final={'scheme':urlsplit(response.geturl()).scheme,'host':urlsplit(response.geturl()).hostname,'path':urlsplit(response.geturl()).path},redirects=redirect.rows)
except BaseException as exc:
 row.update(status='FAILED',error_type=type(exc).__name__,error=str(exc));raise
finally:write_json(rfd,'SOURCE_HEAD_RESULT.json',row);os.close(rfd)
print(json.dumps(row),flush=True)
