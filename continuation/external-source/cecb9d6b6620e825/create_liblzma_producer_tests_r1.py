from pathlib import Path
E=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository/docs/full_project_continuation_v2/evidence/intel-builder-execution-r1')
s=(E/'test_openssl359_runtime_producer_r2.py').read_text().replace('openssl359_runtime_profile_r3 as producer','liblzma_runtime_profile_r1 as producer').replace('openssl359_runtime_verify_r2 as verifier','liblzma_verify_r1 as verifier').replace('openssl359_runtime_successor_r2 as actor','liblzma_successor_r1 as actor\nfrom openssl359_runtime_successor_r2 import INSTALLED as ORIGINAL')
s=s.replace("(actor.INSTALLED/'cpython-input", "(ORIGINAL/'cpython-input").replace("'tls_cert_checks_r4.py'","'liblzma_cert_checks_r1.py'").replace("actor.INSTALLED/'cpython/bin/python3.12'","actor.layout()['install']/'bin/python3.12'").replace("actor.ROOT.name=='intel-tls-openssl359-runtime-r2'","actor.layout()['root'].name=='intel-liblzma-r1'")
s=s.replace("    monkeypatch.setattr(verifier,'ROOT',output)","    import _lzma\n    monkeypatch.setattr(_lzma,'__file__',str(prefix/'lib/python3.12/lib-dynload/_lzma.cpython-312-darwin.so'))\n    monkeypatch.setattr(verifier,'ROOT',output)")
s=s.replace("    assert result['sha256_known_vector']", "    assert result['actual_lzma_roundtrip'] and result['actual_tar_xz_product_roundtrip']\n    assert result['sha256_known_vector']")
s=s.replace("if name=='zlib'", "if name=='_lzma'").replace("match='regressed: zlib'","match='regressed: _lzma'")
start=s.index('def test_minimal_scope_namespace_and_original_recipe():');s=s[:start]
(E/'test_liblzma_producer_r1.py').open('x').write(s)
