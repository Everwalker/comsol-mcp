"""Production complete-list/copy controls; no official unpack or native build."""
import json,os
from pathlib import Path
import pytest
from acquire_exact_archives import Refusal
import liblzma_prepare_r2 as source
import liblzma_successor_r2 as actor
def ordering_fixture(tmp_path):
    (tmp_path/'api/lzma').mkdir(parents=True,mode=0o700);(tmp_path/'api').chmod(0o700)
    for path in (tmp_path/'api/lzma.h',tmp_path/'api/lzma/base.h'):
        path.write_bytes(b'public member bytes\n');path.chmod(0o600)
    rows=source.deps.tree_record(tmp_path)
    projection=[{k:r[k] for k in ('relative_path','kind','mode','bytes','sha256') if k in r} for r in rows]
    expected=sorted(projection,key=lambda row:row['relative_path'])
    assert projection!=expected
    return {'rows':expected}
def test_actual_path_vs_whole_string_order_positive_no_member_filter(tmp_path):
    expected=ordering_fixture(tmp_path);rows=source.extracted_content_matches(tmp_path,expected)
    assert len(rows)==len(expected['rows'])==4
@pytest.mark.parametrize('change',('bytes','mode','added_member','missing_member','duplicate_extra','duplicate_replacement','kind'))
def test_complete_fields_and_duplicate_membership_remain_rejected(tmp_path,change):
    expected=ordering_fixture(tmp_path)
    if change=='bytes':(tmp_path/'api/lzma.h').write_bytes(b'public other bytes!\n')
    elif change=='mode':(tmp_path/'api/lzma.h').chmod(0o644)
    elif change=='added_member':(tmp_path/'api/public-extra').write_bytes(b'extra')
    elif change=='missing_member':(tmp_path/'api/lzma.h').unlink()
    elif change=='duplicate_extra':expected['rows'].append(dict(expected['rows'][0]))
    elif change=='duplicate_replacement':expected['rows'][1]=dict(expected['rows'][0])
    elif change=='kind':expected['rows'][0]=dict(expected['rows'][0],kind='regular')
    with pytest.raises(Refusal,match='complete content/mode'):source.extracted_content_matches(tmp_path,expected)
def test_actual_original704_production_verified_copier_positive(tmp_path):
    inputs=source.fixed.verify_inputs_and_scan();original=source.verify_failed_r1()
    result=source.copy_verified_pristine_xz(tmp_path/'fresh-xz-input',inputs)
    assert result['new_XZ_official_unpack']==0 and result['original_complete_XZ704_exact_copy']
    assert len(source.extracted_content_matches(tmp_path/'fresh-xz-input',inputs['inventory']))==704
    assert source.verify_failed_r1()==original
def test_foreign_or_replaced_first_source_record_copier_rejects(monkeypatch,tmp_path):
    inputs=source.fixed.verify_inputs_and_scan();original=source.verify_failed_r1
    def changed_first_records():
        rows=original();rows=[dict(row) for row in rows]
        item=next(row for row in rows if row['relative_path']=='xz-input/xz-5.8.4/src/liblzma/api/lzma.h');item['identity']=dict(item['identity'],inode=item['identity']['inode']+1)
        return rows
    monkeypatch.setattr(source,'verify_failed_r1',changed_first_records)
    with pytest.raises(Exception):source.copy_verified_pristine_xz(tmp_path/'fresh-xz-input',inputs)
def test_new_namespace_all13_pointer_variants_and_preserved_provider_scope():
    import liblzma_merge_r2 as merge
    import liblzma_acceptance_r2 as accept
    import liblzma_runtime_profile_r2 as profile
    import liblzma_verify_r2 as verify
    import liblzma_cpython_checkpoint_r2 as cp
    assert actor.layout()['root'].name=='intel-liblzma-r2'
    assert profile.INSTALLED==verify.INSTALLED==actor.layout()['root'] and profile.OUTPUT_ROOT==verify.ROOT==actor.layout()['root']
    for module in (actor,source,merge,accept,profile,verify,cp):
        text=Path(module.__file__).read_text()
        if module is not source:assert "'intel-liblzma-r1'" not in text
    plan,_=actor.command_plan(actor.layout());assert len(plan)==13
    assert actor.UNIT_CAP==1800 and actor.RESERVE==300 and actor.CAPS['runtime']==180 and actor.CAPS['prepare']==600
    assert 'liblzma_merge_r2.py' in Path(accept.__file__).read_text()
    assert 'liblzma_runtime_profile_r2.py' in Path(actor.__file__).read_text() and 'liblzma_verify_r2.py' in Path(actor.__file__).read_text()
    profile.validate_provider((3,5,0,9,0),0x30500090,'OpenSSL 3.5.9 29 Sep 2026')
