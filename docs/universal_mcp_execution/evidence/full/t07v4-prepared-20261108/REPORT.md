# T07v4 单次全新 native 验证 —— 准备报告（2026-11-08，未执行）

状态：仅完成离线准备。未执行任何 native/MCP/COMSOL 调用，未申请升级。**实际执行必须由用户明确批准后**按全新 loopback 授权进行一次。

## 候选身份（先于批准明确）

- 本次固定候选：`f49cc0fb2a1acd14336402f4dda5a87cb61e6ca0`（"Classify empty Java sources and exercise domain disk admission"，2026-11-08 00:24 +08:00）。
- 只读 Git 核验：f49 为当前 HEAD 的祖先（先后核验 976c89e 与其后的 16c2c08）；`ce10fda76441f9eaee46cb58f36edba5a8cb91a2` 为 f49 祖先；`git diff f49..HEAD -- comsol_mcp pyproject.toml README.md` 为空。候选以 f49 blob 本身固定，不随 HEAD 前移变化。
- f49 相对 ce10fda 的构建域差异仅 3 个源码文件：`_g2_code.py`（空 source_artifact 改为 PreWriteRefusal，T18 分类）、`_g2_engine.py` 与 `_g2_registry.py`（node.remove 封闭 schema 与未审查删除路径的 pre-write 拒绝，T13）。本 runner 使用的 13 个 operation 与全部参数不受影响，无需改动调用形态（UniversalSmoke.java 非空；不使用 node.remove）。
- 旧候选 ce10fda 的 wheel（SHA-256 `de930c71…3fa782`）为**旧候选构件，不使用**；t07v3 证据保留原样不改写。

## f49 离线导出、构建与隔离安装（本轮已做，全部离线只读/私有目录）

- `GIT_NO_LAZY_FETCH=1 git ls-tree -r f49`（限定 comsol_mcp、pyproject.toml、README.md）得 204 个 blob；逐 blob `git cat-file` 导出到全新私有目录 `B4=.g3-private/full-continuation-20261107/t07v4-native-20261108/build-src`；逐文件 `git hash-object` 回验 204/204 与 ls-tree OID 一致。MANIFEST（oid/sha256/path）见本目录 `f49-MANIFEST.txt`（204 行）。
- `uv build --wheel --offline`（UV_CACHE_DIR 为 t07v3 uv-cache 的全新副本）→ `comsol_mcp-0.1.16-py3-none-any.whl`，SHA-256 = `755e277a620ce05777055b883e52ad55a1e3498a83d57f5f9ed305158149fa1f`（本目录 `f49-wheel-sha256.txt`）。
- 全新 venv（`B4/venv`，uv --python 3.14）离线安装该 wheel；`uv pip check` = 34 包兼容；已安装 `_g2_code` 源码中确认含 f49 的空源码 PreWriteRefusal 行（安装内容即 f49 候选）。
- 执行前仍需重新核对 `WHEEL_SHA256 = 755e277a…149fa1f` 再使用。

## 已批准范围外、待用户批准的单次执行计划（全新资源清单）

| 资源 | 计划值 |
|---|---|
| 私有根 B4 | `.g3-private/full-continuation-20261107/t07v4-native-20261108` |
| 控制域 | 全新 control home D = `B4/mcp-home-d`（A/B/C 及全部历史 project/session/job 不读取、不复用） |
| 项目根 | `B4/independent-projects`（runner 首跑创建；若已存在则 FRESHNESS_REFUSAL 拒跑） |
| project workspace | `t07v4-smoke-20261108`，由服务端 project.create 创建；客户端预检查拒绝任何预存在 |
| client cwd | `/private/tmp/t07v4-client-20261108` |
| request/idempotency 前缀 | `t07v4-f49cc0f-d-`（全部新键，一次一档） |
| MCP 安装 | `B4/venv/bin/comsol-mcp`（f49 wheel，stdio operation_call） |
| Java 源码 | 仓库证据 `sources/UniversalSmoke.java`，SHA-256 `33771c5a…494670`，仅在 project.create 成功后复制进新 workspace 并回验 sha |

顺序（一次通过 c00..c13）：tools/list → runtime.discover → **project.create 成功 → 复制源码** → session.start → session.connect → runtime.doctor → model.create → code.compile_java → code.execute_java → result.evaluate（all-selection）→ plot.render → artifact.list/inspect/read。任一确定失败立即停止；EXECUTION_STATE_UNKNOWN 或无响应立即写 fence 记录并退出（不重试、不 reconcile、不清理、不 cancel/不发信号，IN_FLIGHT 标记保留为证据）。无破坏性清理。

## 离线验证范围（未运行 native）

- `offline/selftest.py.txt` 用离线替身 session 驱动真实 runner 文本（`runner/run_t07v4.py.txt`、`runner/env.py.txt` 均为惰性 .py.txt，未执行）：5 个场景 21 项检查全部通过，见 `offline/offline-selftest-output.txt`。
- 证明：完整调用顺序且源码复制严格位于 create 与 session.start 之间；workspace 预存在时客户端预检查拒发 project.create（t07v3 c02 失败类）；确定失败后无后续调用与复制；UNKNOWN 响应与传输无响应均立即围栏且不再派发。
- 未运行：任何 COMSOL/Worker/session/model/compile/execute/evaluate/plot/artifact；未产生任何数值或图像；无提交。

## 保持开放

f49 上的 T07 原生复验（all-selection 数值回读、all_coverage/getNEntities 证据）与 T17 happy path、runtime.doctor、plot/artifact 回读全部未验证，等待用户批准上述单次执行。

## 主 Agent 复核后的最小修正（2026-11-08，未执行 native）

- **wheel SHA 自验**：runner 新增 `startup_gates`，在任何项目根/控制域创建与任何派发之前，先计算 `env.WHEEL` 的 SHA-256 并与 `WHEEL_SHA256 = 755e277a620ce05777055b883e52ad55a1e3498a83d57f5f9ed305158149fa1f` 比对；不一致或缺文件立即 exit 4（`WHEEL_SHA_REFUSAL`），不创建任何目录。旧报告"仅注释"的说法由此关闭。
- **client cwd 独占创建**：原稿直接把 `/private/tmp/t07v4-client-20261108` 作为 stdio cwd，而该目录当前不存在，属启动阻断。现在 `startup_gates` 用 `os.mkdir` 独占创建；若已存在则 fail-closed（exit 4，拒绝复用且不改动其内容）。门禁顺序：wheel SHA → 域 D 根新鲜性（`mcp-home-d` 或 `independent-projects` 已存在即拒跑）→ 独占创建 client cwd → 之后才 mkdir 项目根。
- **静态核对（结论：无需改动）**：`runtime.doctor` arguments `{request_id, runtime_id}` 符合目录 `02_ACTION_CATALOG.json` input_schema（properties: request_id/runtime_id/checks，required 为空）与 handler `_g3_runtime.py` 的 `_RUNTIME_DOCTOR_FIELDS=("runtime_id","checks")`；`request_id` 经 `_g3_common.py` 的 `ENVELOPE_FIELDS` 通过 `reject_unknown_keys`。模型 revision 提取与已存原生响应一致：`public_model_T06.json` 显示 `data.model_ref.model_tag` 与 `execution.revision=0`，`public_java_execute_T06.json` 显示 revision 递增至 1；runner 的 `sc["execution"].get("revision", ...)` 形态一致。
- **离线验证补充**：新增启动门禁场景 S6–S9（正确 sha 通过且 cwd 被独占创建、二次调用拒绝复用；sha 不符时 cwd/根均不被创建；cwd 已存在被拒且内容保留；`mcp-home-d` 预存在时在创建 cwd 之前即拒绝）。全套 31 项检查、0 失败，输出已重生成于 `offline/offline-selftest-output.txt`。
- **完整性**：`SHA256SUMS.txt` 记录本目录各审阅文件的当前哈希；f49 wheel 哈希不变。本轮未清理任何既有资源（此前替身测试在系统临时目录的残留目录也保留原样）。
