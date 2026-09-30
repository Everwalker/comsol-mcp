# COMSOL MCP full-project progress

**Current stage: W21 initial-stage software accepted; fresh native validation NOT_RUN. Overall project: PARTIAL.** The original scope remains 26 work packages, 272 actions and 60 tests across six targets, subject to recorded user skips. The final independent Reviewer has not started.

The public stage route now separates initial-state dispatch prerequisites from post-solve acceptance. It captures every declared target field, automatic three-expression unit controls, exact solution tuple, actual active Variables field identity, owned current-mesh evidence and saved artifact. Variables xmesh allocation/cleanup uses a private managed EVALUATE ticket after unique-feature selection. Successor mapping/frame/history and continuity/conservation checks remain enforced.

**565 PASS, 1 Windows-conditional SKIP, 0 failures/errors across 566 unique cases.** This includes 280 combined runner/initial-stage/store/successor cases, 245 affected backend/G3/registration cases and 40 passing Worker cases plus the platform skip, with no overlap. Offline Java compilation produced 14 real class files. Main reviewed the cohesive diff and final successor fixture adaptation, independently checked 24 code/test hashes and the compact receipt evidence hashes, and verified the actual software producer positive plus 11 rejected tamper cases. Missing/corrupt/UNKNOWN RPC completions, normal job.wait expiry and xmesh budget/ticket findings are repaired. Earlier failed and intermediate runs remain preserved and are not added to this count.

[Canonical scoped checkpoint and rebuild entry](docs/full_project_execution/project_session/STAGE_BACKEND_SOFTWARE_CHECKPOINT.json#initial_stage_integration_20260930). Candidate code/test fingerprint: `ffdc8c25e4528c2cb42c358a6e1ee4e89898fb172a8891dbee3bcbc86766442a`. The compact receipt retains exact source/report hashes, commands and limits; raw evidence stays under `execution-scratch/w21-initial-stage-integration-20260930` and `execution-scratch/w21-initial-stage-main-review-20260930`. Replays require fresh APFS basetemp and XML paths; occupied runs must not be reused.

**Limits:** this is scoped software acceptance using controlled fake Worker responses through actual daemon/backend/store/transport paths. The new initial-stage producer has not run against COMSOL. Current-mesh content does not establish historical mapping, frame/history transfer or scientific correctness. Earlier 195-test automatic-unit software closure remains historical evidence in the same checkpoint. Fresh Windows 6.4 → 6.3 native execution, original W21 successor transfer and W23–W26 remain required.

## Current native preparation

The scoped software candidate was committed and synchronized to GitHub as `0b5d9efbb517cb4365227ef82bd932b97a4b0b1e`; local and remote full hashes match. Its local archive contains 107 verified source files plus three snapshot metadata files, all matching committed Git blobs. Fresh Windows inventory found non-reparse parents and no existing task root; the dedicated `C:\Temp\w21r15` directory was then created empty. Unrelated listeners were preserved.

**UPLOAD blocked by automatic approval review; PREPARE/remote compile/EXECUTE NOT_RUN.** Both SCP attempts were rejected before starting. The original user approval for this and future project source uploads was verified, but the reviewer still requires direct approval of these six files to this exact Windows destination. The precise 1,154,429-byte payload manifest and both rejection records remain in `execution-scratch/w21-initial-native-w21r15`; an approval question is pending. No alternate transport or indirect execution is authorized. Local successor API/evidence planning is recorded without changing the frozen upload files; the next native action awaits direct upload approval.

## Verified Windows native baseline

The canonical public MCP chain actually completed **Windows 6.4 build 293 → Windows 6.3 build 290** on runtime source `45399a5`: start → connect → small model/geometry/mesh → one solve → exact solution tuple → complete temperature field readback → owned cleanup. Both runners exited 0, returning `[1,1,5,637]`, unit K and selected tuple outer=1/inner=5/solnum=5. Selected temperature maxima were approximately 304.380 K and 304.391 K. The coarse `hauto=9` model proves this runtime chain, not convergence or physical correctness.

Subsequent native unit controls on source `40c9838` also passed in that order: runs `w21-64-20260929T165606Z-142016ea` and `w21-63-20260929T170038Z-0036f835`. One field read requested `T`, `T/1[K]`, `1` without requested units; both returned `[3,1,5,637]` with K/1/1. Normalization and constant-one maximum errors were 0, all imaginary values were 0, all 15 actions succeeded with no UNKNOWN, and exact owned PID/birth exit, listener absence, Worker retirement and temporary Eval removal were verified. The 6.3 run pins the actual successful same-profile 6.4 receipt.

[Canonical two-version checkpoint](docs/full_project_execution/project_session/WINDOWS64_V2_SESSION_START_UNKNOWN_CHECKPOINT.json#canonical_native_chain_windows_round_20260929) and [native unit-control checkpoint](docs/full_project_execution/project_session/PRODUCTION_RUNNER_SOFTWARE_CHECKPOINT.json#auto_unit_controls_native_windows_20260930) retain source/archive/freeze/result fingerprints and rebuild entries. Full evidence stays on SSD under `execution-scratch/w21-solver-path-w21r12` and `execution-scratch/w21-auto-unit-native-w21r14`.

Necessary Windows repairs were verified by real solve/readback: explicit existing RPC wait, short state roots for new homes and the nested COMSOL solver-file path guard. Budgets were unchanged. Historical UNKNOWN/start/path failures remain preserved, with their scoped quiescence/recovery evidence; they are not rewritten as PASS or replayed. Targeted `info/Shape` metadata was actually empty, so it still provides no active-DOF/unit proof.

## Next work and recovery entry

1. Synchronize the scoped initial-stage software candidate, then package a fresh committed source and prepare/review the Windows 6.4 freeze. Preserve successor mapping/frame/history and frozen continuity/conservation checks.
2. Run the resulting native stage chain on Windows 6.4, then 6.3. Do not inherit native PASS from the earlier canonical source or replay historical freezes.
3. Continue original W23 optics/PML/Qabs, W24 shape/UV–thermal–cure–cooling/history, W25 platform/Desktop and W26 delivery requirements. Then freeze the complete candidate for the independent Reviewer and repair/retest loop.

Authoritative recovery: [RESUME.json](docs/full_project_execution/state/RESUME.json), [TASKS.json](docs/full_project_execution/state/TASKS.json), [MAIN_ACCEPTANCE_PLAN.md](docs/full_project_execution/MAIN_ACCEPTANCE_PLAN.md) and [MASTER_GOAL.md](docs/full_project_execution/MASTER_GOAL.md). Do not bootstrap/reset existing state. Native reproduction uses `tools/run_w21_stage_native.py prepare/execute` with a fresh isolated short home, explicit installed COMSOL/JDK and the actual 6.4 prerequisite receipt for 6.3.

Both Mac COMSOL 6.3 targets remain USER_REQUESTED_SKIP / NOT_RUN. Intel Mac 6.4 availability is unconfirmed. The [TMP migration summary](docs/full_project_execution/state/TMP_MIGRATION_SUMMARY.json) records retained inactive paths and occupied roots; new scratch is on SSD. No full-project completion is claimed.

## 2026-09-30 原要求索引补齐

W22 的历史 Windows6.3/6.4 独立 scoped approval 已逐项映射回静态非轴对称热源、阵列/环功率、距离扫描和热预算，见 `docs/full_project_execution/w22/W22_ORIGINAL_REQUIREMENT_MAPPING.json`。原 T016/T045 更宽矩阵仍 PARTIAL，历史批准源码与当前源码等价性 UNVERIFIED；未重跑原生模型。W24 当前索引补齐已有物理控制与 T058 假设报告软件检查点，旧失败和科学验收缺项保留。后继/API 与 W26 readiness 仅为规划证据。精确六文件 SCP 上传批准仍待回复，Windows prepare/compile/native 本次均 NOT_RUN。
