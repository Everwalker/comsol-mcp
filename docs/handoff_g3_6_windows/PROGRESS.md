# G3.6: Windows 原生双版本 (6.3 & 6.4) 独立适配与作业控制缺口 (D01–D09) 定向修复 (COMPLETED)

- **工作阶段**: G3.6 (W19 定向修补 + Windows 原生双版本 6.3/6.4 实机验收)
- **更新日期**: 2026-09-24
- **实机验收主机**: Windows 11 Enterprise (build 10.0.26200, AMD64, Python 3.12.10, IP 192.168.100.2)
- **跨平台保持主机**: Darwin arm64 (macOS 15+, Python 3.12.13)
- **恢复源基线**:
  - `PIN.json` Commit: `3835ab858a4e7a54fd3b1fb901dca04c9ee9ac19`
  - `PIN.json` Tree: `634389371381c179406e06c421a6e43a1f632322`
  - 逐文件完整性审计: 7,328 / 7,328 文件校验通过 (`SOURCE_HASH_VERIFIED`)
  - 包完整性校验: 25 / 25 校验项 `PASS`
- **权威结论**: **PASS** (30/30 用例全部通过)
  - 控制平面与软件测试: **15 CONTROL_PASS**, **1 SOFTWARE_PASS**
  - 原生 Windows 实机测试: **14 NATIVE_PASS_SCOPED** (COMSOL 6.3.0.290 & COMSOL 6.4.0.293 双版本实机引擎运行)
  - 缺陷与阻塞统计: **0 FAIL_IMPLEMENTATION**, **0 BLOCKED_ENVIRONMENT**
  - 交付物结论:
    - `WINDOWS_COMSOL_63_CORE_VERIFIED_SCOPED`
    - `WINDOWS_COMSOL_64_CORE_VERIFIED_SCOPED`
- **实机验收运行 ID**: `g3_6_live_acceptance_20260924T082700Z` (总耗时 201.16s, 历史基线运行 ID: `g3_6_acceptance_20260923T235632Z`)
- **证据存储路径**: `evidence/windows_dual_version/g3_6_live_acceptance_20260924T082700Z/`

---

## 1. 生产缺口定向修复 (D01–D09) 实现清单

| 编号 | 缺口名称 | 修复模块 | 核心改动与防护措施 | 验证状态 |
|---|---|---|---|---|
| **D01** | Windows 隔离证明不得绕过 | `_g2_isolation.py` | 抽象平台观测适配层；新增 Windows `netstat` 解析与 CIM/TCP 连接快照，严密校验回环监听（仅允许 `127.0.0.1` / `::1`，拒绝 `0.0.0.0` 混淆）；动态加载安装配置，彻底消除 macOS `/Applications/COMSOL64/...` 绝对路径绑死。 | **VERIFIED** (单测通过，Windows 实机回环监听校验通过) |
| **D02** | 取消请求不应把 UNKNOWN 写回 RUNNING | `_control_daemon.py` | 修复 `_cancel_job` 状态机：在 `UNKNOWN` 与 `RECONCILING` 状态下仅记录 `CancelRequested` 事件并标记 `cancel_requested=True`，严格保持未知语义，绝不写回 `RUNNING`；前置验证所有参数与授权，非法请求在触碰状态机前直接拒绝。 | **VERIFIED** (单测覆盖未知态与前置参数拦截) |
| **D03** | 终态、操作结果和竞态统一仲裁 | `_operation_store.py` | 统一 CAS 状态转移机制（`transition_status`）；实施**终态免疫原则**（`finish` 与 `update_job` 遇到 `SUCCEEDED` / `FAILED` / `CANCELLED` 等终态时严格拦截并记录 `LateResultRecorded` / `LateTransitionRejected` 事件）；同步更新 `operations.result` 与 `jobs` 状态信封。 | **VERIFIED** (排队取消获胜后晚到 callback 被可靠隔离) |
| **D04** | force-stop 绑定真正 runtime | `_control_daemon.py`, `_platform_process.py` | `_scoped_force_stop` 严密校验后端持久化 `ServerLease` / `RuntimeOwnership`，校对 `lease_id`、`server_pid` 与启动时间戳，彻底禁止仅凭前端布尔授权；废弃裸 `os.kill(pid, 9)`，采用平台级 `terminate_process_tree` 等待进程树实际退出及端口释放；未确认退出保留 `UNKNOWN`。 | **VERIFIED** (模拟租赁与越权终止拦截测试通过) |
| **D05** | 版本与编译缓存必须绑定 | `_java_worker.py`, `PersistentComsolWorker.java` | 重构编译缓存指纹算法：将 Worker Java 源码 SHA256、COMSOL 版本 build、官方 manifest 签名、JDK major/vendor、系统架构及编译参数深度哈希绑定；生成 `cache_receipt.json`；编译全链路强制 `-encoding UTF-8`；杜绝 6.3 与 6.4 编译产物混用。 | **VERIFIED** (双版本不同目录隔离与指纹隔离测试通过) |
| **D06** | Windows 生命周期、权限与文件边界 | `_platform_process.py`, `_security_os.py` | 新增 `validate_windows_path_security`：严厉拦截 ADS (Alternate Data Streams)、DOS 保留设备名（`CON`, `PRN`, `AUX`, `NUL` 等）、非托管 UNC 路径及尾部点/空格；新增 `is_process_in_job`；实现私有目录最小必要 DACL (`icacls` / POSIX 0700)。 | **VERIFIED** (安全边界负控测试全部 PASS) |
| **D07** | 真实负载与竞态测试套件 | `tests/test_g3_6_defects_d07_d09.py` | 新增正在计算时的状态响应高频轮询（50 次采样测算 p95 响应延迟 < 1s）；真实生产队列排队与启动 CAS 竞态测试；stdio 断开后的幂等恢复与冲突键校验；双运行时 ModelRef 错配写前拒绝。 | **VERIFIED** (多线程高并发及冲突保护全部 PASS) |
| **D08** | 取消能力精准披露 | `_control_daemon.py` | 修正将 API 单一状态外推为整个引擎无取消能力的片面陈述；在 `server_info`、`session_health` 与 `job_cancel` 中结构化分别披露取消路线：`native_cooperative_cancel`: UNSUPPORTED, `queued_cancel`: VERIFIED, `owned_process_termination`: VERIFIED。 | **VERIFIED** (三维取消能力信封披露通过) |
| **D09** | 源、部署、证据一一对应 | 全代码库与验收套件 | 所有修复在跨平台单源码库中落地，不派生 6.3 与 6.4 独立业务分支；回归保持 Mac 架构兼容；验收产物结构化落盘于 `evidence/windows_dual_version/<run_id>/`。 | **VERIFIED** (全回归 43/43 控制测试与 1867 源码单测 PASS，无破坏性变更) |

---

## 2. WD00–WD29 验收用例执行台账 (实机运行 ID: `g3_6_acceptance_20260923T235632Z`)

| 用例 ID | 标题 | 目标平台 | 证据级别 | 本轮执行结论 | 实际执行详情与依据 |
|---|---|---|---|---|---|
| **WD00** | 固定源恢复与完整性 | SHARED | STATIC | **CONTROL_PASS** | 验证 `PIN.json` commit `3835ab85` 与 tree `63438937`；25/25 包校验通过；篡改负测通过。 |
| **WD01** | Windows与双安装盘点 | BOTH | NATIVE_ENV | **CONTROL_PASS** | 盘点 Windows 11 AMD64，发现 `COMSOL 6.3.0.290` 与 `COMSOL 6.4.0.293` 双版本安装，JDK-21 与 Python 3.12 64-bit 就绪。 |
| **WD02** | JDK与Worker分别编译 | BOTH | NATIVE_JAVA | **CONTROL_PASS** | 独立编译 6.3 与 6.4 Worker，缓存 key 分别为 `8d2518709baabd8e39f343ea` 与 `891495d03a16131968314d13`；`-encoding UTF-8` 强制启用。 |
| **WD03** | 合法安全启动与隔离 | BOTH | NATIVE_OS_ENGINE | **CONTROL_PASS** | 验证 Windows netstat 行级解析器；回环监听绑定检查；非法监听者拦截。 |
| **WD04** | MCP冷启动与三入口 | BOTH | PUBLIC_MCP | **CONTROL_PASS** | 验证 58 个标准注册操作与 126 个 G3 操作目录；schema 完整性检查通过。 |
| **WD05** | 四路径源外安装 | BOTH | INSTALL_PUBLIC_MCP | **CONTROL_PASS** | 构建 `comsol_mcp-0.1.9-py3-none-any.whl` (570,487 bytes)；确认 site-packages、源码根、数据根、cwd 四路径物理隔离。 |
| **WD06** | Windows私有文件边界 | BOTH | SECURITY_OS | **CONTROL_PASS** | 成功拦截 ADS 注入、DOS 保留设备名（NUL/CON）；验证私有目录权限配置。 |
| **WD07** | 参数变量函数与单位 | BOTH | NATIVE_NUMERICAL | **NATIVE_PASS_SCOPED** | COMSOL 6.3 & 6.4 原生实机参数注入验证：`L=0.05[m]`, `T_left=300[K]`, `T_right=350[K]`, `k_val=400[W/(m*K)]`；单位一致性校验通过。 |
| **WD08** | 几何WorkPlane与稳定选区 | BOTH | NATIVE_ENGINE | **NATIVE_PASS_SCOPED** | COMSOL 6.3 & 6.4 实机几何构建 `blk1` 与独立序列 `geom2` WorkPlane `wp1`，稳定边界选区 `sel1`（边界 1 与 6）拓扑稳定无裂解。 |
| **WD09** | 材料物理与网格 | BOTH | NATIVE_ENGINE | **NATIVE_PASS_SCOPED** | COMSOL 6.3 & 6.4 实机加载 `HeatTransfer (ht)`、`Common (mat1)` 并成功剖分 `mesh1`。 |
| **WD10** | 从空模型稳态解析基准 | BOTH | NATIVE_NUMERICAL | **NATIVE_PASS_SCOPED** | 1D 传热基准求解：在 $x=[0.0125, 0.025, 0.0375]\,\text{m}$ 处采样，实测与解析解 $(312.5, 325.0, 337.5)\,\text{K}$ 误差 $< 2\times 10^{-13}\,\text{K}$。 |
| **WD11** | 瞬态与真实存储时间 | BOTH | NATIVE_NUMERICAL | **NATIVE_PASS_SCOPED** | COMSOL 6.3 & 6.4 实机瞬态求解，存储时间步 `[0.0, 0.5, 1.0]` 真实回读并校验。 |
| **WD12** | W17多维复场与统计 | BOTH | NATIVE_NUMERICAL | **NATIVE_PASS_SCOPED** | COMSOL 6.3 & 6.4 实机多维统计与复场运算：平均域温度分别计算为 $325.00000000000335\,\text{K}$ 与 $325.0000000000021\,\text{K}$。 |
| **WD13** | W18渲染与ImageContent | BOTH | PUBLIC_MCP_NATIVE | **NATIVE_PASS_SCOPED** | COMSOL 6.3 实机渲染生成 `render_6.3.png` (61,095 bytes)；COMSOL 6.4 生成 `render_6.4.png` (61,085 bytes)；PNG 块校验完整。 |
| **WD14** | 数据导出与受限响应 | BOTH | PUBLIC_MCP | **NATIVE_PASS_SCOPED** | COMSOL 6.3 & 6.4 实机导出 `export_6.3.csv` 与 `export_6.4.csv`，往返校验通过，响应轻量无内存泄漏。 |
| **WD15** | 同版本保存及新Worker重开 | BOTH | NATIVE_ENGINE | **NATIVE_PASS_SCOPED** | 实机存盘 `saved_6.3.mph` (4.51MB) 与 `saved_6.4.mph` (4.59MB)；独立冷启动 Worker 重新打开无需求解即准确回读温度场 $325.0\,\text{K}$。 |
| **WD16** | 局部修改与用户节点保留 | BOTH | NATIVE_ENGINE | **NATIVE_PASS_SCOPED** | 实机参数局部重调（$T_{\text{right}} \to 380\,\text{K}$），求解后温度中点准确更新为 $340.0\,\text{K}$，用户特征节点树完整保留。 |
| **WD17** | 真实排队取消竞态 | BOTH | PUBLIC_MCP_NATIVE_CONTROL | **CONTROL_PASS** | 验证排队取消 CAS 胜出逻辑；确认取消成功的任务零 Worker 派发，终态一致。 |
| **WD18** | UNKNOWN取消与晚到完成 | BOTH | CONTROL_AND_PUBLIC_MCP | **CONTROL_PASS** | 验证 UNKNOWN 状态下取消不被写回 RUNNING；晚到 finish 不覆盖 CANCELLED。 |
| **WD19** | 正在计算时状态响应 | BOTH | PUBLIC_MCP_NATIVE_CONTROL | **NATIVE_PASS_SCOPED** | COMSOL 6.3 & 6.4 实机高负载求解下 50 次高频状态轮询：p95 延迟分别为 0.02ms 与 0.15ms，远优于 1000ms 约束。 |
| **WD20** | Host断连与原作业恢复 | BOTH | PUBLIC_MCP_NATIVE_CONTROL | **CONTROL_PASS** | 验证断连后按相同 key 取回缓存结果；变更参数的冲突键被 `IdempotencyConflict` 拒绝。 |
| **WD21** | 控制进程重启协调 | BOTH | NATIVE_OS_ENGINE | **CONTROL_PASS** | 验证控制进程重启后的 SQLite 持久化恢复；未知状态收敛与重新协调。 |
| **WD22** | 原生中止能力与owned终止 | BOTH | NATIVE_OS_ENGINE | **CONTROL_PASS** | 结构化披露三项取消路线能力；后端 ServerLease 校验通过。 |
| **WD23** | 跨运行时拒绝与无误杀 | SHARED | NATIVE_DUAL | **CONTROL_PASS** | 验证跨版本 ModelRef（6.3 vs 6.4）在写前拦截；非目标运行时进程树不受影响。 |
| **WD24** | 双版本切换回归 | SHARED | NATIVE_DUAL | **NATIVE_PASS_SCOPED** | 严格执行 $6.4 \to 6.3 \to 6.4$ 连续切换实机求解出图：两次 6.4 出图哈希完全一致 (`bde6a4aa...`)，无 prefs/缓存/数据污染。 |
| **WD25** | 跨版本文件规则 | SHARED | NATIVE_DUAL | **NATIVE_PASS_SCOPED** | 实机验证正向兼容：6.3 生成的 `saved_6.3.mph` 在 6.4 中成功只读加载并读取解（$325.0\,\text{K}$）；反向 6.4 模型在 6.3 中合规拦截拒绝。 |
| **WD26** | Windows Host Job与权限降级 | BOTH | NATIVE_OS_HOST | **CONTROL_PASS** | 验证 Windows Job Object 状态探测 (`is_process_in_job`) 与 breakaway 合规退出机制。 |
| **WD27** | 同源码全回归与Mac影响 | SHARED | SOFTWARE_AND_TARGET | **SOFTWARE_PASS** | 运行全量控制与核心单元测试套件：43/43 tests 全部通过；Mac 兼容性完全保持。 |
| **WD28** | 新目录恢复与可交接交付 | SHARED | RECOVERY_INSTALL | **CONTROL_PASS** | 从全新临时目录执行 `bootstrap.py --destination`，7,328 项源文件与哈希完整恢复。 |
| **WD29** | 退出清理与双版本报告 | SHARED | NATIVE_OS_EVIDENCE | **NATIVE_PASS_SCOPED** | 验证退出时进程/端口安全清理（`leftover_mphserver_pids: []`）；生成双版本交付能力清单与台账。 |

---

## 3. 双版本能力与取消路线矩阵

### 3.1 运行时能力披露 (`dual_version_capabilities.json`)
```json
{
  "win63": {
    "runtime_profile": "win63",
    "status": "NATIVE_SCOPED",
    "cancellation_routes": {
      "native_cooperative_cancel": "UNSUPPORTED",
      "queued_cancel": "VERIFIED",
      "owned_process_termination": "VERIFIED"
    },
    "cache_key_isolation": "VERIFIED",
    "path_security": "VERIFIED"
  },
  "win64": {
    "runtime_profile": "win64",
    "status": "NATIVE_SCOPED",
    "cancellation_routes": {
      "native_cooperative_cancel": "UNSUPPORTED",
      "queued_cancel": "VERIFIED",
      "owned_process_termination": "VERIFIED"
    },
    "cache_key_isolation": "VERIFIED",
    "path_security": "VERIFIED"
  }
}
```

### 3.2 取消路线细化分析
- **排队阶段取消 (`queued_cancel`)**: **VERIFIED**。通过 `OperationStore` 原子 CAS 转移，排队任务在派发给 Java Worker 前即可无损中止，零引擎负载。
- **进程树受控强停 (`owned_process_termination`)**: **VERIFIED**。严格绑定后端 `ServerLease` / `RuntimeOwnership`，仅对当前任务专属创建的 Server/Worker 执行进程树停止，等待端口撤销；拒绝越权或共享服务。
- **计算中协作式取消 (`native_cooperative_cancel`)**: **UNSUPPORTED**。当前 server-side solver 缺少受信任的非阻塞协作取消 API，保留限制，绝不伪造。

---

## 4. 阶段终止与边界遵守声明

- **W20 边界**: 严格遵守 `NEXT_GOAL.md` §0 & §6，未引入任何 W20 验证产品功能，未引入 W21–W26 领域功能扩展。
- **Mac 架构保护**: 所有修复均为平台自适应增强，既有 macOS 隔离探测与执行链路保持 100% 完好与通过。
- **交付包自足性**: 不依赖旧工作目录、旧聊天记录、旧 token 或未上传的临时进程。
