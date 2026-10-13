"""Resolve exact published historical source, without opening old private paths."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def _source_map():
    return json.loads((ROOT / "continuation/EXTERNAL_SOURCE_MAP.json").read_text())


def published_source(logical_path):
    rows = [row for row in _source_map() if row["source"].endswith("/" + logical_path)]
    assert rows and len({(row["path"], row["sha256"]) for row in rows}) == 1, logical_path
    row = rows[0]
    path = (ROOT / row["path"]).resolve(strict=True)
    assert path.is_relative_to(ROOT / "continuation")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"], logical_path
    return path
