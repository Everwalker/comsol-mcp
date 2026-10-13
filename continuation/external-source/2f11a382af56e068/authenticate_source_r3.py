import os,sys,json,subprocess,time,hashlib,stat
from pathlib import Path
root=Path(__file__).parent;home=Path('/private/tmp/os359-gpg-jpt29w9j')
from security_snapshot._host_scoped_provider import verify_private_path_security
verify_private_path_security(home,directory=True)
common=['/opt/homebrew/bin/gpg','--no-options','--homedir',str(home),'--batch','--no-tty','--no-autostart','--no-auto-key-retrieve']
key=root/'metadata-r2/pubkeys.asc';sig=root/'metadata-r2/openssl-3.5.9.tar.gz.asc';source=root/'archives/openssl-3.5.9.tar.gz'
pins={key:'56e106cd1c44bdb117aec24795f6dfbecaa9bfa0a2901b85331f0059aad16d53',sig:'bdccf2057a7c4ae377b40e832cb8c632ae8b532d4fb64cc7b843066eee748f89',source:'603f5602e2eef00d77fbd429d34dcd5822bb301757a1bc9cdb24c670f1eb859a'}
def verify(path,sha):
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
 try:
  st=os.fstat(fd)
  if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or st.st_nlink!=1 or stat.S_IMODE(st.st_mode)!=0o600:raise RuntimeError('gpg fixed input owner/mode/link differs')
  h=hashlib.sha256()
  while raw:=os.read(fd,1048576):h.update(raw)
  end=os.fstat(fd)
  fields=lambda s:(s.st_dev,s.st_ino,s.st_uid,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
  if h.hexdigest()!=sha or fields(end)!=fields(st):raise RuntimeError('gpg sameFD input changed')
  return {'path':str(path),'bytes':st.st_size,'sha256':sha,'uid':st.st_uid,'mode':stat.S_IMODE(st.st_mode),'device':st.st_dev,'inode':st.st_ino,'nlink':st.st_nlink}
 finally:os.close(fd)
def save(name,value):
 fd=os.open(root/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 try:
  raw=memoryview((json.dumps(value,sort_keys=True,indent=2)+'\n').encode())
  while raw:raw=raw[os.write(fd,raw):]
  os.fsync(fd)
 finally:os.close(fd)
def command(label,argv):
 save(label+'.argv.json',{'argv':argv,'actual_parent_pid':os.getpid(),'system_home_unchanged':True});start=time.monotonic()
 with (root/(label+'.stdout')).open('xb') as out,(root/(label+'.stderr')).open('xb') as err:
  p=subprocess.Popen(argv,stdout=out,stderr=err,env=dict(os.environ,GNUPGHOME=str(home)));save(label+'.spawn.json',{'actual_pid':p.pid,'argv':argv});rc=p.wait();out.flush();err.flush();os.fsync(out.fileno());os.fsync(err.fileno())
 save(label+'.terminal.json',{'actual_rc':rc,'actual_pid':p.pid,'actual_wait_terminal':True,'elapsed_s':time.monotonic()-start})
 if rc!=0:raise RuntimeError('gpg '+label+' actual rc '+str(rc))
 return (root/(label+'.stdout')).read_text()
before={str(path):verify(path,sha) for path,sha in pins.items()}
listed=command('gpg-r3-inspect',common+['--with-colons','--import-options','show-only','--import',str(key)])
primary='B146647E45A7B33947AB226B2A2C87D161692D40'
if sum(line.startswith('fpr:') and line.split(':')[9]==primary for line in listed.splitlines())!=1:raise RuntimeError('canonical primary fingerprint not unique in official keys')
command('gpg-r3-import',common+['--status-fd','1','--import',str(key)])
valid=command('gpg-r3-verify',common+['--status-fd','1','--verify',str(sig),str(source)])
rows=[line.split() for line in valid.splitlines() if line.startswith('[GNUPG:] VALIDSIG ')]
if len(rows)!=1 or rows[0][-1]!=primary:raise RuntimeError('actual signature does not bind canonical primary fingerprint')
if not any(line.startswith('[GNUPG:] GOODSIG ') for line in valid.splitlines()):raise RuntimeError('actual GOODSIG absent')
after={str(path):verify(path,sha) for path,sha in pins.items()}
if before!=after:raise RuntimeError('gpg inputs changed during original verify')
save('AUTHENTICATED_SOURCE_RECEIPT.json',{'status':'PASS_ACTUAL_OFFICIAL_SHA256_AND_PUBLISHER_PGP','source_version':'OpenSSL3.5.9','fixed_primary_fingerprint':primary,'actual_signing_subkey_fingerprint':rows[0][2],'actual_validsig':rows[0],'inputs':before,'actual_native_compile':0,'actual_archive_unpack':0,'gpg_home':str(home),'system_gpg_home_changed':False,'network_in_gpg':0})
print('PASS_ACTUAL_3_5_9_SHA256_PUBLISHER_SIGNATURE',rows[0][2],flush=True)
