"""Fixed source-only Windows kernel controls, admitted by a fresh Main binding.

No COMSOL, JVM, daemon, application install, or historical job replay. Test
faults wrap actual original native functions; they never replace the SDK with
TreeSDK or use a permissive ACL verifier. UNKNOWN cases end only when the
original child process naturally terminates. Parent never sends a signal.
"""
from __future__ import annotations
import argparse,ctypes as C,gc,hashlib,json,os,platform,re,sqlite3,subprocess,sys,time,weakref
from pathlib import Path
from unittest.mock import patch

_ORIGINAL_CHILDREN=[]

SCHEMA='cv3-windows-kernel-restore-binding/2'
JOURNAL_RECOVERY_NAME='state-full-recovery-task-kernel'
CASES=('complete14_registered2_supplement2','private_wal_snapshot','owned_shm','foreign_file','journal_PREPARED','journal_QUARANTINED','journal_REMOVE_INTENT','journal_DONE','full_restore_rollback','full_restore_recover','original_partial_out_unknown','original_close_unknown','native_publisher9')
SOURCE_MEMBERS=('LICENSE', 'README.md', 'comsol_mcp/__init__.py', 'comsol_mcp/_artifact_preview_io.py', 'comsol_mcp/_artifact_store.py', 'comsol_mcp/_atomic_save.py', 'comsol_mcp/_complex_transform.py', 'comsol_mcp/_comsol_progress_capture.py', 'comsol_mcp/_connection.py', 'comsol_mcp/_control_client.py', 'comsol_mcp/_control_daemon.py', 'comsol_mcp/_convergence_store.py', 'comsol_mcp/_daemon_quiescence.py', 'comsol_mcp/_dataset_binding.py', 'comsol_mcp/_desktop_capture.py', 'comsol_mcp/_desktop_macos.py', 'comsol_mcp/_desktop_platforms.py', 'comsol_mcp/_desktop_selector_profile.py', 'comsol_mcp/_desktop_service.py', 'comsol_mcp/_desktop_uia.py', 'comsol_mcp/_desktop_win32_identity.py', 'comsol_mcp/_desktop_windows.py', 'comsol_mcp/_domain_outcome.py', 'comsol_mcp/_ephemeral_result_ticket.py', 'comsol_mcp/_execution_contract.py', 'comsol_mcp/_execution_service.py', 'comsol_mcp/_g2_artifact_preview.py', 'comsol_mcp/_g2_artifact_reads.py', 'comsol_mcp/_g2_artifact_verify.py', 'comsol_mcp/_g2_checkpoint_diff.py', 'comsol_mcp/_g2_checkpoint_ops.py', 'comsol_mcp/_g2_code.py', 'comsol_mcp/_g2_contract.py', 'comsol_mcp/_g2_copy_subtree.py', 'comsol_mcp/_g2_docs.py', 'comsol_mcp/_g2_engine.py', 'comsol_mcp/_g2_group_remove.py', 'comsol_mcp/_g2_isolation.py', 'comsol_mcp/_g2_model_compare.py', 'comsol_mcp/_g2_public_api.py', 'comsol_mcp/_g2_registry.py', 'comsol_mcp/_g2_tools.py', 'comsol_mcp/_g2_transactions.py', 'comsol_mcp/_g3_common.py', 'comsol_mcp/_g3_definition_validation.py', 'comsol_mcp/_g3_exports.py', 'comsol_mcp/_g3_managed_models.py', 'comsol_mcp/_g3_mesh_convergence_executor.py', 'comsol_mcp/_g3_metrics.py', 'comsol_mcp/_g3_model_delivery.py', 'comsol_mcp/_g3_model_domains.py', 'comsol_mcp/_g3_ops.py', 'comsol_mcp/_g3_parameter_io.py', 'comsol_mcp/_g3_results.py', 'comsol_mcp/_g3_runtime.py', 'comsol_mcp/_g3_solver_ops.py', 'comsol_mcp/_g3_units.py', 'comsol_mcp/_g3_w13.py', 'comsol_mcp/_g3_w14.py', 'comsol_mcp/_g3_w15.py', 'comsol_mcp/_g3_w16.py', 'comsol_mcp/_g3_w18.py', 'comsol_mcp/_g3_w20_validation.py', 'comsol_mcp/_g3_w21.py', 'comsol_mcp/_gate_a_reopen.py', 'comsol_mcp/_host_scoped_provider.py', 'comsol_mcp/_java_worker.py', 'comsol_mcp/_managed_backend.py', 'comsol_mcp/_mcp_gateway.py', 'comsol_mcp/_measure_spec.py', 'comsol_mcp/_metric_contract.py', 'comsol_mcp/_mode_overlap.py', 'comsol_mcp/_model.py', 'comsol_mcp/_model_ops.py', 'comsol_mcp/_observation_store.py', 'comsol_mcp/_operation_store.py', 'comsol_mcp/_phase1_runtime.py', 'comsol_mcp/_physics_ops.py', 'comsol_mcp/_platform_paths.py', 'comsol_mcp/_platform_process.py', 'comsol_mcp/_probe_manage.py', 'comsol_mcp/_process_diagnostics.py', 'comsol_mcp/_project_authority.py', 'comsol_mcp/_result_budget.py', 'comsol_mcp/_runtime_control.py', 'comsol_mcp/_runtime_installation.py', 'comsol_mcp/_runtime_state.py', 'comsol_mcp/_security_os.py', 'comsol_mcp/_server.py', 'comsol_mcp/_session_context.py', 'comsol_mcp/_session_lifecycle.py', 'comsol_mcp/_session_server.py', 'comsol_mcp/_solution_binding.py', 'comsol_mcp/_solver_ops.py', 'comsol_mcp/_source_manifest.py', 'comsol_mcp/_stage_contract.py', 'comsol_mcp/_state.py', 'comsol_mcp/_state_backup.py', 'comsol_mcp/_tools_connection.py', 'comsol_mcp/_tools_control.py', 'comsol_mcp/_tools_desktop.py', 'comsol_mcp/_tools_geometry.py', 'comsol_mcp/_tools_model.py', 'comsol_mcp/_tools_params.py', 'comsol_mcp/_tools_phase1.py', 'comsol_mcp/_tools_physics.py', 'comsol_mcp/_tools_snapshot.py', 'comsol_mcp/_tools_solver.py', 'comsol_mcp/_tools_w18.py', 'comsol_mcp/_tools_w20.py', 'comsol_mcp/_tools_w21.py', 'comsol_mcp/_tools_workflow.py', 'comsol_mcp/_vcsel_source.py', 'comsol_mcp/_w21_execution.py', 'comsol_mcp/_w21_initial_stage.py', 'comsol_mcp/_w21_mesh_evidence.py', 'comsol_mcp/_w21_mesh_readback.py', 'comsol_mcp/_w21_native_output.py', 'comsol_mcp/_w21_stage_backend.py', 'comsol_mcp/_w23_basis_v2_contract.py', 'comsol_mcp/_w23_basis_v2_provenance.py', 'comsol_mcp/_w23_basis_v2_results.py', 'comsol_mcp/_w23_results.py', 'comsol_mcp/_w26_memory_resources.py', 'comsol_mcp/_w26_os_resources.py', 'comsol_mcp/_windows_appcontainer.py', 'comsol_mcp/_windows_appcontainer_native.py', 'comsol_mcp/_windows_filter_group.py', 'comsol_mcp/_windows_host_change.py', 'comsol_mcp/_windows_host_identity_owner.py', 'comsol_mcp/_windows_host_preflight_owner.py', 'comsol_mcp/_windows_job_limits.py', 'comsol_mcp/_windows_model_publish.py', 'comsol_mcp/_windows_owned_resources.py', 'comsol_mcp/_windows_parameter_publish.py', 'comsol_mcp/_windows_policy_owner.py', 'comsol_mcp/_windows_service_sid.py', 'comsol_mcp/_windows_service_sid_native.py', 'comsol_mcp/_windows_state_restore.py', 'comsol_mcp/_windows_t037.py', 'comsol_mcp/_windows_t037_acl.py', 'comsol_mcp/_windows_t037_bootstrap.py', 'comsol_mcp/_windows_t037_broker.py', 'comsol_mcp/_windows_t037_child.py', 'comsol_mcp/_windows_t037_effective.py', 'comsol_mcp/_windows_t037_native.py', 'comsol_mcp/_windows_t037_resume.py', 'comsol_mcp/_windows_t037_wfp.py', 'comsol_mcp/_windows_wfp_mutation_owner.py', 'comsol_mcp/data/g2/02_ACTION_CATALOG.json', 'comsol_mcp/data/g2/common.schema.json', 'comsol_mcp/data/science_recipes/SOURCE_ORIGINS.json', 'comsol_mcp/data/science_recipes/java/NativeW23TEScienceFixture.java', 'comsol_mcp/data/science_recipes/java/ScientificBuildReadback.java', 'comsol_mcp/data/science_recipes/java/W22Fixture.java', 'comsol_mcp/data/science_recipes/java/W24CureCouponFixture.java', 'comsol_mcp/data/science_recipes/java/W24StaticShapeFixture.java', 'comsol_mcp/data/science_recipes/parameters.json', 'comsol_mcp/data/science_recipes/w22_benchmark.json', 'comsol_mcp/data/science_recipes/w22_source/basis_l0_g0.txt', 'comsol_mcp/data/science_recipes/w22_source/basis_l0_g1.txt', 'comsol_mcp/data/science_recipes/w22_source/basis_l0_g2.txt', 'comsol_mcp/data/science_recipes/w22_source/basis_l1_g0.txt', 'comsol_mcp/data/science_recipes/w22_source/basis_l1_g1.txt', 'comsol_mcp/data/science_recipes/w22_source/basis_l1_g2.txt', 'comsol_mcp/data/science_recipes/w22_source/basis_l2_g0.txt', 'comsol_mcp/data/science_recipes/w22_source/basis_l2_g1.txt', 'comsol_mcp/data/science_recipes/w22_source/basis_l2_g2.txt', 'comsol_mcp/data/science_recipes/w22_source/manifest.json', 'comsol_mcp/mcp_server.py', 'comsol_mcp/phase1_java/BoundPhase1Poc.java', 'comsol_mcp/phase1_java/BoundPhase1ProductProbe.java', 'comsol_mcp/phase1_java/BoundPhase1Reopen.java', 'comsol_mcp/worker_java/PersistentComsolWorker.java', 'pyproject.toml')

_KEYS={'schema','taskroot','python','source175','helpers','inputs','literal_argv','case_argv','cases','budget','root_identity','root_security','source_directory','python_identity'}
BUDGET={'elapsed_seconds':300,'COMSOL':0,'JVM':0,'GUI':0,'Study':0,'solver':0,'case_processes':13}


def samefd(path):
    path=Path(path);assert not path.is_symlink()
    named_before=path.stat()
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_BINARY',0))
    with os.fdopen(fd,'rb') as f:
        a=os.fstat(f.fileno());data=f.read();named_after=path.stat();b=os.fstat(f.fileno())
        sig=lambda v:(v.st_dev,v.st_ino,v.st_size,v.st_mtime_ns,v.st_ctime_ns)
        assert sig(a)==sig(b) and sig(named_before)==sig(named_after)
        # CPython3.12 Windows named st_ctime is creation time, whereas
        # descriptor st_ctime can be ChangeTime. Preserve both domains and
        # their full before/after checks; never equate or remove ChangeTime.
        named=lambda v:(v.st_dev,v.st_ino,v.st_size,v.st_mtime_ns,getattr(v,'st_birthtime_ns',None))
        assert named(a)==named(named_after)
    return data,hashlib.sha256(data).hexdigest()


def validate(binding,argv):
    assert set(binding)==_KEYS and binding['schema']==SCHEMA
    assert binding['cases']==list(CASES) and binding['budget']==BUDGET
    assert argv==binding['literal_argv'] and sys.executable==binding['python']
    assert binding['python_identity']=={'executable':sys.executable,'prefix':sys.prefix,'version':sys.version,'machine':platform.machine()}
    assert sys.flags.isolated==1 and sys.dont_write_bytecode
    root=Path(binding['taskroot']);assert root.is_absolute() and root.name.startswith('cvw26-current-')
    assert binding['source_directory']=='source-r13'
    entry=str(root/'helpers-r13/windows_restore_native_cycle.py')
    binding_path=str(root/'authority-r13/BINDING.json')
    assert binding['literal_argv']==[entry,'--binding',binding_path,'--approval',str(root/'authority-r13/MAIN_APPROVAL.json'),'--once',str(root/'authority-r13/ONCE_USED.json')]
    assert binding['case_argv']=={case:[entry,'--binding',binding_path,'--case',case] for case in CASES}
    assert tuple(sorted(binding['source175']))==SOURCE_MEMBERS
    assert len(binding['source175'])==175 and sum(n.startswith('comsol_mcp/') for n in binding['source175'])==172
    for n,r in binding['source175'].items():
        assert not Path(n).is_absolute() and '..' not in Path(n).parts
        raw,sha=samefd(root/binding['source_directory']/n);assert sha==r['sha256'] and len(raw)==r['bytes']
    assert set(binding['helpers'])=={'helpers-r13/windows_restore_native_cycle.py','helpers-r13/windows_restore_native_bootstrap.py','helpers-r13/windows_task_thread_owner.py','source-r13/tests/test_windows_parameter_publish_native.py'}
    expected_inputs={'inputs-r3/registered-full-state.tar','inputs-r3/registered-full-state.tar.manifest.json','inputs-r3/closure.json','inputs-r3/whole-database.sqlite3','inputs-r3/files/parameters.json','inputs-r3/files/v3-native-smoke.mph','inputs-r3/files/g2_artifacts/registered/1e3f244c514ff89c75e0c4b39a70470db9314b8042e876ebb84a4370688e973f.json','inputs-r3/files/g2_artifacts/registered/fb9880a77414eeb84e1e79fba624f4d99be2ad359ac1b2ce9b60a72e76870601.mph'}
    assert set(binding['inputs'])==expected_inputs
    for group in ('helpers','inputs'):
        for n,r in binding[group].items():
            assert not Path(n).is_absolute() and '..' not in Path(n).parts
            raw,sha=samefd(root/n);assert sha==r['sha256'] and len(raw)==r['bytes']
    return root


def imports(root,binding):
    os.environ['COMSOL_SERVER_MCP_HOME']=str(root/'home-r13');os.environ['TEMP']=str(root/'scratch-r13');os.environ['TMP']=str(root/'scratch-r13');os.environ['PYTHONDONTWRITEBYTECODE']='1'
    sys.path.insert(0,str(root/binding['source_directory']))
    sys.path.insert(0,str(root/'helpers-r13'))
    from comsol_mcp import _state_backup as b,_windows_state_restore as w
    assert w.directory_identity(root)==binding['root_identity']
    with w._chain(root) as c:assert w.security(c.api,c.leaf,directory=True)==binding['root_security']
    assert all(Path(m.__file__).is_relative_to(root/binding['source_directory']) for n,m in sys.modules.items() if n.startswith('comsol_mcp') and getattr(m,'__file__',None))
    from windows_task_thread_owner import stable_native_bytes
    for group in ('source175','helpers','inputs'):
        for name,record in binding[group].items():
            path=root/binding['source_directory']/name if group=='source175' else root/name
            raw,sha,proof=stable_native_bytes(path)
            assert sha==record['sha256'] and len(raw)==record['bytes']
    return b,w


def newdir(root,name):
    p=root/name;p.mkdir();return p


def write_exclusive(path,data):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
    assert samefd(path)[1]==hashlib.sha256(data).hexdigest()


def clone_whole_input(out,root,binding):
    name='inputs-r3/whole-database.sqlite3'
    raw,sha=samefd(root/name);expected=binding['inputs'][name]
    assert sha==expected['sha256'] and len(raw)==expected['bytes']
    target=out/'source.sqlite3';write_exclusive(target,raw)
    return target


def sql_counts(db):
    with sqlite3.connect('file:'+str(db).replace('\\','/')+'?mode=ro',uri=True) as con:
        names=[r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {n:con.execute('SELECT count(*) FROM "'+n+'"').fetchone()[0] for n in names}


def restore(case,b,w,root):
    home=newdir(root,'control');workspace=newdir(root,'workspace')
    archive=root.parents[1]/'inputs-r3/registered-full-state.tar';manifest=Path(str(archive)+'.manifest.json')
    return home,workspace,lambda:b.restore_full_backup(home,workspace,archive,manifest,confirm=True)


def _private_wal_snapshot_case(source,out,b):
    """Exercise the production private-copy reader before taking the snapshot.

    A direct mode=ro connection may update SQLite's read marks in the source
    SHM file. The readonly contract is instead provided by a private DB/WAL
    copy; online backup runs on that owned copy and its close owns cleanup.
    """
    before=b._sqlite_source_closure(source)
    sidecars_before={suffix:samefd(Path(str(source)+suffix))[1]
                     for suffix in ('-wal','-shm')}
    private=b._private_readonly_connection(source)
    owned_root=Path(private._snapshot_root)
    try:
        assert private.execute('SELECT value FROM task_wal_probe').fetchone()==(73,)
        owned_source=owned_root/b.DATABASE_NAME
        assert owned_source.is_file()
        snapshot_path=out/'snapshot.sqlite3'
        snapshot=b._online_snapshot_database(owned_source,snapshot_path)
        with sqlite3.connect(snapshot_path) as read:
            assert read.execute('SELECT value FROM task_wal_probe').fetchone()==(73,)
    finally:
        private.close()
    assert not owned_root.exists()
    after=b._sqlite_source_closure(source)
    sidecars_after={suffix:samefd(Path(str(source)+suffix))[1]
                    for suffix in ('-wal','-shm')}
    assert before==after and sidecars_before==sidecars_after
    return {'producer':'production _private_readonly_connection',
            'read_value':73,
            'private_copy_root':str(owned_root),
            'private_copy_closed_and_removed':True,
            'source_closure_before':before,
            'source_closure_after':after,
            'source_sidecar_sha256_before':sidecars_before,
            'source_sidecar_sha256_after':sidecars_after,
            'source_closure_unchanged':True,
            'snapshot':snapshot}


def execute_case(case,root,binding):
    b,w=imports(Path(binding['taskroot']),binding);out=newdir(root/'cases-r13',case);result={'case':case,'scope':'ACTUAL_WINDOWS_KERNEL_SOURCE_ONLY_NOT_PRODUCT_OR_SCIENCE','replayed':False}
    if case=='native_publisher9':
        import importlib.util,unittest
        argv=sys.argv;sys.argv=[str(root/binding['source_directory']/'tests/test_windows_parameter_publish_native.py'),'--helper',str(root/binding['source_directory']/'comsol_mcp/_windows_parameter_publish.py')]
        # Fixed original unittest source uses real Win32; no skip accepted.
        spec=importlib.util.spec_from_file_location('fixed_native_publisher_controls',root/binding['source_directory']/'tests/test_windows_parameter_publish_native.py');mod=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.argv=argv;suite=unittest.defaultTestLoader.loadTestsFromTestCase(mod.NativeControls)
        rr=unittest.TextTestRunner().run(suite);assert rr.wasSuccessful() and rr.testsRun==9 and not rr.skipped
        result['native_tests']=rr.testsRun
    elif case=='complete14_registered2_supplement2':
        home,workspace,run=restore(case,b,w,out);receipt=run();closure=json.loads((root/'inputs-r3/closure.json').read_text())
        db=home/b.DATABASE_NAME;counts=sql_counts(db);assert len(counts)==14 and counts==closure['table_counts']
        with b._readonly_connection(db) as con:assert b._history_fingerprint(con)==closure['durable_history_sha256_same_algorithm_as_production']
        with sqlite3.connect(db) as con:
            projects={k:json.loads(v)['workspace'] for k,v in con.execute('SELECT project_id,record_json FROM projects')}
        for f in closure['project_files']:
            destination=Path(projects[f['project_id']])/f['relative_path'];incoming=root/'inputs-r3/files'/f['relative_path']
            if not f['registered_blob']:write_exclusive(destination,incoming.read_bytes())
            assert samefd(destination)[1]==f['copy']['sha256']
        assert receipt['file_count']==2 and len(closure['project_files'])==4
        assert not receipt['worker_or_model_bindings_revived'] and not receipt['operation_replay_performed']
        result.update(receipt=receipt,table_counts=counts,registered_files=2,supplemental_files=2,total_files=4)
    elif case=='private_wal_snapshot':
        source=clone_whole_input(out,root,binding)
        con=sqlite3.connect(source)
        try:
            assert con.execute('PRAGMA journal_mode=WAL').fetchone()[0].lower()=='wal'
            con.execute('CREATE TABLE task_wal_probe(value INTEGER)');con.execute('INSERT INTO task_wal_probe VALUES(73)');con.commit()
            result['private_wal_snapshot']=_private_wal_snapshot_case(source,out,b)
        finally:con.close()
    elif case=='owned_shm':
        source=clone_whole_input(out,root,binding)
        target=out/'owned.sqlite3';birth=w.birth_file(target)
        con=sqlite3.connect(target)
        try:
            con.executescript('CREATE TABLE schema_meta(version INTEGER); INSERT INTO schema_meta VALUES(1);')
            # Use the real full14 DB clone to preserve production schema gates.
            con.close();write=sqlite3.connect(target);src=sqlite3.connect(source);src.backup(write);src.close();write.execute('PRAGMA journal_mode=DELETE');write.close()
            write_exclusive(Path(str(target)+'-shm'),bytes(32768));con=sqlite3.connect(target)
            r=w.finish_standalone(target,con,birth);assert r['action']=='REMOVED_OWNED_ORPHAN_SHM';result['cleanup']=r
        finally:con.close()
    elif case=='foreign_file':
        source=out/'born';target=out/'installed';birth=w.birth_file(source);source.write_bytes(b'owned');sha=hashlib.sha256(b'owned').hexdigest();w.install_file(source,target,sha=sha,birth=birth)
        target.rename(out/'retained-original');target.write_bytes(b'owned')
        try:w.remove_file(target,sha=sha,birth=birth)
        except b.StateBackupError:pass
        else:raise AssertionError('sameSHA foreign replacement accepted')
        assert target.read_bytes()==b'owned' and (out/'retained-original').read_bytes()==b'owned'
    elif case.startswith('journal_'):
        phase=case.removeprefix('journal_');tree=newdir(out,'tree');(tree/'file').write_bytes(b'task-created');home=newdir(out,'journal')
        journal={'schema':b.FULL_RESTORE_JOURNAL_SCHEMA,'recovery_directory':JOURNAL_RECOVERY_NAME};proof=w.directory_identity(tree);rootid=w.directory_identity(out);original=b._full_journal_write;once=[False];seen=[]
        def boundary(h,j):
            original(h,j);p=j['directory_cleanup'][-1]['phase'];seen.append(p)
            if p==phase and not once[0]:once[0]=True;raise OSError('controlled durablephase boundary')
        with patch.object(b,'_full_journal_write',boundary):
            try:b._remove_exact_directory(out,'tree',expected_identity=proof,expected_root_identity=rootid,expected_files={'file':(hashlib.sha256(b'task-created').hexdigest(),12)},journal_home=home,journal=journal,journal_root_relative='')
            except OSError:pass
        assert once[0];reloaded=b._read_full_restore_journal(home)
        if phase!='DONE':b._remove_exact_directory(out,'tree',expected_identity=proof,expected_root_identity=rootid,expected_files={'file':(hashlib.sha256(b'task-created').hexdigest(),12)},journal_home=home,journal=reloaded,journal_root_relative='')
        assert not tree.exists();assert b._read_full_restore_journal(home)['directory_cleanup'][-1]['phase']=='DONE';result['durable_phases']=seen
    elif case in ('full_restore_rollback','full_restore_recover'):
        home,workspace,run=restore(case,b,w,out);original=b._full_journal_write;fired=[False]
        def boundary(h,j):
            original(h,j)
            if j['phase']=='DATABASE_INSTALL_PENDING' and not fired[0]:fired[0]=True;raise OSError('controlled DBinstall pending')
        def refuse_rollback(*a):raise OSError('controlled preservefor explicit recovery')
        with patch.object(b,'_full_journal_write',boundary):
            if case.endswith('recover'):
                with patch.object(b,'_rollback_full_restore',refuse_rollback):
                    try:run()
                    except b.StateBackupError as error:assert error.code=='RESTORE_ROLLBACK_INCOMPLETE'
            else:
                try:run()
                except b.StateBackupError as error:assert error.code=='FULL_RESTORE_FAILED'
        assert fired[0]
        if case.endswith('recover'):assert b.recover_full_restore(home,confirm=True)['success']
        assert not (home/b.DATABASE_NAME).exists() and not (workspace/'projects').exists() and not (home/b.ACTIVE_FULL_RESTORE_JOURNAL).exists()
    elif case in ('original_partial_out_unknown','original_close_unknown'):
        home,workspace,run=restore(case,b,w,out);original_factory=w._API;trigger=[False];rollback=[];before=[]
        def factory():
            api=original_factory()
            if case=='original_partial_out_unknown':
                original=api.nt.NtCreateFile
                def actual(*args):
                    rc=original(*args)
                    attrs=C.cast(args[2],C.POINTER(__import__('comsol_mcp._windows_parameter_publish',fromlist=['_ObjectAttributes'])._ObjectAttributes)).contents
                    name=C.wstring_at(attrs.ObjectName.contents.Buffer)
                    if not trigger[0] and name.startswith('.comsol-full-restore-stage-') and getattr(args[0],'_obj').value:
                        trigger[0]=True;raise OSError('after actual native out HANDLE')
                    return rc
                api.nt.NtCreateFile=actual
            else:
                original=api.k.CloseHandle
                def actual(h):
                    rc=original(h)
                    if not trigger[0] and any(workspace.glob('.comsol-full-restore-stage-*')):trigger[0]=True;return 0
                    return rc
                api.k.CloseHandle=actual
            return api
        with patch.object(w,'_API',factory),patch.object(b,'_rollback_full_restore',lambda *a:rollback.append(a)):
            try:run()
            except BaseException:pass
            else:raise AssertionError('controlled UNKNOWN missing')
        assert trigger[0] and not rollback
        owners=w.retained_unknown_owners();assert owners;ref=weakref.ref(owners[-1]['api']);del owners;gc.collect();assert ref() is not None
        try:b.recover_full_restore(home,confirm=True)
        except b.StateBackupError as error:assert error.code=='RESTORE_ORIGINAL_OWNER_UNKNOWN'
        else:raise AssertionError('same scope UNKNOWN reopened')
        result['original_API_strongroot_after_GC']=True;result['UNKNOWN_preserved_no_retry']=True
    else:raise AssertionError('unbound case')
    write_exclusive(out/'RESULT.json',(json.dumps(result,sort_keys=True,indent=2)+'\n').encode());return result


def main():
    admitted_at=time.monotonic()
    p=argparse.ArgumentParser();p.add_argument('--binding',required=True);p.add_argument('--approval');p.add_argument('--once');p.add_argument('--case',choices=CASES);a=p.parse_args()
    raw,sha=samefd(a.binding);binding=json.loads(raw);root=validate(binding,binding['literal_argv'])
    if a.case:
        assert sys.argv==binding['case_argv'][a.case]
        assert a.approval is None and a.once is None
        # Private fixed child only: it must observe the original consumed once.
        assert json.loads((root/'authority-r13/ONCE_USED.json').read_text())['binding_sha256']==sha
        sys.path.insert(0,str(root/binding['source_directory']));sys.path.insert(0,str(root/'helpers-r13'))
        from windows_task_thread_owner import task_thread_owner
        with task_thread_owner() as token_receipt:
            case_failure=None
            try:execute_case(a.case,root,binding)
            except BaseException as failure:case_failure=failure
        # Receipt is emitted only after strict original thread restoration.
        # stdout is the original already-owned inherited pipe/file.
        print(json.dumps({'thread_case':a.case,'thread_token_receipt':token_receipt,
                          'case_failed':case_failure is not None}),flush=True)
        if case_failure is not None:raise case_failure
        return 0
    assert sys.argv==binding['literal_argv'] and a.approval and a.once
    approval=json.loads(samefd(a.approval)[0]);assert approval=={'schema':'cv3-Main-windows-kernel-once/1','status':'APPROVED_EXECUTE_ONCE','binding_sha256':sha,'literal_argv':binding['literal_argv'],'budget':BUDGET,'native_nonce':approval.get('native_nonce')}
    assert type(approval['native_nonce']) is str and re.fullmatch('[0-9a-f]{32}',approval['native_nonce'])
    assert Path(a.approval)==root/'authority-r13/MAIN_APPROVAL.json'
    assert Path(a.once)==root/'authority-r13/ONCE_USED.json'
    imports(root,binding)
    sys.path.insert(0,str(root/'helpers-r13'))
    from windows_task_thread_owner import task_thread_owner
    with task_thread_owner() as parent_token:
        write_exclusive(a.once,(json.dumps({'binding_sha256':sha,'native_nonce':approval['native_nonce']})+'\n').encode())
        case_failure=None;result=None
        try:result=run_fixed_cases(root,binding,parent_token,admitted_at)
        except BaseException as failure:case_failure=failure
    parent_receipt={'parent_thread_token_receipt':parent_token,'case_failed':case_failure is not None}
    write_exclusive(root/'evidence-r13/PARENT_THREAD_TOKEN_RECEIPT.json',
                    (json.dumps(parent_receipt,sort_keys=True)+'\n').encode())
    print(json.dumps(parent_receipt),flush=True)
    if case_failure is not None:raise case_failure
    return result


def run_fixed_cases(root,binding,parent_token,start=None):
    if start is None:start=time.monotonic()
    records=[];retained=[];(root/'cases-r13').mkdir()
    for case in CASES:
        if time.monotonic()-start>=BUDGET['elapsed_seconds']:raise RuntimeError('budget admission closed before nextcase')
        argv=[binding['python'],'-I','-B',*binding['case_argv'][case]]
        out=(root/'evidence-r13'/('case-'+case+'.stdout')).open('xb');err=(root/'evidence-r13'/('case-'+case+'.stderr')).open('xb')
        # Retain the original Popen object before its constructor can birth.
        original_class=subprocess.Popen
        child=original_class.__new__(original_class);retained.append(child)
        original_poll=original_class.poll.__get__(child)
        holder={'child':child,'original_poll':original_poll,'stdout':out,'stderr':err,'first_cause':None,'birth_confirmed':False}
        _ORIGINAL_CHILDREN.append(holder)
        try:
            original_class.__init__(child,argv,stdout=out,stderr=err,env=os.environ.copy())
            holder['birth_confirmed']=True
        except BaseException as failure:
            holder['first_cause']=failure
            # Ambiguous constructor birth is not reconstructed from PID/path.
            # Keep original object and all outputs, close admission, never retry.
            while True:
                try:
                    # Only this original object's actual birth bookkeeping and
                    # original poll can prove its terminal child. Missing or
                    # partial constructor proof remains unresolved.
                    if getattr(child,'_child_created',False) is True and original_poll() is not None:
                        raise_original=holder['first_cause']
                        break
                except BaseException:pass
                try:time.sleep(.1)
                except BaseException:pass
            raise raise_original
        deadline_exceeded=False;first_failure=None
        while True:
            try:
                if original_poll() is not None:break
            except BaseException as failure:
                if first_failure is None:first_failure=failure;holder['first_cause']=failure
                deadline_exceeded=True
            try:
                if time.monotonic()-start>=BUDGET['elapsed_seconds']:deadline_exceeded=True
            except BaseException as failure:
                if first_failure is None:first_failure=failure;holder['first_cause']=failure
                deadline_exceeded=True
            try:time.sleep(.1)
            except BaseException as failure:
                if first_failure is None:first_failure=failure;holder['first_cause']=failure
                deadline_exceeded=True
        out.flush();err.flush()
        thread=None
        lines=(root/'evidence-r13'/('case-'+case+'.stdout')).read_text(encoding='utf-8-sig').splitlines()
        if lines:
            try:candidate=json.loads(lines[-1])
            except (ValueError,TypeError):candidate=None
            if isinstance(candidate,dict) and candidate.get('thread_case')==case:
                thread=candidate
                assert thread['thread_token_receipt']['restored'] is True
                write_exclusive(root/'evidence-r13'/('thread-'+case+'.json'),(json.dumps(thread)+'\n').encode())
        if child.returncode==0 and thread is None:
            raise RuntimeError('successful case lacks original thread restoration receipt')
        out.close();err.close();records.append({'case':case,'original_PID':child.pid,'original_Popen_terminal':child.returncode,'deadline_exceeded':deadline_exceeded,'thread_token_restored':None if thread is None else thread['thread_token_receipt']['restored']})
        write_exclusive(root/'evidence-r13'/('case-'+case+'.parent.json'),(json.dumps(records[-1])+'\n').encode())
        if child.returncode or deadline_exceeded:raise RuntimeError('case failure; no replay or next admission')
    write_exclusive(root/'evidence-r13/ACTUAL_SUMMARY.json',(json.dumps({'cases':records,'elapsed':time.monotonic()-start,'scope':'source-only Windows kernel controls, no engine/product/science acceptance'})+'\n').encode());return 0


if __name__=='__main__':raise SystemExit(main())
