# G3_OPERATIONS.md — W13–W16 操作覆盖表

依据：Goal `NEXT_GOAL_MAC_G3.md` §4（共同实现契约）与 §10.2（文件组织）。
数据来源（可在本机复算，均非记忆值）：

- 发布面：`comsol_mcp/_g3_ops.DISPATCH / IMPLEMENTED_OPERATIONS / EFFECTS / EFFECT_SOURCES /
  REQUIRES_ISOLATION / OPERATION_ORIGINS`（96 项，模块来源 29/17/19/28/2/1）。
- 路由与效果来源：`comsol_mcp/_g2_registry.BY_ID`（动作目录条目）。目录文件为
  `docs/comsol_mcp_design_v1/02_ACTION_CATALOG.json`，包内副本为
  `comsol_mcp/data/g2/02_ACTION_CATALOG.json`；两者必须字节一致，否则 registry 初始化即报错。
- 驱动用例：`tools/phase4_run_mcp.py` 的 `PLAN`/`CASES` 与实际 `client.action(...)` 调用点。
- 计数：`len(IMPLEMENTED_OPERATIONS) == 96`；`len(REQUIRES_ISOLATION) == 72`。

**状态语义**：本表记录“有可调用实现、有模块测试、驱动用例是否调用”，**不**表示实机验收通过。
本轮 W13–W16 live 验收在 revision 记账上阻塞（见 `G3_REVIEW_FIXES.md` §实机状态汇总），
故所有 live 行保持 BLOCKED/FAIL/NOT_RUN，没有任何一行可读作“已验收”。

“驱动用例”列的取值含义：该操作被 `tools/phase4_run_mcp.py` 的对应用例（或其 `PLAN` 静态门禁）
真实调用；“—”表示驱动未覆盖，证据仅来自模块测试。

## 1. W13 — 参数、变量、函数与选区（29 op）

| operation_id | 模块 | 效果 | 需隔离 | API 来源 | 证据 | 驱动用例 | 未验证/拒绝路径 |
|---|---|---|---|---|---|---|---|
| `parameter.list` | _g3_w13 | READ | 否 | Programming Reference `model.param()`；javap `ModelParam`/`ModelParamGroupList` | tests/test_g3_w13.py（145 测） | — | 模块内「Known, reported gaps」常量与写前拒绝（`function.evaluate`、centroid、`objects` 绑定、几何作用域选区创建、未取回属性表的函数类型） |
| `parameter.get` | _g3_w13 | READ | 否 | 同上 | tests/test_g3_w13.py（145 测） | — | 同上 |
| `parameter.set` | _g3_w13 | WRITE | 是 | 同上 | tests/test_g3_w13.py（145 测） | — | 同上 |
| `parameter.remove` | _g3_w13 | WRITE | 是 | 同上 | tests/test_g3_w13.py（145 测） | — | 同上 |
| `parameter.group_manage` | _g3_w13 | WRITE | 是 | 同上 | tests/test_g3_w13.py（145 测） | — | 同上；参数组重命名无离线出处，写前拒绝 |
| `variable.list` | _g3_w13 | READ | 否 | Programming Reference `model.variable()`；javap `ExprList`/`ComponentExprList`/`ModelNode.variable()` | tests/test_g3_w13.py（145 测） | — | 同上 |
| `variable.get` | _g3_w13 | READ | 否 | 同上 | tests/test_g3_w13.py（145 测） | W13_T006_variables | 同上 |
| `variable.set` | _g3_w13 | WRITE | 是 | 同上 | tests/test_g3_w13.py（145 测） | W13_T006_variables | 同上 |
| `variable.remove` | _g3_w13 | WRITE | 是 | 同上 | tests/test_g3_w13.py（145 测） | — | 同上 |
| `variable.group_create` | _g3_w13 | WRITE | 是 | 同上 | tests/test_g3_w13.py（145 测） | W13_T006_variables | 同上 |
| `variable.selection_set` | _g3_w13 | WRITE | 是 | 同上 | tests/test_g3_w13.py（145 测） | — | 同上；局部选区要求先设 component 节点 |
| `function.list` | _g3_w13 | READ | 否 | Programming Reference `model.func()`（类型表 + 各类型属性表）；javap `FunctionFeatureList`/`FunctionFeature` | tests/test_g3_w13.py（145 测） | — | 同上 |
| `function.create` | _g3_w13 | WRITE | 是 | 同上 | tests/test_g3_w13.py（145 测） | W13_T016_2D_data | 同上；未取回属性表的类型（CGNS/DNN/PartialFractionFit/FunctionSwitch）拒绝写属性 |
| `function.inspect` | _g3_w13 | READ | 否 | 同上 | tests/test_g3_w13.py（145 测） | W13_T016_2D_data | 同上 |
| `function.update` | _g3_w13 | WRITE | 是 | 同上 | tests/test_g3_w13.py（145 测） | — | 同上 |
| `function.remove` | _g3_w13 | WRITE | 是 | 同上 | tests/test_g3_w13.py（145 测） | — | 同上 |
| `function.data_import` | _g3_w13 | WRITE | 是 | 同上（`importData()` javap 核实） | tests/test_g3_w13.py（145 测） | W13_T016_2D_data | 同上；产物路径须由 `layout` 显式提供 |
| `function.data_reload` | _g3_w13 | WRITE | 是 | 同上（`refresh()` javap 核实） | tests/test_g3_w13.py（145 测） | — | 同上 |
| `function.evaluate` | _g3_w13 | EVALUATE | 是 | 同上 | tests/test_g3_w13.py（145 测） | W13_T016_2D_data | **任意坐标采样无离线出处 → 引擎调用前拒绝** |
| `selection.list` | _g3_w13 | READ | 否 | Programming Reference `model.selection()`/`Coordinate-Based Selections`；javap `SelectionList`/`Selection`/`GeomMeasureBase` | tests/test_g3_w13.py（145 测） | — | 同上 |
| `selection.create` | _g3_w13 | WRITE | 是 | 同上 | tests/test_g3_w13.py（145 测） | W13_T048_selection_drift, W15_T007_selections | 同上；几何作用域选区创建拒绝 |
| `selection.inspect` | _g3_w13 | READ | 否 | 同上 | tests/test_g3_w13.py（145 测） | — | 同上 |
| `selection.update` | _g3_w13 | WRITE | 是 | 同上 | tests/test_g3_w13.py（145 测） | — | 同上 |
| `selection.remove` | _g3_w13 | WRITE | 是 | 同上 | tests/test_g3_w13.py（145 测） | — | 同上 |
| `selection.entities` | _g3_w13 | READ | 否 | 同上 | tests/test_g3_w13.py（145 测） | — | 同上 |
| `selection.measure` | _g3_w13 | EVALUATE | 是 | 同上（`GeomMeasureFinal` 度量 getter；无 centroid getter） | tests/test_g3_w13.py（145 测） | W13_T048_selection_drift | 同上；bounding-box 期望因 `getBoundingBox()` 条目顺序无出处而拒绝 |
| `selection.query_spatial` | _g3_w13 | EVALUATE | 是 | 同上（Ball/Box/Cylinder/Disk 属性表） | tests/test_g3_w13.py（145 测） | — | 同上 |
| `selection.adjacency` | _g3_w13 | READ | 否 | 同上（Programming Reference “Adjacency”，`geom.getAdj(fromDim,toDim)`） | tests/test_g3_w13.py（145 测） | — | 同上 |
| `selection.validate` | _g3_w13 | EVALUATE | 是 | 同上 | tests/test_g3_w13.py（145 测） | W13_T048_selection_drift | 同上 |

## 2. W14 — 几何、Work Plane、CAD、坐标与 Pair（17 op）

| operation_id | 模块 | 效果 | 需隔离 | API 来源 | 证据 | 驱动用例 | 未验证/拒绝路径 |
|---|---|---|---|---|---|---|---|
| `geometry.sequence_create` | _g3_w14 | WRITE | 是 | Programming Reference `model.geom()` + KB `comsol_api_geom.48.*`（Geometry Commands）；javap `GeomList`/`GeomSequence`/`GeomFeature`/`GeomInfo` | tests/test_g3_w14.py（139 测） | W16_T018_mesh, W16_T020_solver | `GEOMETRY_FEATURE_TYPE_IDS_UNVERIFIED`（CompositeCurve/Else/ElseIf/EndIf/FromMesh/If 六项）与 `WORKER_UNAVAILABLE_METHODS`（27 项，如 `axisymmetric`/`copy`/`source`/`destination`/`coord`） |
| `geometry.inspect` | _g3_w14 | READ | 否 | 同上 | tests/test_g3_w14.py（139 测） | — | 同上 |
| `geometry.feature_create` | _g3_w14 | WRITE | 是 | 同上 | tests/test_g3_w14.py（139 测） | GUARD_T010, W14_T009_geometry_edit, W16_T019_chainA_steady, W16_T019_chainB_transient, W16_T020_solver | 同上 |
| `geometry.feature_update` | _g3_w14 | WRITE | 是 | 同上 | tests/test_g3_w14.py（139 测） | W14_T009_geometry_edit, W16_T020_solver | 同上 |
| `geometry.feature_remove` | _g3_w14 | WRITE | 是 | 同上 | tests/test_g3_w14.py（139 测） | — | 同上 |
| `geometry.workplane_create` | _g3_w14 | WRITE | 是 | 同上（WorkPlane 页 + `GeomFeature.geom()` javap） | tests/test_g3_w14.py（139 测） | — | 同上 |
| `geometry.workplane_edit` | _g3_w14 | WRITE | 是 | 同上 | tests/test_g3_w14.py（139 测） | W14_T009_geometry_edit | 同上 |
| `geometry.array_create` | _g3_w14 | WRITE | 是 | 同上（Array 页属性表） | tests/test_g3_w14.py（139 测） | W14_T009_geometry_edit | 同上 |
| `geometry.build` | _g3_w14 | COMPUTE | 是 | 同上（“Building Geometry Features”） | tests/test_g3_w14.py（139 测） | W14_T009_geometry_edit, W16_T019_chainA_steady, W16_T019_chainB_transient | 同上 |
| `geometry.finalize` | _g3_w14 | WRITE | 是 | 同上（Finalize 页：`FormUnion`/`FormAssembly`，tag 仅 `fin`） | tests/test_g3_w14.py（139 测） | — | 同上 |
| `geometry.import` | _g3_w14 | WRITE | 是 | 同上（Import 页，格式由 `filename` 选择） | tests/test_g3_w14.py（139 测） | W14_T034_local_paths | 同上；许可证缺失时按探测记录阻塞，不移除条目 |
| `geometry.measure` | _g3_w14 | EVALUATE | 是 | 同上（Measurements 页 + javap） | tests/test_g3_w14.py（139 测） | W14_T009_geometry_edit | 同上 |
| `geometry.validate` | _g3_w14 | EVALUATE | 是 | 同上 | tests/test_g3_w14.py（139 测） | — | 同上 |
| `definition.component_manage` | _g3_w14 | DYNAMIC | 是 | javap `Model`/`ModelNode`/`Material`/`PhysicsList`/`MultiphysicsCouplingList`/`PropFeature`；KB `physics.xml` 完成度数据 | tests/test_g3_w14.py（139 测） | W16_T018_mesh, W16_T020_solver | 同上；component `copy`/`duplicate` 命中 `WORKER_UNAVAILABLE_METHODS` |
| `definition.coordinate_manage` | _g3_w14 | DYNAMIC | 是 | 同上 | tests/test_g3_w14.py（139 测） | — | 同上；`coord()`/`isLinear()`/`isOrthonormal()`/`masterSystem()` 不在白名单 |
| `definition.pair_manage` | _g3_w14 | DYNAMIC | 是 | 同上（`PAIR_TYPE_IDS`：Contact/GeneralContact/Identity/SectorSymmetry） | tests/test_g3_w14.py（139 测） | — | 同上；`source`/`destination` 绑定不在白名单 |
| `definition.coupling_manage` | _g3_w14 | DYNAMIC | 是 | 同上（`COUPLING_TYPE_IDS`） | tests/test_g3_w14.py（139 测） | — | 同上 |

## 3. W15 — 材料、物理场与多物理耦合（19 op）

| operation_id | 模块 | 效果 | 需隔离 | API 来源 | 证据 | 驱动用例 | 未验证/拒绝路径 |
|---|---|---|---|---|---|---|---|
| `material.list` | _g3_w15 | READ | 否 | KB `COMSOL_ProgrammingReferenceManual` p.148/165 + `ApplicationProgrammingGuide`（`density`/`heatcapacity`/`thermalconductivity`）；javap `MaterialList`/`MaterialModel` | tests/test_g3_w15.py（141 测） | — | `UNVERIFIED_PATHS`（4 项：material.import、physics.pde_manage、MATLAB/function 材料类型、`dependent_variables` 数组形式） |
| `material.create` | _g3_w15 | WRITE | 是 | 同上 | tests/test_g3_w15.py（141 测） | W15_T017_material, W16_T019_chainA_steady, W16_T019_chainB_transient, W16_T020_solver | 同上 |
| `material.inspect` | _g3_w15 | READ | 否 | 同上 | tests/test_g3_w15.py（141 测） | — | 同上 |
| `material.set_properties` | _g3_w15 | WRITE | 是 | 同上（含张量/温变属性名） | tests/test_g3_w15.py（141 测） | W15_T017_material, W16_T019_chainA_steady, W16_T019_chainC_continue, W16_T020_solver | 同上；产品侧 `hasProperty()=false` 已按 KB 修正门禁 |
| `material.group_manage` | _g3_w15 | WRITE | 是 | 同上（`propertyGroup` 语义；javap `MaterialModelList`） | tests/test_g3_w15.py（141 测） | — | 同上；`addInput`/`removeInput`/`input` 经白名单访问 |
| `material.selection_set` | _g3_w15 | WRITE | 是 | 同上 | tests/test_g3_w15.py（141 测） | W15_T017_material, W16_T019_chainA_steady | 同上 |
| `material.remove` | _g3_w15 | WRITE | 是 | 同上 | tests/test_g3_w15.py（141 测） | — | 同上 |
| `material.validate` | _g3_w15 | EVALUATE | 是 | 同上（覆盖内预检：材料缺失/关键属性缺失/空选区/维度不匹配；未覆盖规则输出 unknown） | tests/test_g3_w15.py（141 测） | W15_T017_material | 同上 |
| `physics.list` | _g3_w15 | READ | 否 | javap `ComponentPhysicsList`/`Physics`/`PhysicsFeatureList`/`PhysicsFeature`/`MultiphysicsCouplingList`；KB `physics.xml` + `com.comsol.heat_1.0.0.jar` | tests/test_g3_w15.py（141 测） | — | 同上 |
| `physics.create` | _g3_w15 | WRITE | 是 | 同上（接口类型词表 `PHYSICS_INTERFACE_IDS`，真实内部 type） | tests/test_g3_w15.py（141 测） | W13_T015_units, W16_T019_chainA_steady, W16_T019_chainB_transient, W16_T020_solver | 同上；`dependent_variables` 数组形式拒绝 |
| `physics.inspect` | _g3_w15 | READ | 否 | 同上 | tests/test_g3_w15.py（141 测） | W15_T007_selections | 同上 |
| `physics.feature_create` | _g3_w15 | WRITE | 是 | 同上（特征 token 词表 `PHYSICS_FEATURE_TOKENS`） | tests/test_g3_w15.py（141 测） | W13_T015_units, W15_T007_selections, W16_T019_chainA_steady, W16_T019_chainB_transient, W16_T020_solver | 同上 |
| `physics.feature_update` | _g3_w15 | WRITE | 是 | 同上 | tests/test_g3_w15.py（141 测） | W13_T015_units, W15_T007_selections, W16_T019_chainC_continue, W16_T020_solver | 同上 |
| `physics.feature_remove` | _g3_w15 | WRITE | 是 | 同上 | tests/test_g3_w15.py（141 测） | — | 同上 |
| `physics.selection_set` | _g3_w15 | WRITE | 是 | 同上（含 `apply_local_selection`） | tests/test_g3_w15.py（141 测） | W13_T048_selection_drift, W15_T007_selections | 同上 |
| `physics.multiphysics_manage` | _g3_w15 | DYNAMIC | 是 | 同上（`MULTIPHYSICS_COUPLING_IDS`；KB LiveLink for MATLAB 示例） | tests/test_g3_w15.py（141 测） | — | 同上；只对实际测试组合声称可用 |
| `physics.initial_values_set` | _g3_w15 | WRITE | 是 | 同上（`InitialValues` token） | tests/test_g3_w15.py（141 测） | — | 同上 |
| `physics.validate` | _g3_w15 | EVALUATE | 是 | 同上（三态观察：默认不可编辑/继承状态按错误传播处理） | tests/test_g3_w15.py（141 测） | W13_T015_units | 同上 |
| `physics.remove` | _g3_w15 | WRITE | 是 | 同上 | tests/test_g3_w15.py（141 测） | — | 同上 |

## 4. W16 — 网格、Study、Solver（28 op）

| operation_id | 模块 | 效果 | 需隔离 | API 来源 | 证据 | 驱动用例 | 未验证/拒绝路径 |
|---|---|---|---|---|---|---|---|
| `mesh.list` | _g3_w16 | READ | 否 | KB `comsol_api_mesh.49.*`（.004/.005/.007/.010/.011/.021）；javap `MeshSequence`/`MeshFeature`/`GeomMeshFeature` | tests/test_g3_w16.py（73 测） | — | 未发布范围（`mesh.import`/`mesh.export`/`study.sweep_manage`/`solver.log_read` 等）、`quality=custom`、网格最差单元位置与纯网格 DOF 无验证读路径、缺失白名单项以 `allowlist_entry_required` 报告 |
| `mesh.create` | _g3_w16 | WRITE | 是 | 同上（.004 序列创建；.021 物理/用户控制） | tests/test_g3_w16.py（73 测） | W16_T018_mesh, W16_T019_chainA_steady, W16_T019_chainB_transient, W16_T020_solver | 同上 |
| `mesh.inspect` | _g3_w16 | READ | 否 | 同上（.010 status 词表递归） | tests/test_g3_w16.py（73 测） | — | 同上 |
| `mesh.feature_create` | _g3_w16 | WRITE | 是 | 同上（.005 特征创建 + 嵌套属性形式） | tests/test_g3_w16.py（73 测） | W16_T018_mesh, W16_T019_chainA_steady, W16_T019_chainB_transient, W16_T020_solver | 同上 |
| `mesh.feature_update` | _g3_w16 | WRITE | 是 | 同上（走 R03 类型化回读纪律） | tests/test_g3_w16.py（73 测） | W16_T018_mesh, W16_T020_solver | 同上 |
| `mesh.feature_remove` | _g3_w16 | WRITE | 是 | 同上（.011 删除；javap `MeshFeatureList`） | tests/test_g3_w16.py（73 测） | — | 同上 |
| `mesh.build` | _g3_w16 | COMPUTE | 是 | 同上（.007 `run(<ftag>)`） | tests/test_g3_w16.py（73 测） | W16_T018_mesh, W16_T019_chainA_steady, W16_T019_chainB_transient | 同上；build 成功 ≠ 网格合格 |
| `mesh.clear` | _g3_w16 | WRITE | 是 | 同上（`clearMesh()`） | tests/test_g3_w16.py（73 测） | — | 同上 |
| `mesh.statistics` | _g3_w16 | READ | 否 | 同上（单元数/类型/顶点/生长率等统计 getter） | tests/test_g3_w16.py（73 测） | W16_T018_mesh, W16_T019_chainA_steady, W16_T019_chainB_transient | 同上 |
| `mesh.quality` | _g3_w16 | EVALUATE | 是 | 同上（`QUALITY_MEASURES`；最差单元位置 `NOT_AVAILABLE`） | tests/test_g3_w16.py（73 测） | W16_T018_mesh | 同上；`custom` 度量拒绝 |
| `mesh.validate` | _g3_w16 | EVALUATE | 是 | 同上 | tests/test_g3_w16.py（73 测） | — | 同上 |
| `study.list` | _g3_w16 | READ | 否 | KB `comsol_api_solver.51.*`；javap `Study`/`StudyFeature`/`PropFeature` | tests/test_g3_w16.py（73 测） | W16_T020_solver | 同上 |
| `study.create` | _g3_w16 | WRITE | 是 | 同上（`STUDY_STEP_TYPE_SOURCES`） | tests/test_g3_w16.py（73 测） | W16_T019_chainA_steady, W16_T019_chainB_transient, W16_T020_solver | 同上 |
| `study.inspect` | _g3_w16 | READ | 否 | 同上 | tests/test_g3_w16.py（73 测） | — | 同上 |
| `study.remove` | _g3_w16 | WRITE | 是 | 同上 | tests/test_g3_w16.py（73 测） | — | 同上 |
| `study.step_create` | _g3_w16 | WRITE | 是 | 同上（Stationary/Time Dependent step） | tests/test_g3_w16.py（73 测） | W16_T019_chainA_steady, W16_T019_chainB_transient, W16_T020_solver | 同上 |
| `study.step_update` | _g3_w16 | WRITE | 是 | 同上（时间范围/初值等按真实属性表） | tests/test_g3_w16.py（73 测） | W16_T019_chainC_continue | 同上 |
| `study.step_remove` | _g3_w16 | WRITE | 是 | 同上 | tests/test_g3_w16.py（73 测） | — | 同上 |
| `study.physics_activation` | _g3_w16 | WRITE | 是 | 同上（`PHYSICS_ACTIVATION_KEYS` = physics/coupling） | tests/test_g3_w16.py（73 测） | — | 同上 |
| `study.solver_generate` | _g3_w16 | WRITE | 是 | 同上（`createAutoSequences`；`STUDY_AUTO_SEQUENCE_TYPES` = all/jobs/sol） | tests/test_g3_w16.py（73 测） | W16_T019_chainA_steady, W16_T019_chainB_transient, W16_T020_solver | 同上；自动/手工 solver 保留策略显式 |
| `study.run` | _g3_w16 | COMPUTE | 是 | 同上 | tests/test_g3_w16.py（73 测） | W16_T019_chainA_steady, W16_T019_chainB_transient, W16_T019_chainC_continue, W16_T020_solver | 同上；长求解时 health/status/log 保持响应，等待超时不表示引擎已停止 |
| `solver.list` | _g3_w16 | READ | 否 | 同上（javap `SolverSequence`） | tests/test_g3_w16.py（73 测） | W16_T019_chainA_steady, W16_T019_chainB_transient, W16_T020_solver | 同上 |
| `solver.inspect` | _g3_w16 | READ | 否 | 同上（多层 SolverFeature 递归） | tests/test_g3_w16.py（73 测） | W16_T019_chainC_continue, W16_T020_solver | 同上；链 C 实测返回 `INVALID_NODE_PATH`（路径 tag 问题，实机 FAIL） |
| `solver.create` | _g3_w16 | WRITE | 是 | 同上（`SOLVER_FEATURE_TYPE_SOURCES`） | tests/test_g3_w16.py（73 测） | — | 同上 |
| `solver.feature_create` | _g3_w16 | WRITE | 是 | 同上 | tests/test_g3_w16.py（73 测） | — | 同上 |
| `solver.feature_update` | _g3_w16 | WRITE | 是 | 同上（`SOLVER_DOCUMENTED_PROPERTIES`；容差/时间步等按真实 API） | tests/test_g3_w16.py（73 测） | W16_T020_solver | 同上；T020 实测子特征路径 tag 拒绝 |
| `solver.feature_remove` | _g3_w16 | WRITE | 是 | 同上 | tests/test_g3_w16.py（73 测） | — | 同上 |
| `solver.run` | _g3_w16 | COMPUTE | 是 | 同上 | tests/test_g3_w16.py（73 测） | — | 同上 |

## 5. runtime 与 results（3 op）

| operation_id | 模块 | 效果 | 需隔离 | API 来源 | 证据 | 驱动用例 | 未验证/拒绝路径 |
|---|---|---|---|---|---|---|---|
| `runtime.capabilities` | _g3_runtime | READ | 否 | javap `ModelUtil`（`hasProduct`/`getComsolVersion`）；KB Programming Reference PDF p.42 + 应用编程指南 `License Methods`（PDF p.130） | tests/test_g3_runtime.py（76 测） | W15_T042_license | 无法解析/无出处的产品探针按 `BLOCKED_LICENSE`/可识别拒绝返回；不 checkout 席位（`SEAT_CONSUMING_METHODS` 永不调用） |
| `runtime.license_inspect` | _g3_runtime | READ | 否 | 同上（每产品一次 `hasProduct`；引擎身份 `getComsolVersion`） | tests/test_g3_runtime.py（76 测） | W15_T042_license | 同上；T042 实测 product payload 身份不匹配（产品侧缺口） |
| `result.sample_path` | _g3_results | EVALUATE | 是 | javap `Results`/`NumericalFeature`/`Model.sol`；KB `comsol_api_results.52.082`（Interp）、`52.156`（Solution dataset） | tests/test_g3_results.py（90 测） | W16_T019_chainA_steady, W16_T019_chainB_transient | `UNVERIFIED_PATHS`（5 项：selection/aggregate、per-solution 选择、非 real 复数模式、非 line 路径、`getCoordinates()` 不在白名单）与 `ALLOWLIST_ADDITIONS`（12 项未用到的期望方法） |

## 6. 专项约定

### 6.1 隔离要求集合
`REQUIRES_ISOLATION` = 72 项，定义为“效果 ≠ READ”的全部操作（`_g3_ops`）。含 `EVALUATE`（11，可能创建/
改写临时节点）、`DYNAMIC`（5）、`COMPUTE`（4，几何 build/mesh build/study run/solver run）与
`WRITE`（52）。父层在派发前用目录效果再核对一次；`tests/test_g3_wiring.py` 锁定该集合恰为“非 READ”。
隔离本身由控制端验证（loopback + 非 loopback 403 拒绝 + access-log 关联），不信客户端自报。

### 6.2 效果映射（含 DYNAMIC → project_write）
控制面翻译表（`_managed_backend._G3_EFFECT_MAP` 与 `_execution_contract._G3_CATALOG_EFFECTS` 必须一致）：

| 目录效果 | 权限 |
|---|---|
| `READ` | `inspect` |
| `WRITE` | `project_write` |
| `STATE_WRITE` | `state_write` |
| `FILE_WRITE` | `file_write` |
| `EVALUATE` | `evaluate` |
| `COMPUTE` | `compute` |
| `TRUSTED_CODE` | `trusted_code` |
| `DYNAMIC` | `project_write` |

未分类的 G3 效果 → `PERMISSION_DENIED`（fail closed）。本轮 96 个操作全部命中目录效果，
零回退、零默认值。

### 6.3 写前拒绝 vs 写后 data 约定
- **写前拒绝**（`ExecutionContractError` → 外层 `success=false` + 结构化 `error`）：参数/类型/路径/tag/
  单位/不支持项校验失败。控制面把它标为 `REFUSED` 且不派发引擎调用；`allowlist_entry_required` 会指出
  缺失的 Worker 白名单条目。
- **写后结果作为 `data`**（`success=true`，但 `ok/status/partial_change/execution_state_unknown/
  not_executed/applied/failed` 如实描述）：setter 已运行但回读不匹配、build 抛错、属性名在该节点上不存在等。
  不静默重试、不把部分失败写成成功；`isError` 与 `success` 由控制面统一维持一致。
- 引擎状态无法观测时记 `execution_state_unknown`，绝不推断为“未发生”。

### 6.4 ephemeral 节点纪律
`result.sample_path` 等 `EVALUATE` 操作创建**临时**数值节点（`Interp`，tag 前缀 + 唯一化搜索：
`ephemeral_tag`，最多 `MAX_EPHEMERAL_TAG_ATTEMPTS` 次尝试）后，必须：
① 走既有串行 ephemeral mutation 语义（同队列、同 job/幂等路径）；
② 只读回**自身**创建的节点，不触碰用户 Derived Values 与其它结果节点；
③ 用后删除并回读删除结果，未验证删除时在 `data` 中显式报告（`the ephemeral numerical node was not
   verifiably removed`），绝不静默遗留；
④ 只读描述不修改模型；临时节点清理失败不得包装为成功。
产品侧缺口：`evaluate_expressions` 目前不报告临时节点归属（T033 FAIL），属未修复的产品缺陷，
不得据 G3 的实现推断其已解决。
