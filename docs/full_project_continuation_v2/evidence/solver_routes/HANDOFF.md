# Solver routes handoff

Candidate solver-routes-20261011-r17 is frozen for Root review. The exact shared source bytes are recorded in FREEZE_MANIFEST.json; shared files contain concurrent W25 and model-delivery changes as noted there. The r16 manifest and handoff remain preserved as historical candidate receipts.

The five Worker-backed operations and solver.log_read are integrated through the managed dispatch/catalogue path. Managed route positives cover all six operations. The focused software regression is 232 passed, 0 skipped, 0 failed under the strict network guard.

study.initial_solution_set now checks every model.sol sequence association before writing: model.sol().tags(), get(tag), and each sequence.study() must be readable, and the source study must map to exactly one sequence whose tag equals the source SolutionInfo binding. Two same-study sequence selections and incomplete study metadata fail before setters. solver.solution_clear only reports CLEARED when isEmpty() returns true; dispatched exceptions and incomplete readback remain EXECUTION_STATE_UNKNOWN.

Route-level engine data are synthetic fixtures. result.sample_grid uses the existing result adapter but substitutes its adapter response in managed-route coverage; scientific acceptance is NOT_EVALUATED. COMSOL Server, native model operations, GUI, and numerical/scientific acceptance were not run. The sibling Java loopback software test is separate; its Windows-only lock-owner case is skipped. Windows publisher native verification remains not run due the recorded connectivity block.

Official API references include COMSOL 6.4 Programming Reference doc 3626/chunk 10968 (clear readback), doc 3626/chunk 11450 (StudyFeature initial-solution settings), and COMSOL 6.4 solver API doc 4626/chunk 17562 (Direct solver resource properties).

This is a source/software candidate freeze, not independent final acceptance. Root review remains pending.
