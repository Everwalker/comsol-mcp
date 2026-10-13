# 主Agent直接开发，最终独立Reviewer统一验收

主Agent负责一份规范源码的开发/集成/自测，按原合同决定普通任务推进。不需独立Developer，旧记录中Native Sol/Model Sol仅用于找来源，不强制恢复过去的多子Agent分工。

开发时及时做focused和受影响测试，已验证结果按来源/影响继承。必要高风险设计可提前求助Reviewer，但不每个helper/微小改动都创造新的审批关卡。全量候选和当前可执行native结果准备好以后再进行统一最终Review。

最终Reviewer必须由宿主创建独立上下文。主Agent不编辑其批准文件、不自造session ID；不能创建独立上下文时标REVIEW_BLOCKED。Reviewer分批检查完整合同、有效scope、集成差异、producer与原始观测、平台安装和科学计算，自行挑选正负控并真实复测；不能仅运行汇总器。

收到CHANGES_REQUIRED，主Agent按原要求修复、提交新快照；Reviewer只重查受影响范围并保留其余审查覆盖。每个必需问题写出原要求/具体重现/实际和预期/影响，不以“还能更好”阻断。没有固定轮数自动批准，也不要求非阻断待办清零。

最终统一结论：APPROVED / CHANGES_REQUIRED / SCOPED_REVIEW_WITH_BLOCKERS / REVIEW_BLOCKED。部分范围批准不得写成全项目接受；Mac6.3 USER_REQUESTED_SKIP不等于PASS。source-only review不能升级为目标机native。
