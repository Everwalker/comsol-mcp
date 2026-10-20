"""Closed current target overlays from preserved exact artifacts; never imports wheels."""
import base64,csv,hashlib,io,json,os,stat,struct,zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path,PurePosixPath
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name,parse_wheel_filename
from packaging.specifiers import SpecifierSet
from tools import full_project_release as release
from tools.w26_prepare_current_inputs import _helpers,load_closure,validate_wheel

ARCHIVES={
 'win_amd64':('/Volumes/SSD/Comsol-MCP/execution-scratch/w26-real-build-current-20261004-001/windows-dev1-bundle-20261005-001/bundle/comsol-mcp-win_amd64-0.2.0.dev1.zip','72cedd9f70e273a1e7509ce6f4537850741e9f94e8d51bed2275d633a1c2fca3'),
 'macos_x86_64':('/Volumes/SSD/Comsol-MCP/execution-scratch/w26-real-build-current-20261004-001/macos-x86_64-dev1-bundle-20261005-001/bundle/comsol-mcp-macos_x86_64-0.2.0.dev1.zip','66c5ad0fd9089c721071d199835506a0e9544ccb2ed7e729239a24baa04e1521')}
CRYPTO=('/private/tmp/cvw26-intel-derived-current-r3-9568ce62a041/work/wheelhouse/cryptography-50.0.1-cp312-abi3-macosx_12_0_x86_64.whl','a2a2e473f8ccaf1fb9a2e4ee51723f6989dedb16bf3da7a98b1099003d6c0321')
PSUTIL_MANIFEST=('/private/tmp/cvw26-target-inputs-8d44186e/INTEL_PSUTIL_INPUT.json','99db38e817779a9a204be2adcb351e54c62ede8864cb85b8868e4b257c11fde7')
PROVENANCE=('/private/tmp/cvw26-intel-verification-r4-418f72901dca/INTEL_CRYPTO_PROVENANCE.json','b4b8a5561570b81d03812e653cf289ea8f1534665b7c9545e98aca196e31da71')
COUNTS={'macos_arm64':41,'macos_x86_64':41,'win_amd64':34}

# A single additive historical-input profile.  The old CRYPTO/PROVENANCE
# constants remain the default and are deliberately not rewritten to these
# different archive bytes.
HISTORICAL_INTEL_PROFILE='historical-prebuilt-intel-cryptography-50.0.1-r1'
HISTORICAL_INTEL_SCHEMA='W26_CURRENT_THREE_TARGET_INPUTS_INTEL_PREBUILT_V1'
HISTORICAL_INTEL_AUDIT_SHA256='b4391cd170b4f4d513da11f34d75a58a12966565ed903b7704f7b7bcf882f3cc'
HISTORICAL_INTEL_EXPECTED={
 'archive':{'bytes':27409151,'sha256':'66c5ad0fd9089c721071d199835506a0e9544ccb2ed7e729239a24baa04e1521'},
 'wheel':{'member':'wheelhouse/cryptography-50.0.1-cp312-abi3-macosx_12_0_x86_64.whl','bytes':3982449,'sha256':'aaadaf6690ed20966ab3fa3fdac6a5797a46d9bfdbdce4889dc5f6facf280696'},
 'provenance':{'member':'evidence/cryptography-x86-derived-provenance.json','bytes':9003,'sha256':'b56d17cd67c2a6fb95e68f385a0dee57ce615fca06d6f336f9a28207c251666c'},
 'license':{'member':'licenses/openssl-4.0.2-LICENSE.txt','bytes':10175,'sha256':'7d5450cb2d142651b8afa315b5f238efc805dad827d91ba367d8516bc9d49e7a'},
 'sdist_sha256':'5dd9bda1c12b4162f6ff568eeb5e0ff956c28d14406e875cfe8a63a2d414ff20',
 'openssl_source_sha256':'736b467530f916737b7031310ccb21d8218c6229e61e8e160cd1d3458cd543a8',
 'extension_sha256':'5dce9ec31c9f2ad2c80667edfe35a7dc0fd6eedec941aaecf82971e0df67a77e',
}

def _historical_intel_archive_record(record):
    """Read a caller-selected fresh archive copy but pin its actual bytes."""
    expected_keys={'path','dev','inode','uid','mode','nlink','bytes','sha256'}
    if type(record) is not dict or set(record)!=expected_keys or not isinstance(record.get('path'),str):
        raise RuntimeError('historical Intel archive first record is not the exact regular-file shape')
    raw,observed=_helpers().raw_file(Path(record['path']),expected=record,maximum=67108864)
    if (observed['bytes']!=HISTORICAL_INTEL_EXPECTED['archive']['bytes']
            or observed['sha256']!=HISTORICAL_INTEL_EXPECTED['archive']['sha256']):
        raise RuntimeError('historical Intel archive differs from the finite profile bytes')
    return raw,observed

def _intel_crypto_macho(raw):
    """Read-only bounded parser for the one reviewed x86_64 extension image."""
    if not isinstance(raw,bytes) or len(raw)<32 or len(raw)>33554432:
        raise RuntimeError('historical Intel extension size is outside its bound')
    if hashlib.sha256(raw).hexdigest()!=HISTORICAL_INTEL_EXPECTED['extension_sha256']:
        raise RuntimeError('historical Intel extension bytes differ from the static-audit input')
    magic,cpu,subtype,filetype,ncmds,sizeofcmds,flags,reserved=struct.unpack_from('<IiiIIIII',raw)
    if (magic,cpu,filetype)!=(0xfeedfacf,0x01000007,6) or ncmds>4096 or 32+sizeofcmds>len(raw):
        raise RuntimeError('historical Intel extension is not the audited thin x86_64 MH_DYLIB')
    pos=32;versions=[];libraries=[];rpaths=[]
    dylib_load_commands={0x0c,0x80000018,0x8000001f,0x80000023,0x20}
    for _ in range(ncmds):
        if pos+8>32+sizeofcmds:raise RuntimeError('historical Intel Mach-O command is truncated')
        command,size=struct.unpack_from('<II',raw,pos)
        if size<8 or size%4 or pos+size>32+sizeofcmds:raise RuntimeError('historical Intel Mach-O command bounds differ')
        if command==0x32:
            if size<24:raise RuntimeError('historical Intel Mach-O build-version command is short')
            _,_,platform,minimum,sdk,tools_count=struct.unpack_from('<IIIIII',raw,pos)
            if platform!=1 or size!=24+tools_count*8:raise RuntimeError('historical Intel Mach-O platform command differs')
            versions.append(minimum)
        elif command==0x24:
            if size!=16:raise RuntimeError('historical Intel Mach-O minimum-version command differs')
            versions.append(struct.unpack_from('<IIII',raw,pos)[2])
        elif command==0x8000001c:
            rpaths.append('present')
        elif command in dylib_load_commands:
            if size<24:raise RuntimeError('historical Intel Mach-O dylib command is short')
            name_offset=struct.unpack_from('<I',raw,pos+8)[0]
            if not 24<=name_offset<size:raise RuntimeError('historical Intel Mach-O dylib offset differs')
            encoded=raw[pos+name_offset:pos+size]
            if b'\0' not in encoded:raise RuntimeError('historical Intel Mach-O dylib name is unterminated')
            name,padding=encoded.split(b'\0',1)
            if any(padding):raise RuntimeError('historical Intel Mach-O dylib padding differs')
            libraries.append(name.decode('utf-8'))
        pos+=size
    if pos!=32+sizeofcmds or versions!=[0x000c0000] or rpaths:
        raise RuntimeError('historical Intel Mach-O minimum OS or RPATH differs')
    expected_libraries=['/usr/lib/libiconv.2.dylib','/usr/lib/libSystem.B.dylib']
    if libraries!=expected_libraries:raise RuntimeError('historical Intel Mach-O dynamic-library closure differs')
    return {'architecture':'x86_64','filetype':'MH_DYLIB','minimum_os':[12,0,0],
            'dynamic_libraries':libraries,'RPATH':rpaths,'extension_sha256':hashlib.sha256(raw).hexdigest(),
            'executed':False}

def _historical_intel_material(archive_raw,archive_record,project_path):
    """Validate the exact archived bytes and current locked cryptography sdist."""
    if (len(archive_raw)!=HISTORICAL_INTEL_EXPECTED['archive']['bytes']
            or hashlib.sha256(archive_raw).hexdigest()!=HISTORICAL_INTEL_EXPECTED['archive']['sha256']):
        raise RuntimeError('historical Intel profile archive bytes differ')
    import tomllib
    lock_path=Path(project_path).with_name('uv.lock')
    lock_raw,lock_record=_helpers().raw_file(lock_path,maximum=4194304)
    locked=[row for row in tomllib.loads(lock_raw.decode())['package'] if row.get('name')=='cryptography']
    if len(locked)!=1 or locked[0].get('version')!='50.0.1':
        raise RuntimeError('historical Intel profile current uv.lock cryptography identity differs')
    sdist=locked[0].get('sdist',{});locked_sdist=sdist.get('hash','').removeprefix('sha256:')
    if locked_sdist!=HISTORICAL_INTEL_EXPECTED['sdist_sha256']:
        raise RuntimeError('historical Intel profile current uv.lock sdist differs')
    with zipfile.ZipFile(io.BytesIO(archive_raw)) as archive:
        if archive.testzip() is not None:raise RuntimeError('historical Intel archive CRC failed')
        infos=archive.infolist();names=[x.filename for x in infos]
        if len(names)!=len(set(names)) or sum(x.file_size for x in infos)>536870912:
            raise RuntimeError('historical Intel archive member closure differs')
        for item in infos:
            path=PurePosixPath(item.filename)
            if (path.is_absolute() or '\\' in item.filename
                    or any(part in {'','.','..'} for part in item.filename.rstrip('/').split('/'))
                    or stat.S_ISLNK(item.external_attr>>16)):
                raise RuntimeError('historical Intel archive contains an unsafe member')
        member_bytes={}
        for label in ('wheel','provenance','license'):
            expected=HISTORICAL_INTEL_EXPECTED[label];member=expected['member']
            if names.count(member)!=1:raise RuntimeError('historical Intel archive required member is not unique')
            raw=archive.read(member)
            if len(raw)!=expected['bytes'] or hashlib.sha256(raw).hexdigest()!=expected['sha256']:
                raise RuntimeError('historical Intel archive member digest differs: '+label)
            member_bytes[label]=raw
        ext_name='cryptography/hazmat/bindings/_rust.abi3.so'
        wheel_raw=member_bytes['wheel']
        with zipfile.ZipFile(io.BytesIO(wheel_raw)) as wheel_archive:
            if wheel_archive.namelist().count(ext_name)!=1:raise RuntimeError('historical Intel extension member is not unique')
            native=_intel_crypto_macho(wheel_archive.read(ext_name))
        metadata=wheel_record(wheel_raw,HISTORICAL_INTEL_EXPECTED['wheel']['member'].split('/')[-1],'macos_x86_64')
        if (metadata['name']!='cryptography' or metadata['version']!='50.0.1'
                or metadata['actual_record']['sha256']!=HISTORICAL_INTEL_EXPECTED['wheel']['sha256']
                or metadata['record_members']!=119):
            raise RuntimeError('historical Intel wheel identity or complete RECORD differs')
    provenance=json.loads(member_bytes['provenance'])
    native_component=provenance.get('native_component',{});src=provenance.get('source',{})
    if (provenance.get('schema')!='comsol-mcp-derived-wheel/1'
            or provenance.get('status')!='BUILD_CANDIDATE'
            or provenance.get('target')!='macos_x86_64'
            or provenance.get('upstream_source_patched') is not False
            or provenance.get('wheel_filename_relabelled') is not False
            or provenance.get('package',{}).get('name')!='cryptography'
            or provenance.get('package',{}).get('version')!='50.0.1'
            or src.get('sha256')!=HISTORICAL_INTEL_EXPECTED['sdist_sha256']
            or provenance.get('source_lock',{}).get('sdist_sha256')!=HISTORICAL_INTEL_EXPECTED['sdist_sha256']
            or native_component.get('name')!='OpenSSL'
            or native_component.get('version')!='4.0.2'
            or native_component.get('source_sha256')!=HISTORICAL_INTEL_EXPECTED['openssl_source_sha256']
            or native_component.get('linkage')!='static'
            or native_component.get('license')!='Apache-2.0'
            or native_component.get('license_file',{}).get('sha256')!=HISTORICAL_INTEL_EXPECTED['license']['sha256']
            or provenance.get('build',{}).get('cargo_locked') is not True
            or provenance.get('build',{}).get('environment',{}).get('CARGO_NET_OFFLINE')!='true'):
        raise RuntimeError('historical Intel provenance source/build/license claims differ')
    summary={'schema':'W26_INTEL_HISTORICAL_PREBUILT_TARGET_INPUT_V1',
             'profile':HISTORICAL_INTEL_PROFILE,'status':'PASS_STATIC_INPUT_VALIDATION_ONLY',
             'archive_record':archive_record,'archive_sha256':HISTORICAL_INTEL_EXPECTED['archive']['sha256'],
             'members':{key:{'member':HISTORICAL_INTEL_EXPECTED[key]['member'],
                             'bytes':HISTORICAL_INTEL_EXPECTED[key]['bytes'],
                             'sha256':HISTORICAL_INTEL_EXPECTED[key]['sha256']}
                        for key in ('wheel','provenance','license')},
             'current_uv_lock_record':lock_record,'current_uv_lock_sdist_sha256':locked_sdist,
             'historical_build_uv_lock_sha256':provenance.get('source_lock',{}).get('sha256'),
             'historical_build_full_lock_matches_current':provenance.get('source_lock',{}).get('sha256')==lock_record['sha256'],
             'source_claims':{'cryptography_sdist_sha256':src.get('sha256'),
                              'OpenSSL_4_0_2_source_sha256':native_component.get('source_sha256'),
                              'upstream_source_patched':provenance.get('upstream_source_patched'),
                              'cargo_locked_offline':provenance.get('build',{}).get('cargo_locked') is True and provenance.get('build',{}).get('environment',{}).get('CARGO_NET_OFFLINE')=='true'},
             'native_static_audit':native,'independent_main_static_audit_sha256':HISTORICAL_INTEL_AUDIT_SHA256,
             'old_fixed_profile':{'wheel_sha256':CRYPTO[1],'provenance_sha256':'b4b8a5561570b81d03812e653cf289ea8f1534665b7c9545e98aca196e31da71','unchanged':True,'substitution_performed':False},
             'limitations':['Historical BUILD_CANDIDATE provenance is an archive statement; raw build logs and source archives are not present in this persisted bundle.',
                            'No Intel import, install, runtime, hardware, COMSOL or scientific acceptance was performed.',
                            'This explicit input profile does not replace the historical default target profile.']}
    return summary,member_bytes,metadata

def pinned(pair,maximum=67108864):
    raw,row=_helpers().raw_file(Path(pair[0]),maximum=maximum)
    if row['sha256']!=pair[1]:raise RuntimeError('fixed target source fingerprint differs')
    return raw,row

def wheel_record(raw,filename,target):
    """Validate full RECORD, safe ZIP, and CP312 metadata identity before copying."""
    name,version,_,tags=parse_wheel_filename(filename)
    if not release.wheel_target_check(filename,target):raise RuntimeError('target wheel tag incompatible')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos=archive.infolist();names=[x.filename for x in infos]
        if len(names)!=len(set(names)) or sum(x.file_size for x in infos)>536870912:raise RuntimeError('wheel ZIP closure refused')
        for item in infos:
            path=PurePosixPath(item.filename)
            if path.is_absolute() or '\\' in item.filename or any(p in {'','.','..'} for p in item.filename.rstrip('/').split('/')) or stat.S_ISLNK(item.external_attr>>16):raise RuntimeError('unsafe wheel ZIP member')
        metadata_names=[n for n in names if n.count('/')==1 and n.endswith('.dist-info/METADATA')]
        if len(metadata_names)!=1:raise RuntimeError('wheel metadata not unique')
        prefix=metadata_names[0].rsplit('/',1)[0];mraw=archive.read(prefix+'/METADATA');wraw=archive.read(prefix+'/WHEEL');metadata=BytesParser(policy=policy.default).parsebytes(mraw);wheel=BytesParser(policy=policy.default).parsebytes(wraw)
        row={'name':str(name),'version':str(version),'filename':filename,'actual_record':{'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()},'METADATA_sha256':hashlib.sha256(mraw).hexdigest(),'WHEEL_sha256':hashlib.sha256(wraw).hexdigest(),'Requires-Python':metadata.get('Requires-Python',''),'Requires-Dist':metadata.get_all('Requires-Dist',[]),'Provides-Extra':metadata.get_all('Provides-Extra',[]),'filename_tags':sorted(str(t) for t in tags),'internal_tags':wheel.get_all('Tag',[])}
        validate_wheel(raw,row)
        record_name=prefix+'/RECORD';records=list(csv.reader(io.StringIO(archive.read(record_name).decode())));seen=set()
        for fields in records:
            if len(fields)!=3 or fields[0] in seen or fields[0] not in names:raise RuntimeError('wheel RECORD closure differs')
            member,digest,size=fields;seen.add(member)
            if member==record_name:
                if digest or size:raise RuntimeError('wheel RECORD self hash differs')
            else:
                data=archive.read(member);expected='sha256='+base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
                if digest!=expected or size!=str(len(data)):raise RuntimeError('wheel RECORD byte digest differs')
        if seen!={n for n in names if not n.endswith('/')}:raise RuntimeError('wheel RECORD member set differs')
        row['RECORD_sha256']=hashlib.sha256(archive.read(record_name)).hexdigest();row['record_members']=len(records)
    return row

def graph(rows,target,project):
    by={row['name']:row for row in rows}
    if len(by)!=len(rows):raise RuntimeError('duplicate target distribution')
    system='Windows' if target=='win_amd64' else 'Darwin';machine={'win_amd64':'AMD64','macos_arm64':'arm64','macos_x86_64':'x86_64'}[target]
    env={'implementation_name':'cpython','implementation_version':'3.12.13','os_name':'nt' if system=='Windows' else 'posix','platform_machine':machine,'platform_python_implementation':'CPython','platform_release':'','platform_system':system,'platform_version':'','python_full_version':'3.12.13','python_version':'3.12','sys_platform':'win32' if system=='Windows' else 'darwin'}
    todo=[('project',text,'desktop') for text in project['dependencies']+project['optional-dependencies']['desktop']];extras={};edges=[]
    # Existing platform adapters require these explicit delivery roots beyond project extras.
    supplements=['pywin32==312'] if target=='win_amd64' else ['pyobjc-framework-CoreMedia==12.2.2','pyobjc-framework-ScreenCaptureKit==12.2.2']
    todo.extend(('production-platform-adapter',text,'') for text in supplements)
    while todo:
        parent,text,extra=todo.pop(0);req=Requirement(text)
        if req.url:raise RuntimeError('direct URL target dependency refused')
        active=not req.marker or req.marker.evaluate({**env,'extra':extra})
        if not active:continue
        name=canonicalize_name(req.name)
        if name not in by or by[name]['version'] not in req.specifier:raise RuntimeError('current target dependency closure missing: '+str(req))
        edges.append({'parent':parent,'requirement':text,'extra':extra,'selected':name,'version':by[name]['version']})
        previous=extras.setdefault(name,set());requested={'',*req.extras}
        for newextra in sorted(requested-previous):
            if newextra and newextra not in by[name]['Provides-Extra']:raise RuntimeError('target missing required extra')
            previous.add(newextra);todo.extend((name,text,newextra) for text in by[name]['Requires-Dist'])
    if set(extras)!=set(by):raise RuntimeError('target includes unreachable distribution: '+','.join(sorted(set(by)-set(extras))))
    return {'marker_environment':env,'active_edges':edges,'reachable_names':sorted(extras),'explicit_platform_roots':supplements,'wheel_code_imported':False}

def prepare_targets(root,arm_root,project_path,*,intel_target_profile=None,historical_archive_record=None):
    import tomllib
    if intel_target_profile not in (None,HISTORICAL_INTEL_PROFILE):raise RuntimeError('unknown finite Intel target-input profile')
    if intel_target_profile is None and historical_archive_record is not None:raise RuntimeError('historical archive record requires its explicit Intel profile')
    if intel_target_profile==HISTORICAL_INTEL_PROFILE and historical_archive_record is None:raise RuntimeError('explicit historical Intel profile requires its archive first record')
    h=_helpers();root=Path(root);out=root/'target-inputs'
    if root.parent!=Path('/private/tmp') or root!=root.resolve() or not root.name.startswith('cvw26-'):raise RuntimeError('fresh target namespace refused')
    h.secure_dir(root)
    if out.exists() or out.is_symlink():raise RuntimeError('target inputs fresh no-clobber required')
    project_raw,project_record=h.raw_file(Path(project_path));project=tomllib.loads(project_raw.decode())['project'];closure=load_closure();versions={r['name']:r['version'] for r in closure['actual_inputs']};arm_manifest=json.loads(h.raw_file(Path(arm_root)/'INPUTS_MANIFEST.json')[0]);arm={r['name']:r for r in arm_manifest['rows']};arm_pins=release.parse_hash_requirements(Path(arm_root)/'requirements-runtime.lock','macos_arm64');sources=[];targets={};payloads={};historical_profile=None;historical_members=None
    for name in arm_pins:
        record=arm[name]['current_record'];raw,_=h.raw_file(Path(record['path']),expected=record);sources.append(record);payloads.setdefault('macos_arm64',{})[Path(record['path']).name]=raw
    for target,pair in ARCHIVES.items():
        if target=='macos_x86_64' and intel_target_profile==HISTORICAL_INTEL_PROFILE:
            raw,record=_historical_intel_archive_record(historical_archive_record)
            historical_profile,historical_members,_=_historical_intel_material(raw,record,project_path)
        else:raw,record=pinned(pair)
        sources.append(record);payloads[target]={}
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if archive.testzip() is not None:raise RuntimeError('preserved target archive corrupt')
            for item in archive.infolist():
                if item.filename.startswith('wheelhouse/') and item.filename.endswith('.whl'):
                    if PurePosixPath(item.filename).parts!=('wheelhouse',PurePosixPath(item.filename).name):raise RuntimeError('target archive wheel path differs')
                    filename=PurePosixPath(item.filename).name;name,version,_,_=parse_wheel_filename(filename)
                    if name=='comsol-mcp' or target=='macos_x86_64' and name=='cryptography':continue
                    if name=='pywin32':
                        if target!='win_amd64' or str(version)!='312':raise RuntimeError('current Windows desktop input differs')
                    elif versions.get(str(name))!=str(version):raise RuntimeError('preserved target version differs from actual43')
                    if filename in payloads[target]:raise RuntimeError('duplicate preserved target member')
                    payloads[target][filename]=archive.read(item)
    for name,row in arm.items():
        if name.startswith('pyobjc'):
            raw,record=h.raw_file(Path(row['current_record']['path']),expected=row['current_record']);payloads['macos_x86_64'][Path(record['path']).name]=raw
    if intel_target_profile==HISTORICAL_INTEL_PROFILE:
        raw=historical_members['wheel'];payloads['macos_x86_64'][HISTORICAL_INTEL_EXPECTED['wheel']['member'].split('/')[-1]]=raw
    else:
        raw,record=pinned(CRYPTO);sources.append(record);payloads['macos_x86_64'][Path(CRYPTO[0]).name]=raw
    raw,record=pinned(PSUTIL_MANIFEST,262144);sources.append(record);psutil=json.loads(raw)
    # The input first record is bound by its fixed manifest SHA, not caller hints.
    wheelrow=psutil['wheel'];raw,record=h.raw_file(Path(wheelrow['path']));
    if any(record[k]!=wheelrow[k] for k in ['path','bytes','sha256']):raise RuntimeError('fixed official psutil wheel changed')
    sources.append(record);payloads['macos_x86_64'][Path(record['path']).name]=raw
    if intel_target_profile==HISTORICAL_INTEL_PROFILE:
        sources.append(historical_profile['current_uv_lock_record'])
        provenance_raw=historical_members['provenance'];license_raw=historical_members['license']
        provenance=json.loads(provenance_raw)
        license_record={'archive_member':HISTORICAL_INTEL_EXPECTED['license']['member'],
                        'bytes':len(license_raw),'sha256':hashlib.sha256(license_raw).hexdigest()}
    else:
        provenance_raw,provenance=pinned(PROVENANCE,8388608);sources.append(provenance)
        license_path=Path('/private/tmp/cvw26-intel-derived-current-r3-9568ce62a041/work/sources/openssl-4.0.2/LICENSE.txt');license_raw,license_record=h.raw_file(license_path);expected_license=json.loads(provenance_raw)['native_component']['license_file']
        if any(license_record[k]!=expected_license[k] for k in ['bytes','sha256']):raise RuntimeError('derived OpenSSL exact primary notice differs')
        sources.append(license_record)
    for target,files in payloads.items():
        rows=[wheel_record(raw,name,target) for name,raw in sorted(files.items())]
        if len(rows)!=COUNTS[target]:raise RuntimeError('exact target runtime cardinality differs')
        proof=graph(rows,target,project);targets[target]={'rows':rows,'graph':proof}
    out.mkdir(mode=0o700)
    for target,value in targets.items():
        directory=out/target;directory.mkdir(mode=0o700);house=directory/'wheelhouse';house.mkdir(mode=0o700)
        for row in value['rows']:h.copy_blob(house/row['filename'],payloads[target][row['filename']]);row['copy']=h.raw_file(house/row['filename'])[1]
        lock='\n'.join(row['name']+'=='+row['version']+' --hash=sha256:'+row['actual_record']['sha256'] for row in sorted(value['rows'],key=lambda r:r['name']))+'\n';h.copy_blob(directory/'requirements-base.lock',lock.encode());value['requirements']=h.raw_file(directory/'requirements-base.lock')[1]
    if historical_profile is not None:
        x86=targets['macos_x86_64'];crypto=next(row for row in x86['rows'] if row['name']=='cryptography')
        historical_profile['wheel_copy']=crypto['copy']
        provenance_path=out/'macos_x86_64'/'HISTORICAL_INTEL_CRYPTO_PROVENANCE.json'
        license_copy_path=out/'macos_x86_64'/'HISTORICAL_INTEL_OPENSSL_LICENSE.txt'
        h.copy_blob(provenance_path,historical_members['provenance']);historical_profile['provenance_copy']=h.raw_file(provenance_path)[1]
        h.copy_blob(license_copy_path,historical_members['license']);historical_profile['license_copy']=h.raw_file(license_copy_path)[1]
        # Keep the legacy top-level fields in their established first-record
        # shape.  The profile carries the parsed historical document and the
        # explicit source archive chain separately.
        provenance=historical_profile['provenance_copy']
        license_record=historical_profile['license_copy']
    return h.publish(out/'TARGET_INPUTS.json',{'schema':HISTORICAL_INTEL_SCHEMA if historical_profile is not None else 'W26_CURRENT_THREE_TARGET_INPUTS_V1','status':'INPUTS_VERIFIED_NOT_INSTALLED_OR_RELEASED','sources':sources,'project':project_record,'directories':{str(path):h.secure_dir(path) for path in [root,out,*[out/t for t in COUNTS],*[out/t/'wheelhouse' for t in COUNTS]]},'targets':targets,'intel_derived_provenance':provenance,'openssl_license':license_record,'intel_historical_prebuilt_profile':historical_profile,'historical_archives':'READ_ONLY_INPUTS_OLD_APPLICATION_EXCLUDED','physicalIntel':'USER_REQUESTED_SKIP','Windows_actual_install':'NOT_RUN'})

def verify_targets(record,project_path):
    h=_helpers();raw,_=h.raw_file(Path(record['path']),expected=record);value=json.loads(raw)
    schema=value.get('schema')
    historical=schema==HISTORICAL_INTEL_SCHEMA
    if schema not in {'W26_CURRENT_THREE_TARGET_INPUTS_V1',HISTORICAL_INTEL_SCHEMA} or set(value['targets'])!=set(COUNTS):raise RuntimeError('closed three target manifest differs')
    if historical!=(value.get('intel_historical_prebuilt_profile') is not None):raise RuntimeError('finite Intel target profile/schema mismatch')
    directory=Path(record['path']).parent
    expected_dirs={str(path) for path in [directory.parent,directory,*[directory/t for t in COUNTS],*[directory/t/'wheelhouse' for t in COUNTS]]}
    if directory.name!='target-inputs' or directory.parent.parent!=Path('/private/tmp') or directory.parent!=directory.parent.resolve() or not directory.parent.name.startswith('cvw26-') or set(value['directories'])!=expected_dirs:raise RuntimeError('closed target directory namespace differs')
    for name,identity in value['directories'].items():h.secure_dir(Path(name),identity)
    for source in value['sources']:h.raw_file(Path(source['path']),expected=source,maximum=67108864)
    import tomllib
    project_raw,observed=h.raw_file(Path(project_path));expected=value['project']
    if any(observed[k]!=expected[k] for k in ['bytes','sha256']):raise RuntimeError('current target project dependency input drift')
    project=tomllib.loads(project_raw.decode())['project'];root=Path(record['path']).parent;h.secure_dir(root)
    if historical:
        profile=value['intel_historical_prebuilt_profile']
        copy_fields={'wheel_copy','provenance_copy','license_copy'}
        if type(profile) is not dict or profile.get('schema')!='W26_INTEL_HISTORICAL_PREBUILT_TARGET_INPUT_V1' or profile.get('profile')!=HISTORICAL_INTEL_PROFILE:
            raise RuntimeError('historical Intel target profile identity differs')
        archive_raw,archive_record=_historical_intel_archive_record(profile.get('archive_record'))
        recomputed,members,_=_historical_intel_material(archive_raw,archive_record,project_path)
        if {key:item for key,item in profile.items() if key not in copy_fields}!=recomputed:
            raise RuntimeError('historical Intel profile archive/source/Mach-O metadata drift')
        if not any(source==archive_record for source in value['sources']) or not any(source==recomputed['current_uv_lock_record'] for source in value['sources']):
            raise RuntimeError('historical Intel archive or current uv.lock first record is not in the source closure')
        x86_rows=value['targets']['macos_x86_64']['rows'];crypto_rows=[row for row in x86_rows if row['name']=='cryptography']
        if len(crypto_rows)!=1 or crypto_rows[0]['actual_record']['sha256']!=HISTORICAL_INTEL_EXPECTED['wheel']['sha256'] or profile.get('wheel_copy')!=crypto_rows[0]['copy']:
            raise RuntimeError('historical Intel wheel profile does not bind the copied x86_64 target wheel')
        if (value.get('intel_derived_provenance')!=profile.get('provenance_copy')
                or value.get('openssl_license')!=profile.get('license_copy')):
            raise RuntimeError('historical Intel release sidecar first records differ from the explicit profile')
        for key,basename,label in (('provenance_copy','HISTORICAL_INTEL_CRYPTO_PROVENANCE.json','provenance'),('license_copy','HISTORICAL_INTEL_OPENSSL_LICENSE.txt','license')):
            copied=profile.get(key);path=root/'macos_x86_64'/basename
            if type(copied) is not dict or copied.get('path')!=str(path):raise RuntimeError('historical Intel profile sidecar leaves its target directory')
            copied_raw,_=h.raw_file(path,expected=copied,maximum=8388608)
            if copied_raw!=members[label]:raise RuntimeError('historical Intel profile sidecar bytes differ from archive member')
    elif value.get('intel_historical_prebuilt_profile') is not None:
        raise RuntimeError('legacy target profile cannot carry historical Intel override data')
    for target,data in value['targets'].items():
        if len(data['rows'])!=COUNTS[target]:raise RuntimeError('target exact cardinality differs')
        house=root/target/'wheelhouse'
        if {p.name for p in house.iterdir()}!={r['filename'] for r in data['rows']}:raise RuntimeError('target wheelhouse membership drift')
        for row in data['rows']:
            if row['copy']['path']!=str(house/row['filename']):raise RuntimeError('target input path leaves fixed wheelhouse')
            blob,_=h.raw_file(Path(row['copy']['path']),expected=row['copy'])
            if wheel_record(blob,row['filename'],target)!={k:v for k,v in row.items() if k!='copy'}:raise RuntimeError('target internal metadata or RECORD drift')
        if graph(data['rows'],target,project)!=data['graph']:raise RuntimeError('current marker graph drift')
        h.raw_file(Path(data['requirements']['path']),expected=data['requirements'])
        pins=release.parse_hash_requirements(Path(data['requirements']['path']),target)
        if pins!={r['name']:{'name':r['name'],'version':r['version'],'hashes':{r['actual_record']['sha256']},'marker':''} for r in data['rows']}:raise RuntimeError('closed target lock pins differ')
    for name,identity in value['directories'].items():h.secure_dir(Path(name),identity)
    return value
