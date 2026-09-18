# COMSOL MCP 全能力优化方案：完整开发规格

版本1.0，2026-09-18。设计与测试定义，非已实现的软件。

**内容：**架构与兼容性 → 272项动作 → 60项验收 → 26个工作包 → 开发任务 → 契约说明 → 来源。



---

<!-- source: 01_ARCHITECTURE.md -->

# COMSOL MCP 全能力优化方案 v1.0

**用途：**直接交给云端强模型与开发工程师实施的设计与验收规格。  
**目标平台：**Windows x64、macOS Apple Silicon、macOS Intel；COMSOL 6.3 与 6.4。  
**编写日期：**2026-09-18。  
**审查基线：**Ching-Chiang/comsol-mcp，`ccca65aa8277d1205c5de5fb6221e460aca5997a`，仓库版本 0.1.9。  
**交付性质：**设计文件、动作目录和测试定义，不是已经实现或通过 COMSOL 实测的软件。所有新工具名称均为拟议接口。

## 0. 执行摘要与边界

目标不再是“给小模型准备少量防呆按钮”，而是提供一个可观察、可编程、可恢复的 COMSOL 操作环境。云端模型负责理解任务、查阅资料、生成操作计划和代码、解释结果；本地系统负责执行、保存证据、管理生命周期和执行确定性约束。

采用四层能力：**领域工具 + 通用对象操作 + MCP 内受控 Java 执行 + 可选 Desktop 自动化**。前三层构成 Windows/macOS 共用的核心。GUI 层只处理 API 无法完成的窗口、导入切换和视觉观察，不作为普通几何、网格、求解的主要执行路径。

“所有动作”的验收定义：用户任务契约列出的每一步都应有可执行路径；缺少专用工具时，可以经通用 API 或受控代码执行完成，不应因为维护者没有写一个按钮而阻塞。但不能承诺绕过许可证、操作系统原生缺失功能、未公开的 GUI 行为、计算资源上限或不可识别的物理输入。必须区分 `SUPPORTED_VERIFIED`、`SUPPORTED_UNVERIFIED`、`BLOCKED_LICENSE`、`BLOCKED_PLATFORM`、`BLOCKED_PERMISSION`、`BLOCKED_API_GAP`。

**不以工具个数作为完成度指标。**动作目录可以很大，工具发布可以按领域分组；功能不得为满足“75 个工具”等数字目标被删减。强模型应能读取真实类型、单位、数组、矩阵、复杂模型树、原始异常和完整局部 API 文档，而不只收到自然语言摘要。

**MCP 优先意味着所有 COMSOL 改动进入同一个受审计执行环境。**受控 Java 是 MCP 的一部分，不是让 Agent 绕开 MCP 另开 Python/Java 进程、操作另一份模型。现有代码中的同一模型会话思想保留；不要保留错误的全局清理、伪只读和伪超时语义。

## 1. 已核验现状与必须先修的问题

当前基线注册 50 个工具，已有连接、主模型锁、参数、部分几何/物理/求解器操作、异步求解与保存。它不能被视为完整的自主仿真后端。[S01][S07]

| 缺陷编号 | 源码证据与问题 | 实施要求 | 回归验收 |
|---|---|---|---|
| F01 | 聚合求值调用 `_clear_numerical()`，删除整个 numerical collection；却走只读接口。[S02][S05] | 优先非破坏评估；必要时在同一串行队列创建唯一临时节点，finally 只删本次节点。严格只读模式改用副本。 | 用户原有 numerical 节点、表达式、表关联保持不变；异常时也清理临时节点。 |
| F02 | `_create_variable()` 对变量节点写 `name` 与 `expr`；不等价于定义用户指定变量。[S03][S15] | 正确使用 `.set(name, expression)`，通过 `varnames()` 与 `.get(name)` 回读；支持单节点多变量。 | 定义 `q_abs` 后可计算；不产生名为 name/expr 的意外变量。 |
| F03 | 设置物理接口自身选择集时仍调用 `phys.feature(feature_tag)`。[S03] | 显式区分 physics-level 与 feature-level；支持 named、all、explicit、inherited。 | 父 physics 选择集可改；继承不可改时准确报错。 |
| F04 | `_timed_call` 仅停止等待，不能终止正在执行的 COMSOL/Java 操作。[S04] | 调用超时进入 UNKNOWN/RECONCILING；冻结后续冲突写；查询底层状态后才能重试或恢复。 | 超时后不出现两个仍在运行的写操作；不得把 timeout 当成 cancellation。 |
| F05 | `_run_tool_readonly` 不取 runtime lock，而部分调用触及真实模型甚至创建结果节点。[S05] | 缓存状态读与真实引擎访问分离；同一 server 的普通 API 调用统一串行。 | 求解过程中 job 状态可响应；模型 API 不竞争同一线程/会话。 |
| F06 | 主模型加载会清理其他已加载模型。[S06] | 默认 `prune_other_models=false`；显式清理仅限 MCP 所有对象并需授权。 | 接管一个模型不移除用户的第二个模型。 |
| F07 | 类型被压成字符串；单元素向量可能变成标量。[S02] | 使用显式 type、rank、shape；表达式字符串与数值严格区分；按签名做 Java 转换。 | bool/int/double/string、空数组、1 元素数组、矩阵往返一致。 |
| F08 | 聚合默认 geom1/2D；时间选择及全时间结果处理不完整。[S02] | 从 dataset、component、geometry、entity dimension 共同确定；所有维度显式返回。 | 1D 端点、2D 边界、3D 表面、轴对称权重、all times 分别通过。 |
| F09 | `get_core_metrics` 硬编码 corrosion 类变量、域和边界。[S08] | 改为任务级 metric definition；旧接口安全降级，不运行未知模型特定指标。 | 温度/光场/应力模型不受旧变量影响。 |
| F10 | 存在 120 秒锁等待、300 秒加载等硬编码，与异步入口语义不一致。[S05][S06] | 分离队列等待、RPC 超时、求解运行期限、无进展告警；统一配置。 | 修改配置后确实生效；长加载不因“异步”仍被同一硬编码截断。 |
| F11 | 以 tag/label/path 匹配不能证明 Desktop 当前正在看该模型。[S06] | `model_identity_verified` 与 `desktop_binding_verified` 分开；后者无 GUI 证据时为 unknown。 | 无 GUI 时不得报告“当前窗口已同步”。 |
| F12 | 部分 create 对同名对象直接返回存在，未核验类型；文档工具数/签名漂移。 | 同名类型不匹配返回冲突；从 schema 生成文档与旧接口适配器。 | 重复请求幂等；不同类型的同名请求不得伪成功。 |

审查是静态分析，不等于已经在六种平台/版本组合复现全部缺陷。F01/F02/F03/F04 对应的具体代码路径必须进入第一批实机测试。

## 2. 系统架构

```text
用户 / 云端强模型
          │ 模型服务 API（由 Hermes 或其他 MCP host 管理）
Hermes / MCP Host ─────── 项目任务契约、对话、模型路由
          │ stdio（本机）或受保护的 Streamable HTTP（跨机）
MCP 控制进程（Python；不在这里嵌入 COMSOL JVM）
  ├─ Tool/Operation Registry + JSON Schema + Capability Manifest
  ├─ Project Policy + Plan / Readback / Validation
  ├─ Persistent Jobs + Audit + Artifact Store + Checkpoints
  ├─ Platform Adapter: Windows / macOS
  ├─ Engine Adapter: COMSOL 6.3 / 6.4 / build-specific quirks
  ├─ Session A → 独立 Java Worker → COMSOL 6.3 mphserver
  └─ Session B → 独立 Java Worker → COMSOL 6.4 mphserver
                                  ↑
                       对应版本 COMSOL Desktop（可选）
```

### 2.1 控制层与执行层

建议长期主路径为 **Python MCP 控制层 + 独立 Java API Worker**。选择 Java 不是因为 Python 无法控制 COMSOL，而是为了同时支持 typed API、原生 Java 代码、版本/JVM 隔离以及控制进程崩溃恢复。过渡期可保留 `MPh/JPype` backend，但必须放在独立进程，遵守相同会话、队列与结果协议；不得让全部业务逻辑永久依赖某个 Python 包的内部对象。

Java Worker 使用对应 COMSOL 安装中的官方 client classpath，维护一个持久连接，通过内部认证 IPC 接收结构化请求。控制层不传输 JVM 指针；对象句柄带 session_id、generation 和 model_ref，重连后失效。Worker 不允许自行创建替代主模型或退出用户拥有的 Server。

一次 Worker 只绑定一个 COMSOL engine version；同一 JVM 不混装 6.3/6.4 的 JAR。不同 server 可以并行，同一 COMSOL server 默认所有普通 API 请求串行，即使属于不同模型。COMSOL 官方说明一个 server 同时只处理一个 client 的操作。[S14]

### 2.2 运行模式

| 模式 | 用途 | 必须声明的限制 |
|---|---|---|
| attach-existing | 接已有 mphserver 上已存在模型；优先模式 | 不得自动 prune；必须识别模型而不是只比磁盘路径。 |
| managed-server | MCP 管理独立 mphserver | PID、启动时间、所有权、版本、端口、退出策略必须落盘。 |
| isolated-batch | 参数扫描、破坏性试验、回归 | 操作的是分支副本，不宣称修改了 Desktop 主模型。 |
| desktop-assist | 控制窗口、显示模型、截图、必要的 Shell 操作 | Windows/macOS 分别实现；无权限或无可访问控件时准确报告。 |
| file-transfer | 隔离网环境手工传递任务与证据 | 不具备实时云端交互；不能冒称在线自动闭环。 |

这里的 mphserver 指 COMSOL Multiphysics 的计算客户端/服务器模式，不等同于用于发布应用的 COMSOL Server 产品。不要把安装额外应用发布产品列成统一前提。[S16][S17]

### 2.3 云端模型与断网计算机

推荐拓扑是联网 host 调用云端模型，经受保护的局域网连接本地 MCP；COMSOL 机器不需要直接访问模型服务。另一个有效拓扑是本机 Hermes 通过合规代理调用云端模型，MCP 走 stdio。云端模型本身不直接读取 `C:\...` 或 `/Users/...`：必须由 host/MCP 将授权数据作为工具结果提供。

如果计算机与外界及联网 host 完全无通信通道，只能采用任务文件/证据包传递，不能实时调用云端模型。这是部署约束，不是 MCP 功能缺陷。禁止把公网直连 COMSOL 2036 端口作为方案。

## 3. 六组合平台与版本适配

### 3.1 发布矩阵

| 认证组合 | 核心 API | 可视化/GUI | 安装构件 |
|---|---|---|---|
| Windows x64 × COMSOL 6.3 | 必测 | Desktop + 图片导出必测 | win_amd64 |
| Windows x64 × COMSOL 6.4 | 必测 | Desktop + 图片导出必测 | win_amd64 |
| macOS Apple Silicon × COMSOL 6.3 | 必测 | 原生 ARM GUI 与渲染单独测 | macos_arm64 |
| macOS Apple Silicon × COMSOL 6.4 | 必测 | 原生 ARM GUI 与渲染单独测 | macos_arm64 |
| macOS Intel × COMSOL 6.3 | 必测 | Intel GUI 与渲染单独测 | macos_x86_64 |
| macOS Intel × COMSOL 6.4 | 必测 | Intel GUI 与渲染单独测 | macos_x86_64 |

六项是产品验收目标，不是本文已通过测试的声明。Windows ARM 不纳入本次核心运行认证：所查 COMSOL 6.3/6.4 官方矩阵没有 Windows ARM 原生支持。[S09][S10]

记录具体 OS 版本，不写无限制“所有 macOS”。所查 6.3 Apple Silicon 矩阵列 macOS 13/14/15，所查 6.4 Update 3 列 macOS 14/15/26；发布时再次核查官网并存档本次认证 OS/build。不同 update 的要求不能混为一谈。[S09][S10]

### 3.2 JDK 与启动器

**产品自带 Java 与外部 Java API 支持范围必须分开。**6.3 页面列产品自带 OpenJDK 11；6.4 页面列产品自带 OpenJDK 21，但二者外部 client/server Java API 均列 JRE 8/11。建议以外部 JDK 11 作为独立 Worker 的共同认证起点，COMSOL Server 使用该版本官方启动方式；最终由实机验证 classpath 和连接。不要默认让外部 Worker 使用机器上最高版本 Java。[S09][S10]

`runtime_discover` 必须返回安装根目录、引擎完整版本、OS/CPU/JVM 架构、启动器、classpath 来源、JDK 版本、支持矩阵、测试状态。Windows 使用已验证的 COMSOL 安装定位/用户配置；macOS 识别安装目录或 app bundle 实际位置。不能仅把 `C:\Program Files` 替换为 `/Applications`。

具体命令由平台适配器构造参数数组，禁止 shell 字符串拼接。验证：带空格/中文路径、只读安装目录、macOS 执行权限、应用首次启动权限、进程启动/退出和 PID 重用。执行层不写入 COMSOL 安装目录。

### 3.3 API 与模型文件兼容

共同接口优先，6.3/6.4 差异由 `VersionAdapter` 处理；禁止到处写版本 if。capability key 至少含：OS、architecture、COMSOL 完整 build、产品/许可证、backend、图形状态。API 存在、许可证允许、调用成功和验证通过是不同状态。

模型交付要求 6.3 时，在 6.3 引擎创建/保存并重新打开验证；要求双版本时，以共同能力重放同一 build recipe，在两套引擎各产出文件。不把 6.4 保存文件当成可无损“另存为 6.3”的通用功能，不通过改扩展名或内部版本号降级。

6.3 文件在 6.4 打开/求解也必须测试警告、迁移和数值差异。跨 OS 迁移需清理绝对路径、重新绑定外部函数/数据、核对 CAD 内核与可用产品。模型形状、关键设置、验收指标可比，不要求二进制文件/网格编号逐字节相同。

Application Builder、特定 CAD/LiveLink、GPU 和集群功能按平台独立标识；不能因 MCP 跨平台就宣称底层产品功能一致。[S09][S10]

## 4. 全能力调用结构

### 4.1 领域工具

常见动作提供明确 schema 和中文/英文说明，例如 `material_set_properties`、`mesh_feature_create`、`study_step_create`、`result_evaluate`。每项工具必须有输入验证、真实回读、版本能力检查和至少一个测试定义。不是只有工具名能被发现就算实现。

### 4.2 通用对象层

必须覆盖 list/inspect/find/create/copy/remove/rename/reorder/enable、属性 typed get/set/setIndex/setEntry、集合与选择集、构建/执行。路径解析应使用结构化段，例如：

```json
{"segments":[
 {"collection":"component","tag":"comp1"},
 {"collection":"geom","tag":"geom1"},
 {"collection":"feature","tag":"wp3"},
 {"accessor":"geom"},
 {"collection":"feature","tag":"r1"}
]}
```

这可以定位 Work Plane 内矩形；不能把每一层都假定为 `.feature(tag)`。通用层对 object kind 使用小型 typed resolver registry；未知节点应查元数据和文档，不能直接 `eval(path)`。

COMSOL `PropFeature` 已提供 `properties()`、`getType()`、`getValueType()`、typed setter、`getAllowedPropertyValues()` 等。[S11][S12] **自省不等于完整语义文档**：允许值接口只适用于有限字符串枚举，可能返回 null；也不能仅凭它发现所有尚未创建的 feature 类型。要结合版本化文档、现有节点、Java 签名与隔离试建。

### 4.3 专家级代码入口

`code_execute_java` 是核心能力，不是最后阶段可删项。代码在绑定目标模型的 Worker 执行，必须使用系统注入的 `Model model`，在作业开始时核验 model_ref；默认禁止片段调用 `ModelUtil.create/load/clear` 改换主模型。确需管理多模型时，使用单独显式权限。

推荐编译执行接口为 `Map<String,Object> run(Model model, ActionContext ctx)`。模型返回结构化结果；`ctx` 提供日志、进度、artifact 写入和取消检查。代码先编译，编译错误附行号；编译成功不代表语义正确或安全。试运行需要明确标识为副本执行，不能把它冒称无副作用 dry-run。

代码调用要求：source hash、完整源文件、参数、模型身份、预期前置条件、修改声明、checkpoint 策略、期限、结果与副作用记录。COMSOL API 长调用不能依靠 Java 线程 interrupt 保证立即取消，恢复逻辑见任务章节。

高频 code recipe 通过回归后可提升为正式领域工具。这样既能完成未封装动作，又能逐步减少重复代码。

### 4.4 Desktop 层

提供状态、目标窗口绑定、展示指定模型、选择树节点、调整视图、截图、菜单操作、有限键鼠操作及 Shell 执行。Windows 可用 UI Automation，macOS 走 Accessibility/系统支持的窗口接口；对 Java/SWT 控件的可访问性和权限必须做 PoC。不能把 pywinauto 原封不动移植到 macOS，也不能把截图当成模型身份的唯一证明。

普通 standalone Desktop 不会自动变成 API 共享会话。提供显式迁移流程：确认未保存状态→授权保存副本→连接/建立 mphserver→加载/绑定→Desktop 选中服务端模型→核验。只有经过平台验证的 GUI/Shell adapter 才能尝试直接修改当前 standalone 模型；无验证则标为实验性，不能承诺透明接管。

### 4.5 工具发布给强模型

逻辑动作数不设人为上限。支持 full、domain、expert 三种工具展示配置，底层能力相同。静态 host 可以列全工具；有动态工具发现能力的 host 可按域加载；不具备动态发现时提供 `operation_describe` 与有严格二次 schema 验证的 `operation_call`。不能假定所有 MCP host 都实现动态工具更新。

工具结果使用 structuredContent；工具本身失败时设置 isError，同时提供机器错误码。只有全批次成功才返回 success；部分成功必须列逐项状态。长数组以 artifact 引用/分块返回，不静默截断或只给自然语言结论。[S20]

## 5. 统一数据契约

### 5.1 模型身份

每次模型相关请求携带 `project_id`、`session_id`、`model_ref`；后者由 server_instance_id、model_tag、generation、schema_version 组成。标签 label 仅展示，路径仅来源，不能当唯一身份。模型被重新载入、回滚替换或服务重启时 generation 增加，旧句柄失效。

每次写入要求 `expected_revision`（或显式声明 force 策略）、`idempotency_key` 和权限范围。revision 是本系统管理的修订号，不冒称 COMSOL 内建原子 CAS。接入官方 ModelChangedHandler 监视外部客户端修改，结合 touched-node 指纹回读；不能检测的外部改动窗口必须报告。

### 5.2 类型和单位

参数/函数表达式保留为表达式字符串，如 `600[kW/m^2]`，不提前变成裸浮点。typed value 保存 `kind`、`shape`、`data`，必要时给 `java_signature`。坐标、时间、频率、边界/域维数必须带单位或引用明确的模型单位。实体 ID 不是数组索引；COMSOL API 索引约定在适配器处理，不对模型可见 ID 擅自加减 1。

复数返回 real/imag 或幅相表示并声明约定；不能默默取实部。field 数据返回 coordinate_frame、dataset、solution、inner/outer index、time/frequency/parameters、unit、shape、validity_mask。

### 5.3 标准返回

```json
{
 "ok": true,
 "operation_id": "op-uuid",
 "project_id": "wafer-project",
 "session_id": "session-uuid",
 "model_ref": "model-uuid:g3",
 "revision_before": 18,
 "revision_after": 19,
 "status": "SUCCEEDED",
 "data": {},
 "changes": [],
 "warnings": [],
 "artifacts": [],
 "verification": {"readback": "passed", "physical_validation": "not_run"},
 "timings": {"queue_s": 0.0, "engine_s": 0.0, "total_s": 0.0}
}
```

`ok=true` 只表示该动作契约成功，不表示整个科学模型正确。预览、已提交作业、正在运行、取消已请求、恢复成功各自有不同状态，不能统称 done。

### 5.4 错误码

至少包括 `MODEL_IDENTITY_MISMATCH`、`REVISION_CONFLICT`、`ENGINE_BUSY`、`ENGINE_UNRESPONSIVE`、`EXECUTION_STATE_UNKNOWN`、`LICENSE_UNAVAILABLE`、`UNSUPPORTED_PLATFORM`、`UNSUPPORTED_VERSION`、`NODE_NOT_FOUND`、`PROPERTY_TYPE_MISMATCH`、`INVALID_SELECTION`、`EMPTY_SELECTION`、`UNIT_MISMATCH`、`COMPILE_ERROR`、`SOLVER_FAILED`、`NO_SOLUTION`、`ARTIFACT_MISSING`、`PERMISSION_DENIED`、`CANCEL_NOT_CONFIRMED`、`CHECKPOINT_RESTORE_REQUIRES_REBIND`。

错误必须包括 phase、节点路径、原始 COMSOL 异常摘要、完整日志 artifact、是否发生部分改动、可否安全重试、建议的下一步。不要仅以字符串包含某个词推断所有错误原因。

## 6. 完整能力覆盖要求

下面按能力族给出闭环要求；逐动作名称、主要参数、风险等级和实现路线见《动作目录》及 JSON。

| 能力族 | 必须可完成的动作 | 特别要求 |
|---|---|---|
| 环境与许可证 | 发现安装、版本/架构、可用功能、许可证允许与可 checkout 状态、JDK/渲染检查 | 未授权 checkout 不作为无副作用探测；支持状态区别明确。 |
| 会话与模型 | attach/create/load/adopt/list/save/copy/close/rebind、模型依赖、版本重放 | 不擅自清理用户模型；保存覆盖需项目策略。 |
| 自省与通用操作 | 深树、局部树、搜索、typed property、集合 CRUD、嵌套路径、模型 API 调用 | 选择实体、选择对象、selection property 不能混用。 |
| 参数与定义 | 参数组/描述/案例；变量组、增删改查、作用域、批量、表达式验证 | 读取原表达式和求值结果；不丢单位/描述。 |
| 函数 | 解析/插值/分段/阶跃/坡道、输入数据、单位、导数、外推、重载、测试点 | 支持多维静态光斑；拒绝未批准的径向平均替代。 |
| 选择与定位 | named/explicit/spatial/boolean/adjacent、实体 bbox/质心/测度/法向/邻接 | named selection 也需重建后检查，不能假设永远不漂移。 |
| 几何与 CAD | 1D/2D/轴对称/3D、基本体、布尔、变换、阵列、嵌套 Work Plane、拉伸/旋转/扫掠、导入、装配、修复 | 支持只改指定现有 feature；geometry kernel/产品差异须探测。 |
| 坐标系、配对、耦合算子 | 坐标变换、identity/contact pair、积分/平均/极值/映射 | 源/目标方向、实体维数、接触初始间隙须可读回。 |
| 材料 | 材料及属性组 CRUD、域绑定、温度相关函数、张量、光学/热学/力学属性 | property 缺失检查有范围；材料数值来源必须记录。 |
| 物理与多物理 | 接口/feature/subfeature CRUD、域/边界分配、初值、源项、耦合、弱形式/PDE/ODE、移动网格 | 依赖变量名由接口语义确定，不统一强制 u。 |
| 网格 | 序列/feature CRUD、尺寸、自由三角/四面体、扫掠/映射、边界层、分布、局部细化、导入导出 | 质量/实体覆盖/DOF与内存估计；局部失败位置可定位。 |
| Study | study 与 step CRUD、物理启用、初始解来源、时间/频率/特征值、扫描 | 自动 solver 生成不能覆盖用户 solver 而不告知。 |
| Solver | 深层特征、直接/迭代/分离/全耦合、时间步/容差/缩放、延续、解清理/复用 | 成功状态、求解警告、残差/时间/迭代可查；不混淆初始化和有解。 |
| 作业与资源 | 提交、状态、日志、取消、恢复、优先级、内存/核数/磁盘预算 | 结果与状态持久化；引擎繁忙时控制面不失联。 |
| 数据与派生值 | dataset、cutpoint/line/plane/join、全局/点/积分/平均/极值、表格、probe | 全时间、内/外参数解、复场、坐标和单位正确。 |
| 绘图与导出 | plotgroup/feature、云图/切片/等值线/箭头/流线、view/camera、PNG/数据/动画/报告 | 明确数据集与解；原始数值必须与图像绑定。 |
| 优化与试验 | 原生扫参、外部 DOE/优化、设计变量、约束、指标、案例缓存与重试 | 优化模块缺失时只允许明确的外部算法路径，不伪装原生授权。 |
| 校验与比较 | preflight、单位/选择/材料/mesh/study 检查、收敛/守恒/物理指标、版本比较 | 已知规则未覆盖的内容给 unknown；通过不等于物理完备。 |
| 历史与事务 | checkpoint、差异、分支、回放、恢复、提交、来源/假设 | 不是数据库 ACID；外部文件/GUI状态不能自动全回滚。 |
| GUI 与帮助 | Desktop 状态/绑定/截图/操作、API帮助、示例、错误检索、离线索引 | 无 GUI/无文档权限仍保持 API 核心可用。 |

## 7. 面向用户实际任务的能力包

### 7.1 VCSEL 阵列与晶圆加热

任务契约包含晶圆直径/厚度、材料、芯片或模块几何、间距定义、环/模块功率上限、发散分布、距离 L、功率密度阈值、是否旋转、接收面的坐标定义及目标均匀性。

必须支持：在现有 Work Plane 子几何内查询和修改阵列；矩形/环形阵列及多层参数；每圈/每模块独立驱动；二维 `Q(x,y)` 或随时空变化的输入；表面热流 W/m² 与体热源 W/m³ 严格区分；吸收率、反射腔效应作为显式模型假设；材料温度相关参数；对流/辐射；旋转的物理模型或明确的平均化模型；L 扫描与约束优化；静态整片温度图和原始数据。

当任务要求“静止功率分布”时，不得用圆周平均数据替代。若另一个任务要求旋转平均，应作为独立分支，并说明平均成立条件由物理建模验证，不能凭 MCP 工具自行决定。

几何间距验收区分芯片中心距、外缘间距、模块边缘距离和环半径差。图像看似均匀不算验收；指标至少含 ROI、Tmax/Tmin/面积加权平均、标准差、功率预算、网格/时间步敏感性及能量收支。目标 ±1℃ 等来自任务契约，不被写成后端保证。

### 7.2 单模激光器—光纤耦合

必须支持参数化透镜/纤芯/包层/空气域、局部波动光学与适当的尺度分解、数值端口/边界模式、PML、偏振、复场导出、输入功率归一化、模式重叠积分、x/y/z/角度/公差扫描，以及热—结构变形向光学的映射。

`eta_capture`（进入截面的功率比例）与 `eta_mode`（指定导模耦合）必须分开。复场相位/坐标/单位/归一化不能丢失。对声称单模的输入，缺失的快轴发散、近场定义、波长/透镜参数等必须保持 unknown/estimated，不在 backend 私自硬编码。

### 7.3 胶水成形、UV 与热固化

必须支持局部几何、材料表面/接触角分区、稳定液面或相场/水平集路径、胶量守恒、与光纤/镀金台/陶瓷的交互、阶段解传递、固化状态/收缩/黏弹性/热应变以及胶内与光纤应力输出。

提供两条有明确标签的任务路线：静态 UV 前平衡形态比较；融合→UV→烘烤→冷却的全流程。每阶段记录几何、温度、转化率、应力与无应力参考状态的定义和传递规则。不能通过全局缩放几何“补体积”而不说明其物理含义。

跨台阶接触陶瓷是允许的物理路径，不能默认边缘是不可穿越墙。若采用局部钉扎/全局平衡等不同假设，要分别输出并检验敏感性。通过调参复现实验趋势属于校准，不可同时当成独立验证。

### 7.4 阶段状态传递必须成为通用能力

`experiment_stage_define`、`experiment_stage_run`、`checkpoint_create`、`solver_solution_transfer`、`experiment_state_map` 不只服务胶水。它们应能用于热循环、预应力、固化、接触、屈曲和热光耦合。传递项包含来源 solution/dataset、时间/参数索引、变量映射、几何框架、插值方式和守恒误差。

## 8. 持久作业、并发与取消

### 8.1 生命周期

```text
QUEUED → STARTING → RUNNING → SUCCEEDED
                         ├→ FAILED
                         ├→ CANCEL_REQUESTED → CANCELLED
                         └→ UNKNOWN → RECONCILING → RUNNING / FAILED / RECOVERED / LOST
```

所有长操作统一作业：模型加载、几何构建、mesh、solver、参数扫描、CAD 导入、渲染/导出、代码和检查。job 状态写本地持久数据库；数据库不要放不支持一致性的网络共享盘。日志与大 artifact 单独存文件。

每个 job 保存 submitted/started/last-progress/finished 时间、server identity、model generation、request hash、阶段、实际 COMSOL 证据、取消请求与确认、恢复记录。host 对话关闭后 daemon/worker 生命周期按配置保留；不要依赖 MCP stdio 子进程自动幸存。重启后不盲目重试非幂等请求。

### 8.2 四种时间不同

分别配置 `rpc_timeout_s`、`queue_timeout_s`、`execution_timeout_s`、`no_progress_warning_s`。`execution_timeout_s=null` 表示经授权不设任务运行硬期限，仍可取消且受资源预算管理。模型思考时间由 host 记录，不算 COMSOL 求解时间。模型正在长求解时，MCP 的健康与 job status 应读缓存快速返回。

无日志变化不等于挂死；稀疏矩阵分解、I/O、导入等可能长时间无进度。综合 PID、生存性、CPU/I/O、COMSOL状态和日志判断，证据不足标 unknown。

### 8.3 真进度与真取消

可用时接 COMSOL progress log（例如 ModelUtil 的日志接口），并保留原始日志。[S14] 可报告实际时间步、迭代、参数 case；不可计算可靠百分比时返回 null，而不是伪造 80%。

`job_cancel` 首先提交取消请求；只有确认底层计算停止，才能进入 CANCELLED。按版本验证原生中止路线；若缺失，可用受授权 Desktop Cancel。托管隔离 Server 最后可终止自有进程并恢复 checkpoint，返回强制终止语义。共享/用户 Server 不得默认杀死。不能把关线程、丢 Future 或断客户端连接当成求解已停止。[S04][S18]

### 8.4 Desktop 并发

使用本地项目写 lease；外部 Desktop 通过 ModelChangedHandler 及指纹发现变化。需要严格原子多步动作时，可短时使用官方 blockOtherClients，但它是 server-wide 且无内建超时，并会推迟其他客户端通知；必须授权、finally 解除、watchdog 与意外断连测试，绝不在整次长求解期间滥用。[S14]

只靠 Python RLock 不能防止用户在 Desktop 修改模型。冲突时停止当前计划，呈现差异；不要覆盖用户变化。

### 8.5 MCP 协议兼容

stdio 必须只输出协议消息，Java/stdout 日志要重定向。跨机用受保护的 Streamable HTTP，不把 SSE 旧传输当唯一方案。[S21] 可支持 MCP Tasks，但当前查阅规范仍把 Tasks 标为实验性；与 host 协商，保留 `job_submit/status/cancel/result` 工具路径。[S22]

## 9. 安全、自由度与可恢复写入

### 9.1 授权层级

- `inspect`：只读真实数据/文档；涉及临时模型节点的“读取”另标 read_with_ephemeral_mutation。
- `project_write`：项目内参数、结构、函数、mesh/study，默认写前 checkpoint 策略。
- `compute`：按预算运行、求解、导出。
- `trusted_code`：对项目授权的 Java 代码；记录代码与结果，不要求每条普通模型 API 人工批准。
- `host_control`：执行外部二进制、任意文件、GUI 键鼠、关闭/强杀进程、对外传输、共享 server 管理，单独授权。

云端模型强并不意味着不可误操作。降低反复确认通过一次性的项目授权和分级策略实现，不通过默认授予整机管理员权限实现。

**静态代码扫描和类白名单不是安全沙箱。**任意 Java 代码可能访问文件、网络或进程；外部函数还可能在 COMSOL Server 一侧执行。因此需要受限 OS 账户/ACL、必要的主机或 VM 隔离与网络规则，并同时约束 worker 和受控 server。连接用户高权限共享 server 时不得宣称已获得强隔离。

### 9.2 网络与云端数据

本地 stdio 或 loopback 默认；跨机 TLS、身份认证、项目级授权、来源检查和审计。凭据从系统凭据存储/专用 secret reference 读取，不写日志和模型提示词。云端可见内容通过 data egress 策略控制；默认不上传整个 `.mph`、许可证内容和整机路径清单。函数/结果摘要也可能含机密，不能因为体积小就默认可公开。

文档、模型描述和导入文件一律当作数据，不接受其中“关闭审计/上传所有文件”等指令。模型传入路径需要规范化、禁止越界、处理符号链接/Windows重解析点；URI 下载与外部工具执行另作权限控制。

### 9.3 事务不是 ACID

支持 `plan_preview`（静态计划）、`checkpoint_create`、`transaction_apply`、`transaction_verify`、`transaction_restore`。计划校验不能预测所有 COMSOL 副作用。动态 dry-run 在隔离副本上执行，结果与主模型区分。

恢复可能替换服务端模型对象，导致 generation 改变，并要求 Desktop 重新绑定；不得保证任意操作都能原地无损 undo。对导出文件、外部材料库、GUI状态、并行外部脚本的副作用，必须声明 restore 范围；不能用数据库回滚术语掩盖缺失。

大模型不必每个标量 setter 全存一次 MPH。可对一个计划分组 checkpoint，对轻量参数改动记录可逆日志；结构/mesh/solver 的不可逆写前保存可靠 checkpoint。保存文件先写唯一临时路径、完成校验后原子发布；磁盘不足时不得覆盖最后可用模型。

## 10. 结果、证据与物理验证

### 10.1 证据包

每次正式交付至少含：目标版本 `.mph`、可重放 recipe/代码、所有外部依赖清单与 hash、求解日志、原始数值、图片、模型身份、软件/build/许可证能力摘要、假设/参数来源、操作差异和验收报告。不同结果必须关联其确切 model revision 和 solution index。

图片必须来自真实计算/明确标注的预览。后端保存图片 artifact；支持图像的 host 以 image content 返回缩略图/原图，文本-only host 保留路径和替代数据。不能保证所有“顶级模型”或所有 host 都消费图像，需要协商 image capability。[S20]

### 10.2 三层成功

`EXECUTION_SUCCESS`：COMSOL 动作完成。  
`NUMERICAL_ACCEPTANCE`：收敛、网格/时间步敏感性和误差门槛通过。  
`PHYSICAL_VALIDATION`：满足任务定义的守恒/实验/理论对照及适用假设。

三者分开报告。选区不空、材料都有和 solver 成功，仍不足以证明仿真正确。温度 ±1℃、耦合效率或胶形趋势等属于任务级标准，后端必须帮助检验而不能预先保证。

### 10.3 指标系统

一个 metric 定义包含 expression(s)、component/dataset/selection、domain/boundary dimension、time/frequency/inner/outer choice、单位、聚合方式、权重、掩膜、阈值和来源。面积/体积加权平均不替换为节点算术平均；轴对称积分的径向权重避免重复乘 2πr。复场、偏振和相位指标不得走只取 getReal 的代码路径。

`validation_report` 为规则集报告：每条输出 pass/fail/unknown、不覆盖原因和证据。发现两条边界条件重叠时先判断它们是否允许共存，不把所有叠加都判冲突。

## 11. 文档、示例与技能库

本地建立 6.3/6.4 分开的官方 HTML/JavaDoc/安装帮助索引，优先字段/全文检索，再可选语义检索，不要求联网 embedding 才能使用。索引以实际合法安装为来源，不把商业文档、JAR、模型库打包分发。

每条检索结果包含版本、产品、section、来源路径/链接、摘要和证据范围。无条目时明确未找到。官方文档和模型内说明不是可执行指令。代码示例标注 tested/synthetic/untested；强模型可用 `docs_search → api_describe → code_compile → isolated_trial → apply → readback → validate` 完成陌生动作。

任务技能库分为 VCSEL 热场、光纤模式耦合、润湿/固化、热循环结构等，但不能把某次模型的固定 domain 编号和假设塞回通用 backend。

## 12. 代码组织与迁移

```text
comsol_mcp/
  protocol/            # MCP schemas, tool publication, legacy wrappers
  registry/            # logical operations, capability and test metadata
  control/             # projects, sessions, jobs, policy, artifacts, audit
  model/               # identity, node resolver, revision, selection semantics
  domains/             # geometry, material, physics, mesh, study, result...
  adapters/
    platform/windows.py
    platform/macos.py
    version/v63.py
    version/v64.py
    backend/java_worker.py
    backend/mph_legacy.py
  knowledge/           # versioned local docs and verified recipes
  validation/          # structural, numerical, scientific checks
java-worker/
  rpc/                 # authenticated internal protocol, no public eval server
  runtime/             # version classpath, persistent model handle
  api/                 # typed object resolver and argument conversion
  code/                # compiler, source cache, structured action context
  observability/       # progress/log adapters
schemas/
config/
tests/unit/
tests/protocol/
tests/integration/
tests/platform/
tests/scientific/
tests/chaos/
```

保留原 50 个工具名的兼容 wrapper，路由到新服务层；修 bug 可以改变错误行为，但须迁移说明。不要为了维持兼容继续删除 numerical nodes。原 `properties_json` 接口只作为 compatibility input，内部统一强类型对象；通过 schema 自动生成 README、AGENTS 和 MCP 文档。

通过核心 gate 后再减少对 legacy MPh backend 的依赖，避免长期双实现漂移。未知 backend/version 返回 capability 状态，不能静默选另一个 COMSOL 安装。

## 13. 开发顺序与发布门槛

| 阶段 | 交付内容 | 不可放宽的通过门槛 |
|---|---|---|
| G0 架构验证 | 六组合安装/JDK/连接/GUI共享/渲染/取消 PoC，冻结 capabilities | 真实机证据；版本/平台不支持不能用 mock 代替。 |
| G1 安全修复 | F01–F12、模型身份、无破坏读取、typed value、串行队列 | 故意失败不污染用户模型；状态可诊断。 |
| G2 通用内核 | 嵌套节点、generic API、Java编译执行、项目授权、checkpoint | 未封装模型动作能通过 MCP 完成并回读，不另开错误主模型。 |
| G3 完整建模 | definitions/selection/geometry/material/physics/mesh/study/solver | 每组合从空模型到真实求解，保存再开。 |
| G4 观察与恢复 | dataset/复场/metrics/plots/export、持久jobs、取消、冲突恢复 | 长任务中断与重连不重算、不假报成功，证据一致。 |
| G5 项目包 | VCSEL、光纤、固化/阶段传递、优化与扫参 | 用户代表性任务全链跑通；物理假设单列。 |
| G6 发布认证 | hermetic包、全矩阵、可重放脚本、兼容wrapper、文档 | 六组合实测报告；没有被隐藏的 required case。 |

顺序不是日历工期承诺。若 macOS GUI 或共享会话取消路线在 G0 不成立，立即调整能力边界/运行模式，不等到功能全部写完再发现架构问题。

## 14. 安装、离线包与运维

分发控制层和自有 Java Worker，不分发 COMSOL 可执行文件/JAR/商业文档/许可证。按 Windows x64、macOS arm64、macOS x86_64 生成独立 lock 与构件；需要本地原生 Python 扩展时按架构准备 wheelhouse。venv 不能当成跨 OS 通用可搬包。

包应含自检、升级/回退、schema migration、requirements锁、哈希、版本清单、配置示例。安装和运行均不得隐式 pip install 或下载缺失依赖；离线模式必须可预检所有文件。日志轮转与 artifact 保留策略可配置，不自动删用户正式模型。

建议独立用户服务负责 daemon 生命周期：Windows 使用匹配的用户服务/任务机制，macOS 使用用户 launchd；涉及 Desktop 的操作必须在有图形会话的用户上下文运行。进程服务与 GUI 权限分别验证。

`doctor` 输出可执行的诊断：找不到安装、JVM架构不符、版本不符、许可不允许、端口不可达、classpaths不一致、图形后端不可用、项目目录不可写、磁盘不足、文档未索引、host不支持图像/Tasks。

## 15. 定义完成（Definition of Done）

1. 六个核心平台/版本组合均有真实 COMSOL 连接、修改、求解、保存再开、数据和图像导出证据；全部 required 测试通过，不将 skip 计入通过率。
2. 原始模型不因 inspect/evaluate 被删除用户节点；主模型以 session/model_ref/revision 管理，GUI绑定状态不伪造。
3. 任意已授权且在该环境 API 可实现的任务动作有领域工具、generic API 或受控代码路线；缺口可发现、可定位，不靠乱猜执行。
4. 用户代表性三类模型至少各一套端到端验收；原始数据和物理假设公开，图像不是代替数值的证据。
5. 超时、断网、host退出、worker崩溃、engine繁忙、取消、磁盘不足和Desktop外部改动均有故障注入记录，恢复状态真实。
6. 交付物在目标 COMSOL 版本打开，依赖完整、路径可迁移、代码可重放；认证仅覆盖实际测试的build/OS/功能。

没有真实 COMSOL 机器时，允许先交付 unit/protocol 通过的开发版，但状态必须是 `INTEGRATION_UNVERIFIED`，不得标成最终完成。


---

<!-- source: 02_ACTION_CATALOG.md -->

# 全能力动作目录

本目录共 **272 个逻辑动作**，是设计目标，不是声称当前仓库已有这些工具。工具名采用 `domain_action`，逻辑编号采用 `domain.action`。

## 阅读和实现规则

`?` 表示可选参数。模型操作共同要求 project_id/session_id/model_ref；模型写操作另需 expected_revision、idempotency_key。job/status 必须允许引擎断开时读取，不强制活跃模型句柄。数量不是完成度指标；每项以真实路径、回读和测试验收。

风险：READ=只读；WRITE=模型写；EVALUATE=数值计算（可能临时写节点，须串行）；COMPUTE=长计算；FILE_WRITE=产物写；STATE_WRITE=管理状态写；TRUSTED_CODE=授权代码；HOST_CONTROL=窗口/主机/外发控制；DYNAMIC=按明确子动作决定效果与权限。不能因为命名为manage/invoke就逃避权限分类。

JSON 附件为每个动作提供顶层输入 schema；类型见 common.schema.json。涉及 COMSOL feature-specific properties 的语义 schema 必须由实际版本能力生成并经测试，本文不伪造所有商业模块的内部属性列表。输出统一 ActionResult，但发布前仍要按领域补充 data 的具体 schema。

## 环境、安装与许可证（runtime）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `runtime_discover` | `roots:strings?` | 发现全部 COMSOL 安装、JDK 与架构 | READ / G0 |
| `runtime_inspect` | `runtime_id:str` | 读取指定安装完整 build、启动器、classpath | READ / G0 |
| `runtime_doctor` | `runtime_id:str?,checks:strings?` | 检查安装、目录、端口、JVM、渲染与配置 | READ / G0 |
| `runtime_capabilities` | `runtime_id:str?,refresh:bool?` | 读取能力状态、限制与实测证据 | READ / G0 |
| `runtime_license_inspect` | `runtime_id:str,products:strings?` | 检查许可允许的产品，不静默占用许可 | READ / G0 |
| `runtime_license_checkout` | `runtime_id:str,products:strings,authorization_ref:str` | 经授权尝试 checkout 并记录结果 | HOST_CONTROL / G0 |
| `runtime_render_probe` | `runtime_id:str,mode:str?` | 在隔离最小模型测试渲染输出 | COMPUTE / G0 |
| `runtime_compatibility_report` | `runtime_ids:strings,requirements:object` | 检查任务需求与目标安装兼容性 | READ / G0 |

## 工具发现与操作协议（registry）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `registry_list` | `domain:str?,cursor:str?,limit:int?` | 按域分页列出逻辑动作 | READ / G2 |
| `registry_describe` | `operation_id:str` | 取得单一动作完整输入/输出与示例 | READ / G2 |
| `registry_search` | `query:str,domain:str?` | 按任务语义或关键字查动作 | READ / G2 |
| `registry_call` | `operation_id:str,arguments:object` | host无法动态加载时调用指定动作；二次严格校验 | DYNAMIC / G2 |
| `registry_manifest` | `profile:str?` | 输出当前版本实际注册工具及兼容证据 | READ / G2 |

## 项目、任务契约与权限（project）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `project_create` | `label:str,workspace:str,policy:object` | 创建项目工作区和初始策略 | STATE_WRITE / G1 |
| `project_inspect` | `project_id:str` | 读取任务、授权、资源预算与数据策略 | READ / G1 |
| `project_contract_set` | `project_id:str,contract:object` | 登记目标、ROI、物理假设、阈值、交付版本 | STATE_WRITE / G1 |
| `project_policy_set` | `project_id:str,policy:object,authorization_ref:str` | 经授权修改项目权限及超时/资源预算 | HOST_CONTROL / G1 |
| `project_permissions` | `project_id:str` | 读取当前有效授权，不返回密钥 | READ / G1 |
| `project_state_export` | `project_id:str,detail:str?` | 为host压缩/恢复提供模型与任务状态摘要 | READ / G4 |

## Server 会话与所有权（session）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `session_list` | `filter:object?` | 列出活跃/失联/已停止的会话 | READ / G0 |
| `session_connect` | `runtime_id:str,endpoint:Endpoint,credentials_ref:str?` | 连接已有 mphserver，核对版本与所有权 | STATE_WRITE / G0 |
| `session_start` | `runtime_id:str,options:object?,resources:object?` | 启动专用受管 mphserver | HOST_CONTROL / G0 |
| `session_inspect` | `session_id:str` | 读取连接、模型身份、Server所有权与健康状态 | READ / G0 |
| `session_reconnect` | `session_id:str` | 重新附着并使旧对象句柄失效 | STATE_WRITE / G4 |
| `session_disconnect` | `session_id:str` | 断开客户端，不默认关闭Server | STATE_WRITE / G0 |
| `session_stop` | `session_id:str,authorization_ref:str` | 关闭自己管理的Server；共享Server另需授权 | HOST_CONTROL / G4 |
| `session_health` | `session_id:str` | 读取缓存/轻量生存性，不阻塞等待求解 | READ / G0 |
| `session_recover` | `session_id:str,recovery_policy:object?` | 对失联任务和模型作状态核对 | STATE_WRITE / G4 |

## 模型生命周期与可移植性（model）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `model_list` | `session_id:str` | 列出服务端模型，不删除任何模型 | READ / G0 |
| `model_create` | `session_id:str,label:str,dimension:int?` | 显式新建模型并返回唯一身份 | WRITE / G3 |
| `model_load` | `session_id:str,artifact_id:str,path_policy:object?` | 加载文件，默认不清理其他模型 | WRITE / G0 |
| `model_adopt` | `session_id:str,server_model_tag:str` | 绑定已存在的server model tag | STATE_WRITE / G0 |
| `model_inspect` | `detail:str?` | 读取身份、结构摘要、解/外部依赖 | READ / G1 |
| `model_tree` | `path:NodePath?,depth:int?,cursor:str?,limit:int?` | 按深度/路径/分页取得模型树 | READ / G2 |
| `model_save` | `destination:str,overwrite:bool,include_solution:bool?` | 保存到批准路径/Artifact，明确覆盖策略 | FILE_WRITE / G3 |
| `model_clone` | `label:str,include_solution:bool?` | 生成隔离实验模型并返回新model_ref | WRITE / G2 |
| `model_close` | `discard_changes:bool,authorization_ref:str?` | 关闭指定模型；保护未保存修改 | HOST_CONTROL / G1 |
| `model_dependencies` | `verify_hashes:bool?` | 列出函数数据、CAD、外部代码等依赖 | READ / G3 |
| `model_package` | `destination:str,include_solution:bool?` | 导出可移植模型与依赖清单 | FILE_WRITE / G6 |
| `model_rebuild_target` | `target_runtime_id:str,recipe_artifact:str` | 在指定引擎重放recipe，不伪装文件降级 | COMPUTE / G6 |
| `model_compare` | `other_model_ref:str,scope:object?` | 比较模型结构、关键属性与指标 | READ / G6 |

## 通用模型对象与嵌套节点（node）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `node_inspect` | `path:NodePath,include_values:bool?` | 读取类型、标签、属性、选择、子集合 | READ / G2 |
| `node_children` | `path:NodePath,cursor:str?,limit:int?` | 列出指定节点的子集合和对象 | READ / G2 |
| `node_find` | `query:object,root:NodePath?,limit:int?` | 按type/tag/label/path搜索模型节点 | READ / G2 |
| `node_create` | `parent:NodePath,collection:str,tag:str,type_id:str,properties:PropertySet?` | 在typed collection创建节点 | WRITE / G2 |
| `node_copy` | `source:NodePath,target_parent:NodePath,tag:str` | 在同模型或分支复制节点并核对依赖 | WRITE / G2 |
| `node_remove` | `path:NodePath,cascade:bool?` | 预检依赖后移除节点；可报告受影响对象 | WRITE / G2 |
| `node_label_set` | `path:NodePath,label:str` | 修改展示标签，不把标签作为身份 | WRITE / G2 |
| `node_active_set` | `path:NodePath,active:bool` | 启用/禁用节点 | WRITE / G2 |
| `node_move` | `path:NodePath,before:NodePath?,after:NodePath?` | 在支持排序的集合中调整位置 | WRITE / G2 |
| `node_property_schema` | `path:NodePath,name:str?` | 取得属性类型、枚举、索引信息 | READ / G2 |
| `node_property_get` | `path:NodePath,names:strings` | 按类型回读一个或多个属性 | READ / G2 |
| `node_property_set` | `path:NodePath,properties:PropertySet` | 设置带类型的标量/向量/矩阵并回读 | WRITE / G2 |
| `node_property_index_set` | `path:NodePath,name:str,indices:ints,value:TypedValue` | 按明确索引设置数组/矩阵元素 | WRITE / G2 |
| `node_property_entry_set` | `path:NodePath,name:str,key:str,value:TypedValue` | 按entry key设置值 | WRITE / G2 |
| `node_selection_get` | `path:NodePath,selection_name:str?` | 读取实体/对象/命名选择及继承状态 | READ / G2 |
| `node_selection_set` | `path:NodePath,selection:SelectionSpec,selection_name:str?` | 按明确selection种类设置 | WRITE / G2 |

## 公共 API 描述与结构化调用（api）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `api_describe` | `path:NodePath,method:str?` | 列出允许的公共方法、签名和版本文档 | READ / G2 |
| `api_invoke` | `path:NodePath,method:str,arguments:TypedValues,java_signature:strings?,declared_effect:str` | 调用未高层封装的公共COMSOL方法 | DYNAMIC / G2 |
| `api_probe` | `probe:object` | 在副本上验证公共API/特征可用性 | COMPUTE / G2 |

## MCP 内受控代码执行（code）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `code_compile_java` | `runtime_id:str,source_artifact:str,entrypoint:str` | 按目标引擎classpath编译，不执行模型动作 | COMPUTE / G2 |
| `code_execute_java` | `source_artifact:str,entrypoint:str,arguments:object,mode:str,timeout_s:number?,invariants:objects?` | 在注入的目标Model上执行已授权代码 | TRUSTED_CODE / G2 |
| `code_inspect_run` | `job_id:str` | 读取代码、编译信息、日志与副作用 | READ / G2 |
| `code_recipe_register` | `source_artifact:str,input_schema:object,test_refs:strings` | 把测试过代码登记为可复用recipe | STATE_WRITE / G5 |
| `code_recipe_run` | `recipe_id:str,arguments:object` | 按版本检查执行指定recipe | TRUSTED_CODE / G5 |

## 参数、分组与案例（parameter）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `parameter_list` | `group:str?` | 列出参数组、原表达式、描述和单位 | READ / G3 |
| `parameter_get` | `names:strings,evaluate:bool?` | 读取指定参数与可选求值 | READ / G3 |
| `parameter_set` | `parameters:objects,group:str?` | 批量设置参数表达式和描述 | WRITE / G3 |
| `parameter_remove` | `names:strings,group:str?` | 删除参数并报告引用风险 | WRITE / G3 |
| `parameter_group_manage` | `action:str,tag:str,arguments:object?` | 创建/重命名/删除参数组或移动参数 | WRITE / G3 |
| `parameter_case_manage` | `action:str,group:str,case_tag:str,values:object?` | 创建、读取或应用参数案例 | DYNAMIC / G5 |
| `parameter_import` | `artifact_id:str,format:str,group:str?` | 从项目数据文件导入并校验单位 | WRITE / G3 |
| `parameter_export` | `format:str,destination:str` | 导出参数、单位与描述 | FILE_WRITE / G3 |

## 全局/组件变量组（variable）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `variable_list` | `component:str?` | 列出所有组与变量名、表达式、描述 | READ / G1 |
| `variable_group_create` | `tag:str,component:str?,selection:SelectionSpec?` | 创建变量组及作用选区 | WRITE / G1 |
| `variable_set` | `group:NodePath,variables:objects` | 正确设置name→expression，支持同组多变量 | WRITE / G1 |
| `variable_get` | `group:NodePath,names:strings?` | 读取变量表达式/描述/有效范围 | READ / G1 |
| `variable_remove` | `group:NodePath,names:strings?,whole_group:bool?` | 删除组内变量或显式删除整个组 | WRITE / G1 |
| `variable_selection_set` | `group:NodePath,selection:SelectionSpec` | 设置变量组命名选区 | WRITE / G3 |

## 函数与插值热源（function）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `function_list` | `component:str?` | 列出全局/组件函数及依赖 | READ / G3 |
| `function_create` | `scope:NodePath?,tag:str,type_id:str,definition:object` | 创建解析、插值、分段、阶跃等函数 | WRITE / G3 |
| `function_inspect` | `path:NodePath` | 读取定义、参数单位、值单位和外推方式 | READ / G3 |
| `function_update` | `path:NodePath,definition:object` | 更新函数属性与分段/插值设置 | WRITE / G3 |
| `function_remove` | `path:NodePath` | 删除函数并检测引用 | WRITE / G3 |
| `function_data_import` | `path:NodePath,artifact_id:str,layout:object,units:object` | 绑定多维插值数据、坐标轴、单位和网格布局 | WRITE / G3 |
| `function_data_reload` | `path:NodePath` | 重新读取输入文件并记录hash | WRITE / G3 |
| `function_evaluate` | `path:NodePath,arguments:objects,derivative:object?` | 在指定测试点返回值及越界状态 | EVALUATE / G3 |
| `function_validate` | `path:NodePath,checks:object?` | 检查定义域、单位、连续性/外推规则 | EVALUATE / G3 |

## 选择集与空间实体（selection）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `selection_list` | `component:str?` | 列出命名选择、维数与实体数 | READ / G3 |
| `selection_create` | `component:str,tag:str,type_id:str,definition:object` | 创建explicit/空间/布尔/adjacent等命名选择 | WRITE / G3 |
| `selection_inspect` | `component:str,tag:str` | 读取命名选择的定义与当前展开实体 | READ / G3 |
| `selection_update` | `component:str,tag:str,definition:object` | 更新命名选择并报告实体变化 | WRITE / G3 |
| `selection_remove` | `component:str,tag:str` | 移除命名选择并报告引用对象 | WRITE / G3 |
| `selection_entities` | `selection:SelectionSpec,cursor:str?,limit:int?` | 返回实体ID与几何修订，不作跨修订稳定承诺 | READ / G3 |
| `selection_query_spatial` | `component:str,geometry:str,dimension:int,query:object,tolerance:Quantity` | 按点、框、法向、测度等条件检索实体 | EVALUATE / G3 |
| `selection_measure` | `selection:SelectionSpec,metrics:strings` | 取得面积/体积/长度、bbox、质心和方法 | EVALUATE / G3 |
| `selection_adjacency` | `selection:SelectionSpec,target_dimension:int` | 查询域/面/边/点之间邻接 | READ / G3 |
| `selection_validate` | `selection:SelectionSpec,expectations:object` | 检查非空、维数、预期测度/位置/数量 | EVALUATE / G3 |
| `selection_rebind` | `selection:SelectionSpec,rule:object,preview:bool?` | 几何变化后按语义规则重建或核验绑定 | WRITE / G4 |

## 几何、工作平面与 CAD（geometry）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `geometry_sequence_create` | `component:str,tag:str,dimension:int,axisymmetric:bool?` | 创建指定维数/轴对称属性的几何序列 | WRITE / G3 |
| `geometry_inspect` | `path:NodePath` | 读取序列特征、单位、内核和构建状态 | READ / G3 |
| `geometry_feature_create` | `parent:NodePath,tag:str,type_id:str,properties:PropertySet,inputs:object?` | 创建基本体/布尔/变换/阵列等 | WRITE / G3 |
| `geometry_feature_update` | `path:NodePath,properties:PropertySet,inputs:object?` | 改指定现有特征而不重建整个模型 | WRITE / G3 |
| `geometry_feature_remove` | `path:NodePath` | 删除特征并报告下游依赖 | WRITE / G3 |
| `geometry_workplane_create` | `geometry:NodePath,tag:str,definition:object` | 建立工作平面与局部坐标定义 | WRITE / G3 |
| `geometry_workplane_edit` | `workplane:NodePath,actions:objects` | 对嵌套2D几何应用typed操作计划 | WRITE / G3 |
| `geometry_array_create` | `geometry:NodePath,tag:str,definition:object` | 建立线性/矩形/环形/自定义阵列并保留参数 | WRITE / G5 |
| `geometry_build` | `geometry:NodePath,until_tag:str?` | 完整构建或构建到指定feature，作为作业 | COMPUTE / G3 |
| `geometry_import` | `geometry:NodePath,tag:str,artifact_id:str,options:object?` | 导入批准的CAD文件、单位和内核 | WRITE / G3 |
| `geometry_export` | `geometry:NodePath,format:str,destination:str,selection:SelectionSpec?` | 导出几何，检查格式/许可支持 | FILE_WRITE / G5 |
| `geometry_finalize` | `geometry:NodePath,mode:str,options:object?` | 设置Form Union/Assembly与保留内部边界选项 | WRITE / G3 |
| `geometry_repair` | `geometry:NodePath,plan:object` | 修复/去特征/虚拟操作，须副本或checkpoint | WRITE / G5 |
| `geometry_measure` | `geometry:NodePath,query:object` | 测量对象、实体和间距 | EVALUATE / G3 |
| `geometry_validate` | `geometry:NodePath,expectations:object?` | 检查构建问题、实体数量、体积和设计间距 | EVALUATE / G3 |

## 坐标系、Pair、耦合与组件（definition）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `definition_component_manage` | `action:str,tag:str,definition:object?` | 创建/检查/复制/删除组件 | DYNAMIC / G3 |
| `definition_coordinate_manage` | `action:str,path:NodePath?,definition:object?` | 坐标系CRUD与变换检查 | DYNAMIC / G3 |
| `definition_pair_manage` | `action:str,path:NodePath?,definition:object?` | identity/contact等Pair CRUD和方向选择 | DYNAMIC / G3 |
| `definition_coupling_manage` | `action:str,path:NodePath?,definition:object?` | 积分/平均/极值/投影/拉伸算子CRUD | DYNAMIC / G3 |
| `definition_mapping_validate` | `path:NodePath,test_points:objects?,checks:object?` | 检查源目标框架、映射误差和覆盖 | EVALUATE / G5 |

## 材料和属性组（material）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `material_list` | `component:str?` | 列出材料、类型、域覆盖和属性组 | READ / G3 |
| `material_create` | `component:str,tag:str,type_id:str,definition:object?` | 创建材料/链接/切换结构 | WRITE / G3 |
| `material_inspect` | `path:NodePath` | 读取材料属性、表达式、来源与域绑定 | READ / G3 |
| `material_set_properties` | `path:NodePath,group:str,properties:PropertySet,provenance:object?` | 按property group写标量/张量/温度函数 | WRITE / G3 |
| `material_group_manage` | `path:NodePath,action:str,group:str,definition:object?` | 管理材料属性组及输入变量 | WRITE / G3 |
| `material_selection_set` | `path:NodePath,selection:SelectionSpec` | 为材料绑定命名域/边界选择 | WRITE / G3 |
| `material_remove` | `path:NodePath` | 删除材料并给出失去材料的域 | WRITE / G3 |
| `material_import` | `artifact_id:str,selection:SelectionSpec?,provenance:object` | 从合法项目模型/文件导入材料定义 | WRITE / G3 |
| `material_validate` | `scope:NodePath?,checks:object?` | 检查已启用物理需要的已知属性规则 | EVALUATE / G3 |

## 物理接口、子特征与多物理（physics）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `physics_list` | `component:str?` | 列出接口类型、变量、域和feature树 | READ / G3 |
| `physics_create` | `component:str,tag:str,type_id:str,geometry:str,dependent_variables:strings?` | 按真实接口类型及几何绑定创建physics | WRITE / G3 |
| `physics_inspect` | `path:NodePath,depth:int?` | 读取接口及其边界/域/初值与变量设置 | READ / G3 |
| `physics_remove` | `path:NodePath` | 删除接口并报告Study/耦合依赖 | WRITE / G3 |
| `physics_feature_create` | `parent:NodePath,tag:str,type_id:str,entity_dimension:int?,properties:PropertySet?` | 创建域/面/边/点特征和嵌套子特征 | WRITE / G3 |
| `physics_feature_update` | `path:NodePath,properties:PropertySet` | 更新feature属性并回读 | WRITE / G3 |
| `physics_feature_remove` | `path:NodePath` | 删除子特征 | WRITE / G3 |
| `physics_selection_set` | `path:NodePath,selection:SelectionSpec` | 明确物理接口自身或子特征选择 | WRITE / G1 |
| `physics_multiphysics_manage` | `action:str,path:NodePath?,definition:object?` | 多物理耦合CRUD和接口关联 | DYNAMIC / G3 |
| `physics_pde_manage` | `action:str,path:NodePath?,definition:object` | 配置系数/一般/弱形式PDE与全局ODE | WRITE / G5 |
| `physics_initial_values_set` | `path:NodePath,definition:object` | 设置初值或前一阶段解来源 | WRITE / G3 |
| `physics_validate` | `scope:NodePath?,checks:object?` | 检查已知缺失/冲突/继承/维数规则 | EVALUATE / G3 |

## 网格构建与质量（mesh）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `mesh_list` | `component:str?` | 列出网格序列、状态与来源 | READ / G3 |
| `mesh_create` | `component:str,tag:str,geometry:str,mode:str?` | 创建mesh序列及物理控制/用户控制模式 | WRITE / G3 |
| `mesh_inspect` | `path:NodePath,depth:int?` | 读取mesh feature树和已生成网格统计 | READ / G3 |
| `mesh_feature_create` | `parent:NodePath,tag:str,type_id:str,properties:PropertySet,selection:SelectionSpec?` | 创建Size/FreeTri/FreeTet/Swept/边界层等 | WRITE / G3 |
| `mesh_feature_update` | `path:NodePath,properties:PropertySet` | 修改局部尺寸/层厚/分布等 | WRITE / G3 |
| `mesh_feature_remove` | `path:NodePath` | 删除网格特征 | WRITE / G3 |
| `mesh_build` | `path:NodePath,until_tag:str?` | 生成全部网格或生成到feature | COMPUTE / G3 |
| `mesh_clear` | `path:NodePath` | 清除生成网格，不静默删用户配置 | WRITE / G3 |
| `mesh_quality` | `path:NodePath,metric:str,selection:SelectionSpec?,bins:int?` | 返回质量度量定义、分布及差元素位置 | EVALUATE / G3 |
| `mesh_statistics` | `path:NodePath` | 返回单元数、维数、覆盖、DOF/资源估计 | READ / G3 |
| `mesh_import` | `path:NodePath,artifact_id:str,options:object?` | 导入网格并验证单位/拓扑 | WRITE / G5 |
| `mesh_export` | `path:NodePath,format:str,destination:str` | 导出网格文件与实体映射 | FILE_WRITE / G5 |
| `mesh_validate` | `path:NodePath,criteria:object` | 检查域覆盖、单元质量、边界层完整性 | EVALUATE / G3 |
| `mesh_convergence_study` | `definition:object,metrics:strings` | 生成多网格分支并对指定指标比较 | COMPUTE / G5 |

## 研究步骤与扫描（study）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `study_list` | `scope:NodePath?` | 列出Study、step、physics启用和solver关联 | READ / G3 |
| `study_create` | `tag:str,label:str?` | 新建Study | WRITE / G3 |
| `study_inspect` | `path:NodePath` | 读取Study步骤、扫描和依赖 | READ / G3 |
| `study_remove` | `path:NodePath,remove_solver:bool?` | 删除Study并显式处理关联solver | WRITE / G3 |
| `study_step_create` | `study:NodePath,tag:str,type_id:str,properties:PropertySet` | 创建稳态/瞬态/频域/特征值等步骤 | WRITE / G3 |
| `study_step_update` | `path:NodePath,properties:PropertySet` | 更新时间点/频率/求解变量等 | WRITE / G3 |
| `study_step_remove` | `path:NodePath` | 删除步骤 | WRITE / G3 |
| `study_physics_activation` | `step:NodePath,activation:object` | 按步骤启用/停用物理及变量求解 | WRITE / G3 |
| `study_initial_solution_set` | `step:NodePath,source:SolutionSpec,mapping:object?` | 关联先前解/时间点/参数case | WRITE / G5 |
| `study_sweep_manage` | `study:NodePath,action:str,definition:object` | 配置参数/辅助/批处理扫描 | WRITE / G5 |
| `study_solver_generate` | `study:NodePath,replace_existing:bool` | 生成自动solver；覆盖旧配置必须显式 | WRITE / G3 |
| `study_run` | `study:NodePath,resources:object?,timeout_s:number?` | 提交Study求解，立即返回持久job | COMPUTE / G3 |

## 求解器树与解管理（solver）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `solver_list` | `filter:object?` | 列出solver和solution及关联 | READ / G3 |
| `solver_inspect` | `path:NodePath,depth:int?` | 递归读取solver设置、子特征与问题节点 | READ / G3 |
| `solver_create` | `tag:str,study:NodePath` | 新建solver配置并关联Study | WRITE / G3 |
| `solver_feature_create` | `parent:NodePath,tag:str,type_id:str,properties:PropertySet?` | 创建Stationary/Time/Direct/Iterative/Segregated等节点 | WRITE / G3 |
| `solver_feature_update` | `path:NodePath,properties:PropertySet` | typed设置容差、时间步、缩放等 | WRITE / G3 |
| `solver_feature_remove` | `path:NodePath` | 删除任意受支持子feature | WRITE / G3 |
| `solver_run` | `path:NodePath,range:object?,timeout_s:number?` | 提交solver序列或指定范围求解 | COMPUTE / G3 |
| `solver_solution_inspect` | `solution:SolutionSpec` | 区分初始化/空/可用解；返回索引与参数 | READ / G4 |
| `solver_solution_clear` | `path:NodePath,scope:str` | 显式清除解数据与相关缓存 | WRITE / G4 |
| `solver_solution_transfer` | `source:SolutionSpec,target:NodePath,mapping:object` | 将明确选定的解映射到下阶段 | WRITE / G5 |
| `solver_log_read` | `job_id:str,cursor:str?,limit:int?` | 读取真实求解日志和异常链 | READ / G4 |
| `solver_resource_configure` | `resources:object,scope:str` | 设置经支持验证的核数/内存/运行参数 | WRITE / G4 |

## 持久任务与恢复（job）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `job_list` | `filter:object?,cursor:str?,limit:int?` | 列出项目内作业，可在引擎离线时调用 | READ / G4 |
| `job_status` | `job_id:str` | 读取持久状态、阶段与实际进度 | READ / G4 |
| `job_log` | `job_id:str,cursor:str?,limit:int?` | 分页读取原始日志，不需要引擎空闲 | READ / G4 |
| `job_result` | `job_id:str` | 取得最终结果与artifact，不触发重算 | READ / G4 |
| `job_cancel` | `job_id:str,mode:str,reason:str` | 请求中止并等待独立确认状态 | HOST_CONTROL / G4 |
| `job_reconcile` | `job_id:str` | 核对超时/重启后的真实执行状态 | STATE_WRITE / G4 |
| `job_resume` | `job_id:str,checkpoint_id:str?` | 只对可重入的已检查点任务恢复 | COMPUTE / G4 |
| `job_wait` | `job_id:str,max_wait_s:number` | 有界等待并返回状态；不替代持久任务 | READ / G4 |
| `job_cleanup` | `job_ids:strings,policy:object` | 清理已完成job元数据/缓存，保留正式交付物 | HOST_CONTROL / G6 |

## 数据集、截面与解索引（dataset）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `dataset_list` | `filter:object?` | 列出datasets与解、时间/参数索引关联 | READ / G4 |
| `dataset_create` | `tag:str,type_id:str,definition:object` | 创建solution/cutpoint/cutline/cutplane/join等 | WRITE / G4 |
| `dataset_inspect` | `path:NodePath` | 读取数据来源、坐标、过滤及选区 | READ / G4 |
| `dataset_update` | `path:NodePath,definition:object` | 更新数据集属性和源解 | WRITE / G4 |
| `dataset_remove` | `path:NodePath` | 删除数据集并提示依赖plot/evaluation | WRITE / G4 |
| `dataset_solution_indices` | `path:NodePath` | 列出内/外解、真实时间/频率/参数组合 | READ / G4 |

## 数值求值、复场与原始数据（result）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `result_evaluate` | `spec:EvaluationSpec` | 全局/点/积分/平均/极值；保留时间和复数 | EVALUATE / G4 |
| `result_at_points` | `spec:EvaluationSpec,points:objects,coordinate_unit:str,frame:str` | 在明确坐标/框架求值并返回有效掩膜 | EVALUATE / G4 |
| `result_sample_grid` | `spec:EvaluationSpec,grid:object` | 在规则网格或面上导出静态场 | EVALUATE / G4 |
| `result_sample_path` | `spec:EvaluationSpec,path_definition:object` | 沿线/曲线采样并保留弧长与坐标 | EVALUATE / G4 |
| `result_numerical_manage` | `action:str,path:NodePath?,definition:object?` | 维护用户可见Derived Values节点，不清整个集合 | DYNAMIC / G4 |
| `result_table_manage` | `action:str,path:NodePath?,definition:object?` | 表创建/读取/追加/清理/删除 | DYNAMIC / G4 |
| `result_field_export` | `spec:EvaluationSpec,format:str,destination:str` | 导出大复场/解向量及坐标、单位、hash | FILE_WRITE / G4 |
| `result_mode_overlap` | `definition:object` | 计算带归一化与约定的模式重叠指标 | EVALUATE / G5 |

## 探针与监控（probe）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `probe_list` | `filter:object?` | 读取global/point/boundary/domain探针 | READ / G4 |
| `probe_create` | `tag:str,type_id:str,definition:object` | 创建probe、表达式、选区与表 | WRITE / G4 |
| `probe_update` | `path:NodePath,definition:object` | 修改probe配置 | WRITE / G4 |
| `probe_remove` | `path:NodePath` | 移除probe，不删除无关结果 | WRITE / G4 |
| `probe_history` | `path:NodePath,solution:SolutionSpec?,cursor:str?` | 返回记录的probe历史、时间/参数和单位 | READ / G4 |

## 绘图、视图与图像（plot）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `plot_list` | `filter:object?` | 列出plotgroup及其绑定的数据集/解 | READ / G4 |
| `plot_group_create` | `tag:str,dimension:int,dataset:NodePath,properties:PropertySet?` | 创建1D/2D/3D绘图组 | WRITE / G4 |
| `plot_feature_create` | `group:NodePath,tag:str,type_id:str,properties:PropertySet` | 创建surface/slice/contour/arrow/line等 | WRITE / G4 |
| `plot_update` | `path:NodePath,properties:PropertySet` | 修改表达式、范围、颜色/图例/数据绑定 | WRITE / G4 |
| `plot_remove` | `path:NodePath` | 移除绘图组或feature | WRITE / G4 |
| `plot_render` | `path:NodePath,solution:SolutionSpec?,options:object?` | 按明确solution导出真实绘图图像 | COMPUTE / G4 |
| `plot_geometry_render` | `geometry:NodePath,mode:str,options:object?` | 渲染几何/网格/实体标签供检查 | COMPUTE / G4 |
| `plot_view_manage` | `action:str,path:NodePath?,definition:object?` | 创建/读取/设置视角、相机、轴、隐藏实体 | DYNAMIC / G4 |

## 导出、报告与正式交付（export）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `export_list` | `filter:object?` | 列出export节点及设置 | READ / G4 |
| `export_create` | `tag:str,type_id:str,definition:object` | 创建数据/图片/动画/表格导出 | WRITE / G4 |
| `export_update` | `path:NodePath,definition:object` | 更新导出参数和目标Artifact | WRITE / G4 |
| `export_run` | `path:NodePath` | 执行export并检查文件存在、大小、hash | COMPUTE / G4 |
| `export_remove` | `path:NodePath` | 删除export配置，不默认删目标文件 | WRITE / G4 |
| `export_report` | `definition:object,destination:str` | 生成模型/结果/验收报告与来源 | FILE_WRITE / G5 |
| `export_evidence_bundle` | `definition:object,destination:str` | 生成mph+recipe+原始数据+图+日志+来源的包 | FILE_WRITE / G6 |

## 指标、数值与物理验收（metric）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `metric_define` | `metric_id:str,definition:object` | 定义单位、ROI、加权方式、索引与阈值 | STATE_WRITE / G4 |
| `metric_list` | `filter:object?` | 列出任务级指标定义与状态 | READ / G4 |
| `metric_evaluate` | `metric_ids:strings,solution:SolutionSpec?` | 批量评估指标，返回每项证据 | EVALUATE / G4 |
| `metric_remove` | `metric_id:str` | 删除指标定义，不改用户原结果节点 | STATE_WRITE / G4 |
| `metric_compare` | `cases:objects,metric_ids:strings,tolerances:object` | 比较不同case/版本/网格的指标 | EVALUATE / G5 |

## 模型诊断与科学检查（validate）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `validate_preflight` | `scope:object?,checks:strings?` | 求解前结构、材料、选择、网格与study检查 | EVALUATE / G3 |
| `validate_structure` | `scope:NodePath?,rules:strings?` | 检测缺失节点/无效引用/禁用分支 | READ / G3 |
| `validate_expressions` | `expressions:objects,context:object` | 检查表达式/变量/已知单位规则 | EVALUATE / G3 |
| `validate_boundary_conditions` | `scope:NodePath?,rules:strings?` | 诊断已知缺失和冲突，允许合法叠加 | EVALUATE / G3 |
| `validate_solution` | `solution:SolutionSpec,criteria:object` | 检查有解、范围、NaN/Inf、收敛警告 | EVALUATE / G4 |
| `validate_conservation` | `definition:object,solution:SolutionSpec` | 执行任务定义的能量/质量/功率收支 | EVALUATE / G5 |
| `validate_convergence` | `cases:objects,metrics:strings,criteria:object` | 比较网格/时间步/域截断敏感性 | EVALUATE / G5 |
| `validate_report` | `validation_ids:strings,destination:str?` | 聚合pass/fail/unknown及未覆盖项 | FILE_WRITE / G4 |

## 扫描、优化与阶段流程（experiment）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `experiment_design` | `definition:object` | 登记设计变量、边界、约束与采样策略 | STATE_WRITE / G5 |
| `experiment_run` | `experiment_id:str,resources:object?,timeout_s:number?` | 运行DOE/外部优化/原生扫描并保存每个case | COMPUTE / G5 |
| `experiment_inspect` | `experiment_id:str` | 读取case进展、失败原因、缓存与最优可行项 | READ / G5 |
| `experiment_case_result` | `experiment_id:str,case_id:str` | 返回指定case真实模型/原始结果 | READ / G5 |
| `experiment_stage_define` | `definition:object` | 登记阶段次序、状态传递和参考态 | STATE_WRITE / G5 |
| `experiment_stage_run` | `stage_id:str,source:SolutionSpec?` | 运行一阶段并保存状态历史 | COMPUTE / G5 |
| `experiment_state_map` | `source:SolutionSpec,target:NodePath,mapping:object` | 显式映射温度、固化、应力等历史变量 | WRITE / G5 |

## 检查点、分支与恢复（checkpoint）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `checkpoint_create` | `label:str,include_solution:bool?` | 保存恢复点与依赖hash、模型身份 | FILE_WRITE / G1 |
| `checkpoint_list` | `filter:object?` | 列出项目模型恢复点 | READ / G1 |
| `checkpoint_inspect` | `checkpoint_id:str` | 读取检查点完整性和可恢复范围 | READ / G1 |
| `checkpoint_restore` | `checkpoint_id:str,authorization_ref:str?` | 恢复并报告新generation及GUI重绑定需要 | WRITE / G4 |
| `checkpoint_diff` | `left:str,right:str,scope:object?` | 比较当前模型或两个检查点 | READ / G4 |
| `checkpoint_branch` | `checkpoint_id:str,label:str` | 从检查点创建隔离试验分支 | WRITE / G2 |

## 操作计划与分组执行（transaction）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `transaction_preview` | `actions:objects,invariants:objects?` | 仅静态检查计划、权限、依赖与预算 | READ / G2 |
| `transaction_trial` | `actions:objects,invariants:objects?` | 在副本执行计划并输出差异和验证 | COMPUTE / G2 |
| `transaction_apply` | `actions:objects,invariants:objects?,checkpoint_policy:str` | 按前置修订、checkpoint和权限执行动作组 | WRITE / G2 |
| `transaction_verify` | `transaction_id:str,checks:objects?` | 按不变量和回读核验一次事务 | EVALUATE / G2 |
| `transaction_recover` | `transaction_id:str,strategy:str` | 对部分失败执行显式恢复策略 | WRITE / G4 |

## 文件、原始数据与数据外发（artifact）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `artifact_register` | `path:str,role:str,classification:str` | 将批准的文件导入项目并计算hash | FILE_WRITE / G3 |
| `artifact_list` | `filter:object?,cursor:str?,limit:int?` | 分页列出项目Artifact，不扫描整机 | READ / G3 |
| `artifact_inspect` | `artifact_id:str` | 读取大小、类型、hash、版本、生成作业 | READ / G3 |
| `artifact_read` | `artifact_id:str,offset:int?,length:int?` | 分块读取授权Artifact；保留完整文件 | READ / G4 |
| `artifact_preview` | `artifact_id:str,options:object?` | 返回图像/数据预览并声明下采样 | READ / G4 |
| `artifact_publish` | `artifact_ids:strings,destination_ref:str,authorization_ref:str` | 按明确外发授权向host交付文件/图像 | HOST_CONTROL / G4 |
| `artifact_verify` | `artifact_id:str` | 校验包内文件hash、缺失依赖和目标格式 | READ / G6 |

## 可选GUI和现有窗口协作（desktop）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `desktop_status` | `runtime_id:str?` | 读取窗口、进程、权限与adapter验证状态 | READ / G0 |
| `desktop_bind` | `window_ref:str,model_ref:str,verification:object?` | 明确绑定GUI窗口与服务端模型 | HOST_CONTROL / G0 |
| `desktop_show_model` | `window_ref:str,model_ref:str` | 让窗口显示已加载server model并验证 | HOST_CONTROL / G4 |
| `desktop_select_node` | `window_ref:str,path:NodePath` | 按API路径和GUI树映射定位节点 | HOST_CONTROL / G4 |
| `desktop_capture` | `window_ref:str,region:str` | 截取指定窗口/图形区，禁止默认全桌面 | HOST_CONTROL / G4 |
| `desktop_action` | `window_ref:str,action:object` | 执行授权的菜单/快捷键/控件动作 | HOST_CONTROL / G5 |
| `desktop_shell_execute` | `window_ref:str,source_artifact:str,expected_model_ref:str` | 经平台验证后在当前Desktop Shell执行代码 | TRUSTED_CODE / G5 |
| `desktop_migrate_standalone` | `window_ref:str,target_session_id:str,save_policy:object` | 显式保存迁移普通Desktop模型到共享server | HOST_CONTROL / G5 |

## 版本化文档、示例与错误检索（docs）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `docs_index` | `runtime_id:str,sources:strings` | 索引用户合法安装中的帮助，不分发商业文档 | STATE_WRITE / G2 |
| `docs_search` | `query:str,version:str,product:str?,limit:int?` | 按版本/产品检索API与建模文档 | READ / G2 |
| `docs_get` | `document_ref:str,section:str?,offset:int?,length:int?` | 读取具体文档范围并带来源 | READ / G2 |
| `docs_examples` | `query:str,version:str` | 检索已验证/未验证示例并显示状态 | READ / G2 |
| `docs_error_search` | `error:str,version:str,node_type:str?` | 按原始异常与节点类型检索诊断证据 | READ / G4 |



---

<!-- source: 03_ACCEPTANCE.md -->

# 验收与故障注入测试规格

本文件共 60 个测试定义。**所有测试状态为 NOT_RUN；本次未执行真实 COMSOL 仿真。**

## 全局规则

六组合都必须记录真实OS、COMSOL build、JDK、backend、产品与测试机。Mock只验证控制逻辑，不证明COMSOL功能。Required case 未跑或skip不能计为pass；许可证缺失应单列blocked，不隐瞒。不同平台允许数值容差内差异，不要求MPH/网格编号逐字节一致。性能数字是拟议验收目标，不是实测成绩。

每个case的证据目录至少包含 environment.json、request.json、result.json、assertions.json、engine.log；必要时附 before/after模型、原始数据、图和hash。报告须区分schema/unit/protocol、engine integration、numerical acceptance、physical validation。

## T001 — 六组合安装自检

阶段：G0；范围：ALL；状态：NOT_RUN。

**执行：**安装两版本；运行runtime_discover/doctor；核对CPU/JDK/classpath/OS/build。

**通过标准：**六种组合各有实际运行记录；无跨架构隐式仿真；错误路径可诊断。

**必须保留：**doctor.json、进程/版本清单、runtime日志。

## T002 — 6.3/6.4进程隔离

阶段：G0；范围：ALL；状态：NOT_RUN。

**执行：**同时启动两种版本的worker/server并分别创建小模型；交叉请求。

**通过标准：**各自正确读回；把6.3句柄送到6.4明确失败；不混装JAR。

**必须保留：**PID/启动时刻、model_ref、调用日志。

## T003 — 已有模型不被清理

阶段：G1；范围：ALL；状态：NOT_RUN。

**执行：**服务端预载两个模型，在第一个建立用户numerical/plot；adopt第二个。

**通过标准：**第一个模型和所有节点仍存在；无自动prune。

**必须保留：**前后模型列表与结构diff。

## T004 — 真实Desktop共享绑定

阶段：G0；范围：ALL_GUI；状态：NOT_RUN。

**执行：**Desktop和worker连接同一server；交替改参数并回读。

**通过标准：**模型相同；外部修改能检测；无GUI时只能报告binding unknown。

**必须保留：**窗口证据、API读回、binding报告。

## T005 — 聚合求值不破坏用户节点

阶段：G1；范围：ALL；状态：NOT_RUN。

**执行：**预建用户Derived Values和表；执行max/min/avg/integral；再触发无效表达式。

**通过标准：**用户节点、表达式和表关联不变；临时节点最终清理；失败不删除其他节点。

**必须保留：**结构快照、数据表、异常日志。

## T006 — 变量set语义

阶段：G1；范围：ALL；状态：NOT_RUN。

**执行：**在全局/组件同一变量组设两个变量；修改其中一个并求值。

**通过标准：**varnames包含指定变量；不误建name/expr变量；表达式可计算。

**必须保留：**变量表、求值结果。

## T007 — 物理接口/子特征选区

阶段：G1；范围：ALL；状态：NOT_RUN。

**执行：**分别设置physics-level、feature-level、inherited selection，绑定named selection。

**通过标准：**父选择设置正确；继承不可写准确失败；无错误phys.feature(phys_tag)。

**必须保留：**selection读回、实体截图可选。

## T008 — 类型往返

阶段：G2；范围：ALL；状态：NOT_RUN。

**执行：**使用受支持属性测试bool/int/double/string及空、一元素、多维数组；非法矩阵测试。

**通过标准：**形状和类型不丢失；越界/不支持签名在写前拒绝。

**必须保留：**typed-value输入输出、负向断言。

## T009 — 嵌套Work Plane编辑

阶段：G3；范围：ALL；状态：NOT_RUN。

**执行：**载入含wp3和矩形阵列的模型；只改选定子feature并构建。

**通过标准：**定位正确子几何；保留左侧指定对象；主模型不替换；实体数量与间距满足契约。

**必须保留：**局部树diff、几何测量、mph。

## T010 — 幂等与类型冲突

阶段：G2；范围：ALL；状态：NOT_RUN。

**执行：**重复同一idempotency_key；同tag同type重复创建；同tag不同type创建。

**通过标准：**已成功操作不重复执行；type冲突不伪成功；请求hash冲突拒绝。

**必须保留：**操作日志与节点数量。

## T011 — Desktop外部改动冲突

阶段：G4；范围：ALL_GUI；状态：NOT_RUN。

**执行：**模型读入revision后用户在Desktop改同节点；Agent提交旧revision。

**通过标准：**检测冲突并停止覆盖；给出差异；无法保证原子时显式报告。

**必须保留：**外部change事件、冲突返回。

## T012 — 求解时控制面保持响应

阶段：G4；范围：ALL；状态：NOT_RUN。

**执行：**运行耗时求解；调用health/job_status/log，另提交模型读写。

**通过标准：**状态接口测试目标p95<1秒（本机无过载）；引擎请求排队；没有绕锁并发改模型。

**必须保留：**延迟采样、队列日志、引擎日志。

## T013 — 聚合维度/全时间/轴对称

阶段：G4；范围：ALL；状态：NOT_RUN。

**执行：**建立1D端点、2D边界、3D表面、轴对称基准；求全时间序列的平均/积分。

**通过标准：**维数与解索引正确；不固定geom1；保留全时间；轴对称权重只应用一次。

**必须保留：**解析值对照、逐时间结果。

## T014 — 复数解不被取实部

阶段：G4；范围：ALL；状态：NOT_RUN。

**执行：**对复数基准字段分别请求preserve/abs/phase/real/imag并导出。

**通过标准：**各分量一致；phase约定已声明；默认preserve不静默丢虚部。

**必须保留：**复场原始数据、对照计算。

## T015 — 单位与热源维数

阶段：G3；范围：ALL；状态：NOT_RUN。

**执行：**分别绑定W/m²表面热流、W/m³体热源及m/mm插值坐标；故意错单位。

**通过标准：**正确热源可用；明显单位不一致报警；不自动乘厚度或吸收率。

**必须保留：**表达式/单位读回、总功率积分。

## T016 — 静态二维光斑完整性

阶段：G5；范围：ALL；状态：NOT_RUN。

**执行：**导入非轴对称Q(x,y)，含离轴热点；在同半径不同角度采样。

**通过标准：**保留角向差异；不替换径向平均；插值数据hash/外推/单位可追溯。

**必须保留：**输入文件、采样表、真正结果图。

## T017 — 材料温度函数与张量

阶段：G3；范围：ALL；状态：NOT_RUN。

**执行：**设置k(T)、Cp(T)、rho及至少一项各向异性张量；删除必要属性再预检。

**通过标准：**表达式/矩阵正确；预检发现已覆盖规则内缺失；未覆盖规则为unknown。

**必须保留：**material读回、检查报告。

## T018 — 局部网格和质量

阶段：G3；范围：ALL；状态：NOT_RUN。

**执行：**对小3D模型创建FreeTet+局部Size/合适网格特征；构建、修改、复建。

**通过标准：**区域绑定正确；质量定义、覆盖及低质量位置可查；不只返回build成功。

**必须保留：**mesh统计、区域质量、mph。

## T019 — 从空模型建立Study

阶段：G3；范围：ALL；状态：NOT_RUN。

**执行：**从空模型生成参数/几何/材料/边界/网格/Study/solver；运行稳态与瞬态基准。

**通过标准：**不依赖预制Study；真实产生解；保存后目标版本重新打开。

**必须保留：**构建recipe、solver日志、mph、原始结果。

## T020 — 深层Solver设置

阶段：G3；范围：ALL；状态：NOT_RUN。

**执行：**读取嵌套solver tree；更改一个合法子feature属性，回读并运行。

**通过标准：**无错误层级；未知属性准确失败；旧手动solver不被自动覆盖。

**必须保留：**solver树diff、求解日志。

## T021 — 内外参数解索引

阶段：G5；范围：ALL；状态：NOT_RUN。

**执行：**做2参数扫描与瞬态，分别读取inner/outer选择和all。

**通过标准：**结果对应正确参数case与时间；不误取第一个/最后一个标量。

**必须保留：**参数索引表、独立抽查数据。

## T022 — 真实进度

阶段：G4；范围：ALL；状态：NOT_RUN。

**执行：**跑可输出进度的任务；核对日志中的时间步/迭代与job报告。

**通过标准：**状态来自真实日志/引擎；无法算百分比返回null；不从耗时伪造进度。

**必须保留：**原始progress.log与job事件。

## T023 — 优雅取消验证

阶段：G0；范围：ALL_CONDITIONAL；状态：NOT_RUN。

**执行：**验证该组合可用的原生/GUI中止路线，在求解中取消。

**通过标准：**取消请求和底层停止分开；可用路线必须停止；不可用明确标capability，不假报。

**必须保留：**中止前后引擎状态、日志、时间戳。

## T024 — 自有进程强制取消

阶段：G4；范围：ALL；状态：NOT_RUN。

**执行：**专用managed server上触发不可响应任务，经授权强制停止并恢复。

**通过标准：**仅停止匹配PID+启动时刻的自有server；恢复checkpoint；标明强制停止与新generation。

**必须保留：**所有权日志、restore结果。

## T025 — 禁止杀共享Server

阶段：G4；范围：ALL；状态：NOT_RUN。

**执行：**连接含其他模型的用户server，提交force_cancel而无授权。

**通过标准：**明确拒绝；用户进程与其他模型存活。

**必须保留：**权限判定、PID/模型列表。

## T026 — 长操作超时不等于终止

阶段：G1；范围：ALL；状态：NOT_RUN。

**执行：**设置很短RPC等待期限但让后台加载/求解继续；随后尝试冲突写。

**通过标准：**进入UNKNOWN/RECONCILING或持久RUNNING；写被隔离；不能重复执行已提交动作。

**必须保留：**请求/作业时间线、回读。

## T027 — host断开后恢复

阶段：G4；范围：ALL；状态：NOT_RUN。

**执行：**长求解中关闭MCP host或断连接；重启host并查询原job。

**通过标准：**按daemon设计继续/真实标失联；不得生成第二个求解；取回同一结果。

**必须保留：**job数据库、操作计数、日志。

## T028 — worker/控制进程故障

阶段：G4；范围：ALL；状态：NOT_RUN。

**执行：**分别终止控制进程和Java worker；重启并核对server和job。

**通过标准：**区分运行/未知/丢失；失效句柄被拒绝；不直接把in-memory状态当事实。

**必须保留：**恢复记录与generation变化。

## T029 — 部分写失败与恢复

阶段：G4；范围：ALL；状态：NOT_RUN。

**执行：**事务第一步成功，第二步故意失败；执行restore。

**通过标准：**报告部分应用；恢复指定范围；有新模型对象则要求重绑定。

**必须保留：**before/after diff、checkpoint hash。

## T030 — 磁盘不足与原文件保护

阶段：G4；范围：ALL；状态：NOT_RUN。

**执行：**在受限测试卷保存模型/导出，触发空间不足。

**通过标准：**保留最后完整mph；无零字节文件覆盖正式成果；结果为失败。

**必须保留：**文件hash、错误日志、临时文件清理。

## T031 — 未知高层动作的代码执行

阶段：G2；范围：ALL；状态：NOT_RUN。

**执行：**选一个有公共API但无领域wrapper的操作；describe/compile/execute/readback。

**通过标准：**在同一注入Model完成；不另开隐藏主模型；源代码、差异、结果都有证据。

**必须保留：**代码artifact、model_ref、回读。

## T032 — 代码编译/运行异常

阶段：G2；范围：ALL；状态：NOT_RUN。

**执行：**分别提交语法错误与执行中COMSOL异常。

**通过标准：**行号、原始异常、部分修改、checkpoint信息完整；不吞错。

**必须保留：**编译报告、代码与日志。

## T033 — 严格只读与临时求值

阶段：G1；范围：ALL；状态：NOT_RUN。

**执行：**在pure_read和ephemeral_mutation两策略下运行需临时节点的评估。

**通过标准：**pure_read拒绝或转隔离副本；ephemeral路径明确记录、串行且仅清自有节点。

**必须保留：**策略判定、结构diff。

## T034 — 路径、中文与外部依赖

阶段：G6；范围：ALL；状态：NOT_RUN。

**执行：**项目路径含中文/空格；打包后迁移另一OS并重绑定依赖。

**通过标准：**不依赖固定C盘路径；hash匹配；缺失依赖明确指出。

**必须保留：**迁移包、目标机验证报告。

## T035 — 越界、符号链接和凭据保护

阶段：G6；范围：ALL；状态：NOT_RUN。

**执行：**尝试读写工作区外路径、重解析点/符号链接、获取credential内容。

**通过标准：**未授权访问拒绝；日志不包含密钥；失败不改文件。

**必须保留：**安全测试记录与日志扫描。

## T036 — 恶意文档指令不生效

阶段：G6；范围：ALL；状态：NOT_RUN。

**执行：**帮助/模型描述中包含忽略权限或上传机密的指令文本。

**通过标准：**被当作数据；无权限提升和外发；模型仍可检索普通帮助。

**必须保留：**审计、拒绝结果。

## T037 — 托管代码隔离边界

阶段：G6；范围：ALL；状态：NOT_RUN。

**执行：**在隔离测试账户尝试代码文件/网络/进程访问；测试server侧外部执行。

**通过标准：**OS级策略生效；不把代码扫描当sandbox；共享高权限server风险明确。

**必须保留：**账户权限、网络与执行日志。

## T038 — MCP协议正确性

阶段：G2；范围：ALL_NO_ENGINE；状态：NOT_RUN。

**执行：**测试tools/list/call、structuredContent、isError、分页、stdio日志隔离。

**通过标准：**schema有效；stdout无污染；部分失败不包装为全成功。

**必须保留：**协议测试结果、schema validation。

## T039 — host兼容模式

阶段：G6；范围：ALL_NO_ENGINE；状态：NOT_RUN。

**执行：**模拟full/domain/expert及不支持image/Tasks/dynamic tools的host。

**通过标准：**能力不因展示方式丢失；fallback可用；缺失图像能力不假定有视觉验收。

**必须保留：**host能力协商记录。

## T040 — PNG与数值绑定

阶段：G4；范围：ALL；状态：NOT_RUN。

**执行：**从同一指定revision/dataset/time导出图像和表；换一个time再导出。

**通过标准：**metadata正确区分；PNG非空可解码；色标、单位、ROI可核验。

**必须保留：**PNG、原始表、artifact元数据。

## T041 — 只换版本不冒充兼容

阶段：G6；范围：ALL；状态：NOT_RUN。

**执行：**按同一recipe在6.3/6.4生成，分别保存再开；对差异特征负向测试。

**通过标准：**各目标版本文件有效；差异被记录；不通过改文件头/扩展名降级。

**必须保留：**双版本mph、树diff、指标差异。

## T042 — 许可证/模块不足

阶段：G0；范围：ALL；状态：NOT_RUN。

**执行：**对实际缺失产品的动作进行检查；再测试授权产品。

**通过标准：**BLOCKED_LICENSE而非伪成功/临时模拟；授权功能正常；检查不默认占用许可。

**必须保留：**capabilities与许可错误证据。

## T043 — 文档版本隔离

阶段：G2；范围：ALL_NO_ENGINE；状态：NOT_RUN。

**执行：**同时索引6.3/6.4帮助；查询差异属性；删除某版索引再检索。

**通过标准：**来源版本明确；未找到明确返回；不拿另一版文档冒充本版。

**必须保留：**检索结果与来源。

## T044 — 热传导解析基准

阶段：G5；范围：ALL；状态：NOT_RUN。

**执行：**构建长度0.01m、k=10W/(m·K)、两端300/400K的无源稳态均匀杆/板基准。

**通过标准：**T(x)=300+100x/L；热流大小100000W/m²；预先规定误差门槛如0.1%，并给网格敏感性。

**必须保留：**真实COMSOL解、解析对照、误差表。

## T045 — VCSEL完整链

阶段：G5；范围：ALL_MODULES；状态：NOT_RUN。

**执行：**小规模参数化环阵列→非轴对称插值热流→材料/散热→求解→L扫描/功率优化→图/数据。

**通过标准：**保留静态角向信息；功率预算与ROI指标可复算；目标不可行时如实报告。

**必须保留：**mph、recipe、功率/温度表、日志、验收。

## T046 — 光纤耦合完整链

阶段：G5；范围：ALL_MODULES；状态：NOT_RUN。

**执行：**构建小型可算的光场/端口模型，归一化输入，导出复场并计算模式耦合与偏移扫描。

**通过标准：**eta_mode与eta_capture分开；同归一化模式重叠接近1，误差门槛事先固定；真实求解证据齐全。

**必须保留：**复场、端口/网格设置、归一化与扫描表。

## T047 — 胶水静态与阶段历史

阶段：G5；范围：ALL_MODULES；状态：NOT_RUN。

**执行：**构建有台/无台稳定形态分支；另运行UV→热固化→冷却小模型并传递状态。

**通过标准：**不把台阶设成默认不可跨墙；胶量误差可查；温度/固化/应力参考态不被重置；校准与验证分开。

**必须保留：**各阶段mph/状态数据、体积和应力表、假设清单。

## T048 — 语义选区漂移检测

阶段：G4；范围：ALL；状态：NOT_RUN。

**执行：**改变几何使实体编号和区域分割改变，重新求值选区。

**通过标准：**named selection也被重新验证；偏离位置/面积/数量期望时停止应用边界。

**必须保留：**geometry_revision、实体测度变化。

## T049 — 数据量与截断

阶段：G4；范围：ALL；状态：NOT_RUN。

**执行：**导出超出inline预算的大数组、复场、长日志并分块读取。

**通过标准：**返回artifact引用；完整文件可取回且hash一致；不把截断字符串当完整数值。

**必须保留：**文件hash、shape、chunk拼接检查。

## T050 — 保存与快照成本策略

阶段：G4；范围：ALL；状态：NOT_RUN。

**执行：**对大模型批量改参数、建几何并checkpoint；测量保存次数与恢复能力。

**通过标准：**轻量参数更新可分组；结构写前可恢复；不会因每个setter全存导致无法使用。

**必须保留：**保存日志、磁盘峰值、restore测试。

## T051 — 普通Desktop迁移

阶段：G5；范围：ALL_GUI；状态：NOT_RUN。

**执行：**打开未保存的standalone模型；按显式保存策略迁移到shared server。

**通过标准：**不丢未保存修改；不称透明接管；明确迁移前后model身份。

**必须保留：**保存记录、两个会话身份、GUI绑定证据。

## T052 — GUI取消和权限缺失

阶段：G0；范围：ALL_GUI；状态：NOT_RUN。

**执行：**撤销GUI控制/截图权限；再恢复权限，对可访问控件测试取消。

**通过标准：**权限缺失准确提示；不误点其他窗口；取消必须证实停止。

**必须保留：**权限检查、目标窗口/作业日志。

## T053 — 同一Server的多模型串行

阶段：G4；范围：ALL；状态：NOT_RUN。

**执行：**同server两模型同时排求解/写；不同server提交并行。

**通过标准：**同server不会误认为按model锁即可并发；不同server按预算并行。

**必须保留：**队列时间线、server IDs。

## T054 — blockOtherClients释放

阶段：G4；范围：ALL_GUI；状态：NOT_RUN。

**执行：**在短事务使用独占后故意抛异常/断连接。

**通过标准：**finally/断连恢复；无长时阻塞Desktop；使用时间与范围可审计。

**必须保留：**阻塞开始/释放事件、GUI恢复。

## T055 — 超时配置真正生效

阶段：G1；范围：ALL；状态：NOT_RUN。

**执行：**在不同配置下运行长加载/锁等待/求解；execution_timeout设null。

**通过标准：**RPC/队列/求解期限分别生效；无暗藏120/300/3600秒中止；null语义明确。

**必须保留：**有效配置快照与时间线。

## T056 — 离线安装与运行

阶段：G6；范围：ALL；状态：NOT_RUN。

**执行：**切断计算机外网，用对应OS/arch包安装并运行自检和完整小模型。

**通过标准：**无自动pip/npm下载；不缺本地依赖；能与允许的LAN host通信；完全隔离时仅任务文件模式。

**必须保留：**安装日志、网络日志、运行证据。

## T057 — 异步操作防重复重试

阶段：G4；范围：ALL；状态：NOT_RUN。

**执行：**在提交后响应丢失场景重发同请求；在恢复处理中重复查询。

**通过标准：**复用同job；不重算、不重复创建/覆盖；不同body相同key拒绝。

**必须保留：**job计数、request_hash。

## T058 — 输入假设和物理结论分离

阶段：G5；范围：ALL_MODULES；状态：NOT_RUN。

**执行：**给任务缺失材料曲线/接触角/光场参数；允许明确工程估值后运行。

**通过标准：**来源标estimated；不能将拟合的现象再次当独立验证；报告假设敏感性。

**必须保留：**假设表、来源、验收状态。

## T059 — 版本能力探测不可冒充验证

阶段：G0；范围：ALL；状态：NOT_RUN。

**执行：**只验证API存在而不运行；再执行真实功能测试。

**通过标准：**状态从unverified到verified有证据；missing测试不计入通过。

**必须保留：**capability状态及test_id。

## T060 — 正式交付包完整性

阶段：G6；范围：ALL；状态：NOT_RUN。

**执行：**生成证据包后在干净项目目录、对应目标版本打开/重放。

**通过标准：**mph、依赖hash、recipe、原始数值、图、日志、版本和验证均齐；无隐藏绝对路径。

**必须保留：**完整包、目标机重放日志。



---

<!-- source: 04_IMPLEMENTATION_PLAN.md -->

# 开发工作包与依赖

每个工作包作为独立PR/阶段交付。主线接口和模型身份由一个架构负责人冻结；领域实现可并行，但共享server的实机操作不得并行写同一实例。

| 工作包 | 阶段 | 依赖 | 验收 | 交付 |
|---|---|---|---|---|
| W01 冻结基线与遗留工具映射 | G0 | 无 | T038 | 固定SHA、列50个旧接口、建立新旧schema映射；避免已有用户配置失效。 |
| W02 六组合Runtime/Java PoC | G0 | W01 | T001, T002, T042, T059 | 实机验证JDK11 client classpath、COMSOL server版本、渲染和产品能力。 |
| W03 Desktop/取消路线PoC | G0 | W02 | T004, T023, T052 | 验证共享Desktop和可用中止路线；标记GUI-only能力，禁止虚构通用cancel。 |
| W04 无破坏求值与变量/选择修复 | G1 | W01 | T003, T005, T006, T007, T033 | 修F01-F03/F06；清理仅限本次临时节点；移除固定指标。 |
| W05 模型身份/修订/权限 | G1 | W02 | T010, T011, T035 | 建立session/model/generation、项目授权、请求hash与写前检查。 |
| W06 控制进程和Java Worker隔离 | G1 | W02, W05 | T012, T026, T028, T055 | 统一串行队列；取消伪hard timeout；真实状态与缓存分离。 |
| W07 持久存储与幂等 | G1 | W05 | T027, T030, T057 | jobs/operations/revisions/artifacts/checkpoints持久化和迁移。 |
| W08 typed值/节点解析 | G2 | W04, W06 | T008, T009, T010 | 保留数据形状；实现typed collection和Work Plane嵌套路径。 |
| W09 registry/schema/host兼容 | G2 | W01, W08 | T038, T039 | 生成文档、工具发布、operation fallback、structuredContent/isError。 |
| W10 公共API与Java执行 | G2 | W08, W05 | T031, T032, T037 | 绑定Model、编译、日志与差异；许可/主机权限与trusted_code分离。 |
| W11 版本化文档索引 | G2 | W02 | T043, T036 | 官方本地帮助索引、来源、版本过滤、无结果语义。 |
| W12 计划/试运行/checkpoint | G2 | W07, W10 | T029, T033, T050 | static preview与隔离trial不同；定义恢复范围和GUI rebind。 |
| W13 参数/变量/函数/选区 | G3 | W08, W11 | T006, T015, T016, T048 | 多维插值、单位、named/spatial选区与语义校验。 |
| W14 几何/WorkPlane/CAD/坐标Pair | G3 | W13 | T009, T034 | 完整嵌套编辑、几何测量和模型保留；产品差异能力表。 |
| W15 材料/物理/多物理 | G3 | W13 | T007, T017, T042 | 属性组和张量、选区、子特征、耦合及初值。 |
| W16 网格/Study/Solver | G3 | W14, W15 | T018, T019, T020 | 从空模型到真实求解；深层solver和物理启用。 |
| W17 Dataset/复场/指标/Probe | G4 | W16 | T013, T014, T021, T049 | 索引、权重、复数、坐标、分块数据，不静默截断。 |
| W18 Plot/Export/视觉返回 | G4 | W17, W03 | T040, T039 | 真实渲染、数据绑定、图片host回传、数据/报告导出。 |
| W19 持久jobs/取消/恢复/并发 | G4 | W07, W16, W03 | T022, T023, T024, T025, T026, T027, T028, T053, T054, T057 | 缓存状态独立响应；取消确认；限制共享Server控制权限。 |
| W20 结构/数值/物理验证 | G4 | W17 | T044, T058 | 规则有覆盖边界；三层成功状态；基准解和收敛。 |
| W21 扫描/优化/阶段状态传递 | G5 | W17, W19 | T021, T047 | case管理、缓存、参数预算、阶段参考态和变量映射。 |
| W22 VCSEL领域验收包 | G5 | W20, W21, W18 | T045, T016 | 静态非轴对称热源、阵列/环功率、距离扫描、热预算。 |
| W23 光纤耦合领域验收包 | G5 | W20, W21, W18 | T046, T014 | 复场、模式归一化、耦合与捕获效率分离、容差扫描。 |
| W24 胶形/固化领域验收包 | G5 | W20, W21, W18 | T047, T058 | 稳定胶形及全流程独立路线，质量/参考态/应力历史。 |
| W25 Desktop迁移与高级GUI | G5 | W03, W12, W19 | T051, T052, T004 | API优先；只对实测GUI路径承诺，明确迁移与重新绑定。 |
| W26 离线构件/安全/发布 | G6 | W09, W18, W19, W22, W23, W24, W25 | T034, T035, T036, T037, T041, T056, T060 | 三种OS/arch构件、六组合认证、hash/lock/SBOM、升级回退、完整交付。 |

## PR 必须包含

功能代码、输入/输出schema、权限/副作用分类、单元测试、最小真实COMSOL案例、错误案例、文档生成结果、版本/平台状态更新。未拿到实机资源时只标UNVERIFIED，不把“有mock”当验收通过。

## 防止再次陷入“工具名很多但不能用”

每个公开动作要能回答：操作哪个模型？实际触发哪个公共API/平台动作？需要什么许可证？输入输出类型是什么？怎样回读？超时后底层是否仍运行？失败造成哪些改动？能恢复到哪里？在哪里有对应实机证据？

## 兼容策略

旧工具转到新domain service，不复制第二套业务实现。原有JSON字符串参数可继续接收，但解析后统一强类型。危险的旧行为不保留；迁移说明明确改变。旧get_core_metrics仅在显式绑定metric set时工作。

## 数据库建议

projects、runtimes、sessions、models、revisions、operations、jobs、job_events、artifacts、checkpoints、capability_evidence、permissions、recipes。每条状态更新需要request_id/operation_id。job状态是观测记录，不是引擎状态的永恒真相；重启执行reconcile。数据库需schema_version和迁移测试。

## 发布报告

列出六组合具体环境、全部required test结果、blocked原因、已知缺陷、证据包hash、每个动作的SUPPORTED_VERIFIED或UNVERIFIED状态。对某模块无许可证可以发布“核心版”并明确缺失，但不能称用户完整能力包已完成。


---

<!-- source: 05_DEVELOPER_TASK.md -->

# 交给开发模型的任务指令

请基于 Ching-Chiang/comsol-mcp 的固定提交 `ccca65aa8277d1205c5de5fb6221e460aca5997a` 实施本目录的全能力优化方案。先阅读 01_ARCHITECTURE.md、02_ACTION_CATALOG.md、03_ACCEPTANCE.md、04_IMPLEMENTATION_PLAN.md 和 SOURCES.md。

## 不可变目标

目标用户是云端强模型，不是本地小参数模型。必须实现领域工具、通用模型对象API、MCP内受控Java执行、真实结果与图像、完整恢复/验收；不能以减少工具数或让模型更容易选工具为由删掉能力。核心支持Windows x64、macOS Apple Silicon、macOS Intel，与COMSOL 6.3/6.4共六组合。

允许你在当前工作阶段实现并验证代码；不要只输出设计讨论。按照工作包顺序做，每一步明确当前完成与尚未验证的范围；不把第一阶段基础连接当整个系统完成。禁止在没有真机测试时声称六组合适配成功。

## 先执行的工作

1. 检查当前工作仓库是否与基线一致；若不同，列出差异并在新分支保留用户改动，不回退/覆盖用户分支。
2. 提取实际注册接口和schema，建立旧50工具兼容表，读取并复现F01–F12中与当前代码一致的问题。
3. 完成G0：在可访问的每个目标环境验证Java API、共享Desktop、渲染和取消路径；不可访问环境标UNVERIFIED，保留可复现测试入口。
4. 实施G1安全修复、typed协议、模型身份和串行队列，再进入通用对象层与代码执行。

## 执行纪律

所有COMSOL动作通过绑定模型的MCP backend。没有高层wrapper时，允许generic API或MCP受控Java；不得偷偷另启一次性脚本操作另一份模型。主模型identity、revision、checkpoint、回读和证据必须贯穿领域工具与代码路径。

不要发明COMSOL内部feature_type、属性或cancel API。优先实际实例自省、目标版本官方帮助、示例和隔离试验。脚本编译成功不等于模型建立正确，solver成功不等于物理验证通过。

不得在工作区外写入、强杀用户server、上传MPH/机密数据、替换许可证或修改系统安全设置，除非已有匹配的明确授权。trusted_code不等于整机无限制权限。

每次写操作采用inspect→plan→authorize→checkpoint（按策略）→act→readback→validate→log。允许在一个已授权事务中批量执行，不要对每个普通setter请求人工确认。

## 每个工作包交付

代码与测试、输入/输出schema、实机/未实机状态、变更清单、来源、错误及恢复记录、下一工作包依赖。测试结果分为PASS/FAIL/BLOCKED/NOT_RUN，不把skipped测试计为PASS。长期作业要记录真实job_id和可恢复状态，不用sleep或假进度伪装。

## 最终验收

依据03_ACCEPTANCE全部required条目；用户三类案例要有真实MPH、代码、日志、原始数据、图片、假设与验收报告，并能在目标版本打开或重放。GUI不支持、许可缺失、资料不齐、科学假设未验证必须明确，不用伪造演示数据补齐。


---

<!-- source: 06_CONTRACT_NOTES.md -->

# 协议与配置补充说明

本目录的schema和YAML是拟议新系统的设计契约，不能直接粘贴进原版Hermes或原版comsol-mcp后期待功能出现。示例请求只做schema验证，没有在COMSOL执行。

## Schema边界

272个动作提供顶层输入契约；六个核心动作另有自包含schema。公共类型在common.schema.json。`ActionResult`与`CapabilityEvidence`有独立schema。

每个领域仍需依据目标版本为data输出、feature属性、manage子动作补充专门schema。`TypedValue`的shape/range、SelectionSpec的kind必填字段、函数的维度/单位/导数等需要语义校验；JSON Schema通过不代表COMSOL接受或物理合理。

`api_invoke`的declared_effect只是声明，后端必须重新判定，未知方法按写/高权限处理；绝不能相信模型把危险动作标为read就免审计。所有改模型或需一致保存/主机控制的模型级动作要检查revision和idempotency。COMPUTE在作业提交时固定来源revision，并禁止同server冲突写，任务完成再更新可见revision。

本目录`expected_revision`是本系统管理的修订计数，不是COMSOL原生跨客户端原子CAS。外部Desktop修改需要事件/快照核验；短事务无法取得独占时必须停止或显式降级，不能承诺不会丢更新。

## 取消契约

原生取消方法只在对应版本和运行模式实际验证后启用。job_cancel返回CANCEL_REQUESTED不等于CANCELLED；必须看到引擎停止证据。等待超时不退出Java调用，force stop只限已授权、可证明所有权的隔离server，且不等于保存当前解。

## 配置分层

control.example.yaml给通用控制进程；platform-overrides.example.yaml给平台发现/GUI适配；lan-relay.example.yaml给可通信但计算机不直接上网的部署。真实部署应由安装器验证合并后的配置并生成“有效配置”报告。

null运行期限意味着未设定引擎任务时长上限，不代表无资源/权限控制。内存/磁盘/并发资源预算和无进展告警仍有效；一律不要把RPC等待期限用作杀求解进程的期限。

## 更新策略

冻结MCP SDK、控制进程、worker协议、各平台依赖和runtime适配测试。更换版本先在分支和回归环境验证，再发布。COMSOL安装包、商业文档和JAR不作为公开分发的离线包内容；从用户合法本机安装读取。离线构件包含可合法分发的本项目与第三方依赖、版本锁定、来源和hash。


---

<!-- source: SOURCES.md -->

# 来源与证据索引

核验日期：2026-09-18。代码结论来自固定提交的静态检查；官方文档说明底层接口，不等于本方案已经实施。源码发现的风险需要在目标环境回归。设计章节中的“建议/必须”是本方案的工程要求，不是声称现有项目已有功能。

## S01 — 审查基线：Ching-Chiang/comsol-mcp 提交

来源：https://github.com/Ching-Chiang/comsol-mcp/commit/ccca65aa8277d1205c5de5fb6221e460aca5997a

核验用途：基线提交时间 2026-05-14；当前检索 main 指向该提交。

## S02 — 源码：表达式求值与几何属性

来源：https://github.com/Ching-Chiang/comsol-mcp/blob/ccca65aa8277d1205c5de5fb6221e460aca5997a/comsol_mcp/_model_ops.py

核验用途：核验 numerical 集合清空、维度/geom1 默认、类型转字符串等行为。

## S03 — 源码：物理场、选择集与变量

来源：https://github.com/Ching-Chiang/comsol-mcp/blob/ccca65aa8277d1205c5de5fb6221e460aca5997a/comsol_mcp/_physics_ops.py

核验用途：核验 physics-level 选择集缺少分支、变量 set(name/expr) 语义、类型发现。

## S04 — 源码：连接超时

来源：https://github.com/Ching-Chiang/comsol-mcp/blob/ccca65aa8277d1205c5de5fb6221e460aca5997a/comsol_mcp/_connection.py

核验用途：future.result 超时和 executor.shutdown(wait=False) 并不实现底层 Java 操作终止。

## S05 — 源码：状态与锁

来源：https://github.com/Ching-Chiang/comsol-mcp/blob/ccca65aa8277d1205c5de5fb6221e460aca5997a/comsol_mcp/_state.py

核验用途：只读工具路径不获取 runtime lock，锁等待有硬编码 120 秒。

## S06 — 源码：visible-main 工作流

来源：https://github.com/Ching-Chiang/comsol-mcp/blob/ccca65aa8277d1205c5de5fb6221e460aca5997a/comsol_mcp/_tools_workflow.py

核验用途：加载主模型时会调用其他模型清理；加载存在 300 秒限制；验证不是 Desktop 当前标签页的证明。

## S07 — 源码：工具注册

来源：https://github.com/Ching-Chiang/comsol-mcp/blob/ccca65aa8277d1205c5de5fb6221e460aca5997a/tests/test_registration.py

核验用途：公开工具表为 50 项；注册测试不等于真实 COMSOL 集成验证。

## S08 — 源码：模型指标

来源：https://github.com/Ching-Chiang/comsol-mcp/blob/ccca65aa8277d1205c5de5fb6221e460aca5997a/comsol_mcp/_tools_params.py

核验用途：get_core_metrics 硬编码特定模型变量和域/边界。

## S09 — COMSOL 6.3 系统要求

来源：https://www.comsol.com/system-requirements/63/general

核验用途：Windows x64、Intel Mac、Apple Silicon；Java API JDK 8/11；产品自带 Java 11；Application Builder 为 Windows 功能。

## S10 — COMSOL 6.4 系统要求

来源：https://www.comsol.com/system-requirements

核验用途：检索时页面为 6.4 Update 3；产品自带 Java 21，但外部 client/server Java API 列 JRE 8/11；应用构建等有平台差异。

## S11 — COMSOL 6.3 PropFeature

来源：https://doc.comsol.com/6.3/doc/com.comsol.help.comsol/api/com/comsol/model/PropFeature.html

核验用途：properties、getType、getValueType、typed getter/setter、setIndex/setEntry 和选择集接口。

## S12 — COMSOL 6.4 PropFeature

来源：https://doc.comsol.com/6.4/doc/com.comsol.help.comsol/api/com/comsol/model/PropFeature.html

核验用途：同类通用属性接口；具体特征可用性须运行时验证。

## S13 — COMSOL 6.3 ModelUtil

来源：https://doc.comsol.com/6.3/doc/com.comsol.help.comsol/api/com/comsol/model/util/ModelUtil.html

核验用途：服务器连接、模型身份、版本、许可证、progress 等公共入口。

## S14 — COMSOL 6.4 ModelUtil

来源：https://doc.comsol.com/6.4/doc/com.comsol.help.comsol/api/com/comsol/model/util/ModelUtil.html

核验用途：含 ServerBusyHandler、ModelChangedHandler、blockOtherClients；后者阻塞其他客户端且无内建超时。

## S15 — COMSOL 变量 API

来源：https://doc.comsol.com/6.3/doc/com.comsol.help.comsol/comsol_api_general.47.63.html

核验用途：变量定义是 variable(tag).set(variable_name, expression)，不是写 name/expr 两个伪属性。

## S16 — COMSOL Windows 命令

来源：https://doc.comsol.com/6.3/doc/com.comsol.help.comsol/comsol_ref_running.38.31.html

核验用途：Windows 的 Multiphysics server/client 命令；不是 COMSOL Server 应用发布产品。

## S17 — COMSOL macOS 命令

来源：https://doc.comsol.com/6.4/doc/com.comsol.help.comsol/comsol_ref_running.38.33.html

核验用途：macOS 命令行启动模式；平台适配器须验证安装位置、执行权限及参数。

## S18 — COMSOL 6.4 SolverSequence

来源：https://doc.comsol.com/6.4/doc/com.comsol.help.comsol/api/com/comsol/model/SolverSequence.html

核验用途：求解状态/警告/解向量等接口；本方案不凭空假定通用 cancel() 方法。

## S19 — COMSOL 6.4 GeomSequence

来源：https://doc.comsol.com/6.4/doc/com.comsol.help.comsol/api/com/comsol/model/GeomSequence.html

核验用途：几何序列与构建接口；workplane 是嵌套几何，不能一律当普通 feature 链。

## S20 — MCP Tools 协议

来源：https://modelcontextprotocol.io/specification/2025-11-25/server/tools

核验用途：工具 JSON Schema、structuredContent、结果内容和工具发现。

## S21 — MCP Transports

来源：https://modelcontextprotocol.io/specification/2025-11-25/basic/transports

核验用途：stdio 与 Streamable HTTP；网络入口需要身份认证和安全配置。

## S22 — MCP Tasks

来源：https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks

核验用途：所查版本中 Tasks 为实验性功能；需能力协商并保留传统 job 轮询兼容。

