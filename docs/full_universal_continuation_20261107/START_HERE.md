# COMSOL MCP 全范围续作工作包

本包依据 2026-11-07 公开仓库最新交接头 `cd2c9cdc1d862e335851bb7b2b254f01f7c8609c`。
用户本次要求：**尽量拆解任务，持续做到既定通用目标全部完成；主 Agent 可直接开发，最终独立子 Reviewer 审查验收。**

## 五分钟开始

1. 阅读本文件、`LATEST_REVIEW.md`、`MASTER_GOAL.md`。
2. 在新目录解压后运行：
   ```sh
   python3 tools/verify_package.py
   python3 tools/bootstrap.py --target ../comsol-mcp-work --scope active
   ```
   Windows 可将 `python3` 换为 `py -3`。Python 3.10+，建议 Git 2.30+。
   默认 Git 恢复当前代码、测试、工具和核心文档，验证固定完整树的元数据，并明确记录未下载的历史 blob；ZIP 后备路线恢复完整 tracked tree。
   此步骤不会装依赖或启动 COMSOL。网络失败则按 `RECOVERY.md` 处理，不换成浮动 main。
3. 在恢复的仓库中，当前入口为：
   `docs/full_universal_continuation_20261107/MASTER_GOAL.md`。
   `TASKS.json` 是任务依赖表，`WORK_BREAKDOWN.md` 是同一任务表的易读版本。
4. 从 T01 开始；T02 统一仓库当前交接/恢复状态，关闭已接受 Java 软件修复的陈旧 next_action。不要再从旧 r470 的下一步重开这一修复。
5. 主 Agent 连续实现、记录结果、选择下一就绪任务。全部开发完成后冻结候选，由独立子 Reviewer 统一验收并复核整改。阶段结束、测试通过或一次提交都不是停止点。

## 文件导航

| 文件 | 用途 |
|---|---|
| `MASTER_GOAL.md` | 唯一当前任务目标、执行循环、终点与权限边界 |
| `LATEST_REVIEW.md` | 新进展、尚缺范围和优先级判断 |
| `WORK_BREAKDOWN.md` / `TASKS.json` | 细分工作、依赖、产物和完成判据 |
| `RELEASE_DOD.md` / `config/UNIVERSAL_DOD.json` | 原冻结 U01–U12 合同，未改写为新要求 |
| `config/TARGETS.json` | 活跃平台与用户既有跳过 |
| `ACCEPTANCE.md` | 有限验收、证据复用、最后统一 Reviewer 的任务 |
| `RECOVERY.md` / `PIN.json` | 在线固定版本恢复及部分恢复语义 |
| `state/PROGRESS_TEMPLATE.json` | 可复制到仓库进度文件，恢复后持续更新 |
| `tools/next_tasks.py` | 只读列出依赖已完成的任务；不自动验收、不执行任务 |
| `references/` | 本次读取的远端合同、状态和已发布 Java 评审快照 |
| `review/` | 本工作包自身的审查与工具测试；不是产品最终验收 |
| `GOAL.txt` | 可复制短 goal |

本包是**联网恢复和执行包**，未内嵌整个仓库、COMSOL、许可证、私有构件或旧任务所有权。删除旧本地文件后保留本 ZIP 和正常网络即可恢复公共源码与本计划；真实运行还需要约定平台上的合法 COMSOL 环境。无法获取的历史私有构件不能被本包凭空恢复，应从固定公共源码重新构建当前候选。

历史文件中的“下一阶段另行授权”、旧执行者 ID、旧 Java 实施 next_action 及旧的所有案例完成目标，均不是本次任务的当前指令。安全、模型/项目隔离、明确的旧 UNKNOWN 资源保护和用户跳过仍保留。
