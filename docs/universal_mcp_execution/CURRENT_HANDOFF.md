# 通用 MCP 当前续跑入口 — r374（2026-10-28）

r373 Main3 原件保存在 `_state/universal-main-review/20261028-checkpoint-r374/prior-main3/`；11 文件候选待 Main 审，未 commit/push。公开 HEAD `21cdfb6f24606ffe8be1262e07395353613de76a`。

- **.15 源码软件**：hostbootstrap 空 runtime 数据修复及测试/版本五文件 Strong/Main 接受，[compact](evidence/windows-bootstrap-empty-runtime-data-0.1.15-20261028.json)；Main 2938B/SHA d56d3fe7c528560dff103e2519c32ab75f105f3493fe526b384c7e0a298df7fa6c。构件、安装和 runtime NOT_RUN；最新已接受构件及 Mac 软件 runtime 仍 `.14` / Y。当前 `.15` 五文件包含两个 package 文件和三个其他受控文件，不可沿用旧 208/209 等同 Y 的结论。
- **Windows joint**：25 JAR+1 PE/8 ancestors 的有限发现 Strong/Main 接受，[compact](evidence/windows-joint-jar-pe-discovery-20261028.json)。PE FileVersion/ProductVersion 6.4.0.293 是组件数据，engine 版本 NOT_ESTABLISHED；raw 成功行无 status，OBSERVED 为 handoff 摘要。无完整 EXE/JAR bytes，SDK/EOF 仅 source-backed，exact OS launch UTC 未独立序列化；非 native/qualification。session6597 terminal0 SPENT，不 poll/replay。
- **后续**：Main 审 11 文件后才可明确普通同步；发布状态以原 Tool 与 receipt 为准。Euler 正做 `.15` build source/data 受影响审查，无运行权；source ZIP 209 是应用重建输入，完整 tests/fixtures 以公开 Git 为准。

273/60/427、completed_groups=[]、heldouts NOT SELECTED、fresh final NOT STARTED；R6/R7 UNKNOWN 和旧 fences 逐值保留，Mac 历史 free 142901248B 不复采。
