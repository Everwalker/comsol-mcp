# G3.7 Windows Production Closure → W20 Layered Validation

## Status: IN_PROGRESS
**Run ID**: `g3_7_windows_w20_20260924T034500Z`
**Source**: `be5bfc8847d98e0aa3972050661a3beb8ee4cfc5` (tree `dbc871285c9e5465a2e2d9740e908289284304cb`)
**Branch**: `handoff/g3_7_windows_w20`
**Platform**: macOS (development), Windows 11 AMD64 (target execution)
**COMSOL**: 6.3.0.290, 6.4.0.293

## Phase 0: Source Recovery — COMPLETE
- verify_package: 31/31 PASS
- bootstrap: 8326 files, SOURCE_RECOVERED_HASH_VERIFIED, method=git
- audit_repository: 8326 files, 0 hash mismatches, SOURCE_HASH_VERIFIED
- Git branch: `handoff/g3_7_windows_w20` from pinned commit

## Phase 1: Gate A — Production/Security Fixes

### F02: Terminal State & RPC Consistency (A1/A5)
- **Status**: IN_PROGRESS
- **File**: `comsol_mcp/_control_daemon.py` (lines 189-195)
- **Problem**: `_finish()` returns candidate result even when `store.finish()` rejected it due to terminal state immunity. Creates SQLite/RPC/event inconsistency.
- **Fix**: `store.finish()` returns authoritative status+result; `_finish()` uses authoritative state for return value and event emission.

### F03: Windows Private DACL (A2)
- **Status**: IN_PROGRESS
- **File**: `comsol_mcp/_security_os.py` (lines 29-41)
- **Problem**: Spoofable USERNAME env var, ignored icacls exit code, no DACL readback, silent return on missing identity.
- **Fix**: Query trusted SID via `whoami /user`, use `*SID` in icacls, check exit code, readback DACL, raise on failure.

### F04: is_process_in_job PID (A3/A4)
- **Status**: IN_PROGRESS
- **File**: `comsol_mcp/_platform_process.py` (lines 149-163)
- **Problem**: `is_process_in_job(pid)` ignores the pid argument, always checks self.
- **Fix**: OpenProcess for target PID, pass handle to IsProcessInJob, close handle. UNKNOWN on API failure.

### F05: Windows Isolation Observation (A3)
- **Status**: IN_PROGRESS
- **File**: `comsol_mcp/_g2_isolation.py`
- **Problem**: CIM query failure fabricates `command="comsol"`, unknown becomes legal.
- **Fix**: Fail closed — return None or raise when process info unavailable.

### F06: Compilation Cache JAR Binding (A1)
- **Status**: IN_PROGRESS
- **File**: `comsol_mcp/_java_worker.py`
- **Problem**: Cache fingerprint uses manifest text hash, not actual JAR content.
- **Fix**: Include JAR file content hashes in cache key.

### F07: Source Evidence Bridge (A14)
- **Status**: PENDING
- **Fix**: File-by-file hash equivalence bridge between pinned source and runtime.

## Phase 2: Gate A Native Acceptance — NOT_STARTED
A00–A15 per ACCEPTANCE_CASES.json

## Phase 3: W20 Layered Validation — NOT_STARTED
B01–B10 per ACCEPTANCE_CASES.json

## Phase 4: Delivery — NOT_STARTED
C01–C02

## Evidence
All evidence stored under `evidence/g3_7_windows_w20/`.
