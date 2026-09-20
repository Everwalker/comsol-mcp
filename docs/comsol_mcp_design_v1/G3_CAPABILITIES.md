# G3 Capability 边界（W13–W16 + runtime + results）

本文件描述本轮（Goal `NEXT_GOAL_MAC_G3.md`）在 Mac 工作树中实现的领域操作面与其边界。
权威验收摘要为 `evidence/phase4_acceptance.json`；R01–R06 的补修记录见 `G3_REVIEW_FIXES.md`；
逐操作覆盖表见 `G3_OPERATIONS.md`。**注册表里存在一条路由不等于引擎/实机验收通过。**

## 1. 已发布操作面（真实计数）

| 域 | 模块 | 发布 operation 数 |
|---|---|---|
| W13 参数/变量/函数/选区 | `comsol_mcp/_g3_w13.py` | **29** |
| W14 几何/Work Plane/CAD/坐标/Pair | `comsol_mcp/_g3_w14.py` | **17** |
| W15 材料/物理场/多物理耦合 | `comsol_mcp/_g3_w15.py` | **19** |
| W16 网格/Study/Solver | `comsol_mcp/_g3_w16.py` | **28** |
| runtime（许可证/能力探针） | `comsol_mcp/_g3_runtime.py` | **2**（`runtime.capabilities`、`runtime.license_inspect`） |
| results（W16 验收基础设施） | `comsol_mcp/_g3_results.py` | **1**（`result.sample_path`） |
| 合计 | `_g3_ops.DISPATCH` | **96** |

计数来自 `comsol_mcp/_g3_ops.IMPLEMENTED_OPERATIONS`（`len == 96`，模块来源
29/17/19/28/2/1，无跳过模块、无重复 id；重复 id 是硬错误）。

- MCP 静态发布面：`full` profile **65 个工具**（真实 live 运行 `driver4` 的
  `GUARD_T038` 记录 `tool_count = 65`），`domain`/`expert` 为收窄的展示过滤；
  G3 领域操作经 `operation_call` / `registry_call`(别名) / `operation_describe` 调用，
  不是 96 个新增静态工具。
- 效果与隔离：96 个操作的效果**全部**取自动作目录（`EFFECT_SOURCES` 全部 `catalog`，
  0 例回退表、0 例 fail-closed 默认）。分布：`WRITE` 52、`READ` 24、`EVALUATE` 11、
  `DYNAMIC` 5、`COMPUTE` 4；`REQUIRES_ISOLATION` **72** 项（= 全部非 READ 操作，
  含 `EVALUATE`/`DYNAMIC`/`COMPUTE`），按模块 19/16/15/21/0/1。
- 每个操作都必须通过 `tests/test_g3_wiring.py`（5 测）：可执行、权限可分类、
  效果可翻译、隔离集合恰为“非 READ”。

## 2. 逐域能力与 API 来源

所有方法名与 `create(<tag>, <type>)` 类型串都离线核对过以下两类本机来源之一，未凭显示名猜测：
`javap` 已安装公共 API jar
`/Applications/COMSOL64/Multiphysics/apiplugins/com.comsol.api_1.0.0.jar`
（必要时加实现 jar `plugins/com.comsol.model_1.0.0.jar`），以及本机 COMSOL 6.4 文档语料
`/Users/everwalker/Documents/KnowledgeBases/COMSOL-6.4-KB`。

### W13（29 op：parameter 5 / variable 6 / function 8 / selection 10）
- 关键来源：Programming Reference `model.param()`、`model.variable()`、`model.func()`（类型表 +
  各类型属性表）、`model.selection()` 与 `Coordinate-Based Selections`；javap
  `ModelParam`/`ModelParamGroupList`、`ExprList`/`ComponentExprList`/`ModelNode.variable()`、
  `FunctionFeatureList`/`FunctionFeature`、`SelectionList`/`SelectionFeature`/`GeomMeasureBase`。
- 函数类型词表 22 项（`FUNCTION_TYPE_IDS`）；仅对已取回属性表的类型允许写属性，未取回类型
  （`CGNS`/`DNN`/`PartialFractionFit`/`FunctionSwitch`）在写前拒绝。
- 选区类型词表 11 项（`Explicit`/`Union`/`Intersection`/`Difference`/`Complement`/`Adjacent`/
  `Ball`/`Box`/`Cylinder`/`Disk`/`LogicalExpression`）。
- 未核实拒绝路径（模块内“Known, reported gaps”，写前拒绝、绝不猜测）：`function.evaluate` 任意坐标采样
  （无离线出处）、centroid 测量（`GeomMeasureBase` 无该 getter）、读操作的 `spatial`/`objects` 解析、
  `objects` 绑定、几何作用域选区的**创建**、无属性表的函数类型、`data_import` 的产物路径解析
  （引擎侧路径须由 `layout` 提供）、参数组重命名。

### W14（17 op：geometry 13 / definition 4）
- 关键来源：Programming Reference `model.geom()`、KB `comsol_api_geom.48.057`（Geometry Commands）
  与逐命令页（Rectangle/Block/Array/WorkPlane/Import/Finalize/Move/Copy/Compose）；javap
  `GeomList.create(String,int)`、`GeomSequence`/`GeomFeature`/`GeomInfo`。
- 几何特征类型词表 94 项（`GEOMETRY_FEATURE_TYPE_IDS`）；`Finalize` 只允许 `FormUnion`/`FormAssembly`
  且 tag 仅 `fin`；`Part` 不在支持的序列类型内。
- 未核实拒绝路径：`GEOMETRY_FEATURE_TYPE_IDS_UNVERIFIED`（`CompositeCurve`/`Else`/`ElseIf`/`EndIf`/
  `FromMesh`/`If` 六项）与 `WORKER_UNAVAILABLE_METHODS`（27 项，含 `axisymmetric`、`copy`、`duplicate`、
  `source`/`destination`、`coord`/`isLinear`/`masterSystem`、`object`/`objects`/`objectNames`、
  `status`/`problems`、`searchMethod`/`searchDist` 等）。命中即以 `allowlist_entry_required` 拒绝。

### W15（19 op：material 8 / physics 11）
- 关键来源：javap `MaterialList`/`MaterialModelList`/`MaterialModel`、`ComponentPhysicsList`/
  `PhysicsFeatureList`/`PhysicsFeature`、`MultiphysicsCouplingList`、`Model.getUsedProducts()`/
  `isReadOnly()`；KB `COMSOL_ProgrammingReferenceManual` p.148/165（材料类型 `Common`/`Switch`/`Link`/
  `PorousMedia`/`External`、`propertyGroup` 语义）、`ApplicationProgrammingGuide`（`thermalconductivity`/
  `density`/`heatcapacity` 的 `def` 组名称与 3×3 张量形式）、`comsol_api_fileformats.53.15`，以及安装内完成度
  数据 `Multiphysics/data/completion/physics.xml` 与 `com.comsol.heat_1.0.0.jar`（`HeatTransferInSolids`）。
- **产品侧修正（KB 有据）**：新 Common 材料上 `hasProperty()` 返回 false，而 KB 明确记载可直接
  `propertyGroup('def').set('density'/'heatcapacity'/'thermalconductivity')`；产品门禁按 KB 来源修正，
  但仍逐属性回读。
- 未核实拒绝路径（`UNVERIFIED_PATHS`，4 项）：`material.import`（材料库/产物契约无本地核实来源，未发布）、
  `physics.pde_manage`（PDE 属 G5 范围，未取回属性表，未发布）、`MATLAB`/`function` 材料类型
  （文档类型仅上述五种）、`physics.create` 的 `dependent_variables` 数组形式（javap 存在但无变量名/顺序
  契约，仅使用文档化的三参几何形式）。

### W16（28 op：mesh 11 / study 11 / solver 6）
- 关键来源：KB `comsol_api_mesh.49.*`（.004 序列创建、.005 特征创建、.007 build、.010 status 词表、
  .011 删除、.021 物理控制网格）与 `comsol_api_solver.51.*`；javap `MeshSequence`/`MeshFeature`/
  `GeomMeshFeature`、`Study`/`StudyFeature`、`SolverSequence`/`SolverFeature`、`PropFeature`。
- 网格/Study/Solver 类型词表由 `MESH_FEATURE_TYPE_SOURCES`、`STUDY_STEP_TYPE_SOURCES`、
  `SOLVER_FEATURE_TYPE_SOURCES` 三个显式来源表驱动；质量度量词表 `QUALITY_MEASURES`。
- 未核实拒绝路径：`mesh.import`/`mesh.export`/`mesh.convergence_study`、`study.initial_solution_set`/
  `study.sweep_manage`、`solver.solution_inspect`/`solution_clear`/`solution_transfer`/`log_read`/
  `resource_configure` 均**未发布**；`quality=custom` 拒绝（不静默改用默认度量）；网格最差单元位置与
  纯网格 DOF 记 `NOT_AVAILABLE`（无验证读路径，不估算）；缺失白名单项以 `allowlist_entry_required`
  报告（`build`/`stat`/`getNumElem` 等的部分读路径）。

### runtime（2 op）
- `runtime.license_inspect`：逐产品 `ModelUtil.hasProduct(String...)`（每产品一次调用）+ 引擎身份
  `ModelUtil.getComsolVersion()` + 可选 `Model.getUsedProducts()`。**从不 checkout 席位**：
  `SEAT_CONSUMING_METHODS`（`checkoutLicense`、`checkoutLicenseForFileOnServer`）在生产路径绝不调用，
  并有测试锁定。来源：javap `ModelUtil`（含 `public static boolean hasProduct(java.lang.String...)`）
  与 KB Programming Reference PDF p.42、应用编程指南 “License Methods”（PDF p.130）。
- `runtime.capabilities`：报告能力状态、限制与**实际观测到的**证据，不把方法存在升级为 VERIFIED。
- 探针无法解析/无出处时返回可识别拒绝（驱动侧将 `BLOCKED_LICENSE` 记为 BLOCKED，绝不记为 PASS）。

### results（1 op，W16 验收基础设施）
- `result.sample_path`：唯一动机是 Goal §8 允许的“最小采样适配器”。基于一个文档化的**临时** `Interp`
  数值特征（`comsol_api_results.52.082`）与 Solution dataset（`52.156`），实现“沿一条线采样”。
- 明确不是 W17 结果系统：`UNVERIFIED_PATHS` 5 项拒绝（`spec.selection`/`aggregate`/`weight_expression`、
  per-solution 选择（`inner`/`outer`/`time`/`frequency`/`parameters`）、非 `real` 复数模式、非 `line`
  路径类型、`getCoordinates()` 坐标回读）；`ALLOWLIST_ADDITIONS` 记录 12 项“本该用但未入白名单”的方法。

## 3. 线格式与路径词汇

- **PropertySet 行数组 + 映射兼容**：领域操作接受两种线形——
  ① 动作目录的 `PropertySet`：`[{name, value}]` 行数组，行内只允许 `name`+`value`
  （`PROPERTY_ROW_FIELDS`，`additionalProperties=false`），`value` 为线格式 `TypedValue`
  （`kind`/`shape`/`data`/`unit`/`java_signature`）；② 模块内部使用的映射形式
  `{name: JSON 值 | TypedValue | {value, unit}}`，逐字节透传。两种形式在引擎前做同样的结构校验
  （行必须为对象、名称非空且唯一、值必须是良构 TypedValue），畸形即 `INVALID_REQUEST`。
  逐属性 kind/shape/unit 与引擎元数据的比对仍在写入层完成。
- **NodePath 集合名**：`ACCESSOR_METHODS`（29 项）= `active`、`component`、`dataset`、`feature`、
  `geom`、`geometry`、`material`、`mesh`、`modelNode`、`numerical`、`param`、`physics`、`result`、
  `selection`、`sol`、`study`、`table`、`variable`、`view`、`plotGroup`、`export`、`func`、
  `multiphysics`、`pair`、`cpl`、`coordSystem`、`propertyGroup`、`extraDim`、`probe`。
  路径段只能是 `{collection, tag}` 或 `{accessor}`；未知集合/访问器即 `INVALID_NODE_PATH`。
  Work Plane 内层几何走 `feature:<wp>` + `accessor:geom`；solver 子特征走
  `sol:<tag>` + `feature:<tag>` 递归（链 C 夹具验证了多层路径）。
- 线格式回归由 `tests/test_g3_phase4_wire_replay.py`（12 测）负责：直接从驱动源码提取真实 payload
  （≥20 个 PropertySet 调用站点）回放到真实操作代码 + 仓库假引擎树上，并审计没有任何 NodePath 参数
  被裸 tag 字符串替代。

## 4. Java Worker 白名单增补记录（`worker_java/PersistentComsolWorker.java`）

| 分组（文件内注释块） | 增补项数 | 说明 |
|---|---|---|
| R03 入口键回读 | 2 | `getEntryKeys`、`getEntryKeyIndex` |
| G3 通用访问器 | 10 | `func`、`multiphysics`、`pair`、`cpl`、`coordSystem`、`propertyGroup`、`extraDim`、`probe`、`view`、`getUsedProducts` |
| G3 W13 | **27** | `group`、`move`、`evaluate`/`evaluateUnit`/`evaluateComplex`、`scope`、`dim`/`dimension`、`functionNames`、`importData`、`refresh`、`measure`、`getArea`/`getVolume`/`getLength`/`getPerimeter`/`getBoundaryArea`/`getBoundaryVolume`、`getBoundingBox`、`getNEntities`、`getNFiniteVoids`、`getVtxCoord`/`getVtxDistance`/`getEdgeAngle`、`getAdj`、`getSDim`、`lengthUnit` |
| G3 W15 + W16（共用一块 58 项） | W15 **5** / W16 **53** | W15 材料/物理专用：`hasProperty`、`materialType`、`addInput`、`removeInput`、`input`；其余 53 项为网格/Study/Solver 面（`automatic`、`clearMesh`、`current`、`getNumElem`、`getQualityDistr`、`hasProblem`、`isStoreSolution`、`setSolveFor`、`type` 等） |
| `MODEL_UTIL` 命令面 | +1 | `hasProduct`（文档化、不消耗席位；`MODEL_UTIL` 现 9 项） |

计数为对本机 `PersistentComsolWorker.java` 当前字节的分组实测（R03 2 + 通用 10 + W13 27 + W15/16 共用 58
+ MODEL_UTIL 1）。注：之前流传的“W16 54 项”未能从文件复现；文件内共用注释块为 58 项，按
“W15 材料/物理专用 5 项 + 其余 53 项”划分；若把 `hasProperty` 计入通用属性读回，则 W16 侧为 54 项。

## 5. 能力边界（不得升级的结论）

- **本轮 live 验收未完成**：W13–W16 的实机验收在 managed-revision 语义（external-change 门禁、
  多调用流程中的 stale `expected_revision` 采纳）与少量引擎级拒绝（材料属性 rank、`physics.feature_create`
  `ins1`、T020 solver 子特征路径 tag、链 C `solver.inspect` 路径）上保持 BLOCKED/FAIL；R01/R03/R04 的 live
  探针同样被 revision 记账阻塞。因此**不声明** `G3_MAC_EXECUTABLE_SCOPE_PASS`。
- **产品缺口（未修复，记为 FAIL）**：`evaluate_expressions` 未发布求值策略、未报告临时节点归属、
  `3*3` 返回空值（T033）；`model identity mismatch on license probe payload`（T042）；`material def-group`
  在新 Common 材料上的 `hasProperty()=false`（已按 KB 修正门禁，属产品侧发现）。
- **未覆盖组合保持 UNVERIFIED**：Windows x64、macOS Intel、COMSOL 6.3、GUI/Desktop 交互、未授权商业模块
  （CAD 导入等按实际许可证探测，缺失即记录阻塞，不从范围表删除）。
- 原生 `ModelChangedHandler` 回调 FAIL、浅指纹范围、Worker epoch 身份、超时≠取消语义保持原 verdict。
- 注册表可发布（65 工具）与操作可解析（96 op）都**不**代表引擎回读、数值或物理验收通过。
