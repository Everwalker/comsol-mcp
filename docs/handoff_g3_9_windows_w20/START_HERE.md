# G3.9 工作包入口

当前固定源：`886affadc83477e940238c723edd569d00559985`，tree `b9861e5dc407f2aff934d9438fa33ef631df06a1`。本包是**联网恢复包**，不包含完整源码镜像；首次恢复需要访问固定公开 GitHub 提交。

## 开始
在 Windows 的新目录解压并让 Agent 打开本包根目录，复制 GOAL.txt 即可。不需要旧仓库、旧聊天或旧工作包。
Agent 首先运行：
```
python tools/verify_package.py
python tools/bootstrap.py
python tools/audit_repository.py --repo repository --output repository/docs/handoff_g3_9_windows_w20/SOURCE_AUDIT.json
```
先恢复固定提交，然后建立新 venv 和运行实测；不要从旧 .venv/.class/回执推断环境可用。
Windows 可运行 START_WINDOWS.ps1（仅恢复、核对和只读盘点，不启动 COMSOL）。没有 Git 时 bootstrap --method archive；没有 Python 时先配置受信任的 Python 3.12 x64，不替换系统 Python。

## 文档
- NEXT_GOAL.md：当前执行顺序、边界、成功与受阻停止条件。
- REVIEW.md／FINDINGS.json：最新审查与具体修补要求。
- ACCEPTANCE.md／ACCEPTANCE_CASES.json：本轮验收定义，不是现有 PASS。
- ENVIRONMENT.md／WINDOWS_PLAN.md：双版本与权限范围。
- RESTORE.md：无旧目录恢复和不可恢复内容。
- tools/review_probes.py：隔离函数诊断，不能当 COMSOL 实测。
- tools/check_acceptance.py：文件/协议结构审计，不证明真实原生执行。

恢复后的当前阶段文件在 `repository/docs/handoff_g3_9_windows_w20/`。继续开发时用仓库内这个副本记录进度，不必复制旧工作包。

清理只指旧项目工作目录。保留 COMSOL 安装、许可证、未保存模型以及未提交科学数据；这些不是公开仓库的一部分。
