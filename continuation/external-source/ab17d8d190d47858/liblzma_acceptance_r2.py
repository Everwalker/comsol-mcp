"""Exact thin/static/fat library and shared public header acceptance."""
import json,time
from pathlib import Path
from acquire_exact_archives import Refusal
from intel_crypto_successor_r1 import checkpoint_bytes
from intel_deps_prepare import exclusive_json
import intel_deps_stages as deps
import openssl359_cpython_successor_r4 as prior
from tls_binary_r1 import static_archive,fat_archive
def prepare_merge(x,budget,stage_start):
    headers=prior.compare_headers(x['lzma_arm64']/'include',x['lzma_x86_64']/'include')
    # New liblzma headers have no overlap with the accepted OpenSSL directory.
    rows=headers['arm'];names={r['relative_path'] for r in rows}
    if 'lzma.h' not in names or 'lzma/version.h' not in names or any(p!='lzma' and p!='lzma.h' and not p.startswith('lzma/') for p in names):raise Refusal('only actual liblzma public headers accepted')
    archives={'liblzma.a':{}};parsed={}
    for arch in ('arm64','x86_64'):
        path=x['lzma_'+arch]/'lib/liblzma.a';row=prior.record(path);raw,_=checkpoint_bytes(path,row)
        archives['liblzma.a'][arch]=row;parsed[arch]=static_archive(raw,arch)
    exclusive_json(x['root']/'MERGE_INPUTS.json',{'archives':archives,'headers_equal':headers,'thin_actual_objects':parsed})
    pin=prior.record(x['root']/'MERGE_INPUTS.json');exclusive_json(x['root']/'MERGE_INPUT_FIRST_RECORD.json',pin)
    remaining=budget.wait_remaining('merge',stage_start);deadline=time.monotonic()+remaining
    prior.exclusive_text(x['root']/'Merge.mk','merge:\n\t'+prior.HOST_PYTHON+' -B '+str(prior.HERE/'liblzma_merge_r2.py')+' --root '+str(x['root'])+' --checkpoint '+str(x['root']/'MERGE_INPUTS.json')+' --checkpoint-sha '+pin['sha256']+' --deadline '+str(deadline)+'\n')
    # copy_exact_tree creates an entire target; copy these exact header members
    # into a separate fresh input directory, then move only the two fixed names.
    private=x['root']/'lzma-accepted-header-copy';prior.copy_headers(x['lzma_arm64']/'include',private,rows)
    for name in ('lzma','lzma.h'):
        target=x['fat']/'include'/name
        if target.exists() or target.is_symlink():raise Refusal('fresh fixed liblzma header target exists')
        (private/name).rename(target)
    hp=x['root']/'LZMA_HEADERS_FIRST_RECORDS.json'
    exclusive_json(hp,[r for r in deps.tree_record(x['fat']/'include') if r['relative_path'] in ('lzma','lzma.h') or r['relative_path'].startswith('lzma/')])
    return json.dumps({'input':pin,'headers':prior.record(hp)},sort_keys=True).encode()
def accept_merged(x,original_inputs):
    if not isinstance(original_inputs,bytes):raise Refusal('original actor merge inputs missing')
    pins=json.loads(original_inputs);pins['output']=prior.record(x['root']/'MERGE_OUTPUT_VERIFIED.json')
    accepted=json.dumps(pins,sort_keys=True).encode();verify_merged(x,accepted);return accepted
def verify_merged(x,accepted):
    if not isinstance(accepted,bytes):raise Refusal('immutable original actor merge pins missing')
    pins=json.loads(accepted);pin=pins['input']
    if pin['path']!=str(x['root']/'MERGE_INPUTS.json'):raise Refusal('original merge first-record path differs')
    inputs=json.loads(checkpoint_bytes(x['root']/'MERGE_INPUTS.json',pin)[0])
    out_pin=pins['output'];out=json.loads(checkpoint_bytes(x['root']/'MERGE_OUTPUT_VERIFIED.json',out_pin)[0])
    if len(out['archives'])!=1 or out['archives'][0]['name']!='liblzma.a':raise Refusal('one actual liblzma fat archive required')
    entry=out['archives'][0];child=entry['actual_command']
    expected=['/usr/bin/lipo','-create',str(x['lzma_arm64']/'lib/liblzma.a'),str(x['lzma_x86_64']/'lib/liblzma.a'),'-output',str(x['fat']/'lib/liblzma.a')]
    if child['request']['argv']!=expected or child['actual_wait_rc']!=0 or not child['terminal_wait_observed'] or child['diagnostic_errors']:raise Refusal('actual original lipo command/terminal differs')
    raw={}
    for arch in ('arm64','x86_64'):raw[arch]=checkpoint_bytes(x['lzma_'+arch]/'lib/liblzma.a',inputs['archives']['liblzma.a'][arch])[0]
    final=checkpoint_bytes(x['fat']/'lib/liblzma.a',entry['output'])[0]
    if fat_archive(final,raw)!=entry['binary']:raise Refusal('actual lzma fat object/slice bytes differ')
    headers=json.loads(checkpoint_bytes(x['root']/'LZMA_HEADERS_FIRST_RECORDS.json',pins['headers'])[0])
    if [r for r in deps.tree_record(x['fat']/'include') if r['relative_path'] in ('lzma','lzma.h') or r['relative_path'].startswith('lzma/')]!=headers:raise Refusal('accepted liblzma header bytes/identity changed')
    return {'actual_library_two_slices_exact_and_all_objects_min11':True,'actual_shared_headers_unchanged':True}
