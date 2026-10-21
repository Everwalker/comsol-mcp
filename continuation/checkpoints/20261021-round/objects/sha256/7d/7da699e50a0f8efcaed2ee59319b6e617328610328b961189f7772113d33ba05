"""Build the wheel from this checkout and validate an install outside the source tree.

M4 deliverable: a wheel that is built from the frozen tree, installed into a fresh
interpreter that cannot see the checkout, with dependency locking (``pip freeze``),
``pip check``, an import-identity check and a packaged-resource inventory.

Usage::

    python tools/verify_wheel_install.py --run-dir evidence/phase4_3/runs/<name>

The script only writes inside ``--run-dir``; it never modifies the checkout.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def run(cmd: list[str], *, cwd: pathlib.Path | None = None, env: dict | None = None) -> dict:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd or ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    return {
        "cmd": " ".join(cmd),
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def write(run_dir: pathlib.Path, name: str, payload) -> pathlib.Path:
    path = run_dir / name
    if isinstance(payload, (dict, list)):
        path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    else:
        path.write_text(str(payload))
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--builder", default=sys.executable, help="interpreter used to build the wheel")
    args = parser.parse_args()

    run_dir = (ROOT / args.run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    wheelhouse = run_dir / "wheelhouse"
    venv_dir = run_dir / "install_venv"
    steps: list[dict] = []

    # 1. Build the wheel from the frozen checkout.
    build = run([args.builder, "-m", "pip", "wheel", ".", "--no-deps", "-w", str(wheelhouse)])
    steps.append({"step": "wheel_build", **build})
    wheels = sorted(wheelhouse.glob("comsol_mcp-*.whl"))
    if build["exit_code"] != 0 or not wheels:
        write(run_dir, "run_status.json", {"status": "FAIL", "step": "wheel_build", "steps": steps})
        return 1
    wheel = wheels[-1]
    wheel_sha = hashlib.sha256(wheel.read_bytes()).hexdigest()

    # 2. Fresh interpreter outside the source tree.
    venv = run([args.builder, "-m", "venv", str(venv_dir)])
    steps.append({"step": "venv_create", **venv})
    py = venv_dir / "bin" / "python"
    if venv["exit_code"] != 0 or not py.exists():
        write(run_dir, "run_status.json", {"status": "FAIL", "step": "venv_create", "steps": steps})
        return 1

    # 3. Install the wheel with the machine constraints, then lock and check.
    constraints = ROOT / "constraints-macos-arm64-py314.txt"
    constraint_step = run([str(py), "-m", "pip", "install", "--upgrade", "-c", str(constraints), str(wheel)])
    steps.append({"step": "wheel_install_with_constraints", **constraint_step})
    if constraint_step["exit_code"] != 0:  # fall back to a plain install, recorded as such
        install = run([str(py), "-m", "pip", "install", "--upgrade", str(wheel)])
        steps.append({"step": "wheel_install_plain", **install})
    else:
        install = constraint_step
    freeze = run([str(py), "-m", "pip", "freeze"])
    write(run_dir, "install_freeze.txt", freeze["stdout"])
    check = run([str(py), "-m", "pip", "check"])
    write(run_dir, "pip_check.txt", check["stdout"] + check["stderr"])

    # 4. Import identity and packaged-resource inventory, with the checkout invisible.
    probe = run_dir / "probe_import.py"
    probe.write_text(
        "import importlib.metadata as md, importlib.resources as res, json, pathlib\n"
        "import comsol_mcp\n"
        "root = pathlib.Path(comsol_mcp.__file__).resolve().parent\n"
        "files = sorted(str(p.relative_to(pkg)) for p in pkg.rglob('*') if p.is_file())\n".replace("pkg", "res.files('comsol_mcp')")
        + "lower = [f.lower() for f in files]\n"
        "eps = {e.name: e.value for e in md.entry_points(group='console_scripts') if e.name == 'comsol-mcp'}\n"
        "print(json.dumps({'module': str(root), 'file_count': len(files),\n"
        "                  'java_sources': [f for f in files if f.endswith('.java')],\n"
        "                  'has_action_catalog': any(f.endswith('02_action_catalog.json') for f in lower),\n"
        "                  'has_schema': any('schema' in f and f.endswith('.json') for f in lower),\n"
        "                  'console_script': eps.get('comsol-mcp')}))\n"
    )
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("VIRTUAL_ENV", None)
    outside = run_dir.parent.parent.parent.parent.parent  # a directory outside the checkout
    ident = run([str(py), str(probe)], cwd=outside, env=env)
    steps.append({"step": "import_identity", **ident})
    identity = None
    if ident["exit_code"] == 0:
        try:
            identity = json.loads(ident["stdout"].strip().splitlines()[-1])
        except Exception:
            identity = None

    # 5. The console script's entry point must resolve inside the installed wheel.
    entry = run([str(py), "-c", "from comsol_mcp.mcp_server import main; print(callable(main))"], cwd=outside, env=env)
    steps.append({"step": "entry_point_import", **entry})

    site = str(venv_dir / "lib")
    installed_outside_source = bool(
        identity
        and identity.get("module", "").startswith(site)
        and identity.get("module", "") != str(ROOT / "comsol_mcp")
        and "/site-packages/" in identity.get("module", "")
    )
    status = (
        "PASS"
        if install["exit_code"] == 0
        and "No broken requirements found" in (check["stdout"] + check["stderr"])
        and installed_outside_source
        and ident["exit_code"] == 0
        and entry["exit_code"] == 0
        and identity
        and identity.get("java_sources")
        and identity.get("has_action_catalog")
        and identity.get("has_schema")
        and identity.get("console_script")
        else "FAIL"
    )

    write(
        run_dir,
        "run_status.json",
        {
            "schema": "comsol-mcp-g3/wheel-install-verification/1",
            "status": status,
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "wheel": {"path": wheel.relative_to(ROOT).as_posix(), "sha256": wheel_sha, "size": wheel.stat().st_size},
            "install_venv": venv_dir.relative_to(ROOT).as_posix(),
            "installed_outside_source": installed_outside_source,
            "import_identity": identity,
            "pip_check": (check["stdout"] + check["stderr"]).strip(),
            "step_exit_codes": {s["step"]: s["exit_code"] for s in steps},
            "steps": steps,
        },
    )
    print(f"wheel: {wheel.name} sha256={wheel_sha[:16]}… size={wheel.stat().st_size}")
    print(f"status: {status} | installed_outside_source={installed_outside_source}")
    if identity:
        print(f"module: {identity['module']}")
        print(f"packaged files: {identity['file_count']} | java sources: {len(identity['java_sources'])}")
    print(f"pip check: {(check['stdout'] + check['stderr']).strip()[:160]}")
    print(f"entry point import exit: {entry['exit_code']}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
