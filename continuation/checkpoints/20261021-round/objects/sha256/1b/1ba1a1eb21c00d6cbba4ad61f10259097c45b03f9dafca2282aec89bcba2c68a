"""Fresh locked Intel cryptography build; execution only through the W26 owner gate.

Preparation reads official pinned inputs. It does not install or execute input
code. The shared actor must explicitly admit the new closed activity before
execute_intel can mint an owner. Sampling limits are not OS quotas.
"""
import argparse, ast, base64, csv, hashlib, io, json, os, re, shutil, socket, stat, struct, sys, tarfile, time, tomllib, zipfile
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from tools import w26_runtime_actor as actor, full_project_release as release
from tools.w26_prepare_current_inputs import _helpers, COMMON
KIND='CURRENT_INTEL_DERIVED_WHEEL'
TAG='cp312-abi3-macosx_12_0_x86_64'
PYTHON=Path('/private/tmp/cvw26-runtime-rebuild-88a53cde/install/cpython/bin/python3.12')
PYTHON_SHA='07f43800bc9c7ecf842b14adcc189cfdd76cc83b5198bf2d2fd8a89ca5619f05'
SCOPE={'activity_kind':KIND,'budget_seconds':3600,'cleanup_reserve_seconds':180,'prepare_cap_seconds':600,
       'max_make_parallelism':2,'sampled_rss_stop_bytes':6*1024**3,'max_output_bytes':8*1024**3,'minimum_free_bytes':8*1024**3}

def closed_json(raw):
    def pairs(rows):
        value={}
        for k,v in rows:
            if k in value: raise RuntimeError('duplicate JSON field')
            value[k]=v
        return value
    return json.loads(raw,object_pairs_hook=pairs)

def input_blob(path, row):
    raw,record=_helpers().raw_file(Path(path),maximum=268435456)
    if record['sha256']!=row['sha256'] or record['bytes']!=row['bytes']: raise RuntimeError('pinned source bytes differ')
    return raw,record

def verify_source(inputs, manifest_record):
    inputs=Path(inputs);h=_helpers();h.secure_dir(inputs)
    raw,_=h.raw_file(Path(manifest_record['path']),expected=manifest_record,maximum=16777216);manifest=closed_json(raw)
    files=manifest['files'];canon=json.dumps(files,sort_keys=True,separators=(',',':')).encode()
    if hashlib.sha256(canon).hexdigest()!=manifest['source_closure_sha256']:raise RuntimeError('original source closure digest differs')
    for rel,row in files.items():
        safe_member(rel);p=inputs/rel
        current=p.parent
        while current!=inputs:
            if current.is_symlink() or not current.is_dir():raise RuntimeError('original source ancestor differs')
            current=current.parent
        input_blob(p,row)
    return manifest

def safe_member(name):
    path=PurePosixPath(name)
    if not name or path.is_absolute() or any(x in ('','..','.') for x in name.rstrip('/').split('/')) or '\\' in name:
        raise RuntimeError('unsafe archive/member path')
    return path

def unpack_tar(blob,destination,*,cargo_checksum=None,maximum_member_bytes=134217728):
    """Only data copies; original tar members are never patched/executed."""
    h=_helpers();destination=Path(destination);destination.mkdir(mode=0o700,parents=True)
    with tarfile.open(fileobj=io.BytesIO(blob),mode='r:*') as archive:
        infos=archive.getmembers();names=set();files={}
        if len(infos)>50000 or sum(x.size for x in infos)>1024**3:raise RuntimeError('archive expansion bound')
        for info in infos:
            path=safe_member(info.name)
            if info.name in names:raise RuntimeError('duplicate archive member')
            names.add(info.name);target=destination.joinpath(*path.parts)
            if info.isdir():target.mkdir(mode=0o700,parents=True,exist_ok=True);continue
            if not info.isfile() or info.size>maximum_member_bytes:raise RuntimeError('archive special/oversized member')
            raw=archive.extractfile(info).read()
            if len(raw)!=info.size:raise RuntimeError('short archive member')
            target.parent.mkdir(mode=0o700,parents=True,exist_ok=True);h.copy_blob(target,raw)
            files[info.name]=hashlib.sha256(raw).hexdigest()
            # Restore only original user execute bits in this owned work copy.
            if info.mode&0o100:target.chmod(0o700)
    if cargo_checksum is not None:
        tops={PurePosixPath(x).parts[0] for x in names}
        if len(tops)!=1:raise RuntimeError('crate root differs')
        top=next(iter(tops));members={str(PurePosixPath(k).relative_to(top)):v for k,v in files.items()}
        if '.cargo-checksum.json' in members:raise RuntimeError('unexpected preexisting Cargo checksum')
        h.publish(destination/top/'.cargo-checksum.json',{'package':cargo_checksum,'files':members})
    return files

def verify_vendor(vendor, locked):
    expected={(p['name'],p['version']):p['checksum'] for p in locked['package'] if p.get('source')}
    if len(expected)!=32 or any(p.get('source') not in (None,'registry+https://github.com/rust-lang/crates.io-index') for p in locked['package']):
        raise RuntimeError('exact32 crates.io closure differs')
    dirs={p.name for p in Path(vendor).iterdir()}
    if dirs!={n+'-'+v for n,v in expected}:raise RuntimeError('vendor membership differs')
    for (name,version),checksum in expected.items():
        directory=Path(vendor)/(name+'-'+version);doc=closed_json(_helpers().raw_file(directory/'.cargo-checksum.json')[0])
        if set(doc)!={'package','files'} or doc['package']!=checksum:raise RuntimeError('Cargo package checksum differs')
        actual={str(p.relative_to(directory)) for p in directory.rglob('*') if p.is_file() or p.is_symlink()}
        if actual!=set(doc['files'])|{'.cargo-checksum.json'}:raise RuntimeError('Cargo vendor files differ')
        for rel,digest in doc['files'].items():
            safe_member(rel);p=directory/rel;raw,_=_helpers().raw_file(p,maximum=134217728)
            if hashlib.sha256(raw).hexdigest()!=digest:raise RuntimeError('Cargo vendor file hash differs')
    return expected

def wheel_check(path,*,expected_name=None,expected_version=None,tag=None):
    path=Path(path);raw,record=_helpers().raw_file(path,maximum=268435456)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        infos=z.infolist();names=[x.filename for x in infos]
        if len(names)!=len(set(names)) or sum(x.file_size for x in infos)>536870912:raise RuntimeError('wheel expansion/duplicate bound')
        for x in infos:
            safe_member(x.filename)
            if (x.external_attr>>16)&0o170000==0o120000:raise RuntimeError('wheel symlink')
        members={key:[n for n in names if n.count('/')==1 and n.endswith('.dist-info/'+key)] for key in ('METADATA','WHEEL','RECORD')}
        if any(len(v)!=1 for v in members.values()):raise RuntimeError('wheel exact dist-info membership')
        records=csv.reader(io.StringIO(z.read(members['RECORD'][0]).decode()));seen=set()
        for name,digest,size in records:
            if name in seen or name not in names:raise RuntimeError('RECORD membership differs')
            seen.add(name);payload=z.read(name)
            if not digest:
                if name!=members['RECORD'][0]:raise RuntimeError('unhashed wheel member')
            else:
                alg,encoded=digest.split('=',1)
                if alg!='sha256' or base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b'=').decode()!=encoded or str(len(payload))!=size:raise RuntimeError('RECORD hash/size differs')
        if seen!={x.filename for x in infos if not x.is_dir()}:raise RuntimeError('RECORD incomplete')
    meta=release.wheel_metadata(path)
    if not release.wheel_tag_identity_matches(path,meta):raise RuntimeError('actual WHEEL/filename tags differ')
    if expected_name and meta['normalized_name']!=expected_name:raise RuntimeError('wheel package name differs')
    if expected_version and meta['version']!=expected_version:raise RuntimeError('wheel version differs')
    if tag and (meta['wheel_tags']!=[tag] or not release.wheel_target_check(path.name,'macos_x86_64')):raise RuntimeError('wheel is not exact cp312abi3Mac12 Intel')
    return meta,record

def macho12(raw, *, filetypes=(6,8), require_libraries=True):
    """Read actual thin Intel Python extension load commands, without external tools."""
    if len(raw)<32:raise RuntimeError('short Mach-O')
    magic,cpu,sub,kind,n,size,flags,res=struct.unpack_from('<IiiIIIII',raw)
    if magic!=0xfeedfacf or cpu!=0x1000007 or kind not in filetypes or n>4096 or 32+size>len(raw):raise RuntimeError('not exact thin Intel Mach-O bundle')
    pos=32;versions=[];libs=[];ids=[]
    for _ in range(n):
        if pos+8>32+size:raise RuntimeError('truncated load command')
        cmd,length=struct.unpack_from('<II',raw,pos)
        if length<8 or length%4 or pos+length>32+size:raise RuntimeError('load command bounds')
        if cmd==0x32:
            if length<24:raise RuntimeError('build-version short')
            platform,minimum,sdk,tools=struct.unpack_from('<IIII',raw,pos+8)
            if platform!=1 or length!=24+tools*8:raise RuntimeError('wrong build platform/tools')
            versions.append(minimum)
        elif cmd==0x24:
            if length!=16:raise RuntimeError('minOS command length')
            versions.append(struct.unpack_from('<I',raw,pos+8)[0])
        elif cmd in (0xc,0x80000018,0x8000001f,0x20,0x80000023,0xd):
            if length<24:raise RuntimeError('dylib command short')
            offset=struct.unpack_from('<I',raw,pos+8)[0]
            if not 24<=offset<length:raise RuntimeError('dylib name offset')
            encoded=raw[pos+offset:pos+length]
            if b'\0' not in encoded:raise RuntimeError('dylib name unterminated')
            name,padding=encoded.split(b'\0',1)
            if any(padding):raise RuntimeError('dylib name padding')
            (ids if cmd==0xd else libs).append(name.decode())
        elif cmd==0x8000001c:raise RuntimeError('unexpected runtime search path')
        pos+=length
    expected_id='@rpath/cryptography.hazmat.bindings._rust.abi3.so'
    if (kind==6 and ids!=[expected_id]) or (kind!=6 and ids):raise RuntimeError('actual extension self dylib identity differs')
    allowed={'/usr/lib/libSystem.B.dylib','/usr/lib/libiconv.2.dylib'}
    if pos!=32+size or versions!=[0xc0000] or (require_libraries and not libs) or not set(libs)<=allowed:raise RuntimeError('actual minOS12/system dylib whitelist differs')
    return {'architecture':'x86_64','minimum_os':'12.0','mach_o_filetype':kind,'self_install_name':ids[0] if ids else None,'dynamic_libraries':libs,'openssl_dynamic_dependency':False,'temporary_dylib_dependencies':[],
            'extension_sha256':hashlib.sha256(raw).hexdigest()}

def static_archive12(raw):
    if len(raw)>134217728 or not raw.startswith(b'!<arch>\n'):raise RuntimeError('bounded static archive expected')
    pos=8;members=[];seen=set()
    while pos<len(raw):
        if pos+60>len(raw):raise RuntimeError('ar header truncated')
        header=raw[pos:pos+60];pos+=60
        if header[58:60]!=b'`\n':raise RuntimeError('ar header trailer differs')
        try:size=int(header[48:58].decode().strip())
        except ValueError as exc:raise RuntimeError('ar size malformed') from exc
        if size<0 or pos+size>len(raw):raise RuntimeError('ar member bounds')
        data=raw[pos:pos+size];name=header[:16].decode().strip()
        if name.startswith('#1/'):
            length=int(name[3:])
            if not 1<=length<=len(data):raise RuntimeError('ar extended name bounds')
            name=data[:length].rstrip(b'\0').decode();data=data[length:]
        else:name=name.rstrip('/')
        if not name or '/' in name or name in seen:raise RuntimeError('ar member name differs')
        seen.add(name)
        if name not in ('__.SYMDEF','__.SYMDEF SORTED','__.SYMDEF_64','__.SYMDEF_64 SORTED'):
            members.append({'name':name,**macho12(data,filetypes=(1,),require_libraries=False)})
        pos+=size
        if pos%2:
            if raw[pos:pos+1]!=b'\n':raise RuntimeError('ar padding differs')
            pos+=1
    if pos!=len(raw) or not members:raise RuntimeError('ar empty/misaligned')
    return {'architecture':'x86_64','minimum_os':'12.0','sha256':hashlib.sha256(raw).hexdigest(),'objects':members}


def scope(plan):
    if any(type(plan.get(k,v)) is not type(v) or plan.get(k,v)!=v for k,v in SCOPE.items()):raise RuntimeError('closed Intel scope/caps differ')
    return dict(SCOPE)

def environment(root):
    root=Path(root);work=root/'work';host=PYTHON.parents[1];sdk=str(actor.SDK_LINK.resolve());clang=actor.CLANG
    # cc-rs consumes CFLAGS/target CFLAGS, not CPPFLAGS. Use the accepted product headers.
    flags='-arch x86_64 -mmacosx-version-min=12.0 -isysroot '+sdk+' -I'+str(host/'include/python3.12')
    return {'PATH':str(work/'rust/bin')+':/usr/bin:/bin','HOME':str(work/'empty-home'),'TMPDIR':str(work/'tmp'),'LC_ALL':'C','LANG':'C',
        'PYTHONPATH':str(work/'build-site'),'PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1','SETUPTOOLS_USE_DISTUTILS':'local',
        'MACOSX_DEPLOYMENT_TARGET':'12.0','ARCHFLAGS':'-arch x86_64','SDKROOT':sdk,'CC':clang,'CXX':clang+'++','CFLAGS':flags,'CXXFLAGS':flags,
        'CPPFLAGS':'-I'+str(host/'include/python3.12'),'LDFLAGS':flags,'CARGO_HOME':str(work/'cargo-home'),'CARGO_TARGET_DIR':str(work/'cargo-target'),
        'CARGO_NET_OFFLINE':'true','CARGO_BUILD_JOBS':'2','CARGO_BUILD_TARGET':'x86_64-apple-darwin','CARGO_TARGET_X86_64_APPLE_DARWIN_LINKER':clang,
        'CC_x86_64_apple_darwin':clang,'CFLAGS_x86_64_apple_darwin':flags,'OPENSSL_DIR':str(work/'openssl-x86_64'),'OPENSSL_STATIC':'1','OPENSSL_NO_VENDOR':'1',
        'PYO3_PYTHON':str(PYTHON),'PYTHON_SYS_EXECUTABLE':str(PYTHON),'PYO3_CROSS':'1','PYO3_CROSS_LIB_DIR':str(host/'lib'),
        'PYO3_CROSS_PYTHON_VERSION':'3.12','PYO3_CROSS_PYTHON_IMPLEMENTATION':'CPython','_PYTHON_SYSCONFIGDATA_NAME':'_sysconfigdata__darwin_darwin',
        'CLANG_MODULE_CACHE_PATH':str(work/'tmp/module-cache'),'CONFIG_SITE':'/dev/null'}

def stages(root):
    root=Path(root);work=root/'work';env=environment(root);commands=[]
    components=[('rustc','rustc'),('cargo','cargo'),('rust-std','rust-std-aarch64-apple-darwin'),('rust-std','rust-std-x86_64-apple-darwin')]
    for base,component in components:
        target='x86_64-apple-darwin' if component.endswith('x86_64-apple-darwin') else 'aarch64-apple-darwin'
        directory=work/'toolchain-inputs'/(base+'-1.83.0-'+target);prefix=work/'rust'
        commands.append({'label':component+'-'+base,'argv':['/bin/sh',str(directory/'install.sh'),'--prefix='+str(prefix),'--disable-ldconfig','--components='+component,
            '--sysconfdir='+str(prefix/'etc'),'--bindir='+str(prefix/'bin'),'--libdir='+str(prefix/'lib'),'--datadir='+str(prefix/'share'),'--mandir='+str(prefix/'share/man'),'--docdir='+str(prefix/'share/doc/rust')],
            'cwd':str(directory),'environment':env,'cap':180})
    ssl=work/'sources/openssl-4.0.2';prefix=work/'openssl-x86_64';crypto=work/'sources/cryptography-50.0.1'
    for label,argv,cwd,cap in [('openssl-configure',['/usr/bin/perl',str(ssl/'Configure'),'darwin64-x86_64-cc','no-shared','no-tests','--prefix='+str(prefix),'--openssldir='+str(prefix/'ssl'),'-mmacosx-version-min=12.0'],ssl,180),
        ('openssl-build',['/usr/bin/make','-j2','build_libs'],ssl,1200),('openssl-install',['/usr/bin/make','install_sw'],ssl,180),
        ('crypto-wheel',[str(work/'build-site/bin/maturin'),'build','--release','--offline','--locked','--features','pyo3/abi3-py312','--target','x86_64-apple-darwin','--interpreter',str(PYTHON),'--out',str(work/'wheelhouse')],crypto,1200)]:
        commands.append({'label':label,'argv':argv,'cwd':str(cwd),'environment':env,'cap':cap})
    return commands

def host_records(inputs):
    h=_helpers();draft=closed_json(h.raw_file(Path(inputs)/'BUILD_DRAFT.json')[0]);declared=draft['product_python']
    if declared['path']!=str(PYTHON) or declared['sha256']!=PYTHON_SHA:raise RuntimeError('exact accepted product Python differs')
    actual={}
    for name,row in [('python',declared),('pyconfig',declared['header']),('Python_h',declared['Python_h']),('sysconfig',declared['sysconfig'])]:
        path=Path(row['path'])
        if name!='python' and not path.is_relative_to(PYTHON.parents[1]):raise RuntimeError('host header/sysconfig escaped exact product prefix')
        raw,rec=h.raw_file(path,maximum=268435456)
        if rec['sha256']!=row['sha256']:raise RuntimeError('accepted host/header/sysconfig hash differs')
        actual[name]=rec
    for name,row in declared['Main_product_receipts'].items():
        raw,rec=h.raw_file(Path(row['path']))
        if rec['sha256']!=row['sha256']:raise RuntimeError('accepted actual provider/profile receipt differs')
        actual[name]=rec
    prefix=PYTHON.parents[1];headers={}
    for p in (prefix/'include/python3.12').rglob('*'):
        if p.is_file() or p.is_symlink():headers[str(p)]=h.raw_file(p)[1]
    actual['headers']=headers
    return actual

def check_host(records):
    h=_helpers()
    for key,row in records.items():
        if key=='headers':
            for p,record in row.items():h.raw_file(Path(p),expected=record)
        else:h.raw_file(Path(row['path']),expected=row,maximum=268435456)

def prepare(root,inputs,inputs_sha):
    root=Path(root);inputs=Path(inputs);h=_helpers()
    if root.parent!=Path('/private/tmp') or root!=root.resolve() or not root.name.startswith('cvw26-intel-derived-') or root.exists() or root.is_symlink():raise RuntimeError('fresh Intel root required')
    manifest_raw,manifest_record=h.raw_file(inputs/'source.json',maximum=16777216)
    if manifest_record['sha256']!=inputs_sha:raise RuntimeError('exact original input manifest differs')
    verify_source(inputs,manifest_record);host=host_records(inputs);root.mkdir(mode=0o700);identity=h.secure_dir(root)
    metadata={name:h.raw_file(inputs/name,maximum=16777216)[1] for name in ('ARCHIVES_RECEIPT.json','DEPENDENCIES_RECEIPT.json','BUILD_DRAFT.json','ALL32_CRATE_SOURCE_AUDIT.json')}
    prepared=h.publish(root/'INTEL_INPUT_BINDING.json',{'input_root':str(inputs),'manifest':manifest_record,'host':host,'root_identity':identity,'metadata':metadata})
    # Source closure covers every normal owner/bootstrap helper plus originals used by the child.
    source=actor.source_fingerprints()
    for rel in ['tools/w26_intel_derived.py','tools/w26_intel_derived_stage.py','tools/w26_dependency_stage.py','tools/w26_runtime_actor.py','tools/_w26_original_process.py','tools/w26_hold_exec.py','tools/full_project_release.py','tools/w26_prepare_current_inputs.py','uv.lock']:
        path=Path(__file__).parents[1]/rel;source[str(path)]=h.raw_file(path)[1]
    source[str(COMMON)]=h.raw_file(COMMON)[1];source_record=h.publish(root/'INTEL_ACTOR_SOURCE.json',source)
    toolchain={name:actor.system_record(Path(path).resolve()) for name,path in [('sandbox','/usr/bin/sandbox-exec'),('ps','/bin/ps'),('sh','/bin/sh'),('perl','/usr/bin/perl'),('make','/usr/bin/make'),('clang',actor.CLANG),('clangxx',actor.CLANG+'++')]}
    toolchain['sdk']=actor.sdk_snapshot()
    tools=h.publish(root/'INTEL_SYSTEM.json',toolchain)
    plan={**SCOPE,'commands':stages(root),'administrative_python':{'path':str(Path(sys.executable).resolve()),'record':h.raw_file(Path(sys.executable).resolve(),maximum=268435456)[1],'product_runtime':False},'native_process_umask':'0077','sampling_is_not_OS_quota':True}
    plan_record=h.publish(root/'INTEL_BUILD_PLAN.json',plan)
    return h.publish(root/'ACTOR_CANDIDATE.json',{'status':'READY_INTEL_FOR_MAIN_REVIEW_NOT_EXECUTION_APPROVED',**SCOPE,'root_identity':identity,'prepared':prepared,'source':source_record,'toolchain':tools,'plan':plan_record,'native_Popen':0})

def verify_tools(candidate, *, full=False):
    h=_helpers();doc=closed_json(h.raw_file(Path(candidate['toolchain']['path']),expected=candidate['toolchain'])[0])
    actor.verify_toolchain(doc['sdk'],full=full)
    for name,record in doc.items():
        if name!='sdk' and actor.system_record(record['path'])!=record:raise RuntimeError('Intel system tool changed')


def materialize(root,prepared,owner):
    root=Path(root);work=root/'work';inputs=Path(prepared['input_root']);h=_helpers();owner.remaining(600)
    if shutil.disk_usage(root).free<8*1024**3:raise RuntimeError('less than8GiB remaining disk')
    manifest=verify_source(inputs,prepared['manifest']);check_host(prepared['host'])
    for name,record in prepared['metadata'].items():h.raw_file(inputs/name,expected=record,maximum=16777216)
    for name in ('work','logs','evidence'):(root/name).mkdir(mode=0o700)
    for name in ('sources','toolchain-inputs','vendor','cargo-home','build-site','wheelhouse','tmp','empty-home'):(work/name).mkdir(mode=0o700)
    source_receipt=closed_json(h.raw_file(inputs/'ARCHIVES_RECEIPT.json')[0]);dep_receipt=closed_json(h.raw_file(inputs/'DEPENDENCIES_RECEIPT.json')[0])
    for row in source_receipt['results']:
        owner.remaining(600);blob,_=input_blob(inputs/'archives'/row['filename'],manifest['files']['archives/'+row['filename']]);unpack_tar(blob,work/'source-unpack'/row['name'])
        top='cryptography-50.0.1' if row['name']=='cryptography' else 'openssl-4.0.2';os.rename(work/'source-unpack'/row['name']/top,work/'sources'/top)
    locked=tomllib.loads((work/'sources/cryptography-50.0.1/Cargo.lock').read_text());expected={(p['name'],p['version']):p['checksum'] for p in locked['package'] if p.get('source')}
    for row in dep_receipt['rows']:
        owner.remaining(600);blob,_=input_blob(inputs/'dependencies'/row['filename'],manifest['files']['dependencies/'+row['filename']])
        if row['kind']=='CURRENT_CARGO_LOCK_CRATE':
            if expected.get((row['name'],row['version']))!=row['sha256']:raise RuntimeError('Cargo checksum/identity differs')
            temp=work/'crate-unpack'/row['filename'];unpack_tar(blob,temp,cargo_checksum=row['sha256']);top=row['name']+'-'+row['version'];os.rename(temp/top,work/'vendor'/top)
        elif row['kind']=='RUST_TOOLCHAIN_ARCHIVE_INPUT_NOT_INSTALLED':
            temp=work/'rust-unpack'/row['filename'];unpack_tar(blob,temp,maximum_member_bytes=268435456);top=row['filename'].removesuffix('.tar.xz');os.rename(temp/top,work/'toolchain-inputs'/top)
        elif row['kind']!='BUILD_HOST_PYTHON_WHEEL_NOT_INSTALLED':raise RuntimeError('unexpected dependency kind')
    verify_vendor(work/'vendor',locked)
    h.copy_blob(work/'cargo-home/config.toml',('[source.crates-io]\nreplace-with="vendored-sources"\n[source.vendored-sources]\ndirectory='+json.dumps(str(work/'vendor'))+'\n[net]\noffline=true\n').encode())
    draft=closed_json(h.raw_file(inputs/'BUILD_DRAFT.json')[0])
    for row in draft['buildhost_inputs']:
        owner.remaining(600);p=Path(row['path']);meta,_=wheel_check(p,expected_version=row['version']);input_blob(p,manifest['files'][str(p.relative_to(inputs))])
        with zipfile.ZipFile(p) as z:
            for info in z.infolist():
                if info.is_dir():continue
                path=safe_member(info.filename)
                if any(part.endswith('.data') for part in path.parts):
                    if row['name']!='maturin' or str(path)!='maturin-1.14.1.data/scripts/maturin':raise RuntimeError('unexpected hostwheel data installation mapping')
                    dest=work/'build-site/bin/maturin'
                else:dest=work/'build-site'/str(path)
                dest.parent.mkdir(mode=0o700,parents=True,exist_ok=True);h.copy_blob(dest,z.read(info))
                if dest==work/'build-site/bin/maturin':dest.chmod(0o700)
    check_host(prepared['host']);verify_source(inputs,prepared['manifest']);owner.sample()
    return locked

def verify_work_sources(root,prepared):
    inputs=Path(prepared['input_root']);h=_helpers()
    rows=closed_json(h.raw_file(inputs/'ARCHIVES_RECEIPT.json',expected=prepared['metadata']['ARCHIVES_RECEIPT.json'],maximum=16777216)[0])['results']
    for row in rows:
        top='cryptography-50.0.1' if row['name']=='cryptography' else 'openssl-4.0.2'
        for member in row['source_members']:
            rel=PurePosixPath(member['member']).relative_to(top)
            path=Path(root)/'work/sources'/top/str(rel)
            input_blob(path,member)
    for name,record in prepared['metadata'].items():h.raw_file(inputs/name,expected=record,maximum=16777216)
    return True


def verify_product(root,prepared,completed,*,verification_root=None):
    root=Path(root);h=_helpers();verify_work_sources(root,prepared);work=root/'work';wheelhouse=work/'wheelhouse';wheels=list(wheelhouse.iterdir())
    expected='cryptography-50.0.1-'+TAG+'.whl'
    if len(wheels)!=1 or wheels[0].name!=expected:raise RuntimeError('exact single output wheel differs; no retag')
    wheel=wheels[0];meta,record=wheel_check(wheel,expected_name='cryptography',expected_version='50.0.1',tag=TAG)
    with zipfile.ZipFile(wheel) as archive:
        extensions=[n for n in archive.namelist() if n.endswith('.so')]
        if extensions!=['cryptography/hazmat/bindings/_rust.abi3.so']:raise RuntimeError('single actual Rustextension differs')
        native=macho12(archive.read(extensions[0]))
    license_path=work/'sources/openssl-4.0.2/LICENSE.txt';license_record=h.raw_file(license_path)[1]
    source_root=Path(prepared['input_root']);uv_raw,uv_record=h.raw_file(Path(__file__).parents[1]/'uv.lock');uv=tomllib.loads(uv_raw.decode());locked=next(p for p in uv['package'] if p['name']=='cryptography' and p['version']=='50.0.1')['sdist']
    # The input currentUV must agree; immutable old0.1.9 provenance is not rebound.
    original_uv=source_root/'sources/uv.lock'
    if h.raw_file(original_uv)[0]!=uv_raw:raise RuntimeError('current sourceUV changed since input capture')
    input_archives=closed_json(h.raw_file(source_root/'ARCHIVES_RECEIPT.json')[0])['results'];ssl=next(x for x in input_archives if x['name']=='OpenSSL');crypto=next(x for x in input_archives if x['name']=='cryptography')
    if ssl['sha256']!=release.OPENSSL_402_SOURCE_SHA256 or crypto['sha256']!=locked['hash'].removeprefix('sha256:'):raise RuntimeError('actual archive constants/source lock differ')
    static_archives=[]
    for archive in (work/'openssl-x86_64/lib/libssl.a',work/'openssl-x86_64/lib/libcrypto.a'):
        raw,_=h.raw_file(archive,maximum=134217728)
        static_archives.append({'record':h.raw_file(archive,maximum=134217728)[1],'actual_static_MachO':static_archive12(raw)})
    provenance={'schema':'comsol-mcp-derived-wheel/1','status':'BUILD_CANDIDATE','target':'macos_x86_64','package':{'name':'cryptography','version':'50.0.1'},
        'wheel':{'filename':wheel.name,'bytes':record['bytes'],'sha256':record['sha256'],'tag':TAG},
        'source':{'url':locked['url'],'sha256':locked['hash'].removeprefix('sha256:')},'source_lock':{'filename':'uv.lock','sha256':uv_record['sha256'],'sdist_sha256':locked['hash'].removeprefix('sha256:')},
        'native_component':{'name':'OpenSSL','version':'4.0.2','license':'Apache-2.0','linkage':'static','source_url':release.OPENSSL_402_SOURCE_URL,'source_sha256':release.OPENSSL_402_SOURCE_SHA256,
            'license_file':{'filename':license_path.name,'bytes':license_record['bytes'],'sha256':license_record['sha256']}},'native_audit':native,'uv_lock_modified':False,'upstream_source_patched':False,
        'fresh_archive_receipts':input_archives,'static_archive_audits':static_archives,'command_receipts':completed,'inherited_old_build_qualification':False}
    destination=root if verification_root is None else Path(verification_root)
    if verification_root is not None:
        if destination.parent!=Path('/private/tmp') or destination!=destination.resolve() or not destination.name.startswith('cvw26-intel-verification-'):
            raise RuntimeError('fresh task-owned Intel verification namespace required')
        h.secure_dir(destination)
    path=destination/'INTEL_CRYPTO_PROVENANCE.json';h.publish(path,provenance)
    checked=release.verify_derived_wheel(path,wheel,Path(__file__).parents[1]/'uv.lock',license_path,'macos_x86_64')
    verify_source(source_root,prepared['manifest']);check_host(prepared['host'])
    return checked

def execute_intel(root,candidate,binding,binding_record,*,began,admission_token=None):
    if admission_token is not actor._OWNER_TOKEN:raise RuntimeError('Intel owner requires original internal source gate token')
    root=Path(root);h=_helpers();plan=closed_json(h.raw_file(Path(candidate['plan']['path']),expected=candidate['plan'])[0]);scope(plan)
    # Until the shared actor integrates the explicit newkind this remains beforebirth denied.
    observed=actor.activity_scope(plan)
    if any(observed.get(k)!=v for k,v in SCOPE.items()):raise RuntimeError('shared actor has not admitted exact Intel scope')
    prepared=closed_json(h.raw_file(Path(candidate['prepared']['path']),expected=candidate['prepared'])[0]);owner=actor.RuntimeOwner(root,plan,token=admission_token,began=began);actor._RETAINED[str(root)]=owner
    if plan['commands']!=stages(root):raise RuntimeError('exact Intel eight argv/environment changed')
    actor.verify_actor(candidate);verify_tools(candidate,full=True);check_host(prepared['host']);logsfd=None;old=actor.original.subprocess;completed=[]
    try:
        locked=materialize(root,prepared,owner)
        if time.monotonic()-began>600:raise TimeoutError('inclusive600s prepare cap exhausted')
        h.publish(root/'UNIT_ONCE_GUARD.json',{'binding':binding_record,'nonce':binding['nonce'],'epoch':began,**SCOPE})
        sandbox='(version 1)\n(allow default)\n(deny network*)\n(deny file-write*)\n'+''.join('(allow file-write* (subpath '+json.dumps(str(root/name))+'))\n' for name in ('work','logs','evidence'))+'(allow file-write* (literal "/dev/null"))\n'
        h.copy_blob(root/'INTEL_SANDBOX.sb',sandbox.encode());logsfd=os.open(root/'logs',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        actor.original.subprocess=SimpleNamespace(Popen=owner.popen,run=owner.diagnostic_run,TimeoutExpired=actor.subprocess.TimeoutExpired)
        context=h.publish(root/'INTEL_STAGE_CONTEXT.json',{'schema':'w26-intel-derived-stage/1','unit_root':str(root),'root_identity':candidate['root_identity'],
            'legacy_sources':{'common':h.raw_file(COMMON)[1]},'commands':plan['commands']})
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(.25);port=listener.getsockname()[1]
            with socket.create_connection(('127.0.0.1',port),timeout=1):positive,_=listener.accept();positive.close()
            h.publish(root/'evidence/OWNED_LISTENER_POSITIVE.json',{'port':port,'numeric_host':'127.0.0.1','positive_connected':True})
            for index,stage in enumerate(plan['commands']):
                actor.verify_actor(candidate);verify_tools(candidate);check_host(prepared['host']);verify_work_sources(root,prepared);verify_vendor(root/'work/vendor',locked);owner.remaining(stage['cap'])
                if shutil.disk_usage(root).free<SCOPE['minimum_free_bytes']:raise RuntimeError('remaining disk below8GiB before nextstage')
                argv=[plan['administrative_python']['path'],'-S','-B',str(Path(__file__).with_name('w26_intel_derived_stage.py')),'--context',context['path'],'--sha',context['sha256'],'--index',str(index),'--port',str(port)]
                result=actor.run_owned_stage(owner,logsfd,index,stage['label'],argv,root,{'PATH':'/usr/bin:/bin','HOME':str(root/'work/empty-home'),'PYTHONPATH':str(Path(__file__).parents[1]),'PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1','TMPDIR':str(root/'work/tmp')},stage['cap'],binding,plan,sandbox_path=root/'INTEL_SANDBOX.sb')
                if result['rc']!=0:raise RuntimeError('original Intel stage returned failure')
                completed.append(result);h.publish(root/'logs'/('PASS_'+str(index)+'.json'),result)
                try:accepted,_=listener.accept();accepted.close();raise RuntimeError('sandboxed stage reached parent held listener')
                except socket.timeout:pass
        if len(completed)!=8:raise RuntimeError('eight stages incomplete')
        verify_vendor(root/'work/vendor',locked);verified=verify_product(root,prepared,completed);owner.sample();actor.verify_actor(candidate);verify_tools(candidate,full=True)
        return h.publish(root/'INTEL_RESULT.json',{'status':'PASS_ACTUAL_FRESH_INTEL_DERIVED_WHEEL_BYTES_NOT_PHYSICAL_INTEL','verified':verified,'commands':completed,'samples':owner.samples,'sampling_is_not_OS_quota':True,'physicalIntel_COMSOL_release':'NOT_RUN'})
    except BaseException as exc:
        if logsfd is None:
            # No child can yet exist. Preserve firstcause using the same originalactor logic.
            (root/'logs').mkdir(mode=0o700,exist_ok=True);logsfd=os.open(root/'logs',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        actor.retain_first_failure(owner,logsfd,exc,completed,stem='INTEL');raise
    finally:
        actor.original.subprocess=old
        if logsfd is not None:os.close(logsfd)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--inputs',type=Path,required=True);p.add_argument('--inputs-sha',required=True);a=p.parse_args()
    print(prepare(a.root,a.inputs,a.inputs_sha))
