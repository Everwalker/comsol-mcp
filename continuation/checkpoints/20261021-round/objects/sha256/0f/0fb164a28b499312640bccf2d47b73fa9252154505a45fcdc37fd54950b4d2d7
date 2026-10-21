"""Actual Mach-O parser controls; no native process or source build."""
import json,struct,zipfile
from pathlib import Path
import pytest
from tools import w26_intel_derived as d

SELF='@rpath/cryptography.hazmat.bindings._rust.abi3.so'
ROOT=Path('/private/tmp/cvw26-intel-derived-current-r3-9568ce62a041')

def command(code,name):
    encoded=name.encode()+b'\0';return struct.pack('<IIIIII',code,24+len(encoded)+(-len(encoded))%8,24,0,0,0)+encoded+b'\0'*((-len(encoded))%8)

def image(kind=6,ids=(SELF,),minimum=0xc0000,cpu=0x1000007,lib='/usr/lib/libSystem.B.dylib',rpath=False):
    rows=[struct.pack('<IIIIII',0x32,24,1,minimum,0,0),command(0xc,lib)]+[command(0xd,name) for name in ids]
    if rpath:rows.append(struct.pack('<II',0x8000001c,8))
    commands=b''.join(rows);return struct.pack('<IiiIIIII',0xfeedfacf,cpu,3,kind,len(rows),len(commands),0,0)+commands

@pytest.mark.parametrize('kind,ids',[(6,(SELF,)),(8,())])
def test_actual_parser_accepts_exact_python_extension_kinds(kind,ids):
    r=d.macho12(image(kind,ids));assert r['mach_o_filetype']==kind
    assert r['self_install_name']==(SELF if kind==6 else None)

@pytest.mark.parametrize('options',[
    {'ids':()}, {'ids':('/tmp/private.dylib',)}, {'ids':(SELF,SELF)}, {'kind':8},
    {'kind':2,'ids':()}, {'cpu':0x100000c}, {'minimum':0xb0000}, {'lib':'/tmp/libssl.dylib'}, {'rpath':True},
])
def test_actual_parser_keeps_invalid_identity_cpu_floor_and_link_rejection(options):
    with pytest.raises(RuntimeError):d.macho12(image(**options))

@pytest.mark.parametrize('damage',['short','offset','unterminated','padding'])
def test_actual_id_dylib_bounds_and_name_refused(damage):
    raw=bytearray(image());pos=32+24+len(command(0xc,'/usr/lib/libSystem.B.dylib'))
    if damage=='short':struct.pack_into('<I',raw,pos+4,16)
    elif damage=='offset':struct.pack_into('<I',raw,pos+8,1)
    elif damage=='unterminated':raw[pos+24:]=b'x'*(len(raw)-pos-24)
    else:raw[-1]=1
    with pytest.raises(RuntimeError):d.macho12(bytes(raw))

def test_static_object_rule_remains_explicit_one():
    r=d.macho12(image(kind=1,ids=()),filetypes=(1,),require_libraries=False)
    assert r['mach_o_filetype']==1
    with pytest.raises(RuntimeError):d.macho12(image(kind=6),filetypes=(1,),require_libraries=False)

@pytest.mark.skipif(not (ROOT/'work/wheelhouse').is_dir(),reason='SKIP_ENV/NOT_RUN: actual R3 artifact absent')
def test_actual_R3_wheel_readonly_header_observation():
    p=ROOT/'work/wheelhouse/cryptography-50.0.1-cp312-abi3-macosx_12_0_x86_64.whl'
    before=d._helpers().raw_file(p)[1]
    with zipfile.ZipFile(p) as z:r=d.macho12(z.read('cryptography/hazmat/bindings/_rust.abi3.so'))
    assert r['mach_o_filetype']==6 and r['self_install_name']==SELF
    assert r['minimum_os']=='12.0' and set(r['dynamic_libraries'])=={'/usr/lib/libSystem.B.dylib','/usr/lib/libiconv.2.dylib'}
    d._helpers().raw_file(p,expected=before)
