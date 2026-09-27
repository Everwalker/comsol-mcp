# 交给开发模型的任务指令

请基于 Ching-Chiang/comsol-mcp 的固定提交 `ccca65aa8277d1205c5de5fb6221e460aca5997a` 实施本目录的全能力优化方案。先阅读 01_ARCHITECTURE.md、02_ACTION_CATALOG.md、03_ACCEPTANCE.md、04_IMPLEMENTATION_PLAN.md 和 SOURCES.md。

## 不可变目标

目标用户是云端强模型，不是本地小参数模型。必须实现领域工具、通用模型对象API、MCP内受控Java执行、真实结果与图像、完整恢复/验收；不能以减少工具数或让模型更容易选工具为由删掉能力。核心支持Windows x64、macOS Apple Silicon、macOS Intel，与COMSOL 6.3/6.4共六组合。

允许你在当前工作阶段实现并验证代码；不要只输出设计讨论。按照工作包顺序做，每一步明确当前完成与尚未验证的范围；不把第一阶段基础连接当整个系统完成。禁止在没有真机测试时声称六组合适配成功。

## 先执行的工作

1. 检查当前工作仓库是否与基线一致；若不同，列出差异并在新分支保留用户改动，不回退/覆盖用户分支。
2. 提取实际注册接口和schema，建立旧50工具兼容表，读取并复现F01–F12中与当前代码一致的问题。
3. 完成G0：在可访问的每个目标环境验证Java API、共享Desktop、渲染和取消路径；不可访问环境标UNVERIFIED，保留可复现测试入口。
4. 实施G1安全修复、typed协议、模型身份和串行队列，再进入通用对象层与代码执行。

## 执行纪律

所有COMSOL动作通过绑定模型的MCP backend。没有高层wrapper时，允许generic API或MCP受控Java；不得偷偷另启一次性脚本操作另一份模型。主模型identity、revision、checkpoint、回读和证据必须贯穿领域工具与代码路径。

不要发明COMSOL内部feature_type、属性或cancel API。优先实际实例自省、目标版本官方帮助、示例和隔离试验。脚本编译成功不等于模型建立正确，solver成功不等于物理验证通过。

不得在工作区外写入、强杀用户server、上传MPH/机密数据、替换许可证或修改系统安全设置，除非已有匹配的明确授权。trusted_code不等于整机无限制权限。

每次写操作采用inspect→plan→authorize→checkpoint（按策略）→act→readback→validate→log。允许在一个已授权事务中批量执行，不要对每个普通setter请求人工确认。

## 每个工作包交付

代码与测试、输入/输出schema、实机/未实机状态、变更清单、来源、错误及恢复记录、下一工作包依赖。测试结果分为PASS/FAIL/BLOCKED/NOT_RUN，不把skipped测试计为PASS。长期作业要记录真实job_id和可恢复状态，不用sleep或假进度伪装。

## 最终验收

依据03_ACCEPTANCE全部required条目；用户三类案例要有真实MPH、代码、日志、原始数据、图片、假设与验收报告，并能在目标版本打开或重放。GUI不支持、许可缺失、资料不齐、科学假设未验证必须明确，不用伪造演示数据补齐。
