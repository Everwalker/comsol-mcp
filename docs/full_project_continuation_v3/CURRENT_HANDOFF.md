# 当前异地续作交接 — 2026-10-21

用户要求本轮收尾后，将最新进度和全部异地开发所需源码同步 GitHub，核对远端，然后暂停。这个停止边界取代“同步后自动继续”；只有用户明确恢复后才进入下一活动。原 W01–W26 总目标保留，项目仍为 **CHANGES_REQUIRED**，独立最终 Reviewer 为 **NOT_RUN**。

本轮完整源码 checkpoint [`7e25b963`](https://github.com/Everwalker/comsol-mcp/commit/7e25b963156302a3e809c0e9dd79ba6d0e416264) 已普通推送到 GitHub main，并经独立 fetch 核对完全一致。此后仅发布这份同步回执和暂停交接；不继续开发或派发测试。详见 [远端核对回执](state/GITHUB_SOURCE_CHECKPOINT_REMOTE_VERIFICATION_20261021_R1.json)。

## 从哪里开始

1. 在新目录克隆 `https://github.com/Everwalker/comsol-mcp.git` 的 `main`，记录该 checkout 的 `git rev-parse HEAD`。不要覆盖已有项目、重建原任务表或运行旧 bootstrap/init_completion。
2. 读取根 `PROGRESS.md`、本文件、`state/RESUME.json`，以及 `continuation/checkpoints/20261021-round/` 的说明、源码清单和恢复工具。该交接目录保存本轮未进入规范生产树的源码候选及其依赖；按其清单校验并恢复到新目录。
3. 工作包原说明亦收录于该交接目录。沿用原 26 个工作包、272 个动作、60 个测试、六个目标及既有 scope 决定。macOS ARM/Intel COMSOL 6.3 原生和 Intel Mac COMSOL 6.4 硬件为用户要求的跳过，不能改为 PASS；Windows 6.3/6.4 原生仍需完成。
4. 不执行保存的固定入口、旧 shell/native 命令、已消费的 once 或旧 PID 操作。完整恢复源码不恢复本机身份、许可、权限、原始 Popen 或原生执行授权；新机器必须重新绑定这些条件。

R2 源码清单 SHA-256 为 `80277c2255086615e91698e15f4d248b49746251f2aa679faf2431d75e524d2b`：3,978 个文件映射、1,105 个内容对象。Main 已核对全部必需源码覆盖，实际恢复并逐文件验证全部字节；从恢复目录导入执行的 R32 R3 消费者控制为 21/21 通过。完整源码覆盖包括 W23/W25 的 1,145 项、W24 的 900 项、W26 R31/R32 R3 的 905/908 项及最新 Windows 入口和诊断依赖。详见 [Main 源码交接审查](state/GITHUB_SOURCE_CHECKPOINT_MAIN_REVIEW_20261021_R1.json)。

## 规范生产树与开发候选

规范生产树仍为 R30 / current175-r13，897 个成员，源码指纹 `efc3b9717107f0abef4d36a61f40658cc9c4c75b0dcc1321194694a19ad75173`。权威指针是 `state/CURRENT_CANONICAL_ACTIVE_SOURCE.json`，清单为 `state/CURRENT_CANONICAL_SELECTED_SOURCE_R30_W24_R13_R9_TEST_FIX_R1.json`。产品锁 `tools/w26_application_sources_175_r13_prepared.lock.json` 的 SHA-256 为 `1dd9ba4db2264e44a6c778eff8d9d18d34709f7b954bff6252bce6cbede9b826`。

根源码与独立候选不是已经通过全项目验收的发行版。交接包分别保存 W24 R20 有界科学入口、固化属性调查 R32 R3、W23 R17 非隐藏输入修复及新驱动、W25 空白 GUI 入口、Windows R6I 当前入口和只读诊断源码。每个候选的确切清单、测试结果和 Main 审查状态见交接索引；不能整目录互相覆盖，也不能把候选的软件 PASS 移作规范生产树或原生验收。

共享 Core、Worker、结果和存储代码应按逻辑差异合并。候选中的绝对路径与历史 source pins 保留原字节用于审计；迁移后需创建新的活动和绑定，不篡改冻结原件来伪造旧校验通过。

本轮冻结的入口软件结果：W24 R20 为 18 项控制和实际 check-only R6 通过，前五次 check-only 失败保留；W23 R17/R18 driver 为 15 项控制、source-only 与实际导入预检通过；W25 blank-GUI R2 为 2 项控制和旧 R26 installed caller 的只读消费者检查通过。Main 对这三个新包完成了清单完整性复核，尚未完成新的完整功能准入审查，均不得据此启动原生或声称原生通过。旧 R26 环境的非空卸载保护失败保留，不被另一个新 R30 环境的生命周期 PASS 倒填。

## 当前已验证的范围

- **W26 / current175-r13**：三架构离线软件构件，以及实际 ARM 安装、升级、回退、完整数据恢复和非空卸载均有 Main 限定验收。它们不证明新 R32 产品字节或 Windows/Intel 硬件验收。详见 `state/W26_R30_SCOPED_DELIVERY_CHECKPOINT_20261020_R2.md`。
- **W24 R17**：14 个无求解模型均完成建模、保存、注册产物重开及配置回读；28 个不同 model tag，126 个槽内操作、133 个持久化 operation/job 成功，1,374 对 RPC 终态成功，正常收尾完成。phase initialization、Study/solver、稳定胶形和固化科学未运行。旧 ModelRef 已随 Worker 退休失效。
- **W24 R18**：项目内原字节收据投影修复通过 Main 的 7 项消费者控制；新 Worker 仍须重新加载全部 SHA 绑定 MPH 并取得当前 epoch 的完整配置证明。
- **固化属性调查 R32 R3**：Main 对精确 Java 源以官方 349 个 JAR、JDK 11.0.31 编译，4 个 class 均 major 55；R3 严格结果消费者通过 Main 的 21 项控制。仅调查代码和编译兼容通过，实际 COMSOL 属性、类型、许可上下文仍未验证，新的 current175-r14 构建和安装尚未执行。
- **W25 public probe**：R5 的 17 项实际探针及正常收尾通过，169 对 RPC、16 个 operation/job 终态成功；证据仅适用于其记录的源码范围。完整 GUI、模型绑定、共享 Server 交替读写及未保存模型迁移尚未验收。

## 必须保留的失败和 UNKNOWN

W24 R16 trace 容量溢出、SCIENCE76 R4 `Unknown_property: geometricNonlinearity`、W23 R16 隐藏输入路径注册拒绝，以及各次 W25 GUI 失败均保留原始证据和已消费 once。新修复没有把这些活动改为成功。

W23 R16 已在首个 artifact.register 失败前完成三个 setup 调用；没有模型加载、字段读取或 Study。四个持久化作业全部终态。Main 核对身份和唯一终态 connect RPC 后，按用户既有授权对当次 Worker/Server 各一次 SIGTERM，原 Popen 已回收、控制器自然退出；外层 UNKNOWN 保留。R17 修复采用合规非隐藏项目输入，不放松受保护路径规则。

W24 固化属性仅确认在当次实际对象上被拒绝，不能凭 Stationary 示例就断定 Transient 或 Time Solver 的正确 setter。R32 先调查实际对象、属性类型、允许值和当前值；`hasProduct` 可用性不能替代许可签出或属性支持证明。

W25 的 AX cannot_complete、失效引用、窗口身份变化和多窗口拒绝分别处理，不能统一归因 TCC。R8 只读到 13 个节点，伴随 44 个 gaps 和截断，不构成 GUI 或模型绑定通过。详见 [最近错误分析](state/W24_W25_RECENT_ERROR_ANALYSIS_20261021_R3.md)。

## Windows 当前活动：仅原机可观察，禁止重放

截至本轮最近实际观察，原 R6I R2 本地 controller 47502、SSH 47520 / exec session 89189 仍由原持有器保留，原连接没有 stdout/stderr 或终态回执。它已消费本地 once，不能因超时或零字节输出重新发送。

本轮另一个严格 SSH 只读诊断自然退出 0，仅对固定 10 个路径做 lstat：远端 MAIN_APPROVAL 为 2,018 字节、ONCE_USED 为零字节，预期 probe 输出目录和文件不存在。**零字节 once 不代表可再次执行；这些元数据也不证明原测试子进程是否出生、仍活着或终态。** 原活动状态继续为 UNOBSERVED，未发送信号、取消、重试或重放。

暂停 Agent 不自动停止这个持有器。异地开发只能读取公开源码和摘要；不能接管原 Popen 或用旧 PID 操作。若恢复到原机，应先检查原 exec/进程和证据，再决定符合既有范围的下一步。新的只读诊断结果见 `state/WINDOWS_R6I_METADATA_R2_MAIN_ACTUAL_REVIEW_20261021_R1.json`。

## 用户恢复后的顺序

1. 核对交接清单、原机活动与 UNKNOWN，确认依赖、实际安装、所有权和权限；不自动派发旧活动。
2. 按 Main 审查结果整合必要 Core、trace 和输入路径修复。新源码或产品字节使用新产品锁、三目标构建及适用的源外安装验证；不要将旧构件重新标成新候选。
3. W24 先完成实际属性调查；完整 14 槽形态/敏感性科学链使用新 Worker、当前 epoch 回读、原阈值及有界资源。W23 使用新驱动和合规输入完成原字段与科学要求。W25 先完成空白 GUI 观察，再推进真实模型绑定等剩余要求。
4. 完成适用 Windows 6.3/6.4 原生和交付验证。只有在整合后的最终候选及适用测试就绪后，才由真正独立强 Reviewer 统一验收并进入实质问题整改循环。

商业安装/JAR、MPH、作业数据库、原始私有运行目录、缓存/venv、编译产物、凭据、有效审批和 once 留在本机；交接源码清单及 LOCAL_ONLY 索引注明其范围。需要的商业组件重新按合法安装及固定输入取得，缺失科学产物只能准确标缺失或另建新活动，不能冒称恢复同 SHA 历史运行。
