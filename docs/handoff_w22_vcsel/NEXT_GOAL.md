# 当前任务：W22 VCSEL领域真实验收（主Agent开发，独立Reviewer）

## 1. 范围：终于进入下一工作包，不再全局收口
W21已由仓库最新源与独立复测证据限定批准。本轮直接进入原计划W22：静态非轴对称二维热源、阵列/环功率、距离扫描及热预算。依据`frozen/ORIGINAL_W22_REQUIREMENTS.md`的T016/T045，按`frozen/W22_CONTRACT.md`六项固定交付实现。W20/W21原冻结字节在baseline_contracts作为历史依据，不让其旧stop标记禁止本轮W22。

只重测被W22改动影响的W13/W16/W17/W18/W20/W21条目；不重复完整旧验收，不再建立新的G3.x全局审查。新非阻断建议进DEFERRED_BACKLOG；依据BLOCKER_POLICY判断，不扩大required集合。停止于W22，不进入W23光纤耦合、W24胶水、W25全部GUI或W26全面发布。

## 2. 全新目录恢复与实际环境
按START_HERE/RECOVERY先校验包再恢复PIN固定提交。当前2839e177已含最终W21生产代码和证据；旧RESTORE_CURRENT的c2ca+patch路线仅历史，**禁止再次应用旧补丁**。新overlay在repository/docs/handoff_w22_vcsel/。
读取恢复仓库的AGENTS、原架构/工作计划/验收、最新CLOSURE_RESULT、REVIEWER_FINAL_W21_SCOPED和W21已有原始日志。历史绝对路径/端口/token/回执不复用，当前Windows6.3/6.4路径与build重新发现。新建venv/选JDK/读取各版完整classpath，合法软件与许可证是外部前提。
代码、venv、科学项目、私有控制与公开产物根分开。使用正常wheel/stdio入口；不能以测试注入Service/直接Python helper替代PUBLIC_MCP验收。只用任务所有实例与模型，不改用户在用Desktop/共享Server。

## 3. 主Agent直接开发，独立Reviewer真实审查
完整执行TEAM_PROTOCOL。主Agent可以设计、编码、debug和开发测试，不需要独立Developer。必须由Host实际创建独立Reviewer上下文；给其冻结合同、当前候选、diff和原始证据，不能只交总结。Reviewer默认不改生产代码，可以独立写反例和测量程序。
第一次跑科学模型前，双方确认一次benchmark_spec与预算、阈值和数值参考。主Agent执行→冻结候选→Reviewer依原合同读代码与复测→必要整改；不重复制造一套签字/哈希平台。已有可信证据允许有理由地继承。
如果Host没有独立子agent能力，继续可做实现/自测并明确REVIEW_BLOCKED，不伪造会话和批准；不擅自安装/连接外部云账号。

## 4. 最短实现链：来源→热模型→指标→W21
遵循W22_DESIGN.md，优先用本包reference合成输入起步（其中没有真实设备材料/吸收率结论）。生产框架必须能接受其他显式输入，不把19个发射器、12.45W或gaussian写死到通用工具里。

D1：参数化环/阵列清单，分清每颗/每组功率、active mask与中心坐标；静态2D数据既可生成也可导入。导入文件/核模型/单位/归一化/外推范围有哈希和版本。不得换成径向平均，至少两个同半径不同角度点独立对照。数据有效区域之外的外推策略明确，默认不凭空产生功率。

D2：建立实际几何、材料/初温、边界入热和散热。分清emitted、incident-on-workpiece、absorbed、ROI input与lost/outgoing，alpha不能重复乘，W/m²不能当W/m³。用实际selection与积分计算ROI均值/std、max/min和功率，不以稀疏等权点冒充面积平均。热流符号/稳态平衡或瞬态储能项明确。原生求解必须经过当前MCP→ManagedBackend→Worker，所有正确性断言取实际读回。

D3：至少三个L，结合受限环/组功率优化。现有W21执行层只有显式采样/时间/标量指标，必要时只加领域输入准备和ROI指标适配：依赖哈希、模型/解、预算和单位不变。可预计算按L分层的基函数以保持每个case固定输入，也可显式重载外部源；不能仅改L数值而继续用旧q图。文件改名不等于内容变，文件同名不等于相同cache。修改某个源值的负控必须使新case重算。不要让内部orchestrator重新排队到自己占有的同一队列造成死锁。
静态热源可以用瞬态温度响应；若采用稳态case扩展，正确表示其解轴，不伪造时间值。最佳候选只在实际求解成功、预算/物理单位/目标约束检查后可比较。

D4：同一模型限定预算内给best verified feasible或未找到可行；不能用analytical/demo代替生产输出。独立Reviewer在两版本各新解一个已返回的候选并重新算ROI与功率。至少一个明显不可行目标有明确能量上界证明；一般搜索失败只表示未找到可行，不声称全局不可能。

D5：原生COMSOL源图、温度图、原始CSV/JSON、参数/功率表、预算与条件齐全。图像绑定case/L/power/dataset/solution/时间/坐标系，不让旧图补位。至少保存代表或最佳模型和其外部输入，在新路径用同一版本新Worker重开，读回存储结果/设置；无旧未提交数据也能重建。恢复需要的新数据必须纳入交付或有确定recipe，不能只写一个本机绝对路径。

D6：Windows6.3与6.4分别真实执行，按同一benchmark_spec与阈值，与独立源/数值参考比较。不是两个版本互相接近就算正确。保留Mac兼容，未访问Mac/GUI/云端Host单列UNVERIFIED。没有外部测量时physical validation仍UNVERIFIED，数学/数值验证可通过但不能承诺实际高温晶圆性能。

## 5. 对症修补，不重开旧工作
本次源码审查未发现需要阻止W22开始的新A1/A2。以下属于W22新增适配，不推翻W21批准：输入源/L/cache绑定、ROI面积指标、必要的stationary Study类型（若选用）。不要在历史metadata preview helper中再全局找风格问题。
若W22操作确实发现会读错解、越权写、返回旧图等严重回归，先最小复现修补并仅复测相关旧条目。不能直接删除隔离/readback/effect检查来“兼容新领域”。

## 6. 证据与有限工作节奏
一份PROGRESS，一份W22结果账本，一份每候选Reviewer结论足够。每个D1–D6分别记录两版本证据，包含真实命令、source/run/build、原始MCP请求响应、W21case/producer、源文件hash、模型/解、实际指标和图。拟测fixture与原始观测分开，不填固定PASS或把Python reference当引擎结果。
建议里程碑：M0恢复与合约/夹具冻结；M1静态2D源导入和角向对照；M2材料散热原生温度与功率闭合；M3 L扫描及功率搜索；M4独立候选、不可行、图像/保存重开；M5源外安装/受影响回归/交付。6.4做通小链后尽早跑6.3，不等所有功能写完才看版本兼容。

## 7. 正常结束
六项冻结交付均满足、独立Reviewer复测并无A1/A2，即批准W22_SCOPED_APPROVED，不能因待办或新增想法拒签。输出：实际完成范围、最佳候选和物理输入假设、相对基线改进/无改进、未测试项、原始证据与重建路径、最终源SHA与正常同步状态。
缺真实授权/许可证/设备时只暂停依赖项目，完成其余代码与软件测试后明确受阻交付；不能用mock补native，也不循环同一权限错误。无额外权限不变更全机网络/安全/安装。现有明确push授权可普通非force同步，否则交付本地commit/patch。
最后更新可恢复包到本轮最终公开源或提供审核过的源码快照，不让用户只拿到一份更新的报告。**完成后停止，W23作为下一候选，不自动执行。**
