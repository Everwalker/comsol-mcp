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
        # ACE entries always contain the permission specifier ':('
        ace_lines = [line for line in lines if ":(" in line]
        unexpected_aces = []
        short_user = account_name.split("\\")[-1].lower() if "\\" in account_name else account_name.lower()
        # Legitimate Windows OS and administrative principals that retain rights on private directories
        allowed_system_principals = {
            "nt authority\\system",
            "builtin\\administrators",
            "owner rights",
        }
        for ace_line in ace_lines:
            ace_identity = ace_line.split(":(")[0].lower()
            matched = (
                user_sid.lower() in ace_identity
                or f"*{user_sid}".lower() in ace_identity
                or account_name.lower() in ace_identity
                or short_user in ace_identity
                or any(sys_p in ace_identity for sys_p in allowed_system_principals)
            )
            if not matched:
                unexpected_aces.append(ace_line)
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
