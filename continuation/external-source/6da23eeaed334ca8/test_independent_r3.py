"""Actual named-output gate and actual binary parser, public synthetic data only."""
from pathlib import Path
import hashlib,json,os,struct
import pytest
import openssl359_build_gate_r3 as gate
import openssl359_successor_r2 as r2
from acquire_exact_archives import Refusal

OUT=Path(__file__).parent
NAMES={'Makefile':'Makefile','python':'python.exe','_ssl':'_ssl.cpython-312-darwin.so','_hashlib':'_hashlib.cpython-312-darwin.so'}
def blob(path,data):path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);path.chmod(0o600)
def fat(kind,marker):
    parts=[]
    for cpu in (0x100000c,0x1000007):
        raw=struct.pack('<IiiIIIII',0xfeedfacf,cpu,0,kind,1,16,0,0)+struct.pack('<IIII',0x24,16,0xb0000,0)+marker
        parts.append((cpu,raw))
    offset=48;headers=[];body=[]
    for cpu,raw in parts:headers.append(struct.pack('>iiIII',cpu,0,offset,len(raw),0));body.append(raw);offset+=len(raw)
    return struct.pack('>II',0xcafebabe,2)+b''.join(headers)+b''.join(body)
def fixture(folder):
    build=folder/'owned-build';modules=build/'Modules';view=build/'build/lib.macosx-11.0-universal2-3.12';logs=folder/'logs'
    modules.mkdir(parents=True,mode=0o700);view.mkdir(parents=True,mode=0o700);logs.mkdir(mode=0o700)
    fixed={'Makefile':b'BUILDPYTHON=python$(BUILDEXE)\nBUILDEXE=.exe\n# PUBLIC ORIGINAL A\n',
        'python':fat(2,b'PUBLIC ORIGINAL A'),'_ssl':fat(8,b'PUBLIC ORIGINAL A'),'_hashlib':fat(8,b'PUBLIC ORIGINAL A')}
    paths={name:(build/file if name in ('Makefile','python') else modules/file) for name,file in NAMES.items()}
    for name,p in paths.items():blob(p,fixed[name])
    for name in ('_ssl','_hashlib'):(view/NAMES[name]).symlink_to('../../Modules/'+NAMES[name])
    for suffix in ('stdout','stderr'):blob(logs/suffix,b'')
    command={'argv':['/usr/bin/make','-j2'],'rc':0,'owned_process_terminal':True,'owned_group_absent':True,'stdout':'stdout','stderr':'stderr'}
    return {'build':build,'logs':logs},command,paths,fixed,view


def test_actual_fixed_DirFD_gate_supported_original_views_and_known_rejections(monkeypatch,tmp_path):
    original=r2.layout();result=json.loads((original['root']/'RESULT.json').read_bytes())
    command=next(row for row in result['commands'] if row['argv']==['/usr/bin/make','-j2'])
    positive=gate.required_build_outputs(original,command)
    assert set(positive['required_build_binaries'])=={'python','_ssl','_hashlib'}
    assert all(set(row['slices'])=={'arm64','x86_64'} for row in positive['required_build_binaries'].values())
    assert positive['runtime_acceptance']=='NOT_RUN' and not positive['general_symlink_follow']
    controls=[]
    for index,target in enumerate(('/outside/public','../../Modules/_ssl_failed.cpython-312-darwin.so')):
        folder=tmp_path/('literal-'+str(index));folder.mkdir();x,c,paths,values,view=fixture(folder)
        link=view/NAMES['_ssl'];link.unlink();link.symlink_to(target)
        with pytest.raises(Refusal):gate.required_build_outputs(x,c)
        controls.append({'wrong_or_failed_literal':target,'refused':True})
    folder=tmp_path/'terminal-link-swap';folder.mkdir();x,c,paths,values,view=fixture(folder)
    original_read=gate.regular_leaf
    def last_link_swap(parent,name,path,*args,**kwargs):
        value=original_read(parent,name,path,*args,**kwargs)
        if name==NAMES['_hashlib']:
            link=view/NAMES['_ssl'];link.unlink();link.symlink_to('../../Modules/'+NAMES['_ssl'])
        return value
    with monkeypatch.context() as patch:
        patch.setattr(gate,'regular_leaf',last_link_swap)
        with pytest.raises(Refusal,match='before whole gate completed'):gate.required_build_outputs(x,c)
    controls.append({'whole_gate_original_first_link_identity_swap':'REFUSED'})
    (OUT/'ACTUAL_R2_READONLY_POSITIVE_AND_EXISTING_GATES.json').write_text(json.dumps({
        'status':'PASS_SCOPED_READONLY_BYTES_AND_SOURCE_SOFTWARE','actual_R2_physical_gate':positive,
        'public_synthetic_existing_negative_controls':controls,'new_target_runtime_or_compile':0},indent=2)+'\n')


def test_actual_whole_gate_accepts_changed_current_normal_regular_members(monkeypatch,tmp_path):
    records=[]
    for member in NAMES:
        for mutation in ('new_inode','same_inode_same_size_bytes'):
            folder=tmp_path/(member+'-'+mutation);folder.mkdir();x,c,paths,values,view=fixture(folder)
            target=paths[member];old_stat=target.stat();original_read=gate.regular_leaf;changed=[]
            replacement=values[member].replace(b'PUBLIC ORIGINAL A',b'PUBLIC ORIGINAL B')
            assert len(replacement)==len(values[member]) and replacement!=values[member]
            def swap_after_last_read(parent,name,path,*args,**kwargs):
                value=original_read(parent,name,path,*args,**kwargs)
                if name==NAMES['_hashlib']:
                    if mutation=='new_inode':
                        incoming=folder/'public-regular-replacement';blob(incoming,replacement);os.replace(incoming,target)
                    else:target.write_bytes(replacement)
                    changed.append(True)
                return value
            with monkeypatch.context() as patch:
                patch.setattr(gate,'regular_leaf',swap_after_last_read)
                accepted=gate.required_build_outputs(x,c)
            assert changed==[True] and accepted['required_module_ERROR_absent']
            row=accepted['actual_build_Makefile'] if member=='Makefile' else accepted['required_build_binaries'][member]['file']
            current=target.stat();current_sha=hashlib.sha256(target.read_bytes()).hexdigest()
            assert row['sha256']==hashlib.sha256(values[member]).hexdigest() and row['sha256']!=current_sha
            assert row['identity']=={'device':old_stat.st_dev,'inode':old_stat.st_ino}
            assert (current.st_ino!=old_stat.st_ino)==(mutation=='new_inode')
            assert current.st_size==old_stat.st_size and current.st_uid==old_stat.st_uid and current.st_mode==old_stat.st_mode
            assert all(not link['followed_for_bytes'] for link in accepted['official_exact_symbolic_views'])
            records.append({'member':member,'mutation':mutation,'whole_gate_returned_success':True,
                'accepted_first_file_record':row,'current_file_identity':{'device':current.st_dev,'inode':current.st_ino},
                'current_file_sha256':current_sha,'same_size_UID_mode_and_directory_identities':True,
                'official_link_identity_and_literal_unchanged':True,'accepted_SHA_is_stale':True})
    assert len(records)==8
    (OUT/'ACTUAL_CURRENT_REGULAR_MEMBER_WHOLE_GATE_RESIDUAL.json').write_text(json.dumps({
        'status':'REPRODUCED_P2_SOURCE_SOFTWARE_ONLY','actual_producer':'openssl359_build_gate_r3.required_build_outputs',
        'actual_binary_parser':'tls_binary_r1.fat_binary (not mocked)',
        'cases':records,'fixture':'Public minimal inert Mach-O-format data and explicit synthetic make boundary metadata; no OS proof/authority or native compile/runtime minted',
        'handoff_source_fact':'cp-build saves accepted record, then cp-install only verify_copied_inputs, no first accepted regular build member revalidation',
        'user_data_actual_source_newroot_native_effects':0},indent=2)+'\n')
