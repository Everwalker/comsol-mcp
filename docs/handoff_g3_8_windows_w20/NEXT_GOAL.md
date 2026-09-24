# G3.8 执行目标：纠正证据，完成真实 W20，不进入 W21

## 0. 交接上下文与权威来源
用户会清理旧本地文件但保持联网。只使用本包及可访问的官方外部文档推进，不依赖旧聊天、旧 venv、旧 private receipt、未公开 git 对象、特定机器目录或之前的 ZIP。Windows 已安装 COMSOL 6.3 与 6.4；两版本独立验收，Mac 架构保留。

基线 `59d741d6e2514925fcabe3eb8fa7a1661e309779` / tree `1d60b1fc4d7d5a016dac1bf6a1bff2bb882384f0`。先读本文件、REVIEW.md、FINDINGS.json、ACCEPTANCE.md；恢复后读项目 AGENTS.md、原始 05_DEVELOPER_TASK.md、04_IMPLEMENTATION_PLAN.md、03_ACCEPTANCE.md、06_CONTRACT_NOTES.md 与实际代码。旧文档“已经通过”和停止边界不是新证据，不重做有证据的完成项。

**当前审查结论：底层双版本引擎和部分控制修补有进展，W20 仅有框架/纯数据比较，不能据生成报告宣称生产和原生验证完成。保留已有成果，修复本包列出的具体路径。**

## 1. 恢复与基线核对
按 START_HERE.md 验证、恢复、逐文件审计；新建虚拟环境，盘点两套 COMSOL、JDK、Host、权限。记录恢复源、当前运行源码、Python/JDK/runtime build，不将恢复源 SHA 当作修改后运行 SHA。实际代码与审查不同，逐条复核并记录，不盲改。

运行本包隔离探针前使用 --repository 做 AST 对照。探针只定位风险，不计入原生验收。补测试应进入仓库并覆盖产品公开入口，不复制错误的 helper 期望。

## 2. 第一关：证据纠正和 Windows DACL 必要修补
### F01：报告不能创造事实
- 保留历史 JSON 原字节，追加 EVIDENCE_CORRECTION.json：逐条标明工具 generate_w20_evidence.py 的合成稳态、解析扰动瞬态、手填收敛/资源数值只属于 SYNTHETIC_FIXTURE。
- 原 generate_g3_7_report.py 按模板硬编码 PASS、required_evidence、production_entrypoint 的路径必须退役或改为从真实运行记录计算。输入缺失/报告文件坏/没有实际调用时必须 NOT_RUN/ERROR，不能生成 PASS。
- 所有纯比较/合成演示可保留在 tests/fixtures 或明确的 examples 中，不可作为 production native evidence。哈希校验通过不证明原生运行。
- 新台账更正旧结论但不抹除真正的稳态求解、绘图、保存重开和软件回归成果。
### F08：ACL 读回必须比较真实 trustee
- 保留可信 SID 查询和命令退出检查；修复通过字符串包含判断主体、用户名在路径中误匹配、没有解析到 ACE 时仍成功的问题。
- 优先使用安全描述符/ACE/SID 原生结构或能验证的机器可读方式，不靠英文用户名子串；检查实际权限掩码、继承、allow/deny、NULL DACL/empty DACL、必需可用访问和额外主体。
- 策略可明示 SYSTEM/Administrators 等系统主体，但必须精确 SID 与权限；不把目录名里的用户名当成允许证明。失败时在 token/prefs/control endpoint 公开前停止。
- 仅修本轮拥有的测试目录，不批量改用户目录 ACL；外部权限问题不通过放宽策略绕过。

## 3. 第二关：把八个 validate.* 接到真实数据
### F02–F04：覆盖和来源
- validate.structure/preflight：基于指定 ModelRef/runtime 的实际 component/geometry/selection/material/physics/mesh/study 检查；区别存在、激活、构建、绑定正确。Getter 不支持/失败、未知物理、规则未实现返回 UNVERIFIED/UNSUPPORTED 与 coverage，不得进入 ready_to_solve=true。
- scope / model_data 仅是筛选或明确 provided_data 模式，不能在真实读取失败后被拿来当事实。没有必需观测、空规则集、读取零节点、无 solution 都不能验证 PASS。
- validate.expressions：真正使用目标版本的表达式求值/单位路径；非法语法、未定义变量、单位不匹配、NaN/Inf、字符串输入不被处理等必须识别。可保留纯字符串检查但只报告它实际做的事情。
- validate.boundary_conditions：从真实物理 feature 与实体/NamedSelection 关系检查已支持的热学规则；规则名不是 PASS。冲突判定要考虑物理接口实际覆盖/优先级语义，不把所有重叠当冲突，也不把任意无效设置当合法。
- validate.solution：先验证解存在、存储轴、dataset→solution 绑定与 freshness，复用 W17 真实求值；调用者允许给期望与 criteria，不允许伪装 observations。明确区分 ENGINE_EVALUATION、VERIFIED_ARTIFACT、CALLER_SUPPLIED、SYNTHETIC。
- 提供数据比较模式时必须独立标记、不绑定实际模型认证；生产验证以引擎结果或后端已登记、完整匹配来源的产物为依据。
- required observations 必须全齐，重复、缺项、错单位、未知 oracle、错误坐标/outer/inner/时间不得通过。禁止只检查已有观测的子集。
### F05：数值定义和不可变 oracle
- 稳态功率/热流、体积平均/统计、守恒残差必须有实际测度、单位、法向约定、归一化定义。瞬态守恒必须考虑储能项和体源，不能默认 inflow=outflow=0。
- 空数据不是正确数据；权重/容差/尺度/值的 NaN、Inf、布尔、非法符号应按明确类型拒绝。直接复用 FieldArray/MeasureSpec，不另起会丢轴的均匀加权路径。
- FrozenOracle 用不可变内容快照、完整 required-set、确定性 digest 与 freeze 前设置；公开字典不可绕过 freeze。来源哈希、单位、测点、时间、误差范数、容差一同冻结。
- 无模型来源的数学计算结果可以成功，但不称真实模型已通过。三层状态独立，未做的物理验证永远不能因数值 PASS 自动升级。
### F06：收敛不是三条递减数字
- 每一级都关联真实 mesh/solver/time 配置回读、实际计算和观测产物，误差由同一个冻结 oracle 计算，不接受用户直接输入 error 决定收敛 PASS。
- 网格、时间步、solver tolerance 分开研究；只有 tlist 更密不能当求解器内部步长更细。同一 mesh 多跑三遍不是三个精度级别。
- 判据包括目标误差或解差阈值、变化的真实精度参数、有效级别数、范围与限制。误差严格递减不是充分条件，也不是唯一必要条件；精度已达舍入平台可说明，并对非单调结果分析而非自动算伪物理 FAIL。

## 4. 第三关：报告与生产边界
### F07 / F09
- validate.report 只聚合已登记验证 run 及其不可变结果，不默认为 runtime=6.4 / dataset=dset1，不把任意 data 格式化之后写成 numerical PASS。
- 内容哈希绑定原始响应、观察、冻结期望、模型/解/运行身份、规则与代码；不是 hash(时间+任意data)。报告时间可独立存在，不参与替代证据。
- Markdown/JSON 均展示全部规则状态、必需覆盖、缺项、输入假设、误差/单位、告警、排除和三层结论。来源或证据不完整则明确 UNVERIFIED。
- 文件输出使用统一 ArtifactStore/授权根/登记/原子发布/覆盖策略；目标是目录、权限拒绝、磁盘失败、上游计算失败都不能报告“输出成功”。生产公开路径另做越界/私有目录/覆盖负控。
- 验证读取必须通过正常权限/队列；临时求值节点归属和 finally 清理一致，失败/未知/脏模型状态不隐藏。默认不 solve、不改边界或材料来让检查通过。
- code/NodePath/版本/registry/schema/legacy fallback 保留统一基础层，新增或修复 effect 与动态行为时更新真实 schema 和源外 wheel 资源；不能通过把所有动作标 READ 逃避写入控制。

## 5. 第四关：真实 Windows 6.3 与 6.4 验收
严格按 ACCEPTANCE.md 执行：每版本独立的公开 MCP 冷启动、真实模型状态检查、无解/坏表达式/缺观测/错误边界负控、稳态与瞬态解析基准、至少三个实际精度级别、报告写入、数据/图像/重开回归。

构建 fixture 可以使用受控 Java；但验证必须走产品 validate.*，不是验收脚本自己的替代算法。所有 native 用例保存请求、响应、run_id/operation_id/job_id/worker/runtime/model/solution 身份与有界脱敏日志。不能给一段直调 Python helper 的结果填 production_entrypoint=true。

测试用例的 PASS 指“符合预期行为”：故意错误模型应出现 validation FAIL，这个负控测试可以 PASS；不能要求每个数值结论都 PASS。执行失败与验证不通过分别传播，按冻结 schema 处理 isError，禁止模糊化。

运行中的真实 job 检查必须关联正在执行的那一个引擎请求；不手工插入不相干 RUNNING 记录。W19 未证实的 owned termination 继续保留 UNVERIFIED 或禁用，不让无 lease 路径进入本轮验收。不要重新开发 W19 的全部范围，也不以未授权系统修改为前提。

## 6. 独立基准（本轮测试设计，非实际工程材料建议）
1. 稳态：铜块 L=0.05m、截面0.02×0.01m²、k=400W/(mK)，左右300/350K，其余绝热。内部x=0.0125/0.025/0.0375m参考312.5/325/337.5K；功率80W。温度最大绝对误差≤0.1K，功率相对误差≤1%。先回读所有设置再 solve；不因错误工况修改期望。
2. 瞬态：独立数学基准 L=1m、alpha=1m²/s、两端300K、初值300+10sin(pi*x/L)。参考T=300+10sin(pi*x/L)exp(-pi²alpha*t/L²)。x=0.25/0.5/0.75m、t=0.01/0.03/0.1s，全9点最大绝对误差≤0.1K。选用明确满足alpha的数值测试材料，不能挪用铜块rho/Cp后仍比较此参考。
3. 收敛：优先用有空间曲率的瞬态或独立 manufactured case；线性稳态温度可能在粗网格就很准确，不适合强迫显示递减误差。3级实际精度与资源测量；未测内存/时间写NOT_MEASURED，不能预填。

## 7. 执行纪律和有限范围
- 先修证据/最小语义错误，再完成一个真实纵向小链，随即移植另一版本。避免只加类型/方法数量又做一份新报告。
- 所有修补配回归；不降低门槛来达成完成。确认旧测试期望本身错误时，可以更正并给出理由/新增负控，不能私自删除失败测试。
- 不给硬件升级、购买模块或长时间优化工作作为默认前置。许可/权限/环境阻塞明确记录，继续独立修补；禁止无限重试或伪造通过。
- 不依赖目标模型已经保存。保留用户变更，只有本轮拥有的模型/进程可清理。敏感端点、API key、密码、token不进公开证据；脱敏记录原文件安全hash与转换规则。
- 最小必要文件增量，优先已有模块/基础层。不要复制一套专门用于“测试能过”的实现。新测试/规则数据/运行文档可以增加，但不重排整个仓库。

## 8. 最终交付及停止条件
按每个required case/target生成真实记录；本包审计器只验证结构和证据关联，作者/审查仍需看原始行为，不赋予它科学认证权限。更新 docs/handoff_g3_8_windows_w20/PROGRESS.md、根与设计进度索引、能力矩阵和known limitations。

成功停止：所有本轮required实现完成；win63、win64各自公开真实链/负控/独立基准/报告通过；合成文件不进入原生结论；关键ACL边界确认；受影响W17–W19回归；同源码桥接和干净恢复验证；无新的未说明失败。结论只到 W20，物理证据不足仍UNVERIFIED。

受阻停止：已完成可执行代码/测试/文档，所有真实阻塞有可重复诊断与具体缺少条件；输出IMPLEMENTED_WITH_BLOCKED_ACCEPTANCE，不能标阶段完成，不自动进入W21。

交付应含正常git提交/脱敏证据/hash/复现命令/新工作包或可恢复bundle。按已有授权正常同步远端；无凭据保留patch/bundle，不force push。**达到边界后停止，W21扫描优化留下一阶段。**
