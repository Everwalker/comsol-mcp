# COMSOL MCP full-project progress

**Current stage: W21 initial-stage software accepted; fresh6.4 full-probe run FAILED before stage acceptance; cleanup verified, distinct discovery freeze preparing. Overall project: PARTIAL.** The original scope remains 26 work packages, 272 actions and 60 tests across six targets, subject to recorded user skips. The final independent Reviewer has not started.

The public stage route now separates initial-state dispatch prerequisites from post-solve acceptance. It captures every declared target field, automatic three-expression unit controls, exact solution tuple, actual active Variables field identity, owned current-mesh evidence and saved artifact. Variables xmesh allocation/cleanup uses a private managed EVALUATE ticket after unique-feature selection. Successor mapping/frame/history and continuity/conservation checks remain enforced.

**565 PASS, 1 Windows-conditional SKIP, 0 failures/errors across 566 unique cases.** This includes 280 combined runner/initial-stage/store/successor cases, 245 affected backend/G3/registration cases and 40 passing Worker cases plus the platform skip, with no overlap. Offline Java compilation produced 14 real class files. Main reviewed the cohesive diff and final successor fixture adaptation, independently checked 24 code/test hashes and the compact receipt evidence hashes, and verified the actual software producer positive plus 11 rejected tamper cases. Missing/corrupt/UNKNOWN RPC completions, normal job.wait expiry and xmesh budget/ticket findings are repaired. Earlier failed and intermediate runs remain preserved and are not added to this count.

[Canonical scoped checkpoint and rebuild entry](docs/full_project_execution/project_session/STAGE_BACKEND_SOFTWARE_CHECKPOINT.json#initial_stage_integration_20260930). Candidate code/test fingerprint: `ffdc8c25e4528c2cb42c358a6e1ee4e89898fb172a8891dbee3bcbc86766442a`. The compact receipt retains exact source/report hashes, commands and limits; raw evidence stays under `execution-scratch/w21-initial-stage-integration-20260930` and `execution-scratch/w21-initial-stage-main-review-20260930`. Replays require fresh APFS basetemp and XML paths; occupied runs must not be reused.

**Limits:** this is scoped software acceptance using controlled fake Worker responses through actual daemon/backend/store/transport paths. The new initial-stage producer has not run against COMSOL. Current-mesh content does not establish historical mapping, frame/history transfer or scientific correctness. Earlier 195-test automatic-unit software closure remains historical evidence in the same checkpoint. Fresh Windows 6.4 → 6.3 native execution, original W21 successor transfer and W23–W26 remain required.

## Current native preparation

The scoped software candidate was committed and synchronized to GitHub as `0b5d9efbb517cb4365227ef82bd932b97a4b0b1e`; local and remote full hashes match. Its local archive contains 107 verified source files plus three snapshot metadata files, all matching committed Git blobs. Fresh Windows inventory found non-reparse parents and no existing task root; the dedicated `C:\Temp\w21r15` directory was then created empty. Unrelated listeners were preserved.

**Direct approval received; SCP and remote source verification succeeded.** The exact six-file payload is unchanged. Same Luna verified all six remote hashes,110 archive members,107 source files, snapshot source0b5d9ef and candidate import identity. Offline Java compile PASS with22 real classes after generated-argfile repairs. Python prepare wrote one valid PREPARED_ONLY freeze; outer PowerShell postprocessing failed and is retained. Main independently verified107 committed sources, raw/canonical hashes, exact budgets and fresh zero-engine inventory; the initial full-probe6.4 execute later failed at166/128table rows beforestage/solve; exactcleanup verified. A distinct discovery-only freeze is now preparing. Prior automatic rejections and blocked audit remain retained. Public document commit4a37191 was approved, pushed and full-SHA verified; subsequent working-state updates are local.

## Verified Windows native baseline

The canonical public MCP chain actually completed **Windows 6.4 build 293 → Windows 6.3 build 290** on runtime source `45399a5`: start → connect → small model/geometry/mesh → one solve → exact solution tuple → complete temperature field readback → owned cleanup. Both runners exited 0, returning `[1,1,5,637]`, unit K and selected tuple outer=1/inner=5/solnum=5. Selected temperature maxima were approximately 304.380 K and 304.391 K. The coarse `hauto=9` model proves this runtime chain, not convergence or physical correctness.

Subsequent native unit controls on source `40c9838` also passed in that order: runs `w21-64-20260929T165606Z-142016ea` and `w21-63-20260929T170038Z-0036f835`. One field read requested `T`, `T/1[K]`, `1` without requested units; both returned `[3,1,5,637]` with K/1/1. Normalization and constant-one maximum errors were 0, all imaginary values were 0, all 15 actions succeeded with no UNKNOWN, and exact owned PID/birth exit, listener absence, Worker retirement and temporary Eval removal were verified. The 6.3 run pins the actual successful same-profile 6.4 receipt.

[Canonical two-version checkpoint](docs/full_project_execution/project_session/WINDOWS64_V2_SESSION_START_UNKNOWN_CHECKPOINT.json#canonical_native_chain_windows_round_20260929) and [native unit-control checkpoint](docs/full_project_execution/project_session/PRODUCTION_RUNNER_SOFTWARE_CHECKPOINT.json#auto_unit_controls_native_windows_20260930) retain source/archive/freeze/result fingerprints and rebuild entries. Full evidence stays on SSD under `execution-scratch/w21-solver-path-w21r12` and `execution-scratch/w21-auto-unit-native-w21r14`.

Necessary Windows repairs were verified by real solve/readback: explicit existing RPC wait, short state roots for new homes and the nested COMSOL solver-file path guard. Budgets were unchanged. Historical UNKNOWN/start/path failures remain preserved, with their scoped quiescence/recovery evidence; they are not rewritten as PASS or replayed. Targeted `info/Shape` metadata was actually empty, so it still provides no active-DOF/unit proof.

## Next work and recovery entry

1. Prepare and review a distinct discovery-only Windows6.4 initial-stage freeze on unchanged source0b5d9ef. Preserve the failed full-probe run and unchanged full output/successor validators; do not replay its freeze.
2. Run the resulting native stage chain on Windows 6.4, then 6.3. Do not inherit native PASS from the earlier canonical source or replay historical freezes.
3. Continue original W23 optics/PML/Qabs, W24 shape/UV–thermal–cure–cooling/history, W25 platform/Desktop and W26 delivery requirements. Then freeze the complete candidate for the independent Reviewer and repair/retest loop.

Authoritative recovery: [RESUME.json](docs/full_project_execution/state/RESUME.json), [TASKS.json](docs/full_project_execution/state/TASKS.json), [MAIN_ACCEPTANCE_PLAN.md](docs/full_project_execution/MAIN_ACCEPTANCE_PLAN.md) and [MASTER_GOAL.md](docs/full_project_execution/MASTER_GOAL.md). Do not bootstrap/reset existing state. Native reproduction uses `tools/run_w21_stage_native.py prepare/execute` with a fresh isolated short home, explicit installed COMSOL/JDK and the actual 6.4 prerequisite receipt for 6.3.

Both Mac COMSOL 6.3 targets remain USER_REQUESTED_SKIP / NOT_RUN. Intel Mac 6.4 availability is unconfirmed. The [TMP migration summary](docs/full_project_execution/state/TMP_MIGRATION_SUMMARY.json) records retained inactive paths and occupied roots; new scratch is on SSD. No full-project completion is claimed.

## 2026-09-30 原要求索引补齐

W22 的历史 Windows6.3/6.4 独立 scoped approval 已逐项映射回静态非轴对称热源、阵列/环功率、距离扫描和热预算，见 `docs/full_project_execution/w22/W22_ORIGINAL_REQUIREMENT_MAPPING.json`。原 T016/T045 更宽矩阵仍 PARTIAL，历史批准源码与当前源码等价性 UNVERIFIED；未重跑原生模型。W24 当前索引补齐已有物理控制与 T058 假设报告软件检查点，旧失败和科学验收缺项保留。后继/API 与 W26 readiness 仅为规划证据。精确六文件 SCP 上传批准仍待回复，Windows prepare/compile/native 本次均 NOT_RUN。

## 连续目标阻塞审计 — 2026-09-30

Windows 精确六文件源码上传批准连续三轮未回复，自动审批拒绝仍有效；已完成的独立本地实现、API/范围映射与交付 readiness 审查不替代原生验证。公共 GitHub 文档提交 `4a37191` 的推送也被自动审批拒绝，另有精确批准问题待回复。目标处于 BLOCKED_EXTERNAL_WRITE_APPROVAL，原 W01–W26 和最终独立 Reviewer 均未报完成。原生源码冻结仍为 `0b5d9ef`；本地 `4a37191` 已提交未推送，本条阻塞状态是其后的未提交续跑更新。批准恢复后核对精确 payload/commit，只执行获准的外部动作。

## 精确批准后恢复 — 2026-09-30

用户直接回复“批准”后，目标恢复 ACTIVE。文档提交 `4a37191349674badf305dccddeec0f3008522eeb` 已按原公共仓库/普通非 force 通道推送，fetch 后 HEAD 与 origin/main 完整 SHA 一致。Windows 原六文件 SCP 已 exit0；远端 hash/member/import 验证、离线编译和6.4 prepare 正在由同一 Luna 连续执行。冻结源码仍为 `0b5d9ef`，未重打包。新 COMSOL engine/solve 尚未执行；旧拒绝、blocked 审计及科学未验项保留为历史。

## 6.4 initial-stage freeze reviewed — 2026-09-30

Existing run `w21-64-20260930T090420Z-8a05279f`, canonical freeze `9e8c580c69c9a2efb45711ce9f9740f7c5c9c34943f6ddc4035df7c188a0265c`, passed main prepare review. Raw UTF-8 freeze/state valid; zero dispatched counters and no Java/COMSOL at09:15:11Z. Main review receipt is retained in SSD `execution-scratch/w21-initial-native-w21r15/main-prepare-review-002.json`. Same Luna now has exact6.4 execute authorization; native acceptance remains pending. No prepare replay or source/budget change. Conditional6.3 and original W21–W26/final Reviewer remain open. These four current-state updates are local and not yet synchronized.

## 6.4 initial-stage execution failed — 2026-09-30

The reviewed run exited2 after public fixture/probe operations, with `probe output is incomplete or claims a scope outside metadata capture`. Native stage acceptance is FAIL/NOT_REACHED, not PASS. Executor reports original failure cleanup completed, Worker retired/server stopped and fresh engine absence; main raw-evidence review and probe diagnosis are underway. No6.3 or replay authorized. Original software565PASS and all previous native scopes remain separate.

## Native failure diagnosis verified; new discovery freeze preparation — 2026-09-30

Raw original probe result confirms `ht/info/Expression` has166 rows, above the unchanged128-row cap. This run remains FAILED; stage_define/run and study/solver dispatch0. Main independently matched stop request/job to owned server PID27248/birth1790760476824, confirmed child-reaped/exit/listener-absent and Worker retirement; post-run engine inventory clear, unrelated15240/14718 retained. Raw/private SQLite result evidence stays onSSD and is not published. Same source0b5 will prepare a distinct existing `--probe-discovery-only` initial-stage freeze in absent short`s65` root. This pre-probe remains field/tag capture only, UNVERIFIED, while complete actual field/unit/currentmesh/tuple/artifact/RPC output acceptance and all budgets stay unchanged. Prepare only until main review; no6.3.
