# G3.6 — Windows 原生双版本适配与 W19 定向修补

## 0. 本轮任务、事实和授权边界

用户要求：在已安装 **COMSOL 6.3 和 6.4 的 Windows 电脑**上开始适配，两者都需要工作；用户将清理旧项目文件但保持联网。本工作包是唯一项目交接入口，不依赖旧工作目录、旧聊天、Mac 私有分支、旧 token、旧进程或未上传的 tar/bundle。软件安装、合法许可证、用户未提交的科学数据不是仓库内容，不能假定能从本包恢复。

本轮由 **Windows 上的 Agent 直接开发和原生验收**，不需要先连 Mac、SSH 或让 Mac 作为唯一集成端。这是本轮对旧 Mac-only 开发节奏的明确调整。共享核心代码保持同一份，不创建 6.3/6.4 两套业务实现，不破坏 Mac 架构；无法访问 Mac 时不得将新改动声称为 Mac 实机 VERIFIED。

源基线：`PIN.json` 的 `3835ab858a4e7a54fd3b1fb901dca04c9ee9ac19`，tree `634389371381c179406e06c421a6e43a1f632322`。该版本报告 G3.5/W19 的 Mac 限定范围完成；最新台账明确 native cancel 与云端图像接收仍有限制。不要把历史完成报告当作当前每条路径都正确，也不要重做已有且经证据支持的全部 G0–G3 工作。

本轮完成：恢复 → 定向修复 → Windows 生命周期/隔离 → 6.4 和 6.3 原生核心链路 → 双版本切换/隔离 → 正常交付。**不启动 W20 新验证产品功能，也不扩展 W21–W26 的领域功能**；本轮所必需的测试、安装、打包和修复属于适配基础设施，不受此边界禁止。

## 1. 先读，再行动

先读包根 `START_HERE.md`、`REVIEW.md`、`WINDOWS_PLAN.md`、`ENVIRONMENT.md`、`ACCEPTANCE.md` 和 `PIN.json`。

运行包校验后，通过 `tools/bootstrap.py` 恢复源；Git 可用时使用 Git，不可用时可用严格校验的 HTTPS archive。不允许降级成下载浮动 main，也不因没有 Git 就暂停全部准备。恢复目标默认 `repository/`，已有目录绝不自动覆盖。

恢复后读取：
- 当前仓库 `AGENTS.md`、`CLAUDE.md`、根进度与 `docs/comsol_mcp_design_v1/PROGRESS.md`；
- `05_DEVELOPER_TASK.md`、`01_ARCHITECTURE.md`、`04_IMPLEMENTATION_PLAN.md`、`03_ACCEPTANCE.md`、`06_CONTRACT_NOTES.md`（都在上述设计目录）；
- `evidence/g3_5_acceptance.json`、最新运行目录及 `evidence/windows_control_lifecycle/20260919/`；
- 本包复制到 `docs/handoff_g3_6_windows/` 的说明。

本轮用户任务改变了平台推进顺序，但不改变源规范里的模型身份、数据真实性、许可、用户模型保护和公开 API 原则。仓库内过期“只在 Mac 工作”“停在 W19、不许任何平台工作”的阶段文字应作有记录的更新；不得篡改历史证据。已有未提交用户修改不得覆盖。

## 2. 最终交付定义

同一份已冻结修复源码，应在本机原生 Windows x64 上产生两个独立结论：

1. `WINDOWS_COMSOL_63_CORE_VERIFIED_SCOPED`；
2. `WINDOWS_COMSOL_64_CORE_VERIFIED_SCOPED`。

每个结论必须附实际 Windows 版本、架构、Python、外部 JDK、COMSOL 完整 build、客户端 classpath/JAR 指纹、源码指纹、入口、测试范围与未验证项。这里的“core”覆盖本包 WD00–WD29 指定的核心 API/建模/数值/绘图/保存/任务恢复路径，不代表所有商业模块或任意 COMSOL 操作已验证。

两版本必须分别生成并打开模型：6.3 验收不能直接拿 6.4 生成的 MPH 当基础夹具；不能将 6.4 -> 6.3 的文件改名称为降级。跨版本功能比较使用同一构建定义在两引擎分别运行，并比较参数/节点语义与数值，而非要求二进制 MPH 的 SHA 相同。

## 3. 先修的生产缺口（D01–D09）

### D01：Windows 隔离证明不得绕过

`_g2_isolation.py` 当前只允许 darwin，使用 ps/lsof 且固定某次 `/Applications/COMSOL64/.../server.xml` 的目标和哈希。不要删 if 来伪装兼容。

提取共享的证明逻辑；新增/扩展平台观测适配，Windows 通过原生 API 或经验证的 PowerShell/CIM 输出取得：进程映像、PID/创建时间、用户 SID、父子/Job 归属、实际监听地址/端口、连接和运行时配置来源。无法观测时明确 UNVERIFIED/BLOCKED，不凭请求 JSON 或旧回执信任状态。

只针对本任务新建的 Server/Worker 使用 owned-runtime 证明。认证、私有目录 DACL、网络访问限制分别验证，不能将“客户端连 127.0.0.1”当作服务端仅回环监听的证明。6.3/6.4 的配置文件布局及参数在本机分别确认，不复用 Mac 硬编码配置，不修改任意共享 Server 的安装配置。

### D02：取消请求不应把 UNKNOWN 写回 RUNNING

修 `_cancel_job`：UNKNOWN/RECONCILING 状态下记录 cancel intent 后先 reconcile，保持未知语义；有可信活动证据时才更新 observed engine state。原生取消未实现时，明确 `cancel_requested=true, engine_stopped=false`，这不意味着引擎重新进入 RUNNING。

参数/权限校验在任何状态改变之前执行。对于排队任务，先做完整请求校验再原子仲裁；无效 force_stop 或伪造授权不应仍取消任务。

### D03：终态、操作结果和竞态统一仲裁

`OperationStore.update_job` 的现有保护只防止终态回到非终态；`finish` 仍可无条件覆盖状态。建立一处显式状态转移/CAS，覆盖 begin/start/cancel/finish/reconcile/monitor，而不是各层加互相矛盾的 if。

重点：排队取消赢得事务后任何晚到 callback 不得派发；取消确认后晚到异常不可把 CANCELLED 改成 FAILED；成功与取消竞态按已观测事件排序；取消请求不等于取消完成。late result 保存为事件/辅助结果，不覆盖确定终态。操作表、job 表、返回信封保持一致，不能只更新 job 状态而保留旧 operations.result。

### D04：force-stop 绑定真正的 runtime，不信任请求授权布尔

当前正向测试注入 `ManagedService(is_shared=False, server_pid=...)` 并停止 Python sleep 进程，它证明受限进程控制，不是已接通真实 COMSOL 的停机恢复。

用后端持久的 RuntimeOwnership/ServerLease（可沿用现有 receipt，不要求这个名字）绑定 job -> session -> runtime instance -> OS handle/创建时间 -> Windows Job/进程树。请求只能引用后端已授权的 lease，不能自行给出 authorized=true 就扩大权限。

替换在 core 中直接 `os.kill(pid,9)` 的做法。Windows 进程级停止经平台层执行，确认引擎实际进程和所属必要子进程退出/监听撤销后才能设置 `engine_stopped=true`。不得仅观察 launcher 已退出；不得 kill 所有 java/comsol/mphserver。状态无法确认时保留 UNKNOWN，禁止假报 CANCELLED。

两个运行时并存时，取消 A 的任务只能影响 A；B、其他 Server 和 Desktop 必须不受影响。结束后旧 ModelRef/Worker handle 失效、检查点重绑和原 job 查询都有实测证据。

### D05：版本与编译缓存必须绑定

`JavaWorkerPaths` 已有 .exe、`;` 和完整 manifest 选择逻辑，应保留。当前 Worker 的编译目录只依赖 Worker Java 源哈希：增加 COMSOL 版本/build、官方 manifest 内容与 JAR 集合指纹、JDK/vendor/major、架构、编译参数的 cache key 和 receipt。不要拷贝 Mac class 文件或 JAR；不要让 6.4 编译出的 Worker 在 6.3 中未经编译/检查即使用。

启动编译明确 UTF-8 编码与目标 JDK 兼容参数；审查 Java Compiler API 动态代码编译是否同样采用这些约定。读取 classpath manifest 保留顺序，验证来源全来自当前选中安装，不从两个版本拼凑缺失 JAR。

### D06：Windows 生命周期、权限与文件边界

复用 `_platform_process.py` 的无副作用查询与现有 Windows flags 测试，但补真实 Host Job Object、stdio 退出后持久进程存活和 breakaway 拒绝的验证。breakaway 被拒绝时，提供并实际使用外部预启动 broker/daemon 的合规路径，不撤销 Host 保护、不无限重试。

Python chmod(0700) 不能作为 Windows DACL 证明。只为任务私有目录建立最小必要 DACL；不重写用户盘根、Program Files、许可证目录、全局网络或全局 PATH。补 reparse/junction、大小写别名、ADS、UNC（未明确支持时拒绝）、锁占用及原子发布行为测试。

### D07：验收层级与真实负载

保留最新台账将 STATIC/CONTROL/NATIVE_COMSOL 分开的方向。J02 的人为插入 QUEUED 记录、J03 的 Python sleep、J05 重新打开数据库、J06 无负载计时均只能支持有限结论。

本轮必须新增：真实 native 工程任务正在计算时查询状态；生产队列上排队任务与开始竞争；真实控制进程/Host 断连或重启；原 request_id 不重复求解；两 runtime 错配拒绝。单测可以故障注入，但不能改名成 Windows COMSOL 实机 PASS。

### D08：文档/能力结论与取消策略

把“COMSOL 6.4 API 没有取消”改成“本适配器的某条取消路线已验证/未支持/未验证”。官方 Windows 文档有 batch 取消/停止选项，这不能自动外推到当前 server-side solver，也不能忽略它后断言整个 COMSOL 没有取消能力。

按 runtime/backend 分别报告 native cooperative cancel、queued cancel、owned process termination。真实原生中止不能实现时保留限制，不阻塞已授权并验证的进程级恢复路线；但不能发布为 `NATIVE_CANCEL_VERIFIED`。

### D09：源、部署、证据要对应

本轮所有核心改动从相同源快照部署到两个版本。6.3 适配导致公共核心变化后，重跑 6.4 受影响部分；不得混用各自修了一半的源码。仅文档/证据追加可通过实际源文件逐项哈希桥接，不要求把自引用证据文件也冻结成不可能满足的固定 SHA。

更新本轮 `PROGRESS.md` 时采用最新实际运行 ID，保留旧内容为历史。不要将不存在的旧本地交付包、PID 或机器路径列为新的先决条件。

## 4. 实施顺序

### Gate A：恢复 + 环境盘点 + 最小编译 PoC（两版本早测）

在新目录校验并恢复。以 `tools/windows_inventory.py` 输出为候选清单，核验 Windows/CPU/Python 位数和两个安装的实际目录。创建新的本地 x64 venv，不复制 Mac venv；已有 Windows requirements 是参考而不是当前已证明的锁。

外部 **JDK 11 x64** 是共同认证起点，需有 java、javac、javap；不要混淆 6.4 产品自带 Java 21 与外部 client-server API 要求。用户若有其他 JDK，不卸载；为本项目单独指定。网络可用于取得合法依赖/官方文档，不静默升级 COMSOL 或申请商业许可。

先在 6.4 完成发现/编译/只读版本探针，紧接着在 6.3 做同样探针，尽早暴露 API 签名差异。此时先不同时长时间运行两版本，避免许可/资源混淆。

### Gate B：共享安全修补 + Windows 受管 runtime

完成 D01–D06 与相关控制负测。分别建立私有 prefs/tmp/recovery/worker/cache/operation DB/artifact namespace。可采用两个独立 MCP/daemon profile，**不要求本轮为双版本支持推倒现有架构写大型多租户调度器**。

运行身份中含版本/build、安装 manifest 指纹和 boot/instance identity，RPC/状态查询可辨别目标。6.3 的 ModelRef 对 6.4 的调用必须在写前拒绝。没有对任务实例的可靠隔离证明，就不要通过禁用门禁推进危险写入；继续可执行的单测、接口和文档工作，并给出最小权限申请说明。

### Gate C：先 6.4 后 6.3 的完整纵向链

通过真实公开 MCP 入口，从空模型完成参数/变量/函数/选区/几何/材料/物理/网格/Study/求解，回读数值，导出数据，渲染图像并通过 ImageContent 接收。不以直接 DISPATCH() 代替唯一生产验收入口，不注入 Fake Service 绕过新平台隔离层。

按 `ACCEPTANCE.md` 重用可重建的小基准；不得把旧 6.4 MPH 强行在 6.3 中打开。每版本验证静态+瞬态、W17 四轴/复数/选区核心样例、W18 图片真实来源、保存同 SHA 重开读取存储解（不重算）和局部修改保留用户节点。

### Gate D：W19 真实负载 + 两运行时隔离

实测状态响应、排队取消、未知状态取消、断线/重启、幂等/晚到结果和 owned force-stop（明确授权才做）。建立 6.4 -> 6.3 -> 6.4 切换回归；若资源和许可允许，增加并存互不干扰测试，否则独立运行合格后单列并存 BLOCKED，不把资源不足称作版本 API 不兼容。

### Gate E：交付和复现

生成每版本证据、统一能力矩阵、实际安装锁、源码桥和一份当前 Windows 继续任务说明。源外 wheel 安装和另一新目录恢复必须实测。原始科学值/PNG/MPH 可按审计后的产物清单交付；密码、token、控制配置、用户科学资料不得整目录打包上传。

按已有仓库/用户授权普通推送工作分支；不得 force push 或以私有历史替代固定公开祖先。无凭据/无法推送时保留本地可验证提交和待同步说明，技术验收与远端同步分开报告。不冒充推送完成。

## 5. 最终记录

每个工作关卡更新 `docs/handoff_g3_6_windows/PROGRESS.md`，记录实现状态、测试命令、实际结果、环境、证据路径、源码哈希、已知限制和下一依赖。最终在仓库主进度增加本阶段摘要。

建议新证据路径：`evidence/windows_dual_version/<run_id>/{win63,win64,shared}/`。私有运行时目录另置 `.runtime/windows/` 并忽略版本控制，禁止从历史记录复用 token/PID。

结果矩阵必须区分 `IMPLEMENTED_UNVERIFIED`、`CONTROL_PASS`、`NATIVE_PASS_SCOPED`、`BLOCKED_ENVIRONMENT`、`BLOCKED_LICENSE`、`FAIL_IMPLEMENTATION`、`NOT_RUN`。没有真实 Windows 机器执行时，本轮不能报告两个版本 VERIFIED。

## 6. 停止条件

成功停止：两版本 required core 用例按同一源码通过；所有 D01–D09 得到修复或有证据支持的无需修改结论；真实任务、图像、保存重开、取消/恢复范围如实记录；源外安装、新目录恢复、回归、资源清理和交付清单完整。不进入 W20。

受阻停止：存在确实无法自行解决的权限/安装/许可/硬件限制，已完成不依赖该限制的所有本轮工作并生成最小修复/用户操作文档。必须说阶段尚未通过，不能把 blocked/unsupported 改为 PASS。不得无限重复失败命令，不得以已有丰富单测代替目标机验收。
