# 环境和恢复前提

本包不含 COMSOL、JDK、Python、商业许可证、原始科学数据、云端 API key 或完整仓库字节。网络恢复已提交源与证据；本地外部软件合法保留或重新安装。

## 只读盘点

在 Windows 包根运行 `python tools/windows_inventory.py --output local_inventory.json`（也可通过 START_WINDOWS.ps1；它不会启动 COMSOL）。脚本只检查解释器、指定/常见路径、Windows 卸载注册表的 DisplayName/InstallLocation，以及 java/javac 文件是否存在。输出的版本只是候选，**不是引擎版本验证**。

非默认安装可传：

```powershell
python tools/windows_inventory.py --comsol63 "D:\Apps\COMSOL63\Multiphysics" --comsol64 "D:\Apps\COMSOL64\Multiphysics" --output local_inventory.json
```

本轮支持 Windows x64 原生运行；不拿 WSL/Linux 模拟测试称 Windows 原生通过。Agent 在 WSL 中时，测试/编译/COMSOL 执行必须落到 Windows Python/JDK，并验证路径和进程生命周期；更直接的做法是原生 Windows Agent。

## 配置约定

`config/runtime_profiles.template.json` 是**待实现的部署模板，不是当前 MCP 已支持的 schema**。Agent 应映射到已有环境变量和新增所必需的配置。`COMSOL_PROJECT_ROOT`、`COMSOL_ROOT`、`COMSOL_JAVA_HOME` 等按各 profile 子进程分别传入，不改全局 PATH，不让一版本污染另一版本。

优先使用不同控制 home。公开模型/导出路径与 private-control、prefs、token、锁、数据库分开。LocalSystem/服务账户不是本轮默认运行身份；能以普通用户完成的任务不申请管理员权限。

## 正式启动前

核对：Windows OS/build、CPU架构、Python位数、JDK版本与 javac、两个 COMSOL build、官方 client manifest、实际网络监听、所需产品许可、GPU/软件渲染、真实原生 MCP Host 生命周期。

若专用网络隔离需要管理员权限，生成**仅限任务实例/端口/规则 ID**的管理员脚本及清理脚本并说明后果；不关闭防火墙、WiFi、VPN、Tailscale，不修改全部 COMSOL 安装，不自动变更全局执行策略。

## 清理旧文件的边界

先在新目录完成恢复校验，再清理旧项目。未提交/ignored 的 MPH、外部 CSV/材料库、Desktop 未保存内容无自动恢复保证。任务运行中不要清理它的 prefs/recovery/数据库。完成后清理应只移除已验证归属且可再建的任务缓存，保留原始证据和科学产物。

本包恢复固定公开历史，不尝试寻找、合并或发布过去包含 token 的私有 Git 分支。秘密不应写入证据；发布只保留不含值的脱敏说明和必要完整性信息。
