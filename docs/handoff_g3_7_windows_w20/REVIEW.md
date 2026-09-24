# G3.6 审查 → G3.7 Windows 生产链收口与 W20

审查固定提交：`be5bfc8847d98e0aa3972050661a3beb8ee4cfc5`，tree `dbc871285c9e5465a2e2d9740e908289284304cb`，2026-09-24。
范围：最新进度、Windows验收台账与驱动、运行隔离/文件权限、作业状态/强停、Worker缓存。不是全仓库逐行审计；本审查没有运行 Windows、COMSOL 或全仓测试。
本包新增诊断只验证隔离函数控制流，不替代生产/物理测试。实际执行记录见 PACKAGE_QA.json 和 review/。

## 结论
保留已有双版本引擎适配和 Java 建模/求解成果。当前“CORE_VERIFIED_SCOPED”对生产链的范围过宽，先定向补修与重验，再进入 W20。不是推倒重做，也不允许直接以30/30作为全部门槛已通过。

## 已有进展（仓库报告，非本次重跑）
主台账报告30项：15 CONTROL_PASS、14 NATIVE_PASS_SCOPED、1 SOFTWARE_PASS；Windows6.3.0.290/6.4.0.293，软件1832通过41跳过。真实direct Worker链有铜块建模、三个内部温度点312.5/325/337.5 K、保存、新Worker重开325 K、右边界改380 K后中点340 K及版本切换。这些应保留。
引用：PROGRESS.md；docs/handoff_g3_6_windows/PROGRESS.md；evidence/windows_dual_version/g3_6_acceptance_20260924T014214Z/acceptance_result.json；tools/run_g3_6_acceptance.py。

## 1. 不能把测试标题当覆盖证明
- WD03只以样例netstat与Python socket检查解析/观测，不是COMSOL的生产隔离证明。
- WD04只统计registry和G3目录；没有启动普通MCP。
- WD05直接写四路径True，即使无wheel也返回CONTROL_PASS；没有四目录真实安装运行。
- WD06的DACL函数返回None，台账private_directory_dacl_configured=null，但仍PASS。
- WD07–09在Java builder后写预设数据；不等于变量/函数、网格质量和选区变化均回读通过。
- WD11固定tlist并按len(values[0])统计，不能证明真实时间轴。
- WD12只做一个实数平均温度范围测试，不支持“多维复场与统计全部验证”的说法。
- WD13直接plot_render检查PNG，不等于MCP ImageContent宿主接收。
- WD14以csv.writer人工写3行，不调用真实导出与读回。
- WD19启动另一个Worker线程，但查询无Worker绑定的ControlDaemon里手工插入的RUNNING行；求解异常被忽略，无法证明同一个生产job正在计算。
- WD20/21主要数据库模拟；WD22检查写死的能力；WD23杀Python sleep不能作为双COMSOL无误杀验证。
以上不是否认数值运行，而是要求缩小旧声明，并新增生产证据。对应函数位于 tools/run_g3_6_acceptance.py，查找 run_wd03/04/05/06/20/21/22/23 及 execute_live_engine_suite。

## 2. 作业终态存在跨层不一致（F02）
_operation_store.finish 已能拒绝晚到终态覆盖；但 _control_daemon._finish 无论数据库是否接受，都继续返回传入result、发传入status事件。可出现数据库CANCELLED、RPCsuccess=true、事件SUCCEEDED。
transition_status也没有统一终态不可逆规则。必须让所有入口共享原子状态仲裁，返回权威结果。不能只再加一个测试布尔标记。

## 3. Windows权限与生命周期（F03–F05）
_security_os.set_private_directory_permissions 忽略icacls退出码和异常；无USERNAME时直接返回；没有实际ACL回读，也不能移除其它主体的全部显式权限。必须基于可信SID/安全策略验证最终DACL，失败阻止secret端点发布。
_scoped_force_stop 中后端lease仍是可选，时间校验可省略；is_process_in_job(pid)实际查的是当前进程。terminate_process_tree主要确认父PID消失，未完整确认子进程与监听端点，Windows fallback句柄签名也需审查。
_g2_isolation新增Windows解析器是进展，但进程CIM查询失败返回伪command=comsol；未知不得补成合法信息。最终生产验证仍有精确IPv4条件；须真实运行验证，不默认IPv6或所有Windows配置支持。
本次没有访问真实秘密或杀真实进程，不宣称已发生安全事故。

## 4. 版本和证据身份（F06–F07）
编译缓存包含Worker源码/manifest/JDK信息等，但实际JAR内容未绑定；COMSOL版本还会从路径数字推测。替换同名JAR可能沿用缓存，应纳入实际build和依赖字节指纹。
当前验收记录active_commit=197049c67f4a68c86ffbb1a364bfb91902ed0182；公开pin为be5bfc...。不同SHA不自动等于代码不同，但必须有逐文件等价桥或以公开源重新运行。不得依赖找不到的私有提交。
Mac兼容已保留代码不能写成新Mac原生认证；1832/41等数字也应绑定具体命令、平台与skip原因。

## 5. 下一步W20（F08）
在生产与安全门槛满足后，按原始W20推进：结构预检、实际指标、独立基准与守恒/收敛、执行/数值/物理三个状态，以及带来源报告。
不做W21扫描优化，不从头替换现有MCP/Worker/ArtifactStore。具体任务和验收在NEXT_GOAL.md与ACCEPTANCE.md。
