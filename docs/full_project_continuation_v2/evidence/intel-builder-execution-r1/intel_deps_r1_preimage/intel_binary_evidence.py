"""Bounded Mach-O/static ar/wheel inspection; never executes Intel code."""
import hashlib
import struct
import zipfile
from acquire_exact_archives import Refusal

def x86_macho(raw, filetype):
    if len(raw)<32:raise Refusal('truncated Mach-O')
    magic,cpu,sub,kind,count,command_bytes,flags,reserved=struct.unpack_from('<IiiIIIII',raw)
    if magic!=0xfeedfacf or cpu!=0x1000007 or kind!=filetype or count>4096 or 32+command_bytes>len(raw):raise Refusal('not exact thin x86_64 Mach-O type')
    offset=32;versions=[];libraries=[]
    for _ in range(count):
        if offset+8>32+command_bytes:raise Refusal('truncated load command')
        command,size=struct.unpack_from('<II',raw,offset)
        if size<8 or size%4 or offset+size>32+command_bytes:raise Refusal('invalid load command size')
        if command==0x32:
            if size<24:raise Refusal('short build version')
            _,_,platform,minimum,sdk,tools=struct.unpack_from('<IIIIII',raw,offset)
            if platform!=1 or size!=24+tools*8:raise Refusal('wrong build platform/tools')
            versions.append(minimum)
        elif command==0x24:
            if size!=16:raise Refusal('wrong minversion size')
            versions.append(struct.unpack_from('<IIII',raw,offset)[2])
        elif command in (0xc,0x80000018,0x8000001f,0x20,0x80000023):
            if size<24:raise Refusal('short dylib command')
            start=struct.unpack_from('<I',raw,offset+8)[0]
            if not 24<=start<size:raise Refusal('bad dylib string offset')
            name=raw[offset+start:offset+size]
            if b'\0' not in name:raise Refusal('unterminated dylib path')
            libraries.append(name.split(b'\0')[0].decode('utf-8'))
        offset+=size
    if offset!=32+command_bytes or versions!=[0xb0000]:raise Refusal('x86 unit lacks exact macOS11.0 floor')
    allowed=('/usr/lib/','/System/Library/')
    if any(not path.startswith(allowed) for path in libraries):raise Refusal('target binary has non-system dynamic dependency')
    return {'architecture':'x86_64','filetype':filetype,'minimum_os':[11,0,0],'linked_dylibs':libraries,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'executed':False}

def inspect_static_archive(path):
    raw=path.read_bytes()
    if len(raw)>134217728 or not raw.startswith(b'!<arch>\n'):raise Refusal('not bounded regular ar archive')
    offset=8;members=[];names=set();symbols=[]
    while offset<len(raw):
        if offset+60>len(raw):raise Refusal('truncated ar header')
        header=raw[offset:offset+60];offset+=60
        if header[58:60]!=b'`\n':raise Refusal('wrong ar header trailer')
        try:size=int(header[48:58].decode().strip())
        except ValueError as exc:raise Refusal('invalid ar member size') from exc
        if size<0 or offset+size>len(raw):raise Refusal('ar member exceeds archive')
        data=raw[offset:offset+size];name=header[:16].decode('ascii').strip()
        if name.startswith('#1/'):
            try:length=int(name[3:])
            except ValueError as exc:raise Refusal('invalid extended ar name') from exc
            if length>len(data):raise Refusal('ar extended name overrun')
            name=data[:length].rstrip(b'\0').decode('utf-8');data=data[length:]
        else:name=name.rstrip('/')
        if not name or '/' in name or name in names:raise Refusal('noncanonical/duplicate ar member')
        names.add(name)
        if name in ('__.SYMDEF','__.SYMDEF SORTED','__.SYMDEF_64','__.SYMDEF_64 SORTED'):symbols.append(name)
        else:members.append({'name':name,**x86_macho(data,1)})
        offset+=size
        if offset%2:
            if raw[offset:offset+1]!=b'\n':raise Refusal('bad ar padding')
            offset+=1
    if offset!=len(raw) or not members:raise Refusal('empty/misaligned ar archive')
    return {'path':str(path),'archive_bytes':len(raw),'archive_sha256':hashlib.sha256(raw).hexdigest(),'symbol_tables':symbols,'object_members':members,'object_count':len(members),'pure_x86_execution':'NOT_RUN'}

def inspect_crypto_wheel(path):
    if path.stat().st_size>134217728:raise Refusal('target wheel exceeds bound')
    if not path.name.startswith('cryptography-50.0.1-') or not path.name.endswith('-macosx_11_0_x86_64.whl'):raise Refusal('target wheel identity/tag differs')
    with zipfile.ZipFile(path) as z:
        names=z.namelist()
        infos=z.infolist()
        if any(info.file_size>134217728 for info in infos) or sum(info.file_size for info in infos)>268435456:raise Refusal('target wheel expanded bound exceeded')
        if any(name.startswith('/') or '\\' in name or '..' in name.split('/') for name in names):raise Refusal('target wheel path escapes artifact')
        if len(names)!=len(set(names)):raise Refusal('duplicate target wheel member')
        extensions=[name for name in names if name.endswith('.so')]
        if extensions!=['cryptography/hazmat/bindings/_rust.abi3.so']:raise Refusal('unexpected target native extension closure')
        extension=x86_macho(z.read(extensions[0]),6)
        metadata=z.read('cryptography-50.0.1.dist-info/WHEEL').decode()
        if 'Tag: cp39-abi3-macosx_11_0_x86_64' not in metadata:raise Refusal('target ABI tag missing')
        return {'wheel':str(path),'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'extension':extension,'wheel_metadata':metadata,'Intel_runtime':'NOT_RUN'}
