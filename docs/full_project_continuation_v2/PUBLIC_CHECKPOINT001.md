# 当前软件检查点 001 — 2026-10-11

Goal仍ACTIVE，原W01–W26/272动作/60测试及最终统一独立Reviewer验收尚未完成。此检查点用于同步已经审查的软件进度；不能替代原生、科学、安装或项目验收。

当前源码基线60464c85b429e649f4f80484473999e5365ad96a；修改范围为参数IO/预览与Windows安全发布、W24九份已保留科学源修正、W25限定软件集成、模型dependencies/package/rebuild_target与artifact.verify、六项solver/study/result正式路由和保存模型读回工具修正。原规划/历史UNKNOWN/原始进度表均保留。

实际软件证据：参数/预览102 focused及354影响回归PASS；W24 259 PASS；W25 188 PASS；模型交付249 PASS/10 SKIP/20 subtests PASS；真实软件JVM Worker loopback41 PASS/1 SKIP；solver routes r17 232 PASS/0 SKIP。各组范围可能重叠，不能相加成全项目测试总数。Skip为9项Windows kernel、1项旧dev1 archive路径缺失、1项Windows owner语义；没有标为PASS。

模型交付owned5文件freeze及Root检查在evidence/model_delivery；solver r17 combined18文件freeze及Root检查在evidence/solver_routes。共享接口的当前哈希由后者绑定，P0/W25/W24旧检查继承其未受影响范围；独立最终Reviewer NOT_PERFORMED。

原生结果：保存MPH只读重开003已通过（零求解限定范围）。W23新科学活动001与002均在fixture_build失败，study.run提交0；历史UNKNOWN/safe_retry=false不重放。001所有专属进程已清理，NOT_SAVED/清理超1800s预算保留；002所有专属进程/监听已完成安全清理，含cleanup1356.596s<1800s；原错误清理receipt仍保留，正定位Invalid_property_value具体根因。两次构建失败不证明科学功能通过。W24科学native、Windows实机、Intel Mac6.4、W25真实窗口与W26当前构件/安装/离线/完整恢复仍OPEN；两项Mac6.3用户skip继续保留。

剩余源码工作：7个dotted model基本动作及可信code.recipe_register/run，function.validate/selection.rebind/definition.mapping_validate，geometry/material/PDE/mesh剩余动作，report/evidence_bundle/publish，以及真实COMSOL求解日志producer。solver.log_read现仅完成真实durable job events/已记录异常链读取，不能把全路径NOT_CAPTURED的求解输出称为日志完成。

唯一活动指针是本机docs/full_project_continuation_v2/state/RESUME.json；私有DB、模型、原生raw日志与进程清单保留本机，明确排除公开Git和检查点归档。当前GitHub同步状态在同步收据中记录，本文不预先声明发布成功。
