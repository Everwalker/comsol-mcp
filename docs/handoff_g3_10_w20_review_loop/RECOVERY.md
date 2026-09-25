# 从清空旧项目的机器恢复
固定公开commit`3a7c5feb09738ddcfaedd9c30f7db53b893ec3f9`，tree`f32b390fd71c5a084338d41aa377230ad8e26e9d`。无旧本地目录要求。

## 1 恢复程序
在包根运行verify_package、bootstrap；目标默认repository/且必须不存在。Git模式fetch固定commit，直接读Git blob写文件（不执行smudge/hook），校验整树与SHA256；ZIP模式同样重建Git tree验证。大小写/Unicode规范化冲突、Windows设备名、ADS、路径越界、特殊文件均拒绝。
symlink以惰性目标文本保留，receipt声明此事实；不得默认激活指向旧私有路径的链接。Git LFS跟踪指针不等于LFS大文件，receipt会标记，需合法下载其对象后另外验hash才能称实际模型可用。
恢复回执在docs/handoff_g3_10_w20_review_loop/RESTORE_RECEIPT.json；不覆盖仓库根旧回执。归档模式没有Git历史，公开提交前获取同一固定Git父提交并核对补丁，不创建假祖先或force-push。

## 2 环境重建
读取恢复仓库pyproject、机器约束与官方本地帮助。新venv，pip check和freeze；uv.lock/requirements适用性按实际OS/arch审查。不把旧测试的Python3.14或目录jdk11当当前环境事实。
本机已安装COMSOL6.3/6.4是用户给定目标；实际路径和完整build仍需读取。对应客户端JAR、外部JDK、Worker、prefs/privatecontrol/cache、模型输出分别隔离。保留Mac架构；不能通过Windows通过推断Mac新认证。
不从历史run复制PID、session_id、端口、授权收据、用户token。用公开受管入口创建本轮会话，只操作本轮拥有的测试模型/进程。需要系统/许可/凭据权限时记录最小用户动作，不删安全门禁。

## 3 科学数据与报告
公开已提交MPH可恢复并核验。未提交/ignored或被脱敏的历史产物不能从hash逆向恢复；用已提交builder重新生成，记为新artifact，不能冒充旧同SHA。
原生renderer、统计库和观测服务取数必须来自实际运行。工作包内的合成诊断仅控制测试；不能把它们贴上native标签。

## 4 交付
完成后在独立含中文空格路径恢复最终候选源/安装wheel跑最小生产链；不要只恢复本次起始PIN。源码、tests、rules、benchmark、reviewer检查器、依赖与原始证据清单一起冻结。
有既有明确Git写授权则普通分支提交/PR并核对远端SHA；没有授权则输出补丁或安全源码包，不能编造远端已同步。不得打包凭据、worker endpoint token、private DB、旧私有Git全历史或用户唯一科学模型。


## 本轮交付收口
恢复后新规范、FINDINGS、角色文件、配置模板一同位于docs/handoff_g3_10_w20_review_loop/。继续运行辅助工具时仍从本工作包tools读取。不要删除此工作包根目录后仅保留一个Goal文件。
角色创建原始事件可能带Host上下文；只记录本轮必要的实际session标识和创建依据，私有细节留本机，提交前脱敏。独立review签署针对具体candidate，不授权自动发布用户科学数据。
