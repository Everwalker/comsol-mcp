# 工具边界
bootstrap.py：标准库+可选Git，固定完整源恢复。无COMSOL或pip执行。
restore_candidates.py：固定R2内容对象恢复到独立目录，不合并、不执行、不恢复授权。
init_state.py：从真实目录建立新通用任务索引，保留原状态；没有任何默认PASS。
status.py：读取声明进度，不认证实现、Reviewer或原生执行。
preclean_inventory.py：只列Git路径元数据，不备份、不删除、不判安全清理。
verify_package.py：本包哈希检查，不是产品正确性证明。

具体命令见START_HERE/MASTER_GOAL。首次需要联网。PowerShell入口及真实Windows/COMSOL均需目标机验证。
