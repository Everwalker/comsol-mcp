# G3.8：W20 真实验证收口（Windows COMSOL 6.3 / 6.4）

**直接在 Agent 中打开本工作包根目录，发送 GOAL.txt 内容。**

本包锁定 `Everwalker/comsol-mcp@59d741d6e2514925fcabe3eb8fa7a1661e309779`，Git tree `1d60b1fc4d7d5a016dac1bf6a1bff2bb882384f0`。这是联网恢复型工作包，不是源码离线镜像；`repository/` 首次恢复生成。没有把尚未下载的仓库、未跑过的 Windows 或 COMSOL 验收冒充交付物。

## 本轮决定
保持已有 Windows 双版本引擎、W13–W19 和持久作业成果；纠正合成证据被标为实机通过的问题，修复 W20 的无输入 PASS、伪规则执行、报告和 ACL 边界，完成真实模型与独立解析基准验证。**不进入 W21。**

## Agent 执行顺序
1. 阅读 `NEXT_GOAL.md`、`REVIEW.md`、`ACCEPTANCE.md`、`WINDOWS_PLAN.md`。
2. 运行 `python tools/verify_package.py`。
3. 运行 `python tools/bootstrap.py`，只在新目录恢复固定公开源码。
4. 运行 `python tools/audit_repository.py --repo repository --output repository/docs/handoff_g3_8_windows_w20/SOURCE_AUDIT.json` 校验恢复回执；回执位于 `repository/docs/handoff_g3_8_windows_w20/RESTORE_RECEIPT.json`。
5. 用 `tools/windows_inventory.py` 只读盘点本机；在新虚拟环境中开始开发与测试。
6. 进入修补前可运行 `python tools/review_probes.py --repository repository --output local_review.json`；它只运行隔离诊断，**不是验收**。

Windows 可用 `START_WINDOWS.ps1` 做恢复和盘点；脚本本身不安装软件、不启动 COMSOL、不修改防火墙或许可证。也可以由 Agent 逐条调用 Python，不要求 PowerShell 脚本可执行。

## 清理旧文件的边界
先恢复成功，再清理旧项目。工作包不能恢复未提交、ignored、Desktop 未保存的模型和实验数据；不含 COMSOL 安装、许可证、账户凭据。保留它们或从自己的合法备份恢复。历史运行的 PID、token、prefs 不可复用。公开源码中已有的测试构建器可生成新证据，不能冒充旧文件同 SHA。

详见 `RESTORE.md`。本包的哈希检查只证明文件一致；不能证明原生程序实际运行或科学结论正确。
