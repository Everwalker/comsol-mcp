# G3.5 源码审查与 Windows 6.3/6.4 接续建议

审查日期：2026-09-23。源锁：`3835ab858a4e7a54fd3b1fb901dca04c9ee9ac19`（tree `634389371381c179406e06c421a6e43a1f632322`）。本报告针对已公开快照，不包含用户机器未推送的修改。

## 结论

开始 Windows 双版本适配是合理的；不再以继续 Mac-only 为前置。下一阶段应为 **G3.6 Windows 6.3/6.4 原生适配 + W19 定向修补**，暂不扩展 W20 功能。已有 W17/W18 和控制基础保留，不能因为发现局部缺口就重写全部系统。

本次做了连接器源码/测试/台账审查与官方资料核对；未下载完整仓库、未运行完整 pytest、未操作用户 Windows/Mac/COMSOL。工作包工具测试另见 PACKAGE_QA.json，不能当作仓库或引擎验收。未声称逐行审完全部仓库。

## 当前进度的证据等级

最新台账 `evidence/g3_5_acceptance.json` 的运行 ID 是 `g3_5_acceptance_20260923T142306Z`，记录源 `b34990cee046ca57105d8bcc1d9ac5d18aef34d8`，Mac Apple Silicon / COMSOL 6.4 build293，状态 `G3_5_MAC_W19_VERIFIED_SCOPED`。它将 G01/G09 标 STATIC、J01/J02/J04–J09 标 CONTROL、J03 标 PROCESS_OWNERSHIP_CONTROL，以及若干图形/求解案例标原生。这个分层方向正确。

根 PROGRESS.md 仍引用更早的 084906Z 运行，并将一些更强的语义写作“完成”。应使用最新 run 的实际范围，而不是以旧摘要或测试总数推断目标 Windows 已支持。

新增公开 job_list/job_wait/job_cancel 接口确实存在；队列取消有事务实现、代码包含 CANCELLED-before-dispatch 检查；Windows 已有进程查询和 classpath 支持。但“某函数/分支已经存在”与“普通部署下两版本稳定运行”仍是两件事。

Windows 旧记录 `evidence/windows_control_lifecycle/20260919/software_evidence.json` 自己写的是 `MAC_UNIT_PASS_WINDOWS_INTEGRATION_UNVERIFIED`。它有价值，应复用其已识别的 Windows Job Object/daemon 生存期问题，但不是当前 Windows COMSOL 原生认证。

## 需修复或验证的发现

### D01 / P0：真正阻挡 Windows 的 macOS-only 隔离证明

`comsol_mcp/_g2_isolation.py` 开头的 `_require_mac_isolation_adapter()` 对非 darwin 抛 `UNSUPPORTED_PLATFORM`；探针使用 `/bin/ps`、`/usr/sbin/lsof`；部分 receipt 验证写死 `/Applications/COMSOL64/.../server.xml` 与单一内容哈希。

这不是路径小修改。广义模型写入要有 Windows-specific owned-runtime 证明，并针对 6.3/6.4 实际部署分别观测。删除平台拒绝或跳过隔离不是适配。

### D02 / P0：UNKNOWN/RECONCILING 收到取消后会被重新宣称 RUNNING

`_control_daemon.py::_cancel_job` 在非终态、非 QUEUED 且非 force-stop 情况下直接 `update_job(..., "RUNNING")`。现有 update_job 保护只覆盖终态->非终态，不阻止 UNKNOWN->RUNNING。

静态控制流结论：取消意图会覆盖未知的观测状态，而没有实际引擎证据。本次没有执行该仓库的复现，Goal 要求先加针对生产状态机的负测再改实现。

### D03 / P0：终态保护和结果记录不统一

`_operation_store.py::finish()` 无条件更新 operations/jobs 状态；`update_job()` 只拦截终态回非终态，允许终态间重写。因此晚到 finish 可以覆盖已确定的取消结果。`_scoped_force_stop` 更新 job 状态但没有通过相同结果收口过程建立一致的原 operation result。

这属于确定的实现路径缺口；需用真正队列竞态、late callback、cancel/finish/reconcile 组合测试，不能只测试 store.cancel_queued 后一次 update_job(RUNNING)。

### D04 / P0：force-stop 仍是测试桩级接线，缺少实际停止确认

`_scoped_force_stop` 依赖 service.is_shared/server_pid 等动态属性；标准 ExecutionService 构造器未定义这套所有权。正向 J03 通过临时 `ManagedService` 和 Python sleep 进程进入该分支。

分支里 `os.kill(managed_pid,9)` 返回后立即写 `engine_stopped=True`。缺少等待真实引擎及必要子进程退出、版本/实例归属绑定、对另一 runtime 任务的排除和完整恢复。PID 创建时间检查是可选而非可靠必须项。

不要将这解释为已经任意杀了用户进程；目前是适配前应补的可靠性/授权缺口。请求中的 authorized=true 不是 OS 所有权证据；必须对应后端持久的受管实例身份。

### D05 / P1：Worker 编译缓存没有完整版本身份

`_java_worker.py` 已正确支持 Windows exe 和分号 classpath，并验证官方 manifest 的完整来源。值得保留。

但 classes 缓存目录仅按 Worker Java source_hash 建立，未包含 COMSOL build/JAR指纹/JDK架构和编译参数。javac 调用未显式指定 `-encoding UTF-8`。在同机两版本环境应独立编译并按完整环境身份缓存，不能靠“文件夹看起来不同”隐含保证。

### D06 / P1：已有 Windows 生命周期基础尚无本轮实证

Windows `process_identity` 使用 OpenProcess/GetProcessTimes，比不恰当的信号探活好。旧源码已有 detached/breakaway flags 设计与失败提示，不能重复写第二套。

需补：目标 Host Job Object 对持久 daemon 的实际影响、breakaway 拒绝后的外部 broker 路线、SID/DACL、端口与实例观测、junction/ADS/UNC、锁占用和原子发布。chmod 不是 DACL 证明，macOS PID/lsof 记录不能转成 Windows receipt。

### D07 / P1：部分 W19 结论仍超过测试覆盖

`tests/run_g3_5_acceptance.py` 的 J02 人工往 store 插入 QUEUED 行；J03 使用 Python sleep 和测试 ManagedService；J05 重开 SQLite 并建立新的内存 ledger；J06 对没有实际求解负载的 daemon 测 session_health。

它们属于有用的 CONTROL/PROCESS tests，不等于真实 COMSOL 求解时的队列取消、Host 断连/控制进程重启恢复或负载下延迟。Windows 验收必须补生产入口与实际负载，不只是复制 22/22 的标签。

### D08 / P1：不要把本适配器未支持说成全部 COMSOL API 不可能

当前取消消息固定为“COMSOL 6.4 API 不支持”。应改成针对当前 runtime/backend 的 capability。官方 Windows batch 文档确有停止/取消选项，但不应把它直接用于当前 server-side solver；能用哪条路线需要实测。6.3 和6.4 的结论分开。

### D09 / P1：版本矩阵、源对应和旧交付依赖

新 Windows 测试必须先确认本机具体 build，而不沿用 Mac 的6.4.0.293；两个版本不能混用相同 ModelRef、缓存、prefs和数据库。当前 G3.5台账已有源码manifest，应继续使用并核对发布源，而不是固定数量断言。

完整新目录恢复仅保证公开提交内容可得。旧未提交文档索引、模型、科学数据、token、私有历史不可恢复；用户清理后，测试夹具需要从已提交构建器重新生成并作为新证据记录。

## 为什么不是直接 W20

这次用户的明确目标是 Windows 下两个已安装版本都可用。当前主要风险集中在执行/平台边界，而不是缺少下一批领域工具。先验证同源码在 Windows 6.4/6.3 都能完成已有 core链，再考虑 W20 结构/数值验证产品层，能够避免功能越写越多、部署仍不可用。

## 官方外部核对（与仓库事实分开）

- COMSOL 6.3/6.4 官方要求中，外部 client-server Java API 与产品内置 JVM 要区分；以 JDK11 作两版本共同认证起点，不替换产品自带 Java。
- Windows 启动器位于安装内 `bin/win64`，Server 是 `comsolmphserver`。`-port 0`、`-portfile`、`-prefsdir/-tmpdir/-recoverydir`、`-graphics` 等应按安装对应版本帮助验证。不要复用 Mac 命令形状。
- Job Objects 会影响子进程归属和生命周期；持久进程不能假定随 stdio 关闭自然留存。复用已有 detach/breakaway设计并实测，不修改 Host SDK的保护策略。
- COMSOL `-cancel/-stop` 等 batch选项存在，但其语义不能等同于服务器任意 API 正在计算的研究任务。

具体官方 URL 与仓库路径在 SOURCE_MAP.json。报告中的修复策略和验收设计是本次建议，不是仓库已经完成的功能。
