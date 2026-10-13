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
write_json(rfd,'OWNER.json',{'uid':os.getuid(),'security':security,'argv':sys.argv,'scope':'official OpenSSL3.5.9 metadata/publickey/signature only; no sourcecode execution'})
items=[('official-source.html','https://openssl-library.org/source/',1048576),('openssl-3.5.9.tar.gz.sha256','https://openssl-library.org/source/openssl-3.5.9.tar.gz.sha256',4096),('openssl-3.5.9.tar.gz.asc','https://openssl-library.org/source/openssl-3.5.9.tar.gz.asc',16384),('pubkeys.asc','https://openssl-library.org/source/pubkeys.asc',262144)]
for name,url,cap in items:
 redirect=Redirect();partial=name+'.'+uuid.uuid4().hex+'.partial';fd=os.open(partial,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=rfd);h=hashlib.sha256();size=0;row={'url':url,'maximum_bytes':cap,'partial':partial,'started_unix':time.time()}
 write_json(rfd,name+'.request.json',row)
 try:
  check_url(url,{'openssl-library.org'})
  with urllib.request.build_opener(redirect).open(urllib.request.Request(url,headers={'User-Agent':'comsol-official-input/1','Accept-Encoding':'identity'}),timeout=90) as response:
   if response.status!=200 or response.headers.get('Content-Encoding','identity')!='identity':raise Refusal('metadata HTTP/encoding mismatch')
   row.update(http_status=response.status,content_length=response.headers.get('Content-Length'),final={'scheme':urlsplit(response.geturl()).scheme,'host':urlsplit(response.geturl()).hostname,'path':urlsplit(response.geturl()).path})
   while raw:=response.read(min(1048576,cap+1-size)):
    size+=len(raw);view=memoryview(raw)
    while view:view=view[os.write(fd,view):]
    h.update(raw)
    if size>cap:raise Refusal('metadata cap exceeded')
  os.fsync(fd);os.link(partial,name,src_dir_fd=rfd,dst_dir_fd=rfd,follow_symlinks=False);os.unlink(partial,dir_fd=rfd);os.fsync(rfd);row['status']='CAPTURED_METADATA_NOT_SOURCE_AUTH'
 except BaseException as exc:
  row.update(status='FAILED_RETAINED_PARTIAL',error_type=type(exc).__name__,error=str(exc));raise
 finally:
  os.fsync(fd);os.close(fd);row.update(actual_bytes=size,sha256=h.hexdigest(),redirects=redirect.rows,finished_unix=time.time());write_json(rfd,name+'.result.json',row)
 print(json.dumps({'name':name,'bytes':size,'sha256':h.hexdigest()}),flush=True)
os.close(rfd)
