"""Bounded, Windows-only capture of one verified HWND.

This is a low-level pixel source, not a Desktop action adapter.  In particular,
it does not establish which COMSOL model a window displays and is not wired to
``desktop.capture``.  The native PrintWindow call runs in a short-lived helper
process because Microsoft documents that it can block synchronously.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import multiprocessing
import os
from pathlib import Path
import re
import stat
import struct
import sys
import tempfile
from typing import Any, Callable, Mapping

from ._desktop_service import WindowIdentity
from ._desktop_win32_identity import observe_process_birth_and_session


_HWND = re.compile(r"^HWND:([0-9a-fA-F]{1,16})$")
_BMP_HEADER_SIZE = 54
_MAX_DIMENSION = 8192
_MAX_PIXELS = 12_000_000
_MAX_BYTES = 64 * 1024 * 1024
_DEFAULT_TIMEOUT_S = 5.0


class WindowCaptureError(RuntimeError):
    """A fail-closed result from the isolated target-window capture helper."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class CapturedWindowImage:
    """Verified temporary BMP bytes; caller must pass them to a trusted sink."""

    data: bytes
    sha256: str
    width: int
    height: int
    target_identity_fingerprint: str
    format: str = "BMP"
    region: str = "window"
    scope: str = "target_window"


def capture_windows_window(
    identity: WindowIdentity,
    *,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    scratch_root: str | os.PathLike[str] | None = None,
) -> CapturedWindowImage:
    """Capture the whole specified Windows window into bounded temporary storage.

    This API intentionally supports no screen, desktop, client-selected, or
    graphics-only region.  It proves only that the requested HWND still maps to
    the same process birth, login/session and COMSOL executable version before
    and after capture.  It does not prove any model binding.
    """
    if sys.platform != "win32":
        raise WindowCaptureError("UNSUPPORTED_PLATFORM", "PrintWindow capture is available only on Windows")
    _validate_identity(identity)
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not 0.05 <= timeout_s <= 30:
        raise WindowCaptureError("INVALID_TIMEOUT", "timeout_s must be between 0.05 and 30 seconds")
    root = _validated_scratch_root(scratch_root)
    with tempfile.TemporaryDirectory(prefix="comsol-mcp-window-capture-", dir=str(root)) as temp_name:
        temp_dir = Path(temp_name).resolve(strict=True)
        output_path = temp_dir / "window.bmp"
        if output_path.exists() or output_path.is_symlink() or output_path.parent != temp_dir:
            raise WindowCaptureError("CAPTURE_OUTPUT_PATH_REJECTED", "capture output path is not a new direct child of its private temporary directory")
        response = _run_isolated_capture_worker(identity, output_path, timeout_s)
        data = _read_and_verify_capture(identity, output_path, temp_dir, response)
        width, height = _bmp_dimensions(data)
        return CapturedWindowImage(
            data=data,
            sha256=hashlib.sha256(data).hexdigest(),
            width=width,
            height=height,
            target_identity_fingerprint=identity.fingerprint(),
        )


def _validate_identity(identity: WindowIdentity) -> int:
    if not isinstance(identity, WindowIdentity) or identity.platform != "windows":
        raise WindowCaptureError("WINDOW_IDENTITY_REJECTED", "capture requires an observed Windows WindowIdentity")
    match = _HWND.fullmatch(identity.native_window_id)
    if not match:
        raise WindowCaptureError("WINDOW_IDENTITY_REJECTED", "native window id must be a canonical HWND value")
    if identity.process_birth.upper() in {"UNKNOWN", "UNVERIFIED"}:
        raise WindowCaptureError("WINDOW_IDENTITY_REJECTED", "process birth identity is incomplete")
    if identity.login_id.upper() in {"UNKNOWN", "UNVERIFIED"} or identity.desktop_session_id.upper() in {"UNKNOWN", "UNVERIFIED"}:
        raise WindowCaptureError("WINDOW_IDENTITY_REJECTED", "login and desktop session identity are required")
    if identity.comsol_version.upper() in {"UNKNOWN", "UNVERIFIED"}:
        raise WindowCaptureError("WINDOW_IDENTITY_REJECTED", "COMSOL executable version is incomplete")
    hwnd = int(match.group(1), 16)
    if hwnd <= 0:
        raise WindowCaptureError("WINDOW_IDENTITY_REJECTED", "HWND must be positive")
    return hwnd


def _validated_scratch_root(value: str | os.PathLike[str] | None) -> Path:
    candidate = Path(value) if value is not None else Path(tempfile.gettempdir())
    try:
        if candidate.is_symlink():
            raise WindowCaptureError("CAPTURE_OUTPUT_PATH_REJECTED", "scratch root cannot be a symbolic link")
        resolved = candidate.resolve(strict=True)
        info = resolved.stat()
    except WindowCaptureError:
        raise
    except OSError as exc:
        raise WindowCaptureError("CAPTURE_OUTPUT_PATH_REJECTED", "scratch root must be an existing, readable directory") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise WindowCaptureError("CAPTURE_OUTPUT_PATH_REJECTED", "scratch root must be an existing directory")
    return resolved


def _run_isolated_capture_worker(
    identity: WindowIdentity,
    output_path: Path,
    timeout_s: float,
    *,
    worker: Callable[..., None] | None = None,
    process_context: Any = None,
) -> Mapping[str, Any]:
    """Run a module-level capture worker and bound only that helper process."""
    target = worker or _windows_print_window_worker
    context = process_context or multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    helper = context.Process(
        target=target,
        args=(identity, str(output_path), _MAX_DIMENSION, _MAX_PIXELS, _MAX_BYTES, sender),
        name="comsol-mcp-window-capture",
        daemon=True,
    )
    try:
        helper.start()
        sender.close()
        helper.join(float(timeout_s))
        if helper.is_alive():
            # This terminates only the task-owned helper.  The target COMSOL
            # process is never signalled or terminated.  It may still be
            # servicing WM_PRINT after this helper is stopped.
            helper.terminate()
            helper.join(1.0)
            if helper.is_alive() and hasattr(helper, "kill"):
                helper.kill()
                helper.join(1.0)
            raise WindowCaptureError(
                "CAPTURE_HELPER_TIMEOUT",
                "the capture helper exceeded its deadline and was stopped; COMSOL was not terminated, and its WM_PRINT handling state is unknown",
            )
        if helper.exitcode != 0:
            raise WindowCaptureError("CAPTURE_HELPER_FAILED", f"capture helper exited with status {helper.exitcode}")
        if not receiver.poll(0.25):
            raise WindowCaptureError("CAPTURE_HELPER_FAILED", "capture helper exited without an identity and output receipt")
        response = receiver.recv()
        if not isinstance(response, Mapping) or response.get("ok") is not True:
            code = response.get("code") if isinstance(response, Mapping) else None
            message = response.get("message") if isinstance(response, Mapping) else None
            raise WindowCaptureError(str(code or "CAPTURE_HELPER_FAILED"), str(message or "capture helper returned no verified result"))
        return dict(response)
    except WindowCaptureError:
        raise
    except Exception as exc:
        raise WindowCaptureError("CAPTURE_HELPER_FAILED", f"isolated capture helper could not be completed: {type(exc).__name__}") from exc
    finally:
        try:
            sender.close()
        except Exception:
            pass
        try:
            receiver.close()
        except Exception:
            pass
        if helper.pid is not None and helper.is_alive():
            helper.terminate()
            helper.join(1.0)
            if helper.is_alive() and hasattr(helper, "kill"):
                helper.kill()
                helper.join(1.0)


def _read_and_verify_capture(
    identity: WindowIdentity,
    output_path: Path,
    temp_dir: Path,
    response: Mapping[str, Any],
) -> bytes:
    try:
        reported = Path(str(response.get("output_path", "")))
        if reported.absolute() != output_path.absolute():
            raise WindowCaptureError("CAPTURE_OUTPUT_PATH_REJECTED", "helper reported an output outside the exact private target path")
        if reported.resolve(strict=True) != output_path.resolve(strict=True):
            raise WindowCaptureError("CAPTURE_OUTPUT_PATH_REJECTED", "helper output resolves outside the exact private target path")
        if reported.is_symlink() or reported.parent.resolve(strict=True) != temp_dir:
            raise WindowCaptureError("CAPTURE_OUTPUT_PATH_REJECTED", "capture output is not a regular direct child of its private temporary directory")
        info = reported.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size < _BMP_HEADER_SIZE or info.st_size > _MAX_BYTES:
            raise WindowCaptureError("CAPTURE_OUTPUT_REJECTED", "capture output type or size is outside the allowed bounds")
        if response.get("target_identity_fingerprint") != identity.fingerprint():
            raise WindowCaptureError("WINDOW_IDENTITY_CHANGED", "helper did not confirm the expected window/process identity")
        if response.get("pre_identity_fingerprint") != identity.fingerprint() or response.get("post_identity_fingerprint") != identity.fingerprint():
            raise WindowCaptureError("WINDOW_IDENTITY_CHANGED", "window/process identity changed during capture")
        data = reported.read_bytes()
        width, height = _bmp_dimensions(data)
        expected_bytes = _BMP_HEADER_SIZE + width * height * 4
        if len(data) != expected_bytes or info.st_size != expected_bytes:
            raise WindowCaptureError("CAPTURE_OUTPUT_REJECTED", "BMP dimensions do not match its file length")
        if response.get("width") != width or response.get("height") != height:
            raise WindowCaptureError("CAPTURE_OUTPUT_REJECTED", "helper dimension receipt does not match the BMP header")
        if response.get("sha256") != hashlib.sha256(data).hexdigest():
            raise WindowCaptureError("CAPTURE_HASH_MISMATCH", "capture helper hash does not match returned bytes")
        return data
    except WindowCaptureError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise WindowCaptureError("CAPTURE_OUTPUT_REJECTED", "capture output path, metadata, or bytes failed validation") from exc


def _bmp_dimensions(data: bytes) -> tuple[int, int]:
    if not isinstance(data, bytes) or len(data) < _BMP_HEADER_SIZE or data[:2] != b"BM":
        raise WindowCaptureError("CAPTURE_OUTPUT_REJECTED", "capture is not a Windows BMP")
    file_size, _reserved_1, _reserved_2, pixel_offset = struct.unpack_from("<IHHI", data, 2)
    header_size, width, signed_height, planes, bits_per_pixel, compression, image_size = struct.unpack_from("<IiiHHII", data, 14)
    if (file_size != len(data) or pixel_offset != _BMP_HEADER_SIZE or header_size != 40
            or width <= 0 or signed_height == 0 or planes != 1 or bits_per_pixel != 32 or compression != 0):
        raise WindowCaptureError("CAPTURE_OUTPUT_REJECTED", "BMP header does not match the supported uncompressed 32-bit format")
    height = abs(signed_height)
    if width > _MAX_DIMENSION or height > _MAX_DIMENSION or width * height > _MAX_PIXELS:
        raise WindowCaptureError("CAPTURE_DIMENSION_LIMIT", "capture dimensions exceed the configured safety limit")
    expected_image = width * height * 4
    if image_size not in {0, expected_image} or len(data) != _BMP_HEADER_SIZE + expected_image:
        raise WindowCaptureError("CAPTURE_OUTPUT_REJECTED", "BMP image size is inconsistent")
    return width, height


def _windows_print_window_worker(
    identity: WindowIdentity,
    output_path: str,
    max_dimension: int,
    max_pixels: int,
    max_bytes: int,
    sender: Any,
) -> None:
    """Child process entrypoint; every OS call happens outside the caller."""
    try:
        hwnd = _validate_identity(identity)
        before = _observe_hwnd(hwnd)
        if before != identity:
            raise WindowCaptureError("WINDOW_IDENTITY_CHANGED", "HWND no longer maps to the observed process identity")
        rect = _window_rect(hwnd)
        width, height = rect[2] - rect[0], rect[3] - rect[1]
        _validate_dimensions(width, height, max_dimension, max_pixels, max_bytes)
        bitmap = _draw_target_window(hwnd, width, height)
        after = _observe_hwnd(hwnd)
        after_rect = _window_rect(hwnd)
        if after != identity or (after_rect[2] - after_rect[0], after_rect[3] - after_rect[1]) != (width, height):
            raise WindowCaptureError("WINDOW_IDENTITY_CHANGED", "HWND identity or dimensions changed during capture")
        output = Path(output_path)
        _assert_child_output_path(output)
        payload = _encode_bmp(bitmap, width, height)
        if len(payload) > max_bytes:
            raise WindowCaptureError("CAPTURE_SIZE_LIMIT", "captured BMP exceeds the configured byte limit")
        digest = hashlib.sha256(payload).hexdigest()
        descriptor = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        sender.send({
            "ok": True,
            "output_path": str(output),
            "width": width,
            "height": height,
            "sha256": digest,
            "target_identity_fingerprint": identity.fingerprint(),
            "pre_identity_fingerprint": before.fingerprint(),
            "post_identity_fingerprint": after.fingerprint(),
        })
    except WindowCaptureError as exc:
        try:
            sender.send({"ok": False, "code": exc.code, "message": str(exc)})
        except Exception:
            pass
        raise
    except Exception as exc:
        try:
            sender.send({"ok": False, "code": "CAPTURE_NATIVE_API_FAILED", "message": type(exc).__name__})
        except Exception:
            pass
        raise
    finally:
        try:
            sender.close()
        except Exception:
            pass


def _validate_dimensions(width: int, height: int, max_dimension: int, max_pixels: int, max_bytes: int) -> None:
    if width <= 0 or height <= 0:
        raise WindowCaptureError("CAPTURE_DIMENSION_LIMIT", "target window has an empty or invalid rectangle")
    if width > max_dimension or height > max_dimension or width * height > max_pixels:
        raise WindowCaptureError("CAPTURE_DIMENSION_LIMIT", "target window dimensions exceed the configured safety limit")
    if _BMP_HEADER_SIZE + width * height * 4 > max_bytes:
        raise WindowCaptureError("CAPTURE_SIZE_LIMIT", "target window exceeds the configured output size limit")


def _assert_child_output_path(path: Path) -> None:
    parent = path.parent.resolve(strict=True)
    resolved = path.resolve(strict=False)
    if path.name != "window.bmp" or resolved.parent != parent or path.exists() or path.is_symlink():
        raise WindowCaptureError("CAPTURE_OUTPUT_PATH_REJECTED", "child output must be a new window.bmp in the private temporary directory")


def _observe_hwnd(hwnd: int) -> WindowIdentity:
    import win32api
    import win32con
    import win32gui
    import win32process
    import win32security
    import win32ts

    if not win32gui.IsWindow(hwnd):
        raise WindowCaptureError("WINDOW_NOT_FOUND", "target HWND no longer exists")
    _thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)
    if not pid:
        raise WindowCaptureError("WINDOW_IDENTITY_CHANGED", "target HWND has no owning process")
    process = None
    token = None
    try:
        process = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        executable = win32process.QueryFullProcessImageName(process, 0)
        basename = executable.replace("/", "\\").rsplit("\\", 1)[-1].lower()
        if not basename.startswith("comsol") or "mphserver" in basename or basename == "comsolmphserver.exe":
            raise WindowCaptureError("WINDOW_IDENTITY_CHANGED", "target process is not a COMSOL Desktop executable")
        process_birth, session_number = observe_process_birth_and_session(
            win32process, win32ts, process, int(pid),
        )
        token = win32security.OpenProcessToken(process, win32con.TOKEN_QUERY)
        token_user = win32security.GetTokenInformation(token, win32security.TokenUser)
        login_id = win32security.ConvertSidToStringSid(token_user[0])
        version_info = win32api.GetFileVersionInfo(executable, "\\")
        product_ms = int(version_info.get("ProductVersionMS", 0))
        product_ls = int(version_info.get("ProductVersionLS", 0))
        if not (product_ms or product_ls):
            raise WindowCaptureError("WINDOW_IDENTITY_CHANGED", "COMSOL executable version could not be verified")
        version = ".".join(str(part) for part in (
            product_ms >> 16, product_ms & 0xFFFF, product_ls >> 16, product_ls & 0xFFFF,
        ))
        return WindowIdentity(
            platform="windows",
            native_window_id=f"HWND:{hwnd:x}",
            process_id=int(pid),
            process_birth=process_birth,
            login_id=str(login_id),
            desktop_session_id=f"WTS:{session_number}",
            comsol_version=version,
        )
    finally:
        if token is not None:
            try:
                win32api.CloseHandle(token)
            except Exception:
                pass
        if process is not None:
            try:
                win32api.CloseHandle(process)
            except Exception:
                pass


def _window_rect(hwnd: int) -> tuple[int, int, int, int]:
    import win32gui
    if not win32gui.IsWindow(hwnd):
        raise WindowCaptureError("WINDOW_NOT_FOUND", "target HWND no longer exists")
    rect = win32gui.GetWindowRect(hwnd)
    if not isinstance(rect, (tuple, list)) or len(rect) != 4:
        raise WindowCaptureError("CAPTURE_NATIVE_API_FAILED", "GetWindowRect returned invalid bounds")
    return tuple(int(item) for item in rect)


def _draw_target_window(hwnd: int, width: int, height: int) -> bytes:
    """Return top-down 32-bit BGRA bytes rendered by the owning window."""
    import ctypes
    from ctypes import wintypes

    class BitmapInfoHeader(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
        ]

    class BitmapInfo(ctypes.Structure):
        _fields_ = [("bmiHeader", BitmapInfoHeader), ("bmiColors", wintypes.DWORD * 1)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32.GetDC.argtypes = (wintypes.HWND,)
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
    user32.ReleaseDC.restype = ctypes.c_int
    user32.PrintWindow.argtypes = (wintypes.HWND, wintypes.HDC, wintypes.UINT)
    user32.PrintWindow.restype = wintypes.BOOL
    gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.DeleteDC.argtypes = (wintypes.HDC,)
    gdi32.DeleteDC.restype = wintypes.BOOL
    gdi32.CreateDIBSection.argtypes = (
        wintypes.HDC, ctypes.POINTER(BitmapInfo), wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
    )
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
    gdi32.DeleteObject.restype = wintypes.BOOL

    screen_dc = user32.GetDC(None)
    if not screen_dc:
        raise WindowCaptureError("CAPTURE_NATIVE_API_FAILED", "GetDC failed")
    memory_dc = None
    dib = None
    previous = None
    try:
        memory_dc = gdi32.CreateCompatibleDC(screen_dc)
        if not memory_dc:
            raise WindowCaptureError("CAPTURE_NATIVE_API_FAILED", "CreateCompatibleDC failed")
        info = BitmapInfo()
        info.bmiHeader = BitmapInfoHeader(
            ctypes.sizeof(BitmapInfoHeader), width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0,
        )
        bits = ctypes.c_void_p()
        dib = gdi32.CreateDIBSection(screen_dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        if not dib or not bits.value:
            raise WindowCaptureError("CAPTURE_NATIVE_API_FAILED", "CreateDIBSection failed")
        # A driver may leave portions of the bitmap untouched; initialize it
        # before WM_PRINT so a partial render cannot disclose process memory.
        ctypes.memset(bits.value, 0, width * height * 4)
        previous = gdi32.SelectObject(memory_dc, dib)
        if not previous or int(previous) == -1:
            raise WindowCaptureError("CAPTURE_NATIVE_API_FAILED", "SelectObject failed")
        if not user32.PrintWindow(wintypes.HWND(hwnd), memory_dc, 0):
            raise WindowCaptureError("CAPTURE_NATIVE_API_FAILED", "PrintWindow returned failure")
        return ctypes.string_at(bits.value, width * height * 4)
    finally:
        if memory_dc and previous:
            try:
                gdi32.SelectObject(memory_dc, previous)
            except Exception:
                pass
        if dib:
            gdi32.DeleteObject(dib)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(None, screen_dc)


def _encode_bmp(pixel_data: bytes, width: int, height: int) -> bytes:
    if len(pixel_data) != width * height * 4:
        raise WindowCaptureError("CAPTURE_OUTPUT_REJECTED", "native renderer returned the wrong byte count")
    image_size = len(pixel_data)
    file_size = _BMP_HEADER_SIZE + image_size
    file_header = b"BM" + struct.pack("<IHHI", file_size, 0, 0, _BMP_HEADER_SIZE)
    info_header = struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 32, 0, image_size, 0, 0, 0, 0)
    return file_header + info_header + pixel_data
