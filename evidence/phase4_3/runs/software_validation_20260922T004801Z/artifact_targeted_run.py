from __future__ import annotations

from pathlib import Path
import subprocess
import sys


run_dir = Path(__file__).resolve().parent
repo = run_dir.parents[3]
interpreter = run_dir / "software_venv" / "bin" / "python"
command = [
    str(interpreter), "-m", "pytest", "-q",
    "tests/test_g3_artifact_hardening.py",
    "tests/test_g3_d17_artifact_service.py",
    "tests/test_g2_registry_packaging.py",
    "tests/test_g3_w17.py::test_field_export_large_data_and_chunk_verification",
]
completed = subprocess.run(command, cwd=repo, text=True, capture_output=True)
(run_dir / "artifact_targeted.command").write_text(" ".join(command) + "\n", encoding="utf-8")
(run_dir / "artifact_targeted.cwd").write_text(str(repo) + "\n", encoding="utf-8")
(run_dir / "artifact_targeted.interpreter").write_text(str(interpreter) + "\n", encoding="utf-8")
(run_dir / "artifact_targeted.exit_code").write_text(str(completed.returncode) + "\n", encoding="utf-8")
(run_dir / "artifact_targeted.stdout").write_text(completed.stdout, encoding="utf-8")
(run_dir / "artifact_targeted.stderr").write_text(completed.stderr, encoding="utf-8")
sys.stdout.write(completed.stdout)
sys.stderr.write(completed.stderr)
raise SystemExit(completed.returncode)
