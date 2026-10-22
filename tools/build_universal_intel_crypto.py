#!/usr/bin/env python3
"""Plan or build the derived macOS x86_64 cryptography 50.0.1 wheel.

Supply a copied private input tree and a new output path. The tree must match
the paths in dependency-intel-build.json and contain its hash-pinned archives,
build wheels, private Rust toolchain/cache, and Maturin. Default mode only
checks inputs and prints commands. --build runs an offline build; it never
installs system software, modifies inputs, runs COMSOL, or certifies Intel
hardware. Python 3.12+ and the pinned arm64 macOS 27.0 SDK are required.

The input tree includes downloads/cryptography-50.0.1.tar.gz,
downloads/openssl-4.0.3.tar.gz, both python-build CPython 3.12.14 archives,
downloads/python-build-tools.json, wheelhouse/build-tools/, the private
toolchain/cargo/registry and toolchain/rustup tree, and the pinned Maturin
binary. Pinned artifact/manifest hashes and tool versions come from the
evidence JSON; Cargo packages remain governed by the checked Cargo.lock.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import plistlib
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


SCHEMA = "universal-dependency-intel-build/1"
TARGET = "x86_64-apple-darwin"
TOOLCHAIN = "stable-aarch64-apple-darwin"
MIN_MACOS = "12.0"
DEFAULT_EVIDENCE = Path(__file__).resolve().parents[1] / "docs/universal_mcp_execution/evidence/dependency-intel-build.json"


class BuildError(RuntimeError):
    pass


def source_layout(out: Path, stage: str, archive_root: str) -> tuple[Path, Path]:
    """Return the extraction staging directory and the archive's source root."""
    staging = out / "src" / stage
    return staging, staging / archive_root


def assert_no_home_redirect(env: dict[str, str], step: str) -> None:
    redirected = [key for key in env if key.casefold() in {"home", "codex_home"}]
    if redirected:
        raise BuildError(f"{step} must not override HOME/CODEX_HOME: {redirected}")


def assert_output_scoped_env(env: dict[str, str], out: Path, step: str, required: tuple[str, ...] = ()) -> None:
    assert_no_home_redirect(env, step)
    missing = [key for key in required if key not in env]
    if missing:
        raise BuildError(f"{step} is missing private environment paths: {missing}")
    output_root = out.resolve()
    for key in ("RUSTUP_HOME", "CARGO_HOME", "CARGO_TARGET_DIR", "PIP_CACHE_DIR", "TMPDIR"):
        if key in env:
            value = Path(env[key])
            if not value.is_absolute() or not value.resolve().is_relative_to(output_root):
                raise BuildError(f"{step} {key} must be inside the fresh output tree")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def input_rel(value: str) -> Path:
    p = PurePosixPath(value)
    if p.is_absolute() or ".." in p.parts or "\\" in value:
        raise BuildError(f"unsafe evidence path: {value}")
    try:
        i = p.parts.index("universal-intel-crypto")
    except ValueError as e:
        raise BuildError(f"path is outside the private input tree: {value}") from e
    if i + 1 == len(p.parts):
        raise BuildError(f"evidence path is not a file: {value}")
    return Path(*p.parts[i + 1 :])


def get_input(root: Path, relative: Path) -> Path:
    p = root
    for part in relative.parts:
        p = p / part
        if p.is_symlink():
            raise BuildError(f"input symlink rejected: {relative}")
    if not p.is_file():
        raise BuildError(f"missing input: {relative}")
    return p


def run(argv: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    r = subprocess.run(argv, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode:
        raise BuildError(f"command failed ({r.returncode}): {argv!r}\n{r.stdout}")
    return r.stdout.strip()


def load_pins(evidence_path: Path, root: Path) -> tuple[dict, dict[str, Path], list[dict], list[str], dict]:
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise BuildError(f"cannot read evidence: {e}") from e
    if evidence.get("schema") != SCHEMA:
        raise BuildError(f"unsupported evidence schema: {evidence.get('schema')!r}")
    rows = {r["name"]: r for r in evidence.get("verified_inputs", [])}
    required = {
        "cryptography source", "cryptography Cargo.lock", "OpenSSL source", "Maturin",
        "Rustup installer", "Rust toolchain", "Python Build Standalone", "private Python build tools",
    }
    if not required.issubset(rows):
        raise BuildError(f"evidence missing inputs: {sorted(required - rows.keys())}")
    paths: dict[str, Path] = {}
    fingerprints: list[dict] = []

    def checked(key: str, rel: Path, expected: str, size: int | None = None) -> Path:
        p = get_input(root, rel)
        got = sha256(p)
        if got != expected or (size is not None and p.stat().st_size != size):
            raise BuildError(f"input fingerprint mismatch: {rel}")
        fingerprints.append({"path": rel.as_posix(), "sha256": got, "bytes": p.stat().st_size})
        paths[key] = p
        return p

    crypt, openssl = rows["cryptography source"], rows["OpenSSL source"]
    maturin, rustup, rust = rows["Maturin"], rows["Rustup installer"], rows["Rust toolchain"]
    py, build_tools = rows["Python Build Standalone"], rows["private Python build tools"]
    checked("crypt_sdist", input_rel(crypt["local_path"]), crypt["sha256"], crypt["bytes"])
    checked("openssl_sdist", input_rel(openssl["local_path"]), openssl["sha256"])
    checked("maturin_archive", input_rel(maturin["local_archive"]), maturin["archive_sha256"])
    checked("maturin", input_rel(maturin["extracted_binary"]), maturin["extracted_binary_sha256"])
    checked("rustup_installer", input_rel(rustup["local_path"]), rustup["sha256"])
    checked("python_arm", input_rel(py["host_arm64_archive"]), py["host_arm64_sha256"])
    checked("python_x86", input_rel(py["target_x86_64_archive"]), py["target_x86_64_sha256"])
    checked("tools_manifest", input_rel(build_tools["manifest"]), build_tools["manifest_sha256"])
    checked("rust_manifest", input_rel(rust["distribution_manifest"]["path"]), rust["distribution_manifest"]["sha256"])

    manifest = json.loads(paths["tools_manifest"].read_text(encoding="utf-8"))
    tools = {r["project"]: r for r in manifest.get("artifacts", [])}
    versions = build_tools.get("installed_versions", {})
    if set(tools) != {"pip", "setuptools", "cffi", "pycparser"} or set(tools) != set(versions):
        raise BuildError("unexpected build-tools manifest contents")
    requirements = []
    for name, row in sorted(tools.items()):
        if row["version"] != versions[name] or PurePosixPath(row["filename"]).name != row["filename"]:
            raise BuildError(f"build-tools metadata mismatch: {name}")
        p = checked(name, Path("wheelhouse/build-tools") / row["filename"], row["sha256"], row["bytes"])
        requirements.append(f"{name}=={row['version']} --hash=sha256:{sha256(p)}")

    rustup_home, cargo_home = (root / input_rel(p) for p in rust["private_homes"])
    toolchain = rustup_home / "toolchains" / TOOLCHAIN
    rustc, cargo = toolchain / "bin/rustc", toolchain / "bin/cargo"
    if not rustc.is_file() or not cargo.is_file() or not (toolchain / "lib/rustlib" / TARGET / "lib").is_dir():
        raise BuildError("pinned private Rust toolchain or x86_64 target is incomplete")
    if not (cargo_home / "registry").is_dir():
        raise BuildError("private Cargo registry cache is missing")
    with tempfile.TemporaryDirectory(prefix="universal-intel-crypto-probe-") as probe_tmp:
        probe_env = {
            "PATH": ":".join([str(toolchain / "bin"), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]),
            "RUSTUP_HOME": str(rustup_home),
            "CARGO_HOME": str(cargo_home),
            "RUSTUP_TOOLCHAIN": TOOLCHAIN,
            "TMPDIR": probe_tmp,
        }
        assert_no_home_redirect(probe_env, "private Rust/Maturin version probes")
        rustc_ver = run([str(rustc), "--version", "--verbose"], env=probe_env)
        cargo_ver = run([str(cargo), "--version"], env=probe_env)
        maturin_ver = run([str(paths["maturin"]), "--version"], env=probe_env)
    if not rustc_ver.splitlines()[0].startswith(f"rustc {rust['rustc']}") or not cargo_ver.startswith(f"cargo {rust['cargo']}"):
        raise BuildError("private Rust toolchain version differs from evidence")
    if maturin_ver != f"maturin {maturin['version']}":
        raise BuildError("private Maturin version differs from evidence")
    paths.update({"rustup_home": rustup_home, "cargo_home": cargo_home, "rust_toolchain": toolchain})
    paths["input_root"] = root
    paths["build_tools_dir"] = root / "wheelhouse/build-tools"
    meta = {
        "rust": rust, "rustc_version": rustc_ver.splitlines()[0], "cargo_version": cargo_ver,
        "maturin_version": maturin["version"], "cargo_lock_sha256": rows["cryptography Cargo.lock"]["sha256"],
        "minimum_macos": MIN_MACOS, "target": TARGET,
    }
    return evidence, paths, fingerprints, requirements, meta


def sdk_path(arg: Path | None, evidence: dict) -> tuple[Path, str]:
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise BuildError("the pinned private toolchain requires an arm64 macOS host")
    sdk = arg.expanduser().resolve() if arg else Path(run(["xcrun", "--sdk", "macosx", "--show-sdk-path"])).resolve()
    settings = sdk / "SDKSettings.plist"
    if not settings.is_file():
        raise BuildError(f"invalid macOS SDK: {sdk}")
    version = str(plistlib.loads(settings.read_bytes()).get("Version", ""))
    expected = str(evidence["build_environment"]["macos_sdk_version"])
    if version != expected or not Path("/usr/bin/clang").is_file() or not shutil.which("make"):
        raise BuildError(f"Command Line Tools SDK mismatch or unavailable: expected {expected}, got {version}")
    return sdk, version


def safe_extract(archive: Path, destination: Path, root_name: str) -> Path:
    destination.mkdir(parents=True)
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        roots = {PurePosixPath(m.name).parts[0] for m in members if PurePosixPath(m.name).parts}
        if roots != {root_name} or any(PurePosixPath(m.name).is_absolute() or ".." in PurePosixPath(m.name).parts for m in members):
            raise BuildError(f"unexpected or unsafe archive layout: {archive.name}")
        tar.extractall(destination, filter="data")
    return destination / root_name


def logged(out: Path, name: str, argv: list[str], cwd: Path, env: dict[str, str]) -> dict:
    log = out / "logs" / f"{name}.log"
    with log.open("x", encoding="utf-8") as f:
        f.write(f"argv={json.dumps(argv)}\ncwd={cwd}\n")
        f.write("environment=" + json.dumps(env, sort_keys=True) + "\n\n")
        f.flush()
        result = subprocess.run(argv, cwd=cwd, env=env, stdout=f, stderr=subprocess.STDOUT)
    row = {"step": name, "argv": argv, "cwd": cwd.relative_to(out).as_posix(), "log": log.relative_to(out).as_posix(), "exit_code": result.returncode}
    if result.returncode:
        raise BuildError(f"{name} failed ({result.returncode}); log retained at {row['log']}")
    return row


def commands(out: Path, root: Path, paths: dict, sdk: Path, jobs: int) -> list[dict]:
    py = out / "python-build/build-venv/bin/python"
    host_py = out / "python-build/host-archive/python/bin/python3.12"
    openssl = out / "openssl/x86_64-macos12"
    rust_home, cargo_home = out / "toolchain/rustup", out / "toolchain/cargo"
    rust_bin = rust_home / "toolchains" / TOOLCHAIN / "bin"
    sdk_s = str(sdk)
    cflags = f"-arch x86_64 -isysroot {sdk_s} -mmacosx-version-min={MIN_MACOS} -fPIC"
    private_env = {"TMPDIR": str(out / "tmp"), "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    openssl_env = private_env | {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "CC": "/usr/bin/clang", "SDKROOT": sdk_s, "MACOSX_DEPLOYMENT_TARGET": MIN_MACOS, "CFLAGS": cflags, "LDFLAGS": cflags.replace(" -fPIC", "")}
    rust_env = private_env | {
        "PATH": ":".join([str(py.parent), str(rust_bin), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]),
        "CARGO_HOME": str(cargo_home), "RUSTUP_HOME": str(rust_home), "RUSTUP_TOOLCHAIN": TOOLCHAIN,
        "CARGO_TARGET_DIR": str(out / "build/cryptography-target"), "CARGO_BUILD_JOBS": str(jobs), "CARGO_NET_OFFLINE": "true",
        "CARGO_TARGET_X86_64_APPLE_DARWIN_LINKER": "/usr/bin/clang", "MACOSX_DEPLOYMENT_TARGET": MIN_MACOS, "SDKROOT": sdk_s,
        "CC": "/usr/bin/clang", "CXX": "/usr/bin/clang++", "CFLAGS": cflags, "CXXFLAGS": cflags,
        "LDFLAGS": cflags.replace(" -fPIC", ""), "OPENSSL_DIR": str(openssl), "OPENSSL_STATIC": "1",
        "PYO3_PYTHON": str(py), "PYO3_CROSS": "1", "PYO3_CROSS_PYTHON_VERSION": "3.12",
        "PYO3_CROSS_PYTHON_IMPLEMENTATION": "CPython", "PYO3_CROSS_LIB_DIR": str(out / "python-build/target-archive/python/lib/python3.12"),
        "PIP_CACHE_DIR": str(out / "cache/pip"), "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
    }
    _, src = source_layout(out, "cryptography-src", "cryptography-50.0.1")
    plan = [
        {"step": "create private venv", "cwd": str(out), "argv": [str(host_py), "-m", "venv", str(out / "python-build/build-venv")], "environment": private_env},
        {"step": "install build wheels offline", "cwd": str(out), "argv": [str(py), "-m", "pip", "install", "--no-index", "--no-cache-dir", "--require-hashes", "--find-links", str(root / "wheelhouse/build-tools"), "-r", str(out / "build-tools-requirements.txt")], "environment": private_env | {"PIP_CACHE_DIR": str(out / "cache/pip"), "PIP_DISABLE_PIP_VERSION_CHECK": "1"}},
        {"step": "OpenSSL configure", "argv": ["./Configure", "darwin64-x86_64-cc", "no-shared", "no-module", "no-tests", f"--prefix={openssl}", f"--openssldir={openssl}/ssl"], "cwd": str(out / "build/openssl"), "environment": openssl_env},
        {"step": "OpenSSL compile", "cwd": str(out / "build/openssl"), "argv": ["make", f"-j{jobs}"], "environment": openssl_env},
        {"step": "OpenSSL private install", "cwd": str(out / "build/openssl"), "argv": ["make", "install_sw"], "environment": openssl_env},
        {"step": "cryptography wheel", "cwd": str(src), "argv": [str(out / "toolchain/maturin"), "build", "--release", "--target", TARGET, "--interpreter", str(py), "--locked", "--out", str(out / "wheelhouse/macos_x86_64")], "environment": rust_env},
    ]
    expected_source = source_layout(out, "cryptography-src", "cryptography-50.0.1")[1]
    wheel_steps = [row for row in plan if row["step"] == "cryptography wheel"]
    if len(wheel_steps) != 1 or Path(wheel_steps[0]["cwd"]) != expected_source:
        raise BuildError("planned cryptography cwd differs from the source extraction layout")
    required_paths = {
        "create private venv": ("TMPDIR",),
        "install build wheels offline": ("TMPDIR", "PIP_CACHE_DIR"),
        "OpenSSL configure": ("TMPDIR",),
        "OpenSSL compile": ("TMPDIR",),
        "OpenSSL private install": ("TMPDIR",),
        "cryptography wheel": ("TMPDIR", "RUSTUP_HOME", "CARGO_HOME", "CARGO_TARGET_DIR", "PIP_CACHE_DIR"),
    }
    for row in plan:
        assert_output_scoped_env(row["environment"], out, row["step"], required_paths[row["step"]])
    return plan


def build(args, out: Path, evidence: dict, paths: dict, fingerprints: list[dict], requirements: list[str], meta: dict, sdk: Path, sdk_ver: str) -> dict:
    out.mkdir(parents=True, exist_ok=False)
    for d in ("logs", "src", "build", "python-build", "toolchain", "openssl", "wheelhouse/macos_x86_64", "cache/pip", "tmp"):
        (out / d).mkdir(parents=True, exist_ok=True)
    crypt_stage, expected_crypt_source = source_layout(out, "cryptography-src", "cryptography-50.0.1")
    crypt = safe_extract(paths["crypt_sdist"], crypt_stage, "cryptography-50.0.1")
    if crypt != expected_crypt_source:
        raise BuildError("extracted cryptography source differs from the shared plan layout")
    openssl_stage, _ = source_layout(out, "openssl-src", "openssl-4.0.3")
    openssl_src = safe_extract(paths["openssl_sdist"], openssl_stage, "openssl-4.0.3")
    if sha256(crypt / "Cargo.lock") != meta["cargo_lock_sha256"]:
        raise BuildError("extracted Cargo.lock SHA-256 differs from evidence")
    host_top = safe_extract(paths["python_arm"], out / "python-build/host-archive", "python")
    target_top = safe_extract(paths["python_x86"], out / "python-build/target-archive", "python")
    host_python = host_top / "bin/python3.12"
    if not host_python.is_file() or not (target_top / "lib/python3.12").is_dir():
        raise BuildError("standalone CPython 3.12 archives have an unexpected layout")
    rust_home = out / "toolchain/rustup"
    shutil.copytree(paths["rustup_home"], rust_home, symlinks=True)
    cargo_home = out / "toolchain/cargo"
    shutil.copytree(paths["cargo_home"] / "registry", cargo_home / "registry", symlinks=True)
    shutil.copy2(paths["maturin"], out / "toolchain/maturin")
    (out / "toolchain/maturin").chmod((out / "toolchain/maturin").stat().st_mode | stat.S_IXUSR)

    env0 = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "TMPDIR": str(out / "tmp"), "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    assert_output_scoped_env(env0, out, "build base environment", ("TMPDIR",))
    venv = out / "python-build/build-venv"
    steps = [logged(out, "create-venv", [str(host_python), "-m", "venv", str(venv)], out, env0)]
    req = out / "build-tools-requirements.txt"
    req.write_text("\n".join(requirements) + "\n", encoding="utf-8", newline="\n")
    pip_env = env0 | {"PIP_CACHE_DIR": str(out / "cache/pip"), "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    assert_output_scoped_env(pip_env, out, "private pip install environment", ("TMPDIR", "PIP_CACHE_DIR"))
    steps.append(logged(out, "install-build-tools", [str(venv / "bin/python"), "-m", "pip", "install", "--no-index", "--no-cache-dir", "--require-hashes", "--find-links", str(paths["build_tools_dir"]), "-r", str(req)], out, pip_env))

    openssl_build = out / "build/openssl"
    shutil.copytree(openssl_src, openssl_build, symlinks=True)
    openssl_prefix = out / "openssl/x86_64-macos12"
    sdk_s = str(sdk)
    cflags = f"-arch x86_64 -isysroot {sdk_s} -mmacosx-version-min={MIN_MACOS} -fPIC"
    openssl_env = env0 | {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "CC": "/usr/bin/clang", "SDKROOT": sdk_s, "MACOSX_DEPLOYMENT_TARGET": MIN_MACOS, "CFLAGS": cflags, "LDFLAGS": cflags.replace(" -fPIC", "")}
    assert_output_scoped_env(openssl_env, out, "private OpenSSL environment", ("TMPDIR",))
    steps.append(logged(out, "openssl-configure", ["./Configure", "darwin64-x86_64-cc", "no-shared", "no-module", "no-tests", f"--prefix={openssl_prefix}", f"--openssldir={openssl_prefix}/ssl"], openssl_build, openssl_env))
    steps.append(logged(out, "openssl-make", ["make", f"-j{args.jobs}"], openssl_build, openssl_env))
    steps.append(logged(out, "openssl-install", ["make", "install_sw"], openssl_build, openssl_env))

    rust_bin = rust_home / "toolchains" / TOOLCHAIN / "bin"
    crypt_env = env0 | {
        "PATH": ":".join([str(venv / "bin"), str(rust_bin), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]),
        "CARGO_HOME": str(cargo_home), "RUSTUP_HOME": str(rust_home), "RUSTUP_TOOLCHAIN": TOOLCHAIN,
        "CARGO_TARGET_DIR": str(out / "build/cryptography-target"), "CARGO_BUILD_JOBS": str(args.jobs), "CARGO_NET_OFFLINE": "true",
        "CARGO_TARGET_X86_64_APPLE_DARWIN_LINKER": "/usr/bin/clang", "MACOSX_DEPLOYMENT_TARGET": MIN_MACOS, "SDKROOT": sdk_s,
        "CC": "/usr/bin/clang", "CXX": "/usr/bin/clang++", "CFLAGS": cflags, "CXXFLAGS": cflags,
        "LDFLAGS": cflags.replace(" -fPIC", ""), "OPENSSL_DIR": str(openssl_prefix), "OPENSSL_STATIC": "1",
        "PYO3_PYTHON": str(venv / "bin/python"), "PYO3_CROSS": "1", "PYO3_CROSS_PYTHON_VERSION": "3.12",
        "PYO3_CROSS_PYTHON_IMPLEMENTATION": "CPython", "PYO3_CROSS_LIB_DIR": str(target_top / "lib/python3.12"),
        "PIP_CACHE_DIR": str(out / "cache/pip"),
    }
    assert_output_scoped_env(crypt_env, out, "private cryptography build environment", ("TMPDIR", "RUSTUP_HOME", "CARGO_HOME", "CARGO_TARGET_DIR", "PIP_CACHE_DIR"))
    wheel_dir = out / "wheelhouse/macos_x86_64"
    steps.append(logged(out, "cryptography-build", [str(out / "toolchain/maturin"), "build", "--release", "--target", TARGET, "--interpreter", str(venv / "bin/python"), "--locked", "--out", str(wheel_dir)], crypt, crypt_env))
    if sha256(crypt / "Cargo.lock") != meta["cargo_lock_sha256"]:
        raise BuildError("build changed Cargo.lock despite --locked")
    wheels = list(wheel_dir.glob("cryptography-50.0.1-*.whl"))
    if len(wheels) != 1:
        raise BuildError(f"expected one cryptography 50.0.1 wheel, got {len(wheels)}")
    wheel = wheels[0]
    with zipfile.ZipFile(wheel) as z:
        wn = next(n for n in z.namelist() if n.endswith(".dist-info/WHEEL"))
        mn = next(n for n in z.namelist() if n.endswith(".dist-info/METADATA"))
        if "Tag: cp312-abi3-macosx_12_0_x86_64" not in z.read(wn).decode() or "Version: 50.0.1\n" not in z.read(mn).decode():
            raise BuildError("wheel metadata does not match the pinned target/package")
    receipt = {
        "schema": "universal-intel-cryptography-rebuild/1", "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "BUILT_FROM_PINNED_INPUTS; ACCEPTANCE_NOT_IMPLIED", "input_fingerprints": fingerprints,
        "rustc": meta["rustc_version"], "cargo": meta["cargo_version"], "target": TARGET,
        "sdk_version": sdk_ver, "minimum_macos": MIN_MACOS, "commands": steps,
        "wheel": {"path": wheel.relative_to(out).as_posix(), "bytes": wheel.stat().st_size, "sha256": sha256(wheel), "tag": "cp312-abi3-macosx_12_0_x86_64"},
        "functional_check": "NOT_RUN_BY_HELPER", "intel_native_hardware_acceptance": "NOT_RUN_USER_REQUESTED_SKIP", "comsol": "NOT_RUN",
    }
    with (out / "build-provenance.json").open("x", encoding="utf-8") as f:
        json.dump(receipt, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return receipt


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--inputs-dir", type=Path, required=True, help="caller-supplied private inputs tree")
    p.add_argument("--output-dir", type=Path, required=True, help="fresh, non-existing output path")
    p.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE, help="fixed source/toolchain SHA provenance JSON")
    p.add_argument("--sdkroot", type=Path, help="optional pinned SDK path; defaults to xcrun")
    p.add_argument("--jobs", type=int, default=4)
    p.add_argument("--build", action="store_true", help="execute; default is plan/validation only")
    a = p.parse_args()
    try:
        if sys.version_info < (3, 12) or a.jobs not in range(1, 65):
            raise BuildError("require Python >=3.12 and --jobs in [1,64]")
        root = a.inputs_dir.expanduser().resolve(strict=True)
        if not root.is_dir():
            raise BuildError("--inputs-dir must be a directory")
        out = a.output_dir.expanduser()
        if out.exists() or out.is_symlink():
            raise BuildError(f"output must not exist (no overwrite): {out}")
        out = out.resolve()
        try:
            out.relative_to(root)
            raise BuildError("output cannot be inside the input tree")
        except ValueError:
            pass
        try:
            root.relative_to(out)
            raise BuildError("output cannot contain the input tree")
        except ValueError:
            pass
        evidence_path = a.evidence.expanduser().resolve(strict=True)
        evidence, paths, fingerprints, requirements, meta = load_pins(evidence_path, root)
        sdk, sdk_ver = sdk_path(a.sdkroot, evidence)
        if a.build:
            result = build(a, out, evidence, paths, fingerprints, requirements, meta, sdk, sdk_ver)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            print(f"provenance={out / 'build-provenance.json'}")
        else:
            plan = {
                "schema": "universal-intel-cryptography-build-plan/1", "status": "PLAN_ONLY_NO_OUTPUT_CREATED",
                "input_fingerprints": fingerprints, "rustc": meta["rustc_version"], "cargo": meta["cargo_version"],
                "build_tool_requirements": requirements,
                "sdkroot": str(sdk), "sdk_version": sdk_ver, "minimum_macos": MIN_MACOS,
                "preparation": ["hash-verify inputs", "extract source/runtime archives into output", "copy pinned private Rust/Cargo caches into output"],
                "commands": commands(out, root, paths, sdk, a.jobs),
                "build_invocation": [sys.executable, str(Path(__file__).resolve()), "--inputs-dir", str(root), "--output-dir", str(out), "--evidence", str(evidence_path), "--jobs", str(a.jobs), "--build"],
                "clean_rebuild_performed": False,
                "functional_check": "NOT_RUN_BY_HELPER", "intel_native_hardware_acceptance": "NOT_RUN_USER_REQUESTED_SKIP",
            }
            print(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0
    except (BuildError, OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, tarfile.TarError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
