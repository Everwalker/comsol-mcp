## 2026-09-22 independent acceptance supersedes prior G3.3 claims

Current status: **IMPLEMENTED_WITH_OPEN_ACCEPTANCE_DEFECTS**. Prior PASS text below is historical and cannot certify current W17 behavior. Independent audit: `evidence/phase4_3/runs/independent_acceptance_20260922T004753Z/INITIAL_FINDINGS.json`. Fresh engine run `independent_live_baseline_20260922T0052Z` failed; its request/reply trail shows the cleared-solution negative control never executed because its Java source failed compilation, and the runner ignored the failed response. The preserved software baseline is 1648 passed / 1 skipped after rerunning outside the sandbox; software success does not resolve the engine or semantic defects. W18 remains unauthorized.

Required repairs include real Probe update/history, complete solution axes, array-preserving statistics, fail-closed complex/selection handling, typed node writes, project-scoped artifacts and assertion coverage. Existing reports are retained; this correction is not a rewrite of their dates or claims.

## 2026-09-22 clean-room re-run of the live matrix (this round closes the open defects above)

Everything below was produced on the recovered repository by re-running the live groups from scratch in this
round — no delivered PASS text was reused as evidence. All run directories live under
`evidence/phase4_3/runs/` and carry their own `source_manifest.json`, `isolation_receipt.json`,
`transcript.json`, request/reply trail and numeric evidence. Commits: `e14c946`, `cccf32e`, `54360a3`,
`24dce36`, `65d1c51`, `06567cd` (the final freeze commit that the closing ledger is bound to).

### Live matrix (frozen source, software venv, real COMSOL 6.4 build 293)

| Group | Run directory | Verdict |
|---|---|---|
| C00–C17 main runner (19 cases) | `frozen_19case_20260922T060438Z` | **19/19 PASS, exit 0** (C00 clean-tree binding, C17 ledger) |
| M1 single constant on a non-unit volume | `frozen_m1_*` | PASS |
| C04–C10 numeric (measures, axis, complex, coordinates) | `frozen_numeric_*` | PASS |
| C11 nodes / C14 Probe+Table | `frozen_nodes_*`, `frozen_probe_*` | 61 PASS / 29 PASS, 2 documented NOT_RUN each, both covered below |
| C12/C13 export and chunked artifact reads | `frozen_export_*` | 16 PASS, 2 documented NOT_RUN |
| C15 same-key retry, control plane during solve | `frozen_control_20260922T060857Z` | 5/5 PASS |
| Native failing study (unknown state + no replay) | `frozen_study-fault_20260922T061214Z` | PASS |
| C11-CutPlane (2D/3D) | `frozen_cutplane_20260922T060929Z` | 7/7 PASS (covers the `C11-CutPlane-3D` NOT_RUN of the nodes group) |
| Gate A chains a/b/c + protocol | `frozen_chains_*` | PASS; `gate_a_negatives.json` carries 7 native negative controls |
| Transient Probe history/axis | `frozen_probe-transient_*` | PASS (covers the `C14-history-axis-metadata` NOT_RUN of the static group) |
| Native API observations (axis/coordinate/measure/join/shape/outer) | `frozen_*-probe_*` | exploratory by design (`axis_probe` docstring: "not a passing numerical gate"); exit 0 and raw native evidence written, not counted as gates |

### Native API findings behind the repairs

1. **Probe history tables record the solver's internal time steps, not the stored output times.** The isolated
   native probe (`.hermes/cache/scratch/probe_probe_table_axis.py`, run against the same server) shows the
   probe's `Table` rows follow the solver's own step list; the stored output times are what
   `SolverSequence.getPVals()` reports. The verification therefore requires the recorded axis to *cover* the
   stored span (`_axis_covers_stored_times`) instead of being equal to it.
2. **`TableFeature` exposes no time getter.** The time column is only identifiable by its localized header
   (`_is_time_header` recognizes the zh-CN/English labels), which is why the check is label-based.
3. **`SolverSequence.study()` is a method, not a string property.** `getString("study")` always fails and had
   silently classified transient datasets as stationary; the dataset study binding now reads the method result.
4. **`getPVals()` is the stored output times** (`getPVals(1)` its first entry) — used as the published solution
   time axis, never re-derived from array lengths.
5. **The run's own mphserver can still serve a model created by a closed worker.** A post-close reference must
   therefore be judged on data identity: the reopened model must reproduce the closed worker's own
   solution-dependent integral, and a tag that was never created must be refused. A silent empty re-creation
   cannot pass either branch.
6. **`selection.set()` with entity ids the geometry does not expose raises `SelectionOutOfBoundsException` at
   the engine.** It is now classified as `SELECTION_MATCHED_NO_ENTITIES` (nothing was selected, nothing was
   measured) with the engine's own text preserved, instead of surfacing as a generic `SELECTION_APPLY_FAILED`
   after a failed engine mutation.
7. **A model whose stored solution was cleared exposes no datasets at all.** The cleared signature is therefore
   "no datasets", reported as `SOLUTION_CLEARED_OR_EMPTY`; a genuinely missing dataset keeps
   `DATASET_NOT_FOUND`.
8. **A same-key retry of a quiescent job does not dispatch a second solve** and leaves the job log unchanged —
   but only if the pre-retry snapshot is taken *after* quiescence. The earlier control compared a snapshot
   captured while the solve was still running, which made a correct replay look like new work.
9. **A native failing study reports `execution_state_unknown=true` with the cause code (`ENGINE_CALL_FAILED`),
   `safe_retry=false` and a reconciliation demand**; the same-key retry replays the same job without
   re-solving, and a refresh clears `dirty`. The case asserts that contract; a generic
   `EXECUTION_STATE_UNKNOWN` code is not required where a concrete cause exists.

### Resulting repairs (this round)

- Product: `_measure_spec.py` empty-selection classification; `_gate_a_reopen.py` cleared-artifact signature;
  `_g3_results.py` study binding, optional declared output times, finite-real scalar conversion,
  `_probe_manage.py` root/component probe mirroring, `_g3_w14.py` worker allow-list reality, catalog
  `probe.create.component`, `g33_node_probe_cases.py` success-status/quiescence/axis-coverage checks.
- Driver/tools: the 19-case runner (project-root-scoped artifact publishes, chain-A flattening, measure reads,
  canonical 4-axis step selection, table paths, complex-carrying cells, per-case traceback, `--only`) and the
  protocol tool (`study_fault_case` contract assertions).
- The full software suite is green after every change (final: see `evidence/phase4_3_acceptance.json`).

### Provenance of the product hardening

The product-side fixes above (result budget and shape bridge, ArtifactStore immutability, Probe/typed
CRUD, numeric axis handling, semantic module review) were produced by the preceding acceptance session on this
same workpack (Codex thread `01a0c690-c022-74d3-872d-539e1f5e035f`, 2026-09-22 00:43Z – 05:19Z, interrupted
while validating the transient Probe history case). That session committed nothing; its worktree state was
committed by the first commit of this session, `cccf32e`, together with this session's own fixes. Evidence it
left behind is kept as-is: `audit/semantic_module_review.json` (165 files scanned: 58 production Python, 4
production Java, 1 entrypoint, 78 tests, 24 tools; `reviewed_here=78`, `prior_review_only=7`,
`not_reviewed=80` reported honestly, plus its `historical_missing_references` list),
`evidence/phase4_3/runs/coverage_review_20260922T005512Z/coverage_review.md`,
`evidence/phase4_3/runs/coverage_review_20260922T045221Z/coverage_review.md`,
`evidence/phase4_3/runs/c13_final_20260922T034942Z/`,
`evidence/phase4_3/runs/artifact_immutable_20260922T051143Z/` and
`evidence/phase4_3/runs/full_software_wheel_20260922T051548Z/` (its wheel build succeeded; the pytest pass in
that run still had two failures that later commits fixed — the suite is green in this round).

### Machine state observed, not modified by this round

The shared COMSOL installation's `bin/servers/webbridge/conf/server.xml` is currently in its
RemoteAddrValve-applied form (`sha256 1a029e30…`, the tool's `EXPECTED_VALVE_SHA256`; mtime unchanged since
2026-09-21 07:54) while the pristine bytes are preserved at
`.phase1-private/g2-valve-proposal-webbridge-20260919T232153Z/server.original.xml` (`sha256 95478d76…`).
This round's runs took that state as their isolation precondition and verified it; they did not write the file.
Restoring it touches the shared installation and would invalidate the isolation precondition the live cases
assert, so it is left for an explicit decision by the operator.

# G3.3 Audit Findings and Technical Remediation (F01–F12)

## 1. Historical G3.2 Deficiencies Audit

An independent clean-room audit of the pinned G3.2 commit (`2cb46279...`) identified 12 critical defects across evidence auditing, mathematical formulations, array indexing, and runtime safety:

| ID | Category | G3.2 State / Defect | G3.3 Remediation | Verification Evidence |
|---|---|---|---|---|
| **F01** | Evidence Integrity | Mocks (`FakeReopenModel`, `FWiredTree`) were labeled as live acceptance in historical ledgers. | Downgraded to `CONTROL_UNIT` / `UNIT_CONTRACT` in `evidence/w17_correction.json`. Retained original ledgers byte-exact. | `C01`, `BASELINE_REVIEW.json` |
| **F02** | Measures & Statistics | Denominator in averages hardcoded to 1.0; all spatial aggregates mapped to `IntVolume` regardless of entity dimension (0D–3D); variance/std/RMS formulas missing. | Modularized into `MeasureSpec`. Maps dimensions to `IntLine`, `IntSurface`, `IntVolume`. Computes $M = \int w\,d\mu$, $\text{avg} = \int wf / M$, $\sigma = \sqrt{\int w(f-\mu)^2 / M}$. | `C04`, `C05`, `C06` |
| **F03** | Axisymmetric Measures | Axisymmetric weighting was a static boolean flag; risk of double-counting native COMSOL $2\pi r$ integration. | Explicit cross-section vs revolved volume verification; native $2\pi r$ factor honored without duplicate scaling. | `C07` (cylinder $R=2, H=3$) |
| **F04** | Solution Axis Indexing | Sliced outer array `[expr][solnum][vertex]` as `inner - 1` along Axis 0 (expressions), selecting different fields instead of time/solution steps; `outer_indices` hardcoded to `[1]`. | Modularized into `SolutionBinding` and `FieldArray`. Slices Axis 1 (solution step) while preserving expressions on Axis 0. | `C08`, `test_g3_3_remediation.py` |
| **F05** | Complex Data Integrity | `_transform_complex_data` silently padded 0.0 when imaginary extraction failed, converting complex field failures into corrupted real numbers. | Refactored `_complex_transform.py`. Rejects missing imaginary data on complex fields (fail-closed). Supports `preserve`, `real`, `imag`, `abs`, `phase`. | `C09` |
| **F06** | Point Evaluation Readback | `result_at_points` lacked coordinate scaling (m vs mm), frame checks, and readback distance comparison. | Validates point dimensions, scales coordinate units (m, mm, cm, um), verifies spatial frame, and compares readback coordinates within tolerance. | `C10` |
| **F07** | Dataset Dependency Chains | Upstream dataset references (`data` attribute) lacked cycle detection and provenance traversal. | Implemented `SolutionBinding.resolve_dataset_chain` with cycle detection (`DATASET_CYCLE_DETECTED`). | `C11` |
| **F08** | Export Security | `Path(dest).resolve()` allowed project root directory traversal (e.g. `../../etc/passwd`). | Implemented `ArtifactStore.resolve_safe_path` with strict project root containment and parent symlink escape checks (`ACCESS_VIOLATION`). | `C12` |
| **F09** | Export Atomicity & Rollback | Failed evaluations exported empty or corrupt files; destination overwrite occurred before validation. | Pre-validation of evaluation outcome; atomic temporary file creation followed by `os.replace`; pre-existing target file remains intact on error. | `C12` |
| **F10** | Probe vs Derived Values | Definitions probes (`model.probe()`) were conflated with Results Derived Values (`model.result().numerical()`). | Separated into `_probe_manage.py`. Dedicated CRUD for `DomainProbe`, `BoundaryProbe`, `PointProbe`, `GlobalProbe`. | `C14` |
| **F11** | Bounded Chunk Streaming | Chunk verification loaded the entire file into RAM, defeating streaming pagination. | Implemented `ArtifactStore.read_chunk` with seek-based slice reading (capped at 16MB) and per-chunk SHA256 hashing. | `C13` |
| **F12** | Gate A Live Reopen | No actual live COMSOL MPH file reopen without re-solve was performed. | Implemented `comsol_mcp/_gate_a_reopen.py`. Verified identical-SHA saved MPH models reopened in fresh worker with direct stored solution readback across 3 physics chains and 4 negative controls. | `C03` |

---

## 2. Verification Outcomes

1. **AST Counterexamples:** `tools/reproduce_w17_findings.py` executed against `repository`:
   - Counterexamples tested: 5
   - Reproduced defects: **0**
2. **Unit Test Suite:** 166 passed, 1 skipped, 0 failed across all unit test modules.
   - **Correction (2026-09-22 independent recheck, D2/D3):** that figure is a hand-picked module subset, not the repository suite. The full `pytest` run on the same worktree was **6 failed, 1617 passed, 1 skipped** at delivery (`fix_20260922/logs/baseline_full_pytest.txt`). On the fix branch the full suite is **1646 passed, 1 skipped, 0 failed**.
3. **Live Acceptance (C00–C17):** 18 passed, 0 failed in clean-room live suite run `g3_3_acceptance_20260921T135429Z_9dozr6l_`.
   - **Correction (2026-09-22, D14):** the delivered run recorded the C03 chain-A re-solve as verified while the driver called `solve("std1")` at line 838 with no assertion and hardcoded `independent_resolve_verified: True` at line 904. The re-solve is now its own case (C03R) that asserts the reopened model's staleness, re-solves and compares against independently computed expectations, so the live suite has 19 cases.

## Independent review: transient Probe and immutable artifact reads

New run `public_transient_probe_20260922T0525Z` created a GlobalVariable probe but its solve raised a native null-parent-model error. COMSOL 6.4 Programming Reference Manual p181 requires assigning the component using `model.probe(tag).model(component_tag)`; the previous assumption that a GlobalVariable must forbid a component was incorrect. The reference SHA and local API inspection are recorded in the independent run's `probe_api_reference.json`. Creation alone is not an operational probe acceptance. Nonempty native transient table/time readback remains pending until the corrected implementation passes a new run.

The same failure exposed a second boundary: study.run retained a native execution-state-unknown error only in text and returned a known partial failure because timestamp readback was readable. Timestamp availability cannot override an explicit engine uncertainty witness; structured uncertainty propagation is under correction.

Final review also found that artifact.read hashed one open file and reopened the pathname to read the chunk. A same-size replacement could pair an old digest with new bytes. The correction hashes and reads through one descriptor and verifies fd/path identity; no-overwrite export uses atomic no-clobber publication. Fresh race regression and public chunk rerun are required before closure.
