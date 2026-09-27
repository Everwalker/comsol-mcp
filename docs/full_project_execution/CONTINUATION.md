# 长任务续跑，不需要每阶段一个Goal

状态位置：恢复后的 `docs/full_project_execution/state/`。同一Goal在新会话中继续，先读PROGRESS.md、RESUME.json、TASKS.json，再查看必要原始日志，不重读全部历史对话。

安全检查点：一批代码/测试完成、一个原生job结束并保存证据、明确阻塞、或即将compact/会话结束。写当前源码commit+dirty摘要、当前任务/已完成/下一个具体命令、有效run/model/operation/job ID及私有配置引用（不是token值）。使用临时文件+原子替换，保留最近有效状态，避免半写。

恢复规则：
- 先检查是否仍有active/UNKNOWN作业；对原job查询/reconcile，不能重新发起同一求解。
- 验证原有PID/创建时间/Server epoch；旧数字不能当仍是同一实例。
- 确认目录及源码状态；已有修改先保留。不要再运行bootstrap覆盖repository，不再次init覆盖state。
- 从next_action继续；若它因外部环境阻塞，挑选不依赖阻塞的未完任务。
- 候选通过开发自测后进入FINAL_REVIEW；按Reviewer明确问题整改，而非重开总设计。

主Agent可以按安全checkpoint提交并按已有授权同步，随后继续。本文件不创建自动化、守护Agent或无限时长会话，也不承诺Host会在用户关闭后自动继续。工具/context配额限制时保存状态，报告“暂停可续跑”，不是“项目完成”。

结束需核对：PROJECT_ACCEPTED、REVIEW_CHANGES_REQUIRED、ACCEPTANCE_BLOCKED、REVIEW_BLOCKED或PAUSED_RESOURCE_LIMIT。前四者含真实范围；只有满足原全部适用要求和独立最终批准可PROJECT_ACCEPTED。
