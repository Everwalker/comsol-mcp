# Mac G3.1 Goal：修复集成与验收阻碍，完成 G3 实机收口

版本：1.0。审查基线：`Everwalker/comsol-mcp@999174f3a62a6d5689ad4b86315adb6c89dbe56b`，Git tree `354d9bf950e96c6b2268f453bd13dd193aa705e4`。

建议放置：`docs/comsol_mcp_design_v1/NEXT_GOAL_MAC_G3_1.md`。

## 0. 本轮目标及授权边界

继续当前实现，不重新导入上游、不重做已证实的 G2，也不把 G3 已注册的 96 个 operation 当成 96 项已通过实机验收的能力。

本轮是 **G3.1 稳定化和验收收口**：修复本文件列出的错误传播、请求状态、实际 API 适配和验收程序问题，完成原 G3 的 R01–R06、W13–W16 在当前可访问 Mac / COMSOL 环境内的 required 验收。**不进入 W17/G4，不做 Windows 联调。**允许增加这些验收必需的最小采样、存储解时间读取和公共 API 白名单项；不借此扩展完整后处理、GUI、优化或多阶段平台。

真正交付标准：同一生产路径能从空模型建模、求解、定量检查、保存，并由新 Worker 重新打开验证；能继续修改一个带用户节点的已有模型并保持非目标内容；任何失败或未知状态都不冒充成功。

本 Goal 允许更新仓库内 `AGENTS.md` / `CLAUDE.md` 的过期项目地图、阶段边界与测试说明，保留现有安全规则和 Git 同步规则；不涉及用户全局指令文件、不授权新系统权限。不要因这两个项目文档仍写“待批准”而阻止本轮代码与测试。涉及新的系统/安装配置、权限、许可证或用户模型操作时仍须遵守已有批准范围，未授权部分只列出精确的最小操作，不绕过。

## 1. 开始前核对

读取以下真实文件及其相关章节，而不是只读本 Goal：

1. `AGENTS.md`、`docs/comsol_mcp_design_v1/PROGRESS.md`、`G3_EXECUTION_PLAN.md`、`G3_REVIEW_FIXES.md`、`G3_CAPABILITIES.md`、`G3_OPERATIONS.md`。
2. 同目录 `05_DEVELOPER_TASK.md`、`04_IMPLEMENTATION_PLAN.md`、`03_ACCEPTANCE.md`、`06_CONTRACT_NOTES.md`、上一轮 `NEXT_GOAL_MAC_G3.md`。
3. `evidence/phase4_acceptance.json` 与 `evidence/phase4/runs/20260920T130620Z-g3-live/driver4/` 的 `summary.md`、各相关 case 的原始 requests/results/assertions。
4. 本文件所定位的源码、实际测试、Worker 日志与产物路径。旧 `phase2`、`phase3` 证据保留为历史，不从一段旧“NOT_RUN”推断最新状态。

先记录本地 HEAD、分支、工作区差异、远端 SHA、解释器/依赖/COMSOL/JDK 与源码哈希。若本地已有后续修复，逐项核验并继承，不回退到审查基线，不覆盖用户修改。

仓库提交信息写 `1373 passed / 1 skipped`，现有 PROGRESS/phase4 ledger 写 `1359 passed / 1 skipped`，且运行记录的 HEAD 仍为 G2 基线、源码另有 dirty manifest。应建立“测试运行 → 实际源码清单 → 发布提交”的映射，检查缺失的最终输出；不能简单把历史 HEAD 改成新 SHA 以假装运行过新源码，也不能把发布成功当阶段验收通过。收口时重新运行当前源码的软件回归并记录实际数量。

建立 `G3_1_EXECUTION_PLAN.md` 和 `G3_1_ROOT_CAUSES.md`；每条问题记录证据、产品/驱动/夹具/环境分类、影响范围、修复点与负向测试。以下审查结论需对当前本地源码复核；已修项直接回归，不重复实现。

## 2. C01 / P0：统一 G3 领域返回值与持久作业错误语义

### 已发现的机制

`_managed_backend.py::_invoke_g3_model.callback` 将任何正常返回的 Mapping 包装为 `success=True`。但 `_g3_results.py::sample_path` 会以普通 data Mapping 返回 `status.ok=False`、`status.execution_state_unknown=True` 或 `cleanup.cleanup_failed=True`。

`_execution_service.py::execute_legacy` 又先判断 `claimed_success`，再判断 UNKNOWN/cleanup 信号。因此即使显式携带危险信号，也可能选中 succeeded 分支。

### 必须修改

- 定义一个明确、可校验的内部 DomainOutcome 适配契约，接通全部 G3 已发布操作；不要只特判 sample_path，也不要在任意用户数据里递归搜索名为 success/status 的键。
- 将现有 domain status、engine_error、applied/failed/not_executed、cleanup 与“是否已发生引擎变更派发”转换为统一 outcome。未知/cleanup failure 优先于表面的 success。
- 执行失败应体现于 ActionResult.success=false、MCP isError=true、原 job 的对应终态；执行成功但数值验证失败要用独立 verification_status 表示，不把二者混为一层。
- UNKNOWN 必须保留原 job 和实际 Worker request 证据，冻结后续依赖写入；不能只在 data 中放一个 unknown 字符串却记录 SUCCEEDED、清除 dirty。
- 不再仅凭异常类名 `ExecutionContractError` 断言“写前拒绝”。用显式 validation/dispatch stage 或现有 Worker 事件证明没有变更调用；引擎读取已发生不等于变更派发，变更派发失败也不能假定无副作用。
- 已有 Java 受控执行、legacy 工具、G2 property、G3 domain、fallback 四种入口共用最终判断规则；不建第二套互不一致的成功判据。

### 回归

至少覆盖：domain 普通失败字典、嵌套 UNKNOWN、临时节点删除失败、success 与危险信号矛盾、写前参数拒绝、写后读回失败、setter 生效后第二步失败、真实计算失败、普通成功、验证未执行。断言外层 MCP、内层结果、job、revision/dirty 一致，并确认 UNKNOWN 原请求不被自动重发。故障注入仅在测试对象上执行。

## 3. C02 / P0：修复驱动的模型身份、修订号及幂等链

### 已有原始证据

`driver4/cases/GUARD_T033` 的多次 model_inspect 使用固定 key `phase4-driver4phase4-reconcile-model-comsol-mcp-phase4`；原始返回为 IDEMPOTENCY_CONFLICT。后续求值仍携带旧 revision，被 REVISION_CONFLICT 拦截。

驱动 `_expected_revision` 与 backend 的重复 identity 字段优先级不一致；当前程序大量重复传递顶层、arguments 和 execution 中的相同字段。修复必须避免“换 key 就重做”或每步无条件 refresh 掩盖实际外部修改。

### 统一请求状态

- 实现一个集中使用的执行上下文，按完整 ModelRef（session/server epoch/tag/generation）管理已观测 revision/dirty，不使用项目名或一个全局 revision 替代多模型状态。
- 用固定 schema 解包实际 MCP structuredContent / ActionResult / job result，不用模糊递归取首个数字或 success。
- 每次返回后，只采纳匹配该 model_ref 的权威 execution 信息；恢复/重新加载返回新 generation 时显式替换旧引用并失效旧缓存。
- 重复 identity 字段若不一致，在派发前明确拒绝；内部只保留一个规范化来源。禁止请求中一处 revision=2、另一处 revision=3，再各层自行选择。
- 新的逻辑请求分配 run/case/model/step/sequence 唯一 key；一次新的读取即使参数相同，也应与旧读取区分，避免重放旧结果。**仅同一个请求因响应不确定而重试时复用原 key 与完全相同 body。**错误后改变 revision/body 是新的计划，不是原请求重试。
- `model_inspect(refresh=False)` 与承认外部/未知变更的 reconcile 意图分开。refresh=True 当前会 reconcile，不能将其作为所有失败后的自动清错按钮。
- 未完成 job 先通过原 job 的查询/reconcile 验证终止/静止与副作用，再决定恢复检查点、接受已观察变更或停止。未知调用不能靠新 key 再执行。
- 只有已证明 NOT_EXECUTED、模型已合法重新核对且仍符合原意的请求，才允许一次有记录的新计划执行。故意 stale/冲突负向测试禁止自动修复。

### 产品侧诊断而非门禁降级

不要仅为让 stale-revision 测试得到指定文案而交换 begin_write 的 dirty/revision 校验顺序。返回区分 stale_expected、external_observation、unknown_job、generation_mismatch 等拒绝原因，并报告规范化的请求/当前 revision 与可证明的派发阶段。

对真正的“纯读后突然 dirty”，记录同一模型的调用前、变更提交后、下一请求前 fingerprint / event counter / timeModified 与 Worker 事件。只有证据证明自身观测造成变化，才能调整对应观测提交机制；**禁止全局删除 timeModified、忽略外部计数或直接清 dirty**。

### 验收

连续创建→读回→修改→读回→求值→保存不出现由驱动造成的旧 revision 冲突；相同 key/body 不重做，不同 body 同 key 拒绝；两模型切换不串 revision；外部真实变更及故意 stale 请求依然拒绝；丢响应后找回原 job 不重算。

## 4. C03 / P0：测试隔离、第一失败原因和验收状态

- 不再让 R01/R03/R04 的负向试验污染后续所有用例共享的唯一模型。独立 case 使用独立 MCP-owned 模型，或经过实证的 checkpoint restore 与新 ModelRef；同一个 Server 仍严格串行。
- 在 case 开始建立明确 prerequisites：模型、component/geometry、材料、mesh/study、当前 revision。`mesh.create` 缺 geom1 是夹具/前序失败，不是网格算法验证失败。
- 主错误必须保留 original operation/code/cause/dispatch_stage。后续因前序失败未执行的项目列 DEPENDENCY_BLOCKED/NOT_RUN 并指向根因，不形成几十条伪独立缺陷。
- 驱动 ASSERTION/fixture/协议缺陷单列 HARNESS_FAILURE；已承诺 required 能力未实现单列 IMPLEMENTATION_GAP，不能全都塞入环境 BLOCKED。仅真实不可获得平台、产品、系统批准等列 EXTERNAL_BLOCKER。
- 不把 REVISION_CONFLICT、API签名不匹配或路径错误写成“CAD许可证不足”。
- 新增快照回放测试直接消费旧失败请求/返回（保留脱敏与原始哈希），验证 schema 解包、身份规范化、key 生成和分类；测试通过只证明驱动修复，不升级旧实机结果。
- 先完成第 10 节的最小关卡，再跑全部 G3。禁止连续全量 driver5/driver6 只堆日志；重复同一根因而没有代码/环境变化时停止该分支，继续独立可做项。

## 5. C04 / P1：T033 求值策略与临时节点证据

原始 `GUARD_T033/results.json` 的 operation_describe 已包含 `input_schema.properties.evaluation_policy`。`_tools_params.py::evaluate_expressions` 也确实实现了 pure_read 拒绝和 ephemeral_mutation。该次 3*3 请求在 revision 门禁处被挡住，不能证明计算结果为空。

- 修正驱动对 operation_describe 的 schema 读取路径；生成说明应如实描述既有 policy，而非要求同一信息必须出现在测试臆造的字段中。
- 在新建、干净、有效绑定的测试模型上验证 pure_read 的明确拒绝，且无临时节点/写调用；不能用任意错误替代所要求的策略拒绝。
- 验证 ephemeral_mutation 的常量求值、模型表达式、已求解场量及非法表达式；分别记录真实值、所属数据集/解及结果形状。常量请求要明确其求值路由所需条件，不靠 Python 直接算9冒充 COMSOL。
- 通过 UUID/owner operation 与 before/after 清单或事件证明临时节点只属本次请求、已清除，用户 numerical/table 及关联不变；清除失败必须经 C01 到达 UNKNOWN。
- 如成功响应确实缺少 owner/cleanup 诊断，补充共用数据契约，不复制另一个求值实现。
- 给历史“没有 evaluation_policy / 3*3 空值”添加有来源的诊断更正记录，保留原始失败输出不覆盖。

## 6. C05 / P1：runtime / license 作用域与 Java 数组调用

### 具体问题

`_g3_runtime._product_rows` 以 `[product]` 作为调用参数列表；`_license_api_call` 原样送出 `args:[product]`。公开 API 是 `ModelUtil.hasProduct(String...)`，反射参数类型为 String[]。当前 Worker invoke 按固定 arity 匹配，convert 只把 List 转成数组，没有自动把单 String 打包成 String[]。

此外注册表宣称 runtime 控制作用域、只需要 runtime_id，但统一 G3 分派在到达 runtime 操作前要求 model_ref，原始 T042 已返回 MODEL_IDENTITY_MISMATCH。

### 修复与验证

- 对 hasProduct 发送一个数组参数，例如 payload 的 `args:[[product]]`，或现有 TypedValue 的显式 String[] 表示；在真实 Worker 命令路径验证。优先修准确的调用点，而不是无测试地放宽所有反射重载。
- 增加无 COMSOL 的 Java 反射/封送测试，再通过真实 Worker 验证一个已知产品。只测 Python fake facade 不够。
- runtime.capabilities / license_inspect 绑定实际 runtime_id/connection epoch，不强依赖某个模型或 expected_revision；runtime_id 不得仅回显任意调用方字符串。可选 used-products 模型盘点与 runtime 查询分开，仍走统一 Server API 队列，不开旁路客户端。
- hasProduct 返回 false、API不支持、参数转换失败、未连接、许可证席位实际使用失败分别报告；不要把方法不可达/签名错误全翻译成 BLOCKED_LICENSE。
- 只读探针不 checkout 席位；hasProduct=true 也不声称浮动席位一定空闲。若无真实缺失产品可测，缺失产品负向子例如实保留未验证，不随机编造产品token充当许可证缺失证据。
- 错误/部分查询保留每个产品的来源、未解析状态和原始原因，不伪造 boolean，不因一个产品失败丢失其他已观测证据。

## 7. C06 / P1：材料语义、默认特征和深层节点适配

### 材料

修复 driver4 的 `thermalconductivity` rank1/rank2 不一致：材料语义张量与 COMSOL 属性实际存储形状分别建模，按目标 build 的文档/元数据/实际读回建立 property-specific adapter。

- 保留上层明确的 3×3 张量语义；只有已验证的存储协议才允许转换成 COMSOL 所需字符串向量或矩阵。
- 禁止通用 setter 为通过一个测试而无条件 flatten/reshape，禁止静默丢掉非对角项。
- 用不同对角项和非零非对角项的合法材料测试验证索引排列；各向异性正定/对称约束按所用物理模型声明。稳态/瞬态基准可使用原规范的各向同性 k，但不能拿它代替张量验收。
- 在新材料 def propertyGroup 中设置 density、heatcapacity、thermalconductivity 时，使用已核对的官方键、表达式与单位，并实际回读。未知元数据路径需要补相应适配证据，不通过删除检查硬写。

### 物理、网格、求解器

- 先盘点默认 physics features 的 tag/type/selection/继承关系，再处理 `ins1`、初值等。兼容的默认节点可以显式复用并验证其物理作用；不能猜测 ins1 必然存在或可写，也不能在失败后 SKIP 关键边界条件继续宣称链完成。
- Solver 使用当前模型真实返回的嵌套 NodePath、feature type 和合法属性；不要假定 sol1/st1 恰好是要修改的求解器节点。修复 `solver.inspect` 输入schema/NodePath冲突，保持手动 solver 非目标子树不变。
- 网格前先建立并构建目标 geometry；局部 Size 的 target selection、实际元素数、质量定义、覆盖检查要读回，不能只记录 build()无异常。
- Worker白名单由实现负责人集中合并最小已验证公共方法与签名；子模块写“allowlist_entry_required”是待完成事项，不是功能完成。补测试和版本边界，保留禁止危险全局动作的限制。

## 8. C07 / P1：解析基准、采样和现有模型夹具纠偏

### 8.1 固定参数唯一来源

当前 driver 的 CHAIN_A/CHAIN_B 参考均为 Cp=1000 J/(kg·K)，但材料预设分别为100和10。先修配置源，而不是改变容差。

建立不可变 BenchmarkSpec，预登记几何、k、rho、Cp、初值/边界、时间点、单位和误差定义。构建材料使用该规格；解析公式独立实现并经维度/数值 sanity-check。求解前实际回读模型值，逐项核对规格。**不要使用错误的模型回读去改解析预期，从而把构建错误隐藏掉。**

- 链 A：保留原尺度、k=10、rho=1000、Cp=1000，端面300/400 K，侧面绝热；T(x)=300+100x/L，q=k·100/L，总功率按实际端面面积计算。维持原相对误差阈值1e-4及其定义。
- 链 B：保留 Cp=1000 与原 rho/k，T0=300 K，初始正弦扰动20 K；alpha=k/(rho·Cp)，解析解 T=T0+20 sin(pi*x/L) exp[-alpha(pi/L)^2*t]，边界/侧面条件须匹配；沿用原时间点和归一化最大误差1e-3。初值失败不能跳过。
- 原工作包若在本地有更新的明确参数，以有版本的已批准规格为准，记录差异；不得因为当前程序数值不合格就反向改验收参数。

### 8.2 最小采样适配

`T`（温度）与 `t`（时间）是不同字段。当前采样模块依赖 JSON 字段插入顺序配合驱动大小写不敏感匹配，必须修复。

- 按精确 expression key 或明确 columns/values 元数据读取温度；时间有独立键/维度，不依赖顺序。增加 JSON键乱序、同时存在T/t/time的测试。
- 采样绑定实际 dataset、solution、component/geometry和存储时间/solnum；不能把 Study 中请求的 tlist 直接当作已存储时间。确实需要读取实际解时间的最小公共方法时，纳入本轮 W16 验收基础设施验证后使用。
- 坐标单位、材料单位、温度单位显式读回与核对；m/mm不只更换标签。保留 expr×solution×point 维度。
- 不把请求坐标或解析数组当作 COMSOL 已返回的结果；不存在的结果/单位/坐标元数据标明未知。

### 8.3 三条闭环和依赖夹具

- A、B 从真正空模型开始，全部关键操作经公开 MCP/domain 路径，不能用预求解模型代替空建链。
- C 的用户风格模型必须实际含需保留的 numerical/table/association、手动solver和兄弟geometry。现有v11夹具四个SKIPPED、exit1不能直接作为完整前置证据；补齐并验证，或另建完整独立夹具，保留原始失败。
- T034 用真实引用外部依赖的 .mph（例如允许的外部插值数据）完成路径/缺失依赖测试，不用纯文本探针替代。没有CAD产品时仅对应CAD子项外部阻塞，不要阻塞基本路径测试。
- A/B/C 完成后必须保存、退出本轮原 Worker，再用新的私有控制/Worker身份加载同一产物并验证实际材料/边界/手动节点/场值。新host但旧Worker不等于独立重开；只检查generation变化或zip有效也不等于数值恢复。

## 9. C08：执行阶段证据、文档与包内资源

- 保留 G2 和本轮四次旧driver的所有原始证据；修复诊断用新增 supersedes/related_case 链接，禁止改写旧FAIL为PASS。
- 统一由 finalized case manifest 生成 summary/index/phase ledger，生成时标明已结束；禁止读取一半文件后手填另一个计数。使用临时文件+原子发布、schema与总数校验。
- 生成/更新 `G3_1_ROOT_CAUSES.md`、`G3_CAPABILITIES.md`、`G3_OPERATIONS.md`、`PROGRESS.md`、适用的 API/schema 文档与简短 AGENTS/CLAUDE 导航。
- 本轮记录使用 `evidence/phase4_1_acceptance.json` 和 `evidence/phase4_1/runs/<run_id>/`（若已有同等目录，记录映射）。包含实际源码manifest、base_commit+dirty差异、环境、requests、results、assertions、日志、原job/Worker请求ID、真实产物哈希。
- 区分 software、protocol、engine、numerical、physical_validation。新的1373或其他软件数量由真实输出产生；不得宣称本轮测试数量必须达到某个旧值。
- 编译/安装 wheel 后验证运行时注册数据与源码一致，避免只在仓库cwd才可用；不为此联网重装全部依赖，不复制 COMSOL JAR/许可证/私有文档库。
- 公开证据保留诊断所需结构化code/cause与脱敏上下文，私密原文留本机；不要脱敏成只有“失败”而失去可诊断性。

## 10. 执行顺序及最小关卡

### M0：无需引擎的根因回归

完成 C01、C02 的集中修复与原始失败transcript回放；复现 Java String[] 参数转换；修正 benchmark参数与字段解析；列清所有required方法实现缺口。

### M1：单一干净模型最小集成关卡

用当前授权的专用运行时重新验证生命周期与访问隔离，生成新receipt，不能引用旧PID/旧receipt证明当前状态。先跑：连接/建空模型→runtime探针→参数写读→正确推进revision→临时求值→有控制的失败/UNKNOWN→原job查证与恢复。外层/内层/job三者一致后再继续。

优先复用上一轮已经验证的 webbridge 路径与有守卫的流程，不重新猜文件路径/反复尝试已失败配置。当前运行身份、权限范围、原件哈希若已变化则停止相应操作并提出精确缺项。不能接管含用户未保存模型的旧 Server，不杀共享 Server。

### M2：最小物理闭环

先一条链 A，修到材料/边界/mesh/study/solve/采样/数值/保存全部有效，再链 B。任何 failed prerequisite 不允许依靠继续后续步骤得到部分PASS而叫完整链完成。

### M3：保留性与剩余原 G3 验收

链 C、R01–R06 applicable子项、T006/T007/T009/T015/T016/T017/T018/T019/T020/T034/T042/T048以及T005/T010/T033/T035/T038。只有正式既有验收需的最小结果能力，复杂W17不扩展。

### M4：受影响回归与独立重开

针对更改的错误传播、dispatch、revision、key和Worker marshalling执行受影响Phase2/G2回归，包括原job恢复、不重复求解、控制面查询、用户节点保留、检查点真实恢复。已有不受影响平台和历史故障不用机械重跑全部。执行A/B/C新Worker重开、资源/配置清理、final源码测试、证据一致性和发布检查。

## 11. 停止条件

### 成功结束

只有当以下条件同时成立，才标 `G3_1_MAC_EXECUTABLE_SCOPE_PASS`：

- C01/C02严重错误与状态混淆已修复，真实MCP/Worker路径及负向注入证明失败不伪成功、未知不重放。
- A/B从空模型到数值验收与独立重开通过；C非目标节点/数据/手动solver保留且修改后可求解、保存、重开。
- 原G3在当前可用环境内的required能力和相应负向例均满足原标准；没有用mock/拒绝所有调用/删断言/SKIP关键步骤代替。
- 可用而未跑的required子项或代码缺陷不能继续留BLOCKED却宣称阶段通过。真正缺少环境/产品的子项单列具体范围，不外推平台认证。
- 当前SOURCE测试、产物、summary、ledger相互一致；临时资源按批准范围关闭/恢复，不能清除失败证据或强制停止用户进程。
- 依当前有效授权正常非强制同步阶段源码/测试/脱敏证据，核对远端SHA/tree；认证受阻时保留本地提交并如实报告，不把同步失败当技术验收失败，也不冒称已同步。

### 受阻结束

若遇到真实外部阻塞，先完成所有不依赖该阻塞的本轮任务；停止重复同一失败且无新变化的循环，列出当前修复、未通过根因、最小人工动作、准确恢复入口与证据。软件完成不等于实机完成，使用 `IMPLEMENTED_WITH_BLOCKED_ACCEPTANCE` 或明确未通过状态。内部实现缺口与外部不可获得资源要分列，不让“安全门禁”成为没有调查证据的泛化解释。

**无论哪种结束，都不自动进入W17。**最终报告重点是已闭合的真实链、已定位根因和剩余风险，而不是工具数或测试数。

## 12. 审查证据定位（固定提交，代码可在本地有后续差异）

- `PROGRESS.md` 的 G3段；`evidence/phase4_acceptance.json`；driver4 `summary.md`。
- `_managed_backend.py::_invoke_g3_model`、`_execution_service.py::execute_legacy`、`_g3_results.py::_status/sample_path`：C01。
- `_execution_contract.py::SessionLedger`、`tools/phase4_run_mcp.py` 请求/修订助手、GUARD_T033原始requests/results：C02–C04。
- `_g3_runtime.py::_product_rows/_license_api_call`、Worker `modelUtil/invoke/convert`、T042原始输出：C05。
- G3 W15/W16实际实现、原始ChainA/C、T020返回、已安装目标build帮助：C06。
- `tools/phase4_run_mcp.py::CHAIN_A/CHAIN_B/CHAIN_A_MATERIAL_PROPERTIES/CHAIN_B_MATERIAL_PROPERTIES`、`_g3_results.py`T/t逻辑：C07。

随包 `review_probes/` 只含本次审查使用的独立控制流/Java反射夹具。它们不是完整仓库回归、不是COMSOL测试、更不是本轮已修复的证明。应在真实仓库新增对应生产边界回归，并按上文执行实机验证。
