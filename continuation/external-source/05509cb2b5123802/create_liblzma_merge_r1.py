from pathlib import Path
E=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository/docs/full_project_continuation_v2/evidence/intel-builder-execution-r1')
s=(E/'openssl359_merge_r2.py').read_text().replace("intel-tls-openssl359-r2","intel-liblzma-r1").replace("('libssl.a','libcrypto.a')","('liblzma.a',)").replace("('openssl-arm64' if arch=='arm64' else 'openssl-x86_64')","('lzma-arm64' if arch=='arm64' else 'lzma-x86_64')")
(E/'liblzma_merge_r1.py').open('x').write(s)
