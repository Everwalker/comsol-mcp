# G3.2 审查发现与根因明细（G3_2_FINDINGS.md）

基线快照：`Everwalker/comsol-mcp@328202cb8b30df2f0368b4bc06c70e3c91b8bce1`  
Git Tree：`7c8735a11fdd497bbd2e32d1a3c6809945c36f4c`  
依据规范：`REVIEW.md`、`NEXT_GOAL.md`

---

## 发现与修复全景

| 编号 | 优先级 | 影响域 | 核心缺陷 | 根因分类 | 修复状态 |
|---|---|---|---|---|---|
| **F01** | P0 | 阶段账本 / 覆盖率 | `phase4_1_acceptance.json` 包含 `W13_T006 = FAIL / IMPLEMENTATION_GAP`，却给出 `G3_1_MAC_EXECUTABLE_SCOPE_PASS` | 账本判定逻辑缺陷 | **已闭环**：T006 全局/组件变量作用域修复，在 `phase4_2_acceptance.json` 中作为 required 项验证通过 |
| **F02** | P0 | 模型重开 / 数值证明 | A/B/C 三链重开记录中，`representative_values` 仅求值常数 `300[K]` / `293.15[K]`，未核验存储解；C 链指向原始未修改 fixture | 验证设计不足与路径错位 | **已闭环**：A/B/C 三链非平凡存储解读取对比与 4 组负向对照全部通过（`test_g3_gate_a2_f02_reopen.py` 7/7 PASS） |
| **F03** | P0 | 数据集绑定 / 引擎接口 | `_model_ops.py:347` 误用 `__call__` 探测 RemoteJava collection，报 `__call__() is not exposed`，导致 dataset 与 solution 绑定回读失败 | API 契约调用错误 | **已闭环**：改为标准 `collection.get(tag)`，权威 solution/dataset 绑定正常（`test_g3_r03_indexed.py` PASS） |
| **F04** | P0 | 写入见证 / 安全门禁 | `_domain_outcome.py:is_mutation_method` 仅凭名字前缀与粗粒度列表，未能区分带参 setter 与纯 getter 重载（如 `label()` vs `label(String)`） | 写入见证边界不严密 | **已闭环**：实现 `DispatchWitness` 接收者/签名/参数捕获与细粒度副作用表，纯 getter 与 setter 分离（11/11 PASS） |
| **F05** | P0 | 错误状态合并 / 状态机 | `_domain_outcome.py:classify_envelope` 外层字典合并用外层 False/缺失直接覆盖内层危险 True 信号（unknown, partial, cleanup_failed） | 状态机合并策略缺陷 | **已闭环**：实现三态保守升级机制（True > None > False），故障注入验证（11/11 PASS） |
| **F06** | P1 | 变量求值 / 作用域 | T006 用例未为组件变量建立有效求解/几何上下文，依赖全局 `param.evaluate` 导致报错；全局测试退化为 `1+1` 且未测“修改其一” | 驱动用例语义不全 | **已闭环**：建立真实全局与组件双变量组、依赖表达式及修改逻辑，消除实现缺口 |
| **F07** | P1 | 平台复现性 / 元数据 | `pyproject.toml` 版本与依赖未严格锁定，文档中残存特定本机临时路径假设 | 构建与元数据不严密 | **已闭环**：生成 `DEPENDENCIES.md` 固化版本锁与运行依赖，清除死链接与非受控路径假定 |


---

## 详细根因与修复规范

### F01 / P0：阶段 PASS 与原完成条件冲突
- **证据位置**：`evidence/phase4_1_acceptance.json` 行 5 与行 88。
- **根因分析**：分类标签 `IMPLEMENTATION_GAP` 仅仅解释了失败原因，并没有消除实现缺失。原 Goal 明确规定当前可用环境的 required 能力不能留实现缺口宣称通过。
- **修复方案**：不得修改历史 `phase4_1_acceptance.json`。新增 `evidence/phase4_2_acceptance.json`，只有在 T006 真正通过且 required 子项完整闭环后，方可评定当前环境 PASS。

### F02 / P0：重开数值验收实际上是常数求值
- **证据位置**：
  - `evidence/phase4/_reopen_check/20260921T073939340867Z/index.json`
  - `evidence/phase4/_reopen_check/20260921T074644580625Z/index.json`
  - `evidence/phase4/_reopen_check/20260921T081111862152Z/index.json`
- **根因分析**：
  - A/B 链仅对 `300[K]` 表达式做 EvalGlobal 求值（得到 300.0），无法证明求解得到的温度场实际保存在 MPH 中。
  - C 链打开的是初始 fixture `chain_c_fixture/chain_c_user_style.mph`，而非继续修改后保存的新 MPH。
- **修复方案**：
  - 记录保存文件的真实 SHA256；
  - 启动独立新 Worker 载入该 SHA 文件；
  - **绝不重新求解**，直接读取存储解：A 链比对温度梯度 3 点及热流，B 链比对 3 个真实时间步（含非零时刻）及 3 个非边界点，C 链比对修改后的边界/求解器/衍生值；
  - 构造负向测试（清空解、错数据集、错时间点等必须报错）。

### F03 / P0：数据集绑定报错仍被摘要忽略
- **证据位置**：`comsol_mcp/_model_ops.py` 行 347：
  ```python
  node, node_error = _engine_probe(collection, "__call__", tag)
  ```
- **根因分析**：RemoteJava 是 Python 包装器，并没有实现 `__call__`，因此返回 `__call__() is not exposed by this engine handle`。虽然在后续尝试中可能通过降级路径求值，但失去了权威 dataset 到 solution 的绑定校验。
- **修复方案**：
  - 改用标准公共方法：`collection.get(tag)` 或 `results.dataset(tag)`；
  - 读取 `getType()` 与 `getString("solution")`；
  - 无法读取权威 solution 时，返回明确的 `binding_incomplete`，绝不静默默认第一个数据集。

### F04 / P0：写入见证仅凭方法名前缀
- **证据位置**：`comsol_mcp/_domain_outcome.py` 行 183–203：
  ```python
  MUTATION_METHOD_NAMES = frozenset({"all", "inherit", "geom", "named", "selection", ...})
  ...
  return method.startswith(MUTATION_METHOD_PREFIXES)
  ```
- **根因分析**：
  - `named(...)`, `all(...)`, `selection.geom(...)`, `label(String)`, `active(boolean)` 等可能改变模型状态的方法，因为不在前缀名单中或在豁免集合中，被误记为只读；
  - 见证器未接收接收者类型或参数形态。
- **修复方案**：
  - 在 `RemoteJava._call` 及 `PersistentJavaWorker` 派发边界捕获完整命令、接收者类型、方法名及参数；
  - 明确纯 getter 与 setter 重载规则（无参 `label()` 为读，有参 `label(String)` 为写；`geom()` 访问为读，`selection.geom(...)` 为写）；
  - 未知方法、动态反射调用一律保守判定为写（MUTATION）；
  - 只有静态校验未进入写边界且实际见证证实无修改时，才允许标为 `pre_dispatch clean refusal`。

### F05 / P0：外层 False 可覆盖内层危险 True
- **证据位置**：`comsol_mcp/_domain_outcome.py` 行 718–723：
  ```python
  merged: dict[str, Any] = {key: detail[key] for key in CONTRACT_KEYS if key in detail}
  merged.update({key: envelope[key] for key in CONTRACT_KEYS if key in envelope})
  for key in ("execution_state_unknown", "partial_change", "cleanup_failed"):
      if key in envelope:
          merged[key] = envelope[key]
  ```
- **根因分析**：若 `detail`（内层数据）报告 `execution_state_unknown=True`，而外层 `envelope` 包含 `execution_state_unknown=False`，简单的字典更新或循环赋值会导致外层 False 冲掉内层 True。
- **修复方案**：
  - 采用保守的 OR 逻辑升级危险标志：`merged[k] = detail.get(k) is True or envelope.get(k) is True`；
  - 区分缺失（None）、明确 False、明确 True 三态；
  - 保留错误原因与来源链路。

### F06 / P1：组件变量验收混合了夹具缺陷和实现缺口
- **证据位置**：`tools/phase4_run_mcp.py` 行 5380–5475。
- **根因分析**：
  - 用例直接用 `evaluate_expressions` 评估组件变量 `q1` / `q2`，而模型处于未求解状态，求值回退到全局 `model.param().evaluate` 失败；
  - 全局作用域测试仅用 `1+1` 充当，没有真正创建全局变量组，也没有执行原规范要求的“在同一变量组设两个变量、修改其中一个”。
- **修复方案**：
  - 真正创建全局变量组（包含相互依赖表达式，如 `g1=10`, `g2=g1*2`），修改其中一个（`g1=20`），核验求值结果；
  - 为组件变量组提供明确的组件/选区/求解数据集上下文进行合法评估；
  - 彻底关闭 `IMPLEMENTATION_GAP`。

### F07 / P1：部署和状态恢复仍依赖本机上下文
- **证据位置**：`pyproject.toml`、`AGENTS.md` 等。
- **根因分析**：依赖未精确锁定，历史文档包含特定开发环境的死链接或假定。
- **修复方案**：
  - 编写 `DEPENDENCIES.md` 明确声明运行时与锁定版本；
  - 确保 clean install / import 在外部目录正常执行；
  - 项目内文档与真实代码契约保持一致。
