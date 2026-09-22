## 2026-09-22 independent acceptance & W17 verification completed

Current status: **G3_3_MAC_W17_VERIFIED_SCOPED**.
The 2026-09-22 independent audit identified open acceptance defects in historical claims (mock controls, F04 allowlist/indexing gaps, Gate A self-comparisons).
Following comprehensive remediation (commits e85cfda through 1011a1e), the complete live acceptance suite (C00–C17, 19 cases) was re-executed against an isolated COMSOL 6.4 (Build 293) mphserver and achieved **19/19 PASS, exit 0**.
Software test baseline: 1797 passed, 1 skipped.
Clean-room recovery verified over network via `tools/bootstrap.py` matching pinned commit `2cb46279` and tree `dd3095e8`.
Gate A reopen verified on 3 independent physical models without re-solving, with 6 native negative controls.
All numerical oracles (C04, C05, C06, C07, C09, C10) verified against independent mathematical formulas.
Export security, atomic rollback, chunk streaming with bounded memory, definitions probe host dispatch, and out-of-tree wheel installation fully verified.
Next stage boundary: STOP at W17. W18+ requires separate user authorization. Platforms other than macOS Apple Silicon (Windows, Linux, Intel Mac, COMSOL 6.3, GUI) remain UNVERIFIED.

# Progress location

W01 and subsequent work-package progress is maintained in [`docs/comsol_mcp_design_v1/PROGRESS.md`](docs/comsol_mcp_design_v1/PROGRESS.md).
