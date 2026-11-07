# T19 legacy model-load 准入缺口：路径追踪、绕过实证与最小修复 — Qwen 软件证据（2026-11-08）

执行模型：omlx/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S（本地，max reasoning；本任务由该本地模型自始自终直接完成，未再向下委派）。本记录仅软件范围：未启动 COMSOL/持久 Worker/SSH，未查询或触碰任何 T07/ce10 UNKNOWN 资源；未 git commit/stash/reset/clean；G*.json/FULL_COMPLETION、_g3_common.py、_g3_w13.py、_g2_engine.py、_g2_registry.py、_g2_code.py 全程只读（工作树中它们的并发改动为其他子 Agent 所作，与本轮无关）。

## 0 结论

- 证实绕过：配置了 W26 trusted 磁盘准入的 daemon 上，公开 legacy 名称 model_load / load_visible_main_model / load_current_main_model（含 operation_call/registry_call fallback 内层同名调用）从队列准入到引擎派发全程零 W26 事件、零容量/内存准入，而同一 daemon、同一 profile 下 save_model（FILE_WRITE）被 INSUFFICIENT_DISK_BUDGET 先拒；且修复前运维者无法在 trusted profile 中表达这三个名字（validate 直接 ValueError）。修复前证据：T19-legacy-load-bypass-probe-20261108.txt。
- 根因是分类不对称而非授权漏洞：三个 legacy 名经 _execution_contract.LEGACY_TOOL_EFFECTS 归为 project_write（大写化后 PROJECT_WRITE），落在 _w26_os_resources._DISK_PRODUCING_EFFECTS / _w26_memory_resources._MEMORY_PRODUCING_EFFECTS 之外；目录名 model.load 经 _g3_ops.EFFECTS 为 WRITE，已被 producer 集合覆盖。授权链（_authorize_project_execution → project_write grant、bind_project_workspace、canonical_project_path 限定）本身在 legacy 路由上始终生效。
- 最小修复（26+/1-，单文件 _w26_os_resources.py）：新增 _ADMISSION_EFFECT_OVERRIDES，仅让 W26 资源准入分类器 _registered_operation_effect 把这三个名字视为 WRITE（与目录 model.load 同一保守口径：引擎 load 的容量增长）。权限决议、ledger ticket effect、回执、注册契约、Windows profile 表达形态及其余全部 PROJECT_WRITE 操作零改动；PROJECT_WRITE 与 WRITE 在 _EFFECT_PERMISSION 中同映射 project_write，故准入内的授权复核（_authorize_profile_project live grant ∩ policy）语义不变、无放宽。
- 修复后语义钉住（14 用例全过，日志 T19-legacy-load-admission-focused-pytest-20261108.txt）：配置生效时，trusted 预算不足→INSUFFICIENT_DISK_BUDGET、清单缺该操作→RESOURCE_RESERVATION_OPERATION_UNSUPPORTED、项目无 profile→DISK_RESOURCE_PROFILE_NOT_CONFIGURED、无项目身份→PROJECT_IDENTITY_REQUIRED，全部 pre_dispatch 拒绝、Worker 零调用、无 RUNNING；容量充足时三个名字按 W26DiskReservation<W26DiskAdmission<RUNNING<SUCCEEDED<RELEASED 按约执行且引擎调用恰好发生一次；未配置 daemon 三个名字历史路线不变（零 W26 事件）；内存协调器共享同一分类器，trusted memory 清单下 legacy load 同样进门（MEMORY_OPERATION_NOT_CONFIGURED fail-closed 钉住缺估算分支）。
- W26 语义不变：按运维者 trusted 估算的容量 admission，不是 OS 配额/硬上限；未配置时完全无新增行为；无猜测阈值。

## 1 真实公共调用路径（本轮逐段核实）

1. 公开入口：daemon RPC 直接 operation 名（或 operation_call/registry_call fallback：_g2_registry.LEGACY_FALLBACK_NAMES 含三个名字，ControlDaemon._execute 把 disk_operation 取为内层 operation_id，_canonical_operation_from_metadata 将 durable job 的 canonical_operation_id 定为同名 legacy 名）。
2. 队列准入：ControlDaemon._execute（约 12694 起）→ DiskResourceCoordinator.requires_reservation / MemoryResourceCoordinator.requires_reservation → _w26_os_resources._registered_operation_effect：BY_ID miss → _g3_ops.EFFECTS miss → LEGACY_TOOL_EFFECTS["model_load"]="project_write" → "PROJECT_WRITE" ∉ producer 集合 → False → 跳过整段准入，直接 RUNNING → backend.invoke。
3. 派发：ManagedBackend.invoke 的 selections 分支（1145）要求 unbound 请求 → execute_legacy unbound allowlist（_execution_service 380）→ registry callable → 引擎 client.load / workflow load。项目身份经 execution.project_id 进入 _authorize_project_execution（effect→project_write grant 复核）与 _durable_job_context（job/operation 双记录一致的项目绑定），故这三条路由可归属到精确项目——绕过只缺容量准入，不缺授权。
4. G3 对照：model.load 由 _g3_ops.EFFECTS 给出 WRITE，requires_reservation=True，profile/估算/live-grant 复核先于 RUNNING（既有 T19-domain-budget 测试已钉）。in-process 直连模式（_tools_model.py 无 daemon）没有 W26 配置面，不在本准入机制射程（未配置语义）。

## 2 修复前绕过实证（probe，同一 daemon 同一 trusted 配置）

docs/universal_mcp_execution/evidence/full/t19_legacy_load_bypass_probe_20261108.py（sha256 e7aaa02a…5034b8），输出 T19-legacy-load-bypass-probe-20261108.txt（sha256 d0615265…7d9f2）：

- effect：三名字均 PROJECT_WRITE；disk requires_reservation：model_load=False，save_model=True。
- model_load / load_visible_main_model / load_current_main_model：success=true，事件仅 [RUNNING, SUCCEEDED]，零 W26 事件；三者均触发引擎调用 witness。
- save_model（profile 中 10^17 估算）：INSUFFICIENT_DISK_BUDGET，事件仅 [FAILED]，无 RUNNING、引擎 witness 无 save 调用 → 证明该配置确实在执行准入。
- 向 profile 写入 model_load：ValueError "operation model_load is not a supported disk-producing effect" → 修复前运维者无法表达该控制。

修复后同 probe（--include-model-load，估算 10^17）：trusted_profile_can_name_model_load=ACCEPTED；model_load→INSUFFICIENT_DISK_BUDGET、另两名字→RESOURCE_RESERVATION_OPERATION_UNSUPPORTED，事件均 [FAILED]，引擎 witness 为空（T19-legacy-load-probe-post-fix-20261108.txt，sha256 0a9a0bf3…2d10e）。

## 3 最小修复与边界不变

comsol_mcp/_w26_os_resources.py（修复后 sha256 9c20fb39959e2a45a30b3a79588f4e2c53f74080d238ff625da751986726070d）：

- 新增 _ADMISSION_EFFECT_OVERRIDES = {model_load/load_visible_main_model/load_current_main_model: "WRITE"}，仅被 _registered_operation_effect 首先查询；其全部调用方为磁盘 requires_reservation、内存 requires_reservation、_disk_operation_effect（validate_disk_profile 与 _permission_for_disk_operation）。
- 不变式（测试钉住）：permission_for_legacy_tool 三名字仍 project_write；LEGACY_TOOL_EFFECTS 未动；_permission_for_disk_operation 三名字仍 project_write；model_create/node_create/set_parameters/server_connect 的 PROJECT_WRITE 与 producer 判定不变（_disk_operation_effect 仍 ValueError）；model.load 仍 WRITE。
- 未扩任何 allowlist；未改注册契约/回执 effect 字符串；未触碰项目 policy；未虚构任何配额；未声明 OS quota。
- 兼容语义：对已装 trusted 配置的 daemon，这三个名字由"完全绕过"变为"fail-closed 需要 profile+估算"（与 model.save 缺清单即拒同一语义，T19-domain-budget 记录用例 3 已确立该口径）；配置了 profile 但未列 legacy load 的既有部署将先拒直至运维补充 trusted 估算——这是该 W26 设计的既定 fail-closed 方向，不是行为回归。session-bound 无 project_id 的 legacy load 早在本修复前就被 dispatch 级 gate 拒（"session-bound work requires execution.project_id"）；project-less 队列请求在修复后由协调器以 PROJECT_IDENTITY_REQUIRED(pre_dispatch) 拒绝——同样是配置在场时的收紧，未配置不变。

## 4 测试与真实退出码

环境：工作树 venv .g3-private/full-continuation-20261107/venv/bin/python（Python 3.14.7、pytest 9.2.0），cwd=工作树根，退出码取自重定向运行。

| 日志 | 内容 | 结果 |
| --- | --- | --- |
| T19-legacy-load-admission-focused-pytest-20261108.txt | 新 tests/test_legacy_load_admission_t19.py（sha256 b58f9d2b…371a）14 用例 | 14 passed，exit=0 |
| T19-legacy-load-adjacent-pytest-20261108.txt | 上述 + test_w26_os_resources + test_universal_domain_file_budget_t19 + test_control_daemon_projects + test_universal_project_grant_budget_t19 | 85 passed 10 failed，exit=1；失败集与既有基线 T19-domain-budget-w26-regress-20261108 逐字相同（diff 为空） |
| T19-legacy-load-unit-a-pytest-20261108.txt | test_unit_a_node_public_api.py（legacy 效果分类面） | 154 passed，exit=0 |

复用未重跑：T19-domain-budget 既有 9 用例日志、T19-file-budget/T19-qwen 记录及 RESOURCE_DISPATCH_REGRESSION.json 的授权主机 W26 基线。新测试未重复这些机制证明，只补 legacy load 路由。

新测试为真实 ControlDaemon+ExecutionService+OperationStore+trusted 磁盘配置（_write_disk_config/_profile_with 既有夹具形态）+ registry 合成就调用 witness；断言含事件顺序、RELEASED 终态、engine_dispatched=false、job 无 RUNNING、拒绝码与 stage。

## 5 跨 OS 与环境如实

- POSIX（本机 macOS arm64）：磁盘准入路由级断言真实执行。
- Windows：_windows_disk_resources 不自行分类 effect，Windows profile 解析后经共享 validate_disk_profile 同样接受 override（代码级一致）；但 Windows 上的真实路由本轮 NOT_RUN（与 T19-domain-budget 记录同口径），全部新用例 skipif nt。
- 内存准入：共享分类器已钉（requires_reservation True / MEMORY_OPERATION_NOT_CONFIGURED fail-closed）；完整内存预留路由需 HostMemoryObservationProvider 的 kern.boottime 观测，exec 沙箱内 EPERM——与 RESOURCE_DISPATCH_REGRESSION.json 及 10 个基线失败同一根因，未越权重跑。

## 6 遗留缺口（不自判 T19 完成）

1. W26 仍是 trusted 估算 admission，不是 OS 配额/字节强制（§0）；"项目 policy 可配字节配额"仍属主 Agent 设计点 2，未动。
2. Windows 路由级执行、内存完整预留路由真观测执行未做（环境受限，已如实标注）。
3. 原生缺口不变：活跃 target 上批次 setter 持续执行与越权/错资源负控未跑；T07 围栏不变。
4. G3.json 的 T19 open_question/file_budget_followup.known_gap 现可更新为"legacy load 准入不对称已修复并钉住"；G*.json 对本子 Agent 只读，留给主 Agent 整合登记。
5. 已装 trusted 配置的既有部署升级该候选后需补列三个 legacy 估算，否则对应 load fail-closed——建议主 Agent 在发布说明登记该运维注记。

## 7 本轮文件与共享状态

改动（本轮唯一生产改动）：comsol_mcp/_w26_os_resources.py（26+/1-）。新增：tests/test_legacy_load_admission_t19.py；本记录；evidence/full/ 下 probe 脚本 1 个与日志 5 个（修复前 probe、修复后 probe、focused/adjacent/unit-a pytest 各 1）。未 commit/stash/reset/clean/checkout；git status 中 _g3_common.py、_g3_w13.py 与 T14/T20 文件为并发子 Agent 改动，非本任务所作；HEAD 当前 ca93546dc84aca517bf53d62a9ce97b5a48ac71e（运行期并发前移已如实记录）。

## 主 Agent 整合说明

主 Agent 确认 `_registered_operation_effect` 的调用范围仅为 W26 磁盘/内存准入，并核对基线和候选宽范围日志中的 10 个 FAILED 测试节点完全相同；这些受沙箱 `sysctl` EPERM 影响的失败仍未作为通过记录。原注释“完全不用于权限分类”不够准确：磁盘准入内部的权限复核会经此分类，但两种 effect 均映射到 `project_write`。本次仅缩短并修正该注释，没有改变代码行为；整合后 `_w26_os_resources.py` SHA-256 `fb1816efddc20a30e39b35bef88422410733f99c74a320f09ee8ed05d699f45a`。README 已记录已有 trusted 配置需补列 legacy load 估算的升级影响。
