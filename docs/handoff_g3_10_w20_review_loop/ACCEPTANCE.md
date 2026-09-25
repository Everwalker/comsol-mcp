# 本轮冻结验收：16项、双版本分别记录

这是待执行定义，不是当前PASS；细分目标由ACCEPTANCE.json给出。本轮只修明确问题并收口W20，不增加新业务阶段。

正向成功必须同时检查实际传输、执行、validator科学结论、目标模型和raw数值。**isError=false不是模型已验证；温度误差满足不能替代validator失败。**负控允许且要求故障模型返回FAIL，但独立测试的断言通过，两者字段分离。

Reviewer对每个候选快照执行独立测试，不能只读Developer checks。任何required target缺失/低于最低证据，不得在总结里自动跳过。控制测试可验证故障传播但不可顶替native。无权限/许可可以阻塞并继续其它工作，不能篡改scope获得完成。

|ID|目标|最低证据|判据|
|---|---|---|---|
|T01 恢复与来源|shared|SOURCE|固定commit/tree和所有tracked blob一致；无旧本地依赖；不覆盖历史receipt，缺LFS backing objects明确报告。|
|T02 真实独立Review和合同冻结|shared|WORKFLOW|两个不同真实会话ID/Host创建记录；Reviewer先做R0、冻结合同，对当前candidate独立负控；不是角色换名。|
|T03 参数三入口及非W20回归|win63,win64|PUBLIC_MCP|点号/下划线/operation_call逻辑body一致；registry arguments与Java任务Map保留；None缺省包装和冲突/未知字段正确。|
|T04 显式目标与双模型无串扰|win63,win64|PUBLIC_MCP_NATIVE|同Server两模型同dataset名，缺绑定/歧义拒绝而非取第一个；显式旧ref/revision拒绝；返回值来自指定模型。|
|T05 实际effect与隔离|win63,win64|PUBLIC_MCP_NATIVE|纯读可以按读合同工作；有临时节点/文件副作用必须通过真正effect门禁；validate/plot名字不豁免；无有效凭据不写入。|
|T06 可信观测而非caller数字|win63,win64|PUBLIC_MCP_NATIVE|坏模型＋完美caller数组不能获得MODEL_VERIFIED；从W17/ObservationRef取actual source+solution+units+producer；跨模型/篡改hash负控拒绝。|
|T07 结构、表达式和边界只读|win63,win64|PUBLIC_MCP_NATIVE|真实built mesh、物性/手动配置、有效Study及热边界规则；读失败不PASS；getter失败不all/set；合法例外准确NOT_COVERED。|
|T08 稳态场与有向功率|win63,win64|PUBLIC_MCP_NATIVE|B1实际采样3点及两独立端面积分；0.1K/1%阈值预冻；缺readback不回退80；每个producer响应可追溯。|
|T09 瞬态验证器实际成功|win63,win64|PUBLIC_MCP_NATIVE|正确注册oracle名、9个真实存储点；响应isError、success、validator状态与numerical同时满足正控；原C10错误oracle负控确实FAIL。|
|T10 真正瞬态储能守恒|win63,win64|PUBLIC_MCP_NATIVE|实际场积分/同时间边界通量/储能，符号和单位及误差口径明确；同一个Q两边复制不能作为独立证据。|
|T11 真三网格与三时间精度|win63,win64|NATIVE_CONVERGENCE|两组各≥3级实际设置/build/solve/run refs；实际元素/DOF或时间策略，固定采样误差由raw算；硬编码列表输入只可EXTERNAL。|
|T12 状态格与数学失败负控|shared|CONTROL|正向validator FAIL不得case PASS；负控case PASS不把坏模型变好；NaN/Inf/负误差/未知状态/缺字段不变PASS；真实返回缺ID不能随机补。|
|T13 安全报告发布|win63,win64|PUBLIC_MCP_NATIVE|报告读取ValidationRun，JSON/MD同一结论；默认不覆盖、两目标预检、atomic/partial有证据；权限外、字符串false、写失败负控；isolation不靠名字跳过。|
|T14 新Worker重开重新取数|win63,win64|PUBLIC_MCP_NATIVE|同版本同保存SHA，新Worker取实际存储场并验证；旧t2/缓存不能代替；缺存储解/错文件负控；必要图像绑定和产物同源。|
|T15 raw证据与汇总正确|shared|AUDIT|C10原始错误样本必须被正向检查器判不通过；缺测默认值/假UUID/固定native数值/失败日志都不能PASS；build/Worker/source实际读取。|
|T16 最终快照、回归与双角色裁决|shared|SOURCE_AND_REVIEW|当前代码/测试/规则/runner hash一致；Reviewer独立APPROVED同snapshot；每个required target有证据且无open P0/P1；最终新目录恢复/源外安装，明确未测Mac/Host。|

## 证据最小字段
candidate_manifest_hash、contract_hash、case_id/target、实际OS/build/JDK与model_ref、producer job/operation、执行命令/启动路径、原始MCP request/response、validator状态与期望、数值/单位/解/时间、对应artifact hashes、断言输出、Developer和Reviewer会话ID、独立复测结果。
report/gate工具只检查格式/引用/哈希，不证明运行者未伪造这些内容。真实Host创建记录、源审查和Reviewer执行是必要组成。

## 保留范围
当前可访问Windows6.3、6.4分别运行，不能复制build字符串或另一版本记录。Mac无实机保持UNVERIFIED且做共享代码软件回归；云端Host未收到图则不称端到端视觉完成。全套商业模块/GUI/取消不在本轮新增要求。
