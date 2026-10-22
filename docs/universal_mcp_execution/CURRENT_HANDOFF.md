# 当前续跑交接 — 2026-10-22

目标 **IN_PROGRESS**。唯一续跑指针 [state/RESUME.json](state/RESUME.json)，冻结范围 [MASTER_GOAL](MASTER_GOAL.md)/[RELEASE_DOD](RELEASE_DOD.md)。三案例科学终点不作主线；未完成专项状态、历史失败和UNKNOWN原件保留。最终独立Reviewer及两类未预设任务尚未启动。

恢复已核验固定commit `cd47281777eae6f4ea0d40c5c754dca9982eda86`、tree `94e6523bfffe21ace621045710335bea5512c3c1`，21,682原跟踪文件及候选3,978映射/1,105对象。规范生产树为本repository；candidate_sources只作恢复证据，不批量覆盖。Git分支universal-core，普通main发布获既有授权，禁止force。已同步M0/M1 `4759c097`、M4 `47d05a8e`、Windows软件准备/使用说明 `4f236d14`；下一阶段同步状态见RESUME.git。

已审查的有限软件阶段：M1 303项测试、112工具源外协议smoke，覆盖审定公共签名/发现/类型/null/6.3与6.4策略；GENERAL_COVERAGE一次映射273动作/427子断言并只读检查通过。M3 native-default不再按四个表达式特判；原347通过/1失败/1明确排除回归不改写，排除项后由仓库内同SHA旧源码恢复相对路径单项通过。剩余历史编译凭据缺失只影响该历史复现测试。

M4三架构离线构件/哈希锁/SBOM/许可完成限定审查，131项发布测试通过，Mac/Windows正常软件安装及112工具启动已实际执行。Intel同版本cryptography派生wheel已验证静态架构与依赖功能，未冒称Intel COMSOL原生支持。详见evidence/M4_RELEASE_TOOLING.json、evidence/WINDOWS_PREP.json；这些构件不是后续所有核心修改的最终候选。

新交付生命周期证据evidence/M4_DELIVERY_LIFECYCLE.json经Main核验118份原始记录和36个外部文件：真实0.1.10旧→新→旧代码回退、持久UNKNOWN/幂等历史、生产全状态备份恢复、非空标记venv卸载通过。app旧C733、新1706（全SHA见receipt/RESUME）；只属于MacARM软件数据范围，非版本迁移、非原生/科学/完整T056。Framework Python卸载拒绝及额外sandbox的ps拒绝均保留；成功卸载使用私有standalone CPython和原有普通CLI，未放宽任何保护。

M2a原run在workpack/_state/universal-m2a-20261022T115828Z-43b2d2。首次Java执行漏传参数名，源job `a1d8c9ed-2d03-407b-8d0c-65b7b0cc8030`保持UNKNOWN；公开session.recover核验后请求静止，未重放。公开disconnect(retire_worker=true)已退役Worker，旧ModelRefs失效；随后stop拒绝SESSION_BINDING_BUSY，原自有Server PID82522/birth start_epoch_ms:1792670973071仍保留隔离。PID本身不是停止/接管授权，不重试旧stop，不触碰旧科学活动。

Main确认真实源码差距：stop直接阻塞所有非终态job，没有复用disconnect已使用的强session.recover静止证明检查。M2b需复用既有强验证，同时保留历史UNKNOWN/结果和所有权/其他连接/忙碌门禁；不要新造审批/证明框架。另有项目Java默认全局ModelUtil模型管理边界、实际javac禁注解处理、UNKNOWN按真实冲突Server资源隔离三项待修。

model_inspect(refresh=true)项目写权限映射已补，Main对精确冻结模块17项软件测试通过。新修补包app `1706ea4085e72d999de0f0dffe7d56e0af34bb76e275b30d08e6823668c1ff92`，普通42-wheels哈希锁离线安装在workpack/_state/universal-m2a-repaired-install-20261022-d898e40069。Main核对172包文件与冻结源码/wheel完全一致，无editable/借用依赖路径；原生新daemon来源/求解/结果图像/保存仍由同一主执行Luna继续。该候选不含并行Windows SID改动，不是最终候选。

Mac ARM6.4.0.293及Windows6.3.0.290/6.4.0.293与JDK11可达；Windows已有standalone Desktop保持原样。Windows SID与两条SCM lookup已由Main限定接收：R3受影响133软件PASS，173包文件与wheel/冻结源码一致，三种标准身份的实际SCM只读查回、缺profile拒绝及资源释放观察通过。evidence/WINDOWS_SID_FIX.json完整保留两轮失败与绑定。共享host-profile SID同根修补随后完成：161聚焦PASS，真实Windows6正向/16反向intent解析仍NOT_VERIFIED，见evidence/WINDOWS_SHARED_SID.json。原59项共享资源回归48PASS/11FAIL与旧SID checker对照相同；后续已定位外层内存观测EPERM及测试Worker缺身份RPC/严格信封，在真实只读权限及正确文件系统上，原断言完整保留、新增2身份反例后61PASS。Main核对38收据、50原测试函数AST及149生产文件前后相同；见evidence/RESOURCE_DISPATCH_REGRESSION.json，仅软件范围，原失败不改写。网络设计已选固定监听端口及显式有界源端口范围，保持四层默认BLOCK；待实现、验证并重绑定新计划。另源码明确原生admission/one-use issuer尚无真实producer，当前始终拒绝；须按冻结T037补真实OS证据，不能删门禁。host plan v2仍非apply-ready，尚不向用户请求执行批准。新服务、AppContainer、ACL/WFP尚未授权或应用；不得用elevated SSH token冒充主机变更授权。10GiB只是早期提案，不是新增门槛；内存已做独立API交叉观察，实际启动按有限任务预算判定。

Windows新子进程只读证据已由Main限定接收，见evidence/WINDOWS_CHILD_EVIDENCE.json：87软件测试、12真实负控、7类SDK观测，80条命名hash覆盖73独立文件核验；上传/当前/原生自报源码一致。普通高完整性、非AppContainer探针从未Resume，原CreateProcess HANDLE确认退出并各释放一次，无未决资源。现有prebirth/Resume门禁不变，尚未集成T037原owner/issuer。网络范围/原始句柄getter仍在返工验收；新WFP事件枚举只读模块并行实现，二者均不等同有效隔离。

续跑先读RESUME与最新原始pending-refs/job，核实实际进程和所有权；新独立资源可继续，禁止重放旧UNKNOWN、伪造Popen或修改活daemon代码。当前原生目标/跳过范围仍严格按config/TARGETS.json。完成所有required、真实跨平台适用验证及最终独立Reviewer批准前，不写整体完成。

最新主线状态：修补候选1706的真实6.4.0.293 study.run已成功（job b332f63b-9359-4502-b1ac-a2c452827810）；result.evaluate因预算SolutionAxes未证在getData前拒绝，Eval临时节点已清理、target rev3 dirty。managed inspect缺refresh wire，legacy refresh未携带ModelRef调用空默认连接，产生未绑定UNKNOWN1f87ec01-5469-452d-8bfa-ead562faf2e5；job.reconcile拒绝，最终sentinel读被全局围栏拒绝。所有失败/原请求不改写。Primary现连续修上述两项及既有Core边界批次，软件通过后冻新源码/离线安装/新拥有资源重跑；不修活daemon、不重标1706。原Owner可在零活动并满足既有门禁时正常断开/退役/stop一次，否则保留隔离；不凭PID清理。详细现态以RESUME和原始pending-refs为准。
