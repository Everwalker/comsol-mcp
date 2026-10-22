# 恢复、候选与清理

`bootstrap.py`优先用Git固定commit恢复，无Git时HTTPS ZIP并重新计算完整Git tree。只新建目录，禁hook/CRLF/符号链接激活；文件/总量预算明确，超限需先确认原因再调整，不跳过哈希。ZIP漏掉export-ignore/LFS backing时不宣称完整源恢复；可改合法Git路线，LFS backing缺失另记。
`restore_candidates.py`核对随仓库提交的R2清单hash，读取内容地址对象并校验原字节，恢复到独立新目录。不会执行源代码，也不覆盖规范生产源码，不恢复任何权限或活运行态。
`init_state.py`读取恢复回执与实际action catalog建立本轮新索引，不改旧state/TASKS/ACCEPTANCE文件。更改后的任务覆盖由Agent填写，不把目录中的PROPOSED或registered当PASS。

建议短目标路径，如C:/AI/universal，减少Windows长路径问题。用中文空格另做专门路径测试，不为恢复而修改全局系统策略。已有目录拒绝覆盖，可换新目标。恢复失败保留staging，不把部分内容当已恢复。

clean-up之前可以运行`tools/preclean_inventory.py --repo OLD --output LOCAL.json`，它仅列Git修改/未跟踪/ignored路径，不读取内容、不备份、不声明安全删除。唯一MPH/实验数据/未保存Desktop内容必须另行备份；公开索引里有文件名和hash不代表文件字节存在。COMSOL安装和许可证同样不随包恢复。

历史R6I/SSH/once/Popen只能在原拥有机查明，不能在新机沿旧PID操作。新源恢复不会升级旧活动的UNKNOWN，不将新case当旧任务重试。在新授权项目上可以继续不冲突的新工作；需用现有资源时先核实身份。

候选带的科学/审批绝对路径保留历史原字节；运行配置要新生成，旧有效审批和consumer源hash不移作新权限。普通产品使用应收敛到正式入口/能力契约，而不是执行这些旧测试活动。

首次网络恢复在本包制作环境未执行（GitHub连接器可读，容器HTTPS DNS不可用）。本地工具的合成Git/ZIP/CAS测试不构成真实远端恢复或COMSOL认证。
