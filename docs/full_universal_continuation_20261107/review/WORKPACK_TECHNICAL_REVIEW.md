# 工作包工具独立技术审查

- Reviewer：独立子 Agent `/root/review_progress`。
- 日期：2026-11-07 UTC。
- 工作包：`COMSOL_MCP_FULL_UNIVERSAL_WORKPACK_20261107`。
- 续作源码：`cd2c9cdc1d862e335851bb7b2b254f01f7c8609c`；Git tree：`c81188515a5359df5a5bd72d91b498a3ade4d4ff`。
- 结论：**ACCEPTED_SCOPED_WORKPACK_TECHNICAL**。
- 本次审查范围内未发现仍需阻断交付的 P1/P2。该结论只覆盖下述工作包工具与本地验证，不是 COMSOL MCP 产品验收。

## 独立性与范围

本 Reviewer 未编写或修改恢复工具、导航工具、测试选择器、任务图或产品代码；本轮仅新增这份审查报告。审读 `tools/bootstrap.py`、`tools/next_tasks.py`、`tools/selected_tests.py`、`tools/verify_package.py`、对应三份测试文件，以及 PIN、任务图、状态模板和恢复说明。独立运行本地工具测试和有限合成检查，未调用产品 pytest、真实 SDK compiler harness、Worker、COMSOL 或远程恢复。

计划范围另由 `/root/review_generality` 独立审查。本报告不重复为 U01–U12 增加门槛；52 项是执行步骤，最终产品接受仍由冻结合同、实际证据和独立 Reviewer 决定。

## 恢复工具的实际语义

| 检查对象 | 审查观察与判断 |
|---|---|
| 固定版本 | PIN 固定仓库、40 位 commit/tree 和对应 archive URL；Git 读取固定提交，失败不自动退回 main，也不自动改用其他恢复方式。 |
| active 恢复 | 校验完整 Git tree 元数据；只物化根级正常文件及列明的当前源码、测试、工具和核心文档。索引保留固定 tree，未物化路径设置 skip-worktree。本地 fixture 验证这些文件不会被误报为已删除。 |
| full 恢复 | Git full 路径取得并校验该固定 tree 的所有 tracked blob；不是获取全部历史提交。ZIP 路径始终是 full，不因 CLI 默认 scope 为 active 而宣称只下载了当前文件。 |
| 文件内容与路径 | 已物化内容核验 Git blob 标识并记录 SHA-256；拒绝不安全路径、大小写/Unicode 冲突等；符号链接保存为惰性文本并披露，LFS 指针也披露，不能据此宣称已获得链接目标或 LFS 大文件。 |
| 目标与交接文件 | 目标目录已存在时在网络操作前拒绝；中途失败不发布为成功目标。overlay 按工作包 manifest 复制全部受管文件，包含 AGENTS；原仓库历史文件保留。不存在以覆盖用户目录完成恢复的正常路径。 |
| 回执真实性 | active 回执使用 `PINNED_ACTIVE_SOURCE_RESTORED`，记录 deferred blob 数、未全部物化标识和完整 source map；full 使用 `PINNED_SOURCE_RESTORED`。两者都保持 `product_acceptance=NOT_ESTABLISHED`、`runtime_authority_restored=false`、`comsol_started=false`、`dependencies_installed=false`。 |

本地 active fixture 证明的是选择物化、索引及回执语义。远程服务器是否真正支持 partial clone/filter、真实网络传输量、实际大型仓库恢复耗时均未验证；工具与说明没有把这些未运行事项写成已通过。工作包不内嵌完整产品源码，需要后续联网或可用 Git/ZIP 来源完成固定版本恢复。

## 测试选择器与继续执行

`selected_tests.py` 默认只打印 15 个精确 pytest node 的命令，不执行 pytest、不创建测试输出目录。显式 `--run` 才执行；运行环境清除继承的 `PYTEST_ADDOPTS`、`PYTEST_PLUGINS`，并设 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`。本次测试通过 mock 验证参数及污染变量隔离，没有实际运行这 15 个产品节点。此工具只用于已有 Java 定向软件验证，不能替代全项目验收。

`next_tasks.py` 只读取计划和状态，校验 ID、依赖 DAG、字段及证据引用要求，输出可推进项；不修改进度、不执行工作，也不核实引用文件真实性。实现完成与真实验证分离：BLOCKED 任务若 `implementation_done=true`，可满足后续软件实现依赖；要求真实 target check 的依赖仍需对应检查 DONE。`attempt_dependencies` 则用于失败后的修复入口，不把“已经尝试”当作通过。

在实际 52 项任务图上确认：native 阻塞时 T11/T15/T17 等独立软件工作仍可推进；T48 有实际失败尝试及引用后，T49 可进入修复而 T48 仍保持 BLOCKED。即使合成状态将全部 52 项标记 DONE，导航仍返回 `NOT_ESTABLISHED_BY_THIS_TOOL`。最终产品接受不能由导航的 ready、IMPLEMENTED 或任务计数推导。

## 本 Reviewer 实际运行结果

运行环境：Linux，Python 3.12.14，Git 2.51.1。工作目录为本包根目录。

```text
python3 -B -m unittest discover -s tests -v
```

结果：**26 tests，26 passed，0 failed，0 skipped，exit 0**。其中 RecoveryTests 14 项、PacketTests 7 项、PlanTests 5 项；包括真实本地临时 Git、合成 ZIP、惰性链接、active 索引、目标已存在拒绝、Git 失败不回退、完整 overlay 复制、合成 manifest 损坏拒绝、选择器环境隔离及导航依赖分支。

另运行 6 项只读或临时目录合成检查，全部通过：

| 检查 | 实际结果 |
|---|---|
| 实际任务图 native 阻塞与软件推进 | T01–T05 完成、T06–T09 阻塞、T10 实现完成但验证阻塞时，T11/T15/T17 就绪。 |
| T48 失败修复分支 | T48 的 BLOCKED 与失败引用保留；T49 就绪，未伪造 T48 通过。 |
| 全部任务标记完成 | 已完成步骤计数为 52，但产品接受仍为工具未建立。 |
| active 回执 | 合成 3 文件 tree 物化 1 个，准确记录 2 个 deferred、未全部物化及对应 source map。 |
| full 回执 | 同类合成 tree 全部物化，deferred 为 0；产品接受仍未建立。 |
| 导航 CLI 只读 | `python3 -B tools/next_tasks.py --limit 1` 输出 T01；进度模板运行前后 SHA-256 相同。 |

这些合成状态中的 evidence_refs 是测试占位字符串，绝非真实产品证据。首版自有合成检查曾错误期待尚未满足依赖的 T31 就绪；检查实际任务图后修正 Reviewer 的预期为 T11/T15/T17，随后六项通过。没有为此修改工作包工具或计划。

## 未运行事项及结论边界

| 事项 | 本轮状态 |
|---|---|
| 真实远程 Git / codeload ZIP 恢复 | NOT_RUN |
| Windows / macOS 上恢复工具实际运行 | NOT_RUN；本轮是 Linux 本地测试 |
| COMSOL MCP 产品 pytest、真实 SDK 编译 harness | NOT_RUN |
| Worker 构造、COMSOL 引擎、许可、三项活跃 native 目标 | NOT_RUN |
| 当前候选全局独立产品接受 | NOT_ESTABLISHED |
| 最终工作包 manifest、ZIP 和发布后完整性 | 本报告写入时待主 Agent 最终生成、验证与归档；本报告不预先宣称通过 |

固定源码已有 Java snapshot v2 的独立软件 ACCEPT，工作包正确保留该状态；它不提升成当前 native、安装或全项目接受。历史 UNKNOWN、NOT_RUN 以及科学结果边界不因本次工具测试通过而改变。

允许在交付说明中记录“工作包工具独立本地 26 项单元测试及 6 项有限合成检查通过”。不能写成“完整仓库已恢复”“产品 26 项通过”“全平台验收完成”。主 Agent 随后应生成最终 manifest、在新解压目录执行包完整性校验并核对归档；若下列受审逻辑哈希不变，单纯补入报告、QA 和归档记录不需要再次重复产品或全部工具审查。

## 受审文件 SHA-256

```json
{
  "tools/bootstrap.py": "d025f10a4cd8fae06d50353e7eb10c2738835aaa345630c821aa1b696a846e17",
  "tools/next_tasks.py": "bfc7e013cc66cd41f6a51d887556444d8815161ec2b448040ac47faf507effcf",
  "tools/selected_tests.py": "04219428fcdc3afbae167746c6a36f41d3dbdaa4c2a8db987648da8a08cc6062",
  "tools/verify_package.py": "477442143a08bd230bfd227157f5ca8c3edc326996fcc9fa48dd354d1615d565",
  "TASKS.json": "437aae22f8536fcc40c182e9d62ee21b1a3172db4398c0995c95cffff14d74e2",
  "PIN.json": "027377e658a7c9871cd5e8c73ba33748edf88ad1a9b9ebf555b18fcf3c3c2da9",
  "state/PROGRESS_TEMPLATE.json": "9f2778ce0f9291dcb00d1d173877d336085712eb774199f65f0dc54e15eaf750",
  "tests/test_recovery.py": "74781eaf5e577893a9bf36801697db02801940fe7296c7ca21461b8ff020089d",
  "tests/test_packet.py": "66fea893a3997d5216d33e4ce4aeb0fdb293073f365025c0723fc97d7a12860a",
  "tests/test_plan.py": "979a6a416cc14cf74d55d7b8655c59e473a5d2835b34b16418a51af90bbbc0a1"
}
```
