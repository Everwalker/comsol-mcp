"""Portable archive, target marker and hash controls; no target runtime execution."""
import base64,csv,hashlib,io,zipfile
import pytest
from tools import w26_target_inputs as targets

def wheel_bytes(*,wrong_digest=False,extra_member=False):
    info='demo_pkg-1.0.dist-info';members={info+'/METADATA':b'Metadata-Version: 2.1\nName: demo-pkg\nVersion: 1.0\nRequires-Python: >=3.12\n\n',info+'/WHEEL':b'Wheel-Version: 1.0\nTag: py3-none-any\n', 'demo_pkg/__init__.py':b'VALUE=1\n'}
    stream=io.StringIO();writer=csv.writer(stream)
    for name,raw in members.items():writer.writerow([name,'sha256='+base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b'=').decode(),str(len(raw))])
    writer.writerow([info+'/RECORD','','']);members[info+'/RECORD']=stream.getvalue().encode()
    if wrong_digest:members['demo_pkg/__init__.py']=b'TAMPER\n'
    if extra_member:members['demo_pkg/unrecorded.py']=b'X=1\n'
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as archive:
        for name,raw in members.items():archive.writestr(name,raw)
    return buffer.getvalue()

def test_complete_record_and_internal_metadata_accept_portable_wheel():
    row=targets.wheel_record(wheel_bytes(),'demo_pkg-1.0-py3-none-any.whl','win_amd64')
    assert row['record_members']==4 and row['Requires-Python']=='>=3.12'

@pytest.mark.parametrize('fault',['digest','unrecorded','wrongtarget'])
def test_wheel_record_or_target_tamper_refused(fault):
    name='demo_pkg-1.0-cp312-cp312-macosx_11_0_arm64.whl' if fault=='wrongtarget' else 'demo_pkg-1.0-py3-none-any.whl'
    with pytest.raises(RuntimeError):targets.wheel_record(wheel_bytes(wrong_digest=fault=='digest',extra_member=fault=='unrecorded'),name,'win_amd64')

def graph_rows(target):
    names=['demo-pkg','windows-only','pywin32'] if target=='win_amd64' else ['demo-pkg','mac-only','pyobjc-framework-coremedia','pyobjc-framework-screencapturekit']
    rows=[{'name':name,'version':'312' if name=='pywin32' else '12.2.2' if name.startswith('pyobjc') else '1.0','Requires-Dist':[],'Provides-Extra':[]} for name in names]
    rows[0]['Requires-Dist']=["windows-only==1.0; sys_platform=='win32'","mac-only==1.0; sys_platform=='darwin'"]
    return rows

@pytest.mark.parametrize('target',['win_amd64','macos_arm64','macos_x86_64'])
def test_current_target_marker_graph_does_not_use_host_markers(target):
    rows=graph_rows(target);result=targets.graph(rows,target,{'dependencies':['demo-pkg==1.0'],'optional-dependencies':{'desktop':[]}})
    assert len(result['reachable_names'])==len(rows)
    assert ('windows-only' in result['reachable_names'])==(target=='win_amd64')

@pytest.mark.parametrize('fault',['missing','wrongversion','unused'])
def test_target_closure_missing_wrongversion_or_unused_refused(fault):
    rows=graph_rows('win_amd64')
    if fault=='missing':rows.pop(1)
    elif fault=='wrongversion':rows[1]['version']='2.0'
    else:rows.append({'name':'unused','version':'1.0','Requires-Dist':[],'Provides-Extra':[]})
    with pytest.raises(RuntimeError):targets.graph(rows,'win_amd64',{'dependencies':['demo-pkg==1.0'],'optional-dependencies':{'desktop':[]}})

@pytest.mark.parametrize('fault',['symlink','replacement','wrong-directory-membership'])
def test_target_directory_identity_refused_before_project_read(fault):
    import json,os,tempfile,shutil
    from pathlib import Path
    h=targets._helpers();root=Path(tempfile.mkdtemp(prefix='cvw26-target-controls-',dir='/private/tmp'))
    try:
        directory=root/'target-inputs';directory.mkdir(mode=0o700)
        paths=[root,directory]
        for name in targets.COUNTS:
            path=directory/name;path.mkdir(mode=0o700);house=path/'wheelhouse';house.mkdir(mode=0o700);paths.extend([path,house])
        value={'schema':'W26_CURRENT_THREE_TARGET_INPUTS_V1','targets':{name:{} for name in targets.COUNTS},'directories':{str(path):h.secure_dir(path) for path in paths},'sources':[]}
        damaged=directory/'macos_arm64/wheelhouse'
        if fault=='wrong-directory-membership':value['directories'][str(root/'foreign')]={'device':0,'inode':0}
        else:
            damaged.rename(damaged.with_name('original-wheelhouse'))
            if fault=='symlink':damaged.symlink_to(damaged.with_name('original-wheelhouse'),target_is_directory=True)
            else:damaged.mkdir(mode=0o700)
        manifest=h.publish(directory/'TARGET_INPUTS.json',value)
        with pytest.raises(Exception,match='directory'):targets.verify_targets(manifest,root/'DO_NOT_READ_MISSING_PROJECT')
    finally:shutil.rmtree(root)
