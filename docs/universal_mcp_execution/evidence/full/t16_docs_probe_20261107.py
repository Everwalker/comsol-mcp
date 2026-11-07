"""T16/U03 offline evidence probe: real COMSOL 6.4 help <-> public-API introspection.

Runs against the locally installed COMSOL 6.4 documentation only.
Scope: in-process software verification against real local official help.
It bypasses the MCP stdio gateway and the control daemon, so it is NOT a
public MCP end-to-end check (see T16-qwen-20261107.md, public-route section).
No COMSOL process, Worker, SSH, or prior task resource is touched.
Only short quoted fragments are copied out of the documentation; the index
database stays in /private/tmp and no commercial documentation is committed.

Run from the workspace root with the focused-continuation venv:
  .g3-private/full-continuation-20261107/venv/bin/python \\
      docs/universal_mcp_execution/evidence/full/t16_docs_probe_20261107.py
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile

from comsol_mcp import _g2_public_api as api
from comsol_mcp._g2_docs import OfflineDocsIndex, infer_version

HELP_BASE = Path('/Applications/COMSOL64/Multiphysics/doc/help/wtpwebapps'
                 '/ROOT/doc/com.comsol.help.comsol')
TOPIC_ROOT = HELP_BASE  # official help topics; the api/ javadoc subtree is nested inside


def section(name: str) -> None:
    print()
    print(f'== {name} ==')


def small(value: str, limit: int = 300) -> str:
    flat = ' '.join(value.split())
    return flat[:limit] + ('...' if len(flat) > limit else '')


section('A. post-fix install-marker inference')
for value in ['COMSOL64', 'COMSOL63', '/Applications/COMSOL64/Multiphysics/doc',
              'COMSOL 6.4', 'COMSOL640', 'C:\\Program Files\\COMSOL\\COMSOL64\\Multiphysics\\doc']:
    print('infer_version(%r) ->' % value, infer_version(value, 'unknown'))

tmp = Path(tempfile.mkdtemp(prefix='t16-isolation-'))
for marker, body in (('COMSOL64', 'ModelEntity current'), ('COMSOL63', 'ModelEntity legacy')):
    tree = tmp / 'Applications' / marker / 'Multiphysics' / 'doc'
    tree.mkdir(parents=True)
    (tree / 'model.txt').write_text(body)
split = OfflineDocsIndex(tmp / 'idx.sqlite3', allowed_roots=[tmp])
out_split = split.index(runtime_id='COMSOL64', sources=[str(tmp / 'Applications')])
print('mixed-tree index(runtime_id=COMSOL64) version=', out_split['version'])
print('  6.4-current  ->', split.search(query='current', version='6.4')['status'])
print('  6.3-legacy   ->', split.search(query='legacy', version='6.3')['status'])
print('  6.3-current  ->', split.search(query='current', version='6.3')['status'])
print('  6.4-legacy   ->', split.search(query='legacy', version='6.4')['status'])
split.close()

section('B. index the real 6.4 official help tree')
work = Path('/private/tmp/t16-docs-20261107')
work.mkdir(exist_ok=True)
index = OfflineDocsIndex(work / 'docs_index.sqlite3', allowed_roots=[HELP_BASE])
result = index.index(runtime_id='COMSOL64', sources=[str(TOPIC_ROOT)])
print('index status=', result['status'], 'version=', result['version'],
      'indexed_chunks=', result['indexed_count'], 'skipped=', len(result['skipped']))
for row in result['skipped']:
    print('  skipped:', small(str(row), 160))

section('C. introspection -> docs join (pinned capability doc)')
block = api._documentation(api.MODEL_ENTITY)
print('api.describe documentation block:', json.dumps(block, sort_keys=True))
hit = index.search(query='ModelEntity docMarker', version='6.4', limit=3)
print('docs.search(ModelEntity docMarker, 6.4) status=', hit['status'])
rows = hit['results'][:3]
for row in rows:
    print('  hit source=', row['source'].replace(str(HELP_BASE), '<help>'),
          'sha_match_pinned=', row['source_sha256'] == block['sha256'])
pinned = [row for row in hit['results'] if row['source'].endswith(block['path'])
          and row['source_sha256'] == block['sha256']]
print('rows whose source path AND sha256 equal the introspection block:', len(pinned) > 0)
top = pinned[0]

section('D. unfamiliar-but-legal API with no wrapper: ModelEntity.docMarker')
print('in reviewed capability rows:',
      any(row.method == 'docMarker' for row in api._CAPABILITY_ROWS))
for wanted in ('docMarker(java.lang.String', 'docMarker'):
    frag = index.get(document_ref=top['document_ref'], section=wanted, length=700)
    print('docs.get(section=%r) status=' % wanted, frag['status'])
    if frag['status'] == 'SUCCEEDED':
        print('fragment:', small(frag.get('content', '')))
        break
wire = api._method_wire(
    {'interface': api.MODEL_ENTITY, 'method': 'docMarker',
     'parameters': ['java.lang.String'], 'returns': 'java.lang.String'},
    {'runtime_version': '6.4.0.293', 'metadata_complete': True},
)
print('api.describe wire for the same signature:')
print(json.dumps({'policy_status': wire['policy_status'], 'invoke_allowed': wire['invoke_allowed'],
                  'controlled_alternative': wire['controlled_alternative']}, sort_keys=True))

section('E. second legal API with arguments: ModelUtil.load(String,String)')
load = index.search(query='ModelUtil load modelLocation', version='6.4', limit=3)
print('docs.search status=', load['status'])
for row in load['results']:
    print('  ', row['source'].replace(str(HELP_BASE), '<help>'), 'title=', row['title'])
if load['results']:
    body = index.get(document_ref=load['results'][0]['document_ref'],
                     section='Load a model from a model location', length=500)
    print('docs.get(section=description) status=', body['status'])
    print('fragment:', small(body.get('content', ''), 420))

section('F. error/diagnostic route (real content, AND-token verification)')
err = index.search(query='file cannot be read', version='6.4', node_type='ModelUtil', limit=2)
print('docs.error_search("file cannot be read", node_type=ModelUtil) status=', err['status'])
for row in err['results']:
    print('  ', row['source'].replace(str(HELP_BASE), '<help>'))
    print('   phrase present in snippet:', 'file cannot be read' in row['snippet'].lower())
    print('   snippet:', small(row['snippet'], 220))
if err['results']:
    proof = index.get(document_ref=err['results'][0]['document_ref'],
                      section='file cannot be read', length=300)
    print('docs.get(section=phrase) status=', proof['status'])
    print('   phrase context:', small(proof.get('content', ''), 240))

section('G. examples route and honest limits')
examples = index.search(query='Application Builder example tutorial', version='6.4', limit=3)
print('docs.examples(Application Builder, 6.4) status=', examples['status'])
for row in examples['results']:
    print('  ', small(row['title'], 80))

section('H. truthful absence')
print('fabricated feature  ->', index.search(query='comsolHyperSmoothFeature', version='6.4')['status'])
print('unindexed 6.3 slice ->', index.search(query='ModelEntity', version='6.3')['status'])
print('bogus document_ref  ->', index.get(document_ref='docs:6.4:deadbeef:0')['status'])

section('I. index artifact identity')
digest = hashlib.sha256()
with (work / 'docs_index.sqlite3').open('rb') as stream:
    for chunk in iter(lambda: stream.read(1 << 20), b''):
        digest.update(chunk)
print('database=', work / 'docs_index.sqlite3', 'size_bytes=', (work / 'docs_index.sqlite3').stat().st_size,
      'sha256=', digest.hexdigest())
index.close()
