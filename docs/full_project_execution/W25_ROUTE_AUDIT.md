# W25 生产路由只读审计

审计时间：2026-09-26  
审计范围：冻结合同中的 W25、Desktop 相关生产路由、现有权限/身份保护、迁移/取消/异常释放路径和已有证据。此审计没有运行 pytest、COMSOL、GUI 或 computer use，也没有修改生产代码、主状态或环境。

## 结论

**W25 当前是产品实现缺口，不能只记成 GUI 测试阻塞。** 原始合同要求把平台 `WindowHandle` 与 COMSOL `ModelRef` 形成可审计绑定，并包含未保存 standalone 模型迁移、GUI 权限拒绝及目标窗口取消等能力；当前生产包没有 Desktop adapter、MCP Desktop tool 或对应运行时路由。动作目录虽列出八个 `desktop.*` 动作，但当前覆盖表仍是 `UNASSESSED`，`production_route` 与源码路径为空。

API/CLI 已有一条有用但方向相反的可用路径：MCP 连接 COMSOL Server、从文件加载或采用 server-side 模型，再要求用户在 Desktop 中连接同一 Server 并手工导入模型。它不能识别当前窗口、观察 standalone 未保存内存状态、替该窗口保存副本，也不能验证 Desktop 实际选中了哪一个模型。因此，这条既有流程不能当作 W25 迁移或共享绑定通过。

在测试侧，原合同要求的 T004、T051、T052 在六个目标组合均没有验收证据，当前矩阵为 `NOT_RUN`；W25 任务行仍为 `UNASSESSED`。Mac COMSOL Desktop 的既有访问预检明确因 Computer Use 权限拒绝，记录 binding=`unknown`、cancel=`UNVERIFIED`。该事实只阻塞受影响的真实 GUI 验收；它不能解释缺失的产品路由，也不能把其他五个目标组合一律标成相同阻塞。

## 合同与状态

- `docs/full_project_execution/FULL_PLAN.md:37-43` 要求可审计的 WindowHandle–ModelRef 关联、共享 Desktop/API 的交替读写和冲突发现、保留未保存修改的显式迁移、权限/控件/焦点失败时拒绝，以及真实取消停止和异常释放验证。
- `docs/full_project_execution/contract/04_IMPLEMENTATION_PLAN.md:31` 把 W25 定义为依赖 W03/W12/W19 的迁移与高级 GUI；`docs/full_project_execution/state/TASKS.json:548-562` 仍无实现路径、证据或已登记 blocker。
- `docs/full_project_execution/contract/03_ACCEPTANCE.md:41-49,511-529` 与状态矩阵中的 T004/T051/T052（分别约在 `state/ACCEPTANCE_MATRIX.json:167`、`:2721`、`:2775`）要求 GUI 绑定、未保存模型迁移、权限缺失处理及 GUI 取消证据；六个平台/COMSOL 组合目前均是 `NOT_RUN`、证据列表为空。T054 属 W19 依赖但也必须核对，矩阵约在 `:2883`，六组合同样 `NOT_RUN`。
- `docs/full_project_execution/state/ACTION_COVERAGE.json:12794-12802` 和 `:13137-13145` 展示动作覆盖表的首尾：`desktop.status` 到 `desktop.migrate_standalone` 均为 `UNASSESSED`，无 `production_route`、源码路径或目标证据；中间六个动作同样如此。
- 旧的 `docs/comsol_mcp_design_v1/PHASE1_OPERATIONS.md:68-76` 和 `evidence/mac_same_snapshot/20260919/desktop_access_preflight.json:3-12` 已记录 Mac COMSOL GUI 访问因 Computer Use 未获授权而失败；没有改权限、点击或取消操作。`evidence/w03/runs/20260918T111700Z/gui_access.json:2-10` 记录同一边界。当前审计遵循用户本轮“尽量不要使用 computer use”，没有重新探测。

## 现有实现能证明什么

**可用的 Server/API 身份与可观测性。** `_state.py:170-200` 的状态只给出 Server endpoint、当前模型 label/path/origin 和 MCP workflow，没有窗口句柄、Desktop 进程身份、活动窗口、平台 UI 权限或当前选中模型。`PersistentComsolWorker.java:410-420` 能给指定 server model 生成有限指纹（tag、label、路径、修改时间、参数）并报告 server/worker instance、generation；这属于 server-side `ModelRef` 线索，不是窗口绑定证明。`_execution_service.py:40-46,64-72` 会在读写前比较可观测状态并对已观察到的变化 fail closed；其文档同时限定该适配器的范围，不能证明所有外部模型改动都可见或 COMSOL 提供原子 CAS。`G3_CAPABILITIES.md:152-154` 也保留 `ModelChangedHandler` 回调 FAIL、浅指纹范围及 GUI/Desktop 未覆盖的原 verdict。

**当前 Desktop 工作流不是迁移。** `comsol_mcp/_tools_connection.py:169-175` 与 `comsol_mcp/_tools_workflow.py:90-153,340-370` 的顺序是连接 Server、按路径加载/采用 Server 模型、要求用户手动让 Desktop 连接同一 Server 并导入已加载模型。它保护其他 Server 模型，不会从独立 Desktop 进程读取未保存更改。没有证据证明保存策略覆盖未保存增量、复制前后身份或 Desktop 绑定状态。

**没有生产 GUI 发布或调用入口。** `comsol_mcp/mcp_server.py:11-42` 注册连接、工作流、模型、领域、控制等模块，没有 Desktop 模块。`_g2_registry.py:64-98` 的可实现 allowlist 含 registry/node/code/checkpoint/transaction/docs 和 job 控制动作，没有 `desktop.*`；`is_implemented()` 对目录中仅有 schema 的桌面动作不会提供真实实现。动作目录中的 `desktop_status` 等项是契约元数据，不能被算作执行路由或权限保护。

**已有取消是 job 状态控制，不是 Desktop 控件取消。** `ControlDaemon` 对排队任务可原子取消并阻止 engine dispatch；对运行中任务会返回 `UNSUPPORTED_NATIVE_CANCEL`、`engine_stopped=false`（`_control_daemon.py:521-585`）。共享/外部 Server 不可由默认 force stop 杀进程；受管且证明所有权的隔离 Server 进程终止是另一条受限路线。`tests/test_g3_5_w19_control.py:94-190` 用 mock/hanging callback 验证排队取消与“运行中只记请求、不谎报停止”；这不是 COMSOL 求解器或真实 Desktop Cancel 证据。W25 的目标窗口取消仍没有实现。

**worker endpoint 锁不等于 `blockOtherClients`。** Worker 在连接时以本地文件 `FileLock` 把一个持久 worker 限定到一个 endpoint；断开 `finally` 会释放它（`PersistentComsolWorker.java:306-327,383-405`）。`tests/test_java_worker.py:484-499` 的两 worker 测试验证第二个 MCP worker 收到 `ENGINE_BUSY`，没有另一个 Desktop 客户端被阻塞的断言。生产源码没有 `blockOtherClients`/对应解除调用。`unlock_visible_main(reason)`（`_tools_workflow.py:175-200`）仅清除 MCP 的 visible-main 工作流保护位并记录原因；它也不是远端 Server 独占的释放 API。

**现有单元测试的证据边界。** `tests/test_execution_service.py:7-47` 用内存 `Adapter` 模拟 fingerprint/counter 变化，可覆盖 stale revision 门禁逻辑，但不能证明 COMSOL Desktop 控件或 `ModelChangedHandler` 能捕获实际 UI 修改。工作区中未发现 UIA、Accessibility、COMSOL window identity 或 Desktop migration 的产品 adapter/测试文件。以上 fixture/unit 证据可用于软件门禁回归，不能提升为 T004/T051/T052/T054 的原生验收。

## 给主 Agent 的最小补齐设计

1. **先建立 fail-closed 的 Desktop 路由与能力模型。** 在独立 `comsol_mcp/_tools_desktop.py`（或等价模块）建立窄接口并由 `mcp_server.py` 注册：先做只读 `desktop.status`，再按目标平台实现 adapter。状态应区分 `AVAILABLE`、`BLOCKED_PERMISSION`、`UNSUPPORTED_CONTROL`、`WINDOW_NOT_FOUND`、`MODEL_BINDING_UNKNOWN`；无适配器或权限时不得返回虚构的空窗口或已绑定状态。把 GUI 权限探测与 `HOST_CONTROL` 授权分开记录；任何窗口写操作都要求有效授权和明确 `window_ref`。

2. **绑定句柄使用进程实例身份而非标题。** `window_ref` 应是短期、不可由调用者伪造的 MCP 句柄，绑定 OS window handle、进程 PID 与启动时间、登录/桌面会话、COMSOL 版本、窗口控件可访问性状态。`ModelRef` 同时绑定 server instance、model tag/ref、generation 和当前指纹范围。绑定操作必须在 UI adapter 能读到窗口当前 Server endpoint/当前模型身份，并与 Server API 对象交叉核验后才置 `desktop_binding_verified=true`；仅标题、路径或截图不能升级为已绑定。每次动作前重新检查 handle/PID 启动时间/目标控件，发现焦点、身份、权限变化就拒绝并写审计事件。

3. **保留未保存模型的迁移必须是副本流程。** `desktop.migrate_standalone` 先识别确切窗口和 standalone 当前模型，明确报告 dirty 状态；要求用户提供允许的保存策略。优先保存到新建、唯一、项目授权目录下的副本，校验保存产物，再让 API 将副本加载到 shared Server。迁移结果返回原 standalone 身份、新 server/model/generation 身份及保存摘要，原窗口和原模型保持打开。最后通过 UI 重新选中 server model 并以窗口控件状态和 API 身份双向核验。若 dirty 状态、保存结果或 UI 导入/绑定不可读取，整个动作 fail closed，不关闭原模型、不声称迁移成功。测试夹具用独特未保存标记确认内容保留、前后身份不同、取消/失败仍保留原窗口。

4. **取消只针对明确 job 与已验证的目标窗口。** 保留现有 queue cancellation 与 owned-isolated-process stop 语义；仅在 Windows UIA/macOS Accessibility adapter 通过 PoC 后，才把 `job_id` 绑定到指定 Desktop 的 Cancel/Stop 控件。调用后要用原 job 的 worker状态、引擎日志/求解状态轮询证明确实停止；超时、控件不可读或中间解状态返回 `UNKNOWN`/相应部分结果，不报 `CANCELLED`。Stop 若会保留近似解，应与 Cancel 的结果语义分开。无 GUI 路由时精确报告不可用，不向错误窗口发按键，也不终止共享 Server。运行中 API-only cancellation 的当前事实仍是“不支持”，软件 capability 可通过既有 route response 明确呈现。

5. **`blockOtherClients` 独占单列能力，不能复用 worker 文件锁。** 先从同版本 COMSOL Java API 和实际隔离实验确认方法、对象范围、断连语义，再决定是否实现。若可用，只包裹明确 model/server 句柄上的短同步事务：要求授权、限制作用域和时长、进入/释放事件落盘；`finally` 释放，断开/异常清理与 watchdog 可观察。若 SDK 不提供可证明的可靠释放/超时机制，保持 `UNSUPPORTED` 并让调用失败关闭。验收需独立第二客户端确认阻塞与恢复，不能靠第二个 Worker 被本地 endpoint lock 拒绝来替代。

纯软件部分（schema、句柄过期/重绑、授权门禁、状态分类、mock adapter 的负控、取消语义、异常清理逻辑、证据格式）可通过本地代码和 CLI 单测继续完成。T004 的窗口与模型双向绑定、T051 的未保存 Desktop 状态迁移、T052 的真实 UI 权限撤销/恢复和指定控件取消、T054 的 COMSOL 客户端真实阻塞/解除及长操作后可交互性，必须在允许访问的原生图形会话验证。不能用 API 输出 PNG、模型标题、mock adapter 或文件锁代替这些原生证据。

建议验收证据逐目标组合保存：窗口/进程实例标识（不泄漏凭据）、权限探测、UIA/AX 读取到的当前模型/Server、worker API 读回、前后 `ModelRef` 与 generation、操作/保存日志和哈希、job/engine 停止前后日志、block/unblock 时间及第二客户端可用性。MAC 权限只记录结果，不由 MCP 自动修改系统安全设置。

## 可执行缺口与真实 GUI 缺口

| 项 | 当前判定 | 后续可验证条件 |
|---|---|---|
| Desktop operation schemas/dispatch/production handlers 全缺 | 可在本机直接补齐；不是外部阻塞 | MCP 注册表显示各动作准确实现状态；未经授权/无 adapter 真实调用得到结构化拒绝；不可经 fallback 绕过 |
| Server API 有限 model identity、revision/fingerprint 门禁 | 有软件基础，范围有限 | 对允许的 server-side 属性记录指纹覆盖；mock 测试仅验证控制逻辑；所有未覆盖外部变化继续标 unknown |
| standalone 未保存模型读取/保存副本/加载与重新绑定 | 软件流程缺失；其中 GUI 源操作不能用纯 CLI 推断 | disposable 模型中加入仅存在于内存的标记；复制后 API 读回标记，原窗口仍在，双身份可审计 |
| GUI 权限、窗口/控件身份、错误焦点拒绝 | adapter/生产路由缺失；Mac 当前访问拒绝，其他目标没有此项通过证据 | 对每个平台实测权限开启/拒绝和 HWND/AX 重用/焦点切换，拒绝路径无副作用 |
| 运行中 native/GUI solver cancel | queued cancel 已有软件实现；运行中 native API 明确 unsupported；Desktop Cancel 尚无路由 | 对 disposable 小型真实求解指定目标控件，要求 job 与引擎均确认停止；共享 Server 从未被终止 |
| server-wide `blockOtherClients` 异常释放 | 生产接口缺失；OS worker endpoint lock 不覆盖该语义 | 同版本 API 隔离实验 + 异常/断连注入 + 第二客户端阻塞/恢复时间线 |
| T004/T051/T052 六组合矩阵；T054 作为 W19 依赖 | 当前 `NOT_RUN`；既有 Mac GUI 访问证据为 `BLOCKED_PERMISSION` | 按目标分别更新，不把未测目标继承成 PASS/BLOCKED；保存原生证据并独立复核 |

审计结论只适用于本次查看的工作树文件。没有把任何 fixture、目录元数据、用户手动操作说明或 API 图片导出认作原生 Desktop 验收。
