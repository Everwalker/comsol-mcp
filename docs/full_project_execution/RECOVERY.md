# 新目录恢复与最终可恢复性

## 从现有 GitHub 克隆恢复续跑

有现成克隆时，以 GitHub 上已发布的仓库状态为入口，而不是重建工作包状态。先在仓库根核对 `git status --short`、`git rev-parse HEAD`，将 `state/RESUME.json` 的 `verified_git_sync.head` 作为历史同步祖先记录核验，而不是强制当前 HEAD 等于旧值或回退的目标；若明确要追随更新后的 `origin/main`，先 fetch，再阅读新的根 `PROGRESS.md` 和 `RESUME.json`。按 `START_HERE.md` 安装 `pyproject.toml` 声明的依赖，然后从 `PROGRESS.md`、`RESUME.json`、`TASKS.json` 和 `MASTER_GOAL.md` 恢复任务。

不要对已有克隆运行本文件后文的 bootstrap/init 重建命令；它们属于“没有现成克隆时的新目录恢复”，不是 GitHub 工作树初始化。保留已提交状态，不重新初始化任务表或覆盖检查点。只有提交进 GitHub 的相对路径文件会被克隆恢复；原机器绝对路径下的 raw evidence、临时运行目录和许可证/JDK/COMSOL 安装都必须按状态记录重新核验，不能假定它们随仓库存在。

## 当前包
网络恢复固定PIN，不包含完整Git仓库源码字节。Git路径用cat-file原字节写出所有支持的tracked文件并核对root tree/每个blob；归档路径也核对完整树，不自动退main。商业软件、凭据、科学未提交数据均不恢复。

`python tools/bootstrap.py --target NEW_DIRECTORY --method auto`。新目录必须不存在；失效staging不当成功仓库。Git优先；无Git可用archive。archive缺LFS/特殊object/模式导致tree不符应失败后选择Git，不能删证据或绕hash。

大文件/总量预算为安全边界而非产品限制，错误必须报告需要的预算；先检查源树规模，再用明确参数提高，不能静默截断。LFS指针字节不是大文件内容；回执单列指针，若最终任务依赖必须显式获取并验证对应对象。跨平台符号链接保存为惰性文本，不访问旧绝对路径或商业安装。

恢复后原需求文件在docs/comsol_mcp_design_v1；init_completion校验其Git blob并生成当前覆盖表。旧目录中的token/PID/port/preferences不复制；建立新私有运行时，记录实际COMSOL版本/JDK/classpath。

## 最终产品
W26另需真正的目标平台构件/依赖/离线安装/升级回退。当前联网恢复包不能充当离线产品验收。保持联网的用户开发环境与离线运行测试不同；不要为了测试偷偷关闭整个电脑的网络。

最终可交付完整审核过的source snapshot+依赖构件/recipe或新的固定公开commit恢复包。若另有patch，基线和顺序必须唯一，禁止重复应用旧W21补丁。必须包含实际需要的领域数据或可确定性生成器，不能只附本机路径和丢失文件哈希。

源外普通wheel安装、帮助索引重建、两个版本独立MPH创建/重开、图像数据与日志复核都在最终验收内。缺少商业模块/GUI权限/目标Mac机器时保持未完成格子，交付其他成果但不宣称全量认证。
