# 连续执行与恢复规则 V3

唯一当前执行指针：`docs/full_project_continuation_v3/state/RESUME.json`；原项目矩阵继续放原位置，另指向continuation最新checkpoint。

每个安全检查点只记录最小信息：当前规范源码/候选集成状态、任务、实际测试/产物引用、现行runtime/活job发现状态、原UNKNOWN、阻塞、next_action、modified paths。不要复制所有历史正文到每个矩阵格。

短Goal重复发送时：先读状态→核对源码/活作业→继续next_action，不再bootstrap已有repository、不把缺本地输入当可重放旧job。旧active_jobs=[]仅属于旧快照。

软件修补、自测、定向native可以连续进行，默认不逐helper创建独立review任务。必要的运行权限与一次性执行安全检查保留；最后统一Reviewer不表示可以绕过这些保护。

会话限额暂停：写明当前进度和下次命令，不能说无人触发后会后台自动继续。还有安全工作就推进；不可突破Host限制。

项目源、公开科学产物和私有运行数据分开；磁盘不足时先停止新大写入、列盘点/迁移方案，不自动清空/private/tmp或失败目录。有限缓存可重建，但删除仍只在用户明确范围内执行。
