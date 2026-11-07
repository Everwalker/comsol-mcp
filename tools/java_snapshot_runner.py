"""Send runner-owned Java fixtures as bounded v2 source snapshots."""
from pathlib import Path
from typing import Any, Mapping

from comsol_mcp._g2_code import read_source_snapshot


def execute_java_fixture(worker: Any, tag: str, source: str | Path, entrypoint: str,
                         arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
    source = Path(source)
    snapshot = read_source_snapshot(source.parent, source.name)
    reply = worker.execute_java(tag, snapshot.path.name, entrypoint, dict(arguments or {}),
                                source_bytes=snapshot.raw_bytes,
                                expected_source_sha256=snapshot.sha256)
    if (reply.get("ok") is not True or reply.get("status") != "SUCCEEDED"
            or reply.get("_source_binding_verified") is not True):
        raise AssertionError(f"Java fixture operation failed: {reply!r}")
    return reply
