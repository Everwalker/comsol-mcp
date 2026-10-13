import urllib.request,urllib.parse,os,json,hashlib,time,sys
from pathlib import Path
root=Path(__file__).resolve().parent
urls=[("cpython_v3_13_16_ssl.c","https://raw.githubusercontent.com/python/cpython/v3.13.16/Modules/_ssl.c",524288),("cpython_141848.patch","https://github.com/python/cpython/pull/141848.patch",1048576),("openssl_asn1_string.html","https://openssl-library.org/post/2026-04-13-asn1_string/",524288)]
get_count=0
class Redirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,req,fp,code,msg,headers,newurl):
  global get_count
  if get_count>=4:raise RuntimeError("four actual public GET cap reached before redirect")
  get_count+=1
  parsed=urllib.parse.urlsplit(newurl)
  if parsed.scheme!="https" or parsed.hostname not in {"github.com","raw.githubusercontent.com","patch-diff.githubusercontent.com","openssl-library.org"}:raise RuntimeError("unreviewed source redirect")
  redirects.append({"from":req.full_url,"to":newurl,"status":code})
  return super().redirect_request(req,fp,code,msg,headers,newurl)
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),Redirect())
receipts=[]
for name,url,cap in urls:
 if get_count>=4:break
 get_count+=1
 redirects=[];started=time.monotonic();record={"filename":name,"url":url,"max_bytes":cap,"headers":{"User-Agent":"comsol-private-source-review/1"},"redirects":redirects}
 (root/(name+".request.json")).write_text(json.dumps(record,indent=2))
 fd=os.open(root/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);size=0;digest=hashlib.sha256()
 try:
  with opener.open(urllib.request.Request(url,headers=record["headers"]),timeout=60) as response:
   record.update(status=response.status,final_url=response.url)
   while chunk:=response.read(1048576):
    size+=len(chunk)
    if size>cap:raise RuntimeError("official source exceeds cap")
    digest.update(chunk);view=memoryview(chunk)
    while view:view=view[os.write(fd,view):]
  os.fsync(fd);record.update(result="ACQUIRED",bytes=size,sha256=digest.hexdigest())
 except BaseException as exc:record.update(result="FAILED",error_type=type(exc).__name__,cause=str(exc),partial_bytes=size)
 finally:os.close(fd)
 record["elapsed_s"]=time.monotonic()-started
 (root/(name+".result.json")).write_text(json.dumps(record,indent=2));receipts.append(record);print(json.dumps(record),flush=True)
 if record["result"]!="ACQUIRED":break
(root/"RESULT.json").write_text(json.dumps({"public_initial_GETs":len(receipts),"actual_GETs_including_redirects":get_count,"receipts":receipts,"code_execution":False,"TLS_build_started":False},indent=2))
