"""Native ARM runtime evidence only; Tcl interpreter, no GUI/window/COMSOL."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import ssl
import sys
import tempfile
import importlib
import importlib.util
import stat

def read_bound(path,expected=None,maximum=1073741824):
    path=Path(path);fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        before=os.fstat(fd)
        fields=lambda s:(s.st_dev,s.st_ino,s.st_uid,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        if not stat.S_ISREG(before.st_mode) or before.st_uid!=os.getuid() or before.st_nlink!=1 or before.st_mode&0o022 or before.st_size>maximum:
            raise RuntimeError('native profile input owner/link/bound refused')
        chunks=[]
        while chunk:=os.read(fd,1048576):chunks.append(chunk)
        raw=b''.join(chunks)
        if fields(before)!=fields(os.fstat(fd)) or fields(before)!=fields(path.lstat()) or len(raw)!=before.st_size:
            raise RuntimeError('native profile input same-FD/name drift')
        row={'path':str(path),'dev':before.st_dev,'inode':before.st_ino,'uid':before.st_uid,'mode':stat.S_IMODE(before.st_mode),
             'nlink':before.st_nlink,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
        if expected is not None and row!=expected:raise RuntimeError('native profile original producer first record differs')
        return raw,row
    finally:os.close(fd)

def verified_scripts(manifest,library,allowed):
    library=Path(library).resolve(strict=True)
    if not library.is_relative_to(allowed):raise RuntimeError('actual Tcl script library escaped current mode scope')
    matches=[x for x in manifest if Path(x['folder'])==library]
    if len(matches)!=1:raise RuntimeError('actual Tcl script root lacks original producer closure')
    for closure in manifest:
        folder=Path(closure['folder'])
        if not folder.is_relative_to(allowed):raise RuntimeError('Tcl/Tk script producer scope differs')
        paths=[p for p in folder.rglob('*') if not p.is_dir() or p.is_symlink()]
        if {str(p.relative_to(folder)) for p in paths}!=set(closure['files']):raise RuntimeError('Tcl/Tk full script membership differs')
        for path in paths:read_bound(path,closure['files'][str(path.relative_to(folder))])
    return {'actual_info_library':str(library),'verified_whole_script_closures':len(manifest),
            'actual_init_tcl':read_bound(library/'init.tcl')[1]}

def admit_arm_observation(first):
    if first['platform']!='arm64':
        raise RuntimeError('actual required ARM execution differs; fat bytes are not ARM native evidence')

def admit_provider(provider):
    if tuple(provider['INFO'])!=(3,5,0,9,0) or provider['NUMBER']!=0x30500090 or provider['TEXT']!='OpenSSL 3.5.9 29 Sep 2026':
        raise RuntimeError('exact original SSL359 native provider tuple/number/full text differs')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--mode',choices=['build-local','relocated'],required=True)
    parser.add_argument('--script-manifest',type=Path,required=True);parser.add_argument('--script-sha',required=True)
    parser.add_argument('--tls-helper-sha',required=True);args=parser.parse_args();root=args.root
    output=root/'logs/RUNTIME_PROFILE_FIRST.json'
    provider={'INFO':list(ssl.OPENSSL_VERSION_INFO),'NUMBER':ssl.OPENSSL_VERSION_NUMBER,'TEXT':ssl.OPENSSL_VERSION}
    first={'provider':provider,'python_version':sys.version,'executable':sys.executable,'prefix':sys.prefix,'platform':platform.machine()}
    # Provider first observations are saved before rejecting an incorrect build.
    with output.open('x') as stream:json.dump(first,stream,indent=2);stream.flush();os.fsync(stream.fileno())
    admit_arm_observation(first)
    admit_provider(provider)
    if sys.version_info[:3]!=(3,12,13):
        raise RuntimeError('fixed CPython3.12.13/SSL359 native provider differs; first observations retained')
    if Path(sys.executable).resolve()!=root/'install/cpython/bin/python3.12' or Path(sys.prefix)!=root/'install/cpython':
        raise RuntimeError('source-outside installed runtime identity differs')
    required={}
    for name in ('_ssl','_hashlib','ssl','hashlib','zlib','sqlite3','ctypes','venv','_lzma','_gdbm','_tkinter'):
        module=importlib.import_module(name);path=Path(module.__file__).resolve(strict=True)
        if not path.is_relative_to(root/'install/cpython'):raise RuntimeError('required module escaped current prefix '+name)
        required[name]=read_bound(path)[1]
    import dbm.gnu
    import lzma
    import tkinter
    import zlib
    import sqlite3
    import ctypes
    payload=b'synthetic W26 lzma and hashlib fixture';assert lzma.decompress(lzma.compress(payload))==payload
    assert hashlib.sha256(payload).hexdigest()==hashlib.new('sha256',payload).hexdigest()
    known='ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
    assert hashlib.sha256(b'abc').hexdigest()==known
    assert importlib.import_module('_hashlib').openssl_sha256(b'abc').hexdigest()==known
    assert zlib.decompress(zlib.compress(payload))==payload
    with sqlite3.connect(':memory:') as database:
        database.execute('create table fixture (value blob)');database.execute('insert into fixture values (?)',(payload,))
        assert database.execute('select value from fixture').fetchone()[0]==payload
    assert ctypes.sizeof(ctypes.c_void_p)==8
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    assert context.minimum_version>=ssl.TLSVersion.TLSv1_2
    assert not ssl.HAS_SSLv3 and ssl.HAS_TLSv1_2 and ssl.HAS_TLSv1_3
    helper_path=Path(__file__).with_name('_w26_original_tls.py');helper_raw,_=read_bound(helper_path)
    if hashlib.sha256(helper_raw).hexdigest()!=args.tls_helper_sha:raise RuntimeError('exact original TLS helper source differs')
    helper_namespace={};exec(compile(helper_raw,str(helper_path),'exec'),helper_namespace)
    certroot=root/'install/cpython/share/w26-runtime-fixtures'
    cert_cases=helper_namespace['runtime_cert_cases'](ssl,certroot)
    transport=helper_namespace['actual_memorybio_cases'](ssl,certroot/'runtime-certfixtures/keycert.pem')
    with tempfile.TemporaryDirectory(dir=root/'tmp') as folder:
        path=str(Path(folder)/'synthetic-gdbm')
        with dbm.gnu.open(path,'n') as database:database[b'fixture']=payload;database.sync()
        with dbm.gnu.open(path,'r') as database:assert database[b'fixture']==payload
    interp=tkinter.Tcl();assert str(interp.eval('info patchlevel'))=='8.6.18';assert interp.eval('expr {7 * 6}')=='42'
    script_raw,script_record=read_bound(args.script_manifest,maximum=16777216)
    if script_record['sha256']!=args.script_sha:raise RuntimeError('Tcl/Tk producer closure manifest differs')
    script_result=verified_scripts(json.loads(script_raw),interp.eval('info library'),root/'install' if args.mode=='build-local' else root/'install/cpython')
    assert interp.eval('package require http')=='2.9.9'
    assert interp.eval('package require msgcat')=='1.6.1'
    assert interp.eval('clock format 0 -gmt true -format {%Y}')=='1970'
    assert tkinter._default_root is None
    bundled=root/'install/cpython/lib/python3.12/ensurepip/_bundled/pip-25.0.1-py3-none-any.whl'
    assert hashlib.sha256(bundled.read_bytes()).hexdigest()=='c46efd13b6aa8279f33f2864459c8ce587ea6a1a59ee20de055868d8f7688f7f'
    result={**first,'status':'PASS_ARM_SSL359_LZMA_GDBM_TCL_HEADLESS_REQUIRED_RUNTIME',
        'required_modules':required,'sha256_known_vector':True,'zlib_roundtrip':True,'sqlite_roundtrip':True,'ctypes_pointer64':True,
        'actual_certificate_cases':cert_cases,'actual_MemoryBIO':transport,'SSLContext_min_TLS12':True,
        'gdbm_roundtrip':True,'Tcl_scripts':script_result,'mode':args.mode,'delivery_acceptance':'NOT_RUN',
        'optional_dbm_missing':importlib.util.find_spec('_dbm') is None,'Tk_window_created':False,'GUI_acceptance':'NOT_RUN',
        'physical_Intel_acceptance':'NOT_RUN','COMSOL':'NOT_RUN','dependency43_install':'NOT_RUN'}
    with (root/'logs/RUNTIME_PROFILE_RESULT.json').open('x') as stream:json.dump(result,stream,indent=2);stream.flush();os.fsync(stream.fileno())

if __name__=='__main__':main()
