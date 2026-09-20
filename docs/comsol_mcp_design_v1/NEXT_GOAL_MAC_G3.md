# Mac G3 Goal：G2 定向补修 + W13–W16 完整建模链

版本：1.0。审查日期：2026-09-20。
审查仓库：Everwalker/comsol-mcp。
固定审查提交：`1c6a5e982742b6c1a92054409383fbfbbc56658e`。
对应 G2 实现发布提交：`e8766ae2ae25d439a07d00aba9ac79b32d36e361`。
建议存放：`docs/comsol_mcp_design_v1/NEXT_GOAL_MAC_G3.md`。

## 0. 本轮授权目标与边界

在当前 Mac 工作树继续开发：**先完成本文件 R01–R06 的定向补修/契约收口，再按依赖推进 W13 → W14/W15 → W16。**

本轮目标是让云端强模型经生产 MCP 的统一执行路径，从空模型建立参数、变量、函数、选择、几何、材料、物理、网格、Study 和 Solver，真实求解、定量核验、保存并由新 Worker 重新打开。不能只交付接口名、伪实现或绕过生产 MCP 的演示 Java。

本 Goal 取代上轮“不进入 W13”的停止边界；**只授权到 W16，不进入 W17–W26。**保留已有后续代码但不擅自回退/删除；发现本地进度领先时先核对证据，记录实际增量，不机械重做。

坚持 Mac 主开发，不连接 Windows、不重新开展 SSH、依赖安装或 Windows 实机联调。保留 Windows x64、macOS arm64/x86_64、COMSOL 6.3/6.4 的架构目标；不可访问组合保持 UNVERIFIED。本轮的独立 Mac 软件开发不以 GUI、Windows、Intel Mac、6.3 或共享 Server 取消通过为前提。

本文件是任务与验收要求，不是已完成实现，也不自动授予操作系统提权、读取私密文件或修改共享 Server 的权限。

## 1. 审查基线：保留什么，不应误认什么

### 1.1 已有证据

固定提交中的 `evidence/phase3_acceptance.json` 为 `PASS_MAC_EXECUTABLE_SCOPE`，`mac_stage_passed=true`，`full_g2_certified=false`。最终软件记录是 419 passed / 1 skipped。实机范围为 macOS arm64 / COMSOL 6.4.0.293 / 外部 JDK11；这是仓库提交的证据，不是本次审查重新运行的成绩。

W08 有类型/形状、同 tag 类型冲突、wp3 子路径与兄弟节点保留证据。W09 有生产 stdio、发布 profile 和错误/回退证据。W10 有绑定模型 Java 修改、部分失败、reconcile、无重放及恢复证据。W11 有真实本地 6.4 帮助检索证据。W12 有绑定 checkpoint 的 trial、部分应用记录、实际属性恢复和旧引用失效证据。

受影响 Phase2 的求解、指标、保存、Host 重连、同作业恢复、队列和小独立卷 ENOSPC 回归已有记录，不应因为新 Goal 又把它们全部标为 NOT_RUN。

### 1.2 必须保留的边界

- G2 不是“所有平台、所有 API、所有模型全部通过”。历史 Windows 有较早快照的限定软件/固定模型证据；那不是当前 G2 代码的 Windows 认证。
- 原生 `ModelChangedHandler` 回调 FAIL 仍存在；浅指纹不证明任意外部模型修改可检测，更不是跨客户端原子 CAS。
- broad G2 mutation 的生产隔离回执适配当前是 macOS-only。独占/隔离声明必须由控制端验证，不能相信客户端传入 `exclusive=true`。
- W12 的恢复只对已有实际验证范围成立；solution、外部文件、GUI 状态、任意 Java trial 的全模型无影响都不能推定已通过。
- G2 最终专用 Server/Worker/control 已停止，临时配置已恢复。旧 PID、端口、鉴权状态、receipt 只是历史证据，不可当作仍存活的当前运行时。
- 仓库已记录的 provenance xattr 残留、旧失败与未验证项保持可追溯，不为“干净”擅自删除元数据或改写历史证据。

### 1.3 本次审查方式

本次读取了固定提交的进度、阶段 ledger、能力边界、开发/验收计划及关键代码。另以选定源码函数的转录片段做了六个纯 Python 控制流复现，依赖使用桩；未运行完整仓库 pytest、未连接用户 COMSOL、未修改模型。

随审查包提供的 `review_evidence/g2_control_repro.py` 支持 `--repo <本地仓库>`，可从真实本地文件 AST 提取同名 helper 复核。它证明已观察的控制行为，不替代生产 MCP/COMSOL 验收。单文件 Goal 不依赖该审查包：本文件已完整列出必须新增的回归。

## 2. 开始前读取与工作树保护

先检查当前分支、HEAD/tree、Git diff、未跟踪文件、运行中的任务和实际解释器。核对 `origin/main` 与审查 SHA 的差异；不 reset、不覆盖本地用户修改，不为了匹配审查基线强制降级。

读取以下文件：

1. `AGENTS.md`、`docs/comsol_mcp_design_v1/05_DEVELOPER_TASK.md`。
2. 同目录 `04_IMPLEMENTATION_PLAN.md`、`03_ACCEPTANCE.md`、`01_ARCHITECTURE.md`、`06_CONTRACT_NOTES.md`。
3. 同目录 `PROGRESS.md`、`G2_CAPABILITIES.md`、`G2_API.md`、`NEXT_GOAL_MAC_G2.md`。
4. `evidence/phase2_acceptance.json`、`evidence/phase3_acceptance.json` 及其最终成功/失败证据。
5. 原始动作目录/机器可读目录中 W13–W16 对应条目，按任务展开相关章节，不把目录中的原始 NOT_RUN 模板状态当成当前进度。

先生成 `G3_EXECUTION_PLAN.md`：当前差异、R01–R06复核、依赖、拟公开动作及实现路径、当前实际许可证能力、测试/证据安排。计划之后实际写代码和执行，不止输出建议。

## 3. G2 定向补修 R01–R06

所有补修先写能暴露旧行为的测试，再修复，再通过生产 stdio 回归；涉及引擎数据的修复，最终须在已验证隔离的实际 Mac COMSOL 上回读。新问题不能通过删除失败断言、把错误改成成功或关闭 UNKNOWN/权限门禁解决。

### R01 — 表达式、字符串与数值视图的回读一致性

**源码依据：**`_g2_contract.py::validate_typed_value` 允许 expected string 接收 kind=expression；`_g2_engine.py::_typed_readback_matches` 却要求 requested/returned kind 完全相同；String getter 回读被标为 string。因此相同表达式文本也可能在写后被判为不匹配并进入 UNKNOWN。六个复现之一直接观察到 helper 对同文本 expression/string 返回 false。

**要求：**
- 明确语义种类（expression）与 Java 字符串存储/回读表示的映射；保留原始表达式，不把 expression 简单转成计算后的浮点数。
- 同一已验证字符串属性上表达式的精确文本回读应能验证，不因存储类型为 String 人为判失败。
- 对 Double/DoubleRowMatrix 等可存在表达式视图的属性，不从数值 getter 推断完整表达式；按实际版本 API 提供表达式/求值视图，或在写前准确拒绝不支持的写法。
- COMSOL 的合法规范化须有记录的比较规则，不能以删除空白/单位或宽泛强转掩盖不同表达式。数值比较的容差必须显式。
- `unit` 字段与表达式单位的关系要写进契约，不能仅回显单位却用不同量纲赋值。

**最低回归：**同文本 expression→String；表达式数组/矩阵；合法单位表达式；不兼容单位/类型写前拒绝；引擎回读确实不同；已写入但回读失败时仍保留 partial/UNKNOWN。对复数/非有限数沿用严格边界，不据 helper 的成功声称完整复场能力。

### R02 — 完整节点发现、分页、错误与预算

**源码依据：**`_g2_engine.py::children_node` 的固定集合未包含 material、variable、selection 等；`find_nodes` 每个父节点只取 `limit=500` 的一页，没有继续 next_cursor，也不报告搜索不完整；广义异常被吞掉。隔离复现观察到已有 mat1 未列出、真实存在的第501个子节点无法找到。

**要求：**
- 统一节点路径解析、集合发现、Worker accessor allowlist 与领域 registry；按 W13–W16 引入 func、material/propertyGroup、selection、multiphysics、coupling、坐标/pair 等实际需要的集合。名称和签名来自目标版本的真实 API，不能凭显示名猜。
- root/全局与 component 作用域要区分；Work Plane 内部几何、solver 子特征必须递归。
- 搜索继续每层分页。对结果上限、遍历预算或不可访问集合，返回明确 `complete/truncated/next_cursor/errors`，不能把部分结果当不存在。
- 只把“该节点确实不支持该集合”作为可省略项；连接断开、引擎异常、权限错误不能静默转成空列表。
- 游标绑定模型 generation/revision 和查询；陈旧或非法游标明确拒绝，不默认从第0项重来。
- 设置可配置节点数/RPC/时间预算，必要时批量化 Worker 请求；不以另一个隐藏500节点上限代替分页。

**最低回归：**材料/变量/函数/选区可见；>500子节点；多层wp3；截止时返回可恢复游标；查找中断不伪报 NOT_FOUND；失效引用/游标；兄弟节点保留。性能以实测调用量与耗时报告，不捏造加速比例。

### R03 — indexed/keyed 写入必须有真实校验

**源码依据：**`property_index_set` 读取整体属性却没有比较目标元素及非目标项；`property_entry_set` 检查 getter 名存在后调用 setEntry，但返回请求值，没有调用 getter。隔离桩中，错误位置写入或 setter no-op 未触发 helper 的不匹配拒绝。这不是“COMSOL 必定写错”，而是校验路径缺失。

**要求：**
- setIndex 以版本适配的索引语义回读目标元素/行；明确自动扩容是否合法、shape如何改变，检查不应改变的元素。
- setEntry 按真实 key/entry API 回读，不把请求参数当作实际结果；缺少权威读回路径时写前拒绝或给出受控、明确的未验证模式，不能默认 VERIFIED。
- 普通set/index/entry统一产生 applied/failed/not_executed、readback、验证范围和部分失败信息。
- 不把已有有效setter换成猜测的方法；通过运行时接口和文档核实签名。

**最低回归：**向量元素、矩阵cell/row、keyed entry；错误key/index；一次setter成功但值不符；非目标项变化；空/单元素；返回数据及外层 `isError`/UNKNOWN 一致。选择真实支持该操作的属性验收；不存在的组合如实标明。

### R04 — “动作执行完”不等于“invariants成立”

**源码依据：**`_g2_transactions.py::run_transaction` 保存 invariants，但不会执行它们；`_managed_backend.py::_verify_transaction_record` 是有限的持久记录字段检查，不是当前模型的实时验收。这是需收口的契约，不否定其已有 partial-apply/恢复通过证据。

**要求：**
- 明确 execution_status 与 verification_status，日志校验与模型实际状态校验分别标注 scope。
- 为 G3 必需的前置/后置条件提供有限、类型化、可执行检查：节点存在/类型、属性等值或数值容差、选区非空/实体维度、指定作用域保留。
- required invariant 未支持或格式不合法，必须在写前拒绝；可选未支持项是 NOT_RUN/UNKNOWN，不计通过。
- 执行成功但 required invariant 失败时，总体验收不能 PASS。已产生的修改、checkpoint与恢复选项仍须返回；不能伪装原子回滚。
- 验证绑定 transaction 对应的 model_ref/revision，不能在另一个模型上读取旧日志却称该模型验证通过。
- 不借此提前实现整个 W20 物理验证系统；本轮只补建模链必需的有限断言。

**最低回归：**故意违反的required条件、未知条件、旧revision、跨模型记录、动作全成功但检查失败、记录检查通过但实时属性已不同、失败后checkpoint恢复实际属性。

### R05 — 恢复记录损坏不能变成“没有记录”

**源码依据：**`_g2_transactions.py::TransactionStore.__init__` 对 OSError/JSONDecodeError 回退为空字典；独立临时文件复现观察到损坏JSON被加载为空记录且不报错。这里审查的是G2事务JSON sidecar，不是宣称 SQLite jobs 同样损坏或已丢失。

**要求：**
- 文件不存在（首次初始化）与已存在文件不可读/损坏必须区分；后者保留原文件、返回明确持久化故障并阻止依赖该记录的写入/恢复。
- 不能下一次 put 自动覆盖损坏历史。制定可审计恢复流程，并保留失败副本/hash。
- 事务与 checkpoint 的权威记录和 SQLite metadata 明确统一；可以在现有 SQLite 中迁移，也可以加固 sidecar，但不能同时有两套互相矛盾的真相。
- 写入使用现有已验证的原子/持久化方式；并发、进程退出、迁移和恢复失败测试必须明确覆盖范围。
- `checkpoint.diff` 当前显式不支持不算已实现；G3采用有限作用域对比时如实说明，不能把hash不同当语义diff。

**最低回归：**不存在文件正常初始化；损坏/不可读保全；故障恢复不重放引擎动作；跨重启事务/检查点仍可查；迁移前后引用匹配。可以用有界I/O故障注入，不为了本条在普通磁盘填盘，也不重新启动已由用户跳过的Windows T030。

### R06 — 与 G3 直接相关的发现/文档/运行范围收口

1. 本轮新领域动作必须使用同一 model/session/revision、permission、queue、event context、job和幂等路径。不能另建一个“方便版”服务绕过 G2 的隔离回执、UNKNOWN或checkpoint约束。
2. 旧的浅指纹/回调FAIL保持原 verdict。允许在本轮任务自有、验证隔离的 Server 上推进；不要为满足全部外部变更检测而永久卡住，也不要转而在共享用户模型上无保护写入。
3. 将新增平台差异留在 adapter 层；保留已经修好的Windows进程身份、classpath、句柄flush和stdio生命周期代码，不重新散落POSIX专用命令。
4. 复核 `_invoke_g2_control` 中 docs.examples 把 node_type 传给 product 过滤的行为是否符合正式schema/索引字段。以真实索引fixture写回归；这是待闭环的查询契约问题，不得把“有文档但错筛掉”解释为该API不存在。搜索按COMSOL版本、产品、节点类型各自语义处理。
5. README/CLAUDE/AGENTS 与新registry保持一致；历史阶段禁入W13说明保留为历史，当前入口指向本Goal；自动生成的工具总数不是能力验收。
6. 大模型checkpoint成本与完整GUI/solution恢复仍是后续范围；本轮记录必要小模型保存/复制成本，不虚构全模型无副作用或生产大模型性能。

## 4. G3 共同实现契约

建立 W13–W16 动作覆盖表，逐项对应原始动作目录：公开operation、领域/通用API/受控recipe路径、输入输出schema、API来源、版本/产品前提、读回方式、真实证据、未验证原因。

允许把低频复杂动作实现为受控 Java recipe，但必须可通过公开、类型化、生产MCP路径调用，具有上述身份/权限/日志和验收；不能仅在测试准备脚本中手写Java就把领域动作标为完成。也不要求为了工具数量给每个几何形状造一个独立工具。

维持 full/domain/expert 发布和 operation fallback 的功能一致性；同一动作从任何入口都不能降低权限或改变副作用分类。普通领域建模不应仅因内部使用Java实现就要求用户开放任意trusted_code；允许任意用户源码的路径则必须继续单独授权。

所有单位、维度、模型实体、解与数据集选择都显式。无法判断时准确报unknown/unsupported，不退化成默认2D、geom1、固定domain ID或固定第一/最后一个解。

新功能的基本循环：校验输入和当前身份 → 必要的checkpoint/计划 → 统一执行 → 实际回读 → 返回证据与状态。只读描述不能暗中修改模型；需要临时numerical节点时仍走已有串行ephemeral mutation语义。

## 5. W13：参数、变量、函数和选区

依赖 W08/W11；在R01/R02/R03相关底层修复可用后推进。

### 必须可用的范围

- 全局/组件参数与变量组的增删改查、描述、批量更新、单位/表达式读回；一个变量组内多个真实变量，不创建假name/expr变量。
- 解析、分段、插值等原动作目录要求的函数；至少有实际一维和二维插值。管理参数/函数单位、坐标单位、插值/外推、外部数据路径/hash及重载。
- Named Selection 的创建/查询/修改/删除和引用，显式/空间/邻接/布尔组合按实际API实现；物理接口、子节点、材料、网格对选择的引用能回读。
- 实体维度和几何作用域显式；选区为空、对象被删除、几何重建导致范围变化时能发现，不依赖名称本身判断正确。
- 几何实体位置/面积/体积/数量的有限校验，满足W14/W15的定位需要；不把裸ID当永久身份。

### 验收

执行T006、T015、T048的适用子项；提前完成T016的二维数据完整性子项，不因此宣称W22/G5完成。

提供小的非轴对称 `Q(x,y)` 数据fixture，在同半径不同角度采样应保持差异；保存m/mm转换、插值/外推和hash证据。分别验证W/m²表面源和W/m³体源，故意错单位得到确定错误/覆盖内预检告警；不自动乘厚度或吸收率。

删除/重建几何后，对选区引用重新核验；不能仅以“selection tag仍存在”计PASS。

## 6. W14：几何、Work Plane、CAD、坐标与Pair

依赖W13。首先完成通用geometry序列与嵌套路径，不推倒G2已经验证的wp3能力。

- 基本体、布尔、变换、阵列、拉伸/旋转、工作平面内部编辑、局部build和几何测量，按原动作目录映射。
- copy/move/rotate/delete/reorder等操作应有明确目标、参数单位和预期局部结果；保留不在目标范围的节点。
- 1D/2D/轴对称/3D及几何序列的维度正确；同tag异type在写前拒绝，不用ensure隐式改维度。
- 坐标系、Identity/Contact Pair等原范围在实际版本/许可允许时接入；Pair源/目标、实体维度与方向可读回。
- CAD导入、重载、修复及Form Union/Assembly按实际许可证探测；不可用的商业功能保留明确适配边界/可重放测试，不为通过而冒充成功。

### 验收

执行T009完整适用几何子项及T034本机路径/导入子项。用可复现wp3矩形阵列fixture，保留指定最左对象，修改/旋转其余目标，量化检查数量、位置、间距与兄弟节点不变。fixture必须明确“中心距”或“边缘间隙”的定义与矩形尺寸；它不是用户原始mph的替代实测，不猜未给出的原模型参数。

CAD相关许可证缺失不阻塞不依赖它的基本几何开发，但不能将未执行的CAD条目计为通过或从范围表删除。

## 7. W15：材料、物理场和多物理耦合

依赖W13，可与W14软件实现并行；同Server实机操作统一排队。

- 全局/组件材料、属性组、赋值/选择、材料复制/引用及原目录要求的管理操作。
- 密度、比热、导热率的单位/表达式；k(T)、Cp(T)、各向异性矩阵/张量及所用坐标系。
- Physics interface及子/孙feature生命周期、作用域、选择、边界/初始条件、源项，使用真实内部type而非猜标签。
- Multiphysics/coupling及相关Pair引用能建立、读取、更新；只对实际测试组合声称可用。
- 对支持的模型提供有限覆盖的材料缺失、关键属性缺失、空选区、明显维度不匹配预检；规则没覆盖时输出unknown，不能充当完整物理正确性判断。
- 更新某个材料、边界或耦合不得悄悄重建其他用户配置。Physics默认不可编辑/继承状态按已修复的三态观察与错误传播处理。

### 验收

执行T007、T017、T042的适用子项。验证温度函数和至少一种各向异性张量；故意删除必要材料属性产生覆盖内预检错误。许可探测和实际接口创建/求解证据分开。至少一个有许可的多物理/coupling小fixture进入真实测试；没有许可则记录阻塞与负向行为，不以静态方法清单替代。

## 8. W16：网格、Study、Solver与空模型纵向链

依赖W14/W15。目标是实际建模与求解，不是“能运行一个预制Study”。

- Mesh序列与特征的增删改查、选择、顺序、局部Size、构建/复建；按实际支持接入FreeTet、映射/扫掠/边界层等原目录操作。
- 网格统计说明质量定义、单元数、覆盖与低质量位置；build成功不等于网格合格。
- 从空模型创建Stationary和Time Dependent Study/step，配置启用物理、时间范围、初值和所需解引用。
- 读取并修改多层SolverFeature；容差、时间步、变量缩放/耦合策略等按真实API配置。自动solver生成与用户手工solver保留要有显式策略。
- 新domain调用仍经持久Worker、统一队列、job与幂等；长求解时health/status/log保持响应，等待超时不表示引擎已停止。
- 保存目标版本mph；新Worker重新打开后核对模型结构、表达式与代表性数值，不能只检查文件存在或ZIP可读。

### T018/T019/T020的最低实机纵向验收

**链A：小3D稳态导热。**由公开MCP动作从空模型创建块体、常数材料、两端给温、其他面绝热、局部网格、Stationary Study和Solver。参考解由测试方从稳态一维导热方程推导并写入fixture：`T(x)=T0+(T1-T0)*x/L`，热流方向/功率由k、梯度与截面积单独计算。记录坐标、单位、取样误差、边界热收支；不得用目标结果反推输入。

**链B：小瞬态基准。**同样从公开路径创建新的瞬态Study与初值。例如固定两端为T0、横向绝热、初始`T0+ΔT*sin(pi*x/L)`的恒定材料块体；比较`T0+ΔT*sin(pi*x/L)*exp[-k/(rho*Cp)*(pi/L)^2*t]`。测试定义明确材料、坐标原点、边界、时间点、误差度量及网格/时间步。基准公式只对该合成条件成立，不作为用户真实晶圆物理模型。

默认建议验收目标：稳态线性场相对温差误差≤1e-4；瞬态归一化到初始ΔT的最大采样误差≤1e-3。它们是本轮拟议测试门槛，不是现有成绩。可为合理有限元离散做有依据的预先调整；不得看到失败后直接放宽门槛而不保留旧结果、收敛证据和解释。

完整模型创建/修改/Study/求解须经生产MCP；独立验证程序可计算参考值或检查已有结果，不能提前偷偷搭好待测模型。重放recipe/hash、实际请求响应、solver日志、原始数值及mph都入证据。

**链C：已有用户风格模型局部续建。**合成模型中预放与本次无关的材料、物理、手工solver和用户Derived Values；用新域工具改指定目标、构建/求解，验证非目标节点和数据关联保留。此测试补充空模型链，避免“只能新建、不能接着改”。

使用现有求值/导出能力提供这些测试所需的最小结果。若需要一个最小采样或结果绑定适配器，记录为W16验收基础设施；不顺势展开整个W17结果系统或W18绘图平台。

## 9. 运行时与权限：避免再次陷入基础设施空转

1. 启动时重新发现COMSOL/JDK/本轮任务自有进程；不复用历史PID、旧receipt或未知用户模型。当前G2专用Server已停止。
2. 优先复用仓库中已经验证的Mac运行/隔离方案与脚本，确认当前文件hash、安装build、有效配置路径和本轮权限。不要再从旧的错误tomcat模板试起。
3. 需要安装级XML、网络、OS权限变化时按现有授权/系统审批执行；不把本文当越权授权。授权不足只提出一次具体、最小的操作请求，继续本轮独立软件工作。
4. 安全门禁只能通过真实证据或经过审查的等价方案满足，不能删除门禁来让测试通过。隔离证明与公共模型动作验收分别记录。
5. 不为了G3解决全部GUI权限、取消路线或全模型事件检测；这些不是本轮纯API功能的通用前置。已知局限继续暴露在capability。
6. 不在用户共享Server或未保存模型上做破坏性测试。不通过杀客户端冒充求解取消。仅在任务所有权、无活动用户对象和权限明确时清理自有进程。
7. 故障后查看原作业、reconcile和恢复，不盲目重发同一写入/求解。相同阻塞若再次出现且没有新证据/可执行动作，记录BLOCKED并停止该支线，禁止连续空转。
8. 本轮结束清理本轮自有对象/进程，恢复本轮临时改动并独立核对。保持历史证据与用户现有进程不动。

## 10. 验收、回归、证据与状态

### 10.1 本轮必需证据

- R01–R06：修复前复现/契约审查、修复后单元回归、适用生产MCP和实机回读。
- W13：T006/T015/T048与T016数据完整性子项。
- W14：T009完整适用子项、T034本机子项。
- W15：T007/T017/T042适用子项。
- W16：T018/T019/T020和上述A/B/C链。
- 新入口受保护性：T010幂等、T035路径、T038错误传播；所涉修改后的必要T005/T033保留性回归。
- 对queue/job/checkpoint有改动时回归相应T012/T026/T027/T028/T057/T029/T050；不要求无差别重跑所有旧实机案例，但跳过受影响案例必须解释。

### 10.2 文件组织

维护/生成：

- `docs/comsol_mcp_design_v1/G3_EXECUTION_PLAN.md`
- `docs/comsol_mcp_design_v1/G3_REVIEW_FIXES.md`
- `docs/comsol_mcp_design_v1/G3_CAPABILITIES.md`
- `docs/comsol_mcp_design_v1/G3_OPERATIONS.md`
- `docs/comsol_mcp_design_v1/PROGRESS.md`，只追加/明确更新当前段，不抹掉历史。
- `evidence/phase4_acceptance.json`（若已被占用则选不冲突路径并建立指针），含source SHA/tree/dirty manifest、OS/build/JDK/Python、原test ID+subcase、证据级别、状态、原始路径、hash、未通过原因。
- `evidence/phase4/runs/<run_id>/`：environment/request/result/assertions/engine.log；适用前后模型、数据、截图及hash。私密原始内容在本地忽略目录，公开只放授权的脱敏信息与追溯hash。
- 自动生成schema/工具文档及动作覆盖矩阵；runtime capabilities只引用实际证据，不从方法存在升级为VERIFIED。

继承G2 immutable证据保护。最终检查原Phase1/2/3受保护文件hash；新回归另建目录。报告明确区分：代码实现、单元/协议通过、实机通过、数值通过、物理验证。419是旧快照计数；新计数取实际输出，不硬编码。

### 10.3 开发节奏

R补修、W13、W14/W15、W16各有可审查检查点。可用子任务并行开发互不依赖的软件模块；合并前统一接口，并以同一source snapshot串行做Server验收。每个检查点记录已验证能力和下一依赖，避免只说“完成了若干工具”。

## 11. 停止与交付条件

### 成功结束

以下条件同时成立才报告 `G3_MAC_EXECUTABLE_SCOPE_PASS`：

1. R01–R05的确定控制缺口修复，并有对应回归；R04实时/记录语义清楚；R06各项有闭环结论和适用证据。仍未覆盖的全模型/GUI等边界明确保留。
2. 原目录W13–W16的当前可执行必需范围有可调用实现、schema/权限、负向测试及实际证据，不是只注册工具或只跑一条演示。
3. T019稳态/瞬态从空模型链及新Worker重新打开通过；W14局部编辑、W15材料/物理、W16网格/深层solver的必要实际验收通过。
4. 受影响历史能力回归通过；没有新伪成功、用户模型破坏、绕过权限/队列或重复执行问题。
5. 进度、capabilities、运行说明、证据/hash和cleanup已完成。Windows/Intel Mac/6.3/GUI等缺少实机者继续UNVERIFIED，不称全平台G3完成。
6. 按最新AGENTS和用户已有阶段同步授权做普通非force同步；核对远端SHA/tree。保护现有本地和远端改动，不自动覆盖历史。不具备认证时保留提交并报告delivery blocked，不能假称同步成功。
7. **停止，不进入W17。**

### 受阻结束

若当前Mac核心功能的必需实机验收无法完成：继续所有不依赖阻塞的本轮实现/测试；写明 `IMPLEMENTED_WITH_BLOCKED_ACCEPTANCE` 或 `BLOCKED`，不称阶段通过，不靠把required变optional达成Goal。记录最小人工动作与可重放入口，不反复同一失败。许可证缺失只阻塞确实依赖该产品的条目，并保持完整能力目标；不能以可选商业模块阻塞所有基本建模。

遇到可用环境中的真实产品缺陷必须修复或保留FAIL；它不同于设备不存在的UNVERIFIED。无授权系统操作、未知用户模型、历史callback FAIL不允许被“为了继续”掩盖。

## 12. 最终给用户的汇报格式

汇报当前源码/交付SHA；R01–R06结论；W13–W16状态；三个真实建模链的文件与定量结果；软件/协议/实机分开计数；已清理/残留；未通过/未验证；下一阶段W17–W20的依赖是否满足。不要用“已能完成所有COMSOL动作”概括本轮。

## 审查来源（均固定为本文件首部SHA）

- `docs/comsol_mcp_design_v1/PROGRESS.md`：G2最终验收与publication段。
- `evidence/phase3_acceptance.json`；`docs/comsol_mcp_design_v1/G2_CAPABILITIES.md`。
- `comsol_mcp/_g2_contract.py`：TypedValue、ACCESSOR_METHODS、validate_typed_value。
- `comsol_mcp/_g2_engine.py`：children_node/find_nodes/property_set/index_set/entry_set/checkpoint。
- `comsol_mcp/_g2_transactions.py`：run_transaction、TransactionStore。
- `comsol_mcp/_managed_backend.py`：G2 dispatch、docs examples、transaction verify与trial。
- `docs/comsol_mcp_design_v1/04_IMPLEMENTATION_PLAN.md`、`03_ACCEPTANCE.md`、`AGENTS.md`。

固定源码入口：`https://github.com/Everwalker/comsol-mcp/tree/1c6a5e982742b6c1a92054409383fbfbbc56658e`。
外部接口参考：`https://doc.comsol.com/6.4/doc/com.comsol.help.comsol/api/com/comsol/model/PropFeature.html`；实际可用方法仍以目标安装build探测和验收为准。
