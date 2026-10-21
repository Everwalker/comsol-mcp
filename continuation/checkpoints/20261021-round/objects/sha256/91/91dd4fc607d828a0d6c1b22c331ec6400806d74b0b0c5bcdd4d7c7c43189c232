"""Inert stdlib-only Python launcher probe used by runtime binding tests."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time


def main() -> int:
    try:
        ready = Path(sys.argv[sys.argv.index("--ready") + 1])
        hold_ms = int(sys.argv[sys.argv.index("--hold-ms") + 1])
    except (ValueError, IndexError) as exc:
        raise SystemExit("expected --ready PATH and --hold-ms N") from exc
    payload = {
        "pid": __import__("os").getpid(),
        "argv": sys.argv,
        "orig_argv": getattr(sys, "orig_argv", None),
        "executable": sys.executable,
        "no_site": bool(sys.flags.no_site),
    }
    ready.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    time.sleep(hold_ms / 1000)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
