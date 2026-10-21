# 全项目 MASTER V3：集成现有候选，持续完成，再统一独立验收

## 1. 唯一终点与当前起点

用户本轮要求继续完成COMSOL MCP全部原规划，不停在某个W阶段、helper或checkpoint。主Agent自己开发、调试、自测与集成，无需独立Developer。全部实现与可执行测试准备好后，真正独立Reviewer统一验收；必需缺陷整改并复测，直至项目接受或只剩真实外部阻塞。

固定公开起点 `0644ea970688e02beda4be7069f67144b4bcf59e`，tree `2b477e5d7c11dfb59b84994a54da114c7584e0e1`，不是旧cutoff066。根目录为0.1.9基础源码，`continuation/`中还有Windows R19、W23 r15、W24 coupled05及W26候选，**并非已经集成的0.1.10发行版**。先把可用候选合成一份当前代码，再完成未实现/未验证的原要求；不从W01重新开始。

本请求恢复2026-10-13“同步后暂停”之后的开发，但不会把旧一次性审批、私有路径、PID、runtime身份或历史UNKNOWN任务变成新授权。旧暂停文字保留为历史，当前唯一指令是本文件。禁止不加区分运行旧fixed-entry脚本、重放Activity020或把旧UNKNOWN改成成功。

## 2. 范围冻结，不再扩大验收

原26工作包、272逻辑动作、60原测试和六目标标签原字节保持；既有W20/W21/W22冻结合同同样保留。动作可由领域工具、通用API或受控Java合法承载，但必须有正式路由、语义、权限、副作用、回读和适用验收，不以工具数量或万能Java存在代替实现。

沿用仓库USER_SCOPE_DECISIONS：macOS ARM/Intel的COMSOL6.3原生验证为USER_REQUESTED_SKIP，不再次要求该资源、不改PASS。Windows6.3/6.4、macOS6.4及三架构软件构件仍按有效要求完成。不可用机器/许可证/GUI权限/材料数据分别标阻塞或未验证，不自行再删范围。

新发现只在违反原要求、核心不可用、错误科学结论、串模型/版本、破坏数据或越权时阻断相应依赖；其他入DEFERRED_BACKLOG。批准不要求待办清零。只重测受变更影响的旧能力；不要再次全仓挑刺、添加一套新的验收合同。

## 3. 从新目录恢复并承接已有进度

先读START_HERE、REVIEW、FULL_PLAN、RECOVERY、CLEANUP_GUARD、TEAM_PROTOCOL和RELEASE_DOD。

在本包根使用可信Python3.10+执行：
```
python tools/verify_package.py
python tools/bootstrap.py --method auto
python tools/init_resume.py --repo repository
python tools/candidate_inventory.py --repo repository
python tools/status.py --repo repository
```

恢复脚本只取固定公开源码并验证tree/blob，不安装依赖、不运行仓库代码，不覆盖现存目标。没有Git可用archive分支；归档不完整时必须拒绝而非跳过tree校验。上游跟踪内容完整不等于包含本地大模型、商业JAR或未提交修改。

恢复后读取原`docs/comsol_mcp_design_v1/`及`docs/full_project_execution/`的原需求与覆盖表；优先读`continuation/checkpoint/CURRENT_STATE.json`、`continuation/README.md`、`STATES.json`、`INTEGRATION_CONFLICTS.json`、`REVIEWED_MATERIALS_LOGICAL_MAP.json`。大JSON只解析一次，按当前任务投影，不重复粘贴整个历史。

init_resume只创建V3轻量指针，不重置原TASKS/ACTION_COVERAGE/ACCEPTANCE_MATRIX。candidate_inventory只读取/哈希并列出候选差异，不复制候选覆盖生产树、不批准源代码、不生成native PASS。机器上的活job必须重新发现；历史active_jobs=[]不证明现在没有进程。

## 4. 当前必须先做的集成与修补

### 4.1 合成一个规范生产树

按`continuation/INTEGRATION_CONFLICTS.json`和新差异索引，逐个逻辑变更审查并合并Windows R19、W23 r15和相关W24/W26代码。不要整目录覆盖，不使用“最后拷贝的版本获胜”；共享 `_control_daemon`、`_managed_backend`、`_java_worker`、结果与存储实现需要显式协调。

W23 r14保留历史，不当最新候选；r15不能仅凭名称自动覆盖r19的公共平台修复。冻结候选源和原失败不改；对当前差异生成一份集成决策表，记录来源、选择/合并理由、影响测试。原conflict表可能早于r15，实际文件差异也需检查，不能只按旧表漏掉新增冲突。

统一包资源、声明版本与源码范围。0.1.10只有在真实合并后才能作为新候选，正式release还需全量验收。不要通过只改版本号来交付，不使用未合并候选的独立PASS认证整合后的代码。

### 4.2 修复已诊断的 runtime identity 域混用

读取`continuation/packets/activity021-runtime-observation-independent-diagnosis/DIAGNOSIS_RECEIPT.json`及其生产者/消费者定位。已诊断原因是legacy `runtime-WorkerUUID`标签与`comsol-install:<规范化安装路径>`身份被直接比较，并非COMSOL版本API没有返回。

保留现有catalog/Worker runtime_id的兼容语义，在可信后端连接上下文单独绑定installation_runtime_id、Worker UUID、epoch、server identity、endpoint与实际build。比较相同语义域，不能全局替换所有runtime_id，也不能把当前返回值直接复制为expected让检查变成永真。

先做legacy与registered attach两路径、错安装根/UUID/epoch/endpoint/build的有界测试；再做当前合法实例的实际读回。严格消费者继续要求OBSERVED与完整原任务身份；旧UNVERIFIED/UNKNOWN收据原样保留，新修复不倒填历史结果。

### 4.3 缺陷与外部条件分开

Windows R19的324软件测试与W23 r15的764受影响测试只是已记录的限定范围，不能顶替Windows或科学实跑。已有passed且未改变的范围可继承；不用每次重复所有同一软件组。

W26离线依赖venv R1已有源码审查，但8个环境命令仍未跑；先按真实43项固定输入/lock准备新目录，保留实际packaging26.3要求，不能沿用先前26.2描述。旧目录中的inode/审批不可作为当前机器证据。

## 5. 尽早跑最小原生链，连续推进全部工作

### P0 既有通用能力与生产路径

对原覆盖表仍缺的parameter IO、preview、model package/rebuild、版本/文件/权限/恢复动作完成实际实现和回读。先利用合并候选和已有测试，禁止新建重复执行系统。尽早通过正常MCP→ControlDaemon→Worker→真实COMSOL的最小构建/读取/保存链，之后扩展科学任务。不把手工注入Service、编译成功或目录存在当生产通过。

### P1 W23 光纤耦合

保留r15已修正的strict measure、字段方案、固定入口一致性。Activity021只是definition/geometry metadata，不是复场/功率/科学接受；Activity022八槽NOT_RUN必须在当前候选与新运行身份下实际执行相应字段检查。

先检查可访问且hash匹配的已存MPH，优先零solve读取；文件不存在则准确标历史产物不可恢复，并通过已公开builder建立新的小型光场模型、独立运行，不冒称同SHA重放。完整复E/H、对应port面/物理侧、单位、坐标、normal、频率/参数/存储解、功率归一化、mode coupling与capture、相位不变性/偏移/网格敏感性按原冻结标准执行。不能补零、用错面port字段或将静态metadata当数值采样。

### P2 W24 稳定胶形与UV→热固化→冷却

从coupled05候选继续，核对原once消费和HOLD原因，恢复新活动前满足其实际安全/输入条件。完成有台/无台形态、可跨台阶接触、胶量守恒；温度、固化、参考态、位移/应变/应力历史实际传递。通用温度传递PASS不替代T047。缺少真实材料曲线时用明确合成夹具验证机制与敏感性；科学假设未知不报告真实材料已校准。

### P3 W25 Desktop与宿主

API优先完成真实窗口/模型绑定、共享server交替读写、未保存standalone模型的显式保存/迁移、权限缺失处理、可用观察与取消。绝不随机点击或强停用户共享Server；同名路径/tag不证明同一个模型。macOS TCC和Windows窗口/进程权限用当前证据，不复用历史许可。Host工具发现、图像传输、模型实际收图分别记录。

### P4 W26全交付

当前源码制作三架构构件、依赖锁/SBOM/hash与许可清单，实际源外安装、离线运行、升级回退、非空卸载、完整数据库+模型+artifact恢复及三科学recipe。ARM TLS/lzma或fat切片字节检查不等于Intel硬件与全包安装；gdbm/Tk等已有失败按原依赖/功能必要性解决，不为绿灯删除required。离线是产品验收项，用户保持联网不取消它；用授权隔离测试环境，不擅自断整机网络。

P0–P4是内部顺序，非停止点。平台资源缺失只阻塞依赖分支；主Agent完成剩余所有安全可执行工作。不要每完成一个helper、C编号、source review或push就等待新Goal。

## 6. 原生UNKNOWN、一次性入口和预算

保留Activity020 `a177789d-6af3-431f-9fe6-fa33c7e415ed` 等历史UNKNOWN/safe_retry=false与已消费的派发，不重新提交相同作业，不改CANCELLED或PASS。旧脚本硬编码路径/模型hash/审批/session都只是历史材料，不可在新机器直接执行。

本轮恢复开发可建立新的任务所有runtime与有界测试活动；进入新原生动作前核对目标所有权、模型、source、字段、预算和实际权限。既有专门控制gate要求的fresh授权/once流程仍保留，不借最终统一Review跳过运行安全检查；也不把普通自测变成每个文件都需独立Reviewer签字。

新机器不存在原私有库/MPH时，先做LOCAL_ONLY清单分类：可官方重下载、可由已提交recipe重建、必须保留的用户唯一数据、历史证据不可重建。不能为缺文件无限等待旧路径，也不能声称新运行恢复了旧请求。新任务与旧失败分别记录。

## 7. 高效持续执行与资源控制

只有一个当前生产树、一个有效候选源清单、一个轻量RESUME和共享原始证据索引。原状态表保留但不在每格复制长历史。将缓存/依赖/模型/日志放到明确预算的工作盘，避免反复复制大树和/private/tmp积累；不自动清理未知目录或失败证据。

每个安全checkpoint写`docs/full_project_continuation_v3/state/RESUME.json`：源码/集成进度、当前任务、实际job/未知引用、输入产物、可执行next_action和外部阻塞。same Goal续跑先读该文件，核查实际运行，不重新bootstrap现有目录、不重建原矩阵。

聚焦测试+受影响回归在开发时立即做；保持科学参考/阈值先冻结、原始观测实时采集、strict JSON、只清理自有临时节点。已有合格测试不无理由重复，不另造更多“证明审查已审查”的框架。

会话或上下文限制时准确落盘，后续同一Goal可继续；不承诺无人触发的后台自启动。还有安全可做工作就继续，不能仅因完成一个内部阶段而停止。

## 8. 完成后统一独立Reviewer

主Agent直接开发，无独立Developer要求。全量候选、自测和当前可执行原生结果准备就绪后，由Host创建真实独立Reviewer上下文，给原合同+scope waiver、合并差异、候选清单、全部原始证据和阻塞表。

Reviewer自行选关键正负控并在可访问目标独立复测：模型与运行时身份、复场归一化、胶形/状态历史、真实Desktop、Windows权限/恢复、离线安装与全数据恢复。不得只重跑主Agent汇总器，也不能主Agent自己换角色签字。Reviewer可以分批审阅后给一份全项目结论；不是把每次资料批次当新的里程碑门禁。

必需问题→CHANGES_REQUIRED→主Agent修复→新冻结候选→复测受影响范围。未改且证据适用部分可继承；无固定轮数自动批准。非阻断待办不必清零。Host不能创建独立Reviewer时标REVIEW_BLOCKED而不是伪造ID；仍可完成其余开发与自测。

## 9. 完成、交付和停止

最终交付应含当前源码/三架构构件、有效支持矩阵、依赖锁/SBOM/许可、原272能力路径、原验收对应、三领域方程/输入/模型/结果/图与重建recipe、安装升级回退/卸载/完整恢复、安全与独立总审查。源包和原始科学数据分开；不公开token/商业库/未授权用户数据。

依据既有权限做普通非force Git同步；分支保护用PR，无权限则保留commit/patch并报告。不要重新混入私有历史或靠删除工作树凭据忽略中间commit对象。阶段同步后继续，最终合格候选才标发行。

`PROJECT_ACCEPTED_WITH_RECORDED_SCOPE`：原必需功能、现行适用原生与交付矩阵完成，已有Mac6.3 skip如实保留，独立Reviewer对同一最终候选APPROVED，恢复/安全/交付完成。
`ACCEPTANCE_BLOCKED`：所有不依赖外部资源的工作已做完，仅剩具体机器/许可/权限/资料或独立Review条件；不能标项目完成。
`CHANGES_REQUIRED`：仍有可修复缺陷，继续本Goal。不能将代码缺陷改名外部阻塞。

终点是W01–W26全项目，而不是新的source-only候选、Activity022或依赖venv单元。
