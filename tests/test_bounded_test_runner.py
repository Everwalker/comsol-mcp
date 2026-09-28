"""Negative controls for the frozen bounded test runner."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import tools.run_bounded_tests as runner


BASELINE = Path(runner.__file__).with_name("test_efficiency_baseline.json")


def _candidate(tmp_path: Path, monkeypatch):
    root = runner.repo_root()
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    tmp_root = tmp_path / "tmp"
    prefix = tmp_path / "synthetic-venv"
    output = tmp_path / "output"
    env = dict(data["environment"])
    env.update({
        "python_executable": str(prefix / "bin" / "python"),
        "python_version": "3.12.13",
        "python_prefix": str(prefix),
        "pytest_version": "9.1.1",
        "output_root": str(output),
        "tmp_root": str(tmp_root),
        "image_path": str(output / "scratch.sparseimage"),
    })
    data["environment"] = env
    monkeypatch.setattr(runner, "output_root", lambda _root: output)
    identity = {
        "executable": env["python_executable"], "version": env["python_version"],
        "prefix": env["python_prefix"],
        "pytest_origin": str(prefix) + "/lib/python3.12/site-packages/pytest/__init__.py",
        "pytest_version": env["pytest_version"],
        "comsol_origin": str((root / "comsol_mcp" / "__init__.py").resolve()),
    }
    return root, data, identity, tmp_root


def _passing_junit(tmp_path: Path, *, expected: int = 1) -> dict:
    report = tmp_path / "passing.xml"
    cases = "".join(f'<testcase classname="sample" name="case{i}"/>' for i in range(expected))
    report.write_text(f"<testsuite tests=\"{expected}\">{cases}</testsuite>", encoding="utf-8")
    return runner.parse_junit(report, expected)


def test_preflight_rejects_wrong_interpreter_and_import_origin(tmp_path: Path, monkeypatch):
    root, data, identity, tmp_root = _candidate(tmp_path, monkeypatch)
    wrong = {**identity, "version": "0.0", "comsol_origin": "/stale/comsol_mcp/__init__.py"}
    errors = runner.validate_baseline(
        root, BASELINE, data, python_identity=wrong, tmp_root=tmp_root,
    )
    assert any("interpreter version" in error for error in errors)
    assert any("import resolves outside" in error for error in errors)


def test_preflight_rejects_mixed_source_hash_and_stale_candidate(tmp_path: Path, monkeypatch):
    root, data, identity, tmp_root = _candidate(tmp_path, monkeypatch)
    changed_hash = {**data, "source_sha256": {**data["source_sha256"], SOURCE_KEY: "0" * 64}}
    errors = runner.validate_baseline(
        root, BASELINE, changed_hash, python_identity=identity,
        tmp_root=tmp_root,
    )
    assert any("source hash mismatch" in error for error in errors)

    stale = {**data, "candidate_id": ""}
    errors = runner.validate_baseline(
        root, BASELINE, stale, python_identity=identity,
        tmp_root=tmp_root,
    )
    assert any("candidate id is missing or invalid" in error for error in errors)


def test_junit_rejects_missing_malformed_zero_and_unexpected_cases(tmp_path: Path):
    assert runner.parse_junit(tmp_path / "missing.xml", 1)["status"] == "FAIL"
    malformed = tmp_path / "malformed.xml"
    malformed.write_text("<testsuite>", encoding="utf-8")
    assert "malformed JUnit" in runner.parse_junit(malformed, 1)["reason"]
    empty = tmp_path / "empty.xml"
    empty.write_text('<testsuite tests="0"/>', encoding="utf-8")
    assert "zero test cases" in runner.parse_junit(empty, 1)["reason"]
    assert runner.parse_junit(_write_one_case(tmp_path), 1)["status"] == "PASS"
    assert "expected 2 test cases" in runner.parse_junit(_write_one_case(tmp_path), 2)["reason"]


def _write_one_case(tmp_path: Path) -> Path:
    path = tmp_path / "one.xml"
    path.write_text('<testsuite tests="1"><testcase classname="m" name="ok"/></testsuite>', encoding="utf-8")
    return path


def test_nonzero_exit_or_candidate_source_mutation_cannot_pass(tmp_path: Path):
    report = _passing_junit(tmp_path)
    assert runner.evaluate_test_stage(1, report, True)[0] == "FAIL"

    source = tmp_path / "candidate.py"
    source.write_text("before", encoding="utf-8")
    before = runner.sha256_file(source)
    source.write_text("changed during test", encoding="utf-8")
    unchanged = runner.sha256_file(source) == before
    status, errors = runner.evaluate_test_stage(0, report, unchanged)
    assert status == "FAIL"
    assert any("fingerprint changed" in error for error in errors)


def test_junit_failure_and_skip_are_not_passes(tmp_path: Path):
    failed = tmp_path / "failed.xml"
    failed.write_text(
        '<testsuite tests="1" failures="1"><testcase classname="m" name="bad">'
        '<failure message="expected mismatch">trace</failure></testcase></testsuite>',
        encoding="utf-8",
    )
    report = runner.parse_junit(failed, 1)
    assert report["status"] == "FAIL"
    assert report["failures"][0]["nodeid"] == "m::bad"

    skipped = tmp_path / "skipped.xml"
    skipped.write_text(
        '<testsuite tests="1"><testcase classname="m" name="skip"><skipped/></testcase></testsuite>',
        encoding="utf-8",
    )
    assert runner.parse_junit(skipped, 1)["executed"] == 0
    assert runner.parse_junit(skipped, 1)["status"] == "FAIL"


def test_full_raw_log_and_appledouble_detection(tmp_path: Path):
    log = tmp_path / "child.log"
    payload = "x" * 10000
    result = runner.run_capped(
        [runner.sys.executable, "-c", f"print('{payload}')"],
        tmp_path, {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}, log, 10,
    )
    assert result["returncode"] == 0
    assert result["log_truncated"] is False
    assert log.stat().st_size == result["bytes_seen"] == len(payload) + 1
    assert log.read_text(encoding="utf-8") == payload + "\n"

    sidecar = tmp_path / "._metadata"
    sidecar.write_bytes(b"synthetic AppleDouble marker")
    assert runner._appledouble_entries(tmp_path) == [str(sidecar)]


def test_overlay_accepts_dirty_then_clean_descendant_and_rejects_mixed_dependency(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    git("config", "user.name", "test")
    git("config", "user.email", "test@example.invalid")
    for relative, text in {
        "comsol_mcp/dependency.py": "VALUE = 1\n",
        "tests/test_x.py": "def test_x(): pass\n",
        "tools/runner.py": "before\n",
    }.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    changed = repo / "tests/test_x.py"
    changed.write_text("def test_x(): assert True\n", encoding="utf-8")
    (repo / "tools/runner.py").write_text("candidate\n", encoding="utf-8")
    overlay = ("tests/test_x.py", "tools/runner.py")
    scope = ("comsol_mcp", "tests", "tools")
    assert runner.validate_overlay(repo, base, overlay, scope) == []
    git("add", "tests/test_x.py", "tools/runner.py")
    git("commit", "-qm", "candidate")
    assert git("status", "--porcelain") == ""
    assert runner.validate_overlay(repo, base, overlay, scope) == []
    (repo / "comsol_mcp/dependency.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert any("overlay mismatch" in error for error in runner.validate_overlay(repo, base, overlay, scope))


SOURCE_KEY = json.loads(BASELINE.read_text(encoding="utf-8"))["source_paths"][0]


def test_required_regression_policy_and_compact_stdout(capsys):
    assert runner.required_regression_plan(("tests/test_x.py::test_x",), False) == "PARTIAL"
    assert runner.required_regression_plan(("tests/test_x.py::test_x",), True) == "RUN"
    assert runner.required_regression_plan((), True) == "NOT_CONFIGURED"

    runner._emit_summary({
        "schema": "BOUNDED_TEST_RUN_V1", "run_id": "run", "candidate_id": "candidate",
        "status": "FAIL", "preflight": "PASS", "native_acceptance": "NOT_RUN",
        "stages": [{"name": "focus", "status": "FAIL", "returncode": 1,
                    "junit": {"tests": 1, "executed": 1, "failure_count": 1, "error_count": 0}}],
        "failures": [{"nodeid": "x" * 4000, "error": "y" * 4000}],
        "errors": ["z" * 4000], "summary_path": "/tmp/summary.json",
    })
    rendered = capsys.readouterr().out
    assert len(rendered.encode("utf-8")) <= runner.MAX_STDOUT_BYTES
    assert json.loads(rendered)["status"] == "FAIL"
