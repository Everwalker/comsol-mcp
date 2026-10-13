"""Actual production canonical lists and original copier; no native effects."""
import copy,json,os
from pathlib import Path
import pytest
from acquire_exact_archives import Refusal
import liblzma_prepare_r2 as source
O=Path(__file__).parent
def save(name,data):
 fd=os.open(O/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 try:os.write(fd,(json.dumps(data,indent=2)+'\n').encode());os.fsync(fd)
 finally:os.close(fd)
def test_actual_full_list_canonical_sort_preserves_every_field_and_duplicate(tmp_path):
 (tmp_path/'api/lzma').mkdir(parents=True,mode=0o700);(tmp_path/'api').chmod(0o700)
 for path in (tmp_path/'api/lzma.h',tmp_path/'api/lzma/base.h'):path.write_bytes(b'public same bounded bytes\n');path.chmod(0o600)
 first=source.deps.tree_record(tmp_path);project=lambda rows:[{k:r[k] for k in ('relative_path','kind','mode','bytes','sha256') if k in r} for r in rows]
 expected={'rows':sorted(project(first),key=lambda row:row['relative_path'])};assert project(first)!=expected['rows']
 assert source.extracted_content_matches(tmp_path,expected)==first
 reasons=[]
 for mutation in ('bytes','mode','missing','added','duplicate_extra','duplicate_replacement','kind'):
  poisoned=copy.deepcopy(expected)
  if mutation=='bytes':poisoned['rows'][-1]['sha256']='0'*64
  elif mutation=='mode':poisoned['rows'][-1]['mode']=0o644
  elif mutation=='missing':poisoned['rows'].pop()
  elif mutation=='added':poisoned['rows'].append(dict(poisoned['rows'][-1],relative_path='api/public-extra'))
  elif mutation=='duplicate_extra':poisoned['rows'].append(dict(poisoned['rows'][0]))
  elif mutation=='duplicate_replacement':poisoned['rows'][1]=dict(poisoned['rows'][0])
  elif mutation=='kind':poisoned['rows'][0]['kind']='regular'
  with pytest.raises(Refusal,match='complete content/mode') as error:source.extracted_content_matches(tmp_path,poisoned)
  reasons.append({'mutation':mutation,'actual_refusal':str(error.value)})
 save('ACTUAL_COMPLETE_LIST_CANONICAL_SORT_NEGATIVES.json',{'positive_actual_Path_sort_vs_full_string_sort':True,'all4_members_and_full_fields_retained':True,'seven_mutations_rejected':reasons,'mapping_or_duplicate_collapse':False,'native':0})
def test_actual_original704_copy_and_real_samebytes_newinode_refusal(monkeypatch,tmp_path):
 inputs=source.fixed.verify_inputs_and_scan();original=source.verify_failed_r1()
 public_root=tmp_path/'public-original';public_root.mkdir();destination=public_root/'xz-input'
 outcome=source.copy_verified_pristine_xz(destination,inputs)
 assert outcome['new_XZ_official_unpack']==0 and outcome['original_complete_XZ704_exact_copy']
 assert len(source.extracted_content_matches(destination,inputs['inventory']))==704 and source.verify_failed_r1()==original
 # Capture actual synthetic first identities, then swap one owned physical
 # public member. Only the failed-root catalog boundary is a pinned fixture;
 # the real canonical validator and original copy_exact_tree execute unchanged.
 held=source.deps.tree_record(public_root);leaf=destination/'xz-5.8.4/src/liblzma/api/lzma.h';raw=leaf.read_bytes();before=leaf.stat();mode=before.st_mode&0o777
 renamed=leaf.with_name('public-held-original');leaf.rename(renamed);leaf.write_bytes(raw);leaf.chmod(mode);renamed.unlink()
 assert leaf.stat().st_ino!=before.st_ino and leaf.read_bytes()==raw
 with monkeypatch.context() as m:
  m.setattr(source,'FAILED_R1',public_root);m.setattr(source,'verify_failed_r1',lambda:copy.deepcopy(held))
  assert len(source.extracted_content_matches(destination,inputs['inventory']))==704
  refused=tmp_path/'must-remain-uncreated'
  with pytest.raises(Refusal,match='original source identity changed') as error:source.copy_verified_pristine_xz(refused,inputs)
  assert not refused.exists()
 assert source.verify_failed_r1()==original
 save('ACTUAL_PRISTINE_704_COPY_AND_FIRST_INODE_NEGATIVE.json',{'actual_original_R1_full5799_beforeafter':True,'actual_official_auth3_and704_readonly_before_copy':True,'actual_complete_704_production_copier_positive':True,'new_XZ_unpack':0,'negative_boundary':'public physical copied704 source and held first catalog fixture; no originalR1 mutation','same_bytes_new_inode_preserved_full_semantics':True,'actual_original_copy_exact_tree_refusal':str(error.value),'destination_created_on_negative':False,'native_processes_compiler_targetPython':0})
