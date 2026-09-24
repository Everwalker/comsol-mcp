# G3.8 验收：28项定义，原生与软件分开

本文件定义待执行验收，不是既有PASS记录。BOTH必须独立给win63/win64结果，SHARED给shared结果，共44条目标记录。不得将未知环境/未跑用例补成PASS。

## 基本规则
- TEST PASS只表示观察符合预先规定的期望；负控可以是validation FAIL/UNVERIFIED。
- STRUCTURE_AND_HASHES_PASS不证明原生运行。审计器一直返回native_execution_certified=false，需要审阅运行器和原始记录。
- 原生记录至少含mcp_transcript/runtime_identity/source_manifest/observations/checks五种角色的真实文件；没有原始请求响应不能声称production entrypoint。
- 所有观察明确source_kind；SYNTHETIC或CALLER_SUPPLIED不能满足原生要求。每个run有实际source commit、runtime build、版本目标；报告阶段不创造观察。
- validation操作默认只读/受控求值，不自动改变模型以满足判据。
- 判据在求解前冻结；不能把实际结果当作参考，又用同一份结果证明自己正确。不同版本都对同一独立参考比较，不能互相循环认证。
- report生成器允许格式化已验证记录，不得按case_id模板填入status、生产入口、测量值、耗时、内存。
- 延迟结果、清理失败、权限不足、无值、字段不支持、证据缺失分别记录；不要反复重试污染后续用例。

## 用例

### R00 固定源恢复和新目录边界
目标：SHARED；证据：STATIC。
1. 固定commit/tree和逐文件hash正确
2. 无旧目录/私有提交依赖
3. 失败不自动切main

### R01 历史证据纠正与合成样例隔离
目标：SHARED；证据：STATIC。
1. 旧合成数据逐项标明SYNTHETIC_FIXTURE且不篡改原字节
2. 新台账不得从报告模板产生PASS
3. 删除一个原始证据文件/造假的生产标志导致汇总拒绝或NOT_RUN

### R02 空验证和未知规则回归
目标：SHARED；证据：CONTROL。
1. 空数据、坏语法、未知oracle、缺required observation不可数值PASS
2. getter不可用不使ready_to_solve=true
3. 至少一个旧缺陷测试先失败再通过

### R03 Windows真实SID/ACE权限检查
目标：SHARED；证据：WINDOWS_NATIVE_SECURITY。
1. 用户名在路径中不匹配Everyone
2. 同名前缀主体被拒绝；零ACE解析/NULL DACL不可误报配置成功
3. Windows结构化DACL回读，策略精确SID/mask，失败先于私有端点发布
4. 仅改变测试目录

### R04 三层状态与报告非升级
目标：SHARED；证据：CONTROL。
1. 执行/数值/物理状态独立
2. 数值FAIL或UNVERIFIED输入不能被report改PASS
3. 负控测试PASS不等于坏模型数值PASS
4. 导出失败保留job/artifact身份且不重算

### R05 冻结基准和输入数学约束
目标：SHARED；证据：CONTROL。
1. 直接改expectations或外部dict不能改变冻结内容
2. digest含完整required-set/单位/测点/阈值
3. NaN/Inf/无效容差/缺项按契约拒绝
4. 趋零归一化走明确绝对误差定义而非默认1

### R06 操作契约与共享基础层
目标：SHARED；证据：CONTROL。
1. 8个validate.*的schema/effect/权限/来源一致
2. 默认验证不solve或改材料/边界
3. caller数据模式显式不认证模型
4. 真实source版本维持已有基础层而非测试专用旁路

### V01 两版本公开MCP冷启动与源外wheel
目标：BOTH；证据：INSTALL_PUBLIC_MCP。
1. 实际新venv wheel、无源码PYTHONPATH、四目录
2. initialize/list/call真实发生
3. 运行build、JDK、COMSOL类路径真实回读
4. 无注入FakeService

### V02 真实结构预检与未知读取
目标：BOTH；证据：PUBLIC_MCP_NATIVE。
1. 正常热模型支持规则读取
2. 无材料/空选区/未build网格或缺study时不ready
3. getters拒绝返回覆盖限制非PASS
4. 原模型无隐式修改

### V03 边界关系正负控
目标：BOTH；证据：PUBLIC_MCP_NATIVE。
1. 正常热边界通过已支持规则
2. 预设不合法边界/不正确sel确实检出
3. 优先级/覆盖合法的BC不误报冲突
4. 每个结果关联实际feature和实体

### V04 真实表达式及单位
目标：BOTH；证据：PUBLIC_MCP_NATIVE。
1. 有效表达式和单位实际求值
2. 非法语法/未定义/单位不匹配/非有限响应正确定性
3. 字符串和字典格式不能漏查
4. 清理自己的临时节点

### V05 解存在性、轴与观测完整性
目标：BOTH；证据：PUBLIC_MCP_NATIVE。
1. 不存在dataset/solution或未solve不能验证PASS
2. 只给一个通过点不能满足完整oracle
3. 错误runtime/model/outer/inner拒绝
4. callers给完美观察不能覆盖引擎坏结果

### V06 统计/复数/测度与非单位区域
目标：BOTH；证据：PUBLIC_MCP_NATIVE。
1. 复用FieldArray和MeasureSpec实际读取
2. 非单位体积常量/非均匀场核对积分平均std/rms
3. 单位和坐标、表达式/解轴完整
4. 不支持规则明确UNVERIFIED

### V07 稳态独立解析基准与功率
目标：BOTH；证据：PUBLIC_MCP_NATIVE。
1. 从空模型构建后回读设置
2. 三点312.5/325/337.5K误差≤0.1K
3. 两端功率80W及相对误差≤1%
4. 产品validator读取真实结果；预先冻结期望

### V08 瞬态正弦独立解析基准
目标：BOTH；证据：PUBLIC_MCP_NATIVE。
1. L=1m/alpha=1和初始/边界实际回读
2. 读取实际存储时间和9个观测点
3. 全部9点最大绝对误差≤0.1K
4. 不能将解析值加扰动作为观察

### V09 储能与源项守恒
目标：BOTH；证据：PUBLIC_MCP_NATIVE。
1. 稳态/瞬态分别定义边界功率、体源、储能与单位
2. 至少一个遗漏储能或符号反转负控不通过
3. 归一化和趋零情形可解释
4. 实际积分来自指定解和选区

### V10 实际三网格精度研究
目标：BOTH；证据：NATIVE_CONVERGENCE。
1. 每级真实mesh度量/元素数或DOF不同且计算
2. 固定dt/tolerance以隔离网格影响
3. 从独立基准计算误差并执行冻结目标
4. 相同网格和大误差递减不能PASS
5. 舍入平台有准确说明

### V11 实际三时间精度研究
目标：BOTH；证据：NATIVE_CONVERGENCE。
1. 固定足够细网格，至少3级真实步长上限或solver精度
2. 不把tlist密度当内部步长
3. 实际设置与采样时刻回读
4. 没有求解或资源测量的记录不能手填

### V12 可追溯报告与原子产物
目标：BOTH；证据：PUBLIC_MCP_NATIVE。
1. JSON/MD都有3层状态、coverage/exclusions/warnings、真实版本身份
2. 从已登记验证run聚合
3. hash绑定实际证据和冻结期望
4. 目标目录/权限/覆盖/磁盘负控不报输出成功

### V13 权限和无破坏验证
目标：BOTH；证据：PUBLIC_MCP_NATIVE。
1. 缺compute不得隐式solve
2. 验证前后参数/材料/边界/用户结果节点保持
3. 临时节点清理失败传播dirty/UNKNOWN
4. 项目外/私有文件/符号或重解析路径拒绝

### V14 图像/数值/同版本重开回归
目标：BOTH；证据：PUBLIC_MCP_NATIVE。
1. 公开MCP读数和ImageContent来自同一指定存储解
2. 同版本保存，fresh Worker重开不先solve
3. 复查内部场而不是边界常量
4. 真实图像未丢来源、job状态一致

### V15 证据/来源/观测篡改负控
目标：BOTH；证据：PUBLIC_MCP_NATIVE。
1. 改观察文件、SHA、版本或oracle导致报告不可验证
2. source_identity不是恢复baseline常量
3. 旧响应不能当新run观察
4. 合成origin不能通过原生门槛

### V16 真实job关联和故障恢复小链
目标：BOTH；证据：PUBLIC_MCP_NATIVE_CONTROL。
1. 观察正在执行的那个job/worker request而非另造RUNNING记录
2. 响应丢失取回原job不重复计算
3. 晚到结果与持久状态/事件/RPC一致
4. 当前未验证中止路线不得假报已停止

### D01 全软件回归与测试变更审计
目标：SHARED；证据：SOFTWARE。
1. 新env完整pytest输出和skip理由
2. 不通过删除/弱化失败测试收口
3. 数值helper和public集成测试分级
4. Windows与可访问Mac结果分别记录

### D02 运行源与发布源关联
目标：SHARED；证据：STATIC。
1. 运行前后source manifest含实际修改/新文件
2. 运行snapshot与提交对应，非仅固定数量
3. 每条required结果指向测试版本与原始响应
4. 无token/password端点泄露

### D03 清理旧目录后的独立恢复
目标：SHARED；证据：DELIVERY_RESTORE。
1. 新中文空格路径恢复固定或最终发布快照
2. 从新env运行可重复入口
3. 无旧数据依赖，不复制许可证
4. 恢复失败保留原因不降级完整性

### D04 能力/历史/阶段索引一致
目标：SHARED；证据：STATIC。
1. 旧合成报告标为历史不再认证
2. root/design/handoff状态同口径
3. 本轮不做W21，future工作明确
4. Host/GUI/原生取消/商业模块范围真实披露

### D05 Mac兼容边界
目标：SHARED；证据：SOFTWARE。
1. 公共代码不写死Windows
2. 能运行Mac实机则受影响回归，否则明确NOT_RUN
3. 保留以前真实证据范围，不声称新native验证
4. 平台适配与业务逻辑隔离

## 报告格式与审计

使用config/acceptance_report.example.json作为格式示例（刻意NOT_RUN，不能直接算通过）。每条record应有id/target/test_status/evidence_level/run_id/source_commit/checks/expected/observed/artifacts。原生记录增加native_context，artifacts中提供原始transcript/环境/source/观察/检查结果。

运行：
```
python tools/check_acceptance.py --definitions ACCEPTANCE_CASES.json --report PATH --evidence-root repository
```
该脚本能拒绝缺项、合成origin、hash错误、级别错误、缺请求/响应角色、缺真正的run关联字段；不能识别一个人刻意手工伪造的全套原始记录，不能替代真实执行和代码审查。
