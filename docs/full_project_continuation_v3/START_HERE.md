# 全项目持续完成 V3

目标不变：主Agent完成原W01–W26，最后独立Reviewer统一验收并整改。当前起点更新至`0644ea970688e02beda4be7069f67144b4bcf59e`；不再从cutoff066恢复。

**本包是联网恢复型，不内嵌完整仓库。**首次访问固定公开GitHub提交取得全部已提交文件，包括continuation候选。没有旧项目也能继续开发；COMSOL/许可证、未提交模型和本地大产物不是本包能恢复的内容。

## 使用
在新目录解压并用Agent打开本包根，复制GOAL.txt。已有Python3.10+时：
```
python tools/verify_package.py
python tools/bootstrap.py
python tools/init_resume.py --repo repository
python tools/candidate_inventory.py --repo repository
python tools/status.py --repo repository
```
Windows可用START_WINDOWS.ps1；无Git可以`bootstrap.py --method archive`，但归档缺对象时必须拒绝，不跳过哈希校验。依赖安装和原生执行属于后续任务，不由恢复脚本执行。

本轮权威指针：`repository/docs/full_project_continuation_v3/state/RESUME.json`。旧任务矩阵和冻结候选原样保留，工具只索引，不自动合并/批准。原“同步后暂停”现在属于历史，但旧UNKNOWN与一次性审批安全边界保留。

先读MASTER_GOAL→REVIEW→INTEGRATION_PLAN→FULL_PLAN，再读取仓库当前checkpoint。不要直接运行continuation内写死旧机路径的shell/native命令。

清理前先看CLEANUP_GUARD。源码可恢复不意味着历史MPH和私有作业库已备份；建议先验证新目录恢复，再清理旧代码目录。
