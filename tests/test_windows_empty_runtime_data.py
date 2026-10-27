"""Portable fake-SDK tests; 27 names from pinned CPython material manifest, no runtime access."""
import copy
import ctypes as C
import ntpath
import pytest
from comsol_mcp import _windows_host_bootstrap as b
from tests.test_windows_host_bootstrap import intent_doc, encode, native_file_fixture, raw_sd, SOURCE

EMPTY_PATHS = ('python312/tools/Lib/__phello__/ham/__init__.py', 'python312/tools/Lib/__phello__/ham/eggs.py', 'python312/tools/Lib/email/mime/__init__.py', 'python312/tools/Lib/pydoc_data/__init__.py', 'python312/tools/Lib/site-packages/pip-25.0.1.dist-info/REQUESTED', 'python312/tools/Lib/site-packages/pip/_internal/operations/__init__.py', 'python312/tools/Lib/site-packages/pip/_internal/operations/build/__init__.py', 'python312/tools/Lib/site-packages/pip/_internal/resolution/__init__.py', 'python312/tools/Lib/site-packages/pip/_internal/resolution/legacy/__init__.py', 'python312/tools/Lib/site-packages/pip/_internal/resolution/resolvelib/__init__.py', 'python312/tools/Lib/site-packages/pip/_internal/utils/__init__.py', 'python312/tools/Lib/site-packages/pip/_vendor/cachecontrol/py.typed', 'python312/tools/Lib/site-packages/pip/_vendor/certifi/py.typed', 'python312/tools/Lib/site-packages/pip/_vendor/distro/py.typed', 'python312/tools/Lib/site-packages/pip/_vendor/idna/py.typed', 'python312/tools/Lib/site-packages/pip/_vendor/packaging/py.typed', 'python312/tools/Lib/site-packages/pip/_vendor/platformdirs/py.typed', 'python312/tools/Lib/site-packages/pip/_vendor/pyproject_hooks/py.typed', 'python312/tools/Lib/site-packages/pip/_vendor/resolvelib/compat/__init__.py', 'python312/tools/Lib/site-packages/pip/_vendor/resolvelib/py.typed', 'python312/tools/Lib/site-packages/pip/_vendor/rich/py.typed', 'python312/tools/Lib/site-packages/pip/_vendor/truststore/py.typed', 'python312/tools/Lib/site-packages/pip/_vendor/urllib3/contrib/__init__.py', 'python312/tools/Lib/site-packages/pip/_vendor/urllib3/contrib/_securetransport/__init__.py', 'python312/tools/Lib/site-packages/pip/_vendor/urllib3/packages/__init__.py', 'python312/tools/Lib/site-packages/pip/_vendor/urllib3/packages/backports/__init__.py', 'python312/tools/Lib/urllib/__init__.py')
EMPTY_SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

def zero_row(path=EMPTY_PATHS[0]):
    return {'path':path, 'size':0, 'sha256':EMPTY_SHA, 'role':'runtime'}

def test_original_27_zero_materials_parse_as_lib_runtime():
    doc,_=intent_doc(); doc['files'].extend(zero_row(p) for p in EMPTY_PATHS)
    plan=b.parse_plan(encode(doc)); rows={r.path:r for r in plan.files}
    assert len(EMPTY_PATHS)==len(set(EMPTY_PATHS))==27
    assert all(rows[p].size==0 and rows[p].sha256==EMPTY_SHA and rows[p].role=='runtime' for p in EMPTY_PATHS)

def test_duplicate_empty_path_is_refused_case_insensitively():
    doc,_=intent_doc();parent,name=EMPTY_PATHS[0].rsplit('/',1)
    doc['files'].extend([zero_row(),zero_row(parent+'/'+name.upper())])
    with pytest.raises(b.BootstrapError):b.parse_plan(encode(doc))

@pytest.mark.parametrize('field,value', [
    ('sha256','0'*64),('size',False),('size',-1),('size',b.MAX_FILE+1),
    ('role','python'),('role','python_archive'),('role','bundle_archive'),('role','jdk'),
    ('path','python312/tools/empty.dat'),('path','python312/tools/lib/empty.dat'),
    ('path','materials/empty.dat'),('path','python312/tools/Lib/../escape'),
    ('path','python312/tools/Lib/empty.exe'),('path','python312/tools/Lib/empty.DLL'),
    ('path','python312/tools/Lib/empty.pyd'),('path','python312/tools/Lib/empty.jar'),
    ('path','python312/tools/Lib/empty.class'),
])
def test_empty_runtime_invalid_inputs_refused(field,value):
    doc,_=intent_doc(); row=zero_row();row[field]=value;doc['files'].append(row)
    with pytest.raises(b.BootstrapError):b.parse_plan(encode(doc))

@pytest.mark.parametrize('path',[
    'python312/tools/python.exe','python312/tools/python312.dll',
    'python312/tools/Lib/site-packages/comsol_mcp/_control_daemon.py',
    'jdk11/bin/java.exe','materials/python.nupkg','materials/offline.zip'])
def test_required_files_never_empty(path):
    doc,_=intent_doc();row=next(r for r in doc['files'] if r['path']==path);row.update(size=0,sha256=EMPTY_SHA)
    with pytest.raises(b.BootstrapError):b.parse_plan(encode(doc))

def test_real_windows_fake_sdk_empty_image_and_growth_refusal():
    native,model,path,_=native_file_fixture();model.tree[path]['data']=b''
    handle=native._open(path,False);image=native.image(handle,path,False)
    assert image.size==0 and image.sha256==EMPTY_SHA
    reads=[c for c in native.owner.calls if c.name=='ReadFile']
    assert len(reads)==1 and reads[0].args[2]==1
    model.tree[path]['data']=b'x'
    with pytest.raises(b.BootstrapError) as exc:list(native._read_blocks(handle,0))
    assert exc.value.code=='FILE_DRIFT'

def test_real_windows_fake_sdk_zero_create_flush_and_readback():
    native,model,path,_=native_file_fixture();model.tree[path]['data']=b''
    opening=native.sdk['CreateFileW'];calls=[]
    def create(*args):
        if args[4]==1:
            model.add(args[0],False,b'');args=list(args);args[4]=3
        return opening(*args)
    native.sdk['CreateFileW']=create
    native.sdk['FlushFileBuffers']=lambda handle:(calls.append(('flush',handle.value)) or 1)
    native.sdk['WriteFile']=lambda *args:(_ for _ in ()).throw(AssertionError('zero-size write'))
    source=native._open(path,False);target=ntpath.join(SOURCE,'empty-created.bin')
    handle=native.create_file(target,raw_sd(),source,0)
    image=native.image(handle,target,False)
    assert image.size==0 and image.sha256==EMPTY_SHA and calls==[('flush',handle.value)]
    assert not any(c.name=='WriteFile' for c in native.owner.calls)

def test_first_unknown_on_zero_read_blocks_later_sdk_and_close():
    native,model,path,_=native_file_fixture();model.tree[path]['data']=b'';handle=native._open(path,False)
    calls=[]
    def uncertain(*args):calls.append('read');raise OSError('synthetic unknown output')
    native.sdk['ReadFile']=uncertain
    with pytest.raises(OSError):list(native._read_blocks(handle,0))
    count=len(native.owner.calls)
    with pytest.raises(b.BootstrapError):native.read(handle,0)
    with pytest.raises(b.BootstrapError):native.owner.close_all()
    assert len(native.owner.calls)==count and calls==['read'] and not handle.closed

def test_unknown_empty_flush_blocks_later_reads_and_closes():
    native,model,path,_=native_file_fixture();model.tree[path]['data']=b''
    opening=native.sdk['CreateFileW'];calls=[]
    def create(*args):
        if args[4]==1:
            model.add(args[0],False,b'');args=list(args);args[4]=3
        return opening(*args)
    def flush(*args):calls.append('flush');raise OSError('synthetic unknown flush')
    native.sdk['CreateFileW']=create;native.sdk['FlushFileBuffers']=flush
    native.sdk['WriteFile']=lambda *args:(_ for _ in ()).throw(AssertionError('zero-size write'))
    source=native._open(path,False)
    with pytest.raises(OSError):native.create_file(ntpath.join(SOURCE,'unknown-flush.bin'),raw_sd(),source,0)
    count=len(native.owner.calls)
    with pytest.raises(b.BootstrapError):native.read(source,0)
    with pytest.raises(b.BootstrapError):native.owner.close_all()
    assert len(native.owner.calls)==count and calls==['flush'] and not source.closed
