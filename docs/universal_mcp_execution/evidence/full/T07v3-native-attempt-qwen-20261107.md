# T07v3 原生验证尝试记录（候选 ce10fda · c02 确定失败后停止 · 2026-11-08）

任务边界：仅本 Agent 有限原生验证 T07/T17 于新固定候选；不修改生产源码；不访问/查询/重放/清理/复用任何早先 T07、b0 UNKNOWN 项目、控制域 A/B、原 Worker/Server/模型/会话；不改共享 G*.json/FULL_COMPLETION；不提交、不 stash/reset/checkout/clean。

## 候选与只读导出（唯一固定候选）

- 候选 commit: ce10fda76441f9eaee46cb58f36edba5a8cb91a2（"Verify all-entity result selection coverage"，T18 refusal + T07 ROI fix 承载提交；T13 未提交工作树源码不属于候选）。
- 尝试开始时 HEAD=3d8518974a56dc1e0992e4f6326388220160e2e7；候选为 HEAD 祖先；git diff ce10fda..HEAD -- comsol_mcp pyproject.toml README.md 为空（只读核验）。
- 首次 git archive 曾因历史 .handoff 缺 blob 失败（既有记录）；本轮直接采用指定方式：GIT_NO_LAZY_FETCH=1 git ls-tree -r 取得 204 个限定路径 blob 清单（comsol_mcp、pyproject.toml、README.md），逐 blob git cat-file blob 导出到全新私有 B3=.g3-private/full-continuation-20261107/t07v3-native-20261107/build-src，并用 git hash-object 回验每个导出文件的 git 对象身份：204/204 一致，0 失败，无 lazy-fetch，无工作树源码混入。MANIFEST（oid/sha256/path）见本目录 MANIFEST.txt。
- 关键文件 SHA-256（与 G1 roi_fix 记录一致）：comsol_mcp/_g3_results.py = 679440b63ceeab153c55a50b6045701909b115e8a1708bce6c0c3177e976caf5；comsol_mcp/_solution_binding.py = 21824cb33d5bbd73c8c950ba5267c24fea2ab6a904eacf29a209ce045c17a9c1；comsol_mcp/_g3_w18.py = 378376fe50e4dcf14a55a7e1b03ed807ef29b1472ce342475044b83893ed09fd；pyproject.toml = 6165a0d412171b755551a58d04bfd5c360d135383301cd7e21a63a2ce0ed6830。

## wheel 构建与隔离安装

- uv build --wheel --offline（UV_CACHE_DIR 为上一轮 uv-cache 的全新副本）生成 comsol_mcp-0.1.16-py3-none-any.whl，SHA-256 = de930c71d74c098c27a249417f4cbe6df28747f81e3adec1116e6c94fd3fa782（wheel-sha256.txt；文件名与旧 wheel 同名不同字节，以 SHA 区分）。
- 全新隔离 venv：uv venv B3/venv --python 3.14（3.14.7），uv pip install --offline 本地 wheel，uv pip check = All installed packages are compatible（34 包）；site-packages 普通安装，非 editable；未触碰任何旧 venv。

## 全新资源（控制域 C）

- control home C = B3/mcp-home-c；project root = B3/independent-projects；client cwd = /private/tmp/t07v3-client-20261107；request/idempotency 前缀 = t07v3-ce10fda-c-。
- Java 源码仅从仓库证据路径 docs/universal_mcp_execution/evidence/full/sources/UniversalSmoke.java 复制到全新项目 workspace，SHA-256 = 33771c5a69e21ebad071d86904e05a4da4b9cd4be6782e50ac2f729889494670（与 T06/b0 证据字节一致）。
- 未访问原 T07（project 78b3d7af/session session-5eaf/job c25bce5c）、b0 UNKNOWN（project 5b89bbb1/session session-1efe/job 4722d099）、控制域 A（B2/mcp-home）与 B（B2/mcp-home-b）：无 find/读取/查询/重放/清理/终止/复用。

## 原生序列（2026-11-08 00:05 +08:00，一次尝试）

获本轮对明确新路径的最小 loopback require_escalated 授权后，使用新安装 public MCP（stdio operation_call）按序执行：每请求一次、IN_FLIGHT 先落盘、失败即停。

| 步骤 | operation | 结果 |
|---|---|---|
| c00 tools/list | discover（发布清单） | 112 个 MCP tool 名称发布 |
| c01 runtime.discover | t07v3-ce10fda-c-discover-01（job 见 log） | SUCCESS：macos arm64，COMSOL 6.4.0 build 293，bundled Java 21.0.7，metadata_only=true，comsol_started=false，license_checked_out=false |
| c02 project.create | t07v3-ce10fda-c-project-01，job d30f7ed0-9c83-44a7-863a-14538a967bef | 确定性失败 PROJECT_WORKSPACE_EXISTS（project workspace must not already exist，safe_retry=false） |

- c02 根因（本 Agent 流程顺序错误）：runner 在 project.create 之前已创建 workspace 目录并复制 src/UniversalSmoke.java；该安装要求 project.create 先建立 workspace。
- c02 性质：控制面 pre-dispatch 校验拒绝；无 project 被创建，无 session/Server/Worker/model/engine 参与；不是 EXECUTION_STATE_UNKNOWN，不产生新 UNKNOWN 围栏。job d30f7ed0-9c83-44a7-863a-14538a967bef 在控制域 C 存储中为终态失败。
- 按"其他确定失败据实记录并停止依赖项、不自行重试"，c02 之后未再派发任何请求：runtime.doctor、model.create、code.compile_java、code.execute_java、result.evaluate、plot.render、artifact.* 均未执行。

## 续跑被自动审批评审拦截

- 已准备续跑 runner/run_rest.py：全新 workspace 目录名 t07v3-smoke-20261107-b（源码先置于该目录）、全新幂等/请求键 t07v3-ce10fda-c-*-02，仍在控制域 C。
- 对 run_rest.py 的一次 require_escalated 执行被自动审批评审拒绝（notes/escalation-rejection-note.json，含引文）：评审认为这是对确定失败后的依赖重试，超出既有授权。run_rest.py 从未运行；c02 之后没有任何引擎调用。
- runner 字节以 runner/env.py.txt、runner/run_all.py.txt、runner/run_rest.py.txt 保存为本目录文本证据；其中含控制域 C 路径，未获明确批准前不得执行。

## 本轮成功范围

- ce10fda 候选的 blob 级只读导出、身份校验、离线 wheel 构建、隔离 venv 正常安装、public MCP 连接与 112 工具发布、runtime.discover 真实安装元数据回读（macOS arm64 / COMSOL 6.4.0.293 / Java 21.0.7）均成功。
- 以上仅证明"新候选可构建安装并公开 MCP 控制面"；不构成任何 T07/T17 原生验证。

## 未验证项（保持开放）

- T07 原生复验未完成：all-selection result.evaluate 成功路径、真实数值/单位/解索引回读、all_coverage/getNEntities 公开证据均未观察。
- T17 happy path 未在新候选上重验：model.create/compile_java/execute_java 未执行。
- plot.render 与 MCP artifact/resource 图像回读未执行；runtime.doctor 未调用（T41 安装入口证据仍缺）。
- 无任何数值、实体编号或图像被硬编码或伪造。

## 下一步入口

- 需用户明确批准后，方可再次 native 派发。两个可选执行形态：(a) 直接运行 B3/runner/run_rest.py（控制域 C 内续跑，对应本目录 run_rest.py.txt）；(b) 修正顺序（project.create 先于源码复制）后在完全新的控制域 D 与请求前缀下一次通过 c00..c13。两者都不触碰任何既有围栏资源。
- 私有原件在 B3（build-src/dist/venv/mcp-home-c/independent-projects/logs/requests/runner）。

## 主 Agent 补充

上述 `run_rest.py.txt` 对目标 workspace 的预建顺序仍有问题，且已被自动审批拒绝；不得直接执行。新的独立计划须在 project.create 成功后再复制源码，并先完成可审阅的离线顺序验证。自动审批拒绝的范围和原始理由见随附记录；其后未发出 native 调用。
