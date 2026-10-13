# 可持续执行与状态规模

现有 docs/full_project_execution/state 的RESUME/ACTION_COVERAGE/ACCEPTANCE_MATRIX大约2MB级别；它们不是空文件。init_resume解析它们，产生短的状态和路径索引，不重复整段历史。

新活动指针：docs/full_project_continuation_v2/state/RESUME.json。原始大账本仍是长期任务/动作/验收状态来源，更新时按实际条目变更，原子写入且保留旧Git历史；不要重新初始化。

每次安全checkpoint：记录current_phase/current_task、实际source、next_action、活job引用与UNKNOWN、阻塞索引、已有证据引用。不要复制大日志或认证token进去。不能从文本“之前待办”自动执行旧被暂停的危险命令；先按当前MASTER、身份与权限核对。

同一Goal新会话：读新指针→核对工作树→读取所需条目→查询仍存活的任务→继续next_action。原生状态不能仅由历史文件断言；丢失旧DB不等于原job完成。不要重新发起未知请求或重用旧PID。

状态保存不意味着应停止开发。阶段通过或普通Git同步完成后继续原下一工作。只有完成统一验收、所有可做任务耗尽且外部阻塞、或宿主强制会话结束才收束。宿主结束时如实交接可续跑，不承诺后台自行启动。
