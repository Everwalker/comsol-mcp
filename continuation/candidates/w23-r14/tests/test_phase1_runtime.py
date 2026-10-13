import os
from pathlib import Path
import struct,zlib
import pytest
from comsol_mcp._phase1_runtime import bounded_path, client_classpath,validate_png

def test_paths_cannot_escape_via_symlink(tmp_path):
    root=tmp_path/'evidence';root.mkdir();outside=tmp_path/'outside';outside.mkdir();(root/'escape').symlink_to(outside)
    with pytest.raises(ValueError):bounded_path(root/'escape'/'file',root)
    with pytest.raises(ValueError):bounded_path(root,root)

def test_classpath_uses_official_apiplugins_and_rejects_missing(tmp_path):
    (tmp_path/'bin').mkdir();(tmp_path/'apiplugins').mkdir()
    (tmp_path/'bin/comsolclientpath.txt').write_text('one.jar\ntwo.jar\n')
    (tmp_path/'apiplugins/one.jar').touch()
    with pytest.raises(ValueError):client_classpath(tmp_path)
    (tmp_path/'apiplugins/two.jar').touch()
    cp,sha,count=client_classpath(tmp_path)
    entries = [Path(entry) for entry in cp.split(os.pathsep)]
    assert count==2 and entries == [tmp_path/'apiplugins/one.jar', tmp_path/'apiplugins/two.jar'] and len(sha)==64

def test_png_validator_checks_raster_and_crc(tmp_path):
    def chunk(kind,payload):return struct.pack('>I',len(payload))+kind+payload+struct.pack('>I',zlib.crc32(kind+payload))
    png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\x00\x00\xff\x00'))+chunk(b'IEND',b'')
    p=tmp_path/'x.png';p.write_bytes(png);assert validate_png(p)==[1,1]
    p.write_bytes(png[:-1]+b'\x00')
    with pytest.raises(ValueError):validate_png(p)
