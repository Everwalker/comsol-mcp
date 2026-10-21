"""Actual store/owned-filesystem publication concurrency; no runtime or engine."""
import hashlib
import json
import os
from pathlib import Path
from threading import Event, Thread, current_thread

import pytest
from comsol_mcp import _artifact_store as artifacts
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._operation_store import OperationStore


def _register(root, store, role='model_recipe'):
    return artifacts.register_project_artifact(root, store, 'inputs/shared.json',
        project_id='project-test', role=role, classification='internal',
        host_identity=artifacts.local_artifact_host_identity(),
        engine_host_identity=artifacts.local_engine_host_identity('127.0.0.1:2036'),
        request_id=current_thread().name, registering_operation_id=current_thread().name,
        artifact_type='application/json', format_version='comsol-mcp-model-recipe/1')


@pytest.mark.parametrize('same_metadata', [True, False])
def test_publication_window_is_single_link_and_stable_for_actual_concurrent_reader(tmp_path, monkeypatch, same_metadata):
    root=tmp_path/'project';(root/'inputs').mkdir(parents=True)
    raw=b'{"synthetic":"owned publication concurrency, not a native recipe"}'
    (root/'inputs/shared.json').write_bytes(raw)
    target=root/'g2_artifacts/registered'/f'{hashlib.sha256(raw).hexdigest()}.json'
    stores=[OperationStore(tmp_path/'store.sqlite3'),OperationStore(tmp_path/'store.sqlite3')]
    published=Event();reader_pinned=Event();cleanup_done=Event();results={};observed={}
    real_link=os.link;real_rename=artifacts._rename_no_clobber
    real_open=artifacts._open_pinned_artifact;real_unlink=Path.unlink
    def after_publish():
        observed['published_nlink']=target.stat().st_nlink
        published.set();assert reader_pinned.wait(5), 'actual sibling reader failed to pin published FD'
    def link(source,destination,*a,**kw):
        result=real_link(source,destination,*a,**kw)
        if Path(destination)==target:after_publish()
        return result
    def rename(source,destination):
        real_rename(source,destination)
        if Path(destination)==target:after_publish()
    def pinned(path):
        handle,before=real_open(path)
        if current_thread().name=='artifact-observer' and Path(path)==target:
            observed['reader_before']={'inode':before.st_ino,'ctime_ns':before.st_ctime_ns,'nlink':before.st_nlink}
            reader_pinned.set()
            assert cleanup_done.wait(5),'publisher cleanup did not complete'
            after=os.fstat(handle.fileno())
            observed['reader_after']={'inode':after.st_ino,'ctime_ns':after.st_ctime_ns,'nlink':after.st_nlink}
        return handle,before
    def unlink(path,*a,**kw):
        result=real_unlink(path,*a,**kw)
        if current_thread().name=='artifact-publisher' and path.parent==target.parent and path.name.endswith('.partial'):
            cleanup_done.set()
        return result
    monkeypatch.setattr(os,'link',link);monkeypatch.setattr(artifacts,'_rename_no_clobber',rename)
    monkeypatch.setattr(artifacts,'_open_pinned_artifact',pinned);monkeypatch.setattr(Path,'unlink',unlink)
    def call(name,index,role):
        try:results[name]=_register(root,stores[index],role)
        except BaseException as e:results[name]={'error_code':getattr(e,'code',type(e).__name__),'error':str(e)}
    publisher=Thread(target=call,args=('publisher',0,'model_recipe'),name='artifact-publisher')
    observer=Thread(target=call,args=('observer',1,'model_recipe' if same_metadata else 'other_role'),name='artifact-observer')
    try:
        publisher.start();assert published.wait(5), results;observer.start();publisher.join(6);observer.join(6)
        assert not publisher.is_alive() and not observer.is_alive()
        (tmp_path/'PUBLISH_WINDOW.json').write_text(json.dumps({'observed':observed,'results':results},default=str,indent=2)+'\n')
        # Before the fix, actual os.link/temp unlink produces nlink2->1 and
        # ctime drift while the observer owns the same opened inode.
        assert observed['published_nlink']==1
        assert observed['reader_before']==observed['reader_after']
        errors=[v['error_code'] for v in results.values() if 'error_code' in v]
        assert errors==([] if same_metadata else ['ARTIFACT_REGISTRATION_CONFLICT'])
        assert target.read_bytes()==raw and target.stat().st_nlink==1
        assert len(stores[0].list_metadata('artifacts'))==1
        if same_metadata:assert sum(v['reused'] for v in results.values())==1
    finally:
        reader_pinned.set();cleanup_done.set()
        for thread in (publisher,observer):
            if thread.ident is not None:thread.join(6)
        for store in stores:store.close()


@pytest.mark.parametrize('competing', ['bytes', 'symlink'])
def test_atomic_registration_does_not_clobber_or_follow_competing_destination(tmp_path, monkeypatch, competing):
    root=tmp_path/'project';(root/'inputs').mkdir(parents=True)
    (root/'inputs/shared.json').write_bytes(b'original owned source')
    store=OperationStore(tmp_path/'store.sqlite3');outside=tmp_path/'other-owned-file';outside.write_bytes(b'untouched competitor')
    real=artifacts._rename_no_clobber;targets=[]
    def race(source,destination):
        targets.append(destination)
        if competing=='bytes':destination.write_bytes(b'untouched competitor')
        else:destination.symlink_to(outside)
        return real(source,destination)
    monkeypatch.setattr(artifacts,'_rename_no_clobber',race)
    try:
        with pytest.raises(ExecutionContractError) as exc:_register(root,store)
        assert exc.value.code==('ARTIFACT_REGISTRATION_CONFLICT' if competing=='bytes' else 'ACCESS_VIOLATION')
        assert outside.read_bytes()==b'untouched competitor'
        assert targets[0].read_bytes()==b'untouched competitor'
        assert bool(targets[0].is_symlink())==(competing=='symlink')
        assert store.list_metadata('artifacts')==[]
        assert list(targets[0].parent.iterdir())==[targets[0]]
    finally:store.close()


def test_atomic_registration_unsupported_publish_never_uses_link_replace_or_placeholder(tmp_path, monkeypatch):
    import errno
    root=tmp_path/'project';(root/'inputs').mkdir(parents=True)
    (root/'inputs/shared.json').write_bytes(b'complete owned source')
    store=OperationStore(tmp_path/'store.sqlite3');targets=[];real_open=os.open
    def unsupported(source,destination):
        targets.append(destination)
        assert source.parent==destination.parent and source.read_bytes()==b'complete owned source'
        raise OSError(errno.ENOTSUP,'synthetic unsupported atomic publication')
    def forbidden(*a,**kw):raise AssertionError('non-atomic publication fallback forbidden')
    def opened(path,flags,*a,**kw):
        if targets and Path(path)==targets[0] and flags & os.O_CREAT:
            raise AssertionError('empty destination placeholder forbidden')
        return real_open(path,flags,*a,**kw)
    monkeypatch.setattr(artifacts,'_rename_no_clobber',unsupported)
    monkeypatch.setattr(os,'link',forbidden);monkeypatch.setattr(os,'replace',forbidden);monkeypatch.setattr(os,'open',opened)
    try:
        with pytest.raises(ExecutionContractError,match='atomic no-clobber') as exc:_register(root,store)
        assert exc.value.code=='ACCESS_VIOLATION'
        assert not targets[0].exists() and list(targets[0].parent.iterdir())==[]
        assert store.list_metadata('artifacts')==[]
    finally:store.close()


def test_source_change_during_actual_pinned_copy_remains_rejected(tmp_path,monkeypatch):
    root=tmp_path/'project';(root/'inputs').mkdir(parents=True)
    source=root/'inputs/shared.json';source.write_bytes(b'unchanged before hash')
    store=OperationStore(tmp_path/'store.sqlite3');real=artifacts._open_pinned_artifact;calls=0
    def opened(path):
        nonlocal calls
        handle,before=real(path)
        if Path(path)==source:
            calls+=1
            if calls==2:source.write_bytes(b'changed during copy!')
        return handle,before
    monkeypatch.setattr(artifacts,'_open_pinned_artifact',opened)
    try:
        with pytest.raises(ExecutionContractError) as exc:_register(root,store)
        assert calls==2 and exc.value.code=='ARTIFACT_CHANGED'
        assert list((root/'g2_artifacts/registered').iterdir())==[]
        assert store.list_metadata('artifacts')==[]
    finally:store.close()


def test_same_typed_metadata_sequential_registration_reuses_immutable_store_row(tmp_path):
    root=tmp_path/'project';(root/'inputs').mkdir(parents=True)
    source=root/'inputs/shared.json';source.write_bytes(b'owned immutable recipe-format payload')
    store=OperationStore(tmp_path/'store.sqlite3')
    try:
        first=_register(root,store);second=_register(root,store)
        assert first['reused'] is False and second['reused'] is True
        assert first['file_identity']==second['file_identity']
        assert second['artifact_type']=='application/json'
        assert second['format_version']=='comsol-mcp-model-recipe/1'
        assert Path(second['resolved_path']).stat().st_nlink==1
        assert len(store.list_metadata('artifacts'))==1
    finally:store.close()
