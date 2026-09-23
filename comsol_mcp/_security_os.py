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

    # Windows DACL: restrict to current user
    username = os.environ.get("USERNAME", "")
    if not username:
        return
    try:
        # /inheritance:r removes all inherited ACEs
        # /grant:r %USERNAME%:(OI)(CI)F grants full control to current user with inheritance
        subprocess.run(
            ["icacls", str(p), "/inheritance:r", "/grant:r", f"{username}:(OI)(CI)F"],
            capture_output=True, timeout=5, check=False,
        )
    except Exception:
        pass


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
