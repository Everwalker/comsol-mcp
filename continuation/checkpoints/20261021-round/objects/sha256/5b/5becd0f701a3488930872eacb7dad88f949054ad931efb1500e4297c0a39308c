#!/usr/bin/env python3
"""Evidence-structure/hash auditor. NEVER certifies Windows or COMSOL execution.
Rejects missing evidence, unsupported claimed levels, synthetic native inputs,
and reports with no captured MCP request/response pairs. Real execution still
requires source review and native testing; a journal is not remote attestation.
"""
from __future__ import annotations
import argparse,hashlib,json,re
from pathlib import Path,PurePosixPath
NATIVE_MCP={'INSTALL_PUBLIC_MCP','PUBLIC_MCP_NATIVE','NATIVE_CONVERGENCE','PUBLIC_MCP_NATIVE_CONTROL'}
FORBIDDEN_NATIVE_ORIGINS={'SYNTHETIC','SYNTHETIC_FIXTURE','CALLER_SUPPLIED','EXAMPLE','SIMULATED','NOT_RUN'}
OK='EVIDENCE_STRUCTURE_AND_HASHES_OK_NOT_NATIVE_CERTIFICATION'

def strict_json(text):
    def reject(v):raise ValueError('non-finite JSON constant: '+v)
    return json.loads(text,parse_constant=reject)

def evidence_path(root:Path,name:str)->Path:
    if not isinstance(name,str) or not name or '\\' in name or ':' in name or '\x00' in name:raise ValueError('unsafe evidence path')
    rel=PurePosixPath(name)
    if rel.is_absolute() or any(x in ('','.','..') for x in name.split('/')):raise ValueError('unsafe evidence path')
    p=root
    for part in rel.parts:
        p=p/part
        if p.is_symlink():raise ValueError('symlink evidence rejected')
        if p.exists() and getattr(p.stat(follow_symlinks=False),'st_file_attributes',0)&0x400:raise ValueError('reparse evidence rejected')
    p.resolve().relative_to(root)
    if not p.is_file():raise ValueError('evidence file missing')
    return p

def hash_file(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()

def validate_report(definition:dict,report:dict,root:Path)->dict:
    errors=[];pending=[];root=root.resolve();rows={};required=set()
    identity=report.get('source_identity',{})
    for field in ('commit','tree'):
        if not re.fullmatch('[0-9a-f]{40}',str(identity.get(field,''))):errors.append('invalid source '+field)
    for row in report.get('records',[]):
        key=(row.get('id'),row.get('target'))
        if key in rows:errors.append('duplicate '+str(key))
        rows[key]=row
    for case in definition['cases']:
        for target in (('win63','win64') if case['targets']=='BOTH' else ('shared',)):
            key=(case['id'],target);required.add(key);tag=str(key);row=rows.get(key)
            if row is None:pending.append(tag+' missing');continue
            status=row.get('test_status')
            if status!='PASS':pending.append(tag+' '+str(status));continue
            if row.get('evidence_level')!=case['required_evidence']:errors.append(tag+' evidence level mismatch')
            if not isinstance(row.get('run_id'),str) or not row['run_id']:errors.append(tag+' run_id missing')
            if row.get('source_commit')!=identity.get('commit'):errors.append(tag+' execution source mismatch')
            checks=row.get('checks',[])
            if not checks or any(not isinstance(c,dict) or c.get('passed') is not True for c in checks):errors.append(tag+' no fully passing assertion records')
            for field in ('expected','observed'):
                if row.get(field) in (None,{},[]):errors.append(tag+' empty '+field)
            roles={};artifacts=row.get('artifacts',[])
            if not isinstance(artifacts,list) or not artifacts:errors.append(tag+' no evidence artifacts');continue
            for artifact in artifacts:
                try:
                    path=evidence_path(root,artifact['path'])
                    digest=artifact.get('sha256','')
                    if not re.fullmatch('[0-9a-f]{64}',digest) or hash_file(path)!=digest:raise ValueError('hash mismatch')
                    role=artifact.get('role')
                    if not role:raise ValueError('artifact role missing')
                    roles.setdefault(role,[]).append(path)
                except Exception as e:errors.append(tag+' '+str(e))
            if case['required_evidence'] in NATIVE_MCP:
                if row.get('production_entrypoint') is not True:errors.append(tag+' public entrypoint not established')
                if row.get('observation_origin') not in {'ENGINE_EVALUATION','VERIFIED_ENGINE_ARTIFACT'}:errors.append(tag+' native observations have invalid origin')
                ctx=row.get('native_context') or {}
                for field in ('runtime_build','worker_instance_id','model_ref'):
                    if not ctx.get(field):errors.append(tag+' native context missing '+field)
                version='6.3' if target=='win63' else '6.4'
                if version not in str(ctx.get('runtime_build','')):errors.append(tag+' target/runtime mismatch')
                for role in ('mcp_transcript','runtime_identity','source_manifest','observations','checks'):
                    if role not in roles:errors.append(tag+' missing artifact role '+role)
                for p in roles.get('observations',[]):
                    try:
                        obs=strict_json(p.read_text(encoding='utf-8'))
                        if obs.get('origin') not in {'ENGINE_EVALUATION','VERIFIED_ENGINE_ARTIFACT'}:raise ValueError('synthetic/untrusted observation origin')
                        if obs.get('run_id')!=row.get('run_id') or obs.get('target')!=target:raise ValueError('observation run/target mismatch')
                        if obs.get('source_commit')!=row.get('source_commit'):raise ValueError('observation source mismatch')
                        if not obs.get('records'):raise ValueError('no actual observation records')
                    except Exception as e:errors.append(tag+' observations '+str(e))
                for p in roles.get('mcp_transcript',[]):
                    try:
                        trace=strict_json(p.read_text(encoding='utf-8'))
                        if trace.get('run_id')!=row.get('run_id'):raise ValueError('transcript run mismatch')
                        requests={str(e.get('request_id')) for e in trace.get('events',[]) if e.get('direction')=='request' and e.get('method')=='tools/call' and e.get('payload') is not None and e.get('request_id') is not None}
                        replies={str(e.get('request_id')) for e in trace.get('events',[]) if e.get('direction')=='response' and e.get('payload') is not None and e.get('request_id') is not None}
                        if not requests.intersection(replies):raise ValueError('no matched captured tools/call request and response')
                    except Exception as e:errors.append(tag+' transcript '+str(e))
    for extra in rows.keys()-required:errors.append('unexpected '+str(extra))
    return {'status':'INVALID_EVIDENCE_REPORT' if errors else 'INCOMPLETE' if pending else OK,'errors':errors,'pending':pending,'target_records':len(required),'native_execution_certified':False,'physical_correctness_certified':False,'note':'Structure/hash/provenance fields only. Review actual runner and raw execution. A fabricated journal cannot be ruled out by this auditor.'}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--definitions',type=Path,required=True);p.add_argument('--report',type=Path,required=True);p.add_argument('--evidence-root',type=Path,required=True);a=p.parse_args()
    try:r=validate_report(strict_json(a.definitions.read_text(encoding='utf-8')),strict_json(a.report.read_text(encoding='utf-8')),a.evidence_root)
    except Exception as e:r={'status':'INVALID_EVIDENCE_REPORT','errors':[str(e)],'native_execution_certified':False}
    print(json.dumps(r,ensure_ascii=False,indent=2,allow_nan=False));return 0 if r['status']==OK else 2
if __name__=='__main__':raise SystemExit(main())
