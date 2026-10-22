# 当前续跑交接 — 2026-10-23

目标 **IN_PROGRESS**，没有U01–U12整组完成或最终原生验收。唯一续跑指针为 [state/RESUME.json](state/RESUME.json)，合同为 [MASTER_GOAL](MASTER_GOAL.md)/[RELEASE_DOD](RELEASE_DOD.md)。主线是通用MCP，历史三案例与失败证据保留；阶段发布不是停止点。候选冻结后须由fresh强Reviewer自行选择两类未预设任务并循环整改，Main不预设其题目。

规范生产树为本repository，分支universal-core。恢复基线cd472817/tree94e6523b，21,682原跟踪文件、3,978映射/1,105对象已核验。最近发布035b3b9622fecf888d10a9eb890a9d460e521660（Interp/void源软件），前序4c1750b5（原生部分进度/新UNKNOWN）、96c6c9f8（SCM接线软件）、0926fc5e（离线主机包工具）、275c1b84（ControlClient）、7ae6140e（原child-owner）、b90ef486（固定监听）、5aed6a04（Core/W17）均普通发布并独立fetch一致。AGENTS148–152持续授权非force origin/main同步；Main独占Git和三份规范状态，按精确白名单stage，不含其他Agent WIP。旧ca7/c45暂存约束已结束。

已验收有限基线：M0恢复36工具测试；M1 303软件/24公共签名/112正常安装工具协议，GENERAL_COVERAGE一次冻结273动作/427原子断言（414核心/13案例）。M3旧347PASS/1FAIL/1排除保留；精确旧源码仓库副本恢复单项PASS，W23另一历史delta仍缺。M4三架构构件与131发布软件、Mac/Windows安装112工具已限定验收；Intel同版本cryptography派生wheel披露，Intel COMSOL硬件未验证。生命周期核对118原始记录/36文件，0.1.10同版本代码切换回退/全状态备份恢复/standalone CPython非空venv卸载通过；Framework卸载UNKNOWN保留。Windows/Intel完整生命周期及原生T056未据此通过。

Core5aed有限源软件：完整managed refresh wire/ModelRef/权限、按强静止证据处理stop、按实际owned Server隔离UNKNOWN、javac语义ModelUtil边界及-proc:none、通用attached Stationary numeric-count witness。r3公开Eval漏接修补经14adapter/261相关PASS。W17四fixture问题经精确d56基线复现后兼容修补，36PASS；相关完整366PASS/1FAIL，唯一T044历史输入缺失为NOT_RUN。旧357PASS/5FAIL、426PASS/1明确skip保留，不称全suite通过。count-only不虚构outer/inner轴或FieldArray，严格metric/field门不放宽。

当前冻结原生r3是独立旧安装，**不是工作树新源码**。app6b4a40ea76443bb6e6a28a73f47548db2c656df4dfd88b272ae4737d40260e23，source manifest f49cfdef09fb6f9c1d992c2985880104c24b384a12fd5510fcb1ec47b42b1372；173包文件源码/wheel/安装及112工具已核对。目录workpack/_state/universal-m2a-core-repair-20261023-r3/native/authorized-runtime；project df096475-c434-4816-9b5a-86cfa9248c3f，session session-24f852fcaa464a8db9e76b102254db91。原daemon10810/birth1792692096287，Server18219/birth1792692929019/127.0.0.1:54381，server-instance8fd0d9db…；worker3481cda9…/epoch2，最后实际恢复观测PID20044/birth1792693089469。五权限来自该新私有进程有效配置，未改全局默认。本r3 run的一台Server已创建，不在此runtime另起、livepatch、PID接管或重放。

Main独立核对r3公开编译/执行/真实COMSOL6.4.0.293 study.run，以及默认/显式K两条136点300→400数组、at_points中点349.9999999999981K；同解PNG1200×800/33206字节/hash947f6d07…已目检（plot自身unit空，未额外认证）。MPH含解582939字节/hashcccc806c…保存，sentinel47[mm]（原读单位m）、refresh rev9/CAS_NONE与三作业SUCCEEDED已核对；wrong-server为派发前MODEL_IDENTITY_MISMATCH/0job，不是实际UNKNOWN隔离证明。

原load请求8cc009c3…和recover ac8f9c97…两份transport UNKNOWN因sandbox health EPERM产生，Main核对原host health200与原存活身份。按已审原Store原子幂等协议，各用原请求文件/原ID/key一次协调：recover NO_UNRESOLVED_WORK，load新ModelRef mcp_4c4cc11ae0e7/gen1，stored sol1的新默认/显式K136点逐值等于保存前，391ms/日期1792694124971未变。Main核13原始hash/五账本/两个原R1字节和原wire一致；旧UNKNOWN回执不改判。Main核验为_state/universal-main-review/20261023-core-r3/NATIVE_REOPEN_AND_UNKNOWN_READBACK.json。

**r3现有真实阻塞**：随后at_points request390ae65a…/job833f44d3…/operationaf5e80ca…返回post-dispatch UNKNOWN，默认units空在getData后读回K被旧比较误拒。公开recover a8782121…/jobc7d6b21b…自身SUCCEEDED，但recovery PARTIAL_UNRESOLVED：三原call(setInterpolationCoordinates/run/remove) SUCCEEDED、ID/type匹配，Java旧snapshot省略null result，严格terminal检查为false。原事件3292/3298/3522及3410的350K内层数值均读回；不制造proof、不升格外层PASS。重开后的sentinel、disconnect、freshWorker重开、新render均NOT_RUN。原runtime继续由原owner保留围栏，没有更多模型/生命周期请求。

035b的Interp/void源软件修补已Main接收：G3仅原helper/原at_points两处AST；显式单位、expr顺序/shape严格，空默认可读回引擎实际单位，非空漂移拒绝，仍空保持UNVERIFIED。Java snapshot仅一个条件补SUCCEEDED的result:null，反向替换全文件等于81afe；三个严格恢复函数与r3 AST一致。44聚焦/27相关/2编译安全PASS，Main独立3项公开adapter/真实Java JSON/缺key负控PASS，不累加。完整Stationary69PASS/1FAIL（缺历史T044）及过滤69PASS/1deselect分开保留。Main审查_state/universal-main-review/20261023-interp-void/MAIN_REVIEW.json。原安装未更新，新候选由Primary从035b固定archive正常打包安装；此时只准protocol/schema prepare，不提前新COMSOL。后续新独立资源须Main实际有限准入，不把历史许可样本/未服务误认license拒绝。

旧A(workpack/_state/universal-m2a-20261022T115828Z-43b2d2)原Server82522/birth1792670973071；source UNKNOWN a1d8c9ed保留，公开强静止协调/Worker退休后stop SESSION_BINDING_BUSY，Server隔离、旧Refs失效。旧B(workpack/_state/universal-m2a-repaired-install-20261022-d898e40069)原Server58888/birth1792678650131；真实study成功、Eval在getData前拒绝、legacy refresh UNKNOWN1f87ec01，最终sentinel是派发前FAILED。公开disconnect已退休原Worker。旧B stop被自动审批在执行前拒绝：自造authorization_ref无可信provider验证；不省略/替换ref、不换provider或路径重试，不凭PID结束。r3 stop因缺正式ref尚未尝试。三处历史UNKNOWN不冻结另一个已获授权的独立资源。

Windows原standalone Desktop20592及既有安装/服务均保留。SID/SCM、共享SID、资源fixture、普通never-resumed子进程观测、child-process-policy flags、MIC LABEL和网络动态端口getter、空WFP events等早期证据均只在各compact原范围内成立；普通高IL非AppContainer、空事件NOT_OBSERVED、配置intent不升格隔离/issuer/vendor验收。固定监听b90和原child-owner7ae软件分别通过232+1（原219/1skip保留）及514+Main迟到退出1；禁止用普通Popen或新包装替代原HANDLE/registry/factory/provenance。

SCM96c6源软件952PASS+Main独立原锁反例1PASS（不累加），原同进程ServiceMain→RUNNING→ControlDaemon/runtime/factory接线、Job先配置再出生及原child归属保持；出生体/运行时等待在全局控制锁外，短在途计数让close等待且metadata响应。有限A仍因目标/profile/policy producer缺失明确拒绝，B/C issuer未开放。Job关闭新单位Main现已限定验收：固定4c archive620原文件+五overlay/一Main输入后622候选，140实际imports，988PASS（常规987+含Main1），43引用核对。SDK前deferral可原factory通过新public cleanup协调；可能生效关闭UNKNOWN不查询/重关，确认成功后仅补记账；旧UNKNOWN cached replay不改。此前含未验收_w26 f9的工作树988保留单列，最终clean基线_w26为024d。Main审查_state/universal-main-review/20261023-job-close/MAIN_REVIEW.json；本单位仍无真实SDK/host/native验收，准备正常发布。

Windows离线主机包工具0926经Main限定44可复现软件/20imports/30引用验收，真实45,544,157字节包hash8d48b42f…仍绑定旧b90/177生产文件/35wheels；operator仅预览，apply_ready=false。服务/AppContainer/ACL/WFP/BFE/账号/全局安全变化尚未授权或应用。具体源码/profile/apply/compare-and-restore包完成后才请求一次必要host批准。现缺初始SCM/保护namespace bootstrap、Windows可信磁盘/root、有限A/B/C及实际新SID/SD/最终源码绑定；AppContainer.restore源码仍RESTORE_NOT_IMPLEMENTED，不能把其回滚计划文字当实现。

分工：Halley同LunaMax准备035b的干净r4 Mac适用候选/正常离线安装112schema，不启动COMSOL；Leibniz Job单位验收后将接有限A目标/profile源/WFP保护producer，Main新合同_state/universal-windows-network-design/FINITE_A_CONTRACT.json（下一wrapper为V4，既有V3用于Job）；Kier持有新_windows_disk_resources及最小_w26 Windows桥接；Lorentz恢复，独立新_windows_host_bootstrap与专属tests/工具，仅初始保护prefix/一个SCM服务的source/软件闭环，不做host apply；Hume关闭可复用。各Agent不改Main/Git或别人的文件；Main集中验收。不得增加全主机/全部DLL证明、伪造native generation/receipt、删保护或反复check相同阻塞。

剩余实际原生目标Windows x64 6.3/6.4与MacARM6.4；其余硬件跳过按TARGETS，三架构构件要求仍在。当前源码/包均非最终冻结候选，最终强Reviewer/两类未预设任务及完整required矩阵未完成。下一步先完成已授权的source/安装/证据审查，真实限制准确落盘，目标继续推进。
