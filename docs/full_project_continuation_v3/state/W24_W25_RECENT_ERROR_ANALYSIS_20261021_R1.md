# W24 / W25 最近测试错误分析（2026-10-21）

本分析核对 2026-10-14 至 2026-10-21 的实际失败、Main 审查、修复及复测记录。不同分支的 R 编号不能互相排序；例如 W25 public probe R5 与 GUI actor R9 是不同活动。本次分析没有派发新的 COMSOL、GUI 或求解任务，也不改变历史 UNKNOWN。

主要问题集中在 COMSOL 运行时接口假设、执行及证据契约、GUI 对象生命周期。下面几次失败发生在建模或观察阶段，不能解释为胶形或固化计算不收敛；相应科学验收尚未运行。

## W24：已确认的故障与尚未确认的根因

| 阶段 | 实际错误及证据 | 根因判断 | 修复和当前边界 |
| --- | --- | --- | --- |
| R16 R2 第二槽 baseline/step 建模 | `selection trace exceeded its fixed safe envelope`；step 恰好 1,024 条，flat 成功路径已有 957 条。第 1,024 条仅记录 API 调用前的 breadcrumb。 | **已确认：诊断记录器自身容量不足。** 没有预留调用后的成功或失败记录空间。最后一次 API 的实际结果未被完整观测，不能据此断言 COMSOL 的该次调用失败。 | 固定上限改为 4,096，调用前先预留两条；保留私有文件和长度等限制。18 项相关控制通过；新 R17 实跑完成 14/14。原 R16 UNKNOWN、失败和已消费 once 保留。 |
| SCIENCE76 R14 R4 固化模型构建 | 前 63 次 public 调用成功，第 64 次 `W24_CURE` Java builder 报 `Unknown_property: geometricNonlinearity`，复合操作已有部分修改。 | **已确认属性在实际对象上被拒绝；根因未定。** 接收对象、模型上下文及许可条件仍需逐一核实。不能只凭 Stationary 例子就认定 Transient 或 Time Solver 的正确接收对象。 | R5 改为 StudyFeature boolean setter 的候选只通过软件消费者控制，尚未获准作为生产 Java 修复。原操作保持 UNKNOWN / `safe_retry=false`，不重放。 |
| R14 后的终态证明与 Server 收尾 | 公共结果、OperationStore 终态、Worker retirement 和进程身份的读取及比较存在契约缺口；Java 可执行身份比较还混入了观察元数据。 | **软件比较或字段消费问题已经定位；R14 内层 stop 的历史因果仍未证实。** 不能用后来发现的比较器缺陷倒推全部旧失败。 | R15 R3 修复精确 operation/job/request/epoch 证明及语义身份比较，375 项受影响测试通过；R17 正常退休与 public session stop 已实跑通过。 |

R17 Main 已独立核对：14 个无求解模型均完成建模、保存、注册产物重开及完整配置回读；共 28 个不同 model tag，126 个槽内操作成功，全部 133 个持久化 operation/job 成功，1,374 对 Worker RPC 均终态成功。原持有器完成正常收尾，控制器退出 0。

这说明上述修复在 **14 槽无求解链** 上有效。它不证明稳定胶形、跨台阶接触、体积守恒、网格/时间步收敛或 UV→热固化→冷却状态传递已经通过。新 Worker 必须重新加载 SHA 绑定的 MPH 并取得当前 epoch 的配置证明，不能继续使用已经退休的 ModelRef。

同版本官方文档也要求谨慎判断固化属性问题：Time Solver 的表格明确列出 `geometricNonlinearity` 的 `on|off` 形式，并列出模块许可条件；Transient StudyFeature 表格没有列出该项，但表格缺项本身不能证明其不支持。文档不能替代本机实际许可或对象属性证据。

## W25：入口问题与 GUI 问题需要分别修复

| 阶段 | 实际错误 | 根因判断与当前状态 |
| --- | --- | --- |
| public probe 首次 project.create | `POLICY_ESCALATION_REFUSED` | **已确认启动入口遗漏。** 请求了 `host_control`，却没有在默认 daemon 构建前设置现有的 `COMSOL_MCP_HOST_CONTROL` opt-in。策略拒绝正确；当次没有创建 Server/Worker/模型。后续新活动修复入口并通过 public probe。 |
| public probe 宿主配置与退休包装 | host 配置所有权/卷条件拒绝；退休结果字段读取层次错误 | 属于配置及包装器契约问题，应使用本任务私有合规目录，并按实际嵌套响应读终态。R5 实跑 17 项 probe、正常收尾通过：169 对 RPC 终态成功，16 个 operation/job 均成功。证据只覆盖该次 840/172 源码范围，GUI 未运行。 |
| GUI R6/R7/R9 观察 | `AXSize` / `AXWindows -25204`，窗口身份变化，`AXPosition -25202 invalid_ui_element` | **实际故障已观测，不能统一归因 TCC。** `cannot_complete`、失效 AX 引用和窗口身份变化需分别诊断；现有记录不能证明权限不足，也不能证明 R7 究竟哪个身份字段发生变化。 |
| GUI R9 AXRef 候选 | `task-owned process identity is unavailable`，尚未进入 AX readiness | 短命启动子进程消失是一种合理推断，但原始异常链没有保留，**尚未确认**。这次没有实际验证 AXRef 修复。 |
| GUI R9 startup 候选 | 重获窗口身份时发现多个自有可见窗口 | **拒绝选择是正确行为。** 不能任取一个窗口或放宽唯一性来制造通过；该次也没有实际验证预期 startup 恢复路径。 |

GUI R8 曾在同一原始所有者上下文中完成一次有界唯一窗口重选，读取到 13 个 AX 节点，但有 44 个 gaps 且结果截断。它只证明部分元数据观察可行：selector 未认证、模型绑定 UNVERIFIED、像素及控制 NOT_RUN。当前 GUI R5/current175-r12 候选的 29 项控制及已安装调用者字节核对通过，也仍只是进入下一次实跑前的软件证据。

## 为什么软件测试通过后仍连续失败

1. **Java 编译和替身对象不能验证本机 COMSOL 属性语义。** 属性名是字符串，是否存在及其类型还依赖实际 feature、生成的 solver 拓扑、上下文和许可。需要先在同版本的实际对象上做最小属性调查，再修改 builder。
2. **成功路径与边界路径覆盖不同。** flat 的 957 条 trace 未触及 1,024 上限，step 才暴露记录器缺陷。记录器应在派发前保证能够记录终态，并以真实失败 trace 做回归。
3. **测试入口必须与发布入口一致。** cwd、`-S`、依赖注入、installed caller、环境 opt-in、嵌套响应格式都有可能让“单函数通过”与“实际入口失败”同时成立。应验证实际生产者生成的记录能被实际消费者读取，再消费 once。
4. **GUI 启动是有生命周期的过程。** 最先出现的子进程或窗口不一定是稍后观察时的对象。允许的恢复必须有原始 Popen/出生时间/所有者证明、唯一目标以及总时间预算；身份不明或多窗口时继续拒绝。
5. **Main 的审查适配代码也有可避免错误。** R17 审计脚本 R1 缺 SQL 空格，R2 已在保留原始库及归档的前提下修复；没有重放原生活动。入口验证曾偏离要求的 `-S`/cwd/argv，报告计数与哈希也出现过不一致。这些应由精确入口及自动提取实际结果纠正，不能归咎 COMSOL。

## 下一步优先顺序

1. 先通过既有 public MCP/Worker 路径核对固化模型实际 Transient/Time 对象、属性类型/允许值及许可条件；不猜新 setter、不用禁用非线性绕过原物理要求。
2. 在 R17 的 14 份 SHA 绑定模型上，以新 Worker 重新加载和完整回读后推进原 W24 形态及敏感性求解。完整科学终点和既定阈值保持不变。
3. W25 先完成自有空白 GUI 的当前安装、启动状态、唯一窗口及 AX 观察闭环，再推进真实模型绑定、共享 Server 交替读写和未保存模型迁移。失败活动不原样重跑。

## 原始证据索引

- [W24 R16 容量故障与修复分析](../../../_state/w24-r16-selection-trace-overflow-repair-r1-20261021/W24_R16_TRACE_OVERFLOW_DIAGNOSIS_AND_REPAIR_R2.md)
- [W24 R16 实际失败和终态审查](../../../_state/w24-r16-r2-main-recovery-20261021-r1/MAIN_ACTUAL_TERMINAL_REVIEW_R1.json)
- [W24 R17 Main 实际 14 槽验收](W24_R17_MAIN_ACTUAL_SETUP14_ACCEPTANCE_20261021_R1.json)
- [W24 R15 R3 Core/终态契约修复审查](W24_R15_R3_MAIN_SOURCE_SOFTWARE_REVIEW_20261021_R1.json)
- [固化 R4 实际失败和终态审查](../../../_state/science76-r14-r4-main-recovery-20261021-r1/MAIN_ACTUAL_TERMINAL_REVIEW_R1.json)
- [固化 R5 接收对象及 API 证据缺口](SCIENCE76_R14_R5_MAIN_RECEIVER_API_FINDING_20261021_R1.json)
- [W25 public 首次策略拒绝](W25_PROBE17_R4_MAIN_ACTUAL_FIRST_ADMISSION_FAILURE_R1.json)
- [W25 public R5 实际限定验收](../../../_state/w25-r5-fresh-runtime-entry-6fc675b5-c7a1-4483-9de5-51d2ceeccd8f-20261019/evidence/MAIN_ACTUAL_R5_TERMINAL_20261019_R1/MAIN_ACTUAL_NATIVE_TERMINAL_REVIEW_R1.json)
- [W25 GUI R6 AX 失败](W25_R6_GUI_MAIN_ACTUAL_FAILED_EVIDENCE_REVIEW_R1.json)
- [W25 GUI R7 身份变化](W25_R7_GUI_R2_MAIN_ACTUAL_FAILED_EVIDENCE_REVIEW_R1.json)
- [W25 GUI R8 部分元数据成功](W25_R8_MAIN_ACTUAL_GUI_METADATA_REVIEW_R1.json)
- [W25 GUI R9 失效 AX 引用](W25_R9_MAIN_ACTUAL_GUI_FAILURE_RESOURCE_REVIEW_R1.json)
- [W25 GUI R9 进程身份不可用](W25_R9_AXREF_CANDIDATE_MAIN_ACTUAL_FAILURE_REVIEW_R1.json)
- [W25 GUI R9 多窗口拒绝](W25_R9_STARTUP_CANDIDATE_MAIN_ACTUAL_FAILURE_REVIEW_R1.json)
- [W25 当前 GUI 候选的软件审查](W25_GUI_R5_CURRENT175_R12_MAIN_SOURCE_REVIEW_20261020_R1.json)

总体状态仍为 CHANGES_REQUIRED；独立最终 Reviewer 为 NOT_RUN。本分析不构成发行验收或历史状态重分类。
