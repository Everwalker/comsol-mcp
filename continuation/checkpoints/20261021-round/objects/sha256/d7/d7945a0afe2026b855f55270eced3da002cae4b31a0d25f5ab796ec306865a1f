"""Pure software prebirth guards; these do not certify a Windows case."""
import hashlib
from pathlib import Path
import platform
import sys
from types import SimpleNamespace

import pytest
from tools import windows_restore_native_cycle as cycle


@pytest.fixture
def bound(tmp_path, monkeypatch):
    root=tmp_path/'cvw26-current-software';root.mkdir()
    raw=b'closed software input';record={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
    names=set(cycle.SOURCE_MEMBERS)
    helpers={'helpers-r10/windows_restore_native_cycle.py','helpers-r10/windows_restore_native_bootstrap.py','helpers-r10/windows_task_thread_owner.py','source-r10/tests/test_windows_parameter_publish_native.py'}
    inputs={'inputs-r3/registered-full-state.tar','inputs-r3/registered-full-state.tar.manifest.json','inputs-r3/closure.json','inputs-r3/whole-database.sqlite3','inputs-r3/files/parameters.json','inputs-r3/files/v3-native-smoke.mph','inputs-r3/files/g2_artifacts/registered/1e3f244c514ff89c75e0c4b39a70470db9314b8042e876ebb84a4370688e973f.json','inputs-r3/files/g2_artifacts/registered/fb9880a77414eeb84e1e79fba624f4d99be2ad359ac1b2ce9b60a72e76870601.mph'}
    for name in {'source-r10/'+n for n in names}|helpers|inputs:
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    binding={'schema':cycle.SCHEMA,'taskroot':str(root),'python':sys.executable,'python_identity':{'executable':sys.executable,'prefix':sys.prefix,'version':sys.version,'machine':platform.machine()},'source_directory':'source-r10','source175':{n:dict(record) for n in names},'helpers':{n:dict(record) for n in helpers},'inputs':{n:dict(record) for n in inputs},'literal_argv':['fixed-entry','--binding','fixed-binding'],'case_argv':{},'cases':list(cycle.CASES),'budget':dict(cycle.BUDGET),'root_identity':{},'root_security':''}
    entry=str(root/'helpers-r10/windows_restore_native_cycle.py');binding_path=str(root/'authority-r10/BINDING.json')
    binding['literal_argv']=[entry,'--binding',binding_path,'--approval',str(root/'authority-r10/MAIN_APPROVAL.json'),'--once',str(root/'authority-r10/ONCE_USED.json')]
    binding['case_argv']={case:[entry,'--binding',binding_path,'--case',case] for case in cycle.CASES}
    monkeypatch.setattr(cycle.sys,'flags',SimpleNamespace(isolated=1))
    monkeypatch.setattr(cycle.sys,'dont_write_bytecode',True)
    monkeypatch.setattr(cycle.subprocess,'Popen',lambda *a,**k:pytest.fail('Popen before admission'))
    return root,binding


def test_closed_binding_actual_samefd_files(bound):
    root,binding=bound
    assert cycle.validate(binding,binding['literal_argv'])==root


@pytest.mark.parametrize('fault',['dropfield','wrongpython','version','argv','budget','cases','membership','oldmode','sourcehash','outside','inputhash'])
def test_prebirth_refusals(bound,fault):
    root,binding=bound;argv=list(binding['literal_argv'])
    if fault=='dropfield':binding.pop('root_security')
    elif fault=='wrongpython':binding['python']='foreign-python'
    elif fault=='version':binding['python_identity']['version']='3.14 foreign'
    elif fault=='argv':argv.append('unbound')
    elif fault=='budget':binding['budget']['COMSOL']=1
    elif fault=='cases':binding['cases'].pop()
    elif fault=='membership':binding['source175'].pop('comsol_mcp/_state_backup.py')
    elif fault=='oldmode':binding['source_directory']='source-r2'
    elif fault=='sourcehash':(root/'source-r10/comsol_mcp/_state_backup.py').write_bytes(b'tamper')
    elif fault=='outside':binding['helpers']['../outside']=binding['helpers'].pop('helpers-r10/windows_task_thread_owner.py')
    elif fault=='inputhash':binding['inputs']['inputs-r3/whole-database.sqlite3']['sha256']='0'*64
    with pytest.raises(AssertionError):cycle.validate(binding,argv)


def test_original_popen_retained_before_constructor_and_terminal_after_failure(tmp_path, monkeypatch):
    import subprocess
    root=tmp_path/'controller';root.mkdir();(root/'evidence-r10').mkdir()
    script=tmp_path/'harmless.py';script.write_text('import time;time.sleep(0.08)\n')
    original_init=subprocess.Popen.__init__
    class FirstCause(Exception):
        def __str__(self):raise RuntimeError('secondary text failure')
    cause=FirstCause()
    def after_actual_birth(child,*args,**kwargs):
        assert cycle._ORIGINAL_CHILDREN[-1]['child'] is child
        original_init(child,*args,**kwargs)
        raise cause
    monkeypatch.setattr(cycle,'CASES',('harmless',))
    monkeypatch.setattr(subprocess.Popen,'__init__',after_actual_birth)
    binding={'python':sys.executable,'case_argv':{'harmless':[str(script)]}}
    with pytest.raises(FirstCause) as observed:
        cycle.run_fixed_cases(root,binding,{})
    holder=cycle._ORIGINAL_CHILDREN[-1]
    assert observed.value is cause and holder['first_cause'] is cause
    assert holder['child'].returncode==0 and holder['original_poll']()==0
    holder['stdout'].close();holder['stderr'].close()


def test_cli_string_once_path_exclusive_no_clobber(tmp_path):
    path=tmp_path/'private-authority'/'ONCE_USED.json'
    cycle.write_exclusive(str(path),b'exact one-use bytes')
    assert path.read_bytes()==b'exact one-use bytes'
    with pytest.raises(FileExistsError):cycle.write_exclusive(str(path),b'foreign replacement')
    assert path.read_bytes()==b'exact one-use bytes'


def test_actual_journal_reader_accepts_task_case_name_and_refuses_old_invalid_prefix(tmp_path):
    from comsol_mcp import _state_backup as backup
    home=tmp_path/'home';home.mkdir()
    journal={'schema':backup.FULL_RESTORE_JOURNAL_SCHEMA,'recovery_directory':cycle.JOURNAL_RECOVERY_NAME}
    backup._full_journal_write(home,journal)
    assert backup._read_full_restore_journal(home)==journal
    journal['recovery_directory']='task-journal'
    backup._full_journal_write(home,journal)
    with pytest.raises(backup.StateBackupError) as error:backup._read_full_restore_journal(home)
    assert error.value.code=='RESTORE_JOURNAL_INVALID'


def test_sealed_wal_header_input_only_connected_after_exact_private_clone(tmp_path):
    import sqlite3
    incoming=tmp_path/'inputs-r3';incoming.mkdir();input_db=incoming/'whole-database.sqlite3'
    creator=sqlite3.connect(input_db)
    creator.execute('PRAGMA journal_mode=WAL');creator.execute('CREATE TABLE witness(value)');creator.execute('INSERT INTO witness VALUES(73)');creator.commit();creator.close()
    before=input_db.read_bytes();assert before[18:20]==b'\x02\x02'
    fingerprint=cycle.samefd(input_db)[1]
    out=tmp_path/'private-case';out.mkdir()
    binding={'inputs':{'inputs-r3/whole-database.sqlite3':{'sha256':fingerprint,'bytes':len(before)}}}
    clone=cycle.clone_whole_input(out,tmp_path,binding)
    with sqlite3.connect(clone) as reader:assert reader.execute('SELECT value FROM witness').fetchone()==(73,)
    assert input_db.read_bytes()==before and not Path(str(input_db)+'-wal').exists() and not Path(str(input_db)+'-shm').exists()
    bad={'inputs':{'inputs-r3/whole-database.sqlite3':{'sha256':'0'*64,'bytes':len(before)}}}
    with pytest.raises(AssertionError):cycle.clone_whole_input(out,tmp_path,bad)
