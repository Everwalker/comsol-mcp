# 主开发＋独立Reviewer，固定终点

1. 主Agent核对源和冻结要求，直接设计、实现、测试。最先打通一个“参数→真实求解→结果→case记录”链，再复用于其他W21交付。
2. 通过Antigravity/当前Host实际支持的子任务或独立上下文机制建立Reviewer。角色文本/随机session_id不证明独立；记录Host实际给出的会话定位即可，不再建立新签名/证书系统。
3. Reviewer先读冻结合同和源码，再看主Agent摘要；独立选取原要求相关反例并复测。默认不改生产代码，可在review测试目录写反例。
4. 每轮候选由真实commit＋必要工作树清单定位；已有source_snapshot可复用，不要求新证明平台。只纳入代码/依赖/schema/runner/基准，避免证据文件自引用。
5. 输出四种结论之一：APPROVED_SCOPED；CHANGES_REQUIRED（必须对应原合同或A2严重问题）；BLOCKED_ENVIRONMENT；REVIEW_BLOCKED。不能以“还可更好”拒签，也不能因摘要声称完成就批准。
6. 主Agent修复后提交新候选。Reviewer仅重测相关问题及影响，不每轮重跑整个仓库。相同问题第二轮仍存在，改用最小生产复现后修根因；不设置N轮自动通过。
7. 未访问的Windows版本/Host/许可不能写通过。独立子会话不可用时主Agent仍可开发，最终审查标REVIEW_BLOCKED，不能自审冒充独立批准。
8. 原五交付通过、原依赖缺口关闭或被证据确认不适用、无A1/A2时必须批准。待办不必清零。交付后停止W21，不自动W22。
