"""Verbatim original readonly exact thin/fat/archive validators; no native effects."""
import hashlib,struct,re
from tools.w26_prepare_current_inputs import _helpers
Refusal = _helpers().Refusal
CPUS={"arm64":0x100000c,"x86_64":0x1000007}

def canonical_system_dylib(path):
    # Pure syntax, retaining the actual string; dyld-cache paths need not exist.
    if not isinstance(path,str) or not path.startswith('/') or '\x00' in path:
        raise Refusal('dynamic dependency is not an absolute canonical POSIX path')
    components=path.split('/')[1:]
    if any(not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.+-]*',component) or component in ('.','..') for component in components):
        raise Refusal('dynamic dependency has noncanonical POSIX components')
    if components[:2]==['usr','lib']:
        if len(components)<3 or not components[-1].endswith('.dylib'):raise Refusal('noncanonical system dylib suffix/layout')
    elif components[:2]==['System','Library']:
        if components[-1].endswith('.dylib'):
            if len(components)<3:raise Refusal('missing system dylib basename')
        elif len(components)>=4 and components[2] in ('Frameworks','PrivateFrameworks') and components[3].endswith('.framework'):
            framework=components[3].removesuffix('.framework')
            suffix=components[4:]
            if suffix!=[framework] and not (len(suffix)==3 and suffix[0]=='Versions' and suffix[2]==framework):
                raise Refusal('noncanonical system framework binary suffix/layout')
        else:raise Refusal('dynamic dependency has no recognized system library suffix')
    else:raise Refusal('target binary has non-system dynamic dependency')
    return path

def thin(raw,arch,filetypes):
 if len(raw)<32:raise Refusal('short Mach-O')
 magic,cpu,sub,kind,n,nb,flags,res=struct.unpack_from('<IiiIIIII',raw)
 if magic!=0xfeedfacf or cpu!=CPUS[arch] or kind not in filetypes or n>4096 or 32+nb>len(raw):raise Refusal('wrong exact thin architecture/type/command bounds')
 pos=32;versions=[];libs=[]
 for _ in range(n):
  if pos+8>32+nb:raise Refusal('truncated Mach-O command')
  command,size=struct.unpack_from('<II',raw,pos)
  if size<8 or size%4 or pos+size>32+nb:raise Refusal('bad Mach-O command size')
  if command==0x32:
   if size<24:raise Refusal('short build version')
   _,_,plat,minimum,sdk,tools=struct.unpack_from('<IIIIII',raw,pos)
   if plat!=1 or size!=24+tools*8:raise Refusal('wrong macOS build platform/tools')
   versions.append(minimum)
  elif command==0x24:
   if size!=16:raise Refusal('bad minOS command')
   versions.append(struct.unpack_from('<IIII',raw,pos)[2])
  elif command in (0xc,0x80000018,0x8000001f,0x20,0x80000023):
   if size<24:raise Refusal('short dylib')
   start=struct.unpack_from('<I',raw,pos+8)[0]
   if not 24<=start<size:raise Refusal('bad dylib offset')
   encoded=raw[pos+start:pos+size]
   if b'\0' not in encoded:raise Refusal('unterminated dylib')
   name,padding=encoded.split(b'\0',1)
   if any(padding):raise Refusal('nonzero dylib padding')
   libs.append(canonical_system_dylib(name.decode('utf8')))
  pos+=size
 if pos!=32+nb or versions!=[0xb0000]:raise Refusal('actual target minimum OS is not exact11.0')
 return {'architecture':arch,'filetype':kind,'minimum_os':[11,0,0],'linked_dylibs':libs,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'executed':False}

def fat_slices(raw):
 if len(raw)<48 or len(raw)>268435456 or struct.unpack_from('>II',raw)!=(0xcafebabe,2):raise Refusal('not bounded exact two-slice universal file')
 result={};ranges=[]
 for i in range(2):
  cpu,sub,offset,size,align=struct.unpack_from('>iiIII',raw,8+20*i)
  arch=next((a for a,c in CPUS.items() if c==cpu),None)
  if arch is None or arch in result or align>30 or offset<48 or offset%(1<<align) or offset+size>len(raw):raise Refusal('fat architecture/alignment/bounds mismatch')
  result[arch]=raw[offset:offset+size];ranges.append((offset,offset+size))
 if max(ranges[0][0],ranges[1][0])<min(ranges[0][1],ranges[1][1]):raise Refusal('fat slices overlap')
 return result

def static_archive(raw,arch):
 if len(raw)>134217728 or not raw.startswith(b'!<arch>\n'):raise Refusal('not bounded thin ar')
 pos=8;members=[];seen=set();symbols=[]
 while pos<len(raw):
  if pos+60>len(raw):raise Refusal('ar header truncated')
  h=raw[pos:pos+60];pos+=60
  if h[58:60]!=b'`\n':raise Refusal('ar header trailer mismatch')
  try:size=int(h[48:58].decode().strip())
  except ValueError as exc:raise Refusal('ar size malformed') from exc
  if size<0 or pos+size>len(raw):raise Refusal('ar member overruns')
  data=raw[pos:pos+size];name=h[:16].decode().strip()
  if name.startswith('#1/'):
   try:length=int(name[3:])
   except ValueError as exc:raise Refusal('ar name length malformed') from exc
   if length<1 or length>len(data):raise Refusal('ar name overrun')
   name=data[:length].rstrip(b'\0').decode();data=data[length:]
  else:name=name.rstrip('/')
  if not name or '/' in name or name in seen:raise Refusal('ar name duplicate/noncanonical')
  seen.add(name)
  if name in ('__.SYMDEF','__.SYMDEF SORTED','__.SYMDEF_64','__.SYMDEF_64 SORTED'):symbols.append(name)
  else:members.append({'name':name,**thin(data,arch,(1,))})
  pos+=size
  if pos%2:
   if raw[pos:pos+1]!=b'\n':raise Refusal('ar padding malformed')
   pos+=1
 if pos!=len(raw) or not members:raise Refusal('ar empty/misaligned')
 return {'architecture':arch,'archive_bytes':len(raw),'archive_sha256':hashlib.sha256(raw).hexdigest(),'object_count':len(members),'objects':members,'symbol_tables':symbols}

def fat_archive(raw,inputs):
 slices=fat_slices(raw);result={}
 for arch,data in slices.items():
  if data!=inputs[arch]:raise Refusal('lipo output slice is not exact original thin archive bytes')
  result[arch]=static_archive(data,arch)
 return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'slices':result,'writer':'official Apple lipo only'}

def fat_binary(raw,filetypes):
 return {arch:thin(data,arch,filetypes) for arch,data in fat_slices(raw).items()}
