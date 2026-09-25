# G3.10 主任务：恢复生产保护、完成真实W20，独立Reviewer循环验收

## 一、目标与来源
基线PIN.json为3a7c5feb...，当前活动目标只到W20。用户要求Windows上COMSOL6.3与6.4都可用，并保持Mac兼容；没有新Mac实机只能记录软件回归和未验证范围，不建立Mac→Windows SSH前置条件。
本任务不是重做整套COMSOL MCP，也不是加入W21扫描优化。已有原生建模、W17取数、W18图像和W19作业继续使用。针对REVIEW/FINDINGS中的九项缺口实施有限修补，完成原本要求的真实W20模型验证。

## 二、先组织真实团队和恢复
1. 主Agent完整读取本包，运行verify_package和bootstrap，创建新repository/并核对receipt。不需要任何旧本地文件。遵守RECOVERY中的不可恢复数据/权限范围。
2. 在当前Antigravity实际机制中建立Developer和独立Reviewer；加载roles对应文件，记录真实会话标识。禁止同一Agent扮演两个名字。独立审查不可用则明确阻塞，不能捏造分工。
3. Reviewer做R0：核对C10响应为未知oracle错误、当前run检查仍PASS，并冻结ACCEPTANCE与BENCHMARKS。主Agent依据TEAM_PROTOCOL按切片调度开发→冻结→独立复测→整改。
4. 读取恢复仓库的AGENTS、原设计05_DEVELOPER_TASK/04_IMPLEMENTATION_PLAN/01_ARCHITECTURE/03_ACCEPTANCE/06_CONTRACT_NOTES、当前handoff进度和实际源码。旧规范的scope边界可以更新当前指针，安全与数据真实性规则不能移除。
5. 初始化本轮source manifest、requirements hash、issues和progress。不要把git工作树被用户修改的其他内容清掉；当前包默认新目录，任何已有目录必须单独核对。禁止用remote latest替代固定来源来获得未知代码。

## 三、M1先修新回归，Reviewer验证后再继续
### R01 模型、修订和隔离
- 删除“拿ledger中第一个模型”的回退。允许显式ModelRef；若保留便利的当前模型，必须是server-side已授权且唯一/明确选中的binding，返回完整引用，多模型歧义明确失败。
- 绝不能把用户提供的过期ModelRef/revision自动改成当前值。只读可以有明确未携revision的合同，写入/临时数值/渲染/文件输出按真实effect要求进行检查。不要为了演示方便把所有动作同样强制或同样豁免。
- 移除validate.*、plot.*名称豁免。实质纯读动作不需要写级隔离，确有模型/文件副作用的操作则复用现有权限/所有权/队列/临时节点机制；通过明确effect和验证路径降低误阻塞，而不是跳过安全证明。
- 保护同Server其它模型、用户Desktop和另一COMSOL版本。缺当前安全运行前提就分离本轮专用实例按授权配置；不得改全局防火墙/安装权限、杀共享进程。
### R02 arguments归一化只在定义的兼容边界
- W20旧包装兼容可以保留，但实现只处理明确版本/明确入口；不能对所有工具flatten名为arguments的字典。
- registry_call/operation_call保持{operation_id,arguments}嵌套；code.execute_java的任务参数保持原Map；事务动作里的args保持形状。两处展开应归一为单一规则。
- 缺省None与字段“缺失”分开，不能用setdefault在已有None上无声丢掉兼容值。显式冲突应拒绝，不静默优先其中一个。
- 拒绝不支持的额外参数或报告实际未使用字段，不能无条件保留kwargs后当已生效。身份字段、request hash和幂等语义一致，三入口实际请求验证。

## 四、M2真实观测和有限规则，不再只检查caller数字
### R03 使用现有W17读取生产数据
保留外部纯数值比较能力，但命名scope=EXTERNAL_DATA_ONLY，不得据此得到MODEL_VALIDATED或ready状态。生产validate.solution需接受明确query或已登记ObservationRef：
`model_ref/generation/revision, runtime/build, dataset_chain, solution_ref, outer/inner/time/parameters, expressions/units, selection/frame, producer operation/job, artifact hash`。
内部通过已有W17结果/测度服务取数，接入相同队列与EVALUATE副作用；不要从验证函数再递归发公开MCP造成锁死。ObservationRef由后端产生，参数/模型/数据哈希不符、跨模型/跨版本/旧解需拒绝或明确historic scope。
独立Reviewer必做“错误模型+完美caller数字”，不能以origin标签改成CALLER_SUPPLIED就认为需求满足。当前模型验证若未取实际数据，应准确未验证而非数值PASS。
### R04 结构、表达式、边界的profile化检查
只承诺实际支持的热传导profile和通用结构规则；分别读取几何、选区测度/维数、材料必要属性或手动物性、网格元素覆盖、Study激活/Solver/解。存在tag不是完成证据；未覆盖/无法读返回UNKNOWN/NOT_COVERED。
表达式在正确global/component/solution上下文检查，regex仅lint。实体读取不调用all/set等修改fallback。不要把合法默认边界覆盖或纯Neumann问题简单判冲突；规则要声明适用问题、稳定tag和读数。
校验finite、bool、权重符号、单位、array shape及近零尺度；输出strict JSON，错误不是Infinity占位。临时数值节点唯一、清理自己节点、失败传播。

## 五、M3真实守恒和收敛，修验收而非只修字符串
1. 先修C10基准名称与正向响应断言，检查实际product status/oracle verdict，不用not isError单独判科学通过。旧37/37不擦除，新增准确分类和误差摘要。
2. 删除“缺热流回读则用79.999...”及所有缺关键字段→True/PASS/0的成功默认。producer operation ID必须来自真正数据生产步骤，不能用validation消费步骤或随机UUID冒充。
3. BENCHMARKS中参考在计算前冻结。实际独立积分两端热流与体源，瞬态积分储能或声明有限适用范围；不能把同一个数字同时传inflow/outflow再证明守恒。
4. 三网格、三时间精度由runner在任务测试模型/副本中真实设置、build/solve/取数，记录实际元素/DOF/时间设置/耗时和多个case refs；不能只有一组固定列表。
5. 为区分误差是否真的变化，用正弦非均匀稳态或瞬态场；线性温度场可能已被低阶有限元精确表示，不单独作为网格收敛判据。误差、趋势、最终容差、舍入底噪分别报告；不强求伪造单调。
6. 不要求W21通用优化器，只需本轮有限3级实验runner。真实操作仍受资源预算约束；每一新计算需要明确权限，检查函数默认不solve。
7. 重新打开测试必须新Worker从同版本同SHA文件重新采样，清除旧caller数字缓存；原解没保存时报告缺解，不能重算后冒称存储解读取通过。

## 六、M4报告、证据和完成判据
### 报告与产物
validate.report汇总已登记ValidationRun和真实身份，execution/numerical/physical三层不互相提升。未知状态和缺required不变PASS；没有实验或独立物理依据physical=UNVERIFIED。
实际文件输出接入ArtifactStore，strict bool，默认no overwrite，JSON与MD目标均预检；staging/清理/登记失败报告partial，已有文件字节不变。不得将Path.write_text称成原子发布。不为修path删除通用Java能力，而是按独立trusted_code授权处理。
### 真正的验收采集
保留已存在的真实RecordingSendStream/ReceiveStream；将它只用于记录实际消息，不生成数据。公共validator的成功和engine的正向/负向结果必须同时断言。负测试故障模型返回FAIL时，test_case可以PASS，区别显式记录。
case的producer/model标识从实际响应提取；当前模型是transient时不能把copper的ModelRef或采样量贴进结果。runtime build/worker实例实际读取，不从版本字符串和run_id拼接。
Reporter只读回执并计算汇总；不得执行模型、填测试观测或静态赋passed=True。源码文件/所需级别/哈希数字不能证明实机。Reviewer对所有required逐项追溯和抽测。
### 两版执行与外部限制
正常wheel安装A、源码B、科学项目C、cwdD、private E分别独立；真实冷启动，不依赖PYTHONPATH、手工注入service/lease或旧回执。当前已安装6.3和6.4分别走完整规定链。
共同源码变化后，重跑受影响两版本案例。无法访问Mac/云端Host，记录UNVERIFIED，不把ping或工具发现当模型已看到图。原生取消缺口与本轮无关时保留安全拒绝，不扩大为重做W19。

## 七、角色循环与停止
严格TEAM_PROTOCOL：Reviewer先冻结，再Developer实施；每轮Reviewer对新snapshot独立跑反例和必要native测试；CHANGES_REQUIRED交回Developer，复修复测直至必需范围闭合。主Agent无权替代Reviewer写APPROVED。任何代码/标准在批准后改变，旧批准对新版本无效。
本轮artifact/测试/报告放一个阶段根，避免重复几十份全通过文档。roles只是起始提示，不由本包自动创建子Agent；必须使用Host真分派或第二独立会话。
成功：本轮ACCEPTANCE每个required target通过，当前快照独立Reviewer APPROVED，无未闭P0/P1，正向/负向科学验证一致，源码/运行/发布身份自洽，新目录恢复可复现，输出实际限制。然后停止，候选下一阶段W21只能另行授权。
受阻：真实权限/许可证/计算环境/独立Reviewer不可用，继续其它安全可执行工作；待独立工作完成报告已完成部分和最小下一动作，不把BLOCKED放进完成。不要无依据循环跑相同失败，也不能为达目标修改容差或删required。
提交/上传仅按用户既有明确权限普通非强制操作；否则给本地commit/patch与恢复包，不声称GitHub已更新。扫描将发布对象，禁止把运行token/private credentials/用户唯一MPH上传。
