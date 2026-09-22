# Run notes

- run_id: `frozen_19case_20260922T055332Z`
- goal: NEXT_GOAL.md: G3.3 clean-room recovery, evidence correction, and W17 verification
- status: `ACCEPTANCE_FAILED`
- source: `65d1c51c3d1221ad7649d2b9d2253dd0d83250db` (tree `a00fdf8d1c546fbc8bb4be253876ed73025b8831`, branch `acceptance/independent-20260922`)
- engine: unavailable

## What is tracked and what is not

This directory is tracked selectively.  The evidence documents are:

- `result.json` -- the ledger written for this run (same payload as
  `evidence/phase4_3_acceptance.json`);
- `assertions.json` -- per-case assertion records plus any aborted cases
  with their tracebacks;
- `runner.log` -- the runner's own log lines for this run;
- `source_manifest.json` -- HEAD, tree, branch, tracked-file count and the
  aggregate blob-map digest the ledger is bound to;
- `environment.json` -- COMSOL root, JDK, interpreter, cwd, command line;
- `case_inventory.json` -- the case ids recorded in this run;
- `SHA256SUMS.json` -- digests of the run artifacts and of the evidence
  files above, so a verifier can detect edits.

Run-local working state is deliberately *not* tracked (see `.gitignore`):
`artifacts/` (solved models and sentinel files), `prefs/` (the isolated
engine's private workspace), `tmp/`, `locks/`, `recovery/`, `worker_*/`,
`test_venv/`, `wheel_dist/`, generated `*.java` builders, `server.port` and
`mphserver.log`.  Those are inputs the run consumed or produced on disk;
they are reproducible by re-running the suite, and their digests are in
`SHA256SUMS.json`.

## How to re-verify

```
cd /Users/everwalker/Downloads/COMSOL_MCP_G3_3_WORKPACK/repository
.venv/bin/python tests/run_g3_3_live_acceptance.py --run-dir /Users/everwalker/Downloads/COMSOL_MCP_G3_3_WORKPACK/repository/evidence/phase4_3/runs/frozen_19case_20260922T055332Z
```

The run refuses to start from a tree with modified tracked files, so the
ledger it writes is always bound to a commit.
- `request_reply_trail.jsonl` -- the engine request/reply trail (§11): one
  record per worker request event (submitted / observed / unknown /
  unresponsive) with the redacted payload and a request hash, so a request
  that never got a reply stays visible as such;
- `numeric.json` -- the numeric evidence per case: analytic expectations,
  absolute and relative errors, the tolerance with its rationale, the
  achieved margin, and the peak-memory records.
