# G3.5 验收规格：Gate A + W19

本文件 22 个测试定义，生成时状态均为 **NOT_RUN**。它们是下一 Agent 的任务，不是本次已取得的成绩。

## 统一规则

- CONTROL / FILESYSTEM / SOURCE / INSTALL / NATIVE / PUBLIC_MCP / HOST 分别报告；一个用例的控制部分通过不等于原生部分通过。
- 每个required子项记录 PASS / FAIL / BLOCKED / NOT_RUN，不能因多个子项通过而覆盖一个未通过required子项。
- COMSOL异常注入、读回替换、竞态模拟可用控制测试，但不得称“全部live”。正常冷启动与关键操作至少有公开MCP+真实引擎证据。
- 试验样本和误差标准在运行前冻结；图像差异不只比较文件hash，还要检查所选解与数值。
- 未测环境保留UNVERIFIED。无云端凭据不强求发布密码，也不妨碍独立软件工作。
- 三种成功状态分别记录：执行成功、数值/数据契约通过、物理验证通过。W19不扩大为物理模型认证。

## 原计划映射

Gate A 延续 W18 的 T039/T040 及相应数据、身份、安全、清理验收；W19 仍受 T022/T023/T024/T025/T026/T027/T028/T053/T054/T057 约束。以下用例细化测试方式，不能代替或删掉原始 required 条件。

## G01 — 恢复/来源/安装

**状态：NOT_RUN；证据层级：SOURCE + INSTALL + PUBLIC_MCP。**

**执行：** 从本包PIN恢复新目录，核对完整tree及每个blob；真实wheel安装A、源码B、项目C、cwdD，普通入口冷启动；加入单文件被修改的source负控。

**通过判据：** 源码不靠旧184计数认证；模型/输出仅在可信项目C；不能仅Mock四个路径。

**保留证据：** 新RESTORE_RECEIPT、source前后manifest、pip check、wheel文件表、公开启动/读写记录。

## G02 — 旧图不可冒充新产物

**状态：NOT_RUN；证据层级：CONTROL + NATIVE + PUBLIC_MCP。**

**执行：** 分别对plot.render、plot.geometry_render、export.run预置旧有效PNG，模拟/触发新staging未生成或为空，再正常成功渲染。

**通过判据：** 失败不返回新科学图，不登记旧图；target字节不变；成功确有本次文件归属。

**保留证据：** 旧/新hash、request/result、staging/node清单、正常路径原生PNG。

## G03 — 清理与属性恢复

**状态：NOT_RUN；证据层级：CONTROL + PUBLIC_MCP + NATIVE可行部分。**

**执行：** 临时节点remove失败、已有export filename恢复失败、render run失败；检查持久job和模型dirty。

**通过判据：** 失败/UNKNOWN单调升级；原始属性可恢复则回读证明，不可恢复明确报告；不把注入控制测试称全部native。

**保留证据：** 原始/运行中/最终属性、错误链、job_result、剩余节点。

## G04 — 原子发布与覆盖竞态

**状态：NOT_RUN；证据层级：CONTROL + FILESYSTEM。**

**执行：** 默认同名文件拒绝；明确覆盖；并发出现target、父目录链接变化、register失败。

**通过判据：** 未授权不覆盖，无越界；已发布但后续失败的文件如实返回，不假称无副作用。

**保留证据：** 文件hash前后、目录/注册表差异、并发事件时序。

## G05 — 指定存储解的图像绑定

**状态：NOT_RUN；证据层级：NATIVE + PUBLIC_MCP。**

**执行：** Solution→CutPlane/派生dataset链，多outer/inner/时间；不同解分别数值抽查与出图；参数修改但不重算；错solution负控。

**通过判据：** 实际解索引、单位与provenance一致，不把data指向的dataset当solver；旧解明确标注。

**保留证据：** 完整Binding、数值表、PNG、图形属性回读、ModelRef/revision。

## G06 — typed属性与完整路径

**状态：NOT_RUN；证据层级：CONTROL + NATIVE + PUBLIC_MCP。**

**执行：** 数字矩阵、布尔、字符串表达式数组、TypedValue；三级plot子节点和不同component同名geometry。

**通过判据：** 不字符串化矩阵、不截断路径；支持的深度正确修改，不支持写前拒绝；非目标保留。

**保留证据：** setter输入/实际getter、节点树diff、错误用例。

## G07 — PNG、预算与交付错误身份

**状态：NOT_RUN；证据层级：CONTROL + MCP。**

**执行：** 完整PNG、截断24字节头、坏base64/坏块、零维/超像素、超字节、失败envelope带图像；MIME拒绝后查原job。

**通过判据：** 科学图只在已验证成功范围回传；原job/op/ref仍在，交付失败不重跑引擎；decoder和预算明确。

**保留证据：** ImageContent/structuredContent、job_result、调用次数、decoder错误。

## G08 — 真实artifact-only预算

**状态：NOT_RUN；证据层级：PUBLIC_MCP + NATIVE。**

**执行：** 实际result.evaluate强制storage=artifact，断言success与isError后测完整wire；artifact分页还原数值与四轴；缺success负控。

**通过判据：** 不是错误payload短而通过；完整数据不藏在field_array/text；文件元数据未丢。

**保留证据：** 原始tool结果字节数、原值/预览/恢复对照、hash。

## G09 — 运行源—交付源桥

**状态：NOT_RUN；证据层级：SOURCE。**

**执行：** 比较本轮运行前后生产源/测试哈希与最终公开commit；改一个文件后桥必须失败；旧184文件表只保留历史含义。

**通过判据：** 同源结论由文件内容证明；HEAD不同不自动判错，同HEAD不自动判对。

**保留证据：** before/after/commit manifest、差异列表、负控输出。

## G10 — 冷启动及三入口等价

**状态：NOT_RUN；证据层级：PUBLIC_MCP + NATIVE。**

**执行：** 不用注入LiveAdapter/SessionLedger，从正常配置cold-start后server_connect/adopt；比较plot.render、plot_render、operation_call。

**通过判据：** 权限/修订/job/类型/错误/图像一致；至少一条完整链不直接调用DISPATCH。

**保留证据：** 脱敏stdio transcript、进程身份、调用与文件证据。

## G11 — 真实Host及其他Server边界

**状态：NOT_RUN；证据层级：HOST + NATIVE。**

**执行：** Hermes发现与实际图像call分开；条件允许时验证云端模型收图。其他Server存在时核对它未变，不存在如实记。

**通过判据：** 无host或未调用不能写PASS，工具数解析失败不补67；无其他PID不宣称保护过历史PID。

**保留证据：** host版本和实际call/图像日志、凭据不入包、独立Server生命周期记录。

## G12 — W18回归与交付恢复

**状态：NOT_RUN；证据层级：CONTROL + NATIVE + SOURCE。**

**执行：** 保留1D/2D/3D/几何/网格、瞬态、保存重开和源外安装；按改变范围重跑，归档到新目录核验。

**通过判据：** 修补未回退已有功能；所列公共文件都存在；private排除清单准确；正常同步源可恢复。

**保留证据：** 阶段回归、压缩包清单/hash、恢复receipt、remote SHA。

## J01 — 作业目录与状态语义

**状态：NOT_RUN；证据层级：CONTROL + PUBLIC_MCP。**

**执行：** 列出/分页/筛选job，status/log/result/wait；故意不存在、错误project、分页边界、非法超时。

**通过判据：** 缓存与引擎状态区别清楚；别名兼容；无失真进度；bounded分页及schema真实。

**保留证据：** schema、状态迁移表、所有响应与日志页。

## J02 — 排队取消与启动竞态

**状态：NOT_RUN；证据层级：CONTROL + PUBLIC_MCP。**

**执行：** 一个长任务占用引擎后取消排队任务；重复取消；在QUEUED→RUNNING临界点进行竞态测试。

**通过判据：** 排队取消完成时无引擎修改派发；运行任务走不同分支；同key无重复。

**保留证据：** 有序事件、Worker request清单、模型指纹、计数。

## J03 — 运行取消与所有权

**状态：NOT_RUN；证据层级：NATIVE + PUBLIC_MCP。**

**执行：** 对持续真实求解发取消，验证原生路线；若走进程级模式须显式本任务独占授权。测试共享实例/错误PID身份拒绝。

**通过判据：** 取消受理与实际停止分开；记录engine停止/未停止；不得把进程终止标作原生cancel，不触及无关Server。

**保留证据：** 官方API签名/PoC、引擎日志、PID创建时刻、状态时序、checkpoint范围。

## J04 — Host断连与幂等重取

**状态：NOT_RUN；证据层级：PUBLIC_MCP + NATIVE。**

**执行：** 提交后断开Host；用原key/result找回；一次响应丢失后重发同请求；不同body同key负控。

**通过判据：** 求解/渲染/修改执行一次；新读取不被旧读取key缓存；无后台复跑。

**保留证据：** jobID、引擎调用数、请求hash、重连响应。

## J05 — 控制/Worker崩溃协调与恢复

**状态：NOT_RUN；证据层级：CONTROL + NATIVE。**

**执行：** 控制进程重启、Worker失联而Server存活、Server退出；使用原request协调，必要时恢复检查点并更换generation。

**通过判据：** 不重放未知写；部分API成功不等于高层完成；旧ref拒绝；实际模型/存储值恢复有证据。

**保留证据：** 重启前后DB、request状态、checkpoint hash、非边界场值。

## J06 — 进度、日志与响应性

**状态：NOT_RUN；证据层级：NATIVE + PUBLIC_MCP。**

**执行：** 真实长求解时轮询health/job/log，测无过载p95；失联/日志静止/缓存过期；支持时读实际solver日志进度。

**通过判据：** 目标p95<1秒且有样本条件；未知进度不编百分比；控制请求不等引擎队列完成才答复。

**保留证据：** 延迟原始样本、负载、observed_at/source、日志片段。

## J07 — Server级串行和独立并行

**状态：NOT_RUN；证据层级：CONTROL + NATIVE。**

**执行：** 同一Server两个模型并发提交；不同独立Server在预算下运行；注入新请求期间取消。

**通过判据：** 同Server无重叠危险API；不同Server允许已验证并行且不超预算；没有第二实例时单列未测。

**保留证据：** 两个队列/Server标识、开始结束时间、资源与许可记录。

## J08 — 期限和停止策略

**状态：NOT_RUN；证据层级：CONTROL + NATIVE + PUBLIC_MCP。**

**执行：** RPC到期、queue期限、execution期限、no-progress四者独立；null长期执行；取消与deadline同时到达。

**通过判据：** 停止等待不改成已取消；策略显式；实际未停仍报告运行/未知；不引入隐藏固定长任务上限。

**保留证据：** 配置、事件时间、engine_stopped证据、后续job_result。

## J09 — 存储/进程安全与迁移

**状态：NOT_RUN；证据层级：CONTROL + FILESYSTEM + NATIVE可行部分。**

**执行：** 日志轮转/分页、DB升级、磁盘不足、异常退出、PID复用/进程身份不符、控制close策略。

**通过判据：** 不丢已有模型/历史结果，不误杀，不把损坏历史当空记录；明确detach/drain行为。

**保留证据：** migration前后hash、故障注入、进程身份负控、清理清单。

## J10 — 求解—出图—交付恢复总链

**状态：NOT_RUN；证据层级：NATIVE + PUBLIC_MCP + HOST可行部分。**

**执行：** 实际长求解→定量结果→绑定指定解出图→ImageContent；在交付阶段断连/超预算；另测取消处于导出staging期间。

**通过判据：** 重新取原job/artifact而不重新算；失败不发布旧图/半成品；成功产物与原解对应；Host未测单列。

**保留证据：** 数值/图片hash、job/op/ref/solution、调用次数、产物发布日志。

## 阶段判定

Gate A 的科学图新鲜性、绑定、错误传播或幂等恢复仍有FAIL时，不能进入W19引擎执行扩展。Host不可访问、其他OS/版本不可访问应单列，不把其阻塞扩散到无依赖部分。

W19阶段通过必须明确实际验证的取消路线和所有权范围。仅实现cancel schema、Future取消或标志置位不得通过；原生中止无法验证时可报告已验证的独占进程级取消与检查点恢复，但不得据此宣称共享Server协作取消。

本阶段结束时输出当前结果矩阵、未通过项和具体下一步，而不是只输出“测试数量增加了”。
