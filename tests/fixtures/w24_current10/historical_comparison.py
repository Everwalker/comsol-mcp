"""Exact historical current10 comparison function, retained for failure regression."""
from typing import Any

def _compare_reopened_readback(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Require every configured geometry/physics/solver readback to survive MPH reload."""
    # Mesh artifacts carry stage-specific paths and ModelRef revisions. Their
    # raw mesh bytes and volume rows are compared independently below.
    ignored = {"status", "native_study_run_calls", "mesh_volume_observation"}
    expected = {key: value for key, value in before.items() if key not in ignored}
    missing = sorted(set(expected) - set(after))
    added = sorted(set(after) - set(expected))
    changed = sorted(key for key in set(expected) & set(after) if expected[key] != after[key])
    return {"matches": not (missing or added or changed),
            "missing_keys": missing, "added_keys": added, "changed_keys": changed,
            "compared_keys": sorted(expected)}
