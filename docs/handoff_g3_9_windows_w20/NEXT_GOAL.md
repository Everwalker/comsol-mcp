# G3.9：W20生产输入、只读检查、观测溯源与真实验收

## 目标和停止边界
基于 `886affadc83477e940238c723edd569d00559985`（tree `b9861e5dc407f2aff934d9438fa33ef631df06a1`），完成本轮缺陷修补，使Windows COMSOL 6.3与6.4能通过**普通已安装MCP**从真实模型取得验证观测并给出准确报告。保留现有Mac兼容、双版本引擎能力和已修复的空值/必需测点检查。**不进入W21，不再新增大批工具名，也不重建第二套Worker/registry/result/ArtifactStore。**

任务不依赖任何旧目录、旧聊天、旧工作包、私有Git提交、历史PID/端口或回执。保持联网可恢复公开源及依赖。用户清理本地文件不授权删除科学数据、COMSOL安装或许可证。

先读本包REVIEW.md、FINDINGS.json、ACCEPTANCE.md，然后读恢复仓库的AGENTS.md及 docs/comsol_mcp_design_v1 中05_DEVELOPER_TASK、01_ARCHITECTURE、03_ACCEPTANCE、04_IMPLEMENTATION_PLAN、06_CONTRACT_NOTES。旧阶段停止命令和“完成”摘要是历史内容，不能覆盖本轮用户Goal；保留其字节并追加当前阶段导航。

## 0. 先恢复，再核实
执行verify_package→bootstrap→audit_repository。恢复脚本会把本轮文档放入 docs/handoff_g3_9_windows_w20/。创建本轮PROGRESS.md，记录固定源、实际工作树差异、测试命令、结果、失败和下一步。
运行 tools/review_probes.py 可先复现隔离缺陷；用 --repository repository 比较恢复源码的目标函数可执行AST。它不连接COMSOL，不是产品测试通过。若源码或接口与审查不同，先对照验证，不盲改。
优先复用与修正既有模块和测试。只新增必要的当前阶段runner、观测规范及回归，不复制一套同名安全/结果/执行基础设施。

## 1. 先把公开工具输入打通（F01，阶段P0）
当前_tools_w20的签名为arguments:dict；Gateway直接传dict(bound.arguments)，后端不展开这层，领域函数却读取顶层criteria/expressions。必须用tools/list实际schema发请求复现。
选择一个明确、统一的领域请求契约：优先把工具签名对齐领域schema；确需兼容包装时只对明确列出的版本/工具做展开，不对所有操作任意flatten。不调用stub函数来绕过控制服务。
统一点号、下划线、operation_call入口，规范化请求后再计算幂等hash，execution、ModelRef和expected_revision不得丢失；重复字段不一致写前拒绝。注册测试必须调用工具而不只是数名字。
先在新wheel+普通MCP stdio上证明一个有效表达式和一个独立未定义表达式的不同结果，确认测试真正打到目标版本。此关不通过不得执行完整科学验收。

## 2. 非破坏、上下文正确的验证（F02/F03）
- 立刻删除boundary inspector中selection.all()等任何setter回退。Selection.all是修改，不是getter。
- 复用typed NodePath、正确component/geometry/physics维数和Named Selection读取。读取失败→UNVERIFIED/ERROR，不用空列表代替，再推断没有冲突。
- 不接受caller boundary_data/model_data覆盖“当前模型检查”。确需离线数据检查可保留单独显式模式，标INPUT_ONLY_CHECK，不升级模型验证或engine origin。
- 同实体编号在不同component/geometry/physics中不构成自动冲突；考虑特征激活、覆盖与有效物理关系，只支持经过测试的有限规则。没有温度边界可能是纯Neumann问题，按请求的规则与可判定性处理，不能普遍FAIL或PASS。
- structure/preflight中materials/selection读取异常需要进入规则结果；有节点不等于材料属性齐、网格已构建、study启用了目标physics。未实现检查不可列为checks_evaluated已完成。
- 表达式正则只能提示，不充当COMSOL语法/单位解析器。合法2*-3、2^-3、带负指数单位应通过真实语法；undefined变量、维度不一致分别测试，不能放在一个已有坏括号的请求中掩盖失败。
- 每个只读/评估测试前后核对相关节点和选区。失败与取消也不能改变用户节点；temporary节点只清理本次拥有的节点，清理失败沿现有DomainOutcome传播。

## 3. 用真实观测而不是“模型存在+请求值”（F04/F05）
建立**一个**ObservationRef契约并复用W17能力：
```
observation_id, origin, source_identity, runtime_id/build,
model_ref + generation/revision, dataset_chain + solution_ref,
outer/inner + actual_time/parameters, expressions/units,
selection/frame/coordinates, actual_measure_weights,
producer_operation_id/job_id/request_ids,
artifact_id/sha256, produced_at, validation_scope
```
Ref由后端产生并持久登记；保存的数据、元数据与哈希绑定。读回时核对来源、范围和同一存储解，不凭caller自报origin=ENGINE_EVALUATION。
validate.solution默认通过result.at_points/result.evaluate实际获取数据，或加载已登记的同模型同解的ObservationRef。caller values/observations可作为明确的外部数组计算，但不得声称当前模型数值验证PASS。篡改调用者值、不存在dataset、空dataset集合、跨模型引用、旧revision、错误单位必须被正确拒绝/降级。
Frozen oracle包含明确required集合、坐标时间、单位、误差范数和容差。构建不可变的规范化快照（不接受先看数值再改参考），正确校验bool、NaN/Inf、负权重、空集、近零分母。错误响应保持strict JSON，不能回传Infinity。

## 4. 真守恒与真收敛（F05/F06）
- 铜块基准沿用设计：L=0.05m，截面0.02×0.01m，k=400 W/(m K)，两端300/350K，侧面绝热。内部点参考312.5/325/337.5K、热功率幅值80W。**80W只能是参考，实际Q必须由场/边界积分读到。**在收集前冻结温度0.1K及热流1%阈值（变更需物理依据且在运行前版本化）。
- 使用W17测度/单位/选区实际计算边界通量、体源和储能。明确向外法向、正负号、归一化的物理含义；不把未提供项默认成0来掩盖缺项。power不允许“通过非空门禁后丢弃”；定义别名或写前拒绝。
- 瞬态基准L=1m、alpha=1 m²/s、两端300K、初始300+10sin(pi*x/L)K。actual solution时间映射到x={0.25,0.5,0.75}、t={0.01,0.03,0.1}，参考公式在BENCHMARKS.md。不存在指定时间不能取最后一列顶替。
- 三网格与三时间精度分别实际运行。每级保存实际参数回读、网格元素/DOF、真实耗时、source、job和结果。一次只改变要研究的精度变量，其余控制；输出tlist不是求解器实际步长约束。用非平凡正弦场，避免精确线性稳态场在任何网格都机器精度而失去判别力。
- validate.convergence可分析已有真实case refs；不必为此提前实现W21通用扫描器。误差从观测与冻结参考算，不接受caller error作为engine结果。判定必须检查真实细化、有限非负误差和目标阈值；0阈值不得被`or`忽略。趋势/渐近阶/平台现象单独说明，不把单调当充分必要条件。

## 5. 报告不升级状态且安全发布（F07）
报告只汇总已登记validation_run或ObservationRef和规则结果。三轴执行/数值/物理独立；ERROR、BLOCKED、UNSUPPORTED、UNVERIFIED、缺required、NOT_APPLICABLE都不是无条件数值PASS。未知状态拒绝。
嵌套结果按显式schema聚合，不靠遍历任意dict中的status字符串。无实验/独立物理证据physical保持UNVERIFIED；负控测试通过不使故障模型通过。
复用ArtifactStore授权路径、公开产物登记、原子staging与覆盖策略。JSON/Markdown作为一组预检目标（包括已存在同名副文件），默认不覆盖。提交中途失败准确报告partial/artifact状态并保留旧字节。报告文件包含完整ModelRef/解绑定、三状态、规则覆盖与原始证据hash；不得用输入JSON的哈希冒充执行真实性证明。

## 6. 废止拼出来的“原始MCP transcript”（F08/F09）
当前runner先直接调Python/Worker，再make_v_record拼出tools/call request/response、固定PASS和固定build。这不是原始生产记录。保留其确实存在的引擎基准价值，但按函数层/原生adapter层重分类。
新的runner必须真的启动标准安装的MCP进程，通过客户端initialize、tools/list、tools/call读取**真实序列化响应**。原始日志在调用发生时采集；case记录从这些数据导出，不许构造一个看似tools/call的文件来补角色。运行时身份取实际Server/Worker，不拼build或worker_id。
报告器只读取运行结果，不运行模型、不补观测、不补PASS。删除观测产物、破坏哈希、错绑定、缺请求/响应、helper替代public测试，都要使required case失败或未跑。报告检查器不能替代代码审查与实机执行，不能声称远程证明或反伪造。
保留旧证据原字节，在新EVIDENCE_CORRECTION中准确记录：哪些温度可能来自原生采样、哪些80W/守恒/收敛数值是常量、哪些transcript为重建记录。不得笼统说旧一切都是假的，也不得继续把旧44/44当认证。
全回归必须真实调用pytest并保留原始输出/returncode/环境，不能在新runner中写入1922等固定数字。同理源码bridge从实际文件计算，不照搬旧文件数。只跑受影响旧原生回归，避免无效重跑所有阶段。

## 7. 执行顺序和证据
M0恢复+隔离复现→M1单公开API输入闭环→M2只读与观测核心→M3两版本真实守恒/收敛→M4报告和恢复回归。
每完成一小关更新PROGRESS；失败先定位上游并缩小测试，不用同一个坏状态继续跑几十个用例。Windows两版本独立记录，允许安全软件工作并行，单Server API串行。不能因无法访问Mac或云端凭据阻塞当前Windows核心。
本轮产物集中 `evidence/g3_9/<run_id>/`，源码对应清单包括生产代码、调用器、测试、基准定义与配置；不包含凭据。模型原始用户数据不上传。

## 成功停止条件
ACCEPTANCE.md中本轮required目标记录真实完成；不存在用伪造/输入数值标成引擎观测的路径；公开三入口行为一致；验证不改用户模型；两版本独立证明守恒和收敛；错误报告不升级状态/覆盖文件；源码、证据、文档相符；交付从干净目录可重建。停止于W20，不进入W21。

## 受阻停止条件
真正的COMSOL许可、Windows授权、隔离或资源不可用，记录具体命令、错误、未完成能力、所需权限及可重放入口。继续其余安全可执行修补；最后明确阶段未完成，不能把blocked标PASS。不得为达Goal取消权限检查、复用旧凭据或制造native记录。
完成的公共代码可按已有明确权限正常commit/推送；没有推送授权只交付diff/bundle，不说远端已同步。不强推、不上传用户模型/secret。
