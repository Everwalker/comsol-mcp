# G3.9验收定义：23项、37条目标记录

这是待执行定义，不是PASS记录。BOTH分别给win63/win64，SHARED给shared。Windows两版本结果不能互相代替。

## 共同原则
科学结论与测试结论分离。负控测试应通过“产品确实拒绝了错误模型/输入”，不能要求错误模型数值PASS。未知环境/规则/来源均保留未验证。
原生验收必须走普通安装的MCP生产链。受控Java可建fixture，但validate.*的结果必须来自公开调用；不能自己算完后填一份tools/call记录。
报告中的RUN、job、model、runtime、producer操作和原始观测要能相互定位。术语“哈希匹配”只说明字节一致，不说明计算真的执行。
不得以新自测文件数和预先填好的状态作为验收。MCP响应保留严格JSON，不使用Infinity/NaN或任意字符串代替数值错误。

## 执行顺序
C00/C01→C02（先各版本一个真实调用）→C03–C08→C09/C10/C11→C12/C13→C14–C19→C20–C22。
测试可按依赖调整顺序，但不能绕过权限或把上游故障导致的后续错误当全部功能不支持。

### C00 固定源干净恢复
目标：SHARED；证据：STATIC。
- pin commit/tree和逐文件哈希通过；不退回main
- 中文空格路径；没有旧工作包依赖；失败保留staging

### C01 旧证据范围纠正
目标：SHARED；证据：AUDIT。
- 保留旧原字节；分别重分类实际温度、常量热流/收敛、重建transcript
- 报告器不得根据case名或source文件生成PASS

### C02 公开三入口参数与身份
目标：BOTH；证据：PUBLIC_MCP_NATIVE。
- 新wheel/A-B-C-D目录，标准stdio initialize/tools/list/tools/call
- 点号、下划线和operation_call实际参数相同；两个不同阈值导致可观测不同结果
- 冲突字段和错误ModelRef拒绝；execution/幂等身份保留

### C03 检查绝不改边界选区
目标：BOTH；证据：PUBLIC_MCP_NATIVE。
- getter不可用不调用all/set/clear/run；模型关键选区前后相同
- 空选区、未知维数、跨component同编号、覆盖边界的结论有正确范围
- caller boundary_data不能覆盖当前模型检查

### C04 表达式上下文与单位
目标：BOTH；证据：PUBLIC_MCP_NATIVE。
- 独立测试undefined变量、错误单位和坏括号，不让一个反例掩盖另一个
- 合法2*-3、2^-3及负指数单位不被regex误拒
- 真实引擎读取失败不PASS；无数据不数值PASS

### C05 材料网格Study就绪边界
目标：BOTH；证据：PUBLIC_MCP_NATIVE。
- 存在材料tag但缺k/Cp或未覆盖域，检查不误PASS
- 网格未构建和study未启用目标physics得到准确拒绝/UNVERIFIED
- 真正read-only不求解，不改节点；unsupported规则不冒充已检查

### C06 来源绑定与客户端注入
目标：BOTH；证据：PUBLIC_MCP_NATIVE。
- 观测由后端W17动作产生且绑定job/request/ModelRef/solution
- 注入完美caller数值不能掩盖真实错误模型
- 不存在/空dataset、旧generation/revision、跨版本Ref拒绝；origin不能自报

### C07 观测完整性与测度
目标：BOTH；证据：PUBLIC_MCP_NATIVE。
- 必需测点、时间、单位、权重和坐标完整；错误轴/缺数据不PASS
- metadata/hash/producer一致，已登记产物损坏或移除不能继续验证
- 明确外部数组计算不伪装当前模型验证

### C08 外部数字检查与数学输入
目标：SHARED；证据：CONTROL。
- 外部纯数值mode单列INPUT_ONLY_CHECK
- bool/NaN/Inf/负权重/无穷归一化/非法阈值严格拒绝，输出严格JSON
- power只传入时不会被忽略后得到零残差PASS

### C09 真实稳态场及80W积分
目标：BOTH；证据：PUBLIC_MCP_NATIVE。
- 从空模型建铜块，先冻结参考，再采内部3点与两端积分
- 温度误差≤0.1K，功率幅值误差≤1%，符号与法向明确
- 实际Q不来自80.0常量；改k或边界的负控正确失败

### C10 真实正弦瞬态9点
目标：BOTH；证据：PUBLIC_MCP_NATIVE。
- 真实构建/求解，用存储时间映射9个坐标时间点，误差≤0.1K
- 缺目标时间不取最后一列顶替
- 故意错误初值或扩散率时同一验证器应判FAIL

### C11 真实储能和源项平衡
目标：BOTH；证据：PUBLIC_MCP_NATIVE。
- 场积分/差分得到储能与通量，不使用固定等式两边
- 源项/法向/单位/归一化明确；不丢弃power
- 故意漏储能、翻符号、错误源项至少一项产生预期FAIL

### C12 真实三网格研究
目标：BOTH；证据：NATIVE_CONVERGENCE。
- 至少3级实际网格设置/构建/求解记录、DOF/元素/耗时
- 参数控制、坐标时间一致，误差由参考与真实观测计算
- 未细化同一模型、篡改case errors或复制artifact的负控拒绝

### C13 真实三时间精度研究
目标：BOTH；证据：NATIVE_CONVERGENCE。
- 3级实际solver timestep/tolerance验证，固定空间误差基准
- 输出tlist不冒充solver步长；真实时间设置和观测绑定
- 检查最终误差目标，非单调/舍入平台分开说明

### C14 冻结参考不可变与0阈值
目标：SHARED；证据：CONTROL。
- 必需集合/位置/时间/单位/参考/范数/阈值绑定快照hash
- 见到观测后改参考拒绝或产生新基准版本，旧run不可升级
- 0阈值不被or丢弃；负或无穷阈值不能PASS

### C15 状态格与负控报告
目标：SHARED；证据：CONTROL。
- ERROR/BLOCKED/UNSUPPORTED/UNVERIFIED/缺required不升级数值PASS
- negative test PASS与故障模型FAIL分别记录
- 未知状态、嵌套状态与三轴隔离测试；无物理依据保持UNVERIFIED

### C16 报告双文件原子发布
目标：BOTH；证据：PUBLIC_MCP_NATIVE。
- 使用公开validate.report+ArtifactStore，默认不覆盖现有json或md任一文件
- 只允许项目公开产物，私有目录/越界拒绝
- 第二文件失败/登记失败准确记录partial；旧字节保留；报告可回读验证

### C17 原始调用和汇总器负控
目标：SHARED；证据：AUDIT。
- 原始stdio消息在调用时采集；不接受helper记录替代native
- 删证据/改hash/去runtime/用synthetic origin导致审计拒绝
- 报告器不执行模型、不生成观测、不补固定测试数/PASS

### C18 生产授权和未完成状态
目标：BOTH；证据：PUBLIC_MCP_NATIVE。
- 验证通过实际隔离/队列/模型身份，不直接调Worker绕过
- 未授权计算/文件写拒绝；读验证不隐式solve
- 清理失败不成功；已有原作业记录可查询，不重复执行

### C19 保存和新Worker验证
目标：BOTH；证据：PUBLIC_MCP_NATIVE。
- 本版本保存并由新Worker重开同一hash文件，不重算读存储解
- 新ObservationRef绑定新generation，不复用旧模型句柄
- 读数/验证/报告与预期一致，不仅检查tag存在

### C20 软件与受影响回归
目标：SHARED；证据：SOFTWARE。
- 真实pytest命令/退出码/raw output，跳过单列；不填固定通过数
- 旧缺陷测试先失败后通过；不能删除反例或放宽容差
- 保留非破坏求值、revision、jobs、artifact和image受影响回归

### C21 运行源发布源和恢复交付
目标：SHARED；证据：DELIVERY。
- 真实代码/runner/config/reference清单和发布对应；不虚构HEAD
- 无凭据、未授权用户模型上传；远端同步状态准确
- 新目录恢复后安装运行；Git/archive两分支范围明确

### C22 Mac兼容边界
目标：SHARED；证据：SOFTWARE。
- 平台适配不侵入业务逻辑；无Mac实机时记录UNVERIFIED不冒充native
- 保留历史支持范围，不要求Mac/SSH成为当前Windows前置条件
- 停止在W20，不扩大W21

## 记录格式
使用config/acceptance_report.example.json作为字段示意，不把示意值复制成结果。每条记录包含：id、target、test_status、evidence_level、run_id、source_commit、checks、expected、observed、artifacts。
原生记录额外绑定：native_context(runtime_build/worker_instance_id/model_ref)、production_entrypoint、observation_origin；产物角色至少 mcp_capture/runtime_identity/source_manifest/observations/checks。

原始mcp_capture的events含direction和message（真正JSON-RPC对象）；tools/call请求/响应id可匹配，message.params里是实际name与arguments，message.result是实际CallToolResult。至少一个真实执行结果包括structuredContent.execution中的job_id、operation_id、model_ref。无需公开auth token。
observations文件含origin、run_id、target、source_commit、records；每条观测至少producer_operation_id、model_ref、value或artifact_ref及单位/轴等相关元数据。这是从底层结果生产并登记，不由报告模板补出。

```
python tools/check_acceptance.py --definitions ACCEPTANCE_CASES.json --report PATH --evidence-root repository
```
这个工具只审计结构、哈希和关联。无法鉴别执行者伪造全部记录，不能称native认证。真实runner及执行证据仍需审查。
