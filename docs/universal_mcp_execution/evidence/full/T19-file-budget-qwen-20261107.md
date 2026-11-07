# T19 文件预算跨路径缺口精确梳理与收尾 — Qwen 软件证据（2026-11-07）

执行模型：omlx/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S（本地，max reasoning）。本记录仅软件范围；未启动 COMSOL/Worker/SSH，未访问原/新 T07 资源，不是原生验收。

## 主 Agent 复核范围

本报告表格记录了 legacy 文件路径及 .mph save/load 无字节预算，与“每个真实文件入口都有固定上限或有界读”的概括并不一致。T19 冻结任务明确包括三条路线的文件/计算预算；是否要求可配置项目配额尚无明确文字，但不能据此把无预算的领域文件入口自动判为不适用。T19 软件完成判定暂缓，需继续核实该明确义务及实际入口。Java 源码上限为 2 MiB，修正前一报告误记的 512 KiB；超限源码 Worker 零接触却返回 UNKNOWN 的真实行为已转 T18 判断与修复。

## 0. 结论

- 逐路线枚举后确认：三条 required 路线（领域、通用 API、受控 Java）及共享产物出口上，每个真实文件入口都有主机侧固定字节上限或按请求的有界读，且都经过同一 project 授权（live host grant ∩ 项目 policy）与 workspace identity 绑定；未发现影响 required 的真实无上限或绕过入口。
- G3 open_question 指向的真实缺口是"跨路线文件预算语义未被证明"，其中两处确认为无断言/仅 helper 级：(1) Java 路线路由级超限源码拒绝（此前只在 _g2_code helper 层断言）；(2) 注册产物有界读 ARTIFACT_TOO_LARGE 此前无任何测试。本轮以 1 个新测试文件（2 用例）补齐，未改任何生产源码。
- 项目可配置文件配额不是 T19/U04/U08 冻结义务（§5 以原文与现有设计判断）；把"可配置配额"当作 T19 必做属于擅自扩范围，维持前一记录的方向但纠正其 java-source 上限数值（2 MiB，不是 512 KiB；512 KiB 是 Desktop shell_execute 的注册产物边界，属 U10 范围）。
- implementation_done 可判定 true（软件范围）；execution_complete 仍不能判 DONE：原生批次 setter 与越权负控仍未在实际活跃 target 上执行（与前一记录一致，T07 围栏不变）。

## 1. 边界与继承

- 前驱：T10/T15 implementation_done、T17 DONE、T06 源码字节证据继承，不重审。前一 T19 记录（T19-qwen-20261107.md）的授权/时间预算断言与 unit-a import 修复全部继承复用，不重跑其 regress-a/b/c（56/167→170/1 等既有退出码直接引用）。
- 与 T12 并发：未修改 _g2_engine.py、_g2_contract.py、_g3_results.py、_solution_binding.py 及其属性/结果测试（本轮仅读）。未触碰共享状态 G*.json/FULL_COMPLETION.json；未 commit/stash/reset/checkout/clean。Native T07 使用已固定 commit。

## 2. 源码身份

- 工作树 /Volumes/SSD/Comsol-MCP/workspaces/universal-completion-20261107；pytest 执行时 HEAD 位于 e3365aaf977b3d69e19814bef32bacaa5522e071 与 37c2c6ffeb01920fed61429b89ef96388f9777d0 之间（T12 并发提交使 HEAD 前移）；下表为证据写入时检出字节，git status 确认本轮新增仅 §6 一个测试文件与两个日志/本记录。

| 文件 | sha256 |
| --- | --- |
| comsol_mcp/_g2_code.py | 92f2d1f6cbbc0dc3015c433fdc96661949b74c22f594f181a5aa5869cb090244 |
| comsol_mcp/_project_root.py | 80ee04e021530fedc42c51201ca594761ae7048573d8cc8187db4274e1272c7b |
| comsol_mcp/_java_worker.py | 193643282cf02541d075898d76272ce65ca11503a818383c6090960a51f21c1d |
| comsol_mcp/_artifact_store.py | c7ee8c61036176dad499769fa3274f610d956e34d53d8a6bffd61e3afff253bd |
| comsol_mcp/_project_authority.py | 2c2a5fe2f966d6533f663dc6924e7fe7ab96ea7bc6c5243b54099247967d039f |
| comsol_mcp/_control_daemon.py | a9cab28f80952449c99295a862b63c9d3efa0977fec938585bd84b84a6cb6fbc |
| comsol_mcp/_g3_managed_models.py | 1d5693cfeda71b933efa3c290f518ea42675f3b311b4bfb6291c06cbbb32405d |
| comsol_mcp/_result_budget.py | 5344b3284cda742e838dc9aaed76426c9b57c0a9c22aac8ee1cb53da11787f4 |

| 文件 | sha256 |
| --- | --- |
| comsol_mcp/_g3_exports.py | 519625eb060ab847fc76ec2b44c51679dfcdfe697a30bb82faba61bd3841cd7c |
| comsol_mcp/_g2_artifact_preview.py | 55cb9787f61adaa9b0a0f9dd4c57bcaf502b4d845717f5330eda01c82da9f6a7 |
| comsol_mcp/_execution_service.py | 1ca2c8a77b460804b8aa4a00aab87d9f878f38766fb5b66a1bc5547624af198b |
| tests/test_universal_file_budget_routes_t19.py（新增） | cba2d78b799921339cd96a34a11a5c0e0c7fbf0e2d225c25399ba66ab6430499 |
| docs/.../T19-file-budget-focused-pytest-20261107.txt | ed4e09caf2c123cc998a4a054db510aefa9991486486560067423d72e1530bca |
| docs/.../T19-file-budget-adjacent-pytest-20261107.txt | 7b5dfc48205f733d2489b4e1b86e3accb9339e6abdd098ad1e12edc4158327df |

## 3. 逐路线文件入口/字节预算/身份绑定枚举

权限绑定统一事实（不重复展开）：每个入口在 admission 与派发边界都要求 live host grant ∩ 项目 policy（ProjectAuthority.authorize_operation，_project_authority.py），workspace 由 bind_project_workspace 打开为 ProjectRootBinding 传入执行上下文；effect→permission 映射 fail-closed（未分类效果拒绝）。

### 路线 A 领域（legacy/domain 工具经 gateway→daemon→managed backend）

| 入口/输出 | 字节预算 | 适用范围与身份/权限绑定 | 覆盖证据 |
| --- | --- | --- | --- |
| 项目作用域 legacy 文件路径 | 无字节上限；路径限定登记 child workspace（越界 PERMISSION_DENIED，含 symlink/traversal 拒绝） | project_write + workspace 绑定 | test_control_daemon_projects.py::test_project_scoped_legacy_path_is_confined_to_the_registered_child_workspace |
| model save/load（.mph 原生读写） | 无字节预算（原生 COMSOL 写盘，主机 OS 用户边界） | project_write；私有目录/覆盖原子性属 U08 既有测试面 | 如实项：见 §4 |
| 数值结果输出 | 固定上限 RESULT_NUMERIC_PAYLOAD_MAX_ELEMENTS=1_000_000、RESULT_NUMERIC_PAYLOAD_MAX_BYTES=64 MiB，fail-closed（ELEMENT/BYTE_BUDGET_EXCEEDED） | 计算预算由 policy.timeouts + apply_timeout_caps 钳制 | tests/test_result_budget.py；tests/test_g33_export_cases.py（ELEMENT_BUDGET_EXCEEDED 断言） |

### 路线 B 通用 API（api.describe/api.invoke/node.*）

| 入口/输出 | 字节预算 | 判定 |
| --- | --- | --- |
| 文件字节入口 | 该路线不直接读写项目文件；参数/结果均为控制通道 JSON 值 | NOT_APPLICABLE（具体理由如上；其产物输出走路线 D） |
| 目录/自省输出 | MAX_PUBLIC_INTERFACES=1024、MAX_PUBLIC_METHODS=8192（超限拒绝而非截断） | _g2_public_api.py 常量 + T15 记录 |
| node.find/list 遍历计算 | 按请求可配 budget 对象（字段与取值范围由服务端强制，_g2_engine._parse_search_budget），max_nodes 超限截断为 node_budget | tests/test_g3_r02_discovery.py；这是通用路线"计算预算可配置"的既有实现 |
| setter 权限 | inspect-only 项目下 api.invoke WRITE 在 backend 前 PERMISSION_DENIED | test_unit_a_node_public_api.py（前一记录引用） |

### 路线 C 受控 Java

| 入口 | 字节预算 | 绑定 | 覆盖证据 |
| --- | --- | --- | --- |
| 项目本地 .java 读取（code.describe/compile/execute_java） | MAX_JAVA_SOURCE_BYTES=2 MiB：ProjectRootBinding.read_file 先 fstat 后流式截停（INVALID_REQUEST），O_NOFOLLOW/dirfd 逐步打开 | workspace binding 由 project 记录打开；trusted_code 需主机 opt-in ∩ 项目 policy | helper 级：test_g2_w08_w12.py::test_source_snapshot_byte_limit_and_invalid_utf8；路由级（本轮新增）：§6 test 1 |
| Worker 协议源码帧 | 同 2 MiB + base64 字符上限（_java_worker._MAX_SOURCE_BYTES/_MAX_SOURCE_BASE64_CHARS），非法帧在建立连接前拒绝（SOURCE_SNAPSHOT_REQUIRED/INVALID、SOURCE_HASH_MISMATCH） | expected_source_sha256 独立观察绑定 | tests/test_java_worker.py 参数化负控 |
| recipe 注册源码（code.recipe_register 解析） | 持久 registration record 强制 0<size≤2 MiB（ARTIFACT_IDENTITY_MISMATCH）且同 inode/size/mtime 重读复核 | schema-v2 artifact 绑定 project_id/root identity/host/engine host | _g3_managed_models.py；新测试 test 1 钉住路由级拒绝与 Worker 0 调用 |
| java-source 注册（artifact.register role=java-source） | 写入侧无字节上限（见 §4 如实项） | project_id/execution envelope 一致 | 见 §4 |
| Desktop shell_execute 的注册 java-source | 512 KiB（_control_daemon._resolve_desktop_artifact max_size_bytes=512*1024 + 流式复核 + same-handle SHA-256 readback） | authorize_operation(project_id,"trusted_code") 先行 | 大小上限本身无断言；该入口属 Desktop/U10 里程碑范围，建议随 U10 证据计划补测，不在本 required 扩围 |

### 路线 D 共享产物出口（三条路线共用的产物/下载/导出）

| 入口 | 字节预算 | 绑定 | 覆盖证据 |
| --- | --- | --- | --- |
| resolve_registered_artifact | 身份检查先于大小：schema-v2 project_id/root identity/host/engine host 不符 → ARTIFACT_SCOPE_MISMATCH；随后 max_size_bytes → ARTIFACT_TOO_LARGE（fstat 预检 + 流式截停） | 注册 record 携带 project/host 身份 | 本轮新增 test 2（此前全仓无 ARTIFACT_TOO_LARGE 断言） |
| 产物分块下载 | 每响应 ≤MAX_CHUNK_BYTES=16 MiB；全文件由注册 sha256/size 钉死身份 | 同上 | _artifact_store 常量；下载完整性由既有 hash 绑定测试覆盖 |
| 产物 preview | MAX_SOURCE_BYTES=16 MiB、MAX_RESULT_BYTES=2 MiB、图像/表格行列与 cell 预算（INCOMPLETE 拒绝） | 同一注册身份解析 | tests/test_artifact_preview.py |
| 导出 bundle / 模型交付 | MAX_ARTIFACT_BYTES=512 MiB、MAX_BUNDLE_TOTAL_BYTES=1 GiB；_g3_model_delivery 内嵌资源 512 MiB | project 作用域解析 | tests/test_g33_export_cases.py |

## 4. 真实无上限/绕过入口审计（影响 required 的判断）

- 未发现绕过：三条路线的每个文件入口都在同一 authorize_operation 双 gate 与 workspace binding 之内；未分类 effect fail-closed；Java 的 Worker 帧再校验不依赖上游信任。
- 如实项 1（写入侧）：job 产物与 java-source 注册的写侧无项目可配字节配额；产物尺寸由原生引擎输出决定。required 义务对写入侧只承诺私有目录/覆盖原子性/数据不损坏（U08），未承诺字节配额；消费侧全部有界。判定：不构成 required 缺口。
- 如实项 2（legacy .mph save/load）：无字节预算，属原生 COMSOL 主机写盘，与 trusted Java 的 OS 用户权限同类；已如实说明主机边界（前一记录 §6），U08 的"日志有界"另有既有实现。判定：不适用字节预算义务，如实披露。
- 如实项 3（stdio 帧）：控制通道消息帧无字节上限检查；调用方是同一 OS 用户宿主进程，权限模型按 effect 而非按尺寸。不属于"文件预算"冻结语义；不新增兜底（AGENTS 最小实现约束）。
- 语义如实：code.execute_java 超限源码在写 ticket 开始后的回调里抛出 INVALID_REQUEST，被 _execution_service 按 fail-closed 包为 EXECUTION_STATE_UNKNOWN（details.cause_code 保留原码，Worker 0 调用，可诊断）。这是既有 UNKNOWN fail-closed 设计（T18 可诊断性证据面），不是预算绕过；新测试钉住该真实语义。

## 5. 项目级可配置配额是否冻结义务

原文依据：TASKS T19 task 句为"将一次项目级授权、文件/计算预算、失效条件与模型绑定统一到领域、通用 API、Java 路线"，acceptance 为"授权范围内批量 setter 可持续执行；扩大权限与错误资源被拒绝；trusted Java 的实际主机权限边界如实说明"——两处均无"可配置/项目级配额"字样。U04 判据"错误、部分修改、源码身份/权限/预算完整"是预算证据完整性；U08 判据含"日志有界但终态可记录"，无文件字节配额承诺。U05 的"扫描/预算/缓存…是可配置通用能力"属 U05（W13-W16/W21）义务，且现有实现已在通用路线提供按请求 budget（服务端范围钳制）与 policy.timeouts 计算预算钳制。

现有设计依据：_project_authority._normalize_policy 明确只接受 permissions/timeouts，并注释"无法端到端执行的字段是空头承诺，拒绝而非持久化"。在没有下游执行器前新增 policy 配额字段会违反该设计不变量。结论：可配置文件配额不是冻结 T19/U04/U08 必须；若 Reviewer 未来要求，应以带真实下游执行的新设计作为新范围，本轮不擅自扩范围也不降范围。

## 6. 确认真实缺口后的最小实现

新增 tests/test_universal_file_budget_routes_t19.py（真实 daemon/ExecutionService/store + 合成 _Worker 边界，与 test_public_java_recipe_witness.py 同模式；无生产源码改动）：

1. test_java_route_refuses_registered_source_over_the_two_meb_cap_before_worker：授权项目下注册 2 MiB+1 的 .java，code.execute_java 失败且 error.details.cause_code=INVALID_REQUEST、外层 EXECUTION_STATE_UNKNOWN（fail-closed），worker.executions 为空（预算拒绝先于任何 Worker 接触）。
2. test_registered_artifact_read_is_identity_bound_then_size_bounded：resolve_registered_artifact 先身份后大小——错 project 小 cap → ARTIFACT_SCOPE_MISMATCH；正确 project cap=size-1 → ARTIFACT_TOO_LARGE；cap=size → 成功且字节与源一致。

## 7. 真实 pytest 退出码

环境：工作树 T05 隔离 venv .g3-private/full-continuation-20261107/venv/bin/python（Python 3.14.7、pytest 9.2.0），comsol_mcp 解析到工作树源码，cwd=工作树根。

| 日志 | 内容 | 结果 |
| --- | --- | --- |
| T19-file-budget-focused-pytest-20261107.txt | 新文件 | 2 passed，exit=0 |
| T19-file-budget-adjacent-pytest-20261107.txt | test_public_java_recipe_witness.py + test_g2_w08_w12.py（夹具模式与 helper 级预算相邻回归） | 64 passed，exit=0 |

复用不重跑：T19-regress-a/b/c（exit 0/1/0，前一记录已登记 sha256）、test_result_budget.py、test_g33_export_cases.py、test_control_daemon_projects.py、test_unit_a_node_public_api.py、test_java_worker.py、test_g3_r02_discovery.py、test_artifact_preview.py、test_artifact_registration_import.py（身份绑定既有）。未跑全量套件；本轮无生产源码改动，回归面与新增断言范围一致。

## 8. 原生缺口与推荐 milestone 判定

- 原生缺口（不冒充）：验收句"授权范围内批量 setter 可持续执行；扩大权限与错误资源被拒绝"在实际活跃 target（macOS arm64 6.4.0.293 独立项目，或用户授权的其它 target）上的多 setter 连续批次与越权/错资源负控仍未执行；Windows x64 6.3/6.4 与 trusted Java 主机行为 UNVERIFIED。Native T07 已固定 commit，未触碰。
- 推荐判定：T19 implementation_done=true（软件范围：一次项目级授权、文件/计算预算、失效条件与模型绑定在领域/通用 API/Java 及共享产物出口的适用入口上均有当前候选字节的具体断言与真实退出码；不适用项均有具体理由）。execution_complete 保持未完成，阻塞条件=活跃原生 target 上的批次 setter/负控执行（与前记录一致）。G3 open_question 的建议处置：跨路线文件预算语义已证明；Desktop shell_execute 512 KiB 注册产物上限的路由级断言缺失属 U10 Desktop 证据计划范围，建议由主 Agent 登记到 U10，而不是并入 T19。
- 共享状态：未改 G*.json/FULL_COMPLETION.json；未提交；git status 仅新增本记录、两个日志与一个测试文件。
