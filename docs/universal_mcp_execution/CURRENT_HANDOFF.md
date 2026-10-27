# 通用 MCP 当前续跑入口 — r335

目标 IN_PROGRESS；273/60/427、native、save + fresh Worker、两个未预设 licensed 任务族与 fresh 最终 Reviewer 均未通过；completed_groups=[]、heldout NOT_SELECTED、fresh final NOT_STARTED。r334 Main3 原件保存在 `_state/universal-main-review/20261027-checkpoint-r335/prior-main3/`，其 Stage B pre-dispatch 指针为历史快照。

- **T008/.13**：canonical 0.1.13 source与软件已 Strong/Main 接受；postmerge 15/83/1 各原 Tool0，version/coverage check 0，52第三方依赖和 ledger 不变。[软件紧凑证据](evidence/t008-matrix-signature-0.1.13-software-20261027.json)，Main 接受 `_state/universal-main-review/20261027-t008-matrix-descriptor-integration-r1/MAIN_INTEGRATION_ACCEPTANCE.json` 3389B/SHA dd141295667a5035052124ab75a098f9694532d4a08cf07f6c6f66c479ee8e36。六项冻结 T008 断言、.13 构件/安装/实际MCP/COMSOL/native 均 NOT_RUN；.12 构件与实际运行只属于旧版。
- **Windows known nonzero**：R5-r2 B0/B1 known0、固定X exit 3221225794/0xc0000142，wait0 为signaled，双EOF空、Job0、child/Job close；三文件9896B。[失败证据](evidence/ordinary-x-windows-known-nonzero-20261027.json)已由Strong/Main限定接受，Main重核42 inputs/276026B。根因未知，parent OS数值退出未观察；不能推测当前活态或称native PASS。02ed GO/collector已spent，不query/cleanup/retry/adopt；旧UNKNOWN owners全部fenced。
- **R6**：仅source PREP；`_state/universal-main-review/20261027-ordinary-x-r6-nonconsole-MAIN_DECISION.md` 4046B/SHA e258cf7ce5493b863ec2c6ceee6a8f28e27705bcbdeb80a75c1e8cc75e0aab81；winexe subsystem对照是假说，Job/policy不变，无native release。

当前公开HEAD a214e1ca；.13源码与两份紧凑证据待11文件ordinary同步。Core上次strict-free 142901248B低于2GiB/1GiB，非当前新测、不复采。下一步 Main 验收并发布11文件候选，继续R6 source review；不改 frozen required。
