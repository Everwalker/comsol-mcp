# Managed model lifecycle and code recipes handoff

This checkpoint freezes the production software implementation for `model.list`, `model.create`, `model.load`, `model.tree`, `model.save`, `model.clone`, `model.close`, `code.recipe_register`, and `code.recipe_run`.

The exact source list, per-file SHA-256 values, complete source patch, runtime boundaries, test commands, and raw JUnit hashes are in [SOURCE_FREEZE.json](SOURCE_FREEZE.json). The full changed-source patch is [MODEL_LIFECYCLE_RECIPE_SOURCE.patch](MODEL_LIFECYCLE_RECIPE_SOURCE.patch).

Validation at this checkpoint:

- Managed model and recipe routes plus selected ControlDaemon, registry, permission, artifact, and W24 regressions: **157 passed, 0 skipped, 0 failures**.
- W25 Desktop READY/non-ready fence and session lifecycle regressions: **210 passed, 0 skipped, 0 failures**.
- Persistent Java Worker local software loopback: **40 passed, 1 macOS-only ACL skip, 0 failures**. The dispatched-engine-method allowlist test was deliberately deselected pending integration/review of the Windows-owned `objectNames`, `exportFinal`, and `insert` methods.
- The actual ControlDaemon/OperationStore route used a synthetic Worker fixture. The Worker loopback result is software-only. COMSOL native execution is **NOT_RUN**; numerical/physical acceptance is not claimed; target portability is **UNVERIFIED**; independent review and project acceptance remain open.

Shared source ownership is transferred to the Windows agent for definitions/domain and job-log integration. This agent will not edit the frozen shared route/backend/registry/Worker files until that full integration unit returns with its own freeze.
