"""Official public CPython certificate fixtures; no sockets or private user inputs."""
import hashlib,json,os,stat
from pathlib import Path

EXPECTED={
 'allsans.pem':{'subjectAltName':(('DNS','allsans'),('othername','<unsupported>'),('othername','<unsupported>'),('email','user@example.org'),('DNS','www.example.org'),('DirName',((('countryName','XY'),),(('localityName','Castle Anthrax'),),(('organizationName','Python Software Foundation'),),(('commonName','dirname example'),))),('URI','https://www.python.org/'),('IP Address','127.0.0.1'),('IP Address','0:0:0:0:0:0:0:1'),('Registered ID','1.2.3.4.5'))},
 'nullbytecert.pem':{'subjectAltName':(('DNS','altnull.python.org\0example.com'),('email','null@python.org\0user@example.org'),('URI','http://null.python.org\0http://example.org'),('IP Address','192.0.2.1'),('IP Address','2001:DB8:0:0:0:0:0:1'))},
 'nokia.pem':{'subjectAltName':(('DNS','projects.developer.nokia.com'),('DNS','projects.forum.nokia.com')),'OCSP':('http://ocsp.verisign.com',),'caIssuers':('http://SVRIntl-G3-aia.verisign.com/SVRIntlG3.cer',),'crlDistributionPoints':('http://SVRIntl-G3-crl.verisign.com/SVRIntlG3.crl',)}
}
FIXTURE_BYTES={
 'allsans.pem':{'bytes':10105,'sha256':'b6e7f6eeef758dd9f2f3f2645029471f09b8a8eb25670045d9d91fd24c597629'},
 'nullbytecert.pem':{'bytes':5435,'sha256':'3454585a198ffea4f78c67d58d1ebe89a0be1107612058e25ed4cb37964f2a71'},
 'nokia.pem':{'bytes':1923,'sha256':'b34d31d2e3c349a6b90c727f0b0ce5561837395749e3b7f8ce0a9075dd1201f4'},
 'badcert.pem':{'bytes':1928,'sha256':'262a107916641c7f211ac5898c0177535cd0bdc5aa872cc6e883842694d8f521'}
}
def checked_fixture(path,record):
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
 try:
  st=os.fstat(fd)
  if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or st.st_nlink!=1 or st.st_size!=record['bytes'] or st.st_size>16384:raise RuntimeError('official certificate fixture ownership/bound differs')
  raw=bytearray()
  while data:=os.read(fd,16384):raw.extend(data)
  final=os.fstat(fd)
  if len(raw)!=st.st_size or (final.st_dev,final.st_ino,final.st_size)!=(st.st_dev,st.st_ino,st.st_size) or hashlib.sha256(raw).hexdigest()!=record['sha256']:raise RuntimeError('official certificate fixture same-FD bytes differ')
  return {'identity':{'device':st.st_dev,'inode':st.st_ino},'bytes':st.st_size,'sha256':record['sha256']}
 finally:os.close(fd)
def validate_fields(name,decoded):
 if name not in EXPECTED:raise RuntimeError('unknown official certificate fixture')
 for key,expected in EXPECTED[name].items():
  if decoded.get(key)!=expected:raise RuntimeError('actual patched certificate field differs: '+name+':'+key)
 return {key:decoded[key] for key in EXPECTED[name]}
def runtime_cert_cases(ssl_module,root):
 fd=os.open(root/'CERT_FIXTURE_INPUTS.json',os.O_RDONLY|os.O_NOFOLLOW)
 try:
  st=os.fstat(fd)
  if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or st.st_nlink!=1 or st.st_size>8192:raise RuntimeError('certificate fixture manifest owner/bound differs')
  raw=os.read(fd,8193)
  if len(raw)!=st.st_size:raise RuntimeError('certificate manifest size changed')
  manifest=json.loads(raw)
 finally:os.close(fd)
 folder=root/'runtime-certfixtures';results=[]
 if set(manifest)!={*EXPECTED,'badcert.pem'}:raise RuntimeError('exact four official fixture set required')
 if manifest!=FIXTURE_BYTES:raise RuntimeError('official fixture source SHA/size binding differs')
 for name in (*EXPECTED,'badcert.pem'):
  path=folder/name;record=checked_fixture(path,manifest[name])
  if name=='badcert.pem':
   try:ssl_module._ssl._test_decode_cert(str(path))
   except ssl_module.SSLError as exc:results.append({'fixture':name,'input':record,'actual_malformed_rejected':True,'error_type':type(exc).__name__,'error':str(exc)})
   else:raise RuntimeError('actual malformed certificate was not rejected')
  else:
   decoded=ssl_module._ssl._test_decode_cert(str(path));fields=validate_fields(name,decoded);results.append({'fixture':name,'input':record,'actual_fields':fields,'expected_fields_matched':True})
 return {'actual_backend':'fresh ARM-selected patched _ssl._test_decode_cert','cases':results,'IPv4_IPv6_SAN_URI_AIA_CRL':True,'malformed_negative':True,'socket_calls':0}
