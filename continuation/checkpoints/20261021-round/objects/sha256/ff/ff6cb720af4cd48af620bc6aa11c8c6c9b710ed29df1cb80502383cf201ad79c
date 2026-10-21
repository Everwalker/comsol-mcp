from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import uuid

from tests.w26_r9_source_fixture import saved_r9_source_fixture

CODE_REPOSITORY = Path(__file__).resolve().parents[1]
SOURCE_REPOSITORY = CODE_REPOSITORY / "source"
SAVED_R9_SOURCE, _SAVED_R9_EXTRAS = saved_r9_source_fixture()
REPOSITORY = Path(os.environ.get("W25_R9_REPOSITORY_ROOT", SAVED_R9_SOURCE)).resolve()
SCRATCH_ROOT = Path(os.environ["W25_R9_SCRATCH_ROOT"]).resolve() if os.environ.get("W25_R9_SCRATCH_ROOT") else None
OVERLAY_TEST_ROOT = Path(os.environ.get("W25_R9_OVERLAY_TEST_ROOT", "/private/tmp")).resolve()


def _load_actor(kind: str, name: str):
    if SCRATCH_ROOT is not None:
        path = SCRATCH_ROOT / "tools" / ("w25_current_r9_offline_install_actor.py" if kind == "install" else "w25_macos_owned_desktop_actor_r9.py")
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        module.REPOSITORY = REPOSITORY
        module.STATE = CODE_REPOSITORY / "docs/full_project_continuation_v3/state"
        return module
    if kind == "install":
        from tools import w25_current_r9_offline_install_actor as module
    else:
        from tools import w25_macos_owned_desktop_actor_r9 as module
    module.REPOSITORY = REPOSITORY
    module.STATE = CODE_REPOSITORY / "docs/full_project_continuation_v3/state"
    return module


def _overlay_root() -> tuple[Path, dict[str, object]]:
    installer = _load_actor("install", "candidate_r9_install_actor")
    root = OVERLAY_TEST_ROOT / ("cvw26-r9install-" + uuid.uuid4().hex[:8])
    root.mkdir(mode=0o700)
    record = installer._prepare_current_desktop_overlay(root, {"path": "/immutable/App.whl", "sha256": "a" * 64})
    candidate = root / "W25_R9_INSTALL_CANDIDATE.json"
    candidate.write_text(json.dumps({"current_desktop_overlay": record}, sort_keys=True))
    candidate.chmod(0o600)
    return root, record


def _run_isolated(code: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["W25_R9_REPOSITORY_ROOT"] = str(REPOSITORY)
    env["W25_R9_CODE_REPOSITORY_ROOT"] = str(CODE_REPOSITORY)
    existing = [part for part in env.get("PYTHONPATH", "").split(os.pathsep) if part]
    env["PYTHONPATH"] = os.pathsep.join([str(SOURCE_REPOSITORY), *[part for part in existing if part != str(SOURCE_REPOSITORY)]])
    return subprocess.run([sys.executable, "-B", "-c", code], cwd=CODE_REPOSITORY,
                          env=env, capture_output=True, text=True, timeout=30, check=False)


def _base_child_code(root: Path, suffix: str) -> str:
    return f"""import importlib.util,json,os,pathlib,sys
scratch=os.environ.get('W25_R9_SCRATCH_ROOT')
if scratch:
 path=pathlib.Path(scratch)/'tools/w25_macos_owned_desktop_actor_r9.py'
 spec=importlib.util.spec_from_file_location('candidate_r9_gui_actor',path)
 module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
else:
 from tools import w25_macos_owned_desktop_actor_r9 as module
module.REPOSITORY=pathlib.Path(os.environ['W25_R9_REPOSITORY_ROOT'])
module.STATE=pathlib.Path(os.environ['W25_R9_CODE_REPOSITORY_ROOT'])/'docs/full_project_continuation_v3/state'
""" + suffix


def test_overlay_copier_and_gui_importer_select_exact_current_sources():
    root, manifest_record = _overlay_root()
    assert manifest_record["path"] == str(root / "CURRENT_DESKTOP_OVERLAY.json")
    code = _base_child_code(root, """
module._activate_current_desktop_overlay(pathlib.Path({root!r}))
for name,filename in module.DESKTOP_MODULES.items():
 imported=sys.modules[name]
 expected=pathlib.Path({root!r})/'current-desktop-overlay'/'comsol_mcp'/filename
 assert pathlib.Path(imported.__file__)==expected
 assert module.file_record(expected)['sha256']==module.CURRENT_DESKTOP_SOURCE_HASHES[name]
print(json.dumps({'status':'PASS_EXACT_CURRENT_DESKTOP_OVERLAY_SELECTED','native_Popen':0,'GUI':'NOT_RUN','COMSOL':'NOT_RUN','JVM':'NOT_RUN'}))
""").replace("{root!r}", repr(str(root)))
    result = _run_isolated(code)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.splitlines()[-1])["status"] == "PASS_EXACT_CURRENT_DESKTOP_OVERLAY_SELECTED"


def test_overlay_import_refuses_changed_copy_before_import():
    root, _manifest_record = _overlay_root()
    target = root / "current-desktop-overlay" / "comsol_mcp" / "_desktop_service.py"
    target.write_bytes(target.read_bytes() + b"\n# tampered\n")
    code = _base_child_code(root, """
try:
 module._activate_current_desktop_overlay(pathlib.Path({root!r}))
except RuntimeError as exc:
 assert 'overlay/source changed' in str(exc) or 'external first-record mismatch' in str(exc)
 assert not any(name.startswith('comsol_mcp._desktop_') for name in sys.modules)
 print(json.dumps({'status':'PASS_TAMPER_REFUSED_BEFORE_DESKTOP_IMPORT','native_Popen':0}))
else: raise AssertionError('modified overlay accepted')
""").replace("{root!r}", repr(str(root)))
    result = _run_isolated(code)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.splitlines()[-1])["status"] == "PASS_TAMPER_REFUSED_BEFORE_DESKTOP_IMPORT"


def test_overlay_import_refuses_preloaded_non_overlay_desktop_module():
    root, _manifest_record = _overlay_root()
    code = _base_child_code(root, """
import comsol_mcp._desktop_macos
try:
 module._activate_current_desktop_overlay(pathlib.Path({root!r}))
except RuntimeError as exc:
 assert 'preloaded from another source' in str(exc)
 print(json.dumps({'status':'PASS_FOREIGN_PRELOAD_REFUSED','native_Popen':0}))
else: raise AssertionError('foreign module preload accepted')
""").replace("{root!r}", repr(str(root)))
    result = _run_isolated(code)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.splitlines()[-1])["status"] == "PASS_FOREIGN_PRELOAD_REFUSED"


def test_overlay_import_refuses_symlinked_package_path():
    root, _manifest_record = _overlay_root()
    overlay_root = root / "current-desktop-overlay"
    moved = root / "current-desktop-overlay-original"
    overlay_root.rename(moved)
    overlay_root.symlink_to(moved, target_is_directory=True)
    code = _base_child_code(root, """
try:
 module._activate_current_desktop_overlay(pathlib.Path({root!r}))
except RuntimeError as exc:
 assert 'symlink' in str(exc)
 assert not any(name.startswith('comsol_mcp._desktop_') for name in sys.modules)
 print(json.dumps({'status':'PASS_SYMLINKED_OVERLAY_REFUSED','native_Popen':0}))
else: raise AssertionError('symlinked overlay accepted')
""").replace("{root!r}", repr(str(root)))
    result = _run_isolated(code)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.splitlines()[-1])["status"] == "PASS_SYMLINKED_OVERLAY_REFUSED"
