# 通用 COMSOL MCP：新任务无需修改服务端

这不是下一份特定案例工作包。本轮目标是把已实现的对象、类型、执行、结果、GUI、恢复和安装能力收敛为一套通用系统。用户的新要求允许将VCSEL、光纤和胶形/固化的科学完成状态从通用核心毕业条件中拆出，历史记录保留而不改写。

**这是联网恢复包，不是包含完整源码的离线镜像，也不是已经完成通用化的程序。**固定公开源 `cd47281777eae6f4ea0d40c5c754dca9982eda86`。包内含本轮完整任务、范围变更、角色、冻结验收、恢复与续跑工具；首次运行取回完整Git跟踪源码，并恢复仓库携带的未合并候选。

在新目录打开整个工作包，发送GOAL.txt。已有Python3.10+可直接：
```
python tools/verify_package.py
python tools/bootstrap.py
python tools/restore_candidates.py --repo repository
python tools/init_state.py --repo repository
```
Windows可使用`py -3`或实际python.exe。START_WINDOWS.ps1执行同样四步，不启动COMSOL。缺Git可用`bootstrap.py --method archive`；不能把Windows Store占位命令当实际Python。

恢复默认到全新 `repository/`；候选默认到其相邻 `candidate_sources/`，不覆盖规范生产树。新执行入口在 `repository/docs/universal_mcp_execution/`。后续主要阅读仓库内该副本和其state，不必反复从旧聊天找上下文。

先读 MASTER_GOAL.md、SCOPE_RESET.md、RELEASE_DOD.md。主Agent直接开发；最终独立Reviewer审查全部必需目标并执行未预设的小任务。日常开发自测不中断，普通阶段完成后继续推进，直到最终验收或真实外部阻塞。

源码恢复不恢复运行时授权、旧PID/进程句柄/once/token。保留COMSOL安装、许可证、未保存模型和唯一未提交科学数据。本包不会自动删除任何旧目录。网络恢复及本机COMSOL测试需由目标Agent执行；制作环境未能下载完整远端仓库，实际包测试范围见PACKAGE_QA.json。
