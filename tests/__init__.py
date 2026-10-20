"""R13 source-only tests import the isolated source snapshot."""
import sys
from pathlib import Path

_SOURCE = Path(__file__).resolve().parents[1] / "source"
if str(_SOURCE) not in sys.path:
    sys.path.insert(0, str(_SOURCE))
