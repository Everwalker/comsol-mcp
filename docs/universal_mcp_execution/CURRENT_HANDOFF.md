# 通用 MCP 当前续跑入口 — r371（2026-10-28）

r370 Main3 原件保存在 `_state/universal-main-review/20261028-checkpoint-r371/prior-main3/`；本次六文件候选等待 Main 审，未 commit/push。公开 HEAD `39eaeb46a1ee751f419563f8c409a097737a5527`；已接受构件及运行证据绑定 source Y `c4b733750ebb1ca27fc62462976d67690a1baf12`。

- **Mac .14**：Strong/Main 限定正常安装及固定公开 MCP 软件协议接受，[compact](evidence/public-mcp-0.1.14-macos-arm64-20261028.json)；Main 4672B/SHA a40628ced6f21db3ab2720800c29bafabc1d87d8c0a298df898b13efbb74c056。实际 11 responses/112 listed，discover NOT_FOUND、doctor PASS_STATIC、daemon wait -15；stdio wait0/initialized 仅 source-success-gate 推导。SourceBase 前后匹配仅覆盖 normal helper 调用；session5914 terminal0 SPENT，勿 poll/replay。非 COMSOL/JDK/Core/native 或全矩阵验收。
- **Windows**：six-seed 有限发现 Strong/Main 接受，[compact](evidence/windows-six-seed-readonly-discovery-20261028.json)，task SPENT；25 JAR source 与 PE version entity 架构已接受，26 files/8 ancestors 仅 source/software 准备，candidate/version UNOBSERVED、无 runtime。旧 R6/R7 原 owner UNKNOWN fences 保留。
- **源码与发布**：README 中英由过时的未构建陈述改为已接受构件及限定运行证据；allowlist 209 项中 208 项仍与 Y 一致，README 唯一变化，200 package 项不变；原构件仍绑定 Y。六文件拟普通同步，实际发布以原 Tool 和 receipt 为准；本快照不预先认证推送。构件 archive 字节保持私有。

273/60/427、completed_groups=[]、heldouts NOT SELECTED、fresh final NOT STARTED；Mac 历史 free 142901248B 不复采，所有终态 session 不 poll。

Windows 完整 bootstrap 尚不能装配：真实 CPython NuGet 1322 个 tools 文件中有 27 个合法零字节文件，现有 hostbootstrap 正 size 谓词拒绝。Euler 正按 Main 决策做私有源码/测试修复，仅限 runtime `python312/tools/Lib` 和空 SHA；必需文件正 size 与 SDK guards 不变。未集成生产源码或发布 `.15`；PE source 材料继续准备。
