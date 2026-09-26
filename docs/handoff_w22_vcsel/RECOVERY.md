# 固定公开源码恢复

## 当前入口
PIN=2839e17738838441985ccb7a08a835bd654fe3e1，其父实现 commit 为 `07a44329e96d922a0955e944a8a65ba732894278`。当前提交已含最终代码与公开证据。旧 RESTORE_CURRENT 的“c2ca5e4 + patch”是历史重建路径，不要对本次新源重复打补丁。

恢复工具标准库+可选Git，只下载/写入新目标目录，不执行仓库脚本、不安装包、不启动COMSOL。默认 `repository/`；已有目录拒绝。错误时保留staging和诊断，不切换main、不跳过hash。
Git方案逐blob物化、禁hooks和工作树换行转换、保留真实父提交。历史symlink保存为惰性文本，不跟随旧私有路径。Git LFS pointer只表示指针，若出现须另外取得许可可访问对象或从recipe生成并记录，不能称已恢复二进制。
ZIP方案验证整棵Git树，不包含原Git历史；发布前从同一PIN建真实Git checkout并准确迁移diff，不向unrelated history强推。

## 干净运行环境
创建新Windows Python3.12 x64 venv或项目当前已支持环境，依赖从pyproject/适用lock安装并pip check。源码、安装环境、科学project、控制私有root分离，6.3/6.4各用自己的COMSOL JAR/Worker目录/缓存/Server实例。
实际发现java/javac、COMSOL路径和完整build，不照抄旧用户名、PID、端口、token、JDK路径或junction树。读取 docs/handoff_w21_execution_closure/RESTORE_CURRENT.md 了解已验证运行方案，但只在本轮获准的任务所有目录重新构造；共享安装保持只读。复制的私有配置必须验证当前版本，原 XML 哈希保持不变，绝不重用旧隔离回执。

## 产物边界
公开源码、测试、已提交合成MPH/数据可恢复；未提交实验数据、未保存Desktop模型、登录状态、许可证不能恢复。旧运行的Windows私有runtime junctions不是交付品，不跟随/复用。最终原始科学结果可以由本轮已提交recipe重建，生成新SHA与run_id，不冒充旧产物。

## 发布
保留用户现有修改；新分支 handoff/w22_vcsel。开始/交付前检查远端是否有并发提交，正常合并/PR，不force、不reset远端。只有已有明确授权时push，缺权限交付本地commit/patch并注明。
最终再生成含当前源码和必要公开测试的归档，或更新pin到本轮公开源码；排除商业JAR、token、private DB和未授权模型。对本轮恢复/安装做一次干净路径检查即可，不新造签字/哈希服务。
