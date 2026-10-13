# Solver routes handoff

Candidate solver-routes-20261011-r16 is frozen for Root review. The exact shared file bytes are recorded in FREEZE_MANIFEST.json; shared files contain concurrent W25 and model-delivery changes as noted there.

The five Worker-backed operations and solver.log_read are integrated through the managed dispatch/catalogue path. Route coverage and evidence boundaries are listed in the manifest. The focused software regression is 229 passed, 0 skipped, 0 failed under the strict network guard.

Managed route positives cover solver.solution_inspect, solver.solution_clear, solver.resource_configure, study.initial_solution_set, result.sample_grid, and direct/nested solver.log_read. Clear incomplete readback and post-dispatch exception both remain EXECUTION_STATE_UNKNOWN. The route-layer engine data are synthetic fixtures; result.sample_grid uses the established result adapter, and the route test substitutes a fixture adapter response.

COMSOL Server, native model operations, GUI, and numerical/scientific acceptance were not run. The Java loopback test is separate software evidence and its Windows-only lock-owner case is skipped. Windows native publisher verification remains not run due the previously recorded connectivity block.

Official API references used by the implementation include COMSOL 6.4 Programming Reference doc 3626/chunk 10968 (clear semantics; isEmpty() is readback while isInitialized() may remain true), doc 3626/chunk 11450 (StudyFeature initial-solution settings), and COMSOL 6.4 solver API doc 4626/chunk 17562 (Direct solver resource properties). Exact reference hashes are retained in source comments and earlier evidence.

This is a source/software candidate freeze, not independent final acceptance. Root review remains pending.
