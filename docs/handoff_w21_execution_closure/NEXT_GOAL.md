# 当前任务：W21真实执行收口（主Agent开发，独立Reviewer）

## 1. 当前源与目标
恢复PIN.json的公开c2ca5e4提交。它新增W21类/接口/演示和W20签字，但脚本没有实际调用COMSOL完成W21。保留代码和控制测试，将既有五项接入生产，不写第二套实现。**不把“系统分析”变成再次全仓审计，不追加required集合，不进入W22。**

读START_HERE、REVIEW、CLOSURE_MAP、TEAM_PROTOCOL、frozen/W21_PLAN、frozen/BLOCKER_POLICY；frozen/还保留原W20合同及旧Goal字节。原W20停止前W21的旧字段已经被Closure授权覆盖；本轮允许继续已开启的W21实现，但最终通过仍遵守原依赖。

用户运行Antigravity；主Agent可以直接开发，无需独立Developer；必须建立实际独立Reviewer。Windows本机6.3和6.4分别执行，Mac兼容保留但未访问的平台不新增认证前置。

## 2. 恢复与一次性差距确认
- 执行verify_package/check_contract/bootstrap，不能覆盖旧目录；恢复后的当前说明在docs/handoff_w21_execution_closure/。
- 创建一个本轮PROGRESS，比较真实源码/原始证据与原五交付；已有代码正确部分复用。不要重跑全部历史文件审计、不要追逐增加一套证书/hash服务。
- 发现实际Windows安装、完整build、JDK/compiler、classpath和权限；不复用旧token/端口/进程身份，保护现有用户模型及共享Server。源码/venv/模型项目/私有控制根分开，普通wheel入口冷启动而不是注入fake service。
- 若远端或本地有更新，不reset回PIN；在独立目录比较并记录采用的真实源。没有旧本地文件不阻塞恢复，但未提交科学数据不能凭空恢复。

## 3. 原依赖的有限纠偏（先小链，不开全审）
当前原W20 T06仍允许caller自报ref、T09/T12仍有数值通过就physical PASS；它们直接影响W21候选验证。复用持久metadata和ArtifactStore登记真实W17采样/producer，验证从已登记引用取值，caller不能替换；无独立物理证据只保留numerical结论。
原T11/T14签字的精化/重开证据只作有限核对；没有对应实际生产记录就按原要求补，用W21真实case/阶段测试共享夹具和求解记录，避免重复计算。其他未改要求按可靠旧证据复用，禁止整体重开27项。独立Reviewer针对原T06/T09的明确反例及原规定关键目标确认，旧签字原件不修改，只追加更正。
如果验证逻辑读到了错误数据仍可通过，这是原合同阻断，不得作为“减少scope”延期。风格、更多GUI/平台/算法不阻断。

## 4. 原W21-1：参数case真正改变模型并求解
先实现一条公开MCP入口：登记case→应用指定参数并回读→在指定模型/Study求解→通过W17读取指定存储解→W20选定规则验证→持久case结果。然后扩到原要求的2参数×瞬态小扫描。
传入model/parameter/study/definition缺失应明确错误，不能默认替换成铜块demo；action必须兑现其语义。定义单位并与引擎一致，case ID、实际参数、状态、producer job与解索引可追溯。
inner/outer/time不要混淆。可以有自己的case序号，但须另外映射真实解信息。请求不存在时刻时明确选择/插值策略、返回实际时刻，不能静默nearest冒充exact。物理式子仅用于独立oracle或显式DEMO，不作为生产输出。

## 5. 原W21-2：预算和结果复用
将现有ResultCache与case orchestration接通，不只单测独立dict。复用已存在OperationStore/ArtifactStore/jobs，避免另建全局无边界数据库。
参数/单位、实际模型与求解设置、引擎build、输入数据身份构成key；不得round到固定8位令不同输入碰撞。可使用数值无损规范化/明确的容差语义，但容差必须由契约显式提供，不能偷偷近似缓存。
同request重试不重复solve；改变模型/输入/配置/版本后不得使用旧结果。缓存命中返回原producer与来源，失败/UNKNOWN/未完成case不可被缓存为成功。
预算在派发前原子核对，命中cache与实际compute计数分清；墙钟预算到期只阻止新候选，不谎称底层COMSOL已停止。保留连续失败计数与总失败数的真实含义。并发只需原范围，禁止同Server无锁写。

## 6. 原W21-3：有限优化使用真实目标
沿用现有有界网格/模式搜索即可，不要求全局最优或新算法框架。替换默认eval中的固定325K/abs(k-400)为共享case executor的真实结果。
候选的engine失败、未知状态、缺目标、非有限目标/残差、单位不符、违反约束均不能成为best feasible；记录具体状态与已发生动作，不返回Infinity/NaN污染协议。参数边界/约束在计算前检查，不能静默夹紧到另一问题而不说明。
预算耗尽返回“当前最佳已验证可行候选”或“未找到可行”，而非声称求得全局最优。如果候选来自cache，仍验证原结果与本次身份。Reviewer独立重算一个返回候选并检查其约束，范围由原冻结任务决定。

## 7. 原W21-4：真实阶段末态→初态
StageCheckpoint/TransferManager可保留metadata用途，但数据来源必须是实际存储solution/已登记场，非客户端默认T=325或domain[1]。源模型/解/时间/选区/单位/producer与目标模型/阶段明确。
配置目标Study或物理初值使用源末态，回读设置并评价阶段2初值；多点空间场与源末态一致。再续算一个短阶段，与已知连续参考或独立整体运行对照。仅返回target_initial_state字典不能算apply/verify成功。
错误映射、冲突目标、单位不兼容和过期源先拒绝；历史不静默重置。checkpoint实际校验来源与哈希，不用常量source_hash_verified=True。保留原checkpoint.create语义，避免和W12已有模型检查点路由碰撞。
仅原T047通用传递子项，胶形、UV化学、应力领域全部留W24，不新建它们作为本轮前置。

## 8. 原W21-5：真实双版本与入口
用同一源码在实际Windows6.3/6.4各自执行五项通用链；不能仅向Python函数传两个version字符串。实际build、Worker、model/ref/solution、参数与输出从引擎回读。
核对公开_tools_w21工具名和OPERATIONS ID，明确alias→canonical映射并保留execution/请求hash。stage/optimization/checkpoint不能依赖stub返回的merged dict（Gateway是否调用它要按真实代码验证）。直接函数单测可留，但至少一条实际tools/call贯通以及同版保存新Worker重开属于原native交付。
现有scripts/run_w21_acceptance.py改为控制用例或明确演示，再增加/复用真正native runner；报告器只汇总运行记录，不填PASS和生成伪transcript。已有版间数值完全一致只能说明演示公式相同，不作为build兼容证据。

## 9. Reviewer闭环与有限终点
Reviewer依据冻结五项、原相关W20要求与A1/A2，独立检查主代码、实际日志并复测必要正负控。只需要一份简明差距表和审查结论；不以新一套签字校验平台证明review发生。
主Agent修→冻结候选→Reviewer复审；若同问题重复，先最小纵向复现而非重跑全部报告。不设置最大轮数自动通过。发现新非阻断建议进DEFERRED_BACKLOG；严禁改旧冻结集合、数值阈值和最低证据等级以过关。
能够独立推进的实现/测试继续，缺实际许可/授权/第二版本/Reviewer能力时只阻塞相应结果，不能mock补native。没有能满足原required的环境则如实受阻交付，不称完成。

## 10. 交付与停止
完成五项真实执行、原依赖必要纠偏、相关回归，原始请求/引擎结果/模型/参数/源文件对应清楚；候选和审查结论一致。保存脱敏证据、模型或可重建recipe、当前源码和新环境恢复说明。
按已有明确权限普通非force提交/同步，未授权上传的用户科学数据/凭据不公开；无push权限给可核对本地commit/patch，不虚构remote SHA。恢复后的最终交付不能只是一份更新报告。
原required通过且没有A1/A2，Reviewer必须批准W21_SCOPED_APPROVED，即使待办非空。**完成后停止，不自动W22。**候选W22可在报告列为下一步，不能把它的更多物理需求前移本轮。
