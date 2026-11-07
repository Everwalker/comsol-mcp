# 删除旧本地文件后的恢复

## 包含什么

本 ZIP 包含当前分析、完整目标、52 项任务、原冻结合同、已发布审查/状态快照、固定 Git 身份、恢复/校验和只读任务导航工具。它不包含全仓库副本、商业 COMSOL、许可证、旧机器账号/会话或私有发行构件。

固定来源是公开仓库 `https://github.com/Everwalker/comsol-mcp.git`，固定提交 `cd2c9cdc1d862e335851bb7b2b254f01f7c8609c`。旧 main、旧 WIP、旧工作包和聊天记录都不是必需的本地依赖。

## 默认：当前开发范围

从解压目录运行：
```sh
python3 tools/verify_package.py
python3 tools/bootstrap.py --target ../comsol-mcp-work --scope active
```

Git 路线浅取固定 commit（active 使用 blob 过滤），读取完整 tracked tree，重建并校验 tree SHA1，然后只取当前源码、测试、tools/scripts、examples、原设计、通用执行文档和 Java 评审目录及正常根文件。每个落盘文件校验 Git blob identity 和 SHA256。跳过的历史文件保留在完整 index/tree map 中并标 skip-worktree，避免误报成用户删除。

`RESTORE_RECEIPT.json` 明确区分：
- tree_entries_verified：验证了多少个树条目；
- file_count / source_bytes：实际 materialize 并验证了多少文件/字节；
- deferred_blob_count：尚未 materialize 的历史 blob 数；
- all_tracked_blob_bytes_materialized：是否真的全部 tracked bytes 都在本地；
- missing_lfs_backing_objects：如有 LFS 指针，商业/大对象没有被凭空恢复；
- runtime_authority_restored=false / comsol_started=false。

完整路径/模式/blob/materialized 清单在新增交接目录的 `RESTORE_SOURCE_MAP.json`。生成树 map 不能替代 missing blob 验证。Git 服务器如不支持 filter，可能仍传输较多对象；只要恢复语义和校验一致，不将传输优化当产品功能门槛。

当前 bundle 默认不装 pip 依赖，不执行仓库脚本、不建 Worker。随后 T01/T05 依恢复后的真实 pyproject/README/锁文件准备环境。不要对整个仓库一上来执行 broad pytest：部分测试会启动实际 Worker。

## 全量 tracked tree

如确实需要全部历史文件且资源允许：
```sh
python3 tools/bootstrap.py --target ../comsol-mcp-full --method git --scope full
```

没有 Git 时 `--method auto` 使用固定 commit 的 HTTPS ZIP；ZIP 路线总是 full，并校验完整 tree，因此可能很大。可显式指定 `--method archive`。Archive 恢复不建立 Git 历史，开始提交/合并前应建立真正连接固定远端历史的 Git 工作树，不能把解压内容 init 成伪造的替代 main。

默认预算为单文件 1 GiB、总解压 4 GiB、下载 2 GiB；超预算是可解释的恢复失败，可在确认实际所需范围后显式调整参数，或用 active Git 路线。不要把下载全部旧证据作为开始当前代码开发的前置。

## 部分失败与补取

- 只写全新的 target，已存在目录拒绝覆盖；失败 staging 留下供诊断，不清理用户数据。
- Git 失败不会悄悄切换 archive 或浮动分支。读取 `_state/recovery_failure.json`，区分网络、Git、对象缺失和预算原因；原因未变化不反复重试。
- 网络策略禁止 GitHub 时，使用宿主明确提供的 GitHub connector 或合法公共源码获取能力读取固定 commit 的 tree/blob。若完整克隆暂不可行，按固定 tree map 获取本轮必需源码并验证实际 bytes；准确记录 PARTIAL，不宣称完整恢复。不能改网络策略或借其他目标规避限制。
- active 模式缺某个实际需要的历史 fixture 时，使用当前 Git 仓库按固定 commit/path/blob 取回到新临时文件，校验后再放入对应未占用路径；不要忽略缺文件继续说测试通过。开发中已有修改必须保留，不为补取做全树 reset/clean。
- 大小/网络问题不是更换成 main 的理由。若远端 pin 已不可取得，明确 SOURCE_UNAVAILABLE；先完成可离线的计划/证据整理，再要求恢复该精确对象或提供可验证的新基线。

## 当前入口与历史指令

恢复工具把整个工作包复制到仓库 `docs/full_universal_continuation_20261107/`；不覆盖原根 AGENTS、原 RESUME 或历史证据。T02 根据用户本次指令更新当前入口及 root 的冲突分工/停止描述：主 Agent 直接开发、U01–U12 持续完成、最终独立 Reviewer。保留安全与资源围栏，不通过修改说明取消权限。

树中任何旧路径/PID/lease/token/回执只是历史数据，不产生当前所有权。新任务使用新的独立项目与有效许可。旧 Windows Stage2 transfer UNKNOWN 及原可能 Worker fixture owner 保持原资源围栏，不能在本恢复流程内查询、重放、接管或清理它们；无关新资源不受这一围栏扩张限制。

## 清理前的续作保存

本包只固定 2026-11-07 的起点。后续 Agent 必须把源码修改、任务进度、原始证据和当前候选身份持久化；在有授权的远端工作分支发布，或交付更新的可恢复源码包/补丁与 pin。未保存到远端或新包的本地工作，在清理本地文件后无法恢复。最终 T52 的交付必须包含这种恢复入口。

## 本包实际验证范围

当前作者环境只验证本包 Python 工具、本地 fixture Git/ZIP 恢复、路径与完整性、任务图和 relocation；没有在当前环境完整下载远端 22k+ tree，没有执行 COMSOL native 或 Windows/macOS 实机恢复。具体结果见 `review/QA.json`。不得从工作包工具 PASS 推导产品 PASS。
