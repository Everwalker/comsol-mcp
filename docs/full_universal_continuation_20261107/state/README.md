# 唯一持续进度

将 `PROGRESS_TEMPLATE.json` 复制到恢复仓库的 `docs/universal_mcp_execution/state/FULL_COMPLETION.json`。T02 更新现有 RESUME/CURRENT_HANDOFF 让它们指向该文件；不让多个“下一任务”互相冲突。

状态：
- TODO：尚未核对/实施。
- RUNNING：正在实施或验证，记录真实 job/操作的身份和当前所有权。
- IMPLEMENTED：相关实现已完成，验收证据未齐；不能冒称 native 通过。
- BLOCKED：写具体 blocker。若代码/接口已完成，可同时 `implementation_done=true`，让下游软件任务继续。
- DONE：本执行步骤的产物和判据有 evidence_refs；不是整个产品 PASS。
- NOT_APPLICABLE：仅在原合同允许且 Reviewer 认可具体适用理由时使用；不能自删 required。

各平台记录在 target_checks：一个 target 缺机器，其他 target 仍能推进。T47 的 T43/T46 两个 Windows 安装/核心原生检查必须实际 DONE，不能用实现完成替代；它不等待 Mac 或全部生命周期/Desktop 验收。T50–T52 的最终审查必须覆盖所有适用目标。

T48 真实尝试结束后记 execution_complete=true 并保留 evidence_refs（失败也可以）。该标记只放行 T49 修复支路，不能代替 T48 通过或最终验收。问题修复、候选重冻和等价变体通过后再关闭相应验收项。

建议保留每组一个证据 bundle，里面按 Txx 记录代码/构件、平台/build、命令或公共 MCP 调用、实际结果、限制与原始文件引用。不要求 52 份独立报告。没有必要为了目录好看反复归档旧历史。

`next_tasks.py` 只给导航建议，不执行任务，不替你决定真实权限，不证明独立性。blocked 解除必须有环境/代码/输入变化；不要通过改状态伪造就绪。
