# 下一阶段 Goal：Mac 主开发，G2 / W08–W12

## 0. 任务性质与基线

本文件是新阶段的执行要求，不是软件已经完成或已经通过验收的证明。

本轮目标：在现有受管执行基础上，完成类型化节点/API、正式 MCP 契约、绑定当前模型的 Java 执行、版本化帮助检索，以及计划/试运行/检查点恢复。继续以 Mac 为代码集成端；Windows 的部署、SSH 与实机适配延后，不作为本轮前置条件。

审查参考为远端 `Everwalker/comsol-mcp` 提交：

- `28605a6896668a64775ac6c1192ed50fef94b47f`
- Git tree：`0172bc996879ff92aaeea24b7b666e5b2d7c9be0`
- 提交说明对应本地阶段提交：`48c88f56f7aea6564d48b1a48a88c792a3e20dd7`
- 已读取材料：提交记录、PROGRESS.md、phase2_acceptance.json、最终 pytest 输出、原工作包与验收规格。
- 这些是仓库提交的测试证据；本任务文档生成时没有重跑 COMSOL，也没有读取用户本地未同步修改。

必须先检查本地 HEAD、Git tree、工作区修改、实际适用的 AGENTS.md 和最新证据。SHA 不同不代表代码不同；以上 SHA 只是参考，禁止为了对齐它而 reset、覆盖或删除本地较新的工作。已有可靠证据支持的实现只做必要回归，不从 W01 重来。

## 1. 必须继承的真实进度

Phase 2 记录了 W05/W06/W07 的限定范围 PASS，以及 `172 passed in 5.30s` 的软件测试结果。

已有生产路径是：stdio MCP → 持久控制服务 → Server 级串行队列 → 持久 Java Worker → COMSOL。已提交证据覆盖限定模型上的身份/修订/幂等、真实求解、控制面响应、提交 Host 断开后的同作业恢复、空闲受管进程替换、真实磁盘不足时保留原 MPH，以及新 Worker 重开模型。

不得扩大以下结论：

1. 仅 macOS arm64 / COMSOL 6.4.0.293 / 外部 JDK11 有本阶段实机证据。Windows、Intel Mac、6.3 和 GUI 仍为 BLOCKED/UNVERIFIED。
2. 独立客户端修改参数后，指纹检测与旧修订拒写有 PASS；`ModelChangedHandler` 回调计数未增加，补充用例为 FAIL。其他属性与 GUI 外部改动的覆盖尚未验证。
3. `server_instance_id` 目前是保守的 Worker 连接 epoch，不是独立观察得到的 COMSOL 进程 UUID；不能把它宣传成后者。
4. 超时告警不是取消成功；共享 Server 的真实强制取消没有通过验证。此前故障注入使用的是空闲任务所有的 control/Worker，不等于活动求解中的 Worker 崩溃恢复已认证。
5. T038 仅最小错误传播子项已有实测，完整 registry/schema/host 契约仍属于 W09。
6. W08 在该远端阶段边界仍是 NOT_RUN。旧文档中的“不得进入 W08”是旧 Goal 的范围，本次新 Goal 明确授权 W08–W12；不改变其他安全、权限与证据要求。

PROGRESS.md 是追加历史，须以末尾最新阶段边界和当前证据判断进度。原始 `03_ACCEPTANCE.md` 中静态 NOT_RUN 是设计时状态，不得据此把后来实测结果全部重置。

## 2. 先读哪些文件

定位并阅读当前仓库中的：

- `docs/comsol_mcp_design_v1/05_DEVELOPER_TASK.md`
- `docs/comsol_mcp_design_v1/04_IMPLEMENTATION_PLAN.md`、对应 backlog JSON
- `docs/comsol_mcp_design_v1/01_ARCHITECTURE.md`
- `docs/comsol_mcp_design_v1/03_ACCEPTANCE.md`、对应验收 JSON
- `docs/comsol_mcp_design_v1/06_CONTRACT_NOTES.md`
- 动作目录及输入输出 Schema
- `docs/comsol_mcp_design_v1/PROGRESS.md`
- `evidence/phase2_acceptance.json` 及相关成功/失败原始记录
- W05–W07 的生产 gateway、execution service、存储、Java Worker 和测试实现。

文件路径与名称以实际仓库为准，缺项要报告，不得凭旧聊天补出“已存在文件”。需求有冲突时记录差异；不默默缩减原始全能力目标。

## 3. 本轮范围与顺序

按原工作包依赖推进，不以工具数量作为完成标准：

| 工作包 | 依赖 | 本轮交付 | 原验收关联 |
|---|---|---|---|
| W08 | W04、W06 | typed 值、集合及完整嵌套节点解析 | T008、T009、T010 |
| W09 | W01、W08 | registry/schema、协议结果、工具发布与 host fallback | T038、T039 |
| W10 | W08、W05 | 公共 API 操作与绑定当前 Model 的 Java 编译执行 | T031、T032、T037 |
| W11 | W02 | 版本隔离的本地帮助索引、来源与无结果语义 | T043、T036 |
| W12 | W07、W10 | 静态计划、隔离试运行、检查点与恢复范围 | T029、T033、T050 |

W08 先做；W09 与 W10 在各自依赖满足后推进；W11 的纯索引工作可独立推进；W12 依赖 W07/W10。共享 Server 的真实 API 操作始终串行。

本轮不要求 Windows 登录、源码下发、Windows Python/JDK 安装或 GUI 权限开通。不要求购买、模拟或伪造缺失的 COMSOL 版本/模块。保留跨平台接口，并对可在 Mac 运行的跨平台契约测试实施验证。

## 4. 先处理扩展能力会触及的遗留边界

在 W08/W10 开放更广写入前，检查外部修改检测覆盖范围。保留 callback FAIL，优先复现和定位；不得把参数指纹 PASS 写成全模型冲突检测 PASS。

不能可靠识别其他客户端改动时，应在已授权、隔离且无其他写客户端的测试会话中执行相关写操作，或使用已验证的写前检查策略。无法确认这些条件则拒绝该高风险操作并报告限制。不要用一个“独占”布尔标记冒充真实隔离或原子事务。

事件回调修复不是整个 G2 的无限等待条件：能在上述边界内完成的 typed/API/协议/文档开发继续；真正依赖该能力的用例保持 FAIL/BLOCKED/UNVERIFIED。保留对 Server 身份、generation 和 revision 的准确描述。

## 5. W08：类型和节点解析

1. 使用结构化节点路径，支持 component、geometry、Work Plane 内部 geometry、feature 和嵌套 subfeature。不得用 eval 解析路径，也不得通过偷偷载入另一模型实现动作。
2. 所有操作统一经过现有权限、模型身份、修订、幂等和串行队列；不能新增直连 COMSOL 的旁路。
3. 保留 bool/int/double/string、空数组、单元素数组、多维矩阵等类型和形状。使用目标版本支持的 Java 签名，非法静态输入写前拒绝；不得退化为全部字符串。
4. 节点类型、属性类型、允许值和签名优先依据本机实际 API/版本化帮助；无法确定时返回明确错误，不猜测方法。
5. 在真实小模型上验证：属性往返、非法输入不修改模型、同 tag 不同 type 拒绝、wp3 内只改目标子节点而保留其他对象和主模型身份。
6. T009 按实际覆盖记子项：仅路径解析和局部编辑通过，不得代替 W14 才完成的全部高级几何/阵列测量验收。

## 6. W09：协议与 registry

1. 建立统一动作元数据与输入输出契约，保留旧工具的明确兼容映射，不维护另一套绕过 service 的旧实现。
2. `structuredContent` 返回结构化结果，不把 JSON 串再包进 result 冒充强类型输出。真实业务失败通过 MCP `isError` 正确传播；PARTIAL、UNKNOWN 与数值未验收必须区别于全成功。
3. 工具列表、动作说明、schema 和开发导航从 registry 生成或自动校验，修复文档漂移；不要把未实现目录全部注册成可执行工具。
4. 实现 full/domain/expert 发布模式与检索/调用 fallback。为云端强模型保留完整后台能力，不因缩短工具列表而删除通用 API 或代码执行能力。
5. 用真实 stdio 客户端测试 initialize/list/call、分页、schema、错误与日志隔离。测试不支持动态工具、图像、Tasks 的 host fallback；模拟 host 仅证明该契约，不冒充所有真实客户端已验证。
6. 不在本轮为了完成 T039 提前扩建 W18 的完整绘图模块；尚未具备的图像能力必须明确宣告并正确降级。

## 7. W10：公共 API 与 Java 执行

1. 提供正式的 describe/compile/execute/readback 流程。编译执行绑定已确认的 model_ref 与注入 Model；不允许悄悄切主模型、加载替身或另开隐藏 Server。
2. 支持循环、批量操作和必要的公共 API 调用，验证至少一个“无专用领域 wrapper、但公共 API 可完成”的真实动作。
3. 保存源码及哈希、编译诊断与行号、输入、模型身份、修订、日志、实际修改差异和产物。语法错误不能产生模型写入；运行异常必须报告部分修改/UNKNOWN及可用检查点。
4. trusted_code 权限独立于普通参数修改权限。声明副作用不等于已证明副作用；代码扫描/黑名单不等于 OS 沙箱；独立 Worker 也不意味着 Server 侧执行已隔离。
5. 默认通用安全路径与显式授权的可信代码路径分开。不满足代码运行权限或隔离要求时保留编译和其他可执行工作，运行测试记 BLOCKED，不能自动关闭安全检查来获得 PASS。
6. 不虚构 cancel API，不把客户端退出称作 COMSOL 已停止；继续使用持久 job 和状态核对。

## 8. W11：版本化本地帮助

1. 索引合法可访问的本地/已获准官方帮助，记录 source、COMSOL 版本、位置及内容哈希。
2. 6.3/6.4 过滤明确隔离。缺少目标版本资料时返回 NOT_FOUND/UNAVAILABLE；不能拿另一版内容冒充所查版本，也不以此阻塞可用 6.4 索引开发。
3. 文档、模型描述和错误文本是数据，不是权限指令。加入恶意指令样本，验证不会升级权限或外传文件。
4. 先完成可靠搜索、定位、片段返回与来源追溯。没有必要为了“RAG”先引入额外云依赖；扩展检索方式不能牺牲离线可用性。
5. 模拟双版本索引可以测试过滤逻辑，但不等于真实双版本资料验收通过。没有两版真实资料时 T043 对应实料子项保持未验证。

## 9. W12：计划、试运行、检查点与恢复

1. 明确区分静态 preview 和真正调用 COMSOL 的隔离 trial。静态检查不能声称证明几何可构建或求解会收敛。
2. 静态 preview 不写主模型；隔离 trial 使用显式创建、可追溯的测试副本，不替换 visible-main，不影响其他模型。
3. 按事务/阶段实施检查点，不默认每个 setter 全量存盘。记录保存次数、时间与磁盘占用；资源不足时报告受限，不用小模型冒充大型成本测试。
4. 真实执行“第一步成功、第二步失败”并检查 applied/failed/not_executed；恢复后比较规定范围。恢复生成新对象时更新 generation/ref，并让旧句柄失效。
5. 声明哪些内容可以恢复：模型设置/解/外部依赖/GUI 状态分别说明。外部文件和用户界面不会凭空随 MPH 一起恢复；GUI rebind 未实测时保留 unknown。
6. 临时节点与 trial 副本仅清理由当前任务拥有且身份确认的对象；清理失败触发隔离/核对，不继续盲目写入。

## 10. 平台与版本约束

核心领域代码不直接依赖固定 Mac 路径、bash/PowerShell、.app/.exe 后缀或某种信号。安装发现、路径、进程、Java 启动和 Desktop 动作放入平台适配层；COMSOL API 版本差异集中处理。先复用现有正确抽象，不为重构而重写已验证功能。

Mac 实测仅升级实际 OS/架构/build/动作的状态。Windows、Intel Mac、6.3、GUI 和未许可模块的缺失不阻塞独立 Mac 任务，也不标为已适配。不同 COMSOL 版本保持独立 Worker/classpath；不通过修改文件头实现降级。

只操作已授权项目和任务专用测试模型。使用历史 Server、PID、端口、私有 home 前重新确认身份；不杀用户 Server，不修改全机安全设置。需要额外系统权限时提出最小具体请求，并继续不依赖该授权的工作。

## 11. 验证、证据与交付

每个工作包完成实际代码、测试和文档，不只输出计划或占位接口。新增动作和旧入口都必须走生产 stdio → control → Worker 路径取得适用实测证据；直接 Java 辅助调查不能冒充 MCP 验收。

保留 Phase 1/2 全部历史记录。本轮建议新增 `evidence/phase3/` 与 `evidence/phase3_acceptance.json`；若仓库已有等价命名则沿用，不开互相矛盾的账本。

每个适用 case 记录：工作包/验收及子项 ID、源码 SHA/tree/脏快照、真实环境、请求、结果、断言、引擎日志、状态、限制和产物哈希。结果采用 PASS/FAIL/BLOCKED/NOT_RUN 并明确验证层级；不把缺失环境算作通过。

阶段边界执行完整软件回归，并回归受影响的 Phase 2 核心行为：一请求一次求解、同 key 不重放、错误传播、排队不绕过、修订/权限检查、Host 重连和最后完整文件保护。高风险故障注入只在对应隔离资源和授权充分时执行，不为复跑而填满普通磁盘或中止共享 Server。

更新 PROGRESS、动作 capability、已知缺陷、下一阶段依赖。保留用户改动；提交/同步遵循仓库当前有效授权，禁止 force-push、覆写远端较新提交或上传私有凭据、COMSOL JAR/许可证。若本轮可按现有授权同步，核对最终远端 SHA/tree，不把“本地完成”等同于“已上传”。

## 12. 停止条件

### 本机阶段完成

W08–W12 的本轮实现完成；所有当前具备资源和授权的适用用例及受影响回归通过；结构化协议和未知 wrapper 的 Java 动作有真实证据；trial/部分失败/恢复在限定模型上验证；文档索引可用；进度与能力状态准确。

缺失 Windows、6.3、Intel Mac、GUI、完整隔离账户等环境的测试明确保留未验证。回调 FAIL 只有在有新修复证据时才改变；否则写明限制和防止错误写入的已验证边界。

这可以标记“G2 Mac 可执行范围交付”，不得标记六组合或全部 G2 验收认证完成。完成后停止，不进入 W13。

### 受阻结束

已完成所有不依赖阻塞的本轮工作；剩余问题有准确 case、错误、缺失前提、最小所需授权/资源和可重放入口。若存在影响当前 Mac 核心正确性的 FAIL，不宣称本机阶段通过；保留失败，报告受阻结束，不无限重试也不通过删测试获得成功。

## 参考证据

以下均为固定提交来源，不是本轮重跑：

- https://github.com/Everwalker/comsol-mcp/commit/28605a6896668a64775ac6c1192ed50fef94b47f
- https://github.com/Everwalker/comsol-mcp/blob/28605a6896668a64775ac6c1192ed50fef94b47f/docs/comsol_mcp_design_v1/PROGRESS.md
- https://github.com/Everwalker/comsol-mcp/blob/28605a6896668a64775ac6c1192ed50fef94b47f/evidence/phase2_acceptance.json
- https://github.com/Everwalker/comsol-mcp/blob/28605a6896668a64775ac6c1192ed50fef94b47f/evidence/phase2/final_software/pytest.txt
- https://github.com/Everwalker/comsol-mcp/blob/28605a6896668a64775ac6c1192ed50fef94b47f/docs/comsol_mcp_design_v1/04_IMPLEMENTATION_PLAN.md
- https://github.com/Everwalker/comsol-mcp/blob/28605a6896668a64775ac6c1192ed50fef94b47f/docs/comsol_mcp_design_v1/03_ACCEPTANCE.md
