"""Synthetic fresh closure tests against actual same-FD/DB/native-origin gates."""
import copy
import hashlib
import json
from pathlib import Path
from uuid import uuid4
import pytest
from tests.w24_attachment_fixture import attachment_case
from tools import native_runtime_binding as binding
from tools import w24_setup_input_attachment as attachment
from tools import run_native_w24_static_shape_setup_campaign as campaign

@pytest.fixture
def fresh_case(attachment_case,monkeypatch):
    manifest=json.loads(attachment_case['manifest'].read_text())
    scope=attachment.WORK;scope.chmod(0o700)
    repo=Path(__file__).resolve().parents[1]
    observed={'python':{'entry':'/synthetic/python','site_packages':'/synthetic/site'},'comsol':{'root':'/synthetic/comsol'},'jdk':{'home':'/synthetic/jdk'}}
    monkeypatch.setattr(binding,'observe',lambda *a,**k:copy.deepcopy(observed))
    packet=binding.create(repo=repo,authority=scope/'fresh-authority.json',activity_id=str(uuid4()),python=Path('/synthetic/python'),packages=Path('/synthetic/site'),install=Path('/synthetic/comsol'),jdk=Path('/synthetic/jdk'),budget=campaign.SETUP_BUDGET)
    review_path=Path(manifest['roles']['root_review'])
    review=json.loads(review_path.read_text())
    review={'schema':'FRESH_W24_ROOT_INPUT_REVIEW_V1','activity_id':packet['activity_id'],'source_manifest_sha256':packet['source']['sha256'],'verified_references':review['verified_references']}
    review_path.write_text(json.dumps(review)); sha=hashlib.sha256(review_path.read_bytes()).hexdigest()
    for row in manifest['files']:
        if row['path']==str(review_path):row.update(bytes=review_path.stat().st_size,sha256=sha)
    manifest.update(schema=attachment.FRESH_SCHEMA,activity_id=packet['activity_id'],source_manifest_sha256=packet['source']['sha256'])
    path=scope/'fresh-manifest.json';path.write_text(json.dumps(manifest))
    return path,manifest,{'runtime_binding':packet,'repo':repo,'budget':campaign.SETUP_BUDGET,'root_review_path':review_path,'root_review_sha256':sha},attachment_case['receipt']

def test_fresh_production_origin_closure_positive_is_only_synthetic(fresh_case):
    path,manifest,kwargs,receipt=fresh_case
    result=attachment.validate_fresh_attachment(path,receipt,**kwargs)
    assert result['verified_file_count']==len(manifest['files'])
    assert result['comparison']['matches'] is True
    assert 'FRESH_EPOCH_REQUIRED' in result['historical_refs']
    with pytest.raises(attachment.AttachmentError):attachment.validate_attachment(path,receipt)

@pytest.mark.parametrize('fault',['oldmode','activity','source','reviewhash','reviewpath','missingraw','duplicate','outside','extra','dropfield'])
def test_fresh_closed_schema_rejects_mixing_tampering_or_self_trust(fresh_case,fault):
    path,manifest,kwargs,receipt=fresh_case
    if fault=='oldmode':manifest['schema']=attachment.SCHEMA
    elif fault=='activity':manifest['activity_id']=str(uuid4())
    elif fault=='source':manifest['source_manifest_sha256']='0'*64
    elif fault=='reviewhash':kwargs['root_review_sha256']='0'*64
    elif fault=='reviewpath':kwargs['root_review_path']=Path(manifest['roles']['build_readback'])
    elif fault=='missingraw':manifest['files'].pop(0)
    elif fault=='duplicate':manifest['files'].append(copy.deepcopy(manifest['files'][0]))
    elif fault=='outside':manifest['files'][0]['path']='/etc/hosts'
    elif fault=='extra':manifest['claim_native_acceptance']=True
    elif fault=='dropfield':manifest.pop('source_manifest_sha256')
    path.write_text(json.dumps(manifest))
    with pytest.raises((attachment.AttachmentError,KeyError)):
        attachment.validate_fresh_attachment(path,receipt,**kwargs)
