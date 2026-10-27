# 当前进度 — 2026-10-27（r332）

通用 MCP 目标 **IN_PROGRESS**。273 actions / 60 tests / 427 子断言、适用 native、真实保存和 fresh Worker 重开、两个未预设 licensed 任务族及 fresh 最终 Reviewer 均未通过；completed_groups=[]，heldout NOT_SELECTED。

Mac 0.1.12 实际公开 MCP 阶段已获 Strong/Main 限定接受并普通非强制发布；公开 HEAD/origin main/FETCH_HEAD `b230fe16c8f418d6bbcfd4e9713b5a89cb461d4e`，四 API blobs 已独立字节回读，207 应用源码 blobs 与 d774ec0e 一致。该验收不代表 Core/native、license 或全目标通过。

**Coverage 证据对齐已获 Main 限定接受，四文件发布候选待 Main。** 既有 ledger 增补 actual MCP 的九项 notes 与共享指针；Main 核整个 JSON 精确等于10项新增/18基线 guards及56个静态 API 叶路径元数据刷新，全部执行和断言状态、273/60/427 不变。旧 `--check` exit2/API_SNAPSHOT_CHANGED 保留，refresh 与最终 `--check` 原 Tool 均 0；不等于 native 或 M1 全通过。ledger 2950510B/SHA 00af26d7ab492a49521109a4c4a9b2a79a36e3fe19cbbafbd268e3922b095858；[Main 接受](_state/universal-public-mcp-readiness-prep-20261027/owner-public-coverage-delta-r1/refresh-20261027-r1/MAIN_COVERAGE_DELTA_ACCEPTANCE.json)。

Windows R5 初版因 fstat 仅校验 size 未绑定 opened file 被 Huygens 指出 1 P2；新 R5-r2 已修复并完成 fake/full static/freeze，seal 942570fb5dc35881fcac637b34dfb5460ddb0ededd3fe7dd9ac611330d1a12af，manifest dc023758604bed2626c869b0754d6b3fe233c890202aba4a87960384bd8c53ff，Huygens affected复审0 P1/P2，Main核110 inputs/568669B无漂移并限定接受源码。WinPath/fd 差异属同版 CPython 静态兼容依据，不是 Windows 实际运行证据；reserved remote 02ed 未探测/创建/授权，无 R5-r2 runtime release。

原 R4 7ea UNKNOWN owner、9681 等继续 fenced，R4 collector 已花费，不再读取或 SDK/GO/cleanup/retry。Core 上次 03:30Z strict-free 142901248B 低于 Server 2GiB/Worker 1GiB 门槛，记录为 BLOCKED，非当前新测，不复采。

下一步：Main 发布本 coverage 证据对齐候选；R5-r2源码已接受；按持续同范围权限由Main决定fresh runtime。详 RESUME.json.current_checkpoint_r332。
