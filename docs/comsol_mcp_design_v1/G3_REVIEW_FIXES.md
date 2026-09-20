# G3_REVIEW_FIXES.md — R01–R06 定向补修记录

依据：`docs/comsol_mcp_design_v1/NEXT_GOAL_MAC_G3.md` §3、§10.1–§10.3、§11。
审查基线：`1c6a5e982742b6c1a92054409383fbfbbc56658e`（本地分支 `codex/mac-g2-accepted-delivery`）。
环境：macOS 27.0 arm64；COMSOL 6.4.0.293（`/Applications/COMSOL64`）；Corretto 11；验收解释器
`…/comsol-mcp-server/.venv/bin/python`（CPython 3.12，cwd 指向本仓库）。
验收摘要：`evidence/phase4_acceptance.json`；运行证据根：`evidence/phase4/runs/20260920T130620Z-g3-live/`。

本文件记录的是**控制/协议层缺口已修复且有回归**，不等于引擎/实机验收通过。实机侧结论按
PASS / FAIL / BLOCKED / NOT_RUN 逐项列出；本轮 live 验收未完成（见 §实机状态汇总）。

## 0. 计数与证据来源

| 项 | 回归文件 | 收集用例数（实测） | 依据命令 |
|---|---|---|---|
| R01 | `tests/test_g3_r01_expression.py` | **10** | `pytest tests/test_g3_r01_expression.py --collect-only -q -p no:randomly` |
| R02 | `tests/test_g3_r02_discovery.py` | **13** | 同上（R02 文件） |
| R03 | `tests/test_g3_r03_indexed.py` | **21** | 同上（R03 文件） |
| R04 | `tests/test_g3_r04_invariants.py` | **23** | 同上（R04 文件） |
| R05 | `tests/test_g3_r05_store.py` | **12** | 同上（R05 文件） |
| R06 | `tests/test_g3_r06_docs_query.py` | **5** | 同上（R06 文件） |
| 合计 | 6 文件 | **84** | 2026-09-20 本机实测 |

全量软件回归（本轮核验）：`pytest tests/ -q -p no:randomly` → **1359 passed, 1 skipped**
（1 skipped 为既有 Windows-only 跳过项）。驱动侧协议/线格式回归另见
`tests/test_g3_phase4_wire_replay.py`（12 测）、`tests/test_g3_phase4_driver.py`（10 测）、
`tests/test_g3_phase4_reconcile_and_probes.py`（33 测）。

下列每一项的“问题”一栏是修复前的**源码行为**（Goal §3 的审查结论），不是对 COMSOL 行为的断言。

## R01 — 表达式/字符串/数值视图回读一致性

**问题（修复前）**
- `_g2_contract.validate_typed_value` 允许 expected `string` 接收 `kind=expression`；
  `_g2_engine._typed_readback_matches` 却要求 requested/returned `kind` 完全相同。同一表达式文本写入
  String 存储属性后回读为 `string`，被判 mismatch → 进入 UNKNOWN。
- 数值 getter（`Double`/`DoubleRowMatrix`）不提供表达式视图，却可能被当作表达式回读依据。

**修复**
- 契约层新增显式「语义种类 ↔ COMSOL 存储/回读」映射：`_g2_engine._TEXTUAL_KINDS = {string, expression}`，
  比较记录返回 rule `exact_text_expression_string_mapping`（跨种类仅此一种，且要求文本 **逐字节相等**；
  COMSOL 规范化后的文本仍报不匹配，不静默接受）。原始表达式文本保留，不转浮点。
- `unit` 字段降级为**文本值元数据**：仅允许伴随 `expression`/`string`，本层从不做单位换算
  （`PROPERTY_TYPE_MISMATCH`：`unit may only accompany expression/string typed values`）。
- `kind=expression` 写入数值类属性在**写前**拒绝（`PROPERTY_TYPE_MISMATCH`），不猜测数值视图。
- 数值比较容差显式：`numeric_tolerance` 参数；未提供即精确比较，不存在隐式容差。
- 驱动侧引用缺键修复：R01 live 用例读取回读记录时改为防御式取值（`comparison` / `applied` 缺失时
  记为不匹配并给出原因），缺键不会被当作匹配、也不会让用例崩溃；`tests/test_g3_phase4_wire_replay.py`
  直接从驱动源码提取真实 payload 站点（`SITES`），任何被 gate 的 payload 站点缺失即失败。

**验证**（10 测）
同文本 expression→String 验证、表达式数组/矩阵、带单位表达式文本保留、引擎规范化文本不静默接受、
表达式写入数值属性写前拒绝、数值属性带 `unit` 拒绝且不换算、非有限文本严格拒绝、`numeric_tolerance`
必须显式、形状不匹配仍拒绝、精确字符串写命中 `exact` 规则。

**实机状态（driver4，2026-09-20T14:30Z 结束的直播运行）— BLOCKED**
`expression_string_same_text_verified` **PASS**（live，真实回读同文本）、
`nonfinite_text_strictly_rejected` **PASS**（live）；`expression_array_or_matrix_verified`、
`unit_expression_text_preserved` **NOT_RUN**（绑定模型未暴露相应属性）；
`incompatible_kind_or_unit_rejected_before_write`、`non_matching_readback_not_silently_accepted`
**BLOCKED**（负向探针以 `EXECUTION_STATE_UNKNOWN` 被拒，无法确定该写入是否到达引擎）。
R01_LIVE 整体 BLOCKED 与 R01 契约无关，是 managed-revision 记账问题（见 §实机状态汇总）。

## R02 — 完整节点发现、分页、错误与预算

**问题（修复前）**：`children_node` 固定集合不含 material、variable、func、selection、multiphysics、
pair、cpl、coordSystem、propertyGroup 等；`find_nodes` 每层只取 `limit=500` 一页、忽略 `next_cursor`、
不报告搜索不完整；广义异常被吞掉（既有 501 个子节点找不到、已存在 mat1 未列出）。

**修复**
- 集合发现改为「allowlist + 逐集合能力探测」：只有引擎明确回答“该节点不支持该集合”才可省略；
  连接断开/引擎异常/权限错误进入 `errors[]`，绝不静默转成空列表（Worker 侧新增
  `children`/`walk` 命令与 `COLLECTION_PROBE_FAILED`、`CHILD_RESOLVE_FAILED`、`TAGS_PROBE_FAILED`、
  `METHOD_NOT_ALLOWED`、`STALE_WORKER_HANDLE` 错误码）。
- 每层继续分页，返回 `complete / truncated / next_cursor / errors`；游标绑定
  `generation + model_revision + 查询哈希`，陈旧/非法游标显式拒绝（不从第 0 项重来）。
- 预算可配置：`max_nodes` / `max_rpc` / `max_seconds`，截止时返回可恢复游标；批量 `walk` 减少 RPC。
- `ACCESSOR_METHODS` 扩展为 29 项（新增 `func`、`multiphysics`、`pair`、`cpl`、`coordSystem`、
  `propertyGroup`、`extraDim`、`probe` 等），名称与签名逐个经 javap/KB 核实。

**验证**（13 测）新增集合可见、>500 子节点分页、wp3 内层几何递归、预算截止返回可恢复游标、
中断不伪报 NOT_FOUND、失效/非法游标拒绝、游标绑定查询与 epoch、兄弟节点保留、真实 RPC 调用量记录。

**实机状态**：**NOT_RUN**（本轮没有 R02 专用 live 用例；驱动用例清单中亦无 R02 项）。
R02 的实机回读仍 UNVERIFIED。

## R03 — indexed/keyed 写入必须有真实校验

**问题（修复前）**：`property_index_set` 读取整体属性却不比较目标元素与非目标项；`property_entry_set`
检查 getter 名存在后调用 setEntry 并**返回请求值**，从不调用 getter。

**修复（权威回读）**
- `setIndex`：写入后按版本适配的索引语义**回读目标元素/行**并与请求值比较；同时比较**非目标元素**是否
  保持不变（变化 → partial/UNKNOWN）；引擎自动扩容与 shape 变化显式记录，不静默。
- `setEntry`：以真实 key/entry API 回读（Worker 白名单新增 `getEntryKeys`、`getEntryKeyIndex`）；
  缺少权威回读路径或元数据未知且无显式 `java_signature` → **写前拒绝**；受控未验证模式单独标注，
  永不声明 VERIFIED。
- 普通 `set`/`setIndex`/`setEntry` 统一产生 `applied` / `failed` / `not_executed`、`readback`、
  `verification_scope` 与部分失败信息；managed 信封下“写入成功但回读不符”记为错误而非 UNKNOWN。
- 不替换任何已验证 setter。

**验证**（21 测）向量元素（目标校验 + 非目标保留）、矩阵 cell/行、keyed entry、错误 index/key、
setter no-op 检出、写错位置检出、非目标项变化检出、空/单元素、自动扩容显式、字符串元素复用 R01 映射、
无权威回读写前拒绝、受控未验证模式不声明 VERIFIED、managed 信封一致。

**实机状态（driver4）— BLOCKED**：`wrong_index_rejected_without_write`、`setter_noop_or_normalized_value_detected`
**PASS**（live）；`vector_element_readback` **BLOCKED**（索引写入未从独立回读得到验证）、
`non_target_items_unchanged` **BLOCKED**；矩阵/keyed/空单元素三项 **NOT_RUN**（绑定模型未暴露相应属性，
keyed 属性无法发现或创建）。R03_LIVE 整体 BLOCKED 同样来自 managed-revision 记账。

## R04 — “动作执行完” ≠ “invariants 成立”

**问题（修复前）**：`run_transaction` 保存 invariants 但从不执行；`_verify_transaction_record` 只是有限的
持久记录字段检查，不是当前模型的实时验收。

**修复（类型化 invariants + 三态）**
- 可执行 invariant 词表（仅数据、无自由谓词/代码钩子）：`node_exists`、`node_type`、
  `property_equals`、`property_tolerance`、`selection_non_empty`、`selection_dimension`。
- **双状态分离**：`execution_status`（`NOT_EXECUTED` / `FAILED` / `PARTIAL` / `UNKNOWN` / 成功）
  与 `verification_status`（`NOT_RUN` / `VERIFIED` / `PARTIAL` / `FAILED`）；每条检查自身三态
  `PASS` / `FAIL` / `NOT_RUN`，并标注 scope（记录校验 vs 实时模型读取）。
- 写前 preflight：required 且不支持/格式非法 → 写前拒绝（`INVALID_INVARIANT` / `INVARIANT_UNSUPPORTED`）；
  optional 不支持 → `NOT_RUN`，不计通过；无 invariants → `NOT_RUN`（非 VERIFIED）。
- 执行成功但 required invariant 失败 → 总体验收非 PASS（`FAILED`/`PARTIAL`），且**仍返回**已产生的
  修改、checkpoint 与恢复选项，不伪装原子回滚。
- 验证绑定 transaction 的 `model_ref`/`revision`：跨模型或旧 revision 的记录在 `transaction.verify`
  被拒；记录检查通过但实时属性已不同 → 以实时读取为准。
- 明确不提前实现 W20 物理验证系统。

**验证**（23 测）required 不支持/非法写前拒绝、optional 不支持 `NOT_RUN` 且动作照常执行、
无 invariants 记 `NOT_RUN`、required 全通过为 `VERIFIED`、required 实时失败为非成功但保留 applied、
动作变更后被重新读取而非回显、`node_exists`/`node_type`/容差边界/非数值拒绝/选区非空与维度、
required 求值错误为 FAIL 而 optional 为 `NOT_RUN`、跨模型记录拒绝、scope 标注、记录过期检查通过但
实时重读证明变化、损坏 store 阻断持久写入。

**实机状态（driver4）— BLOCKED**：静态/协议子项 **PASS**（unsupported invariant 写前拒绝、
事务操作可执行）；live 子项因 `REVISION_CONFLICT`（事务被拒）**BLOCKED**。

## R05 — 恢复记录损坏不能变成“没有记录”

**问题（修复前）**：`TransactionStore.__init__` 对 `OSError`/`JSONDecodeError` 回退为空字典，
损坏 JSON 被加载成空记录且不报错；下一次 `put` 会覆盖损坏历史。

**修复（STORE_CORRUPT + 显式恢复）**
- 区分「文件不存在（首次初始化）」与「已存在但不可读/损坏」：后者抛 `STORE_CORRUPT`（附带
  path + 摘要 + sha256），保留原文件、阻断依赖该记录的读取与写入；带外损坏在下一次 `put` 时
  同样阻断而非覆盖。
- 显式恢复流程 `recover(destination_dir=…)`：审计并保留失败副本（`<name>.corrupt-<时间戳>`）、
  记录原 hash 与阻断错误；对健康 store 调用 `recover` 被拒；源文件消失时如实报告而不伪造副本。
- 发布/写入复用既有原子写方式；写入不丢弃另一实例已写入的记录；不遗留临时文件。
- 事务与 checkpoint 的**权威记录统一**到既有 `operations.sqlite3`（迁移后事务记录引用可解析回
  operation store），不再有两套互相矛盾的真相。
- `checkpoint.diff` 仍显式“不支持”；不以 hash 差异冒充语义 diff。

**验证**（12 测）缺失文件正常初始化与跨重启可查、损坏/不可读阻断且原文件 hash 不变、
带外损坏阻断下一次 put、并发实例记录不丢、无临时文件残留、`recover` 审计与 hash、源消失不伪造、
健康 store 拒绝 recover、事务记录引用解析。

**实机状态**：**NOT_RUN**（R05 是本地持久化契约，本轮无实机用例；软件范围 12 测通过）。

## R06 — 与 G3 直接相关的收口

1. **统一执行路径**：96 个 G3 领域操作全部经 `operation_call`/`registry_call` 进入既有
   `ManagedBackend` → ticket / isolation / 幂等 / queue / event context / job 门禁；未新建“方便版”服务
   （`tests/test_g3_wiring.py` 5 测锁定：每个已发布操作可执行、效果可用、隔离集合 = 非 READ 集合）。
2. **旧判定不变**：浅指纹与原生 `ModelChangedHandler` 回调 FAIL 保持原 verdict，未借 G3 重开。
3. **平台差异留在 adapter 层**：Windows 进程身份/classpath/句柄 flush 与 stdio 生命周期代码保留，
   未新增散落的 POSIX 专用命令。
4. **docs 查询契约（node_type 不当 product）**：`node_type` 是**节点/特征类型**的内容维度，
   与 `product`/版本各自独立语义；此前把 `node_type` 传给 `product` 过滤会把“有文档”错筛为不可用。
   现 `docs.error_search` 与 `docs.search` 各自接收 `node_type`，在标题/内容上做独立过滤，
   `docs.examples` 不接收该字段。回归 5 测：node_type 与 product/version 隔离、node_type 匹配标题与内容
   （非 product）、backend `error_search` 使用 node_type、`docs` 操作转发文档化维度、声明的 schema 中
   `product` 与 `node_type` 保持分离。
5. **README/CLAUDE/AGENTS**：已按新 registry 更新计数与入口（见各文件“当前阶段”段），
   历史阶段“禁入 W13”保留为历史说明。
6. **大模型 checkpoint 成本与完整 GUI/solution 恢复**：仍为后续范围，本轮不虚构全模型无副作用结论。

**实机状态（driver4）**：`GUARD_T035`（docs 路径/凭据保护）**PASS** live；`GUARD_T038`（错误传播、
结构化结果、IsError 一致）**PASS** protocol；`GUARD_T005`（用户节点保留、无效表达式不删节点、
临时节点清理）**PASS** live；`GUARD_T010`（幂等）**NOT_RUN**；`GUARD_T033`（求值策略/临时节点归属）
**FAIL**（产品缺口：`operation_describe` 未发布 evaluate 策略、连续 ephemeral 求值未全部记录、
未报告自身临时节点归属）。

## 实机状态汇总（本轮真实计数）

live 运行目录：`evidence/phase4/runs/20260920T130620Z-g3-live/`（driver / driver2 / driver3 / driver4）。

| 运行 | 用例 PASS/FAIL/BLOCKED/NOT_RUN | 子用例 PASS/FAIL/BLOCKED/NOT_RUN | 主要阻塞 |
|---|---|---|---|
| driver | 2 / 7 / 12 / 2 | 47 / 15 / 18 / 68 | 未 reconcile 的 UNKNOWN 作业关闭引擎门禁 |
| driver2 | 3 / 18 / 1 / 1 | 55 / 24 / 9 / 62 | 用例级问题（含 REVISION_CONFLICT 传播） |
| driver3 | 2 / 3 / 17 / 1 | 49 / 6 / 36 / 60 | 门禁再次关闭；驱动无法发现未解析作业 id |
| driver4 | 3 / 7 / 12 / 1 | 68 / 8 / 24 / 50 | revision 采纳长尾（external-change / stale `expected_revision`）+ 少量引擎级拒绝 |

（driver4 的运行内 `summary.md` 与 `index.json` 自报 `PASS 3 / FAIL 7 / BLOCKED 12 / NOT_RUN 1`；
`evidence/phase4_acceptance.json` 记为 `FAIL 6 / BLOCKED 13`，两者相差一例，以运行目录为准。）

- R01/R03/R04 live：受同一 revision 记账阻塞（R01 的同文本回读与 R03 的错误 index 拒绝已 live PASS）。
- GUARD：T038 / T035 / T005 live PASS；T010 NOT_RUN；T033 FAIL（产品缺口，未修复）。
- 未完成：W13–W16 live 验收、T042 许可证探针（产品侧 payload 身份不匹配）、T033 产品缺口、
  reopen-check（新 Worker 重开）未运行、最终验收运行未到、未同步远端。
- 因此本轮**不构成** `G3_MAC_EXECUTABLE_SCOPE_PASS`；当前记录为
  `IMPLEMENTED_WITH_BLOCKED_ACCEPTANCE`。
