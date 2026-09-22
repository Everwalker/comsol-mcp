# NEXT GOAL｜G3.3：干净目录恢复、证据纠正与 W17 真实结果系统

## 0. 本轮授权范围和边界

继续 `Everwalker/comsol-mcp`，审查源固定在 `PIN.json` 的 G3.2 提交
`2cb4627924d1a3240818ea7cd00453d4bd2d2da8`，tree
`dd3095e89c640c19aeb151cd0e8efe4af3c55802`。

用户将清理旧本地文件，但保持联网。只依靠本工作包、固定 GitHub 源和本机合法安装继续。
不要要求旧聊天、旧env、旧审查包或旧工作目录才能开工；外部商业安装/许可证和用户未提交数据
不是可由本包重建的内容，按 RESTORE.md 明示边界。

**本轮：恢复 -> 审计/纠正证据 -> 修复W17 -> 补真实Gate A/结果验收 -> 清洁重建验证 -> 发布。**
不自动进入 W18–W26，不做 Windows 远程联调，不把 GUI、Intel Mac、6.3 或许可证外模块
作为所有独立 Mac 工作的阻塞。保留跨平台/跨版本适配接口，未测试组合仍 UNVERIFIED。
这份新 Goal 替代旧阶段的停止授权；不替代用户权限、共享Server和秘密数据保护边界。

必须实际改代码、运行测试、生成可审计结果，不只给计划或新增测试数量。

## 1. 恢复和基线

1. 完整阅读 START_HERE.md、PIN.json、REVIEW.md、RESTORE.md、ACCEPTANCE.md。
2. 运行本包软件自检，使用 `tools/bootstrap.py` 恢复新 repository；不覆盖现存目录。
   源恢复必须通过 commit/tree 和每个文件hash核对。若网络故障，保留明确失败，不能切到latest。
3. 在 recovered repository 建新工作分支/新venv；Git路线保留真实父提交。
   ZIP路线先补历史锚点再公开发布；不强推一个新建无父提交历史。
4. 读取仓库 AGENTS.md、CLAUDE.md、原始00_FULL_SPEC/01_ARCHITECTURE/03_ACCEPTANCE/
   04_IMPLEMENTATION_PLAN/05_DEVELOPER_TASK/06_CONTRACT_NOTES（按实际仓库路径找），
   当前PROGRESS、G3/G3.1/G3.2文档及phase4_1、phase4_2、w17台账。
   不把原设计里NOT_RUN默认值当成最新状态，也不把当前PASS文字本身当证据。
5. 运行本包 `tools/audit_repository.py --repo repository --output review/source_inventory.json`
   （按执行cwd调整）。生成全文件清单/字节读取/语法结果；它不是逐文件语义验收。
   对生产源码、测试夹具、证据生成器逐模块记录实质审查结论；历史二进制只记录hash/格式，
   不跟随旧symlink。发现缺失引用（例如 audit/semantic_review.json）标MISSING。
6. 新环境中先运行现有软件基线，原样保存输出、命令、cwd、解释器与source hash。
   历史报告1616/1不自动当新基线，失败/skip各自解释。
7. 仓库内普通开发指引/过期版本/阶段限制可依据本Goal更新；保留历史说明。
   不修改用户全局Codex/Hermes配置或扩大系统级权限。

## 2. 先纠正证据，再修改结果语义

创建 `evidence/phase4_3/BASELINE_REVIEW.json`，列每条主张及其实际支持来源。
`tests/test_g3_gate_a2_f02_reopen.py` 的 FakeReopenModel 与预设bytes/hash/字段检查，
只标 CONTROL_UNIT；`tests/test_g3_w17.py` 的 FWiredTree 只标 UNIT/CONTRACT。
不能标engine integration、real reopen或numerical acceptance。

在修正数值语义前，将受影响能力标为未通过并避免向真实任务输出“成功”的错误数值。
这是临时保护，不得永久用UNSUPPORTED替代原W17要求的实现。

保留 phase4_1/phase4_2/w17 原件字节不变，新增 correction ledger；PROGRESS和能力摘要
必须指向最新纠正状态，不能继续对外显示 W17 全部通过。修复后新真实运行可证明新状态，
但不能回填/伪造旧时间线。原测试可作为控制测试保留，不用删除它们来减少失败数。

每条证据至少包含：case_id、subcase_id、scope、evidence_level、source_commit/tree/dirty
manifest、command、cwd、interpreter、exit_code、runtime、请求/回复/断言路径、artifact hash。
`PASS`要求可重算断言；缺失日志、硬编码数值、自比SHA不能代替实际测试。

## 3. F02/F03：测度、选区、统计量、轴对称

重构为共享 MeasureSpec/SelectionBinding，而不是为每个函数继续复制条件分支。
`aggregate` 要按真实实体维数（点、线、面、体）与物理含义映射到已验证的API。
不能把所有请求都发送为 IntVolume；不能忽略spec.selection。

对于实场和显式正权重w：
- M = ∫w dμ；average = ∫wf dμ / M。
- population variance = ∫w(f-mean)^2 dμ / M；std=sqrt(variance)。
- RMS = sqrt(∫w|f|² dμ / M)。
- integral/min/max必须使用相同的真实选区/数据集/解轴。

明确零测度、空选区、非有限值和单位行为。不要用均匀节点算术平均冒充积分平均。
可以使用经过实机验证的原生平均/统计特征，或明确的临时积分组合；分母不得写死1。
轴对称记录“二维截面测度”与“旋转后的物理测度”；核实原生设置是否已含2πr，禁止重复乘。
`axisymmetric_applied_count`只能由真实算法/设置证据产生，不能由bool直接宣布正确。

先用 ACCEPTANCE 的非单位尺寸/常量/非均匀场基准验证，再增加复杂情况。
COMSOL原始单位和表达式必须回读；相同数值不同单位不是默认相等。

## 4. F04：统一数据集/解索引/数组轴

建立 SolutionBinding 与 FieldArray schema，显式携带表达式轴、outer、inner、时间/频率/
参数元数据、坐标轴、值类型/形状。不要依赖list长度或列名字猜解轴。
官方NumericalFeature的getData/getImagData是[expr][solnum][vertex]；不同getter/columnwise
布局必须由版本适配层标准化。禁止用 `transformed[inner-1]` 选择表达式轴冒充时间轴。

outer_indices不能硬编码[1]。通过实际SolutionInfo/已验证公共接口取得可用映射。
多个参数的值不能仅zip两个数组就声称完整组合。严格实现明确支持的 inner/outer/time/
frequency/parameter选择；不支持的选项明确拒绝，不能静默忽略。

数据集 `data` 引用可能是上游dataset，不是solution tag。解析Solution/CutPoint/CutLine/
CutPlane/Join等数据链，检测环、歧义、错geometry/comp/solution、过期generation。
没有可证明binding时 `binding_complete=false` 且相应数值操作不成功。

## 5. F05/F06：复场和坐标真实性

确认isComplex结果；读取失败不能当false。只有已经确认全实数的结果才能构造零虚部。
真实复场的imag读取失败或shape不匹配必须失败/UNKNOWN，不可以补0后成功返回。
统一preserve/real/imag/abs/phase，声明相位单位、分支与零幅值行为；统计前后复数变换
顺序必须显式，不能混淆 abs(mean(f)) 和 mean(abs(f))。

point evaluation与原sample_path共用坐标验证服务。validate点的dim、shape、数量和finite；
将input coordinate_unit/geometry单位/API坐标约定明确转换，frame不得只原样回显。
getCoordinates非空不代表匹配；比较shape、排序、单位、数值和选择映射，报告匹配误差。
不支持的参考系拒绝；缺回读为UNVERIFIED，不准VERIFIED。

## 6. F07/F08/F09：节点与文件副作用

所有Dataset/Numerical/Table路径按类型解析；不得只取最后一个tag，不得让geometry同名tag
误映射为dataset。创建前预校验可验证的整个输入，写后回读type/属性。中途失败立刻停止
后续写入，返回applied/failed/not_executed和实际不确定状态，不无条件继续所有properties。
与现有DomainOutcome、DispatchWitness、ledger、job事件串接，不另建旁路。

Field export的权限校验要发生在计算/创建目录/文件之前。destination相对授权项目根解析，
检查逃逸、symlink/非授权目录、覆盖授权；临时文件+原子发布+原文件hash保留。
自动artifact目录不得取任意cwd，应由ArtifactStore统一管理。

上游evaluate任何error、空有效数据、cleanup_failed或UNKNOWN，必须传到export的MCP/job/ledger；
不得仍生成一个看似成功的空JSON。不能仅凭异常类名认定写前无副作用。
所有serializer拒绝非有限JSON值；CSV对复数、轴/坐标/单位有明确列契约，未知format拒绝。

实现真正可由host请求的artifact/chunk读取或受权资源契约，绑定文件不可变hash、size、offset、
length、format。不能将chunk_info加上“把整个文件读进内存再拼回去”称为分页/流式。
明确引擎采样与文件导出的峰值内存/预算；受底层API限制时明示并用分块采样/预算拒绝，
不能悄悄先全量加载再宣称任意大数据支持。大数组仍保留全部轴和数据来源。

## 7. F10/F12：补齐W17范围并减少重复实现

Definitions Probe和Results Derived Values分开。`result.numerical_manage`存在不等于Probe已实现。
按原W17任务完成可用Probe创建/表达式/选择/读值/历史/表关联，保留用户节点；必要API缺口
用官方文档+隔离探针补，不把本来属于W17的功能改称W18以跳过。

将Dataset/Numerical/Table的类型白名单、typed setter、cleanup、solution binding、
serialization、动态effect判定收敛到公共服务。保留现有API兼容层，不改坏G0–G3已验证路径。
未来Windows/6.3只接平台/版本适配，不让核心继续嵌入固定/Users路径和某个build特例。

## 8. Gate A真实补验：相同已存文件重新打开

若旧MPH已经不在GitHub/本机，不能假装原文件已恢复。用可追溯源重新生成三条新夹具，
在新run_id/新sha下完整执行。所有生成仍通过授权MCP/公开操作；夹具构造与验收分开。

保存前记录：材料/边界/solver设置、实际空间场值、dataset/solution/解轴和非目标节点。
保存后hash、size、source snapshot固定。原作业quiescent后关闭本任务Worker/Host（不杀共享Server），
新Worker/新generation加载**同一hash文件**，不重新求解直接读取：
A >=3个内部梯度点和热流；B >=3个时刻且至少2个非初值时刻、>=3空间点；
C 修改后artifact（非初始夹具）中的目标变化、手工solver、Derived Values/表关联与场值。
独立再求解是另一个case，不能拿它替代存储解验收。

负控必须调用同一个生产检查器：错误artifact/hash、错误dataset/solution、清空存储解、
修改场值/缺Derived Values。不能用硬编码字符串不相等或直接断言假字典替代。

## 9. 执行关卡（先小闭环，再全量）

M0：干净恢复、旧台账纠正、依赖锁审查；原软件baseline + 旧失败保全。
M1：AST源码反例/contract测试与核心数值实现；先一条常量+非单位体积的真实求值链。
M2：维度/selection/轴对称、复数、inner/outer、坐标、Dataset/Numerical/Table/Probe分组实机测试。
M3：Gate A三条新存储解重开 + 阴性控制；正式MCP协议/危险状态/导出原子性/分块测试。
M4：受影响G2/G3回归、全量软件测试、wheel源外安装、干净路径再恢复、证据审计与同步。

同一Server的引擎操作串行；纯代码/独立数据验证可并行。不反复用同一个dirty测试模型跑整套用例。
某负控造成UNKNOWN时先查询原job和派发记录，证据证明quiescent后只对本任务模型恢复。
原操作请求与同key/body重试、全新观察请求的新key严格分开；不能靠自动换key重复求解。

现有G3真实成果不要求无差别重跑；根据修改影响建立依赖矩阵并重跑必要部分。
只要修改结果/保存/绑定链，不能沿用旧source的通过来认证新行为。

## 10. 资源、授权和停止空转

无真实COMSOL/模块/权限：继续独立实现、数值oracle、依赖/packaging/协议测试，记录
IMPLEMENTED_UNVERIFIED或BLOCKED；不能因为函数有fake测试就完成实机项。
当前模块完整能力仍以用户原始目标为准，缺能力要修复，不无限新增“验证门禁”代替实现。

普通本地临时目录、测试数据、工作分支和项目文档变更属于本Goal。
系统防火墙、SSH、许可证、COMSOL安装文件、共享Server终止仍需原权限规则。
没有新授权时复用安全的合法启动方式/只读检查或留下最小人工动作；不绕过限制。
缺环境的相同阻塞记录一次后继续独立任务，独立任务完成即受阻结束，不循环重试。

## 11. 本轮交付

仓库内保存：
- docs/comsol_mcp_design_v1/G3_3_PLAN.md、G3_3_FINDINGS.md、G3_3_OPERATIONS.md、RECOVERY.md；
- evidence/phase4_3_acceptance.json 和 evidence/w17_correction.json；
- evidence/phase4_3/runs/<run_id>/ 中环境、请求、回复、断言、日志、数值、文件hash、source manifest；
- 原设计Txxx对照表及本包ACCEPTANCE case_id映射；
- 新依赖机器锁、pip check/freeze、wheel install/资源完整性检查；
- 源文件/证据全文件清单和语义审查覆盖，缺引用清单，实际支持矩阵；
- 下一个agent无旧本地文件也能按RECOVERY.md继续的完整任务/恢复资料。

私钥、密码、Token、软件安装JAR、授权文档语料、venv和索引库不可进入公共证据。
新raw证据可以私有保留，但公开摘要必须有可追踪哈希，不能把不可取得的私有文件当通用恢复依赖。

最好在本机成功恢复源码后另生成 `release/COMSOL_MCP_SOURCE_WORKPACK_<sha>.zip`：
含实际源码/测试/必要公开设计证据、新Goal和恢复说明，排除秘密/缓存，记录全部文件hash。
这是agent实际取到源码后的归档，不虚称当前收到的在线恢复包已含整个仓库。
若无法随包分发某文件，注明类型、来源与重建方法。

## 12. 停止条件

成功：已在新目录独立恢复并通过核对；F01–F12已修复或有明确、正确的scope决定；
所有本机可执行的W17 required数值/MCP项和Gate A重开项完成真实验收；
旧证据保留、纠正台账生效；源码/运行/测试绑定；资源安全收尾；正常non-force同步有SHA证据。
此时标 `G3_3_MAC_W17_VERIFIED_SCOPED`，不是六平台/所有模块通用认证，然后停止，不进W18。

受阻：独立开发/软件验证已经完成，剩余是不可解决的合法环境/授权/资源阻塞，
输出 `IMPLEMENTED_WITH_BLOCKED_LIVE_ACCEPTANCE` 和最小人工动作。不能把仍然存在的代码缺陷
仅改名BLOCKED后宣称完成。不得再添加假引擎样例把失败覆盖成PASS。

最终回复只报告实际改动、真实测试、未验证项、恢复能力、当前SHA和下一阶段是否允许进入。
