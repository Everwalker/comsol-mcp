# 全项目持续完成工作包

这是新一轮唯一活动入口：**主 Agent 直接开发全部剩余规划；最后由独立 Reviewer 统一验收，必要整改后再复审，最终交付。**不再在W23/W24/W25完成时停下来等新的Goal。开发过程仍运行自测和必要安全检查，不是等到最后才测试。

## 使用
在装有COMSOL的Windows电脑，把整个包解压到新的普通本地目录，用Antigravity或实际Agent Host打开包根，粘贴GOAL.txt。主文档是MASTER_GOAL.md，旧阶段“停止W22/不得W23”的指令不再是当前范围。

```powershell
python tools/verify_package.py
python tools/bootstrap.py
python tools/init_completion.py --repo repository
```

优先用已安装的明确Python 3.10+解释器；产品环境另建Windows 3.12 x64 venv并按项目依赖实测。恢复器没有Git时可用 `--method archive`；完整树不符必须失败，不能悄悄换main或省略文件。

## 这份包包含与不包含什么
这是**联网恢复型工作包，不是离线仓库镜像，也不是已经完成的软件**。当前固定公开源为a3418bd4546ecc33e26448e7f2a1982727a37153，第一次恢复会下载全部跟踪源码/测试/公开证据，验证commit/tree/逐文件hash。本次制作环境无法直接下载仓库二进制，所以完整源码不在ZIP内；恢复脚本的本地夹具测试不等于远端恢复已执行。

包内已提供当前进展、原合同身份、总路线、任务恢复与统一审查规则；不依赖旧项目目录或旧聊天。旧W20/W21/W22冻结合同副本只作继承依据，不成为新的阶段停点。

保留COMSOL安装、许可证、未保存的Desktop模型、未提交实验数据和账号凭据。只有已提交到固定公开源的文件能恢复；生成器可重新生成测试模型，但不能复原遗失文件的历史SHA。

## 执行终点
目标是原规划W01–W26全部交付及原适用验收，不是“先做一个Windows版本”或“做到W23”。六组合与GUI/离线交付目标仍保留。资源不足时继续所有不依赖它的任务，然后给出唯一全项目阻塞清单；不得冒充全量完成。

先读MASTER_GOAL.md、FULL_PLAN.md、REVIEW.md和TEAM_PROTOCOL.md。需要恢复执行时只看CONTINUATION.md与仓库内state，不重新全局规划。
