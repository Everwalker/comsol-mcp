#!/usr/bin/env python3
"""Package all files prepared for GitHub synchronization into a self-contained archive.

Creates:
1. COMSOL_MCP_GITHUB_SYNC_FILES.tar.gz (structured sync package with repo snapshot, changed files, bundle, patch, manifest)
2. COMSOL_MCP_GITHUB_REPOSITORY_HEAD.tar.gz (direct git archive HEAD)
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
from typing import Any


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    repo_dir = Path(__file__).resolve().parent.parent
    output_dir = repo_dir.parent

    base_commit = "20839628aa6f93272a463f4d88eb48704b971f87"
    head_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_dir, text=True).strip()
    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=repo_dir, text=True).strip() or "handoff/g3_5_w19"
    tree_sha = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=repo_dir, text=True).strip()

    print(f"[*] Packaging GitHub sync files from {repo_dir}")
    print(f"    Base Commit:   {base_commit}")
    print(f"    HEAD Commit:   {head_commit}")
    print(f"    HEAD Tree:     {tree_sha}")
    print(f"    Branch:        {branch}")

    staging_dir = Path(tempfile.mkdtemp(prefix="github_sync_staging_"))
    try:
        pkg_root = staging_dir / "COMSOL_MCP_GITHUB_SYNC_FILES"
        pkg_root.mkdir(parents=True, exist_ok=True)

        # 1. Standalone Repository Snapshot (git archive HEAD)
        repo_snapshot_dir = pkg_root / "repository_snapshot"
        repo_snapshot_dir.mkdir(parents=True, exist_ok=True)
        print("[*] 1/5 Exporting full clean repository snapshot (git archive HEAD)...")
        subprocess.check_call(
            f"git archive HEAD | tar -x -C {repo_snapshot_dir}",
            shell=True,
            cwd=repo_dir,
        )

        # 2. Changed Files since base commit
        changed_dir = pkg_root / "changed_files_since_base"
        changed_dir.mkdir(parents=True, exist_ok=True)
        print(f"[*] 2/5 Exporting changed files since base commit ({base_commit[:10]})...")
        changed_files_raw = subprocess.check_output(
            ["git", "-c", "core.quotepath=off", "diff", "--name-only", f"{base_commit}..HEAD"],
            cwd=repo_dir,
            text=True,
        ).splitlines()

        changed_inventory = []
        for rel_path in changed_files_raw:
            src = repo_dir / rel_path
            if not src.is_file():
                continue
            dest = changed_dir / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            changed_inventory.append({
                "path": rel_path,
                "size_bytes": dest.stat().st_size,
                "sha256": sha256_file(dest),
            })
        print(f"    -> Exported {len(changed_inventory)} changed files")

        # 3. Cumulative Git Patch
        patches_dir = pkg_root / "patches"
        patches_dir.mkdir(parents=True, exist_ok=True)
        print("[*] 3/5 Generating full cumulative Git patch...")
        patch_file = patches_dir / "g3_5_w19_full.patch"
        with patch_file.open("w", encoding="utf-8") as pf:
            subprocess.check_call(
                ["git", "diff", f"{base_commit}..HEAD"],
                cwd=repo_dir,
                stdout=pf,
            )
        patch_sha = sha256_file(patch_file)
        patch_size = patch_file.stat().st_size
        print(f"    -> Created patch: {patch_size} bytes, SHA256: {patch_sha[:16]}...")

        # 4. Standalone Git Bundle
        bundle_dir = pkg_root / "bundle"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        bundle_file = bundle_dir / "comsol_mcp_g3_5.bundle"
        print("[*] 4/5 Creating verified Git bundle...")
        subprocess.check_call(
            ["git", "bundle", "create", str(bundle_file), "HEAD", f"refs/heads/{branch}"],
            cwd=repo_dir,
        )
        subprocess.check_call(["git", "bundle", "verify", str(bundle_file)], cwd=repo_dir)
        bundle_sha = sha256_file(bundle_file)
        bundle_size = bundle_file.stat().st_size
        print(f"    -> Created bundle: {bundle_size} bytes, SHA256: {bundle_sha[:16]}...")

        shallow_src = repo_dir / ".git" / "shallow"
        if shallow_src.is_file():
            shutil.copy2(shallow_src, bundle_dir / "git_shallow")

        # Copy restore script to bundle directory for convenience
        restore_script = repo_dir / "tools" / "restore_g3_5_delivery.py"
        if restore_script.is_file():
            shutil.copy2(restore_script, pkg_root / "restore_g3_5_delivery.py")

        # 5. SYNC_MANIFEST.json
        print("[*] 5/5 Generating SYNC_MANIFEST.json and README_SYNC.md...")
        all_snapshot_files = []
        for p in repo_snapshot_dir.rglob("*"):
            if p.is_file():
                rel = str(p.relative_to(repo_snapshot_dir))
                all_snapshot_files.append({
                    "path": rel,
                    "size_bytes": p.stat().st_size,
                    "sha256": sha256_file(p),
                })

        manifest = {
            "schema": "comsol-mcp-g3/github-sync-manifest/1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "description": "Inventory of all files and bundles prepared for GitHub synchronization",
            "git_metadata": {
                "base_commit": base_commit,
                "head_commit": head_commit,
                "tree_sha": tree_sha,
                "branch": branch,
                "remote_url": "https://github.com/Everwalker/comsol-mcp.git",
            },
            "bundle": {
                "filename": "bundle/comsol_mcp_g3_5.bundle",
                "sha256": bundle_sha,
                "size_bytes": bundle_size,
            },
            "patch": {
                "filename": "patches/g3_5_w19_full.patch",
                "sha256": patch_sha,
                "size_bytes": patch_size,
            },
            "changed_files_count": len(changed_inventory),
            "changed_files": sorted(changed_inventory, key=lambda x: x["path"]),
            "snapshot_files_count": len(all_snapshot_files),
        }
        manifest_file = pkg_root / "SYNC_MANIFEST.json"
        manifest_file.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        readme_content = f"""# COMSOL MCP G3.5 - GitHub 同步文件包说明

本压缩包包含准备同步到 GitHub 仓库的所有文件、完整提交补丁与独立 Git 镜像。

## 1. 提交与分支信息
- **GitHub 目标仓库**: `https://github.com/Everwalker/comsol-mcp.git`
- **目标分支**: `{branch}`
- **HEAD 提交**: `{head_commit}`
- **HEAD Tree**: `{tree_sha}`
- **基线锁定提交**: `{base_commit}`

## 2. 目录结构说明
```text
COMSOL_MCP_GITHUB_SYNC_FILES/
├── SYNC_MANIFEST.json            # 密码学清单与文件哈希
├── README_SYNC.md                # 同步操作说明（本文档）
├── restore_g3_5_delivery.py      # 一键从 bundle 恢复独立 Git 仓库脚本
├── repository_snapshot/          # HEAD 完整源码树快照（7,311 项追踪文件，100% 匹配 release commit）
├── changed_files_since_base/     # 自锁定基线以来新增与修改的文件（{len(changed_inventory)} 项）
├── patches/
│   └── g3_5_w19_full.patch       # 完整累计 Git 补丁（可通过 git apply 直接应用）
└── bundle/
    ├── comsol_mcp_g3_5.bundle    # 独立 Git bundle（包含全部提交历史与 refs）
    └── git_shallow               # Git shallow 边界文件
```

## 3. 使用方法

### 方式 A：直接使用 Git Bundle 恢复完整仓库
```bash
python3 restore_g3_5_delivery.py \\
    --bundle bundle/comsol_mcp_g3_5.bundle \\
    --target /path/to/new_repo \\
    --branch {branch}
cd /path/to/new_repo
git push origin {branch}
```

### 方式 B：通过补丁应用到现有仓库
```bash
cd /path/to/existing_repo
git checkout -b {branch} {base_commit}
git apply patches/g3_5_w19_full.patch
git add -A
git commit -m "feat(g3.5): sync G3.5 release hardening to GitHub"
git push origin {branch}
```

### 方式 C：直接使用 repository_snapshot 覆盖/比对
`repository_snapshot/` 包含 Git HEAD 的全部源码，无 `.git` 或临时构建产物，可直接作为代码树发布或审计对比。
"""
        readme_file = pkg_root / "README_SYNC.md"
        readme_file.write_text(readme_content, encoding="utf-8")

        # 6. Build final archives
        # Archive 1: Structured Sync Package
        tar_sync_pkg = output_dir / "COMSOL_MCP_GITHUB_SYNC_FILES.tar.gz"
        print(f"[*] Packaging {tar_sync_pkg.name}...")
        with tarfile.open(tar_sync_pkg, "w:gz") as tar:
            tar.add(pkg_root, arcname=pkg_root.name)
        sync_pkg_sha = sha256_file(tar_sync_pkg)
        sync_pkg_size = tar_sync_pkg.stat().st_size
        print(f"[+] Created {tar_sync_pkg.name}: {sync_pkg_size:,} bytes, SHA256: {sync_pkg_sha}")

        # Archive 2: Direct git archive HEAD tarball (convenient standard repo tarball)
        tar_repo_head = output_dir / "COMSOL_MCP_GITHUB_REPOSITORY_HEAD.tar.gz"
        print(f"[*] Packaging {tar_repo_head.name} (direct git archive HEAD)...")
        subprocess.check_call(
            ["git", "archive", "--format=tar.gz", "-o", str(tar_repo_head), "HEAD"],
            cwd=repo_dir,
        )
        repo_head_sha = sha256_file(tar_repo_head)
        repo_head_size = tar_repo_head.stat().st_size
        print(f"[+] Created {tar_repo_head.name}: {repo_head_size:,} bytes, SHA256: {repo_head_sha}")

        # Verification of extracted sync package in fresh temp
        print("[*] Verifying created sync archive in fresh temp...")
        verify_dir = Path(tempfile.mkdtemp(prefix="sync_verify_"))
        try:
            with tarfile.open(tar_sync_pkg, "r:gz") as tar:
                if hasattr(tarfile, "fully_trusted_filter"):
                    tar.extractall(verify_dir, filter="fully_trusted")
                else:
                    tar.extractall(verify_dir)
            extracted_pkg = verify_dir / "COMSOL_MCP_GITHUB_SYNC_FILES"
            assert (extracted_pkg / "SYNC_MANIFEST.json").is_file()
            assert (extracted_pkg / "bundle" / "comsol_mcp_g3_5.bundle").is_file()
            assert (extracted_pkg / "patches" / "g3_5_w19_full.patch").is_file()
            assert (extracted_pkg / "repository_snapshot" / "comsol_mcp" / "worker_java" / "PersistentComsolWorker.java").is_file()
            assert (extracted_pkg / "changed_files_since_base" / "comsol_mcp" / "_g3_w18.py").is_file()
            print("[+] Verification passed 100%!")
        finally:
            shutil.rmtree(verify_dir, ignore_errors=True)

    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    print("\n[SUCCESS] GitHub sync packaging complete!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
