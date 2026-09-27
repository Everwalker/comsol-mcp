# P0 完整动作—生产路由审计

审计快照：2026-09-26T04:56:47Z UTC。冻结目录 SHA-256 `f912d417a0fccb6b099377306018d824c8d437e4774525e92f77814a49d814de`，按原 272 条 action 对照。本轮只有源码/AST 静态读取；没有导入生产模块、运行测试、COMSOL、GUI/computer use，也没有改生产代码、环境或主状态。

JSON 附件提供 272 条逐项地图：冻结参数字段/效果、MCP 直达工具与签名、legacy fallback/近似工具、canonical registry 类别与 handler 行号、字段适配、能力限制、补齐家族和源码 SHA-256。Canonical 只表示找到当前代码路径；状态为未验收，不能替代 fixture/native/platform evidence。

## 计数快照

| 项 | 数量 |
|---|---:|
| frozen_actions | 272 |
| domains | 33 |
| canonical_registry_routes_total | 179 |
| canonical_g2_routes | 32 |
| canonical_control_routes | 8 |
| canonical_control_compute_routes | 1 |
| canonical_g3_routes | 138 |
| direct_action_name_present | 29 |
| legacy_fallback_exact_name | 3 |
| semantic_contract_mismatches | 4 |
| actions_without_canonical_registry_route | 93 |
| `CANONICAL_ROUTE_CONTRACT_MISMATCH` | 4 |
| `CANONICAL_ROUTE_PRESENT_UNVERIFIED` | 171 |
| `DIRECT_LEGACY_ONLY` | 4 |
| `EXPLICIT_UNSUPPORTED_RESPONSE_ONLY` | 2 |
| `IN_PROGRESS_SNAPSHOT_ROUTE_PRESENT` | 3 |
| `LEGACY_ANALOGUE_ONLY` | 83 |
| `NO_PRODUCTION_ROUTE` | 5 |

Registry 的附加 `code.describe_java` 与 G3 的 `stage.checkpoint_create` 均不在冻结 272 条，不计 contract drift。`job.resume/model.adopt/model.inspect` 有 canonical 路径，但按主执行指示标作进行中快照；最终验收要重算源 hash 并复核实现。

## 必须先处理的发现

- **R001 BLOCKING_ACTION_GAP** — Frozen action has no usable canonical route, or its exact route always returns an explicit unsupported response; legacy analogues do not satisfy its contract.
  - 涉及：`runtime.discover`, `runtime.inspect`, `runtime.doctor`, `runtime.license_checkout`, `runtime.render_probe`, `runtime.compatibility_report`, `project.create`, `project.inspect`, `project.contract_set`, `project.policy_set`, `project.permissions`, `project.state_export`, `session.list`, `session.connect`, `session.start`, `session.inspect`, `session.reconnect`, `session.disconnect`, `session.stop`, `session.health`, `session.recover`, `model.list`, `model.create`, `model.load`, `model.tree`, `model.save`, `model.clone`, `model.close`, `model.dependencies`, `model.package`, `model.rebuild_target`, `model.compare`, `node.create`, `node.copy`, `node.remove`, `node.label_set`, `node.active_set`, `node.move`, `node.selection_get`, `node.selection_set`, `api.describe`, `api.invoke`, `api.probe`, `code.recipe_register`, `code.recipe_run`, `parameter.import`, `parameter.export`, `function.evaluate`, `function.validate`, `selection.rebind`, `geometry.export`, `geometry.repair`, `definition.mapping_validate`, `material.import`, `physics.pde_manage`, `mesh.import`, `mesh.export`, `mesh.convergence_study`, `study.initial_solution_set`, `solver.solution_inspect`, `solver.solution_clear`, `solver.log_read`, `solver.resource_configure`, `result.sample_grid`, `result.mode_overlap`, `export.report`, `export.evidence_bundle`, `metric.define`, `metric.list`, `metric.evaluate`, `metric.remove`, `metric.compare`, `experiment.design`, `experiment.inspect`, `experiment.case_result`, `experiment.stage_define`, `experiment.stage_run`, `experiment.state_map`, `checkpoint.diff`, `checkpoint.branch`, `artifact.register`, `artifact.list`, `artifact.inspect`, `artifact.preview`, `artifact.publish`, `artifact.verify`, `desktop.status`, `desktop.bind`, `desktop.show_model`, `desktop.select_node`, `desktop.capture`, `desktop.action`, `desktop.shell_execute`, `desktop.migrate_standalone`
- **R002 BLOCKING_CONTRACT_MISMATCH** — Four exact operation IDs appear in G3 maps, but handler semantics or artifact-reference resolution conflict with the frozen action contract; registry membership overstates usable coverage.
  - 涉及：`experiment.run`, `solver.solution_transfer`, `function.data_import`, `geometry.import`
- **R003 IN_PROGRESS_SNAPSHOT** — Canonical routes exist in this snapshot, but all three operations are in primary executor scope; final schema/permissions/readback/idempotency and regression acceptance remains pending.
  - 涉及：`job.resume`, `model.adopt`, `model.inspect`
- **R004 GUI_ONLY_ACCEPTANCE** — W25 routes are absent; even after API/CLI implementation, native window identity, dirty-model migration, permissions and target-window control need genuine GUI validation. See W25 audit; fixtures cannot certify it.
- **R005 NO_NATIVE_ACCEPTANCE_CLAIM** — This report is static source evidence only; frozen per-target/COMSOL action acceptance remains separate.
- **R006 ROUTED_BUT_UNUSABLE** — `function.evaluate` 映射后明确拒绝；`function.data_import`/`geometry.import` 未把 `artifact_id` 解析为授权 ArtifactStore 文件路径。详见下表静态源码位置和最小适配方向。

`experiment.run` 虽在 G3 映射表，但实际目标函数是有界优化器，要求 objective、parameter bounds、constraints、study、definition；冻结 action 要求 experiment_id/resources/timeout。`solver.solution_transfer` 实际调用 W21 staged transfer，要求已登记 checkpoint、目标 stage、同网格同变量映射和采样容差；冻结 schema 使用 source SolutionSpec/target NodePath/mapping。`function.data_import` 与 `geometry.import` 还分别要求未声明/未解析的 engine path，而不是 ArtifactStore 对 `artifact_id` 的解析；`function.evaluate` 走 exact 映射后仍总是 `API_UNSUPPORTED`。这些动作必须实现原合同或明确保持 unsupported，不能把 exact ID 注册视为完成。


## 注册路由存在但不满足冻结动作

| action | 静态证据 | 精确缺口 | 最小补齐方向 |
|---|---|---|---|
| `function.evaluate` | `comsol_mcp/_g3_w13.py:1364-1414` | handler 校验输入并 probe `functionNames()` 后，无条件抛 `API_UNSUPPORTED`；没有采样 API 调用。 | 先以版本化 COMSOL 证据确认取样 API，再实现点/单位/导数边界和失败读回测试；未确认前保持明确 unsupported。 |
| `function.data_import` | `comsol_mcp/_g3_w13.py:1216-1246` | `artifact_id` 只做字符串校验；实际 filename 只能来自 `layout.file_path`/`layout.resolved_path`，artifact id 单独使用被拒绝。 | 经 ArtifactStore 授权解析 ID→engine path，并绑定 hash/格式/大小校验和导入后读回。 |
| `geometry.import` | `comsol_mcp/_g3_w14.py:1650-1674` | 将 `artifact_id` 作为 COMSOL `filename` 直接传入；没有 ArtifactStore ID 查找，当前仅能传 engine-visible path。 | 增加授权 artifact ID 解析、引擎可见性和 hash 校验，再执行 geometry import。 |

## 最小补齐家族

| 家族 | 域 | 动作数 | 当前路由状态 | 精确缺口与最小补齐方向 |
|---|---|---:|---|---|
| **F01 runtime install/licensing** | runtime | 8 | CANONICAL_ROUTE_PRESENT_UNVERIFIED 2, LEGACY_ANALOGUE_ONLY 6 | 只有 runtime.capabilities/license_inspect exact；其他六个 runtime 动作缺 handler；license inspect须不占 seat。 补 read-only runtime inventory/inspect/doctor adapter；把 license checkout 单独放在显式 host-control 授权路径，保持 license inspection 不占 seat。 |
| **F02 registry contract** | registry | 5 | CANONICAL_ROUTE_PRESENT_UNVERIFIED 5 | 五个 registry action exact；G2 allowlist/strict catalog validator 才是执行边界，generic ActionResult 不是 per-domain data acceptance。 维持源目录/allowlist 分离；补全 per-action data schema，并仅在 normal/failure/readback checks 后广告为 implemented。 |
| **F03 project and policy** | project | 6 | LEGACY_ANALOGUE_ONLY 6 | 无 project entity/policy store；project_root/path guards 不等价于 project authorization CRUD。 建立持久 project record/store，校验授权 workspace/root，提供 contract/policy schema、权限读回及 project state export。 |
| **F04 session ownership** | session | 9 | DIRECT_LEGACY_ONLY 1, LEGACY_ANALOGUE_ONLY 8 | 旧 endpoint/health 工具不建立 runtime-bound session ownership/recovery；共享 Server 生命周期写操作须保持 fail-closed。 为 connect/start/inspect/reconnect/disconnect/stop/recover 建立 runtime/session id 和 owner 状态；共享 Server stop 需 scoped authorization + ownership proof。 |
| **F05 model lifecycle** | model | 13 | DIRECT_LEGACY_ONLY 3, IN_PROGRESS_SNAPSHOT_ROUTE_PRESENT 2, LEGACY_ANALOGUE_ONLY 8 | adopt/inspect canonical route 在 active snapshot；其余生命周期/包/依赖动作缺失。inspect依赖 inventory仅覆盖Model.FileResourceList。 按 ModelRef/revision 增补 list/clone/close/save/dependencies/package/compare/rebuild；落地覆盖策略和文件读回；单独完成 adopt/inspect 当前 review。 |
| **F06 typed node adapter** | node | 16 | CANONICAL_ROUTE_PRESENT_UNVERIFIED 8, LEGACY_ANALOGUE_ONLY 8 | 只有 inspect/children/find/property schema/get/set/index/entry；无 create/copy/remove/label/active/move/selection；属性范围受 Worker/API allowlist 限制。 沿用 NodePath/TypedValue，只增 typed collection create/copy/remove/label/active/move/selection handlers；保留 schema、权限/revision 门禁和 post-write readback。 |
| **F07 safe public API adapter** | api | 3 | NO_PRODUCTION_ROUTE 3 | 没有 api.* exact route；Worker Java reflection/invoke allowlist 是内部能力，不能满足公开 api.describe/invoke/probe schema。 建立 api.describe/invoke/probe 合同，用 Worker method/signature allowlist、effect declaration 与副本 probe；generic Java execution 不是替代。 |
| **F08 trusted code lifecycle** | code | 5 | CANONICAL_ROUTE_PRESENT_UNVERIFIED 3, NO_PRODUCTION_ROUTE 2 | describe/compile/execute/inspect routes exist；recipe register/run 缺失。Java执行需 TRUSTED_CODE授权；compile不证明目标运行。 新增 content-addressed recipe 登记；run 前绑定 source/runtime/test refs；保留 compile、inspect、trusted execution 分别验收。 |
| **F09 parameter/variable/function/selection** | function, parameter, selection, variable | 34 | CANONICAL_ROUTE_CONTRACT_MISMATCH 1, CANONICAL_ROUTE_PRESENT_UNVERIFIED 28, EXPLICIT_UNSUPPORTED_RESPONSE_ONLY 1, LEGACY_ANALOGUE_ONLY 4 | 29 个 W13 operation ID exact mapped；function.evaluate 映射后总是 API_UNSUPPORTED；function.data_import 不解析 artifact_id，只接收 layout 中的 engine path。另缺 parameter.import/export,function.validate,selection.rebind；centroid/selection-kind 也有明确拒绝边界。 先补 ArtifactStore 授权 artifact_id→engine-visible path adapter，并只在有版本化 API 证据后实现 function sampling；再补 parameter import/export、function validation 和 geometry-revision-aware selection rebind，沿用 G3 typed/readback/write-ticket 纪律。 |
| **F10 geometry and definitions** | definition, geometry | 20 | CANONICAL_ROUTE_CONTRACT_MISMATCH 1, CANONICAL_ROUTE_PRESENT_UNVERIFIED 16, LEGACY_ANALOGUE_ONLY 3 | 17 个 W14 exact；geometry.import 将 artifact_id 直接当 engine filename 而不解析 ArtifactStore ID；另缺 geometry.export/repair 与 definition.mapping_validate；properties/type vocab 只限已检索/验证 API表。 先补受授权 ArtifactStore ID 到 engine path 的解析，再新增窄化 geometry export/repair 与 mapping validation；保留类型 allowlist、preview/readback、partial-change 失败结果。 |
| **F11 material and physics** | material, physics | 21 | CANONICAL_ROUTE_PRESENT_UNVERIFIED 19, LEGACY_ANALOGUE_ONLY 2 | 19 个 W15 exact；缺 material.import/physics.pde_manage；材料/物理特征类型及属性不做任意猜测。 分开实现 material import 和 PDE management；先定义属性词汇并验证 setter readback。 |
| **F12 mesh/study/solver** | mesh, solver, study | 38 | CANONICAL_ROUTE_CONTRACT_MISMATCH 1, CANONICAL_ROUTE_PRESENT_UNVERIFIED 29, LEGACY_ANALOGUE_ONLY 8 | 28 个 W16 exact；缺 mesh.import/export/convergence_study, study.initial_solution_set, solver.solution_inspect/clear/log_read/resource_configure。solver.solution_transfer虽有路由但合同错配。 补 mesh import/export/convergence、initial solution mapping、solver inspect/clear/log/resource；修正 solver.solution_transfer schema/handler 错配。 |
| **F13 durable job control** | job | 9 | CANONICAL_ROUTE_PRESENT_UNVERIFIED 8, IN_PROGRESS_SNAPSHOT_ROUTE_PRESENT 1 | 8/9 cached control actions exact；resume 当前 snapshot 的 direct/registry route 已存在且处于主执行中；cleanup仅压缩metadata；running cancel 不声称 engine stopped。 只对经过证明可重入的任务与checkpoint做 atomic claim/idempotency resume；最终重算 active route 源 hash；保持 queued/running cancel 真值和 metadata-only cleanup。 |
| **F14 dataset/result/probe** | dataset, probe, result | 19 | CANONICAL_ROUTE_PRESENT_UNVERIFIED 17, LEGACY_ANALOGUE_ONLY 2 | dataset/results/probe action routes覆盖窄 API；缺 result.sample_grid/mode_overlap；sample_path仅 W16 线采样。 把 grid sampling/mode overlap做成独立结果合同；保持 sample_path 的线采样边界及 solution/dataset provenance。 |
| **F15 plotting and export** | export, plot | 15 | CANONICAL_ROUTE_PRESENT_UNVERIFIED 13, LEGACY_ANALOGUE_ONLY 2 | PNG server render、三层路径、registered export feature 执行；无 export.report/evidence_bundle；不是 Desktop UI。 添加 report/evidence bundle manifest、hash verification；保留 PNG render 边界并区分 server plot 与 Desktop capture。 |
| **F16 metric/validation/experiment** | experiment, metric, validate | 20 | CANONICAL_ROUTE_CONTRACT_MISMATCH 1, CANONICAL_ROUTE_PRESENT_UNVERIFIED 8, LEGACY_ANALOGUE_ONLY 11 | 8个 validate actions exact；metric全族与 experiment CRUD/stage/state map缺；experiment.run handler/schema不兼容。 实现 metric definition/store/compare、experiment CRUD/stage/state-map 持久化；拆分或重写 experiment.run 使其服从 experiment_id 合同。 |
| **F17 checkpoints and transactions** | checkpoint, transaction | 11 | CANONICAL_ROUTE_PRESENT_UNVERIFIED 9, EXPLICIT_UNSUPPORTED_RESPONSE_ONLY 1, LEGACY_ANALOGUE_ONLY 1 | checkpoint create/list/inspect/restore 和五个 transaction exact；diff 明确 unsupported，branch缺；transaction仅执行白名单 actions/invariants。 对 hash-bound saved generation 实现 checkpoint diff/branch；事务维持已声明动作/invariant 白名单和可恢复状态。 |
| **F18 artifact lifecycle** | artifact | 7 | CANONICAL_ROUTE_PRESENT_UNVERIFIED 1, LEGACY_ANALOGUE_ONLY 6 | 只注册 artifact.read，分块/hash/授权限制明确；其余 lifecycle缺 route。 围绕路径授权、artifact identity/hash/size limits 和 publish authorization 建 register/list/inspect/preview/publish/verify。 |
| **F19 Desktop adapter** | desktop | 8 | LEGACY_ANALOGUE_ONLY 8 | 无 Desktop adapter/tool/WindowHandle binding/dirty standalone migration/target-window cancel/native acceptance。 依 W25_ROUTE_AUDIT.md 补平台 adapter、verified WindowHandle↔ModelRef 绑定、dirty standalone copy migration 与目标窗口停止；原生 GUI gate 独立验收。 |
| **F20 offline documentation** | docs | 5 | CANONICAL_ROUTE_PRESENT_UNVERIFIED 5 | local offline index/search/get/examples/error_search routes；不包含外部发现或 runtime compatibility probe。 保持本地文档索引/search 版本范围明确；无需为完整合同补充非本地动作。 |

## 未闭合动作逐项清单

每个家族仅列 canonical 缺失、legacy-only、明确 unsupported、契约错配和主执行者在改的动作。其余 exact registry handlers 也都只是静态存在。完整 272 项统一见 JSON。

### F01 runtime install/licensing — runtime

| action | 状态 | direct / legacy 路由 | handler / 缺口和精确限制 |
|---|---|---|---|
| `runtime.discover` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: server_info, check_server_port, server_start, runtime_poc_v64 | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 这些是本机/端口/连接旧工具；不构成 runtime_id 安装库存、兼容报告、许可证 checkout 或 GUI render probe。 |
| `runtime.inspect` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: server_info, check_server_port, server_start, runtime_poc_v64 | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 这些是本机/端口/连接旧工具；不构成 runtime_id 安装库存、兼容报告、许可证 checkout 或 GUI render probe。 |
| `runtime.doctor` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: server_info, check_server_port, server_start, runtime_poc_v64 | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 这些是本机/端口/连接旧工具；不构成 runtime_id 安装库存、兼容报告、许可证 checkout 或 GUI render probe。 |
| `runtime.license_checkout` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: server_info, check_server_port, server_start, runtime_poc_v64 | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 这些是本机/端口/连接旧工具；不构成 runtime_id 安装库存、兼容报告、许可证 checkout 或 GUI render probe。 |
| `runtime.render_probe` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: server_info, check_server_port, server_start, runtime_poc_v64 | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 这些是本机/端口/连接旧工具；不构成 runtime_id 安装库存、兼容报告、许可证 checkout 或 GUI render probe。 |
| `runtime.compatibility_report` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: server_info, check_server_port, server_start, runtime_poc_v64 | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 这些是本机/端口/连接旧工具；不构成 runtime_id 安装库存、兼容报告、许可证 checkout 或 GUI render probe。 |

### F02 registry contract — registry

此家族所有冻结动作均能找到 exact-operation handler；仍未运行目标验收。

### F03 project and policy — project

| action | 状态 | direct / legacy 路由 | handler / 缺口和精确限制 |
|---|---|---|---|
| `project.create` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: workflow_info, mcp_tool_audit | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. workflow 状态文件与工具审计不是项目实体、授权策略或项目状态导出。 |
| `project.inspect` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: workflow_info, mcp_tool_audit | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. workflow 状态文件与工具审计不是项目实体、授权策略或项目状态导出。 |
| `project.contract_set` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: workflow_info, mcp_tool_audit | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. workflow 状态文件与工具审计不是项目实体、授权策略或项目状态导出。 |
| `project.policy_set` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: workflow_info, mcp_tool_audit | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. workflow 状态文件与工具审计不是项目实体、授权策略或项目状态导出。 |
| `project.permissions` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: workflow_info, mcp_tool_audit | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. workflow 状态文件与工具审计不是项目实体、授权策略或项目状态导出。 |
| `project.state_export` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: workflow_info, mcp_tool_audit | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. workflow 状态文件与工具审计不是项目实体、授权策略或项目状态导出。 |

### F04 session ownership — session

| action | 状态 | direct / legacy 路由 | handler / 缺口和精确限制 |
|---|---|---|---|
| `session.list` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: session_health, server_info, check_server_port, server_connect, server_start, server_disconnect | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧工具只管理当前客户端/固定 endpoint；受管共享后端拒绝 Server 生命周期写操作，缺少 runtime/session 所有权、重连与恢复合同。 |
| `session.connect` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: session_health, server_info, check_server_port, server_connect, server_start, server_disconnect | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧工具只管理当前客户端/固定 endpoint；受管共享后端拒绝 Server 生命周期写操作，缺少 runtime/session 所有权、重连与恢复合同。 |
| `session.start` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: session_health, server_info, check_server_port, server_connect, server_start, server_disconnect | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧工具只管理当前客户端/固定 endpoint；受管共享后端拒绝 Server 生命周期写操作，缺少 runtime/session 所有权、重连与恢复合同。 |
| `session.inspect` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: session_health, server_info, check_server_port, server_connect, server_start, server_disconnect | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧工具只管理当前客户端/固定 endpoint；受管共享后端拒绝 Server 生命周期写操作，缺少 runtime/session 所有权、重连与恢复合同。 |
| `session.reconnect` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: session_health, server_info, check_server_port, server_connect, server_start, server_disconnect | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧工具只管理当前客户端/固定 endpoint；受管共享后端拒绝 Server 生命周期写操作，缺少 runtime/session 所有权、重连与恢复合同。 |
| `session.disconnect` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: session_health, server_info, check_server_port, server_connect, server_start, server_disconnect | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧工具只管理当前客户端/固定 endpoint；受管共享后端拒绝 Server 生命周期写操作，缺少 runtime/session 所有权、重连与恢复合同。 |
| `session.stop` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: session_health, server_info, check_server_port, server_connect, server_start, server_disconnect | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧工具只管理当前客户端/固定 endpoint；受管共享后端拒绝 Server 生命周期写操作，缺少 runtime/session 所有权、重连与恢复合同。 |
| `session.health` | `DIRECT_LEGACY_ONLY` | direct: session_health; legacy: server_info, check_server_port, server_connect, server_start, server_disconnect | 无 canonical handler; MCP tool `session_health` signature `session_health()`; frozen action-specific fields absent from the direct schema: none. Legacy workflow semantics do not prove `session.health` contract. 旧工具只管理当前客户端/固定 endpoint；受管共享后端拒绝 Server 生命周期写操作，缺少 runtime/session 所有权、重连与恢复合同。 |
| `session.recover` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: session_health, server_info, check_server_port, server_connect, server_start, server_disconnect | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧工具只管理当前客户端/固定 endpoint；受管共享后端拒绝 Server 生命周期写操作，缺少 runtime/session 所有权、重连与恢复合同。 |

### F05 model lifecycle — model

| action | 状态 | direct / legacy 路由 | handler / 缺口和精确限制 |
|---|---|---|---|
| `model.list` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: model_create, model_load, model_adopt, model_inspect, model_tree, save_model, prune_loaded_models, load_visible_main_model | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧工具按当前模型/路径工作；save_model 可保存当前 server model，但无冻结动作要求的完整 ModelRef、覆盖授权、依赖包或跨版本重建合同。 |
| `model.create` | `DIRECT_LEGACY_ONLY` | direct: model_create; legacy: model_load, model_adopt, model_inspect, model_tree, save_model, prune_loaded_models, load_visible_main_model | 无 canonical handler; MCP tool `model_create` signature `model_create(name)`; frozen action-specific fields absent from the direct schema: ['label', 'dimension']. Legacy workflow semantics do not prove `model.create` contract. 旧工具按当前模型/路径工作；save_model 可保存当前 server model，但无冻结动作要求的完整 ModelRef、覆盖授权、依赖包或跨版本重建合同。 |
| `model.load` | `DIRECT_LEGACY_ONLY` | direct: model_load; legacy: model_create, model_adopt, model_inspect, model_tree, save_model, prune_loaded_models, load_visible_main_model | 无 canonical handler; MCP tool `model_load` signature `model_load(path?)`; frozen action-specific fields absent from the direct schema: ['artifact_id', 'path_policy']. Legacy workflow semantics do not prove `model.load` contract. 旧工具按当前模型/路径工作；save_model 可保存当前 server model，但无冻结动作要求的完整 ModelRef、覆盖授权、依赖包或跨版本重建合同。 |
| `model.adopt` | `IN_PROGRESS_SNAPSHOT_ROUTE_PRESENT` | direct: model_adopt; legacy: model_create, model_load, model_inspect, model_tree, save_model, prune_loaded_models, load_visible_main_model | comsol_mcp/_g2_registry.py:672, comsol_mcp/_managed_backend.py:?, comsol_mcp/_control_daemon.py:?; Canonical G2路由已存在；主执行者正在改 action schema/dispatch/readback。 |
| `model.inspect` | `IN_PROGRESS_SNAPSHOT_ROUTE_PRESENT` | direct: model_inspect; legacy: model_create, model_load, model_adopt, model_tree, save_model, prune_loaded_models, load_visible_main_model | comsol_mcp/_g2_registry.py:672, comsol_mcp/_managed_backend.py:?, comsol_mcp/_control_daemon.py:?; Canonical G2路由已存在；主执行者正在改 action schema/dispatch/readback。 |
| `model.tree` | `DIRECT_LEGACY_ONLY` | direct: model_tree; legacy: model_create, model_load, model_adopt, model_inspect, save_model, prune_loaded_models, load_visible_main_model | 无 canonical handler; MCP tool `model_tree` signature `model_tree()`; frozen action-specific fields absent from the direct schema: ['path', 'depth', 'cursor', 'limit']. Legacy workflow semantics do not prove `model.tree` contract. 旧工具按当前模型/路径工作；save_model 可保存当前 server model，但无冻结动作要求的完整 ModelRef、覆盖授权、依赖包或跨版本重建合同。 |
| `model.save` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: model_create, model_load, model_adopt, model_inspect, model_tree, save_model, prune_loaded_models, load_visible_main_model | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧工具按当前模型/路径工作；save_model 可保存当前 server model，但无冻结动作要求的完整 ModelRef、覆盖授权、依赖包或跨版本重建合同。 |
| `model.clone` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: model_create, model_load, model_adopt, model_inspect, model_tree, save_model, prune_loaded_models, load_visible_main_model | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧工具按当前模型/路径工作；save_model 可保存当前 server model，但无冻结动作要求的完整 ModelRef、覆盖授权、依赖包或跨版本重建合同。 |
| `model.close` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: model_create, model_load, model_adopt, model_inspect, model_tree, save_model, prune_loaded_models, load_visible_main_model | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧工具按当前模型/路径工作；save_model 可保存当前 server model，但无冻结动作要求的完整 ModelRef、覆盖授权、依赖包或跨版本重建合同。 |
| `model.dependencies` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: model_create, model_load, model_adopt, model_inspect, model_tree, save_model, prune_loaded_models, load_visible_main_model | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧工具按当前模型/路径工作；save_model 可保存当前 server model，但无冻结动作要求的完整 ModelRef、覆盖授权、依赖包或跨版本重建合同。 |
| `model.package` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: model_create, model_load, model_adopt, model_inspect, model_tree, save_model, prune_loaded_models, load_visible_main_model | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧工具按当前模型/路径工作；save_model 可保存当前 server model，但无冻结动作要求的完整 ModelRef、覆盖授权、依赖包或跨版本重建合同。 |
| `model.rebuild_target` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: model_create, model_load, model_adopt, model_inspect, model_tree, save_model, prune_loaded_models, load_visible_main_model | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧工具按当前模型/路径工作；save_model 可保存当前 server model，但无冻结动作要求的完整 ModelRef、覆盖授权、依赖包或跨版本重建合同。 |
| `model.compare` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: model_create, model_load, model_adopt, model_inspect, model_tree, save_model, prune_loaded_models, load_visible_main_model | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧工具按当前模型/路径工作；save_model 可保存当前 server model，但无冻结动作要求的完整 ModelRef、覆盖授权、依赖包或跨版本重建合同。 |

### F06 typed node adapter — node

| action | 状态 | direct / legacy 路由 | handler / 缺口和精确限制 |
|---|---|---|---|
| `node.create` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: model_tree, create_feature, update_feature, delete_feature, ensure_component, ensure_geometry | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧几何/物理工具只覆盖特定对象/特征；不是通用 NodePath/TypedValue/selection adapter。 |
| `node.copy` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: model_tree, create_feature, update_feature, delete_feature, ensure_component, ensure_geometry | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧几何/物理工具只覆盖特定对象/特征；不是通用 NodePath/TypedValue/selection adapter。 |
| `node.remove` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: model_tree, create_feature, update_feature, delete_feature, ensure_component, ensure_geometry | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧几何/物理工具只覆盖特定对象/特征；不是通用 NodePath/TypedValue/selection adapter。 |
| `node.label_set` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: model_tree, create_feature, update_feature, delete_feature, ensure_component, ensure_geometry | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧几何/物理工具只覆盖特定对象/特征；不是通用 NodePath/TypedValue/selection adapter。 |
| `node.active_set` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: model_tree, create_feature, update_feature, delete_feature, ensure_component, ensure_geometry | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧几何/物理工具只覆盖特定对象/特征；不是通用 NodePath/TypedValue/selection adapter。 |
| `node.move` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: model_tree, create_feature, update_feature, delete_feature, ensure_component, ensure_geometry | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧几何/物理工具只覆盖特定对象/特征；不是通用 NodePath/TypedValue/selection adapter。 |
| `node.selection_get` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: model_tree, create_feature, update_feature, delete_feature, ensure_component, ensure_geometry | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧几何/物理工具只覆盖特定对象/特征；不是通用 NodePath/TypedValue/selection adapter。 |
| `node.selection_set` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: model_tree, create_feature, update_feature, delete_feature, ensure_component, ensure_geometry | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧几何/物理工具只覆盖特定对象/特征；不是通用 NodePath/TypedValue/selection adapter。 |

### F07 safe public API adapter — api

| action | 状态 | direct / legacy 路由 | handler / 缺口和精确限制 |
|---|---|---|---|
| `api.describe` | `NO_PRODUCTION_ROUTE` | direct: 无精确 direct 工具; legacy: 无 | 无 canonical handler; No direct MCP action tool or canonical registry handler found in the current source registration/allowlists. |
| `api.invoke` | `NO_PRODUCTION_ROUTE` | direct: 无精确 direct 工具; legacy: 无 | 无 canonical handler; No direct MCP action tool or canonical registry handler found in the current source registration/allowlists. |
| `api.probe` | `NO_PRODUCTION_ROUTE` | direct: 无精确 direct 工具; legacy: 无 | 无 canonical handler; No direct MCP action tool or canonical registry handler found in the current source registration/allowlists. |

### F08 trusted code lifecycle — code

| action | 状态 | direct / legacy 路由 | handler / 缺口和精确限制 |
|---|---|---|---|
| `code.recipe_register` | `NO_PRODUCTION_ROUTE` | direct: 无精确 direct 工具; legacy: 无 | 无 canonical handler; No direct MCP action tool or canonical registry handler found in the current source registration/allowlists. |
| `code.recipe_run` | `NO_PRODUCTION_ROUTE` | direct: 无精确 direct 工具; legacy: 无 | 无 canonical handler; No direct MCP action tool or canonical registry handler found in the current source registration/allowlists. |

### F09 parameter/variable/function/selection — function, parameter, selection, variable

| action | 状态 | direct / legacy 路由 | handler / 缺口和精确限制 |
|---|---|---|---|
| `parameter.import` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: get_parameters, set_parameters, evaluate_expressions, manage_variables | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧参数/表达式工具围绕当前模型和特定结果查询；没有冻结动作的分组/导入导出/单位完整读回语义。 |
| `parameter.export` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: get_parameters, set_parameters, evaluate_expressions, manage_variables | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧参数/表达式工具围绕当前模型和特定结果查询；没有冻结动作的分组/导入导出/单位完整读回语义。 |
| `function.validate` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: evaluate_expressions, manage_variables | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 表达式求值不是 COMSOL 函数对象生命周期；未覆盖类型表、导入绑定、任意点求值/验证。 |
| `selection.rebind` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: set_physics_selection, create_feature, update_feature, delete_feature | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. legacy physics selection 或通用 feature 更新不是稳定几何修订上的 selection/rebind、entity query 与 measurement。 |

### F10 geometry and definitions — definition, geometry

| action | 状态 | direct / legacy 路由 | handler / 缺口和精确限制 |
|---|---|---|---|
| `geometry.export` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: ensure_component, ensure_geometry, ensure_mesh, create_feature, update_feature, delete_feature, run_feature | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧 feature helpers 面向常用工作流；不覆盖冻结 NodePath、依赖图、repair/export、输入/读回完整合同。 |
| `geometry.repair` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: ensure_component, ensure_geometry, ensure_mesh, create_feature, update_feature, delete_feature, run_feature | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧 feature helpers 面向常用工作流；不覆盖冻结 NodePath、依赖图、repair/export、输入/读回完整合同。 |
| `definition.mapping_validate` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: create_feature, update_feature, delete_feature, create_physics_feature | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 没有通用坐标系/Pair/Coupling 定义注册路由；旧物理 feature helpers 不能替代 mapping_validate。 |

### F11 material and physics — material, physics

| action | 状态 | direct / legacy 路由 | handler / 缺口和精确限制 |
|---|---|---|---|
| `material.import` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: create_physics, create_physics_feature, update_physics_feature | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 没有材料库导入/版本/来源/属性批量管理与验证合同。 |
| `physics.pde_manage` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: create_physics, list_physics, list_physics_features, create_physics_feature, update_physics_feature, remove_physics, set_physics_selection, manage_variables | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧工具覆盖已支持的物理创建/管理路径；非通用 PDE、多物理耦合配置接口。 |

### F12 mesh/study/solver — mesh, solver, study

| action | 状态 | direct / legacy 路由 | handler / 缺口和精确限制 |
|---|---|---|---|
| `mesh.import` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: ensure_mesh, run_feature, create_feature, configure_solver | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧工具有创建和研究流程，不是网格导入导出、收敛研究、质量/统计完整 action schema。 |
| `mesh.export` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: ensure_mesh, run_feature, create_feature, configure_solver | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧工具有创建和研究流程，不是网格导入导出、收敛研究、质量/统计完整 action schema。 |
| `mesh.convergence_study` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: ensure_mesh, run_feature, create_feature, configure_solver | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧工具有创建和研究流程，不是网格导入导出、收敛研究、质量/统计完整 action schema。 |
| `study.initial_solution_set` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: run_study, run_study_async, run_study_status, configure_solver | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧 study 调用针对当前模型和 study tag；不覆盖初值映射、资源预算/版本身份所有权完整合同。 |
| `solver.solution_inspect` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: configure_solver, create_solver_config, list_solver_config, list_solver_features, run_study, run_study_async, run_study_status | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧 solver 配置/运行 helper 不提供解生命周期、resource config、solver log/readback 全部动作。 |
| `solver.solution_clear` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: configure_solver, create_solver_config, list_solver_config, list_solver_features, run_study, run_study_async, run_study_status | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧 solver 配置/运行 helper 不提供解生命周期、resource config、solver log/readback 全部动作。 |
| `solver.solution_transfer` | `CANONICAL_ROUTE_CONTRACT_MISMATCH` | direct: stage.state_transfer, stage_state_transfer; legacy: configure_solver, create_solver_config, list_solver_config, list_solver_features, run_study, run_study_async, run_study_status | comsol_mcp/_g3_w21.py:616, comsol_mcp/_g3_ops.py:216, comsol_mcp/_managed_backend.py:?; Canonical ID 映射到 W21 stage transfer，但 handler 要已登记 stage checkpoint_id、target_stage_id、variable_mapping、target_sample 与 tolerance；冻结合同是 source SolutionSpec/target NodePath/mapping。仅支持同网格、相同因变量名、带读回的特定瞬态初值传递，当前 schema/handler 错配。 |
| `solver.log_read` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: configure_solver, create_solver_config, list_solver_config, list_solver_features, run_study, run_study_async, run_study_status | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧 solver 配置/运行 helper 不提供解生命周期、resource config、solver log/readback 全部动作。 |
| `solver.resource_configure` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: configure_solver, create_solver_config, list_solver_config, list_solver_features, run_study, run_study_async, run_study_status | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧 solver 配置/运行 helper 不提供解生命周期、resource config、solver log/readback 全部动作。 |

### F13 durable job control — job

| action | 状态 | direct / legacy 路由 | handler / 缺口和精确限制 |
|---|---|---|---|
| `job.resume` | `IN_PROGRESS_SNAPSHOT_ROUTE_PRESENT` | direct: 无精确 direct 工具; legacy: job_list, job_status, job_log, job_result, job_wait, job_cancel, job_reconcile | comsol_mcp/_g2_registry.py:672, comsol_mcp/_control_daemon.py:?, comsol_mcp/_operation_store.py:?; Canonical registry and ControlDaemon compute route are now present. Supported scope is opt-in direct/registry study.run restart from a backend-created pre-run checkpoint, after failure/quiescence, hash/ModelRef/revision/permission checks and atomic idempotent continuation claim; this restarts the study from a saved model, not solver-iteration continuation. Route is still in active executor scope and unverified. |

### F14 dataset/result/probe — dataset, probe, result

| action | 状态 | direct / legacy 路由 | handler / 缺口和精确限制 |
|---|---|---|---|
| `result.sample_grid` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: evaluate_expressions, get_core_metrics, plot_render, save_model | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. legacy 表达式计算可能创建短期数值节点；不可视为完整结果采样/模式重叠/文件导出。 |
| `result.mode_overlap` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: evaluate_expressions, get_core_metrics, plot_render, save_model | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. legacy 表达式计算可能创建短期数值节点；不可视为完整结果采样/模式重叠/文件导出。 |

### F15 plotting and export — export, plot

| action | 状态 | direct / legacy 路由 | handler / 缺口和精确限制 |
|---|---|---|---|
| `export.report` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: save_model, save_main_model_snapshot, plot_render | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 模型保存/图像导出不是结果报告或 evidence bundle 清单/校验/发布。 |
| `export.evidence_bundle` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: save_model, save_main_model_snapshot, plot_render | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 模型保存/图像导出不是结果报告或 evidence bundle 清单/校验/发布。 |

### F16 metric/validation/experiment — experiment, metric, validate

| action | 状态 | direct / legacy 路由 | handler / 缺口和精确限制 |
|---|---|---|---|
| `metric.define` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: get_core_metrics, evaluate_expressions, run_study | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 固定 core metrics / expressions 无持久定义、删除、跨 case 对比/容差合同。 |
| `metric.list` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: get_core_metrics, evaluate_expressions, run_study | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 固定 core metrics / expressions 无持久定义、删除、跨 case 对比/容差合同。 |
| `metric.evaluate` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: get_core_metrics, evaluate_expressions, run_study | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 固定 core metrics / expressions 无持久定义、删除、跨 case 对比/容差合同。 |
| `metric.remove` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: get_core_metrics, evaluate_expressions, run_study | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 固定 core metrics / expressions 无持久定义、删除、跨 case 对比/容差合同。 |
| `metric.compare` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: get_core_metrics, evaluate_expressions, run_study | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 固定 core metrics / expressions 无持久定义、删除、跨 case 对比/容差合同。 |
| `experiment.design` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: run_study_async, run_study, parameter_case_manage, optimization.bounded_run, optimization_bounded_run, stage.state_transfer, stage_state_transfer | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. W21 optimizer alias仅执行有界参数优化；不支持基于 experiment_id 的 DOE/native sweep 案例登记/查询，也无阶段 CRUD/state map 全族。 |
| `experiment.run` | `CANONICAL_ROUTE_CONTRACT_MISMATCH` | direct: optimization.bounded_run, optimization_bounded_run; legacy: run_study_async, run_study, parameter_case_manage, stage.state_transfer, stage_state_transfer | comsol_mcp/_g3_w21.py:618, comsol_mcp/_g3_ops.py:216, comsol_mcp/_managed_backend.py:?; Canonical ID 映射到 bounded optimization（要求 objective_name/parameter_bounds/constraints/study/definition）；冻结输入是 experiment_id/resources/timeout。严格 catalog schema 与当前 handler 不一致，不能执行冻结的 experiment.run 合同。 |
| `experiment.inspect` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: run_study_async, run_study, parameter_case_manage, optimization.bounded_run, optimization_bounded_run, stage.state_transfer, stage_state_transfer | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. W21 optimizer alias仅执行有界参数优化；不支持基于 experiment_id 的 DOE/native sweep 案例登记/查询，也无阶段 CRUD/state map 全族。 |
| `experiment.case_result` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: run_study_async, run_study, parameter_case_manage, optimization.bounded_run, optimization_bounded_run, stage.state_transfer, stage_state_transfer | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. W21 optimizer alias仅执行有界参数优化；不支持基于 experiment_id 的 DOE/native sweep 案例登记/查询，也无阶段 CRUD/state map 全族。 |
| `experiment.stage_define` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: run_study_async, run_study, parameter_case_manage, optimization.bounded_run, optimization_bounded_run, stage.state_transfer, stage_state_transfer | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. W21 optimizer alias仅执行有界参数优化；不支持基于 experiment_id 的 DOE/native sweep 案例登记/查询，也无阶段 CRUD/state map 全族。 |
| `experiment.stage_run` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: run_study_async, run_study, parameter_case_manage, optimization.bounded_run, optimization_bounded_run, stage.state_transfer, stage_state_transfer | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. W21 optimizer alias仅执行有界参数优化；不支持基于 experiment_id 的 DOE/native sweep 案例登记/查询，也无阶段 CRUD/state map 全族。 |
| `experiment.state_map` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: run_study_async, run_study, parameter_case_manage, optimization.bounded_run, optimization_bounded_run, stage.state_transfer, stage_state_transfer | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. W21 optimizer alias仅执行有界参数优化；不支持基于 experiment_id 的 DOE/native sweep 案例登记/查询，也无阶段 CRUD/state map 全族。 |

### F17 checkpoints and transactions — checkpoint, transaction

| action | 状态 | direct / legacy 路由 | handler / 缺口和精确限制 |
|---|---|---|---|
| `checkpoint.diff` | `EXPLICIT_UNSUPPORTED_RESPONSE_ONLY` | direct: 无精确 direct 工具; legacy: save_model, save_main_model_snapshot | 无 canonical handler; 控制读路径识别此名字，但返回 NOT_RUN/UNSUPPORTED_OPERATION（需版本化 COMSOL 比较 adapter）；注册表不将其标为可执行。 |
| `checkpoint.branch` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: save_model, save_main_model_snapshot | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧保存/缓存元数据不能替代 diff/branch；restore以新 model generation 恢复，diff adapter 明确 unsupported。 |

### F18 artifact lifecycle — artifact

| action | 状态 | direct / legacy 路由 | handler / 缺口和精确限制 |
|---|---|---|---|
| `artifact.register` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: save_model, save_main_model_snapshot, mcp_tool_audit | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 现有 artifact.read 仅读取已登记/授权文件的分块并校验 digest；不覆盖登记、列举、预览、发布、verify 全生命周期。 |
| `artifact.list` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: save_model, save_main_model_snapshot, mcp_tool_audit | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 现有 artifact.read 仅读取已登记/授权文件的分块并校验 digest；不覆盖登记、列举、预览、发布、verify 全生命周期。 |
| `artifact.inspect` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: save_model, save_main_model_snapshot, mcp_tool_audit | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 现有 artifact.read 仅读取已登记/授权文件的分块并校验 digest；不覆盖登记、列举、预览、发布、verify 全生命周期。 |
| `artifact.preview` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: save_model, save_main_model_snapshot, mcp_tool_audit | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 现有 artifact.read 仅读取已登记/授权文件的分块并校验 digest；不覆盖登记、列举、预览、发布、verify 全生命周期。 |
| `artifact.publish` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: save_model, save_main_model_snapshot, mcp_tool_audit | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 现有 artifact.read 仅读取已登记/授权文件的分块并校验 digest；不覆盖登记、列举、预览、发布、verify 全生命周期。 |
| `artifact.verify` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: save_model, save_main_model_snapshot, mcp_tool_audit | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 现有 artifact.read 仅读取已登记/授权文件的分块并校验 digest；不覆盖登记、列举、预览、发布、verify 全生命周期。 |

### F19 Desktop adapter — desktop

| action | 状态 | direct / legacy 路由 | handler / 缺口和精确限制 |
|---|---|---|---|
| `desktop.status` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: load_visible_main_model, verify_visible_main_session, run_visible_main_iteration, unlock_visible_main | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧 workflow 要求用户手工让 Desktop 连接同一 Server；没有 WindowHandle/ModelRef 双向绑定、dirty standalone 迁移或目标窗口取消。 |
| `desktop.bind` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: load_visible_main_model, verify_visible_main_session, run_visible_main_iteration, unlock_visible_main | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧 workflow 要求用户手工让 Desktop 连接同一 Server；没有 WindowHandle/ModelRef 双向绑定、dirty standalone 迁移或目标窗口取消。 |
| `desktop.show_model` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: load_visible_main_model, verify_visible_main_session, run_visible_main_iteration, unlock_visible_main | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧 workflow 要求用户手工让 Desktop 连接同一 Server；没有 WindowHandle/ModelRef 双向绑定、dirty standalone 迁移或目标窗口取消。 |
| `desktop.select_node` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: load_visible_main_model, verify_visible_main_session, run_visible_main_iteration, unlock_visible_main | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧 workflow 要求用户手工让 Desktop 连接同一 Server；没有 WindowHandle/ModelRef 双向绑定、dirty standalone 迁移或目标窗口取消。 |
| `desktop.capture` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: load_visible_main_model, verify_visible_main_session, run_visible_main_iteration, unlock_visible_main | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧 workflow 要求用户手工让 Desktop 连接同一 Server；没有 WindowHandle/ModelRef 双向绑定、dirty standalone 迁移或目标窗口取消。 |
| `desktop.action` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: load_visible_main_model, verify_visible_main_session, run_visible_main_iteration, unlock_visible_main | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧 workflow 要求用户手工让 Desktop 连接同一 Server；没有 WindowHandle/ModelRef 双向绑定、dirty standalone 迁移或目标窗口取消。 |
| `desktop.shell_execute` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: load_visible_main_model, verify_visible_main_session, run_visible_main_iteration, unlock_visible_main | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧 workflow 要求用户手工让 Desktop 连接同一 Server；没有 WindowHandle/ModelRef 双向绑定、dirty standalone 迁移或目标窗口取消。 |
| `desktop.migrate_standalone` | `LEGACY_ANALOGUE_ONLY` | direct: 无精确 direct 工具; legacy: load_visible_main_model, verify_visible_main_session, run_visible_main_iteration, unlock_visible_main | 无 canonical handler; No exact canonical handler; similar legacy helper(s) do not satisfy frozen schema/effect/result. 旧 workflow 要求用户手工让 Desktop 连接同一 Server；没有 WindowHandle/ModelRef 双向绑定、dirty standalone 迁移或目标窗口取消。 |

### F20 offline documentation — docs

此家族所有冻结动作均能找到 exact-operation handler；仍未运行目标验收。

## 权限、读回和失败语义

Canonical registry uses frozen catalog validation via `_g2_registry.validate_call`. G3 handlers derive effects from the catalog and enter managed model revision/write-ticket/isolation gates; the generic typed property setters perform readback. Job status/log/cancel cached reads bypass the serial engine queue, while `job.resume` deliberately queues a new study execution. These are software route/guard observations, not per-action or per-target acceptance claims.

Do not count legacy helpers as frozen action implementations: old tool signature, generic feature helpers, `evaluate_expressions`, generic Java methods and worker file locks have different argument, permission or result semantics. JSON records the public tool fields and fit for each action. Fixture tests remain software evidence and do not substitute the original six-target native checks.

W25 Desktop route and GUI-only acceptance boundary are detailed in [W25_ROUTE_AUDIT.md](W25_ROUTE_AUDIT.md). Product adapter routes are missing; building CLI/API gates can proceed independently, but actual WindowHandle/ModelRef, dirty standalone migration, UI permissions and target-window stop must be validated in an allowed native GUI session.

## Snapshot binding

Frozen catalog SHA-256: `f912d417a0fccb6b099377306018d824c8d437e4774525e92f77814a49d814de`. Source manifest SHA-256: `38822a29caca41d6a7db3c958993151214ae5b49f5fc40cfbd23b3ca9ed59086`; per-file hashes for the 148 Python/Java sources are in JSON. Two-pass identical hash set: `True`. Files may continue changing under the primary executor, so the three in-progress action rows include per-file hashes and explicit re-audit conditions.

No `ACTION_COVERAGE` statuses were changed or treated as route evidence.
