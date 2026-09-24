"""OS-level security, private DACL permissions, and filesystem boundaries (D06)."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from ._platform_process import validate_windows_path_security


def set_private_directory_permissions(path: Path | str) -> None:
    """Set minimal necessary private permissions on a directory (0700 / Windows DACL).

    On POSIX: os.chmod(0700).
    On Windows: uses icacls to remove inherited permissions and grant Full Control only
    to the current user, preventing other local users from accessing private run state.
    """
    p = Path(path).resolve()
    p.mkdir(parents=True, exist_ok=True)

    if sys.platform != "win32" and os.name != "nt":
        try:
            os.chmod(p, 0o700)
        except OSError:
            pass
        return

    # Windows DACL: restrict to current user via trusted SID
    # F03: Query the actual SID from the OS token, not the spoofable USERNAME env var.
    try:
        import csv
        import io
        sid_proc = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            capture_output=True, timeout=10, check=True,
            text=True,
        )
        reader = csv.reader(io.StringIO(sid_proc.stdout.strip()))
        row = next(reader, None)
        if not row or len(row) < 2:
            raise PermissionError(
                f"Cannot determine current user SID for private directory {p}; "
                "refusing to publish secret endpoints without verified DACL"
            )
        account_name = row[0].strip()
        user_sid = row[1].strip()
        if not user_sid.startswith("S-1-"):
            raise PermissionError(
                f"Invalid SID format '{user_sid}' for private directory {p}"
            )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, StopIteration) as exc:
        raise PermissionError(
            f"Cannot query current user SID for private directory {p}: {exc}"
        ) from exc

    # Apply DACL: remove inherited ACEs, grant full control only to trusted SID
    try:
        result = subprocess.run(
            ["icacls", str(p), "/inheritance:r", "/grant:r", f"*{user_sid}:(OI)(CI)F"],
            capture_output=True, timeout=10, text=True,
        )
        if result.returncode != 0:
            raise PermissionError(
                f"icacls failed (exit {result.returncode}) setting DACL on {p}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise PermissionError(
            f"icacls execution failed for private directory {p}: {exc}"
        ) from exc

    # Readback: verify the DACL actually contains only our SID/account
    try:
        readback = subprocess.run(
            ["icacls", str(p)],
            capture_output=True, timeout=10, text=True,
        )
        if readback.returncode != 0:
            raise PermissionError(
                f"icacls DACL readback failed (exit {readback.returncode}) on {p}"
            )
        lines = [line.strip() for line in readback.stdout.splitlines() if line.strip()]
        ace_lines = []
        unexpected_aces = []
        found_user_ace = False

        # Legitimate Windows OS and administrative principals that retain rights on private directories
        allowed_system_principals = {
            "nt authority\\system",
            "builtin\\administrators",
            "owner rights",
            "s-1-5-18",
            "s-1-5-32-544",
            "s-1-3-4",
        }
        user_identities = {
            user_sid.lower(),
            f"*{user_sid}".lower(),
            account_name.lower(),
        }

        p_str = str(p).lower()
        p_res = str(p.resolve()).lower()
        p_name = p.name.lower()

        for raw_line in lines:
            if ":(" not in raw_line:
                continue
            line = raw_line.strip()
            # Strip path prefix if icacls prepended it on the first line
            for prefix in (p_res, p_str, p_name):
                if line.lower().startswith(prefix):
                    line = line[len(prefix):].strip()
                    break
            if ":(" not in line:
                continue

            ace_lines.append(raw_line)
            ace_identity = line.split(":(")[0].strip().lower()
            perms = line[len(ace_identity):].upper()

            # Exact trustee matching only (no substring matching, preventing spoofing or directory name match)
            if ace_identity in user_identities:
                if "(F)" in perms or ":F" in perms:
                    found_user_ace = True
            elif ace_identity in allowed_system_principals:
                pass
            else:
                unexpected_aces.append(raw_line)

        if not ace_lines:
            raise PermissionError(
                f"No ACEs could be parsed from icacls output for {p}; possible NULL DACL or empty permissions"
            )
        if not found_user_ace:
            raise PermissionError(
                f"Required user ACE granting Full Control for SID {user_sid} ({account_name}) not found on {p}"
            )
        if unexpected_aces:
            raise PermissionError(
                f"Unexpected ACEs found on private directory {p} after DACL set; "
                f"only SID {user_sid} ({account_name}) or system principals should have access. "
                f"Unexpected: {unexpected_aces}"
            )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise PermissionError(
            f"DACL readback verification failed for {p}: {exc}"
        ) from exc


def validate_path_boundaries(
    target_path: Path | str,
    *,
    allowed_root: Path | str | None = None,
    allow_unc: bool = False,
) -> Path:
    """Validate path safety against Windows device names, ADS, UNC, and traversal."""
    p_str = str(target_path)
    if not allow_unc and (p_str.startswith(("\\\\", "//"))):
        raise ValueError(f"UNC network paths are rejected: {p_str}")

    validate_windows_path_security(p_str)

    p = Path(target_path)
    if allowed_root is not None:
        root = Path(allowed_root).resolve()
        try:
            resolved = p.resolve()
            resolved.relative_to(root)
        except (ValueError, OSError) as exc:
            raise ValueError(f"Path escapes allowed boundary root {root}: {p}") from exc
    return p
