import pathlib,json,tarfile,hashlib
base=pathlib.Path('/Volumes/SSD/Comsol-MCP/tmp-migration-20261005-001')
out=pathlib.Path('/private/tmp/comsol-v2-p0-sources-20261007'); out.mkdir(exist_ok=False)
sets={'parameter-io-candidate002-v002':['comsol_mcp/_artifact_store.py','comsol_mcp/_g2_registry.py','comsol_mcp/_g3_ops.py','comsol_mcp/_g3_parameter_io.py','comsol_mcp/_managed_backend.py','tests/test_artifact_registration_import.py'],'artifact-preview-candidate002':['comsol_mcp/_control_daemon.py','comsol_mcp/_g2_artifact_reads.py','comsol_mcp/_g2_registry.py','comsol_mcp/_artifact_preview_io.py','comsol_mcp/_g2_artifact_preview.py','tests/test_artifact_preview.py','tests/test_artifact_preview_repair.py']}
receipt=[]
for sid,allow in sets.items():
 arc=base/'archives'/f'{sid}.tar'; expected=(arc.with_suffix('.tar.sha256')).read_text().split()[0]
 h=hashlib.sha256()
 with arc.open('rb') as f:
  for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
 assert h.hexdigest()==expected
 inv=json.loads((base/'inventory'/f'{sid}.pre.json').read_text()); lookup={r['path']:r for r in inv['entries']}
 with tarfile.open(arc,'r:') as t:
  for rel in allow:
   matches=[m for m in t.getmembers() if m.name.endswith('/repository/'+rel) or m.name.endswith('/'+rel)]
   matches=[m for m in matches if not any(p.startswith('._') for p in pathlib.PurePosixPath(m.name).parts)]
   assert len(matches)==1,(sid,rel,len(matches));m=matches[0]
   assert m.isfile() and not m.issym() and '..' not in pathlib.PurePosixPath(m.name).parts and not m.name.startswith('/')
   data=t.extractfile(m).read();sha=hashlib.sha256(data).hexdigest();assert sha==lookup[m.name]['sha256'],m.name
   dest=out/sid/rel;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data)
   receipt.append({'archive':sid,'archive_sha256':expected,'member':m.name,'path':str(dest),'sha256':sha})
pathlib.Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/overlay/evidence/p0_integration/source_restore.json').write_text(json.dumps(receipt,indent=2))
print(out)
