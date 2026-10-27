# 通用 MCP 当前续跑入口 — r317

目标 active / IN_PROGRESS。历史 Main3 原字节保存在 `_state/universal-main-review/20261027-checkpoint-r317/prior-main3/`；精确证据与角色见 RESUME.json.current_checkpoint_r317 / current_source_roles_r317 / current_execution_gates。本页只汇总当前状态。

| 范围 | 当前结论 |
| --- | --- |
| 完整 MASTER_GOAL | 273 actions /60 tests /427子断言原标准不变；completed_groups=[]；heldouts未选；全量/平台native/save+freshWorker/两个未预设licensed任务族/fresh最终Reviewer均NOT_PASSED |
| 应用源码 | 0.1.11 /592471c5efeecf22abbed3d12f54b5f174ab4de9，已发布；canonical18 unique scoped PASS；非native/CI/整体通过 |
| R5四发行产物 | Strong/Main实际字节接受；wheel205成员，Win/ARM/Intel归档47/54/56成员，55份unique nested wheel RECORD；Main655pins/236459532B |
| Q6-r3 | final binding + 第五sourceZIP207成员 + 一次真实readonly precopy获Strong/Main限定接受；normal flow已Main once释放，实际结果待报，未推断PASS |
| Mac Core | 最近free-only142901248B，不足原Server2GiB/Worker1GiB；继续BLOCKED，无新Core/native放行 |
| Windows当前 | R23r3实际READY已Main接受，实际GO及B1已known0，native终态UNKNOWN_OWNER_RETAINED，未通过；原owner封存 |
| 当前发布 | 仅Main3+紧凑发行证据准备普通非强制同步，原始/运行材料和商业二进制不发布 |

当前精确发行物见 `docs/universal_mcp_execution/evidence/distribution-0.1.11-20261027.json`。四份SHA分别为wheel `3f7b539e3f7e9ef916c646b66c096c97c8c23b696d82468d350651d95c351993`、Win `759a37f5fab005f6e8c8c3e5baa8aee53b438cd2d73fb80bffba9638d57c82a4`、ARM `b56b1d5f88e1c486cae3382e196bf2e2e10f3b994714367dcd0f2863633d46fe`、Intel `d095fb3a3af0867781c6bc72a3772582e0b9d4021d6a0fabb2b1c468c2891c2c`。Main分发接受在 `20261027-r5-coherent-artifacts-independent-review/MAIN_DISTRIBUTION_BYTES_ACCEPTANCE.json`。真实canary六项/cwd见证和build均wait0/Tool0/双EOF；R5仅driver cwd=root/tmp，其余五代码/策略原字节保持R4。补充完整effect-gate inert在build之后，不能改称事前证明。四产物只证明发行字节，未证明平台安装/原生运行/所有内部子进程UNKNOWN安全。Intel历史原始文档b7494ac7仍未恢复，当前963a76b4 locator不同；OpenSSL实际4.0.3与旧license文件名4.0.2差异如实保留。不再重建/重跑canary，旧失败和UNKNOWN保留。

Q6实际capsule19745B/SHA853d35c79215f341d84112d7ed6e6ae8bd18b69f3784c7317f5f6c5667b88b48，sourceZIP3341963B/SHAf2dcc32eb03ab352f65bbe041800ca050128bf54ada69bbe0c8a06309777e43d。该ZIP是在R5四产物之后独立数据装配，不计入原build。Main当前接受 `20261027-q6-r3-final-binding-independent-review/MAIN_FINAL_BINDING_PRECOPY_ACCEPTANCE.json`：557pins/171520171B；literal/data delta，binder函数和normal/UNKNOWN gates不变；current8inert、10deny probes、历史3/8等失败分开，不累加。一次真实validator直接调用precopy返回BOUND_INPUTS_VERIFIED_READ_ONLY、42wheels/199映射、SourceBase1805before=after、fullenv相同、futureRoot missing、原terminal0。capsule execution=false从不自行授权。

Q6下一次normal flow必须由Main具体释放现有binder.prepare_owned_layout_from_capsule，经真实完整precopy后独占复制新ebb5命名空间owned-base；再由其lexical Python -I -B启动原operator：offline-install→含自建内容漂移的negative uninstall plan→只移除自有inside sentinel→ready plan→confirm removal仅该application。原SourceBase只读，owned-base和application为新root兄弟，genuine HOME/home/CODEX_HOME保留；known结果核SourceBase并保存primary/postcheck/record双因，firstUNKNOWN即原owner hold，零后扫描/重试/清理。当前7859B最小driver与实际Mainrelease已通过无效果validate_release/argv校验，Main已放行一次原正常流程，copy/runtime/install/uninstall真实结果待报，不推断完成；普通Q6代码无Core内存门，不能新增required或推导执行许可。OS级只能写runroot隔离尚未证明。

Windows R23r2已修scratch关闭次序、invalid descriptor后继续SDK、PE header-RVA剩余extent，current21 unique inert有原run02返回/trace；Strong/Main源码接受保持。新R23r3只更新identity数据，10程序与已接受r2相同、29函数/2类operator AST相同，A0–A5原Tool/operator/transport均0。实际READY6248B/SHA0d36778bd08b50e6cd6f36bf6fdc88db4e01beb7b329f185cd4fa1994caa05f4、birth精确十进制134375482314729137；Main核84refs、8origins/15cscargv/6compiler/3runtime/6DLL。只证明来源/编译与普通父上下文；PE不证明loaded CLR，READY不证明当前存活。

旧eadadd namespace因Main验收schema不兼容在本地拒绝B0，原A已过期且NO GO，不接管/查询/清理。新254f namespace首B0又在verify_freeze因Main误放两份UNSEALED补充文件而本地preclaim拒绝，无Popen/SSH/GO；原Tool与STOP_INDEX保持。两文件已原字节归档至contract-fix/r23r3-main-placement-correction/，33冻结文件、seal、原lease/acceptance均不变，Main完整冻结static/READY/Blease/argv/budget无效果预检PASS。Main只允许首次真正进入claim/transport的一次B0，known0且完整原返回落盘后才准一次预订B1记录收集。原GO截止2026-10-27T05:00:26.545Z不延长，至少60秒余量，B1仅新root既定三记录，不是进程/handle/liveness查询。首次nativeUNKNOWN零后SDK/query/restore/cleanup/retry/adopt；这不是native通过，适用同人类R18新UI/AppC/Job/新root-code-exe ACL+LowNW范围，不加服务/账号/WFP/BFE/既有ACL/COMSOL/JDK/debugger/impersonation权限。

所有旧UNKNOWN包括Lorentz CoreHOLD、R10 PTY19679、Q1、Euler89383、Windows各原owner/UI/AppC/Job继续封存；禁止poll/input/EOF/close/kill/query/cleanup/adopt/replay。新已终态builder35688/79881/60673不再poll。agent notLoaded/notfound绝不证明native终止。旧R22子退出0xc0000142且IPC前latch UNKNOWN，没有DLL原因证明，不能重分类成功。W23缺fixture仍supplemental FAILED_MISSING_TEST_INPUT。

后续公开MCP开发自测依冻结 `20261027-current-public-mcp-next-sequence/REMAINING_PUBLIC_MCP_SEQUENCE.md` 的合同顺序（其起跑前旧状态保留为历史）：真实构件/正常安装发现→连接与对象自省→同Model项目Java→作业/结果/资源→保存与fresh Worker重开。沿用60测试/427断言，无新增required，尚不选择heldouts。Main集中规划/验收，同terra high连续实现调试；独立Reviewer仅当前阶段审查，最终未预设统一验收仍未开始。

本次实际Windows结果覆盖先前pendingGO状态：B0/B1实际原Tool/operator/transport0，collect三文件25853B逐Base64/hash匹配，FAILURE为UNKNOWN_OWNER_RETAINED。Main已读55行journal并核20refs114482B；profiles/scratch/新UI/Job/两helper已创建resume，Afirstpredicate process_live=false，原HANDLE GetExit BOOL1/exit0xc0000142、lastError122陈旧，IPC/ABA/正常cleanup未完成。254f所有一次额度花完，零后remote/SDK/query/restore/cleanup/retry/adopt；当前存活状态未查。Volta独立限定capture复审进行，Euler仅做已保存证据/官方文档/现有工具的源侧架构可行性，不启动下一实验。

Q6 once release在execution-prep-r1/MAIN_RELEASE_ONCE.json，SHA4efd07194e053bc28f426df69096280e492cddf2082f2446988e416499a9c6c2，driver SHA0eca87b0e52620de01b41047577498a9da3ae2d6cf6e1f988c430468cd9a2318；Main完整7859B源码及原RootedCapturePrep审阅后在noeffects gate实际validate_release/launchargv通过。相同terra执行唯一正常序列，不改capsule/source，不手动ensurepip/pip/repair，不执行Core；普通helper内部短时loopback隔离probe属于原合同，无externalnetwork/download。原始Tool先store+notify/原样落盘，driver全量stdout/stderr重定向避免截断。首次UNKNOWN原owner非返回hold，known非零保存后交Main决定，不自动换root。
