import os,pathlib,subprocess,json,sys
root=pathlib.Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');e=root/'overlay/evidence/p0_integration';scratch=pathlib.Path('/private/tmp/comsol-v2-p0-tests-20261007');scratch.mkdir(exist_ok=True)
python='/private/tmp/comsol-v2-runtime-20261007-new/comsol-mcp-recovery-runtime-20261004-001/python312/bin/python'
guard=pathlib.Path('/Volumes/SSD/Comsol-MCP/execution-scratch/temp-loss-runtime-recovery-20261004-001/builder-preparation022/network-guard')
selectors={'focused':['tests/test_artifact_registration_import.py','tests/test_artifact_preview.py','tests/test_artifact_preview_repair.py','/Volumes/SSD/Comsol-MCP/execution-scratch/artifact-preview-independent-review-20261005-001/reviewer-controls/test_artifact_preview_review_controls.py'],'regression':['tests/test_artifact_registration_import.py','tests/test_g2_registry_packaging.py','tests/test_execution_service.py','tests/test_execution_state_propagation.py','tests/test_g3_common.py','tests/test_g3_w13.py','tests/test_control_daemon_projects.py','tests/test_control_daemon.py']}
name=sys.argv[1];suite=name.split('-rerun')[0];run=e/name;run.mkdir(exist_ok=False);tmp=scratch/name;tmp.mkdir();(run/'network-guard.jsonl').touch()
env=os.environ.copy();env.update({'ARTIFACT_PREVIEW_REVIEW_REPO':str(root/'repository'),'HOME':str(tmp),'TMPDIR':str(tmp),'XDG_CACHE_HOME':str(tmp/'cache'),'PYTHONPYCACHEPREFIX':str(tmp/'pycache'),'PYTHONPATH':str(guard)+':'+str(root/'repository'),'CODEX_NETWORK_GUARD_LOG':str(run/'network-guard.jsonl'),'CODEX_NETWORK_GUARD_LABEL':'p0-integration','CODEX_NETWORK_GUARD_RUN_ID':name})
args=['-q',*selectors[suite],'--basetemp',str(tmp/'basetemp'),'--cache-clear','-o','cache_dir='+str(tmp/'pytest-cache'),'--junitxml='+str(run/'junit.xml')]
code='import pytest; raise SystemExit(pytest.main('+repr(args)+'))'
argv=[python,str(guard/'guarded_entry.py'),'exec',code];(run/'argv.json').write_text(json.dumps({'argv':argv,'cwd':str(root/'repository'),'environment':{k:env[k] for k in ['HOME','TMPDIR','PYTHONPATH','PYTHONPYCACHEPREFIX','XDG_CACHE_HOME']}},indent=2))
with (run/'stdout.txt').open('w') as out,(run/'stderr.txt').open('w') as err:
 p=subprocess.run(argv,cwd=root/'repository',env=env,stdout=out,stderr=err)
(run/'exit.json').write_text(json.dumps({'exit_code':p.returncode}));print((run/'stdout.txt').read_text());print((run/'stderr.txt').read_text());sys.exit(p.returncode)
