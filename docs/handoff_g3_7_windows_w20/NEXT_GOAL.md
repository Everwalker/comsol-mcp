# G3.7：Windows 6.3/6.4 生产链补修 → W20 分层验证

## 0. 本轮授权与成果
基于 PIN.json 的固定公开源码继续实际开发，不只给建议。
本轮授权 Gate A 的安全、状态、证据和生产链补修，以及随后 W20 的结构/数值/物理验证服务；不授权 W21–W26 新功能扩展。
用户已有 Windows COMSOL6.3和6.4，两者都需要独立验收。沿用同一代码，保留Mac兼容；当前不要求先SSH到Mac或由Mac统一改代码。
本文件更新旧AGENTS/交接文件中的阶段停止边界，但不放松数据保护、许可证、进程归属、授权及证据标准。

## 1. 必读和恢复
先读本包 START_HERE.md、REVIEW.md、FINDINGS.json、WINDOWS_PLAN.md、ENVIRONMENT.md、ACCEPTANCE.md、PIN.json。
执行verify_package、bootstrap、audit_repository；旧目录一律非前提。bootstrap为源码恢复工具，不是已通过实机的项目。
进入repository后读：AGENTS.md，docs/comsol_mcp_design_v1/05_DEVELOPER_TASK.md、04_IMPLEMENTATION_PLAN.md、01_ARCHITECTURE.md、03_ACCEPTANCE.md、06_CONTRACT_NOTES.md；再读现有PROGRESS、handoff_g3_6_windows、最新acceptance与实际实现。
核对git status并保留已有用户改动。不要假定远端main与PIN始终相同；本轮基线固定，发现后续修改可记录/正常集成，不静默换源。
创建 docs/handoff_g3_7_windows_w20/PROGRESS.md，记录每项实现、执行命令、实际回读、失败与下一依赖。只创建必要文件，复用现有模块/工具/台账格式，不复制几套基础设施。

## 2. Gate A：先确保生产执行可信
### A1 作业终态与RPC（F02）
让OperationStore的finish/update/transition统一检查合法状态转移、终态不可被覆盖、操作与job结果一致。
原子仲裁返回实际持久状态/结果给ControlDaemon；_finish不得返回被拒绝的候选success或发伪SUCCEEDED事件。LateResultRecorded保留原结果但不能改权威结论。
排队→开始与排队→取消竞争必须在同一原子边界裁决；取消获胜零Worker派发，开始获胜按实际运行中语义处理。UNKNOWN/RECONCILING取消只记意图，不能凭请求转为RUNNING。
在真实公共调用、job_status/job_result、SQLite与事件上同时断言一致；覆盖取消/超时/晚到成功/晚到失败/双请求重试。

### A2 Windows权限、隔离与强停（F03–F05）
以当前可信用户SID设置任务专用private目录；错误码/异常/缺少身份均报错，实际回读DACL。处理意外显式ACE；不要重置用户数据目录/系统目录权限。权限未落实之前不发布token、启动可读取secret的服务。
Windows隔离观测不得伪造command/birth。实际COMSOL监听者、创建时间、可执行路径、精确endpoint/worker peers、认证与私有配置满足原生产verify_owned_server；只验证Python socket/解析器不算。
不得以删require-isolation、mock proof、在测试中注入trusted service或全局改防火墙来通过。监听限制涉及系统权限时最小授权；无法获得保持BLOCKED，不关闭防线。
强停必须有后端持久lease/job/session/runtime对应关系和明确任务归属；前端authorized布尔值不是所有权。租约缺失、身份不可读/过期/错版本/不匹配均拒绝。
Windows使用平台接口验证创建身份/进程句柄；is_process_in_job指定pid必须查目标而非自身，失败UNKNOWN而非False。等待所拥有父子进程及监听端点确实停止后才engine_stopped=true。只结束任务拥有的进程，不做进程名批量杀。句柄权限和64bit ctypes签名完整。
实际原生cooperative cancel不能确认可用时继续UNSUPPORTED/UNVERIFIED，不外推整个COMSOL产品无取消功能；owned termination须真实验证，不依据硬编码字符串VERIFIED。

### A3 双版本环境与缓存（F06）
发现6.3/6.4实际安装、java/javac版本/供应商/架构、完整引擎build。优先外部JDK11x64；其它实际可用版本独立实验记录。
Worker缓存至少绑定源代码、JAR集合内容哈希、manifest、实际引擎build、JDK和架构/编译选项。路径名不是版本权威。同名JAR替换、旧缓存重用、两版本交叉引用负测。
分离两版本private prefs、worker、control home、端点、锁、结果、证据。四路径：wheel安装A、源码B、科学项目C、任意cwd D。无PYTHONPATH/可编辑安装回退，在新目录运行公开MCP。

### A4 原生生产证据纠正与补齐（F01/F07）
旧WD台账不删除；追加每项实际覆盖/不足/替代测试。测试函数写死True、helper成功、文件存在或schema数量不得自动成为NATIVE/PUBLIC PASS。
至少两版本各执行一次：正常配置冷启动MCP→绑定runtime/model→参数/变量/函数→几何/材料/网格/研究→求解→实际数值→product CSV/artifact.read→ImageContent→保存→新Worker同版本重开。以公共operation或受控code.execute_java完成，不能绕开生产权限/作业门禁使用测试私有worker入口作为唯一证据。
测试期不要注入人工SessionLedger/ExecutionService、手改数据库RUNNING或造成功信封。可以保留这些作为CONTROL负测，但不可代替原生链。
WD19替代测试须同一由生产入口提交的正在计算job：记录Worker request已开始/未结束的观测区间，在此期间测状态延迟；不能在另一个空daemon里造job。记录50个样本实际时间/状态，短任务未覆盖时应改测试而非造RUNNING。
断开host、重启control、排队取消与同key重试验证原job无重复写，原请求仍可协调。服务退出后的旧ModelRef无效。
真实result.field_export与artifact.read，CSV读回逐值/单位/四轴比较；真实plot.render返回ImageContent，错误和交付失败保留job/产物。若云端凭据不存在，真实stdio与HOST_DELIVERY_UNVERIFIED分开，不阻塞无关任务。
真实时间轴来自SolutionInfo/存储解；测试多个表达式、外/内解、复数、统计，不能用手写tlist或平均T范围替代。
实际运行源码和公开发布源码逐文件hash对应；不依赖旧私有SHA。每条required测试都校验expected/observed、关键断言以及证据路径哈希。

### Gate A通过条件
A00–A15按ACCEPTANCE要求满足，两版本基础安全和生产纵向链均通过。unsupported原生协同中止/缺云端凭据作为已声明能力边界，不作伪PASS，也不阻止不依赖它们的W20。
若基础生产/安全仍失败，不宣称Gate A过关，不进行依赖它的W20原生验证；可以完成独立W20软件设计/测试并以受阻结论收尾。

## 3. W20：结构、数值与物理验证产品能力
### B1 结构预检
先查原动作目录，复用/实现对应validation操作，不另建同名相近平行系统。将每项规则标记适用模块、版本、维度、读权限、覆盖范围与不支持原因。
初始覆盖：component/geometry存在和构建错误，named selection非空与维度/范围，材料域覆盖与当前物理所需属性，相关函数/单位，物理/边界/初值激活，网格域覆盖与可读质量，Study/Solver连接与解存在。
只验证规则能证明的内容。未知getter、无法解析表达式、未支持物理模块返回UNVERIFIED/NOT_APPLICABLE/UNSUPPORTED，不当作未发现错误后的PASS。选区名字存在不等于目标表面正确。
纯结构检查不应偷偷建模/求解/删节点；需临时求值或隔离计算时明示EVALUATE/COMPUTE副作用并走现有权限、串行队列和cleanup。

### B2 数值与守恒
通过已验证W17服务读取指定dataset/solution/outer/inner及实际时间、参数、坐标、单位、测度和复数值。复用FieldArray/ArtifactStore，禁止另写简化版求值器或用CSV手写代替导出。
提供有限且明确的指标定义：finite、范围、统计、指定selection积分、标准化守恒残差、基准误差。小分母、空选区、单位不一致、复数丢失、错解/过期解都要拒绝或UNVERIFIED。
数值成功必须来自实际值和冻结期望/容差，不能回读错误材料后修改oracle，也不能失败后放宽阈值。

### B3 独立基准与收敛
使用下面的小型解析夹具，不把它们当作用户工程模型。期望函数由独立公式生成，参数/公式/tolerance在运行前冻结；不读取观测值来生成期望。
(1) 稳态铜块：L=0.05m，横截面0.02m×0.01m，k=400W/(m·K)，两端300/350K，其余绝热；x=.0125,.025,.0375处312.5/325/337.5K，总传热80W。温度误差≤0.1K、端面热量相对误差≤1%为拟定起点，在执行前固定。
(2) 瞬态合成扩散：L=1m，k=1W/(m·K)，rho=1kg/m³，Cp=1J/(kg·K)，两端300K，初温300K+10K sin(pi*x/L)，其余绝热；alpha=1m²/s，T=300+10 sin(pi*x/L) exp(-pi² alpha t/L²)。在x=.25,.5,.75m和t=.01,.03,.1s实际存储时间核验。拟定温度误差≤0.1K并在执行前冻结。参数是数学测试夹具不是材料建议。
至少3个网格或3个时间精度级别进行数值收敛；记录实际网格/容差/步长、误差、运行资源。不要求误差每一项严格单调，但须说明收敛趋势及任何不满足判据。求解仅在明确授权隔离副本/检查点内，保护主模型和用户节点。
输出版本间比较，但不可用“6.3与6.4相同”代替独立正确性。

### B4 三层结论与报告
每次返回独立状态：execution_status、numerical_verification_status、physical_validation_status。API调用成功不等于数值正确；数值正确不等于物理假设/实验验证成立。
无实验或独立物理依据时 physical_validation_status=UNVERIFIED（即使解析测试通过），不能为了整体PASS伪造实验。报告支持NOT_APPLICABLE但须理由。
输出JSON和Markdown，经受限artifact注册与读取；绑定源码/runtime/model/解/时间/选区/单位、规则版本、输入假设、冻结期望、原始观测、误差/容差、覆盖/排除项、警告和hash。完整原始数值在产物，wire有界摘要。
调用权限不足、原作业超时/UNKNOWN、cleanup失败时，验证报告不能掩盖执行风险；复用W19恢复，不能换key盲重算。

## 4. 测试与停止
按ACCEPTANCE_CASES.json推进；先局部负测再最小原生链，再相关回归，避免每个小改动都重跑全30个旧用例。
允许正式unit/control注入测试故障；要求PUBLIC_MCP_NATIVE的正向链不注入测试服务、不假造proof或数据库状态。原生资源不足时记录BLOCKED_LICENSE/ENVIRONMENT，继续独立实现与软件回归。
每次验收冻结源码并记录run_id、commit/tree/dirty与文件清单、解释器/JDK/COMSOLbuild、真实命令/退出码、关键请求/响应、模型与产物哈希。skip逐条说明；不能把Mac历史结果提升成新版本实机认证。
安全扫描不公开token/私钥/运行时数据库；脱敏说明保留原证据的hash与范围。只清理本任务拥有且已识别的运行时，不停止外部COMSOL/Java/Desktop。
成功停止条件：Gate A通过；W20要求的产品动作、两版本实机/数值验收、相关负测与软件回归完成；源外wheel与新目录恢复通过；报告/进度/能力矩阵、源码桥/校验和与交付包一致。只在授权的普通分支同步，不force；未同步需BLOCKED_SYNC。
受阻停止条件：完成所有不依赖阻塞的工作，清楚报告未满足项、具体证据和下一步；不得标记完整G3.7通过。
严格停止于W20，不自行进入W21扫描优化。
