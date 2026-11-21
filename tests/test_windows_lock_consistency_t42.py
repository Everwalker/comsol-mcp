"""T42: requirements-windows-cp312.txt must not drift from the uv.lock lineage.

The Windows cp312 pin file is a secondary, un-hashed candidate lock. The
authoritative dependency lineage is uv.lock (bundled wheelhouse lineage, see
docs/universal_mcp_execution/evidence/full/T42-packaging-qwen-20261108.md and
G8.json "reconcile" item). Exact pins are checked against lock entries active
for the Windows x64 CPython 3.12.13 packaging target after PEP 503 name
normalization; a different Python patch version is not established here.

This is a packaging-consistency software test only. It is not a three-architecture
artifact build and not native Windows acceptance.
"""
import re
import tomllib
from pathlib import Path

from packaging.markers import Marker

ROOT = Path(__file__).resolve().parents[1]
REQ_FILE = ROOT / "requirements-windows-cp312.txt"
LOCK_FILE = ROOT / "uv.lock"

_PIN_RE = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)")
_WINDOWS_CP312 = {
    "implementation_name": "cpython",
    "implementation_version": "3.12.13",
    "os_name": "nt",
    "platform_machine": "AMD64",
    "platform_python_implementation": "CPython",
    "platform_release": "",
    "platform_system": "Windows",
    "platform_version": "",
    "python_full_version": "3.12.13",
    "python_version": "3.12",
    "sys_platform": "win32",
}


def normalize(name: str) -> str:
    """PEP 503 canonical name (case-folded, runs of -_. collapsed to -)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def lock_versions_from_text(lock_text: str) -> dict[str, str]:
    """Select lock versions for the declared Windows x64 CPython 3.12.13 target."""
    data = tomllib.loads(lock_text)
    versions: dict[str, str] = {}
    for pkg in data.get("package", []):
        markers = pkg.get("resolution-markers", [])
        if any(re.search(r"\b(?:platform_release|platform_version|extra|extras|dependency_groups)\b", marker)
               for marker in markers):
            raise ValueError("unsupported target marker: unspecified Windows release or dependency group")
        if markers and not any(Marker(marker).evaluate(_WINDOWS_CP312) for marker in markers):
            continue
        name, version = normalize(pkg["name"]), pkg["version"]
        if name in versions and versions[name] != version:
            raise ValueError(f"ambiguous active lock versions for {name}: {versions[name]}, {version}")
        versions[name] = version
    return versions


def pinned_requirements(text: str) -> list[tuple[str, str]]:
    """Parse exact ``name==version`` pins; reject unpinned or malformed lines."""
    pins: list[tuple[str, str]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _PIN_RE.fullmatch(line)
        if match is None:
            raise ValueError(
                f"line {lineno}: not an exact pinned requirement: {raw!r}"
            )
        pins.append((match.group(1), match.group(2)))
    return pins


def find_drift(
    pins: list[tuple[str, str]], lock: dict[str, str]
) -> list[dict[str, "str | None"]]:
    """Return drift records: version mismatch or pin absent from the lock."""
    drift: list[dict[str, "str | None"]] = []
    for name, version in pins:
        key = normalize(name)
        if key not in lock:
            drift.append({"name": name, "pin": version, "lock": None})
        elif lock[key] != version:
            drift.append({"name": name, "pin": version, "lock": lock[key]})
    return drift


def test_windows_file_is_all_exact_pins():
    """The Windows candidate lock contract: one exact ``name==version`` per line."""
    pins = pinned_requirements(REQ_FILE.read_text(encoding="utf-8"))
    assert pins, "no pins parsed"
    normalized = [normalize(name) for name, _ in pins]
    assert len(normalized) == len(set(normalized)), "duplicate distribution pins"


def test_windows_pins_match_uv_lock_lineage():
    """Every Windows pin must equal the uv.lock version under name normalization."""
    pins = pinned_requirements(REQ_FILE.read_text(encoding="utf-8"))
    lock = lock_versions_from_text(LOCK_FILE.read_text(encoding="utf-8"))
    drift = find_drift(pins, lock)
    assert drift == [], f"pin drift vs uv.lock: {drift}"


def test_normalized_name_variants_are_not_false_drift():
    """Spellings like pydantic_core / JPype1 / MPh match lock names, no drift."""
    lock = {
        normalize(n): v
        for n, v in [
            ("pydantic-core", "2.46.5"),
            ("jpype1", "1.7.1"),
            ("mph", "1.4.0"),
            ("typing-extensions", "4.16.0"),
        ]
    }
    pins = [
        ("pydantic_core", "2.46.5"),
        ("Pydantic-Core", "2.46.5"),
        ("JPype1", "1.7.1"),
        ("MPh", "1.4.0"),
        ("typing_extensions", "4.16.0"),
    ]
    assert find_drift(pins, lock) == []


def test_windows_lock_selection_uses_target_markers_not_package_order():
    """An inactive version listed last cannot mask drift for Windows CPython 3.12."""
    lock_text = '''
[[package]]
name = "demo"
version = "2.0"
resolution-markers = ["python_full_version >= '3.12' and sys_platform == 'win32'"]

[[package]]
name = "demo"
version = "1.0"
resolution-markers = ["python_full_version < '3.12' and sys_platform == 'win32'"]
'''
    selected = lock_versions_from_text(lock_text)
    assert selected["demo"] == "2.0"
    assert find_drift([("demo", "1.0")], selected) == [
        {"name": "demo", "pin": "1.0", "lock": "2.0"}
    ]


def test_windows_lock_rejects_undetermined_os_release_markers():
    """Do not pretend an unobserved Windows release selected a lock branch."""
    import pytest

    lock_text = '''
[[package]]
name = "demo"
version = "1.0"
resolution-markers = ["platform_release == '10' and sys_platform == 'win32'"]
'''
    with pytest.raises(ValueError, match="unsupported target marker"):
        lock_versions_from_text(lock_text)


def test_windows_lock_rejects_ambiguous_active_versions():
    """A malformed target lock cannot silently choose one of two versions."""
    import pytest

    lock_text = '''
[[package]]
name = "demo"
version = "1.0"
resolution-markers = ["python_full_version >= '3.12' and sys_platform == 'win32'"]

[[package]]
name = "demo"
version = "2.0"
resolution-markers = ["python_full_version >= '3.12' and sys_platform == 'win32'"]
'''
    with pytest.raises(ValueError, match="ambiguous active lock versions"):
        lock_versions_from_text(lock_text)


def test_real_pin_drift_is_detected():
    """Negative control: the check must flag an actual version drift and a missing pin."""
    lock = {
        "uvicorn": "0.53.0",
        "httpx": "0.28.1",
    }
    pins = [
        ("uvicorn", "0.54.0"),  # drifts from lock lineage
        ("httpx", "0.28.1"),  # consistent
        ("some-new-package", "1.0"),  # not in the lock at all
    ]
    drift = find_drift(pins, lock)
    assert len(drift) == 2
    uvicorn_drift = next(d for d in drift if d["name"] == "uvicorn")
    assert uvicorn_drift["pin"] == "0.54.0"
    assert uvicorn_drift["lock"] == "0.53.0"
    assert any(d["name"] == "some-new-package" and d["lock"] is None for d in drift)
