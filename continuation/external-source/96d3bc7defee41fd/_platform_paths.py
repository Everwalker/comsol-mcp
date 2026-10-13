"""Platform-scoped paths for optional COMSOL installation resources."""
from __future__ import annotations

from collections.abc import Callable, Mapping
import os
from pathlib import Path
import sys


# Installation discovery is intentionally limited to the paths documented for
# this macOS deployment.  Other platforms must provide COMSOL_DOCS_ROOT rather
# than receiving guessed installation locations.
MAC_COMSOL_HELP_ROOTS = (
    Path("/Applications/COMSOL64/Multiphysics/doc"),
    Path("/Applications/COMSOL63/Multiphysics/doc"),
)


def default_comsol_help_roots(
    project_root: str | Path,
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    path_exists: Callable[[Path], bool] | None = None,
) -> list[Path]:
    """Return approved project/configured help roots for the current platform.

    ``COMSOL_DOCS_ROOT`` is the portable configuration point.  The two
    installation paths are considered only on Darwin, where they reflect the
    supported local Mac installation layout.  ``path_exists`` is a small test
    seam so platform behavior can be checked without manufacturing an
    installation on another host.
    """
    env = os.environ if environ is None else environ
    roots = [Path(project_root)]
    configured = env.get("COMSOL_DOCS_ROOT")
    if configured:
        roots.append(Path(configured))

    platform_name = (sys.platform if platform is None else platform).lower()
    if platform_name.startswith("darwin"):
        exists = path_exists or (lambda path: path.exists())
        roots.extend(path for path in MAC_COMSOL_HELP_ROOTS if exists(path))
    return roots
