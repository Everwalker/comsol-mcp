# G3.11验收：16项定义，27条目标记录（全部待执行）
沿用原T01–T16的范围，不用旧37条C记录代替。新角色结构是主Agent开发、独立子Reviewer；不要求独立Developer。核心案例两Windows版本分别记录。

## 一致性和独立性
每条test_status PASS必须有实际source/target/build/request/observed/expected/checks和artifact引用。CONTROL不升级为NATIVE；Reviewer审查不只读取主Agentsummary。独立复测的选区、采样点或输入反例由Reviewer自己预登记，不针对已知输出硬编码。
原生聚合可只读先前运行记录并engine_calls=0，但先前必须是真正独立采样/精化/求解，不能输入固定数组。引用hash证明字节一致，不证明引擎来源。
Reviewer逐项追溯16项/所有目标；最低独立重测覆盖T03、T06、T09、T11、T13、T14涉及的新增核心行为。其他有效历史native证据可按明确源码scope复用，但不得据此签署未测试的新实现。

|ID|项目|目标|最低证据|通过标准|
|---|---|---|---|---|
|T01|干净恢复、来源与角色入口|shared|SOURCE|固定PIN/blob/tree恢复；独立目录/源外安装；主Agent可以直接开发，不能依赖旧资料。|
|T02|主开发与独立Reviewer|shared|WORKFLOW|主Agent与Reviewer为不同实际Host会话；冻结要求与候选；Reviewer至少自己构造一个未登记ref反例；角色ID和hash不当作实际审查证明。|
|T03|参数三入口及非W20回归|win63,win64|PUBLIC_MCP|点号/下划线/operation_call逻辑body一致；registry arguments与Java任务Map保留；None缺省包装和冲突/未知字段正确。|
|T04|显式目标与双模型无串扰|win63,win64|PUBLIC_MCP_NATIVE|同Server两模型同dataset名，缺绑定/歧义拒绝而非取第一个；显式旧ref/revision拒绝；返回值来自指定模型。|
|T05|实际effect与隔离|win63,win64|PUBLIC_MCP_NATIVE|纯读可以按读合同工作；有临时节点/文件副作用必须通过真正effect门禁；validate/plot名字不豁免；无有效凭据不写入。|
|T06|后端观测来源与完整绑定|win63,win64|PUBLIC_MCP_NATIVE|未登记ref、缺hash、自算hash、caller values替换、跨runtime/session/generation/revision/solution、producer不存在全部拒绝；正常登记值与W17采样一致。|
|T07|结构、表达式和边界只读|win63,win64|PUBLIC_MCP_NATIVE|真实built mesh、物性/手动配置、有效Study及热边界规则；读失败不PASS；getter失败不all/set；合法例外准确NOT_COVERED。|
|T08|稳态场与有向功率|win63,win64|PUBLIC_MCP_NATIVE|B1实际采样3点及两独立端面积分；0.1K/1%阈值预冻；缺readback不回退80；每个producer响应可追溯。|
|T09|瞬态9点及三层状态|win63,win64|PUBLIC_MCP_NATIVE|B2真实9点存储解与oracle正确；isError/业务status/数值正控一致；无独立物理依据physical仍UNVERIFIED；故意错误Cp/初值负控被同一检查器抓住。|
|T10|真正瞬态储能守恒|win63,win64|PUBLIC_MCP_NATIVE|B2同一时刻独立能量及端面通量/体热源采集；储能率含时间/差分误差；不以两边复制同值过关；normalization=字符串1e309拒绝。|
|T11|真实三网格和三时间研究|win63,win64|NATIVE_CONVERGENCE|每版至少3档实际网格与3档时间精度，回读设置、element/DOF及实际解；producer和case refs可追溯；固定列表、重复网格、伪造error拒绝。|
|T12|科学状态、数学和strict JSON|shared|CONTROL|数值PASS不提升physical；bool/NaN/Inf、无效误差阈值/权重、零尺度拒绝；报告/错误输出strict JSON；负控测试PASS不等于模型PASS。|
|T13|可信输出根与报告组提交|win63,win64|PUBLIC_MCP_NATIVE|root仅取后端配置，读失败不从dest.parent或/建权限；拒绝后不resolve重试；JSON/MD整组预检、partial发布/登记准确，原文件不损。|
|T14|新Worker实际重开采样|win63,win64|PUBLIC_MCP_NATIVE|保存后新Worker同版本加载同一SHA且不重算；重新采样新ObservationRef，对比原解；不能传入旧t2缓存；缺存储解/错文件负控有效。|
|T15|原始证据与合同覆盖|shared|AUDIT|原始消息从实际stdio收集，所有required按原T合同而非旧C计数覆盖；主Agent摘要和Reviewer签字不得压过源码及测量；旧R03结论保留并追加纠正。|
|T16|最终候选与独立验收|shared|SOURCE_AND_REVIEW|主Agent完成开发回归；Reviewer逐项审计并每版独立复测关键来源/安全/原生反例；当前snapshot匹配，无本轮开放P0/P1。审批后改代码必须再审。|

## 不能靠检查器证明的事情
review_gate.py只检查合同/候选/角色引用和证据引用的完整性，不认证真实Host分派或COMSOL执行。若全部JSON被人为编造，哈希检查无法识别。因此保留Reviewer实际独立命令、自己的测试源码和原始native消息；主Agent不能把`PACKET_CONSISTENT`当产品PASS。
未知权限、Host独立会话不可用、引擎许可证/平台缺失单列BLOCKED；本轮确认的实现缺陷仍为FAIL/OPEN，不能改为环境阻塞。停止之前不能省掉第二个COMSOL版本。
