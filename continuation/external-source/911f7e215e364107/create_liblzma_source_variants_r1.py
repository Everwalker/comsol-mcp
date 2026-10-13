from pathlib import Path
E=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository/docs/full_project_continuation_v2/evidence/intel-builder-execution-r1')
# Fixed five-member first-accepted physical build gate, same FD protocol.
s=(E/'openssl359_build_gate_r4.py').read_text().replace("('_ssl','_hashlib')","('_ssl','_hashlib','_lzma')").replace("{'Makefile','python','_ssl','_hashlib'}","{'Makefile','python','_ssl','_hashlib','_lzma'}").replace('four original accepted regular members required','five original accepted regular members required').replace('all_four_original_first_records_unchanged','all_five_original_first_records_unchanged').replace('(?:_ssl|_hashlib)','(?:_ssl|_hashlib|_lzma)').replace('only two exact required normal module names allowed','only three exact required normal module names allowed')
(E/'liblzma_build_gate_r1.py').open('x').write(s)
# Existing exact CP archive extraction/accepted 359 fat & cert copying.
s=(E/'openssl359_cpython_checkpoint_r4.py').read_text().replace('intel-tls-openssl359-cpython-r4','intel-liblzma-r1')
s=s.replace("if deps.tree_record(x['fat']/'include')!=report['fresh_headers']", "if [r for r in deps.tree_record(x['fat']/'include') if r['relative_path']=='openssl' or r['relative_path'].startswith('openssl/')]!=report['fresh_headers']")
(E/'liblzma_cpython_checkpoint_r1.py').open('x').write(s)
# Same complete native runtime profile, required LZMA and tar.xz consumer.
s=(E/'openssl359_runtime_profile_r3.py').read_text().replace('intel-tls-openssl359-cpython-r4','intel-liblzma-r1').replace("OUTPUT_ROOT=INSTALLED.parent/'intel-tls-openssl359-runtime-r2'","OUTPUT_ROOT=INSTALLED")
s=s.replace("('zlib','sqlite3','ctypes','venv')", "('zlib','sqlite3','ctypes','venv','_lzma')")
s=s.replace(" result={'software_variant'", " import io,lzma,tarfile\n payload=b'comsol-public-tar-xz-product-fixture'\n compressed=lzma.compress(payload,format=lzma.FORMAT_XZ)\n if lzma.decompress(compressed,format=lzma.FORMAT_XZ)!=payload:raise RuntimeError('required real LZMA roundtrip differs')\n archive=io.BytesIO()\n with tarfile.open(fileobj=archive,mode='w:xz') as tar:\n  entry=tarfile.TarInfo('public-product-fixture.txt');entry.size=len(payload);tar.addfile(entry,io.BytesIO(payload))\n archive.seek(0)\n with tarfile.open(fileobj=archive,mode='r:xz') as tar:\n  if tar.getnames()!=['public-product-fixture.txt'] or tar.extractfile('public-product-fixture.txt').read()!=payload:raise RuntimeError('required real tar.xz roundtrip differs')\n result={'actual_lzma_roundtrip':True,'actual_tar_xz_product_roundtrip':True,'_lzma':importlib.import_module('_lzma').__file__,'software_variant'")
(E/'liblzma_runtime_profile_r1.py').open('x').write(s)
