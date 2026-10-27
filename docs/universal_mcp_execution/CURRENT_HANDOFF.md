# 通用 MCP 当前续跑入口 — r368（2026-10-28）

r367 Main3 原件保存在 `_state/universal-main-review/20261028-checkpoint-r368/prior-main3/`。公开 HEAD/build snapshot `c4b733750ebb1ca27fc62462976d67690a1baf12`，`.14` code `b642eab24e889efeb217344019cb71077cf93cd5`。

- **.14 构件字节**：五个实际构件统一 Strong/Main 限定接受，Main 5104B/SHA 63ef813df961e5d5dc36e6207e8b257b66a130aaac3443d9998c00993df7fa6c；513 inputs/304880147B、2909 checks。详 [compact](evidence/distribution-0.1.14-20261028.json)。209 HEAD allowlist/sourceZIP、203 release snapshot、200 package、206 wheel 分别计数；source ZIP 独立 assembly，不是原 builder 第五输出。Intel Rust b749 文档及 OpenSSL label gap 的既有限界保留。
- **实际 runtime 与发现**：最新实际正常安装/public MCP 软件 runtime 仍 `.13` / 53c；`.14` 安装/MCP/native NOT_RUN。R8 限定 25 项只读发现已接受，[compact](evidence/windows-readonly-finite-discovery-20261028.json)；A3 仅 policy canary 接受。R6/R7 original UNKNOWN fences、R2 FAIL、r1 自动审批拒绝及旧历史不变。
- **后续**：Main 审本阶段精确五文件，并确认 `.14` Mac 材料 HEAD 依赖后明确普通同步；实际发布以原 Tool 和 receipt 为准，不预先认证成功。Euler 仅 Mac source/data，Aristotle 仅本地 qualification source PREP，无新远端/高影响权限。

273/60/427、completed_groups=[]、heldouts NOT SELECTED、fresh final NOT STARTED；Mac 历史 free 142901248B 不复采，所有已终态 session 不 poll。
