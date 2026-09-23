# G3.6 Windows双版本验收

这30项是**待执行定义**。两版本目标为BOTH的每项必须分别记录6.3和6.4结果；SHARED也需要明确运行了哪些版本。现有软件测试通过不能自动填入NATIVE_PASS。原生取消、云端视觉、特殊许可证功能按实际能力拆分子项，不得把实现缺陷降级为可忽略限制。

## 数值标准

在第一次求解前冻结BenchmarkSpec、解析预期、容差、采样点和单位，保存哈希。稳态建议常截面块体两端固定温度、其余绝热，无体热源；期望 T(x)=T0+(T1-T0)x/L，热流 -k(T1-T0)/L。容差必须结合网格误差预注册；不在看到失败后放宽。瞬态可用1D模态衰减或其他可独立验证的小解析解，实际k/rho/Cp与参考一致。复杂数据采用带外参数、时间和空间可区分的场，防止轴错位碰巧通过。

## 状态口径

`CONTROL_PASS`只证明控制测试；`NATIVE_PASS_SCOPED`才代表指定build实机范围。阻塞时保留`BLOCKED_ENVIRONMENT/BLOCKED_LICENSE`，软件缺陷记`FAIL_IMPLEMENTATION`。每项证据含source hash、run_id、实际入口、输入输出、独立预期和结果；失败及原始记录不覆盖。WD25不属于核心必过范围，但必须有明确实际结果或NOT_RUN理由，绝不伪造反向兼容。

| ID | 测试 | 目标 | 证据级别 |
|---|---|---|---|
| WD00 | 固定源恢复与完整性 | SHARED | STATIC |
| WD01 | Windows与双安装盘点 | BOTH | NATIVE_ENV |
| WD02 | JDK与Worker分别编译 | BOTH | NATIVE_JAVA |
| WD03 | 合法安全启动与隔离 | BOTH | NATIVE_OS_ENGINE |
| WD04 | MCP冷启动与三入口 | BOTH | PUBLIC_MCP |
| WD05 | 四路径源外安装 | BOTH | INSTALL_PUBLIC_MCP |
| WD06 | Windows私有文件边界 | BOTH | SECURITY_OS |
| WD07 | 参数变量函数与单位 | BOTH | NATIVE_NUMERICAL |
| WD08 | 几何WorkPlane与稳定选区 | BOTH | NATIVE_ENGINE |
| WD09 | 材料物理与网格 | BOTH | NATIVE_ENGINE |
| WD10 | 从空模型稳态解析基准 | BOTH | NATIVE_NUMERICAL |
| WD11 | 瞬态与真实存储时间 | BOTH | NATIVE_NUMERICAL |
| WD12 | W17多维复场与统计 | BOTH | NATIVE_NUMERICAL |
| WD13 | W18渲染与ImageContent | BOTH | PUBLIC_MCP_NATIVE |
| WD14 | 数据导出与受限响应 | BOTH | PUBLIC_MCP |
| WD15 | 同版本保存及新Worker重开 | BOTH | NATIVE_ENGINE |
| WD16 | 局部修改与用户节点保留 | BOTH | NATIVE_ENGINE |
| WD17 | 真实排队取消竞态 | BOTH | PUBLIC_MCP_NATIVE_CONTROL |
| WD18 | UNKNOWN取消与晚到完成 | BOTH | CONTROL_AND_PUBLIC_MCP |
| WD19 | 正在计算时状态响应 | BOTH | PUBLIC_MCP_NATIVE_CONTROL |
| WD20 | Host断连与原作业恢复 | BOTH | PUBLIC_MCP_NATIVE_CONTROL |
| WD21 | 控制进程重启协调 | BOTH | NATIVE_OS_ENGINE |
| WD22 | 原生中止能力与owned终止 | BOTH | NATIVE_OS_ENGINE |
| WD23 | 跨运行时拒绝与无误杀 | SHARED | NATIVE_DUAL |
| WD24 | 双版本切换回归 | SHARED | NATIVE_DUAL |
| WD25 | 跨版本文件规则 | SHARED | NATIVE_DUAL |
| WD26 | Windows Host Job与权限降级 | BOTH | NATIVE_OS_HOST |
| WD27 | 同源码全回归与Mac影响 | SHARED | SOFTWARE_AND_TARGET |
| WD28 | 新目录恢复与可交接交付 | SHARED | RECOVERY_INSTALL |
| WD29 | 退出清理与双版本报告 | SHARED | NATIVE_OS_EVIDENCE |

## 逐项完成判据

### WD00 固定源恢复与完整性

从空目录恢复PIN全部文件；核对tree与逐文件哈希；单文件篡改负控必须失败。

产物：RESTORE_RECEIPT、source inventory、tamper negative。初始状态：NOT_RUN。

### WD01 Windows与双安装盘点

实际OS/build/架构、解释器位数、两个安装路径及引擎build可对应；文件夹名不充当版本证据。

产物：环境JSON、CLI及引擎版本回读。初始状态：NOT_RUN。

### WD02 JDK与Worker分别编译

按各自官方client manifest编译运行，缓存key含COMSOL/JAR/JDK/arch/源码/参数；故意交换classpath或旧缓存必须拒绝/重编译。

产物：编译输出、classpath/JAR哈希、cache receipt。初始状态：NOT_RUN。

### WD03 合法安全启动与隔离

新建任务Server验证PID创建身份、映像/端口/用户/私有配置；Windows证明成功才能广义写入；错PID/过期回执/未知listener拒绝。

产物：私有值脱敏后的ownership与socket/ACL回执。初始状态：NOT_RUN。

### WD04 MCP冷启动与三入口

从正常安装和配置启动，不注入Fake Service；工具目录、规范名/alias/operation fallback同等遵循身份和权限。

产物：原始stdio请求响应、安装来源。初始状态：NOT_RUN。

### WD05 四路径源外安装

A安装/B源码/C模型/Dcwd分离，实际运行参数修改/保存/绘图只写C；site-packages/private目录写入拒绝。

产物：wheel来源、路径清单、实际产物。初始状态：NOT_RUN。

### WD06 Windows私有文件边界

DACL、注册产物、junction/reparse/ADS、私有sentinel与占用文件测试；拒绝越权且旧文件字节不变。

产物：合成sentinel请求、ACL检查、哈希。初始状态：NOT_RUN。

### WD07 参数变量函数与单位

全局/组件变量依赖可求值；带单位参数和2D插值函数正确，类型/作用域/单位错误有准确拒绝。

产物：MCP请求、回读、独立预期值。初始状态：NOT_RUN。

### WD08 几何WorkPlane与稳定选区

工作平面内嵌套修改/阵列和命名选区重建；兄弟节点保留、实体位置/测度有验证。

产物：节点快照、选区回读、几何测度。初始状态：NOT_RUN。

### WD09 材料物理与网格

材料属性/张量、初边值、物理选择和局部网格真实建立；空选区/缺材料/错误网格负控拒绝。

产物：源配置与引擎回读、mesh统计。初始状态：NOT_RUN。

### WD10 从空模型稳态解析基准

固定非单位尺寸导热基准，内部至少3点及热流符合预注册解析解，材料/边界失败不可SKIP。

产物：完整建模请求、求解日志、误差表。初始状态：NOT_RUN。

### WD11 瞬态与真实存储时间

由同一BenchmarkSpec生成材料和参考；至少3空间点×3非初始时间，解轴来自引擎存储解，不从请求tlist假定。

产物：参数回读、stored axes、数值误差。初始状态：NOT_RUN。

### WD12 W17多维复场与统计

多表达式×outer/inner×point正确；非单位测度平均/std/rms、2D轴对称样例、复数实虚部与维数保持。

产物：轴元数据、复数恒等式、独立积分预期。初始状态：NOT_RUN。

### WD13 W18渲染与ImageContent

几何/1D/2D切面/3D表面真实PNG，对应正确数据集/solution/时刻；坏图、旧target复用、清理失败负测不能成功。

产物：PNG、MCP ImageContent字节匹配、provenance。初始状态：NOT_RUN。

### WD14 数据导出与受限响应

CSV往返四轴/单位/复数与JSON一致；artifact-only无全量副本；分页完整性和旧文件保护正确。

产物：产物/分页/预算和负测。初始状态：NOT_RUN。

### WD15 同版本保存及新Worker重开

记录真实MPH哈希；新Worker打开同文件不先求解，读内部场值/时间轴/手动节点；错SHA/清空解由同一检查器拒绝。

产物：保存回执、worker代际、原字段比较/负控。初始状态：NOT_RUN。

### WD16 局部修改与用户节点保留

已完成求解的可重建模型仅修改目标参数/几何，保留非目标Study/Results/manual solver；再次求解比较。

产物：前后树/数值、目标范围与保存证据。初始状态：NOT_RUN。

### WD17 真实排队取消竞态

真实任务A占用引擎，B经公开入口排队；取消/启动竞态多次注入，取消获胜的B零Worker派发，终态与原操作结果一致。

产物：时间线、request_id、状态表及Worker请求计数。初始状态：NOT_RUN。

### WD18 UNKNOWN取消与晚到完成

UNKNOWN/RECONCILING收到取消不得无证据变RUNNING；晚到finish/异常不能覆盖确定终态；原job_result一致。

产物：状态迁移/数据库/信封断言。初始状态：NOT_RUN。

### WD19 正在计算时状态响应

足够长但预算受控的真实求解期间测50次状态/日志；预设p95目标1秒并报告实际值、负载，不能空daemon计时替代。

产物：原始延迟样本、实际运行区间。初始状态：NOT_RUN。

### WD20 Host断连与原作业恢复

关闭stdio/Host连接后独立daemon继续任务；重连原job取结果，相同key不重算；变更body同key冲突。

产物：Host退出时间、process身份、dispatch计数。初始状态：NOT_RUN。

### WD21 控制进程重启协调

真正重启控制进程而非只重新打开SQLite；关联仍在运行Worker/请求，保持未知边界，不重发未确定变更。

产物：进程前后身份、原job状态与回读。初始状态：NOT_RUN。

### WD22 原生中止能力与owned终止

各runtime分别验证取消路线；native不支持须如实声明。明确授权的任务实例进程级停止需确认真实退出并恢复，不能仅杀sleep或launcher。

产物：capability、授权lease、wait/exit/socket和checkpoint回执。初始状态：NOT_RUN。

### WD23 跨运行时拒绝与无误杀

两个版本的错ModelRef/job/receipt/cache必须拒绝；终止A不触碰B/用户Desktop；资源不足时并存项单列BLOCKED。

产物：隔离矩阵、目标与非目标身份、负测。初始状态：NOT_RUN。

### WD24 双版本切换回归

按6.4->6.3->6.4切换，同一冻结源码，分别读本版本存储模型和出图；DB/prefs/缓存/解无串用。

产物：三次runtime receipts及结果比对。初始状态：NOT_RUN。

### WD25 跨版本文件规则

6.3产生的模型被6.4只读加载另存可测；反向不默认为兼容，需要时按构建定义在6.3重放；原始文件哈希不变。

产物：原/另存哈希、实际打开结果、限制说明。初始状态：NOT_RUN。

### WD26 Windows Host Job与权限降级

真实Host Job下验证daemon生命周期；breakaway被拒绝时不降低保护，外部预启动模式成功或准确BLOCKED。

产物：Job/创建flags/独立daemon和退出回执。初始状态：NOT_RUN。

### WD27 同源码全回归与Mac影响

最终共享代码修改后两版本受影响回归；完整软件测试和安装资源检查；Mac无实机则保持新源回归UNVERIFIED。

产物：源码前后manifest、pytest原始输出、影响矩阵。初始状态：NOT_RUN。

### WD28 新目录恢复与可交接交付

从新路径恢复修复源/安装/跑小链路；不依赖旧绝对目录、token或私有分支，公开产物清单无悬空引用。

产物：交付SHA、恢复回执、冷启动结果。初始状态：NOT_RUN。

### WD29 退出清理与双版本报告

确认本任务进程/句柄/端口按模式正确释放，未清用户模型和服务；分别给6.3/6.4核心状态及限制，不以一者代替另一者。

产物：cleanup inventory、dual-version capability、状态台账。初始状态：NOT_RUN。

## 禁止的验收替代

不能用源码文件存在代替引擎执行；不能用fixture常量代替真实存储解；不能用sleep进程代替COMSOL终止；不能仅校验PNG头代替图像解码和数值绑定；不能把假Service注入路径称为正常MCP冷启动；不能只在6.4通过就给6.3打勾；不能只比较测试数量而不比较本轮代码哈希。
