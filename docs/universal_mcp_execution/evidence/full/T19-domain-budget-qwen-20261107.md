# T19 领域文件预算义务：逐路径界定、既有机制路由证明与最小补强 — Qwen 软件证据（2026-11-08）

执行模型：omlx/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S（本地，max reasoning）。本记录仅软件范围：未启动 COMSOL/Worker/SSH，未查询/触碰两处 T07 UNKNOWN 项目及固定 ce10/T07v3 候选资源（空输入缺陷分支为纯主机合成测试），不是原生验收。

## 0 结论

- T19 领域路线的既有显式文件预算机制是 W26 trusted-startup 磁盘准入（COMSOL_MCP_DISK_RESOURCE_CONFIG / --disk-resource-config，_w26_os_resources.DiskResourceCoordinator）：按项目 profile + 运维者提供的按操作 trusted 估算做派发前容量准入，admission-only（不谎称 OS 配额/沙箱），安装后对全部 disk-producing 项目路线 fail-closed，未配置时历史路线完全不变。此前 T19 记录把它漏出预算枚举（写"无字节预算"不完整），并因此把领域路线判为无界；本轮逐路径重界定并补上领域 save 路线的真实断言。
- 分类事实（本轮 probe 固定）：save_model/save_main_model_snapshot/commit_current_main_model/model.save/model.package/artifact.register=FILE_WRITE；model.load/model.clone/checkpoint.restore=WRITE；model.create=WRITE；job.resume/rebuild_target=COMPUTE；以上全部 ∈ _DISK_PRODUCING_EFFECTS（_MEMORY_PRODUCING_EFFECTS 同集合）。legacy model_load/load_visible_main_model/load_current_main_model=PROJECT_WRITE，不在 producer 集合（与 G3 model.load=WRITE 不对称）。READ 类（model.inspect/artifact.preview/model.dependencies）无写盘语义。
- 新增 2 个测试文件共 9 用例钉住：(1) model.save 在 trusted 配置下先于任何引擎调用被 INSUFFICIENT_DISK_BUDGET 拒绝（零 model.java.calls、无 RUNNING、目标文件不存在）；(2) 充足容量时 W26DiskReservation<W26DiskAdmission<RUNNING<SUCCEEDED<RELEASED 顺序且文件/工件真实生成；(3) 部分 trusted 清单缺 model.save 时 RESOURCE_RESERVATION_OPERATION_UNSUPPORTED fail-closed；(4) 未配置 daemon 无 W26 事件、路线不变（大模型兼容不依赖任何猜测阈值）；(5) effect 分类边界钉住，legacy load 不对称显式登记而非擅自改动。未改任何生产源码即完成该证明。
- 附带完成主 Agent 转来的 T18/T19 同路径缺口：公开 code.execute_java 空/空白/非字符串 source_artifact 的首个输入检查位于 _host_side_refusal 保护之外，只能被包成 EXECUTION_STATE_UNKNOWN/post_dispatch。最小修复=把该纯主机检查移入同一保护块使其声明 validation stage；4 个新用例钉住 helper/witness 级/公开路线级确定拒绝（INVALID_REQUEST+validation、Worker 零调用、模型 gate 不污染）且未声明 stage 的 raise 仍保持 fail-closed UNKNOWN。_g2_code.py 已由 T18 提交且无人并发编辑，本轮改动 6+/2-。
- implementation_done 的软件判定要素中"文件预算统一到领域路线"现具备路由级证据；是否据此判 T19 软件完成属主 Agent 裁决（§8 设计点未闭合前本记录不自判）。execution_complete 的原生缺口与前记录一致（活跃 target 上批次 setter/越权负控未执行）。

## 1 边界与继承

- 前驱与既有证据复用：T19-qwen/T19-file-budget 两记录的授权、超时钳制、Java 2 MiB、artifact 身份→大小语义、result/export/preview 预算不重复审、不重跑其日志；T18 记录（T18-qwen-20261107.md）的 PreWriteRefusal 双证设计直接继承；G3.json file_budget_followup/predispatch_java_error 字段引用。
- 禁改边界遵守：未触碰 _g2_engine.py/_g2_registry.py（T13 并发，期间已提交推进 HEAD）、_g3_results.py/_measure_spec.py/_solution_binding.py（ROI 并发）及其测试；未修改 G*.json/FULL_COMPLETION.json；未 commit/stash/reset/checkout/clean；未启动 COMSOL/Worker/SSH。
- 工作树并发：测试执行时 HEAD 位于 ceb32fe（登记空 Java 源拒绝缺口）→ 976c89e → b93b12c（其他 worker 提交使 HEAD 前移）。本轮改动仅 §2 所列文件。

## 2 子 Agent 交付时源码身份

| 文件 | sha256 |
| --- | --- |
| comsol_mcp/_g2_code.py（修复后） | 136432f359fc2b3a3ed1f38a549ae134858d9270d925faf31f383e01327c32cb |
| comsol_mcp/_g2_code.py（修复前=T18 版） | 56c68c655fa35891436ca8e12692e1d7f7b69ff222f1ccbc48d6afcadc33abbd |
| tests/test_universal_domain_file_budget_t19.py（新增） | 79f69dd1a3b4df1ab9ddce0530f40f9d7b6adf3fb94e47df1a01148dca4001a2 |
| tests/test_java_empty_source_validation_t19.py（新增） | f4315042024a20dac72eb1855a99e0ce88c072898e507292f231abc65bb103cd |
| evidence/full/T19-domain-budget-focused-pytest-20261108.txt | 2db18c015cef70e3752c9e825220c8963cdaa886d634471be8397c56f5c593cd |
| evidence/full/T19-domain-budget-w26-regress-pytest-20261108.txt | 5aa9c5031940861c22852f78f5b5f09da3db7e3ccf83d1344b2bb7e487d17839 |
| evidence/full/T19-domain-budget-adjacent-pytest-20261108.txt | 4bc36ed7667e9e77d665d02f393d05188cc3c0f0a7fd0082637d768c0c89de00 |

只读依赖（与工作树当前字节一致，未改）：_project_authority.py、_project_root.py、_artifact_store.py、_g3_managed_models.py、_w26_os_resources.py、_w26_memory_resources.py、_execution_contract.py、_execution_service.py、_managed_backend.py、_control_daemon.py。

## 3 领域文件 I/O 逐路径现状与 T19 适用性

权限绑定统一事实（继承前记录，不重复）：每个入口在 admission 与派发边界要求 live host grant ∩ 项目 policy；workspace 由 bind_project_workspace 绑定；未分类 effect fail-closed。

| 入口 | 写盘/读盘语义 | 预算现状（本轮核实） | T19 判定 |
| --- | --- | --- | --- |
| legacy save_model / save_main_model_snapshot / commit_current_main_model | COMSOL 经原子发布路径写 .mph（file_write） | trusted 磁盘/内存准入覆盖（FILE_WRITE producer）；原子发布+完整性校验既有 | 受 T19；准入既有，本轮补 model.save 路由断言代理证明机制在 G3 路由生效；legacy 名以分类断言钉住（§5.2） |
| G3 model.save / model.package / artifact.register | 真实写 .mph/工件（FILE_WRITE） | 同上 + scratch→publish→register 身份链 | 受 T19；本轮路由级断言 |
| G3 model.load / model.clone / checkpoint.restore | 读注册 .mph 入引擎（WRITE 保守归入 producer） | trusted 配置下要求容量准入（保守覆盖）；身份检查先于大小；G3 load 无 max_size 但 WRITE 准入兜容量 | 受 T19；准入既有 |
| model.create / rebuild_target / job.resume | 引擎态增长（WRITE/COMPUTE） | producer 集合覆盖（本轮 fixture 意外实证 create 也需 profile，fail-closed 属实） | 受 T19（计算/容量准入既有） |
| legacy model_load / load_visible_main_model / load_current_main_model | 读任意（workspace 限定）.mph 入引擎 | PROJECT_WRITE：不在 disk/memory producer 集合；无任何字节/容量准入 | 受 T19 语义但机制缺席=§8 设计点 1，未擅自扩类 |
| 数值结果/导出/下载/preview | 消费侧 | RESULT 64 MiB/1M 元素、export 512 MiB/1 GiB、chunk 16 MiB、preview 上限（前记录已钉） | 受 T19 消费侧，证据既有不复跑 |
| Java 源码（三处）与 Desktop shell_execute | 读入/注册 | 2 MiB 三层 + Worker 帧；Desktop 512 KiB 路由断言缺失 | 前记录已判：Desktop 属 U10，建议主 Agent 登记 U10 |
| API 路线直接文件 I/O | 无（控制通道 JSON） | N/A，产物走共享出口 | NOT_APPLICABLE（具体理由：无文件字节入口；前记录同判） |
| COMSOL Server 自身 autosave/日志 | 不经 MCP 控制面 | 主机 OS 用户边界 | 不适用 T19（非 MCP 文件入口）；如实项 |
| stdio 控制帧 | 无字节上限 | 前记录如实项 3 维持，不新增兜底 | 非冻结"文件预算"语义 |

## 4 W26 准入语义（为何它满足"不猜阈值/不破坏兼容/不放宽权限/可验证"）

- 不猜固定阈值：估算与 reserve_free/max_task 全部来自受信启动 JSON（TrustedDiskResourceConfig.load 校验 schema/权限/host/platform，篡改矩阵已有测试），运维者按项目实测定值。
- 不破坏既有大模型兼容：未配置=不构造 coordinator=历史路线（本轮用例 4 断言无 W26 事件）；配置后也是 admission-only 容量判断，无默认上限。
- 不依赖放宽权限：profile 绑定 project_id+root 身份+host/storage；reserve 前再查 live grant ∩ project policy（_authorize_profile_project）；fail-closed：配置存在但项目/操作缺 profile → RESOURCE_RESERVATION_* 拒绝先于 RUNNING（本轮用例 3）。
- 可验证有效执行：本轮用例 1/2/3 给出真实派发前拒绝与事件顺序、RELEASED 终态、零引擎调用与零产物的负控；UNKNOWN 保留持有的既有 fence 未被触碰（W26 套件回归）。

## 5 本轮最小实现

### 5.1 _g2_code 空输入 validation（T18/T19 同路径缺口）

read_source_snapshot 的 isinstance/strip 检查移入 _host_side_refusal 保护块（6+/2-，含注释）。修复后主 Agent 复现边界 read_source_snapshot(root, "") 经 _dispatch_with_witness：stage=validation ∧ witness.mutation_issued=False 双证成立 → 确定 INVALID_REQUEST/validation（status REFUSED、dispatch_stage validation、witness 零调用），与 T18 超限修复同一语义；公开 code.execute_java 传空 source_artifact（schema 只约束 string，空串可达）同样确定拒绝且模型不 dirty、revision 不变、重试仍同码；未声明 stage 的 raise 仍 EXECUTION_STATE_UNKNOWN/post_dispatch（unproven_pre_dispatch=False 分支保留）。未改 _g2_registry schema、未扩 allowlist、未触碰 T07v3/ce10 资源。

### 5.2 领域 save 路线 W26 准入路由断言（无生产源码改动）

tests/test_universal_domain_file_budget_t19.py：真实 ControlDaemon+ExecutionService+OperationStore+ArtifactStore+合成 _Worker（saveModelPackage 写真实 zip），trusted 磁盘 profile 由 _profile_with/_write_disk_config（W26 既有夹具模式）构造；POSIX 用例 skipif nt（Windows 适配器 NOT_RUN 与前记录一致）。分类断言用例同时钉住 legacy load 不对称现状。

## 6 真实 pytest 退出码

环境：工作树隔离 venv .g3-private/full-continuation-20261107/venv/bin/python（Python 3.14.7、pytest 9.2.0），comsol_mcp 解析到工作树源码，cwd=工作树根；退出码取自重定向文件运行（非管道尾命令）。

| 日志 | 内容 | 结果 |
| --- | --- | --- |
| T19-domain-budget-focused-pytest-20261108.txt | 本轮两个新测试文件 | 9 passed，exit=0 |
| T19-domain-budget-adjacent-pytest-20261108.txt | test_g3_managed_models + test_universal_file_budget_routes_t19 + test_control_daemon_projects + test_m1_product_repairs + test_g3_domain_outcome | 129 passed，exit=0 |
| T19-domain-budget-w26-regress-pytest-20261108.txt | test_w26_os_resources 全套 | 51 passed 10 failed，exit=1 |
| （开发期控制台，未留日志） | 空输入文件单独首跑 4 passed exit=0；其相邻 unit（file-budget+g2_w08_w12+m1+domain_outcome+phase4）123 passed 21 skipped exit=0 | 最终以上述文件日志为准 |

W26 的 10 个失败全部是内存准入用例（MEMORY_OBSERVATION_UNAVAILABLE），根因是 exec 沙箱内 Darwin _sysctl(kern.boottime) EPERM —— 同一根因与"授权主机权限下 56 pass 3 fail"的结果已登记于 evidence/RESOURCE_DISPATCH_REGRESSION.json；本轮未越权重跑，磁盘准入用例 51 个全过。该失败与本轮改动无因果（改动仅 _g2_code 输入检查位置与两个新测试文件；失败用例不触 _g2_code）。

## 7 trusted Java / 主机边界如实

- W26 profile 的 docstring 与实现均为 capacity admission：不证明配额、不证明进程隔离。trusted Java 仍在常驻 Worker JVM 内以 daemon/Worker 同一 OS 用户权限执行；磁盘/内存准入是控制面容量判断，不是 OS 沙箱（Windows T037 例外围栏不变）。

## 8 需主 Agent 裁决的设计点（本轮未擅自实施也未降范围）

1. legacy load 不对称：model_load/load_visible_main_model/load_current_main_model 为 PROJECT_WRITE，落在 disk/memory producer 集合之外；G3 model.load 为 WRITE 已被保守覆盖。选项 (a) 维持现状+如实理由（读不写盘；引擎内存增长由后续 COMPUTE 类 solve 的内存准入覆盖）并把"读入字节预算"明确排除出 T19；选项 (b) 把 legacy load effect 重分类为 WRITE 或将 PROJECT_WRITE 纳入 producer 集合——effect 字符串同时流入 permission_for_legacy_tool、注册契约、回执与 Windows 路线，属跨路径/Windows 重大改动。本轮两者都未做，仅用例钉住现状。
2. 项目 policy 可配置字节配额（policy.budgets）：_normalize_policy 的不变量是"无下游执行的字段拒绝而非持久化"。W26 trusted 启动配置是现成有真实下游执行的通道；若 Reviewer 要求把配额挪进项目 policy，需要 policy→coordinator 的估计来源改造与契约更新（含 Windows profile 对齐），属新范围设计，交主 Agent 定夺。
3. Desktop shell_execute 注册 java-source 512 KiB 上限的路由级断言缺失：前记录已建议登记 U10，本轮维持该建议。
4. Windows：磁盘准入有 Windows 适配器（_windows_disk_resources，Windows 磁盘 profile 测试存在），但本轮路由断言仅在 macOS POSIX；Windows 上的同一语义 UNVERIFIED。
5. 原生缺口不变：活跃 target 上批次 setter 持续执行与越权/错资源负控未跑；T07 围栏不变。

## 9 判定建议（不自判完成）

- 现可认定：领域/G3/注册产物路线的文件预算语义在候选字节上有当前断言与真实退出码（写侧=trusted 准入+原子发布；消费侧=既有固定上限）；不适用项均有具体理由。
- 不可认定：§8-1 未裁决前，"全部领域文件入口受预算"不成立；T19 软件完成判定留给主 Agent。execution_complete=DONE 仍被原生批次 setter/负控阻塞。

## 10 共享状态

未改 G*.json/FULL_COMPLETION.json；未提交；git status 仅 §2 的 1 个生产文件改动、2 个新测试、3 个新日志与本记录（其余并发 worker 的已提交改动非本任务所作）。

## 11 主 Agent 整合复核

子 Agent 的空输入修复草稿会先尝试打开项目根目录，再拒绝空源码；主 Agent 用不存在的项目根和空源码复现了原本不应出现的 ACCESS_VIOLATION（1 failed），随即保留原有的先验证输入顺序，直接声明 PreWriteRefusal。新增回归覆盖了这个顺序和 validation stage。上述 §0/§5 对草稿实现的描述保留为历史；最终 `_g2_code.py` SHA-256 `f0c180b7d3323725eea23d4f4271a6ebe5c5b247b64c6deb252b7b7710646f04`。

删除了把未覆盖的 legacy load 分类当作永久预期的测试断言；该不一致仍为 T19 未完成问题。最终 Java 输入与 save 准入两文件在整合后重跑 `9 passed in 0.35s`，日志 `T19-main-integration-pytest-20261108.txt`；对应两个测试文件 SHA-256 分别为 `810f38e5db9f502daa27479513327e1aed5f8529fd2fe5864e02379ccdca45f3`、`43a18b72f079ae2aecf7d9cee119b0416f8cd2f189c3c90bb84d8bf8b95fede9`。W26 准入是按可信估算的容量 admission，并非实际 OS 文件配额，未配置时不生效；不应将其扩大解释为所有文件入口的强制字节上限。
