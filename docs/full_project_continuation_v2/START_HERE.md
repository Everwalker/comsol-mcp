# 全项目持续完成工作包 V2 — 从 cutoff066 继续

锁定公开提交：`60464c85b429e649f4f80484473999e5365ad96a`，tree：`9ed8d11dc0f4d04830823821cc5a73fa81d1ff67`。不是从旧W22包重来。

本包为**联网恢复型**，不包含完整仓库源码镜像。恢复器从固定公开GitHub提交获取所有受支持的tracked文件并验证Git tree/blob；Git LFS指针和未提交文件不等于内容已备份。GitHub原生连接器能够读取文本，但本次制作环境无法下载完整归档；没有伪装成已完成原生网络恢复。

在 Agent 中打开本包根目录，粘贴 GOAL.txt。主 Agent 直接开发，不需要另设 Developer；最终统一独立 Reviewer 验收。

```text
python tools/verify_package.py
python tools/bootstrap.py
python tools/init_resume.py --repo repository
python tools/status.py --repo repository
```

已有 Python 即可恢复，Git优先；没有Git可用 `--method archive`。`START_WINDOWS.ps1`仅提供相同顺序的可选入口，不安装软件、不改系统执行策略、不启动COMSOL。

**不要运行旧 init_completion.py 来重建零进度表。**本版只核对原需求并索引现有 TASKS/ACTION_COVERAGE/ACCEPTANCE_MATRIX，已有原始状态字节不会被覆盖。新的轻量指针位于 `repository/docs/full_project_continuation_v2/state/`；旧目录保留为权威历史来源。

建议先确认新目录能恢复，再清理旧项目。当前仓库明确提到九处保护中的科学工作树修改；未提交内容不能从GitHub恢复。清理边界见 CLEANUP_GUARD.md。不要删除许可证、唯一科学数据或仍被进程使用的目录。

当前已记录两类Mac6.3原生验证被用户跳过，不重新要求该资源；Windows两版、Mac6.4及三架构软件交付继续按原范围推进。跳过不表示通过。
