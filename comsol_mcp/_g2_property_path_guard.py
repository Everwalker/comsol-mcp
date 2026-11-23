"""Lexical path-shape screen for generic typed property writes (T19/T20).

EXPLICITLY A PARTIAL MITIGATION, NOT A PROJECT FILE SANDBOX.

This helper rejects, before any engine setter/checkpoint/copy is dispatched,
only typed-value string leaves that fail its partial lexical and
descriptor-relative checks for the approved project root on the local host:

* POSIX absolute paths that cannot be kept inside the durable project
  workspace by a **resolution-order** component walk (see below),
* Windows drive (``C:\\``) / UNC (``\\\\server\\share``) / drive-relative
  (``\\name``, and the separator-less ``C:name`` / ``C:dir\\name`` forms)
  shapes that cannot be proven inside the root on this host,
* relative values that contain a real ``..`` parent-directory segment (the
  engine resolves relative values against the *engine host's* working
  directory, which is unverified, so containment can never be proven),
* hierarchical resource URIs such as ``scheme://...``: this screen has no
  project-bound resolver for them, and COMSOL supports file-scheme URIs,
* values containing NUL bytes,
* absolute values whose components — walked **in the order the OS resolves
  them**, strictly below a pinned approved root, with no-follow
  descriptor-relative inspection — fail
  the scan: a symlink component, or a final regular-file target with a
  filesystem link count other than one (the same single-link rule the
  accepted T20 hardlink/digest adjudication uses).

The component walk deliberately does **not** normalize the value first. The
kernel follows a symlink *before* applying the ``..`` that follows it, so
``<root>/link/../x`` with a symlinked ``link`` resolves to the symlink target's
parent — not to ``<root>/x``. ``os.path.normpath`` (or any norm-then-scan
order) folds that hop away. Walking instead of folding catches a symlink
before applying the following ``..`` and adjudicates ``<root>/..`` at the
project-root boundary. Each lookup below the root is relative to a pinned
project-root descriptor; descendant directories are opened with no-follow
flags and matched against their no-follow directory-entry metadata.

Other unclassified values pass untouched: plain text (``linear``, ``f1``,
``m/s^-1``, ``file``), line-format payloads, expressions, and absolute paths
that pass the component checks. A URI-shaped value is refused
even if a particular property would treat it as literal text; authorized
Model Manager URI inputs also require a separate project-binding route.
This lexical screen does not classify file semantics. T19.a adds a separate,
finite rule for the documented ``Import.filename`` and
``Interpolation.filename`` fields: the node type and property identifier
must match exactly, and relative values require a bound route. Bare relative
values remain undecided for every other property. No
"complete UNKNOWN fail-closed" or general file-boundary claim is made or
implied. Equally, no claim is made that *every* project-internal value is
allowed — the screen is deliberately asymmetric and its conservatism has a
compatibility cost:

* an absolute value that does not already sit under the approved root
  *before* any ``..`` folding (``/outside/../<root>/x``) is refused even
  though it folds back inside;
* a ``..`` at the root boundary is refused even when the caller intended a
  fold back into the project;
* any value whose first two characters are a single letter plus ``:``
  (``C:secret.dat``, ``D:dir\\secret.dat``, but also an ambiguous text such
  as ``a:b``) is refused on every host.  On a Windows engine host that is a
  drive-plus-CWD path whose current directory is unprovable; on a POSIX
  engine host it is only a relative filename, and it is refused *stricter*
  than the undecided bare-relative class because the token is a path shape
  on the sibling platform and its containment is unprovable there too.
  Two-character prefixes (``ab:cd``) are not a drive shape and pass.

The POSIX control-side walk is rooted in a pinned ``ProjectRootBinding`` and
does not follow a replaced descendant name for subsequent metadata lookups.
A name can be replaced after its post-open check, even before the walk returns;
the original path may then resolve outside the project while the held descriptor
still refers to the earlier inode. This does not constrain engine re-resolution,
establish file provenance, or close the check-then-engine-use TOCTOU window.
The Windows drive/UNC branch remains lexical only: it performs
no Windows component/reparse scan, so a ``..`` segment inside a Windows-shaped
absolute value is refused (``absolute_traversal_unscannable``) rather than
folded, and no Windows containment claim beyond lexical containment is made.
Roots and host identity must be supplied by the managed backend from the
durable project record and the local endpoint — a caller cannot inject a
root, a provider, or a screen through the request body.
"""
from __future__ import annotations

import os
import re
import stat
from typing import Any, Mapping

SHAPE_POSIX_ABSOLUTE = "posix_absolute"
SHAPE_WINDOWS_DRIVE = "windows_drive"
SHAPE_UNC = "unc"
SHAPE_DRIVE_RELATIVE = "drive_relative"
SHAPE_DOTDOT_TRAVERSAL = "dotdot_traversal"
SHAPE_NUL = "nul_byte"
SHAPE_RESOURCE_URI = "resource_uri"

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
# "C:name" / "C:dir\name" / bare "C:" — a Windows drive letter *without* a
# separator is drive-and-CWD-relative, not an absolute path on that drive.
# The separator-anchored pattern above never sees it, so it used to fall
# through the classifier as an ordinary string.
_WINDOWS_DRIVE_RELATIVE_RE = re.compile(r"^[A-Za-z]:(?![\\/])")
_RESOURCE_URI_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_RELATIVE_SPLIT_RE = re.compile(r"[/\\]")
_KNOWN_ABSOLUTE_FILE_SHAPES = frozenset({
    SHAPE_POSIX_ABSOLUTE, SHAPE_WINDOWS_DRIVE, SHAPE_UNC,
})


def classify_string_leaf(text: str) -> str | None:
    """Return the dangerous path shape of a string, or ``None``.

    Purely lexical; performs no filesystem access.  A ``None`` result means
    "no decision", never "proven safe" and never "proven not-a-path".
    """
    if not isinstance(text, str) or not text:
        return None
    if "\x00" in text:
        return SHAPE_NUL
    if text.startswith("\\\\"):
        return SHAPE_UNC
    if _WINDOWS_DRIVE_RE.match(text):
        return SHAPE_WINDOWS_DRIVE
    if _WINDOWS_DRIVE_RELATIVE_RE.match(text):
        return SHAPE_DRIVE_RELATIVE
    if _RESOURCE_URI_RE.match(text):
        return SHAPE_RESOURCE_URI
    if text.startswith("\\"):
        return SHAPE_DRIVE_RELATIVE
    if text.startswith("/"):
        return SHAPE_POSIX_ABSOLUTE
    if any(segment == ".." for segment in _RELATIVE_SPLIT_RE.split(text)):
        return SHAPE_DOTDOT_TRAVERSAL
    return None


def known_file_value_refusal(type_id: Any, property_name: Any, typed: Any) -> str | None:
    """Return a refusal reason for the documented file-valued properties.

    COMSOL 6.4 DesignModuleUsersGuide, chunk 24610 (source SHA-256
    ``9152bd0306fc93bb3fd6129220f1c4bc1fd1be78defbac31f41a04159425dd2a``),
    identifies the geometry feature created with type ``Import`` and its
    ``filename`` property as a CAD file name. COMSOL 6.4 Programming Reference
    Manual, page 1252, chunk 12007 (SHA-256
    ``5b7f23ad2eae77f59d71935f4f6c9b6b9ede380dc34da065181841e512bbc105``)
    identifies ``Interpolation.filename`` as a file. This function recognizes
    only these exact type/property pairs. The caller supplies the
    type used by ``node.create`` or read from the resolved engine node; there
    is no request-body semantic flag.

    An absolute path shape is only eligible for the existing project-root and
    host path screen. This function does not authorize it, bind an open
    descriptor to COMSOL, or close the engine's later re-resolution window.
    """
    if property_name != "filename" or type_id not in ("Import", "Interpolation"):
        return None
    if not isinstance(typed, Mapping) or typed.get("kind") != "string":
        return f"COMSOL {type_id}.filename requires a literal scalar string path"
    shape = typed.get("shape")
    data = typed.get("data")
    if not isinstance(shape, (list, tuple)) or len(shape) != 0 or not isinstance(data, str):
        return f"COMSOL {type_id}.filename requires a literal scalar string path"
    if data == "":
        return None
    if classify_string_leaf(data) not in _KNOWN_ABSOLUTE_FILE_SHAPES:
        return (
            f"COMSOL {type_id}.filename names a file; a relative name has no project binding. "
            "Supply an absolute path for the project-root check or use a route that "
            "binds a registered artifact."
        )
    return None


def require_known_file_path(type_id: Any, property_name: Any, typed: Any) -> None:
    """Raise a pre-write refusal for a relative value in the finite known case."""
    reason = known_file_value_refusal(type_id, property_name, typed)
    if reason is None:
        return
    from ._execution_contract import PreWriteRefusal
    raise PreWriteRefusal(
        "ACCESS_VIOLATION", reason,
        details={"mitigation": "t19a-known-file-semantics",
                 "type_id": type_id, "property": property_name,
                 "scope_limit": "exact Import.filename and Interpolation.filename pairs only"},
    )


def typed_value_string_leaves(typed: Any, prefix: str = "value") -> list[tuple[str, str]]:
    """Every string leaf inside a typed value payload (arrays/matrices recurse).

    Only ``data`` payloads are walked — property names, keys, kinds and Java
    signatures are schema-validated identifiers, not value strings.
    """
    out: list[tuple[str, str]] = []

    def walk(node: Any, where: str) -> None:
        if isinstance(node, str):
            out.append((where, node))
        elif isinstance(node, (list, tuple)):
            for index, item in enumerate(node):
                walk(item, f"{where}[{index}]")

    if isinstance(typed, Mapping):
        walk(typed.get("data"), prefix)
    else:
        walk(typed, prefix)
    return out


def _root_components(root: str) -> list[str]:
    """The approved root's own components, in order (no folding applied)."""
    return [part for part in root.split("/") if part and part != "."]


def _posix_ordered_component_walk(root: str, text: str, *, project_root_binding=None) -> str | None:
    """Walk a POSIX-absolute value from a pinned, no-follow project root.

    Components are processed in the original resolution order. Each metadata
    lookup is relative to an open directory descriptor; a child directory is
    opened with O_NOFOLLOW, then the parent descriptor is checked again with
    no-follow stat and must still name the opened inode before descent.
    Replacing a traversed directory name during this check is refused. The
    final check-to-engine-use race remains outside this control-side walk.
    """
    parts = text.split("/")
    if parts[0] != "":
        return "outside_project_boundary"
    body = parts[1:]
    root_parts = _root_components(root)
    if body[:len(root_parts)] != root_parts:
        return "outside_project_boundary"

    from ._execution_contract import ExecutionContractError
    from ._project_root import (ProjectRootBinding, _DIR_FLAGS,
                                _finish_cleanup, close_binding_releasing,
                                open_project_root_binding)

    owned_binding = project_root_binding is None
    if owned_binding:
        try:
            binding = open_project_root_binding(root)
        except ExecutionContractError as exc:
            if exc.details.get("cleanup_status") == "UNCONFIRMED":
                raise
            return "root_binding_unavailable"
        except Exception:
            return "root_binding_unavailable"
    else:
        binding = project_root_binding

    try:
        binding_path = os.path.normcase(os.path.abspath(os.fspath(binding.path)))
        expected_path = os.path.normcase(os.path.abspath(root))
        valid_binding = (
            isinstance(binding, ProjectRootBinding)
            and not binding._closed
            and binding._api is None
            and binding._fd is not None
            and binding_path == expected_path
            and bool(getattr(os, "O_NOFOLLOW", 0))
            and bool(getattr(os, "O_DIRECTORY", 0))
            and os.open in os.supports_dir_fd
            and os.stat in os.supports_dir_fd
        )
    except (AttributeError, OSError, TypeError, ValueError):
        valid_binding = False
    if not valid_binding:
        if owned_binding:
            close_binding_releasing(binding, None)
        return "root_binding_unavailable"

    owned_fds: list[int] = []
    primary_error: BaseException | None = None
    try:
        try:
            root_fd = os.dup(binding._fd)
            owned_fds.append(root_fd)
            root_info = os.fstat(root_fd)
        except OSError:
            return "root_binding_unavailable"
        if (not stat.S_ISDIR(root_info.st_mode)
                or (int(root_info.st_dev), int(root_info.st_ino)) != tuple(binding.identity[:2])):
            return "root_binding_unavailable"

        consumed: list[str] = []
        infos: list[Any] = []
        fd_stack: list[int | None] = [root_fd]
        for part in body[len(root_parts):]:
            if part in ("", "."):
                continue
            if part == "..":
                if not consumed:
                    return "outside_project_boundary"
                if infos[-1] is None or not stat.S_ISDIR(infos[-1].st_mode):
                    # A missing or non-directory predecessor cannot be
                    # traversed by the kernel before applying "..".
                    return "component_uninspectable"
                consumed.pop()
                infos.pop()
                fd_stack.pop()
                continue

            consumed.append(part)
            if any(info is None for info in infos):
                # A parent component is absent: probe nothing deeper.
                infos.append(None)
                fd_stack.append(None)
                continue
            parent_fd = fd_stack[-1]
            if parent_fd is None:
                return "component_uninspectable"
            try:
                info = os.stat(part, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                infos.append(None)
                fd_stack.append(None)
                continue
            except OSError:
                return "component_uninspectable"
            if stat.S_ISLNK(info.st_mode):
                return "symlink_component"
            infos.append(info)
            if stat.S_ISDIR(info.st_mode):
                try:
                    child_fd = os.open(part, _DIR_FLAGS, dir_fd=parent_fd)
                    owned_fds.append(child_fd)
                    opened_info = os.fstat(child_fd)
                except OSError:
                    return "component_uninspectable"
                try:
                    named_info = os.stat(part, dir_fd=parent_fd, follow_symlinks=False)
                except OSError:
                    return "component_changed_during_walk"
                if stat.S_ISLNK(named_info.st_mode):
                    return "symlink_component"
                opened_identity = (int(opened_info.st_dev), int(opened_info.st_ino))
                if (not stat.S_ISDIR(opened_info.st_mode)
                        or not stat.S_ISDIR(named_info.st_mode)
                        or opened_identity != (int(info.st_dev), int(info.st_ino))
                        or opened_identity != (int(named_info.st_dev), int(named_info.st_ino))):
                    return "component_changed_during_walk"
                fd_stack.append(child_fd)
            else:
                fd_stack.append(None)

        deepest = next((info for info in reversed(infos) if info is not None), None)
        if deepest is not None and stat.S_ISREG(deepest.st_mode) and deepest.st_nlink != 1:
            return "multiple_link_target"
        return None
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            _finish_cleanup(os.close, reversed(owned_fds), primary_error)
        except BaseException as exc:
            if primary_error is None:
                primary_error = exc
            raise
        finally:
            if owned_binding:
                close_binding_releasing(binding, primary_error)


def _lexically_inside_nt(lexical: str, root: str) -> bool:
    import ntpath
    try:
        common = ntpath.commonpath((ntpath.normcase(lexical), ntpath.normcase(root)))
        return common == ntpath.normcase(root)
    except ValueError:
        return False


def _has_parent_segment(text: str) -> bool:
    """True when a real ``..`` component appears under either separator."""
    return any(segment == ".." for segment in _RELATIVE_SPLIT_RE.split(text))


def adjudicate_path_leaf(text: str, shape: str, *, project_root: str | None,
                         engine_host_proven: bool, project_root_binding=None) -> tuple[bool, str]:
    """Decide one path-shaped string leaf. Returns ``(allowed, reason)``.

    ``engine_host_proven`` must mean: the COMSOL endpoint was proven to be a
    literal loopback address on this host, so the control-side project root
    and the engine's filesystem are the same host filesystem.  Without that
    proof every path shape is unprovable and therefore refused (the
    ``function.data_import`` ARTIFACT_HOST_UNVERIFIED precedent).
    """
    if shape == SHAPE_NUL:
        return False, "nul_byte_in_value"
    if shape == SHAPE_RESOURCE_URI:
        # A file-scheme URI can resolve outside the project workspace. The
        # generic string setter has no trusted project-bound URI resolver.
        return False, "resource_uri_binding_unavailable"
    if shape == SHAPE_DOTDOT_TRAVERSAL:
        # A relative value with a real ".." segment resolves against the
        # engine host's own (unverified) working directory: containment can
        # never be proven, and the shape can escape any directory.
        return False, "relative_traversal_undecidable"
    if shape == SHAPE_DRIVE_RELATIVE:
        # "\name" resolves against an unprovable current drive root, and
        # "C:name" / "C:dir\name" / bare "C:" resolve against the unprovable
        # current directory of that drive: neither is provably project-local
        # on any host (on a POSIX host the drive-letter forms are only
        # relative names, whose containment is equally unprovable).
        return False, "drive_relative_undecidable"
    host_is_windows = os.name == "nt"
    if shape == SHAPE_WINDOWS_DRIVE or shape == SHAPE_UNC:
        if not host_is_windows:
            # A Windows-shaped value has no provable relationship to a POSIX
            # project root; on a POSIX engine host it is also not something
            # this guard can adjudicate as project-local.
            return False, "windows_shape_host_unprovable"
        if not engine_host_proven or not project_root:
            return False, "adjudication_unavailable"
        if _has_parent_segment(text):
            # This branch is lexical only: there is no Windows component /
            # reparse-point scan here (and no native Windows run), so a ".."
            # could fold across a junction or symlinked component whose
            # parent lies elsewhere.  Refuse the fold instead of assuming it;
            # in-project Windows values written with a ".." fold are refused
            # with it (recorded compatibility cost).
            return False, "absolute_traversal_unscannable"
        import ntpath
        lexical = ntpath.normpath(text)
        if not _lexically_inside_nt(lexical, project_root):
            return False, "outside_project_boundary"
        return True, "inside_project_boundary_lexical_only"
    if shape == SHAPE_POSIX_ABSOLUTE:
        if host_is_windows:
            # "/name" on a Windows engine host is drive-relative: unprovable.
            return False, "drive_relative_undecidable"
        if not engine_host_proven or not project_root:
            return False, "adjudication_unavailable"
        # No normpath: the components are walked in resolution order so that
        # a symlink component in front of a ".." is seen before the fold.
        reason = _posix_ordered_component_walk(
            project_root, text, project_root_binding=project_root_binding)
        if reason:
            return False, reason
        return True, "inside_project_boundary"
    return False, "unknown_shape"
