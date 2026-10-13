import hashlib,re,struct
class Refusal(RuntimeError):pass

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
            string,padding=name.split(b'\0',1)
            if any(padding):raise Refusal('dylib path has nonzero bytes after NUL terminator')
            try:path=string.decode('utf-8')
            except UnicodeDecodeError as exc:raise Refusal('invalid dylib path encoding') from exc
            libraries.append(canonical_system_dylib(path))
        offset+=size
    if offset!=32+command_bytes or versions!=[0xb0000]:raise Refusal('x86 unit lacks exact macOS11.0 floor')
    return {'architecture':'x86_64','filetype':filetype,'minimum_os':[11,0,0],'linked_dylibs':libraries,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'executed':False}
