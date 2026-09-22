## 2026-09-22 independent acceptance supersedes prior G3.3 claims

Current status: **IMPLEMENTED_WITH_OPEN_ACCEPTANCE_DEFECTS**. Prior PASS text below is historical and cannot certify current W17 behavior. Independent audit: `evidence/phase4_3/runs/independent_acceptance_20260922T004753Z/INITIAL_FINDINGS.json`. Fresh engine run `independent_live_baseline_20260922T0052Z` failed; its request/reply trail shows the cleared-solution negative control never executed because its Java source failed compilation, and the runner ignored the failed response. The preserved software baseline is 1648 passed / 1 skipped after rerunning outside the sandbox; software success does not resolve the engine or semantic defects. W18 remains unauthorized.

Required repairs include real Probe update/history, complete solution axes, array-preserving statistics, fail-closed complex/selection handling, typed node writes, project-scoped artifacts and assertion coverage. Existing reports are retained; this correction is not a rewrite of their dates or claims.

# Progress location

W01 and subsequent work-package progress is maintained in [`docs/comsol_mcp_design_v1/PROGRESS.md`](docs/comsol_mcp_design_v1/PROGRESS.md).
