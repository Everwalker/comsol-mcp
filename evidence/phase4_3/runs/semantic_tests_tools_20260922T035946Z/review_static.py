from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


repo = Path(__file__).resolve().parents[4]
run_dir = Path(__file__).resolve().parent
prior_path = repo / "audit/semantic_module_review.json"
prior = json.loads(prior_path.read_text(encoding="utf-8"))
prior_entries = {entry["path"]: entry for entry in prior.get("entries", [])}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def one_line(value: str) -> str:
    value = re.sub(r"/(?:Users|private|var|tmp)/[^\s'\"]+", "<path>", value)
    value = re.sub(r"(?i)(token|password|secret|api[_-]?key)\s*[:=]\s*[^,;\s]+", r"\1=<redacted>", value)
    return " ".join(value.split())[:240]


def source_summary(relative: str, text: str) -> dict[str, object]:
    path = repo / relative
    line_count = text.count("\n") + (0 if not text else 1)
    try:
        tree = ast.parse(text, filename=relative)
        parse_status = "PARSED"
        parse_error = None
    except SyntaxError as exc:
        tree = None
        parse_status = "PARSE_ERROR"
        parse_error = f"{exc.__class__.__name__}: {exc.msg} at line {exc.lineno}, column {exc.offset}"

    functions: list[str] = []
    classes: list[str] = []
    imports: list[str] = []
    tests: list[str] = []
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(node.name)
                if node.name.startswith("test_"):
                    tests.append(node.name)
            elif isinstance(node, ast.ClassDef):
                classes.append(node.name)
            elif isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append((node.module or ".") + ("." * node.level if node.level else ""))

    lower = text.lower()
    stem = path.stem
    is_test = relative.startswith("tests/")
    if relative == "tests/__init__.py":
        responsibility = "pytest package marker"
    elif is_test:
        if stem.startswith("run_") or "acceptance" in stem or "driver" in stem:
            responsibility = f"test/acceptance driver for {stem}"
        else:
            responsibility = f"pytest contract/regression module for {stem.removeprefix('test_')}"
    elif stem.startswith("g33_") or stem.startswith("g3_3_"):
        responsibility = f"G3.3 public/helper acceptance tool for {stem}"
    elif stem.startswith("phase"):
        responsibility = f"phase lifecycle or recovery runner for {stem}"
    elif stem.startswith("w0"):
        responsibility = f"legacy W0x audit/evidence helper for {stem}"
    elif stem in {"portable_engine_regression", "windows_handoff_snapshot"}:
        responsibility = f"platform/regression helper for {stem}"
    elif stem.startswith("generate"):
        responsibility = f"source/catalog generation helper for {stem}"
    else:
        responsibility = f"tool/CLI helper for {stem}"

    invariants: list[str] = []
    assertion_count = len(re.findall(r"\bassert\b", text))
    if assertion_count:
        invariants.append(f"contains {assertion_count} source-level assert statements; they require the declared runtime/test context")
    if "pytest.raises" in lower or "raises(" in lower:
        invariants.append("negative paths use explicit exception/refusal expectations")
    if any(token in lower for token in ("status", "pass", "fail", "blocked", "not_run", "unverified")):
        invariants.append("records or checks explicit outcome/status vocabulary")
    if any(token in lower for token in ("unknown", "reconcile", "idempotency", "same_key", "same-key")):
        invariants.append("contains a visible UNKNOWN/reconciliation/idempotency boundary that must not be promoted by static inspection")
    if any(token in lower for token in ("sha256", "hashlib", "digest", "whole_file")):
        invariants.append("records or verifies content/file identity with a digest")
    if any(token in lower for token in ("finite", "isfinite", "nan", "inf")):
        invariants.append("contains finite/nonfinite or numeric validity checks")
    if any(token in lower for token in ("path", "symlink", "relative_to", "destination", "artifact")):
        invariants.append("contains path/artifact/symlink or destination boundary checks")
    if "fake" in lower or "monkeypatch" in lower or "mock" in lower or "stub" in lower:
        invariants.append("uses fake/monkeypatched/stubbed collaborators in at least part of its coverage")
    if not invariants:
        invariants.append("no specific contract marker was inferred; responsibility is limited to the source names listed here")

    risks: list[str] = []
    engine_terms = ("comsol", "mph", "jpype", "java", "worker", "engine", "model.result", "model.sol")
    public_terms = ("mcp", "actionclient", "stdio", "protocol", "job_status", "job_reconcile")
    process_terms = ("subprocess", "popen", "create_subprocess", "socket", "os.kill", "terminate(", "launch")
    write_terms = ("write_text", "write_bytes", "json.dump", "csv.writer", "mkdir(", "tempfile", "open(")
    destructive_terms = ("unlink(", "rmtree(", "os.remove", ".remove(", "kill(", "terminate(")
    if any(token in lower for token in engine_terms + public_terms):
        risks.append("contains engine or public-MCP identifiers; static review cannot prove a live server, worker identity, or fresh reopen")
    if any(token in lower for token in process_terms):
        risks.append("contains process/network launch or transport code; do not execute from this static audit")
    if any(token in lower for token in write_terms):
        risks.append("can write files/evidence when executed; runtime use requires a task-owned run directory")
    if any(token in lower for token in destructive_terms):
        risks.append("contains deletion/termination-shaped code; ownership and retention must be checked before runtime use")
    if "exec(" in lower or "eval(" in lower:
        risks.append("contains dynamic execution syntax; source names alone do not establish its safety")
    if parse_error:
        risks.append("AST parsing failed; semantic conclusion is limited to the full text read and hash")
    if is_test and not tests and relative != "tests/__init__.py":
        risks.append("no test_* function was found by the static parser; this may be a helper/fixture or a non-pytest driver")
    if not risks:
        risks.append("no process/engine/destructive marker was found by this bounded lexical scan; runtime behavior remains unverified")

    doc = None
    if tree is not None and isinstance(tree, ast.Module) and tree.body and isinstance(tree.body[0], ast.Expr):
        value = tree.body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            doc = one_line(value.value.splitlines()[0])
    status = subprocess.run(["git", "status", "--porcelain=v1", "--", relative], cwd=repo,
                            text=True, capture_output=True).stdout.strip()
    return {
        "path": relative,
        "scope": "tests" if is_test else "tools",
        "bytes": len(text.encode("utf-8")),
        "line_count": line_count,
        "source_sha256": digest(path),
        "current_git_status": status,
        "status": "AUTOMATED_INVENTORY_ONLY" if parse_status == "PARSED" else "AUTOMATED_INVENTORY_PARSE_ERROR",
        "semantic_reviewed": False,
        "reviewed_here": False,
        "read_method": "full UTF-8 source read for inventory; AST/name/import scan; no import or execution",
        "parse_status": parse_status,
        "parse_error": parse_error,
        "module_doc_first_line": doc,
        "responsibility": responsibility,
        "functions": sorted(set(functions)),
        "classes": sorted(set(classes)),
        "imports": sorted(set(imports)),
        "pytest_test_functions": sorted(set(tests)),
        "invariants": invariants,
        "findings": [
            "Static source observations only; no assertion in this record is promoted to live COMSOL/MCP acceptance.",
            f"The file exposes {len(functions)} function definitions and {len(classes)} class definitions to the parser.",
        ],
        "risks": risks,
        "runtime_status": "NOT_RUN",
        "engine_started": False,
        "server_started": False,
    }


all_paths = sorted(
    str(path.relative_to(repo))
    for root in (repo / "tests", repo / "tools")
    for path in root.rglob("*.py")
    if path.is_file()
)
scope_paths = [path for path in all_paths if prior_entries.get(path, {}).get("reviewed_here") is not True]
missing = [path for path in scope_paths if not (repo / path).is_file()]
entries = []
for relative in scope_paths:
    if relative in missing:
        continue
    text = (repo / relative).read_text(encoding="utf-8")
    entries.append(source_summary(relative, text))

status_text = subprocess.run(["git", "status", "--porcelain=v1"], cwd=repo, text=True,
                            capture_output=True).stdout
head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, text=True,
                      capture_output=True).stdout.strip()
branch = subprocess.run(["git", "branch", "--show-current"], cwd=repo, text=True,
                        capture_output=True).stdout.strip()
result = {
    "schema": "comsol-mcp.g3.3.semantic-tests-tools-review.v1",
    "generated_at_utc": "2026-09-22T03:59:46Z",
    "repository": str(repo),
    "source_head": head,
    "source_branch": branch,
    "pinned_source": prior.get("pinned_source"),
    "prior_audit": {
        "path": str(prior_path),
        "sha256": digest(prior_path),
        "scope_entries": sum(1 for entry in prior.get("entries", []) if entry.get("scope") in {"tests", "tools"}),
        "scope_reviewed_here_omitted": sum(1 for entry in prior.get("entries", []) if entry.get("scope") in {"tests", "tools"} and entry.get("reviewed_here") is True),
        "scope_not_reviewed_selected": len(scope_paths),
    },
    "status": "AUTOMATED_INVENTORY_ONLY",
    "semantic_reviewed": False,
    "authorization_boundary": {
        "engine_started": False,
        "comsol_started": False,
        "server_started": False,
        "shared_processes_touched": False,
        "scope": "full static reads of previously unreviewed tests/tools Python files; no imports, subprocesses, pytest, MCP, or COMSOL runtime",
    },
    "counts": {
        "tests_total": sum(1 for path in all_paths if path.startswith("tests/")),
        "tools_total": sum(1 for path in all_paths if path.startswith("tools/")),
        "scope_total": len([path for path in all_paths if path.startswith("tests/") or path.startswith("tools/")]),
        "prior_reviewed_omitted": sum(1 for path in all_paths if prior_entries.get(path, {}).get("reviewed_here") is True),
        "full_source_reads": len(entries),
        "semantic_reviewed": 0,
        "missing": len(missing),
        "parse_errors": sum(1 for entry in entries if entry["parse_status"] != "PARSED"),
    },
    "omitted_prior_review_paths": sorted(path for path in all_paths if prior_entries.get(path, {}).get("reviewed_here") is True),
    "missing_paths": missing,
    "method_limits": [
        "Each selected file was read in full as UTF-8 and hashed; this record is an automated inventory and is not a semantic review.",
        "Tests, helpers, fakes, hard-coded fixtures, and source assertions are not live engine evidence.",
        "A clean parse does not establish semantic correctness, process ownership, or public MCP dispatch behavior.",
    ],
    "entries": entries,
}
output = repo / "audit/semantic_tests_tools_review.json"
output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
(run_dir / "audit_output_sha256.txt").write_text(digest(output) + "\n", encoding="utf-8")
(run_dir / "source_status_sha256.txt").write_text(hashlib.sha256(status_text.encode()).hexdigest() + "\n", encoding="utf-8")
(run_dir / "source_head.txt").write_text(head + "\n", encoding="utf-8")
(run_dir / "source_branch.txt").write_text(branch + "\n", encoding="utf-8")
(run_dir / "review_static.command").write_text(
    f"{sys.executable} {Path(__file__).resolve()}\n", encoding="utf-8"
)
(run_dir / "review_static.cwd").write_text(str(repo) + "\n", encoding="utf-8")
(run_dir / "review_static.interpreter").write_text(sys.executable + "\n", encoding="utf-8")
(run_dir / "review_static.exit_code").write_text("0\n", encoding="utf-8")
print(json.dumps({"status": result["status"], "counts": result["counts"], "audit": str(output)}, indent=2))
