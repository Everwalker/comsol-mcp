# G3.7 验收定义（尚未执行）

BOTH必须分别有win63和win64结果；SHARED可用共享软件/静态证据。新验收使用本文件ID，旧WD记录保留但不自动继承PASS。

| ID | 目标 | 最低证据 | 判据 |
|---|---|---|---|
| A00 干净路径固定公开源恢复 | SHARED | SOURCE | 固定commit/tree、全部tracked文件hash、恢复回执；修改1字节/错误tree/越界归档均被拒绝；不读取旧目录。 |
| A01 实际引擎/JDK/缓存身份 | BOTH | NATIVE_ENGINE | 分别读取java/javac与引擎完整build，JAR集合哈希；6.3/6.4独立编译；同名JAR变更导致缓存失效。 |
| A02 实际私有ACL与失败拒绝 | SHARED | NATIVE_OS | 以可信SID验证DACL；含意外显式ACE与icacls失败/缺身份负测。权限失败时未发布secret端点。 |
| A03 真实COMSOL隔离门禁 | BOTH | PUBLIC_MCP_NATIVE | 生产verify_owned_server观察实际COMSOL PID/birth/命令/监听和peer，错误端口/进程/非允许监听拒绝；解析器测试不替代。 |
| A04 强停归属与真实退出 | BOTH | PUBLIC_MCP_NATIVE | 缺lease/错job或runtime/过期birth拒绝；显式启用owned stop须真实任务引擎父子进程/监听退出、另一个任务无影响；未支持不得VERIFIED。 |
| A05 终态与RPC/事件一致 | SHARED | CONTROL | 取消赢与完成赢、timeout、late result、CAS路径；SQLite操作和job、原RPC、重取结果、事件权威终态一致。 |
| A06 真实生产队列与活动观测 | BOTH | PUBLIC_MCP_NATIVE | 生产job在Worker已开始未结束区间测50次响应；排队取消真实竞态且零派发；禁止手动插RUNNING。 |
| A07 普通配置冷启动与调用入口 | BOTH | PUBLIC_MCP_NATIVE | 真正stdio initialize/list/call和registry fallback，正常ControlDaemon/Worker，不注入测试service；版本和model_ref回读。 |
| A08 四路径wheel安装 | BOTH | PUBLIC_MCP_NATIVE | 新venv实际wheel安装于A，源码B，科学项目C，cwdD；禁editable/PYTHONPATH，核对import路径；MCP写只到获准C。 |
| A09 W17解轴/复数/统计回归 | BOTH | PUBLIC_MCP_NATIVE | 多个表达式/outer/inner/点；实际时间元数据；复数、非单位测度、mean/std/rms独立期望，错轴/虚部丢失失败。 |
| A10 实际CSV/产物/图像交付 | BOTH | PUBLIC_MCP_NATIVE | product export与artifact.read，CSV往返轴/单位/坐标/复数一致；真ImageContent解码；artifact-only有界且完整文件可取。 |
| A11 host与control中断恢复 | BOTH | PUBLIC_MCP_NATIVE | 在原请求运行时断开host、另案重启control，原Worker/job可协调，幂等不重执行；无证据不标SUCCEEDED。 |
| A12 版本切换与错引用拒绝 | BOTH | PUBLIC_MCP_NATIVE | 6.4→6.3→6.4，跨runtime ModelRef写前拒绝；资源许可并存时不误停另一个引擎；并存限制单列。 |
| A13 同版本保存新Worker重开 | BOTH | PUBLIC_MCP_NATIVE | 冻结MPH哈希，新Worker读取同一文件不先重算，核对内部多点/时间、配置、用户节点保留；错误文件/解负控。 |
| A14 源/运行/发布证据对应 | SHARED | SOURCE | 本轮公开可恢复源、运行前后manifest、dirty说明、发布等价桥；旧197049...不作为必需下载；skip逐项解释。 |
| A15 产物注册与私有边界 | BOTH | PUBLIC_MCP_NATIVE | 合成private sentinel经公开入口拒绝；ADS/junction/路径别名/覆盖和正在被占用文件负测；不读真实秘密。 |
| B01 结构预检规则与覆盖 | BOTH | PUBLIC_MCP_NATIVE | 模型/几何/选区/材料/物理/网格/Study/Solver初始规则，已知好坏模型有回读；不支持规则不自动PASS。 |
| B02 数值指标与单位 | BOTH | PUBLIC_MCP_NATIVE | 实际finite/范围/加权统计/积分/守恒；绑定解和测度，空域/单位错/过期解/NaN失败；副作用明示。 |
| B03 独立稳态解析基准 | BOTH | PUBLIC_MCP_NATIVE | 冻结铜块参数与公式；312.5/325/337.5K、80W按任务阈值核验；错误k/边界/单位负控同一checker拒绝。 |
| B04 独立瞬态解析基准 | BOTH | PUBLIC_MCP_NATIVE | 冻结正弦衰减解和参数，3点×3个非初始真实存储时间；不能echo tlist或使用观测生成期望。 |
| B05 三精度级别收敛 | BOTH | PUBLIC_MCP_NATIVE | 隔离副本至少3个网格/时间精度级别，实际误差与资源记录；不篡改容差，主模型未损坏。 |
| B06 三层状态及缺证据 | SHARED | CONTROL | execution/numerical/physical三轴独立；求解成功但数值失败、数值正确但物理依据缺失均正确标注。 |
| B07 验证权限/副作用/恢复 | BOTH | PUBLIC_MCP_NATIVE | READ不隐式求解；EVALUATE/COMPUTE走生产队列/权限；临时节点cleanup失败/中断可追溯原job。 |
| B08 冻结oracle与负控 | BOTH | PUBLIC_MCP_NATIVE | 同一checker处理正确结果与错模型/解/单位/规则/期望哈希；负控不可只比较两个常量字符串。 |
| B09 报告与原始证据可读 | BOTH | PUBLIC_MCP_NATIVE | JSON/Markdown报告注册与分块回取，规则版本/观测/期望/误差/覆盖/来源完整；缺实验physical=UNVERIFIED。 |
| B10 同代码双版本一致性 | BOTH | PUBLIC_MCP_NATIVE | 两个独立build结果各自对解析基准，不只两版本互比；相同修复源码、独立运行目录；Mac影响明确。 |
| C01 全量软件/安装/再次恢复 | SHARED | SOFTWARE | 真实命令与原始输出、skip列表、pip check、wheel资源、中文/空格新路径恢复；不可重用旧环境。 |
| C02 交付清理与同步 | SHARED | SOURCE | 任务进程退出/外部进程不受影响、secret扫描、最终manifest、普通分支同步或BLOCKED_SYNC，停止W20。 |

## 结论规则
- PASS须有实际命令/请求、source identity、assertions、expected/observed和可验证证据文件；代码写好不是验收。
- PUBLIC_MCP_NATIVE必须是产品公开入口与真实目标COMSOL，不能用direct Worker、mock proof、人工DB状态替代。CONTROL和静态检查按本身等级保留。
- BLOCKED_ENVIRONMENT、BLOCKED_LICENSE、UNSUPPORTED、NOT_RUN、FAIL是独立结果，不计作PASS。原生协同中止与云端视觉接收可以作为明确未支持能力，但不得提升owned termination/Host的状态。
- A04若没有可用合法owned停止，维持强停关闭并报告本阶段限定未完成；不得以测试杀Python sleep证明两版本引擎安全退出。
- 未有独立实验/物理依据时physical_validation_status=UNVERIFIED是正确语义，不能为通过而伪造实验。
- C02代码/证据发布源身份采用逐文件桥，避免让提交自身hash形成自引用循环。

## 证据报告格式
`tools/check_acceptance.py`提供结构检查，不验证引擎是否真的运行。不得把它的结构PASS当成原生PASS。报告格式见`config/acceptance_report.example.json`（只是模板，默认NOT_RUN）。
每个证据文件相对evidence_root存储并附sha256；原始失败/历史不覆盖。敏感运行时资料另存本地，公开redaction manifest列hash与原因。
