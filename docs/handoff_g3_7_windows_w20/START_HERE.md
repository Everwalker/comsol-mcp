# G3.7：Windows 生产链收口 → W20 分层验证

## 直接使用
在 Agent 中打开本工作包的根目录，发送 `GOAL.txt` 的一行命令。主任务为 `NEXT_GOAL.md`。
无需旧项目、旧聊天、旧虚拟环境或旧工作包。联网恢复固定公开源码后，才执行新的开发与实机验证。

```powershell
python tools/verify_package.py
python tools/bootstrap.py
python tools/audit_repository.py --repo repository --output repository/docs/handoff_g3_7_windows_w20/SOURCE_AUDIT.json
```
也可运行 `START_WINDOWS.ps1`，它只负责已有解释器发现、完整性核对、恢复与只读盘点，不安装软件、不启动 COMSOL。
工具的实际参数以 `--help` 为准。程序需要 Python 3.10+；本轮原生 Windows 建议新建 CPython 3.12 x64 环境。

## 包的性质
这是**联网恢复型交接包**，不是完整离线源码/依赖镜像。源码来自 `PIN.json` 的公开固定提交，恢复时核对 Git tree 与全部文件内容。
默认恢复到 `repository/`；已有目录会被拒绝，避免覆盖用户修改。无 Git 时可用 HTTPS archive，但若归档缺少 export-ignore/LFS 对象导致 tree 不符，必须改用 Git，不能降低校验。
Git 路径从固定提交建立 `handoff/g3_7_windows_w20` 分支；归档路径无历史，发布前须安全重建同一固定提交的 Git 工作区并迁移改动。

## 本轮边界
先处理 Gate A：证据等级、真实冷启动、ACL/隔离、作业终态、运行时归属和双版本实机生产闭环；再推进 W20。
W20 是结构/数值/物理验证及报告，不是 W21 扫描优化，也不是 W22–W24 特定工程方案。
现有 Windows 6.3/6.4 Java/建模/求解实现保留；不从头重做。不要求先回 Mac 开发，但保留 Mac 兼容与回归边界。

## 清理前
先在新目录完成恢复核对，再清理旧项目。未提交/ignored 的实验数据、模型、Desktop 未保存内容、合法许可证和凭据不能由 GitHub 恢复。
不得把清理旧项目理解为删除 COMSOL 安装、许可证或唯一科学数据副本。
