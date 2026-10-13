import json,hashlib,collections
from pathlib import Path
O=Path('local-preservation/github-main-sync-20261013-r1');S=O/'send-tree';rows=json.loads((O/'SEND_MANIFEST_R3.json').read_text());m={r['path']:r for r in rows};conflicts=[]
for label,prefix in [('Windows R19','continuation/candidates/windows-r19/candidate/'),('W23 R14','continuation/candidates/w23-r14/'),('W24 coupled05','continuation/candidates/w24-coupled05/repository/')]:
 for key,r in m.items():
  if key.startswith(prefix):
   rel=key[len(prefix):]
   if rel in m and m[rel]['sha256']!=r['sha256'] and rel.startswith(('comsol_mcp/','tools/','tests/')):conflicts.append({'candidate':label,'path':rel,'base_sha256':m[rel]['sha256'],'candidate_sha256':r['sha256'],'integration':'ROOT_DECISION_REQUIRED_NO_OVERLAY'})
(S/'continuation').mkdir(exist_ok=True)
(S/'continuation/INTEGRATION_CONFLICTS.json').write_text(json.dumps(conflicts,indent=2)+'\n')
for name in ['EXTERNAL_SOURCE_MAP.json','MISSING_SOURCE_REFERENCES.json','SECRET_PATTERN_SCAN.json','LOCAL_ONLY_MANIFEST_R2.json']: (S/'continuation'/name).write_bytes((O/name).read_bytes())
(S/'continuation/README.md').write_text('''# Continuation checkpoint — source, not final acceptance

The tree root is the complete current 0.1.9 production checkout. It is not a new 0.1.10 integrated release. Independent candidate source is preserved under `candidates/`; never overlay these trees automatically. See INTEGRATION_CONFLICTS.json for byte differences needing integration review.

- Windows R19: complete candidate source and software evidence; native Windows acceptance remains separate.
- W23 R14: full candidate and final6 packet, CHANGES_REQUIRED (shared-measure regression and approval021/metadata022 mismatch). Activity022 NOT_RUN. A final R15 may be added before publication.
- W24 coupled05: independent candidate, no new scientific execution in this checkpoint.
- W26 offline dependency-venv unit R1: Root source-only PASS; independent review pending; target venv/pip/runtime execution NOT_RUN. Packaging is 26.3 from the actual lock. Current runtime observation mismatch remains UNFIXED.
- Successful dependency runtime evidence is limited to ARM TLS/hashlib/lzma/tar.xz and actual fat bytes; Intel hardware and complete three-architecture delivery remain OPEN. gdbm/Tk failures are preserved.

## Source reconstruction on another machine

1. Use the root checkout for ordinary package/test/build entrypoints; Java source is included, commercial COMSOL/JAR binaries are not.
2. Choose a single candidate and compare its full source against the root; resolve listed conflicts explicitly. Candidate native launchers contain immutable historical local paths and guards. Do not run them unchanged or replay an old once invocation.
3. `EXTERNAL_SOURCE_MAP.json` maps every additionally available frozen source to included exact bytes in external-source/. Frozen originals are unchanged. Administrative wrappers and their imported helpers are included; reconstruct fresh private paths using these sources, then regenerate context/freezes and obtain a new source/once review. Never reuse the published approval hashes as authority for a fresh native action.
4. Reacquire fixed official inputs using included exact acquisition manifests, checksums/signature fingerprints and offline locks. LOCAL_ONLY_MANIFEST_R2.json identifies original large inputs/artifacts by SHA/size; these are not embedded. Actual native checkpoint inventories are historical evidence, not substitutes for a new machine's owner/runtime identity.
5. MISSING_SOURCE_REFERENCES.json lists old deleted temporary paths and some historical command strings. They are not claimed recoverable. Current production source and current candidates are present in full; historical native proof remains local when indexed only.

Large MPH, commercial libraries, wheels, runtime trees, caches, raw private ledgers and broad logs remain local. Compact failure/UNKNOWN receipts and source hashes are retained. No source patch erases the original failures. No COMSOL/JVM/native test was started while preparing this publication.

The main Agent supplies the final canonical PROGRESS/NEXT/RESUME pages and approved publish commit. This preparation did not push to GitHub or modify the original .git.
''')
verification=[]
for r in rows:
 raw=(S/r['path']).read_bytes()
 if len(raw)!=r['bytes'] or hashlib.sha256(raw).hexdigest()!=r['sha256']:raise RuntimeError(r['path'])
 verification.append(r)
(O/'CONTENT_VERIFICATION.json').write_text(json.dumps({'status':'PASS_EXACT_INCLUDED_BYTES','files':len(rows),'bytes':sum(r['bytes'] for r in rows),'conflicts':len(conflicts),'native_effects':0,'push':0},indent=2)+'\n')
print('verified',len(rows),'bytes',sum(r['bytes'] for r in rows),'conflicts',len(conflicts))
