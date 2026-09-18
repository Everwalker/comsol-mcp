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
