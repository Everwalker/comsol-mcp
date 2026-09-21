# G3.3 Clean-room Recovery and Continuation Guide

## 1. Overview & Objective

This document specifies how an incoming agent or auditor can independently recover, verify, and continue the `Everwalker/comsol-mcp` project starting in an empty environment without access to previous local virtual environments, temporary folders, or shell sessions.

---

## 2. Step-by-Step Clean Recovery Procedure

### Step 1: Environment Readiness Check
Verify commercial installation paths:
- **COMSOL 6.4:** `/Applications/COMSOL64/Multiphysics`
- **JDK 11:** `/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home`
- **Python:** Python 3.12+ (tested on Python 3.13.14)

### Step 2: Bootstrap Repository
From the root of the workpack:
```bash
python3 tools/bootstrap.py
```
This script:
1. Clones from `Everwalker/comsol-mcp` over the network.
2. Checks out the exact pinned commit `2cb4627924d1a3240818ea7cd00453d4bd2d2da8`.
3. Verifies git tree hash `dd3095e89c640c19aeb151cd0e8efe4af3c55802`.
4. Creates working branch `handoff/g3_3`.

### Step 3: Setup Virtual Environment
```bash
cd repository
python3 -m venv .venv
source .venv/bin/activate
pip install -r constraints-macos-arm64-py313.txt
pip install -e .
```

### Step 4: Verify Historical Ledgers Integrity
Ensure historical evidence files match expected byte-exact hashes:
- `evidence/phase4_1_acceptance.json`: `a2e91f37d021274c5a5f1321f03961c57271e1e614d17f23d3b0ef6627335278`
- `evidence/phase4_2_acceptance.json`: `741e702cf019bfede8564cc032bccdbd30c83ee830ec46ce6a3e16356faa52c4`
- `evidence/w17_acceptance.json`: `53daf14f51720f59e5fb8ed731083cd851b80bd3392c4fdeb7fee4a89a225197`

Correction details reside in `evidence/w17_correction.json` and `evidence/phase4_3/BASELINE_REVIEW.json`.

### Step 5: Verify AST Counterexamples
```bash
.venv/bin/python ../tools/reproduce_w17_findings.py --repo . --output ../review/reproduce_w17.json
```
Verify that `reproduced` equals `0`.

### Step 6: Execute Unit Tests
```bash
.venv/bin/pytest tests/test_g3_3_remediation.py \
                 tests/test_g3_gate_a2_f02_reopen.py \
                 tests/test_g3_results.py \
                 tests/test_g3_w17.py \
                 tests/test_java_worker.py
```
Expected result: 166 passed, 1 skipped.

### Step 7: Execute Live COMSOL Acceptance Suite
```bash
.venv/bin/python tests/run_g3_3_live_acceptance.py
```
Expected result: All 18 cases C00 through C17 pass with zero failures. Output ledger is updated at `evidence/phase4_3_acceptance.json`.

---

## 3. Strict Boundary & Stop Rule

> [!CAUTION]
> **STOPPING CONDITION:** Workstream W17 is verified and certified under tag `G3_3_MAC_W17_VERIFIED_SCOPED`.
> Do **NOT** advance into W18–W26 (Geometry mutation, physics generation, automated study chaining, etc.) without explicit instruction from the user.
