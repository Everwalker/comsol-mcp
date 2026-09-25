# 实际独立子Agent工作循环

## 1 角色与启动证明
主Agent是Orchestrator，仅调度、冻结候选、管理有限资源和汇总；实际产品代码由Developer子Agent修改；独立Reviewer子Agent做需求对应、源码审查、反例和实测。Reviewer不是Developer换名字，不是在同一上下文继续说“我审查通过”。
启动时先核对当前Antigravity产品/版本的真实子Agent工具。官方CLI文档支持独立会话与权限划分，IDE也可多会话，但不能假定每个界面具有相同命令。使用宿主实际机制创建两个不同session/agent ID，记录宿主返回的真实ID及启动事件来源；不要凭空生成ID冒充子Agent创建。
若此Host确实不能派生独立上下文，可创建可用的第二Agent会话并给它roles/REVIEWER.md和候选路径。若两者均不可用，只允许准备或开发，标INDEPENDENT_REVIEW_UNAVAILABLE；不自行签署通过。无需因此停止其余不依赖独立审查的准备。
本包只提供角色指令及检查工具，不是已启动或自动运行的agent服务。

## 2 权限和工作区
- Developer：读全部规范，改生产源码和普通开发测试，写开发结果；不能修改独立Review记录、放宽冻结判据、删反例使通过。
- Reviewer：读候选源码、原始日志和冻结规范；可在独立测试工作区创建/修改对抗测试，执行获准的单元、公开MCP和本轮专用COMSOL测试。默认不改生产源码。发现问题交回Developer；Reviewer亲自修代码即成为该补丁作者，需另一独立审查者复核此补丁。
- Orchestrator：发起实际子任务、建立source snapshot和资源租用记录、判定审查是否针对当前快照。不得覆盖Reviewer拒绝、替Developer签署评审、把schema工具结果当native通过。
角色权限只是在Host支持范围内实施的工作流约束，不是防御机器管理员的OS沙箱。不能通过chmod文本文件宣称强隔离。生产源权限、科学目录、COMSOL共享Server和用户原始模型依旧遵循项目安全规则。

## 3 冻结规范与初次审查 R0
Reviewer首先不看Developer的结论，读取本包ACCEPTANCE/REVIEW和原设计目标；检查C10已提交response的FAIL与checks的PASS矛盾，生成独立baseline记录。
将本轮验收定义、基准参数/容差、期望状态语义、对照模型和角色规则哈希写入contract_manifest。Reviewer可以补关键反例，但不能降低原要求或把许可/代码缺口删除。任何改变必需能力/容差需要明确理由；涉及缩小用户要求的变更不能由Developer单方面批准。
不要求一次把所有历史测试重新执行；先冻结本轮有限范围，使“修完什么算完成”可以计算。

## 4 循环：Developer → Reviewer → Developer
每个循环均记录 round_id；source snapshot包含当前生产源码、tests、runner、Schema、benchmark及冻结合同（不含生成证据本身，避免循环哈希）。可以是commit+dirty manifest，不要求无关用户修改也提交。
1. Developer选一组满足依赖的P0/P1问题，提交实际代码、最小回归、执行命令和证据位置。不能只写说明。
2. Orchestrator冻结该候选，停止Developer修改该目录，给Reviewer只读候选/独立副本。若测试依赖同一COMSOL Server，串行分配本轮专用实例，不让两个Agent同时改同一模型。
3. Reviewer先从规范设计至少一个独立负控，再读diff/原始结果。核对工具契约、请求body、实际response、engine来源和断言。不得依赖Developer摘要或“所有pytest通过”代替验收。
4. Reviewer输出 APPROVED / CHANGES_REQUIRED / BLOCKED，并列每个finding的源位置、复现、严重性、对应case、最低修复要求、实际执行的证据层级。每个裁决绑定source snapshot和contract hash。
5. CHANGES_REQUIRED交Developer修复；新源码产生新snapshot，至少重测失败项及影响路径，再复审。不因旧probe不再触发就宣布整个W20完成。
6. APPROVED只对当前候选和范围有效。任何后续生产/测试/规范变更导致旧裁决过期；再核对影响范围并补评审。运行环境改变但源码相同也要说明哪些native证据失效。

## 5 审查必须独立做的验证
- C10正向请求返回isError=true时，整项正向验收必定不能PASS；不要拿无关温度误差替代validator结论。
- Developer加上任何操作名前缀来免除权限/隔离的改动默认重点审查。选模型不允许依赖dict插入顺序；显式旧revision不得被替换。
- 给registry_call/operation_call/code.execute_java构造带arguments的合法嵌套body；W20兼容包装不能破坏其它调用。
- 使用错误模型＋完美caller数字的盲测，验证不能获得当前模型验证通过；使用同一dataset名的两个模型测试来源串扰。
- 在宣称三网格或三时间级别前，自己检查不同级别的设置、引擎request、元素/时间/数值和误差由来；看到固定列表直接拒绝native认证。
- 报告写入失败、缺字段、NaN/Inf、未知规则/阈值、未证实清理，不能变成科学通过。负控测试的PASS与产品返回FAIL分开记录。
Reviewer不必故意注入不安全OS故障或杀共享引擎；无法安全native注入的案例可用CONTROL注明边界，不伪装成真实引擎测试。

## 6 资源与停止
相同Server普通API串行，独立Reviewer使用自己的新测试模型/输出root；复用只读保存模型时核对hash。同一时刻只有一个Agent获准做该引擎变更。没有任何角色可关闭用户共享Server。
执行循环不设任意“3轮就算过”。相同根因连续出现而没有新证据时，先缩小复现/重新设计，不循环重跑全套。仅在真正的环境/权限/许可/独立会话不可用、且所有可执行工作完成后受阻结束；明确尚未达到完成，不让BLOCKED挤进APPROVED。
终止：所有本轮required case的每个target达到所需真实层级，Reviewer对当前candidate无P0/P1未闭合项，源码/合同/报告一致，交付完整。否则输出明确部分成果和剩余项。停止W20，不自动推进W21。

## 7 记录最小化
只保留一个活动progress、一个issues表、每轮Developer交付与Reviewer裁决，以及必要raw artifacts。不要重复生成多套新的“全通过总结”。建议 `.handoff/rounds/<id>/` 存小型元数据，`evidence/g3_10/<run>/` 存可公开原始证据，private control在外部私有目录。
review_gate.py仅检查结构/哈希/一致性，不能证明session没有伪造或引擎真的运行。独立角色是实际执行机制，不是JSON字段或哈希本身提供的真实性。

## 8 快照与检查工具示例

在恢复的repository中，主Agent冻结本轮范围（根据实际项目补齐，不直接漏掉调用器/Schema/规则）：
```text
python ../tools/source_snapshot.py --root . --include comsol_mcp tests tools pyproject.toml docs/handoff_g3_10_w20_review_loop/NEXT_GOAL.md docs/handoff_g3_10_w20_review_loop/ACCEPTANCE.json docs/handoff_g3_10_w20_review_loop/BENCHMARKS.md docs/handoff_g3_10_w20_review_loop/TEAM_PROTOCOL.md docs/handoff_g3_10_w20_review_loop/roles --output .handoff/rounds/R01/candidate.json
```
Reviewer与Developer的packet均绑定候选manifest的SHA256。每个独立测试记录也保留相同candidate摘要；环境未变化的旧证据只能按明确影响复核引用，不能自动标成独立重跑。审批后新增文件也会使源文件集合检查失败。
`config/review_packet.template.json`初始NOT_REVIEWED，真实角色ID来自Host，不自行生成。
```text
python ../tools/review_gate.py --packet .handoff/rounds/R01/review_packet.json --contract docs/handoff_g3_10_w20_review_loop/ACCEPTANCE.json --frozen-contract-sha256 <R0冻结的真实hash> --root .
```
命令输出仅证明packet文件层面一致；它不会认证实际独立会话、许可证、COMSOL运行或物理正确。最终裁决必须来自真实Reviewer。
