# Master Goal：完成通用 COMSOL MCP 的全部既定范围

## 目标与边界

在固定基线 `cd2c9cdc1d862e335851bb7b2b254f01f7c8609c` 上持续开发，完成 `RELEASE_DOD.md` 和 `config/UNIVERSAL_DOD.json` 中的 U01–U12，并按 `config/TARGETS.json` 交付。主 Agent 直接开发；最终由独立子 Reviewer 对同一冻结候选统一审查、提出有依据的缺陷，主 Agent 修复后复核。

“全部完成”指这份有限合同全部完成。产品是可供强模型组合使用的通用 COMSOL 操作系统：通用领域接口、实际版本的 API/属性自省、受控项目 Java、模型/作业管理、真实结果和恢复交付相互配合。无需为每个 API 增加 wrapper，也不承诺所有许可证模块、任意科学课题或任意 Desktop 接管均可用。

W22–W24 的 VCSEL、光纤/胶等案例科学终点、更多 recipe、额外平台和无界性能优化是后续可选待办，不能重新塞回 required。两个正式 heldout 家族和现有平台矩阵是有限验收，不扩为不断追加新题的大平台。

## 从已完成内容继续

- 固定续作头：`cd2c9cdc`；软件评审对象：`0827fb9`；main 仍是 `c6e078ab`。恢复后工作分支不要直接退回 main。
- Java source snapshot v2 的 bytes/hash/协议/runner/FIFO/事件及连接断言修复已有独立软件 ACCEPT。基线没有新增生产代码变化时复用此结论；本次重点是当前构件的真实已安装入口/Worker/native 运行。
- `references/JAVA_REVIEW_*.md` 是已发布评审记录；其中未发布的临时路径不能成为接续依赖。
- `references/DOD_STATUS_AT_PIN.json` 是旧聚合状态，不能将 9 PARTIAL/3 NOT_RUN 当成各子功能从未实现；先按源码和证据差异承接。
- 仓库旧 `RESUME.json` 仍让旧 Agent 修 Java。T02 要建立唯一当前入口，保留历史但消除错误 next_action；不把全局 REVIEW 的 NOT_RUN 改成 PASS。

## 执行循环

1. 按 T01/T02 恢复与承接后，读取任务表和已落盘进度；选一个依赖已满足、能推进真实能力的任务。
2. 先定位现有实现与证据。已满足的部分直接引用其范围和身份；只实现缺口，避免重写成熟模块。
3. 实现该项产物，运行能决定其完成与否的有限检查。测试必须覆盖真实风险，禁止重复运行没有输入/代码/环境变化的同一失败。
4. 写明任务状态、候选 commit/构件 hash、适用平台、实际命令/关键结果、证据位置。实现已完成但原生验证受阻时，记录 implementation_done=true 和具体 blocker，让下游软件任务继续。任务表的 DONE 要满足本步骤的实际判据，但仍不等于最终产品通过。
5. 自动推进下一就绪任务。可先做软件/构建/文档/权限逻辑，平台受阻不挡住无依赖工作；同一个 COMSOL Server 的写入仍须串行。
6. 生产实现、T43 两个 Windows 正常安装行与 T46 两个 Windows 核心原生行就绪后，进入 T47–T49 冻结候选及两类 heldout；不必等待 Mac 或全部生命周期/Desktop 验收。T50–T52 的最终全局判断仍必须补齐全部适用验证。T48 出现实际失败即记 execution_complete=true 和原始证据，保持未验收并立即进入 T49 修复，不要求失败任务先通过才能修。
7. 每次上下文压缩/进程重启，从持久进度的下一任务恢复，不重新调查全部历史。提交和阶段报告不是最终停止点。

`TASKS.json` 是有限执行步骤，不是额外的几十个审签门槛。组间可并行推进允许的独立工作；主 Agent 无需再雇一个 developer。除必要缺陷复核外，不为每一文件、命令或小步骤新增独立 Reviewer 审批。

## 进度落盘

仓库保存 `docs/universal_mcp_execution/state/FULL_COMPLETION.json`，由 `state/PROGRESS_TEMPLATE.json` 初始化；同时更新简短的 `CURRENT_HANDOFF.md` 和 `PROGRESS.md`。每项记录：

- id，status（TODO/RUNNING/IMPLEMENTED/DONE/BLOCKED/NOT_APPLICABLE），实际产物与 evidence_refs；
- 当前候选身份、已验证环境/范围；
- 若阻塞：具体缺少什么、受影响后继、解除条件、可继续的下一项；
- 若不适用：合同允许的具体理由，经 Reviewer 认可。用户跳过只适用于 TARGETS 中对应 native 行，不能删除架构构建责任。

不要复制旧实时 token、审批状态、lease 或进程所有权。历史 UNKNOWN 保持资源级围栏；全新独立任务按当前有效权限执行。不要从存量记录猜测资源已停止，也不要无限轮询原所有者。

`python3 tools/next_tasks.py --state <FULL_COMPLETION.json>` 只读展示任务 readiness；它不能证明执行、独立性、原生结果或全局 PASS。没有进度文件时默认所有步骤待承接，不会自动标记已通过。

## 已安装入口与第一条真实闭环

先在一个可用活跃目标完成：从当前构件源外正常启动 MCP → 新项目及绑定模型 → 现场写新 Java 并执行 → 小模型求解 → 真实数值、单位和同解图像 → 保存 → 同 COMSOL 版本的新 Worker/新目录打开存储解。

这是一条开发验证路径，不是正式 heldout。可以用小而可检查的任务，不需追求复杂领域科学收敛。后续对通用对象、自省、扫描、结果、恢复和交付逐项补齐；第一条闭环完成后自动继续，不结束整个 goal。

## 统一验收

遵循 `ACCEPTANCE.md`，每一 required 合同项都有当前适用的实现/执行路线与证据。至少覆盖 Windows x64 COMSOL 6.3、Windows x64 COMSOL 6.4、macOS ARM64 COMSOL 6.4 的要求；三架构构件交付仍保留。跳过行按原约定公开说明。

Reviewer 必须独立于主 Agent 的实现身份，阅读候选代码和真实证据，自己选择有限反例与两类未预设任务。主 Agent 不得代写“Reviewer 通过”。如果当前会话工具不支持子 Reviewer，可以准备完整候选与审查包，完成所有可推进项，再明确等待独立审查条件，不能自我认证。

Reviewer 提出的阻断缺陷须有可复现事实、受影响 U 条款/范围、具体修复要求。P1/P2 与任何影响 required 的正确性、授权、数据完整性缺口必须关闭；纯风格/未来增强归后续待办。低优先级不自动豁免真实合同违约，P3 也不自动扩成新发布门槛。

## 允许的终态

**成功：** 所有 required/适用目标完成，覆盖映射无未解释缺口，独立 Reviewer 接受同一最终候选，写 `UNIVERSAL_CORE_VERIFIED_SCOPED`，交付来源、构件、安装说明、适用平台、证据摘要和真实限制。明确未完成专项科学目标仍为可选待办。

**外部阻塞：** 所有无需缺失条件的工作均已完成，剩余项确实只能等待平台、许可证、凭证、资源或独立 Reviewer。写 `WAITING_EXTERNAL`，逐项列出阻塞、已完成成果、恢复条件及下一条具体任务，不声称“全部完成”，不空转重试。

不得因一个阶段 ACCEPT、测试全绿、一次 commit、一次工作包输出或旧记录繁杂而提前停止。不得删除 required、把 BLOCKED/UNKNOWN/NOT_RUN 改为 PASS 来满足目标。
