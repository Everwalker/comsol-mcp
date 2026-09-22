# G3.3 Operations Guide: Live Acceptance and Clean-room Procedures

## 1. Environment & Prerequisites

### Certified Stack
- **OS:** macOS 15.x / Darwin 24.x (Apple Silicon aarch64)
- **COMSOL:** COMSOL Multiphysics 6.4 (Build 293), installed at `/Applications/COMSOL64/Multiphysics`
- **JDK:** Amazon Corretto JDK 11 (11.0.31), installed at `/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home`
- **Python:** Python 3.13.14 with virtual environment at `.venv`

---

## 2. Clean-room Isolated Server Operations

To prevent interference with any external shared COMSOL server (e.g. PID 5014), G3.3 live acceptance spawns a dedicated, fully isolated `mphserver` instance:

```bash
# Command line executed by runner
/Applications/COMSOL64/Multiphysics/bin/comsol mphserver \
  -port 0 \
  -login auto \
  -silent \
  -multi on \
  -prefsdir <run_dir>/prefs \
  -tmpdir <run_dir>/tmp \
  -recoverydir <run_dir>/recovery
```

### Essential Parameters
1. `-port 0`: Tells the OS kernel to bind an available ephemeral port dynamically.
2. `login auto` & `-prefsdir`: Shares the exact private preferences directory with `PersistentJavaWorker` (`-Dcs.prefsdir`), allowing seamless authentication without manual credentials.
3. `-multi on`: Allows multiple Java client connections (e.g. Model Builder followed by Fresh-Worker Reopen Verifier).

---

## 3. Worker Configuration & Scoping

The worker path configuration must set `project_root` to ensure that model saves and artifact exports remain strictly contained:

```python
from comsol_mcp._java_worker import JavaWorkerPaths, PersistentJavaWorker

paths = JavaWorkerPaths(
    comsol_root=Path("/Applications/COMSOL64/Multiphysics"),
    jdk_home=Path("/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home"),
    private_prefs=run_dir / "prefs",
    project_root=run_dir,
    global_lock_root=run_dir / "locks",
)
worker = PersistentJavaWorker(paths, state_dir=run_dir / "worker_state")
worker.start()
worker.client().connect(server_port, "127.0.0.1")
```

---

## 4. Acceptance Test Execution

### Running the Full Live Acceptance Suite (C00–C17)
```bash
# In repository directory:
.venv/bin/python tests/run_g3_3_live_acceptance.py
```
This executes:
- **C00:** Commit/tree hash verification against `PIN.json`, wheel building and out-of-tree installation in a clean temp venv.
- **C01:** Historical ledger SHA256 preservation and correction ledger validation.
- **C02:** DomainOutcome state transitions and export refusal on failure.
- **C03:** Gate A live reopen: solves Chain A (steady), Chain B (transient), Chain C (continuation); shuts down builder; reopens in fresh worker without solving; verifies spatial gradient points; executes 4 negative controls.
- **C04–C07:** Analytical measures, multi-dimensional feature mapping, non-uniform fields, and axisymmetric cylindrical weighting.
- **C08–C11:** Solution axis slicing, complex transforms, coordinate unit scaling, and dataset cycle detection.
- **C12–C15:** Export path traversal protection, chunk streaming, probe management, and worker health responsiveness.
- **C16–C17:** Packaging checks, isolated server teardown, and ledger writing to `evidence/phase4_3_acceptance.json`.

### Running Unit Test Suites
```bash
.venv/bin/pytest tests/test_g3_3_remediation.py \
                 tests/test_g3_gate_a2_f02_reopen.py \
                 tests/test_g3_results.py \
                 tests/test_g3_w17.py \
                 tests/test_java_worker.py
```

### Running Counterexample Verification
```bash
.venv/bin/python ../tools/reproduce_w17_findings.py --repo . --output ../review/reproduce_w17.json
```
Expected output: `{"counterexamples": 5, "reproduced": 0, "live_comsol_run": false}`.
