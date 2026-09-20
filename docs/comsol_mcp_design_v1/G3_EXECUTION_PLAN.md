# G3_EXECUTION_PLAN.md — Mac 定向补修 R01–R06 与 W13–W16 执行计划

依据：`docs/comsol_mcp_design_v1/NEXT_GOAL_MAC_G3.md`（v1.0，审查 2026-09-20）。
本文件是执行计划（当前差异、复核、依赖、拟公开动作、许可证能力、测试/证据安排），随后按此实际写代码与执行。

## 0. 基线与现场状态（2026-09-20）

- 工作树：分支 `codex/mac-g2-accepted-delivery`，HEAD `1c6a5e9` 与审查 SHA `1c6a5e982742b6c1a92054409383fbfbbc56658e` 一致；除本 Goal 文档（未跟踪）外无本地修改、无本地领先实现；不 reset、不覆盖。
- 环境：macOS 27.0 arm64；COMSOL 6.4.0.293（`/Applications/COMSOL64/Multiphysics`）；外部 JDK Corretto 11；宿主 Python 3.12.13（Homebrew）。生产 stdio 运行使用已验证解释器 `/Users/everwalker/Documents/Codex/2026-09-05/https-github-com-wjc9011-comsol-multiphysics/comsol-mcp-server/.venv/bin/python`（提供 mcp SDK；cwd 指向本仓库）。
- 运行时：无本任务自有 COMSOL Server/Worker 存活；历史 Phase2 control/worker（PID 31074 / 30185）为明示保留的历史进程，不复用、不终止。端口 56388/56389 空闲。
- 安装配置：`bin/servers/webbridge/conf/server.xml` sha256 `95478d76…`（已恢复原字节，inode 14720294，mode/owner 与授权记录一致；provenance xattr 保留并如实记录）；`bin/tomcat/conf/server.xml` sha256 `8bb393d2…`。
- 许可证能力（历史探测）：ACDC `hasProduct=true`（未 checkout）；无实际缺失产品可用作负例，T042 负向子项保持 BLOCKED。完整模块清单在实机阶段用产品探针重新读取，仅作为本次采用依据。
- 未完成项：R01–R06 与 W13–W16 均未在工作树中实现（等于审查基线）。

## 1. R01–R06 复核结论与修复路径（先 RED，后修复）

### R01 表达式/字符串/数值视图回读一致性
- 确认：`_g2_contract.validate_typed_value` 允许 expected `string` 接收 `kind=expression`（预期种类判断分支）；`_g2_engine._typed_readback_matches` 要求 requested/returned `kind` 与 `shape` 完全相等，故同文本表达式写入 String 属性后回读为 `string` 即判 mismatch → UNKNOWN。
- 修复：在契约层定义显式「语义种类 ↔ COMSOL 存储/回读」映射：`expression` 写入 String 存储时，回读允许 `expression↔string` 同语义比较（保留原始表达式文本，不转浮点）；对 `Double/DoubleRowMatrix` 等可存在表达式视图的属性：数值 getter 不用于推断表达式，未支持的表达式写法在写前拒绝（保持 fail-closed）；`unit` 字段与表达式单位的关系写入契约（仅回显不算赋值成功）；数值比较容差显式（`numeric_tolerance`，默认精确比较）。
- 回归（`tests/test_g3_r01_expression.py`）：同文本 expression→String 回读验证；表达式数组/矩阵；合法单位表达式；不兼容单位/类型写前拒绝；引擎回读确实不同（仍 UNKNOWN/partial 保留）；非有限数严格边界。

### R02 完整节点发现、分页、错误与预算
- 确认：`children_node` 固定 10 个集合（无 material、variable、func、selection、multiphysics、coupling/坐标/pair 等）且异常吞掉；`find_nodes` 每层单页 `limit=500`、忽略 `next_cursor`、不报告搜索不完整。
- 修复：集合发现改为「allowlist + 逐集合能力探测」：`unsupported collection` 才可省略；连接断开/引擎异常/权限错误进入 `errors[]`，不静默为空。每层继续分页（`next_cursor` 链），返回 `complete/truncated/next_cursor/errors`；cursor 绑定 `generation/model_revision + 查询哈希`，陈旧/非法游标显式拒绝；设置 `max_nodes/max_rpc/max_seconds` 预算并可批量请求。`_g2_contract.ACCESSOR_METHODS` 与 Worker `METHODS` 同步扩展（func、multiphysics、pair、cpl、coordSystem、propertyGroup 等），名称与签名逐个以 6.4 真实 API 核实（KB 引用 + 实机探测），不凭显示名猜。
- 回归（`tests/test_g3_r02_discovery.py`）：material/variable/function/selection 可见；>500 子节点分页；多层 wp3 递归；截止时返回可恢复游标；中断不伪报 NOT_FOUND；失效引用/游标拒绝；兄弟节点保留；调用量实测报告。

### R03 indexed/keyed 写入的真实校验
- 确认：`property_index_set` 回读整体属性但不比较目标元素与非目标项；`property_entry_set` 不调用 getter、返回请求值。
- 修复：`setIndex` 后按版本适配的索引语义回读目标元素/行并比较请求值；检测自动扩容与 shape 变化；记录非目标元素是否保持不变（默认要求不变，变化时报 partial/UNKNOWN）；`setEntry` 以真实 key/entry 回读 API 校验；无权威回读路径时写前拒绝或受控未验证模式（默认拒绝）；普通 set/index/entry 统一产生 `applied/failed/not_executed`、`readback`、`verification_scope`、部分失败信息；不更换已验证 setter。
- 回归（`tests/test_g3_r03_indexed.py`）：向量元素、矩阵 cell/row、keyed entry；错误 key/index；setter no-op 检出；非目标项变化检出；空/单元素；外层 `isError`/UNKNOWN 一致。真实支持该操作的属性在实机验收中选择性覆盖。

### R04 “动作执行完” ≠ “invariants 成立”
- 确认：`run_transaction` 保存 invariants 但不执行；`_verify_transaction_record` 仅有限持久记录字段检查。
- 修复：定义有限、类型化的可执行 invariant 词汇：`node_exists` / `node_type` / `property_equals` / `property_tolerance` / `selection_non_empty` / `selection_dimension` / `scope_preserved`；`execution_status` 与 `verification_status` 分开，记录校验与实时模型校验分别标注 scope；apply 前 preflight：required 且未支持/格式非法 → 写前拒绝，可选未支持 → `NOT_RUN`（不计通过）；执行后逐条实时求值，required 失败 → 总体验收非 PASS（修改、checkpoint 与恢复选项仍返回，不伪装原子回滚）；验证绑定 transaction 的 `model_ref`/`revision`；不提前实现 W20。
- 回归（`tests/test_g3_r04_invariants.py`）：故意违反 required；未知条件；旧 revision；跨模型记录；动作全成功但检查失败；记录检查通过但实时属性已不同；失败后 checkpoint 恢复实际属性。

### R05 恢复记录损坏不能变成“没有记录”
- 确认：`TransactionStore.__init__` 对 `OSError/JSONDecodeError` 回退为空字典，损坏 JSON 被当作空记录且无报错。
- 修复：区分「文件不存在（首次初始化）」与「已存在但不可读/损坏」；后者保留原文件、返回明确持久化故障并阻止依赖该记录的写入/恢复；损坏后 `put` 不以新记录覆盖损坏历史；提供可审计恢复流程（保留失败副本 + hash 记录）；权威记录与 SQLite metadata 明确统一（在既有 `operations.sqlite3` 迁移或加固 sidecar，二选一并记录；禁止两套互相矛盾的真相）；写入复用已验证原子/持久化方式；并发/进程退出/迁移/恢复失败测试写明覆盖范围；`checkpoint.diff` 保持显式“不支持”，不以 hash 差异冒充语义 diff。
- 回归（`tests/test_g3_r05_store.py`）：不存在文件正常初始化；损坏/不可读保全（原文件 hash 不变）；故障恢复不重放引擎动作；跨重启事务/检查点可查；迁移前后引用匹配。

### R06 与 G3 直接相关的收口
1. 新领域动作全部走同一 model/session/revision、permission、queue、event context、job 与幂等路径（§3 按此实现；不新建“方便版”服务）。
2. 旧浅指纹/回调 FAIL 判定保持不变，不借 G3 重开。
3. 平台差异留在 adapter 层；保留 Windows 进程身份/classpath/句柄 flush 与 stdio 生命周期代码。
4. docs 查询契约：确认 `docs.error_search` 正式 schema 含 `node_type`，`docs.examples` 不含；现实现把 `node_type` 传给 `product` 过滤，会把「有文档」错筛为不可用。修复为版本/产品/节点类型各自语义（node_type 采用独立的标题/内容语义过滤并在契约中写明），以真实索引 fixture 在 `tests/test_g3_r06_docs_query.py` 回归（含“有文档但错筛掉”反例）。
5. README/CLAUDE/AGENTS 与新 registry 保持一致；历史阶段“禁入 W13”保留为历史说明，当前入口指向本 Goal。
6. 大模型 checkpoint 成本与完整 GUI/solution 恢复仍属后续范围；本轮只记录小模型保存/复制成本，不虚构全模型无副作用。

## 2. 依赖与顺序

`R01 → R02/R03（引擎底层）→ W13 → W14/W15（软件可并行，同 Server 实机队列串行）→ W16`；R04/R05 与 R06 各项可与上述并行，但合并前统一接口。实机验收需要 §5 的运行时授权，且不与软件工作互斥。

## 3. W13–W16 拟公开动作与实现路径

实现模式（遵守 R06.1）：新增 `comsol_mcp/_g3_common.py` 与 `_g3_w13.py`/`_g3_w14.py`/`_g3_w15.py`/`_g3_w16.py`；操作注册进 registry（扩展 `IMPLEMENTED_OPERATIONS`），经既有 `operation_call`/`registry_call` 公开、类型化路径调用；统一经过 `ManagedBackend` 的 ticket / isolation / 幂等 / queue / event context 门禁；Java 侧只做「受控 accessor 与命令」扩展（在持久 Worker 内，属普通 `project_write` 领域实现），普通建模不要求 trusted_code；任意用户 Java 仍单独授权。

按域首批范围（P0 为验收必需，P1 常用，P2 低频率；全部登记覆盖表）：
- W13：`parameter.list/get/set/remove/group_manage`；`variable.list/get/set/remove/group_create/selection_set`；`function.list/create/inspect/update/remove/data_import/data_reload/evaluate`（≥1D/2D 插值 + 解析/分段）；`selection.list/create/inspect/update/remove/entities/measure/query_spatial/adjacency/validate`。
- W14：`geometry.sequence_create/inspect/feature_create/feature_update/feature_remove/workplane_create/workplane_edit/array_create/build/finalize/measure/validate`；`definition.component_manage/coordinate_manage/pair_manage/coupling_manage`；`geometry.import` 按许可证探测决定可用性（不可用保持 BLOCKED 记录，不移除条目）。
- W15：`material.list/create/inspect/set_properties/group_manage/selection_set/remove/validate`（含 k(T)/Cp(T) 与各向异性张量、坐标系）；`physics.list/create/inspect/remove/feature_create/feature_update/feature_remove/selection_set/multiphysics_manage/initial_values_set/validate`（真实内部 type，如 `HeatTransferInSolids` 等，以实机 `physics().create` 探测为准）。
- W16：`mesh.list/create/inspect/feature_create/feature_update/feature_remove/build/clear/statistics/quality/validate`；`study.list/create/inspect/remove/step_create/step_update/step_remove/physics_activation/solver_generate/run`；`solver.list/inspect/create/feature_create/feature_update/feature_remove/run`。

Java Worker 需新增并经 6.4 API 核实的方法（示例）：`func`、`multiphysics`、`pair`、`cpl`、`coordSystem`、`propertyGroup`、`propertyGroupNames`、mesh/study/solver 相关既有 `create/set/run/tags` 复用；`getPropertyGroup` 等以 KB 与实机探测为准。`getEntityFromModelPath`、`entities` 等用于实体读回。

## 4. 覆盖表、证据与文档布局

- `G3_OPERATIONS.md`：W13–W16 动作覆盖表（公开 operation、领域/通用 API/受控 recipe 路径、输入输出 schema、API 来源、版本/产品前提、读回方式、真实证据、未验证原因），含未实现与许可证阻塞条目。
- `G3_REVIEW_FIXES.md`：R01–R06 逐项修复记录（RED 复现、修复、回归、实机回读证据指针）。
- `G3_CAPABILITIES.md`：本轮能力与边界。
- 证据：`evidence/phase4_acceptance.json` + `evidence/phase4/runs/<run_id>/`（environment/request/result/assertions/engine.log/前后模型与 hash）。私密原始内容留在本地忽略目录，公开只放脱敏信息与追溯 hash。
- 驱动：`tools/phase4_run_mcp.py`（生产 stdio，不启动/停止 COMSOL；复用 phase3 驱动模式与红色输出约定）。

## 5. 实机验收与运行时

- 运行时复用已审查的 Mac 方案：`tools/phase3_remote_addr_valve_runtime.py`（临时启用 webbridge RemoteAddrValve → 启动任务自有 Server → 隔离证明 → 验收 → 停止并恢复原文件）。该安装级修改属 G2 授权范围之外，需一次性 G3 最小授权（见会话请求）；未获授权期间只推进不依赖它的软件工作。
- 实机用例：R01/R03/R04/R05 引擎数据回读；W13（T006/T015/T048 适用子项 + T016 二维数据完整性子项）；W14（T009 完整适用几何子项 + T034 本机路径/导入子项）；W15（T007/T017/T042 适用子项）；W16（T018/T019/T020 + 链 A/B/C：空模型 3D 稳态导热、瞬态基准、已有用户风格模型局部续建）。
- 新入口受保护性：T010 幂等、T035 路径、T038 错误传播；所涉修改后的 T005/T033 保留性回归。
- 验收门槛（本轮拟议）：稳态线性场相对温差误差 ≤1e-4；瞬态归一化最大采样误差 ≤1e-3；如需为合理有限元离散调整，须保留旧结果、收敛证据与解释。

## 6. 测试与证据安排

- 每项修复先写暴露旧行为的测试（RED），再修复，再通过生产 stdio 回归（offline 部分）。
- 全量软件回归以实际输出计数报告（旧 419/1 快照不硬编码）；受影响 Phase2 回归按需执行 T012/T026/T027/T028/T057/T029/T050 中受影响者，跳过必须解释。
- 报告分别计数：代码实现、单元/协议通过、实机通过、数值通过、物理验证；Windows/Intel Mac/6.3/GUI 保持 UNVERIFIED。

## 7. 停止条件

按 Goal §11：成功 = `G3_MAC_EXECUTABLE_SCOPE_PASS`（R01–R05 确定缺口修复且有回归、R04 语义清楚、R06 各项闭环、W13–W16 可执行必需范围有可调用实现/权限/负向测试/实机证据、T019 链与新 Worker 重新打开通过、受影响回归通过、文档/证据/清理完成、普通非 force 同步并核对远端 SHA）；否则按受阻结束记录 `IMPLEMENTED_WITH_BLOCKED_ACCEPTANCE` / `BLOCKED`。**停止，不进入 W17。**
