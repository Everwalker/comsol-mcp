# G3.2 / W17 实施与复核计划（G3_2_W17_PLAN.md）

基线快照：`Everwalker/comsol-mcp@328202cb8b30df2f0368b4bc06c70e3c91b8bce1`  
Git Tree：`7c8735a11fdd497bbd2e32d1a3c6809945c36f4c`  
依据规范：`START_HERE.md`、`REVIEW.md`、`NEXT_GOAL.md`、`04_IMPLEMENTATION_PLAN.md`

---

## 1. 真实运行环境与事实冻结

### 1.1 平台与工具链
- **操作系统**：macOS 27.0 (arm64 Apple Silicon)
- **COMSOL 引擎**：COMSOL Multiphysics 6.4 (Build 6.4.0.293) 安装于 `/Applications/COMSOL64/Multiphysics`
- **JDK 运行时**：Amazon Corretto 11.0.31 (JDK 11) 于 `/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home`
- **Python 运行环境**：隔离虚拟环境 `.venv` (CPython 3.13.14, pytest 9.1.1, anyio 4.15.1)
- **COMSOL 进程与生命周期纪律**：
  - 系统中存在历史运行的 COMSOL Server 实例（PID 5014，端口 56389）。
  - **重要纪律**：严禁发送 kill / terminate 信号给历史或共享 Server 实例；严禁修改其共享配置或端口；所有针对测试的实机操作均使用受控独立的 Worker 或独立模型标签，严格遵守 Task-owned vs Shared 生命周期管理。

### 1.2 事实边界与历史账本
- 历史 `phase4_1_acceptance.json` 保持字节级不变，保留作为基线参考证据。
- 新增 `evidence/phase4_2_acceptance.json` 作为本次复核与补强验收的正式账本。
- 原 G3.1 账本虽声称 `G3_1_MAC_EXECUTABLE_SCOPE_PASS`，但审查确认存在 F01–F07 七大缺口，不能作为无条件进入 G4 的理由。必须先在 Gate A 收口全部 P0 缺陷与当前 Mac 可执行 required 范围，方可推进 Gate B (W17)。

---

## 2. 问题清单与修复方案（Gate A）

| ID | 级别 | 问题描述 | 关键文件与根因 | 修复策略 | 回归与验收要求 |
|---|---|---|---|---|---|
| **F01** | P0 | M1 中 T006 留有 `IMPLEMENTATION_GAP` 却宣称全阶段 PASS | `tools/phase4_run_mcp.py`，账本中分类解释被误当作豁免理由 | 补全 T006 用例语义：全局与组件双变量组、依赖表达式、修改其一、读回并合法求值 | T006 实机/软件判定双 PASS，required 集合无 gap |
| **F02** | P0 | 重开验收仅验证常数 300K/293.15K，产物路径与修改后状态错位 | `evidence/phase4/_reopen_check/*/index.json`，未比对存储解场值 | 加载同 SHA 产物，不重算直接读解，对比非平凡空间/时间多点；负向对照（清解、篡改）报错 | A/B/C 三条链均完成真重开比对，负向对照组灵敏拦截 |
| **F03** | P0 | 数据集绑定报错 `__call__() is not exposed`，摘要忽视阻断 | `_model_ops.py:347` 误用 `__call__` 探测 RemoteJava | 改为标准 Java collection `get(tag)`；输出权威 binding；未确认 solution 时报 incomplete | 权威 solution 读回正常，拒绝时明确 `binding_incomplete` |
| **F04** | P0 | 写入见证仅凭方法名前缀，误判 named/all/label/active 等 | `_domain_outcome.py:183-203` 仅查前缀与静态名单 | 在真实派发边界捕获 command/receiver/method/args，区分 getter/setter 重载；未知一律保守标记写 | 纯 getter 不报写，setter 异常绝不报未派发，各入口统一判定 |
| **F05** | P0 | 外层 False/缺失覆盖内层危险 True 信号 | `_domain_outcome.py:718-723` 字典更新覆盖 | 保守状态升级机制（三态逻辑），危险信号（unknown, partial, cleanup_failed）只升级不降级 | 故障注入单测：内层 unknown=true 外层 false 判定 isError=True |
| **F06** | P1 | 组件变量验收缺少明确评估上下文 | `tools/phase4_run_mcp.py:5420` 直接查局部变量 | 建立显式几何/选区/数据集上下文，或走合法组件评估路由；全局变量建立真实依赖表达式 | 全局与组件变量分别在合法作用域中成功求值 |
| **F07** | P1 | 环境配置与文档包含旧机器硬编码 | `pyproject.toml`、`AGENTS.md` 等 | 规范平台适配层，更新依赖锁定文档 `DEPENDENCIES.md`，清理非通用假定 | 外部干净目录测试 import 与 registry 正常 |

---

## 3. Gate B：W17 结果系统详细设计

完成 Gate A 之后，启动 W17 结果系统实现，止于 W17，绝不进入 W18。

### 3.1 数据集与 SolutionSpec
- 支持 Dataset 的完整生命周期与树状结构探索。
- 实现结构化 `SolutionSpec`：
  - `dataset`: 明确数据集名称
  - `solution`: 对应求解器解标签
  - `inner_index` / `outer_index`: 参数化扫描解索引
  - `time` / `frequency`: 显式物理时间步或频点
  - 严格校验索引有效性，越界或歧义时前置拒绝，不得静默取首尾标量。

### 3.2 结果形状与复数场
- 统一数据返回契约：
  - 默认 `preserve` 模式：完整保留实部与虚部（`real`, `imag`），并给出 `shape`。
  - 显式分量提取：`real`、`imag`、`abs`、`phase`（声明弧度/角度约定与分支割线）。
  - 特殊值处理：NaN / Inf 遵循合法 JSON 表达或显式错误说明。
- 大数据分块与产物机制（T049）：超出 inline 限制时，写入临时结果文件产物，返回 `artifact_ref`、SHA256、shape 及分块迭代器。

### 3.3 空间测度与统计评估
- 统一全局、点、线、面、体积分与平均计算接口。
- 区分 1D/2D/3D/轴对称空间维数；轴对称问题严格仅应用一次 $2\pi r$ 权重。
- 实现 `max`, `min`, `mean`, `integral`, `rms`, `std` 算子。

### 3.4 探针与临时节点管理
- 规范结果节点与探针（Probe）的生命周期。
- 临时求值节点具有唯一 UUID 标识，操作结束确保可靠清理；若清理失败，维持 `cleanup_failed` 并升级状态为 `UNKNOWN`。

### 3.5 W17 验收测试集
- 执行并验证：`T013`（多维全时间轴对称）、`T014`（复场分量完整性）、`T021`（内外参数解索引）、`T049`（大数据量分块与产物），以及相关联的 `T005`、`T015`、`T033`、`T035`、`T038`。

---

## 4. 执行路线与推进步骤

1. **Step 1 (A0)**：运行逐文件审查，更新 `audit/semantic_review.json`，确保全仓 3571 个文件逐路径具备状态。
2. **Step 2 (Gate A1)**：修改 `_domain_outcome.py` 和 `_java_worker.py`，实现 F04 写入见证与 F05 信号合并；编写针对性单测并通过。
3. **Step 3 (Gate A2)**：修复 `_model_ops.py` 数据集绑定 F03；重构 `_case_w13_t006` 补全 F01/F06；执行 A/B/C 三链非平凡存储解真重开 F02（含负向对照）。
4. **Step 4 (Gate A3)**：复核全部 G3 required 子项，生成 `evidence/phase4_2_acceptance.json`。
5. **Step 5 (Gate B)**：开发 W17 核心模块与适配器，完成 W17 验收测试集，产出 W17 独立账本。
6. **Step 6 (交付)**：更新 `DEPENDENCIES.md`，运行全套回归，重新打包 `COMSOL_MCP_READY_328202c.zip`，验证完整性，停止于 W17。
