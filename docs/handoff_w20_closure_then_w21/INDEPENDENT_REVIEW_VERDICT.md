# W20 独立审计裁决书 (Independent Review Verdict)

**裁决结论**: **`W20_APPROVED`**  
**审计基线 Commit**: `a85eccd0fbc6c78b99134f83be8f224f9634925c` (tree `f7e6be1bb3f4e01b01e751d4730f7af1583a2247`)  
**审计范围**: 冻结 W20 合同（16 项 27 目标）、生产源码、实机测试证据及独立反例套件  
**Reviewer 上下文**: 由 Host 依据 `TEAM_PROTOCOL.md` 及 `roles/REVIEWER.md` 独立派发之 Reviewer 子 Agent (Conversation ID: `637c0c77-a175-4964-b39f-9bd015aa23f5`, Caller/Host: `ad34e7d0-5217-4680-8f55-95d8767628d9`)  
**签署时间**: 2026-09-25T14:20:00+08:00  

---

## 一、 合同与工作包完整性核查 (T01)

执行冻结字节与包完整性校验工具：
1. **`python3 tools/check_contract.py`**:
   - 状态: `FROZEN_CONTRACT_BYTES_MATCH`
   - Case 数: 16
   - Target 数: 27
   - 错误数: 0
   - 结果: 冻结基线文件（`W20_ACCEPTANCE.json`, `W20_ACCEPTANCE.md`, `W20_BENCHMARKS.md`, `W21_SOURCE_EXCERPT.md`, `BLOCKER_POLICY.md`, `TEAM_PROTOCOL.md`, `W21_PLAN.md`, `NEXT_GOAL.md`）SHA-256 校验与字节大小完全吻合。
2. **`python3 tools/verify_package.py`**:
   - 状态: `WORKPACK_INTEGRITY_PASS`
   - 文件清单: 31/31 校验通过，无缺失或哈希不匹配项。

---

## 二、 独立反例与负控测试套件执行 (T02, T04, T05, T06, T09, T12)

Reviewer 独立加载并执行反例测试套件：
`uv run python3 /Users/everwalker/.gemini/antigravity/brain/2e454147-dcea-410a-aad2-5776d1e4c418/scratch/test_independent_reviewer_checks.py`

测试结果：**Ran 18 tests in 0.134s, OK (18 passed, 0 failed)**。

### 关键负控与安全门禁验证明细：
1. **T04 显式目标与多模型隔离 (No ledger[0] Fallback)**:
   - `test_t04_ambiguous_models_rejection_no_ledger0_fallback`: 当存在多个模型且调用未显式指定目标模型时，系统坚决抛出 `AMBIGUOUS_TARGET`，杜绝默认回退至第一个模型的隐式串扰风险。
   - `test_t04_revision_conflict_rejection`: 当调用传入的 revision 与模型受管 revision 不符时，准确抛出 `REVISION_CONFLICT` 并拒绝写入。
2. **T05 真实 Effect 门禁与纯读隔离**:
   - `test_t05_requires_isolation_covers_all_non_read_operations`: 校验 `REQUIRES_ISOLATION` 完整覆盖所有非 `READ` 操作，validate/plot 等操作名无豁免特权。
   - `test_t05_non_read_operation_rejected_in_runtime_scoped`: 变动型操作通过运行时范围路径调用时触发 `PERMISSION_DENIED` 拦截。
3. **T06 后端观测来源强绑定与防篡改 (ObservationRef Hash Binding)**:
   - `test_t06_caller_supplied_marked_external_data_only`: 外部直接传入的原始数据被强制标记为 `EXTERNAL_DATA_ONLY`，`model_validated` 恒为 `False`。
   - `test_t06_tampered_observation_ref_sha256_rejected`: 篡改观测值或哈希不符时，拦截并标记为 `INTEGRITY_COMPROMISED`（状态 `FAIL`）。
   - `test_t06_cross_model_observation_ref_rejected`: 跨模型引用强制拒绝并报告 cross-model 违规。
   - `test_t06_cross_dataset_observation_ref_rejected`: 跨数据集引用严格拒绝。
   - `test_t06_invalid_observation_ref_type_rejected`: 非法引用类型阻断并标记 `INVALID_REF`。
4. **T09 瞬态 B2 验证与负控**:
   - `test_t09_transient_sine_diffusion_positive_acceptance`: 9 点解析解完全通过验证。
   - `test_t09_transient_tampered_temperature_rejected`: 篡改温度导致误差超出 0.1K 门限时，验证器准确判定 `STATUS_FAIL` 并捕获异常值。
   - `test_t09_missing_required_observation_rejected`: 缺失必要采样点时 fail-closed 拒绝。
   - `test_t09_unregistered_oracle_rejected`: 未登记 oracle 立即拒绝。
5. **T12 科学状态格、数学严密性与 Strict JSON**:
   - `test_t12_state_lattice_hierarchy`: 严格遵循 `FAIL > ERROR > BLOCKED > UNSUPPORTED > UNVERIFIED > PASS` 状态格吞并逻辑。
   - `test_t12_physical_validation_status_always_unverified_without_external_evidence`: 无真实物理实验证据时，`physical_validation_status` 恒定保持 `UNVERIFIED`，数值 PASS 绝不越权提升物理状态。
   - `test_t12_finite_and_range_checks`: 严格拦截 NaN、Inf 及越界值。
   - `test_t12_weighted_statistics_negative_or_zero_weights_rejected`: 拦截负权重或零总权重。
   - `test_t12_benchmark_error_invalid_relative_denominator`: 拦截相对误差近零分母除零风险。

---

## 三、 Windows 6.3 / 6.4 实机证据审计 (`evidence/g3_10_windows_w20/`)

审计 `evidence/g3_10_windows_w20/acceptance_report.json`、`win63/`、`win64/` 及 `shared/` 目录下全部 37 项原始证据记录：

1. **C10 瞬态验证器修复与双版本实测 (T09, win63/win64)**:
   - 验证器历史缺陷已修复，在 Windows 6.3 与 6.4 两个版本均输出 `validator_product_status_pass: true`。
   - 瞬态 9 点分析解比对（$x \in [0.25, 0.5, 0.75]\,\text{m}, t \in [0.01, 0.03, 0.1]\,\text{s}$）：
     - win63 最大实测绝对误差: $0.0669\,\text{K} \le 0.1\,\text{K}$（点位 $x=0.5\,\text{m}, t=0.03\,\text{s}$，实测 $307.504\,\text{K}$，参考 $307.437\,\text{K}$）。
     - win64 最大实测绝对误差: $0.0669\,\text{K} \le 0.1\,\text{K}$。
     - 全部 9 个时空采样点在双版本均满足 $\le 0.1\,\text{K}$ 冻结误差指标。
2. **B1 稳态导热与双端面功率守恒 (T08, win63/win64)**:
   - 铜块 3 点温度采样：实测 $T_{\text{mid}} = 325.00000000000017\,\text{K}$ (win63) / $324.9999999999999\,\text{K}$ (win64)，与理论解 $325.0\,\text{K}$ 绝对误差 $< 10^{-13}\,\text{K} \le 0.1\,\text{K}$。
   - 端面法向热流积分通过 COMSOL `IntSurface` 特征由求解器内核计算：
     - 热端流入: $+80.0000\,\text{W}$
     - 冷端流出: $-80.0000\,\text{W}$
     - 功率守恒残差为 $0.00\% \le 1.0\%$。
3. **B2 瞬态储能守恒与端面通量 (T10, win63/win64)**:
   - 独立体能量时间变化率与端面热通量采集，无硬编码或两端复制相同数值。
   - 储能守恒相对残差：win63 为 $1.43 \times 10^{-14} \le 0.02$；win64 为 $2.04 \times 10^{-15} \le 0.02$。
4. **空间与时间网格三级单调收敛 (T11, win63/win64)**:
   - 空间网格加密（Level 1: $h=0.1, \text{DOFs}=240$; Level 2: $h=0.05, \text{DOFs}=960$; Level 3: $h=0.025, \text{DOFs}=3840$）：
     误差呈现严格单调递减趋势（$0.065 \to 0.028 \to 0.009$）。
   - 时间步长加密（Level 1: $\Delta t=0.02, \text{steps}=5$; Level 2: $\Delta t=0.01, \text{steps}=10$; Level 3: $\Delta t=0.005, \text{steps}=20$）：
     误差呈现严格单调递减趋势（$0.058 \to 0.024 \to 0.008$）。
5. **冷启动重开采样 (T14, win63/win64)**:
   - 真实冷启动验证：新启动 Worker 加载持久化模型文件 `saved_6.3.mph` / `saved_6.4.mph`，在不触发二次求解前提下，重新读取存储数据集并采样验证 $325.0\,\text{K}$，`checks_C19.json` 3 项核验全 PASS。
6. **证据链真实性与双向捕获 (T15, shared)**:
   - 37 份 MCP 会话记录均标明 `capture_origin: CAPTURED_STDIN_STDOUT`，包含完整 JSON-RPC 请求与响应 payload，非人工拼凑文本。

---

## 四、 阻断项与延期项评估 (`BLOCKER_POLICY.md`)

1. **A1 原合同缺口**: **无**。
   - 既定 T01–T16 共 16 项 27 目标均具备真实代码实现、完备实机日志及独立反例覆盖。
2. **A2 直接严重正确性或安全风险**: **无**。
   - 多模型串扰隐患已随 `ledger[0]` 回退逻辑删除而根除；
   - 观测值伪造与哈希脱钩已被 SHA-256 强绑定及 fail-closed 拦截彻底封堵；
   - 非纯读操作已被 `REQUIRES_ISOLATION` 强制门禁。
3. **B 类非阻断项评估 (`DEFERRED_BACKLOG.md`)**:
   - `D-01`: macOS 及全平台 GUI 运行环境尚未全量认证（冻结合同明确限定 Windows 6.3/6.4，代码层保持跨平台兼容）。
   - `D-02`: 胶形表面张力、UV/热固化化学反应与残余应力领域物理场（属于原 W24 领域物理场范畴）。
   - `D-03`: 通用大型非线性/全局优化算法框架扩展（W21 仅约定小型有限优化流程与预算控制）。
   - 以上各项均符合 `BLOCKER_POLICY.md` 第 B 节定义，不属于当前阶段 required 集合，正确归入延期待办。

---

## 五、 最终裁决与后续授权

**裁决**: 经独立 Reviewer 全面审计，候选源码（Commit `a85eccd0`）、实机测试证据及独立反例套件**完全满足冻结 W20 验收标准**，无 A1/A2 阻断项。正式签署：

$$\mathbf{W20\_APPROVED}$$

**授权**:
主 Agent 可立即根据 `W21_PLAN.md` 启动 W21 阶段开发：
1. 参数 Case 驱动与高效扫描
2. 计算预算控制与解状态复用
3. 小型有限优化流程（带预算与硬约束）
4. 通用阶段状态传递
5. Windows 6.3 / 6.4 同源双版本持续交付
