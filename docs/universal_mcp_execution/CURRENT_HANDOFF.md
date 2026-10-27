# 通用 MCP 当前续跑入口 — r357（2026-10-28）

r356 Main3 原件保存在 `_state/universal-main-review/20261028-checkpoint-r357/prior-main3/`。公开最后受理 HEAD `bea799f1a075258be0b65fb8d4109e58b11ecf14`；`.14` 源码已本地 commit `b642eab24e889efeb217344019cb71077cf93cd5`、tree `330d183a05dee04430f2694cccfa98ef364f8d20`，尚待普通同步。

- **Windows netsh owner .14**：Strong/Main 源码软件接受 [compact](evidence/windows-netsh-query-owner-0.1.14-20261028.json)，focused 199、selected 39、原完整范围 80 PASS 为独立计数，旧失败保留。`.14` build/install/Windows native NOT_RUN；实际已接受构件和 Mac 软件协议仍是 `.13` / source `53c16d0484b8971385ef56397bc96a28c34beea4`，session41807 SPENT。
- **phase1 R5**：已接受的有限 SSH 认证拒绝 terminal255 失败 [compact](evidence/windows-readonly-inventory-auth-refusal-20261028.json)；inventory NOT_OBSERVED、target 当前 UNKNOWN_NOT_QUERIED、native NOT_RUN。原 once SPENT，不 query/retry/cleanup；不能推断远端从未启动。R6 仅新 nonce 与现有身份绑定的 source/data 窄范围独立复审，无 runtime lease/claim。

下一步 Main 核本地源码与五文件候选后明确普通同步；实际发布状态以原 Tool 和 receipt 为准。273/60/427、completed_groups=[]、heldouts NOT_SELECTED、fresh final NOT_STARTED；T008 六项 recipe NOT_READY/NOT_RUN。旧 UNKNOWN/SPENT fences 保留，Mac 历史 free 142901248B 不复采；新 service/SYSTEM/WFP effects 仍须具体材料经 Main 审后获人类授权。
