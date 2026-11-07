# T07v2 原生验证尝试记录（新候选 b0cbd2d · 单次 result.evaluate → 新 UNKNOWN · 2026-11-07）

任务边界：仅本 Agent 执行有限原生验证，不改生产源码；按约定不得访问原 T07 项目/job/session/Worker/model。实际曾只读列举原项目目录，详见偏差记录；未做任何 reconcile/recover/inspect/replay/cleanup。

## 候选与源码身份

- 唯一允许候选：commit `b0cbd2d398c66023ab45dc658fd8f2f09ff8edc4`（"Gate empty solution diagnostics on verified count evidence"）。
- HEAD 关系：尝试开始时 HEAD=b0cbd2d；主 Agent 文档交接提交 `46b7b01`（仅 PROGRESS.md / CURRENT_HANDOFF.md / FULL_COMPLETION.json）在其后；`git diff b0cbd2d..HEAD -- comsol_mcp pyproject.toml README.md` 为空（只读核验）。构建不使用工作树源码。
- 导出（私有目录 `<wt>/.g3-private/full-continuation-20261107/t07v2-native-20261107`，下称 B2）：
  - 首个 `git archive b0cbd2d`（含与不含 `--` 受限清单）均因 partial-clone promisor 需要历史 blob `.handoff/agent_sessions/developer_session.json`（离线不可取）而失败 → 记录为构建前失败（不涉及作业）。
  - 按主 Agent 指示改用等价只读方式：`GIT_NO_LAZY_FETCH=1` 下对 `comsol_mcp pyproject.toml README.md` 的 204 个 tracked blob 逐个 `git cat-file blob` 导出（B2/build-src，MANIFEST.txt 同目录），204/204 成功，无 lazy-fetch。
  - 关键文件 SHA-256：`comsol_mcp/_g3_results.py` = 35987e14249e0112805dd0d9b90d44b895df326ecd1b3a4d275fe76a64f7fe84（与 G1 candidate_fix 记录一致）；`comsol_mcp/_solution_binding.py` = 21824cb33d5bbd73c8c950ba5267c24fea2ab6a904eacf29a209ce045c17a9c1（一致）；`pyproject.toml` = 6165a0d412171b755551a58d04bfd5c360d135383301cd7e21a63a2ce0ed6830。

## 构建 / 安装（全部新资源）

- wheel：`uv build --wheel --offline`（UV_CACHE_DIR=B2/uv-cache，由 T05 缓存副本只读复用）→ `comsol_mcp-0.1.16-py3-none-any.whl`，SHA-256 = 584d905f37b24120ea1bee8556b96af890a6228dd549dc62d6feb2e16bb5c8cf（B2/dist，副本 sha 见 t07v2-b0cbd2d-20261107/wheel-sha256.txt）。
- venv：`uv venv B2/venv --python 3.14`（Python 3.14.7），`uv pip install --offline <wheel>`，`uv pip check` = All installed packages are compatible；安装为 site-packages 普通安装（非 editable）。未触碰 T05 旧 venv。

## 控制域与 sandbox 失败

- 控制域 A（B2/mcp-home）：sandbox 内首次 describe 尝试返回 CONTROL_REQUEST_NOT_SUBMITTED / ControlStartupError，dispatch_stage=pre_dispatch、operation_dispatched=false、execution_state_unknown=false（操作未提交，非 UNKNOWN）。私有 control.log 两次 traceback 均为 socket.bind PermissionError [Errno 1]（sandbox loopback 拒绝），失败 daemon 进程随异常退出（未发信号）。首失败证据：logs/sandbox-attempt-1.stderr.txt 与 logs/describe_result.evaluate_plot.render_artifact.list.json。
- 获最小 loopback 授权后、围栏指示到达前，首次授权 describe 复用了域 A：启动健康 daemon（DAEMON_READY 2026-11-07T15:09:24Z，pid 60167，port 59058，control.lock 由该 pid 持有，lsof 只读确认），仅执行 operation_describe（READ catalog：result.evaluate/plot.render/plot.list/plot.group_create/artifact.list/artifact.inspect/artifact.read/runtime.render_probe/runtime.doctor，全部 success=true；schema 存 logs/describe_result.evaluate_plot.render_plot.list_plot.group_create_arti.json）。无 project/session/model/engine 调用。域 A 之后按指示保留：不再使用、不发信号、不清理。
- 控制域 B（B2/mcp-home-b，主路径专用，request 前缀 t07v2-b0cbd2d-b-）：全新 home、全新 project/session/Worker/model/idempotency。

## 偏差如实记录

1) 2026-11-07T15:05:47Z（本地 23:05 +08:00），本 Agent 曾执行 `find <old>/independent-projects/full-universal-smoke-20261107 -maxdepth 3`，只读列举了原 T06/T07 围栏项目的 workspace 目录树（仅 src/UniversalSmoke.java 路径）。纯文件系统列举：无 MCP/控制面查询、无 Worker RPC、无进程信号、无重放/清理/修改。原 T07 job c25bce5c-87a1-4be5-b367-20ce5624f730 及其 project/session/Worker/model 围栏未受影响；此后未再读取该路径。Java 源码字节仅取自仓库证据文件。详见 notes/deviation-note-t07v2-20261107.json。
2) 上述域 A 复用 describe（发生在围栏指示之前）。详见 notes/control-domain-A-note-t07v2-20261107.json。

## 原生序列（域 B；均单发，无重放）

COMSOL 安装：runtime.discover OBSERVED 6.4.0 build 293 arm64，bundled Java 21.0.7（metadata_only，未在此步启动/签出）。connect 观察 remote_engine_version = "COMSOL Multiphysics 6.4 (开发版本: 293)"，remote_engine_build_source=NOT_REPORTED。

| 步骤 | request_id | job_id | 结果 |
|---|---|---|---|
| runtime.discover | t07v2-b0cbd2d-b-discover-01 | b940be19-1465-49eb-b81e-eb215d3c46c9 | SUCCESS |
| project.create | …-project-01 | 7d2ac0e3-71be-4165-859f-ed63a3262dcd | SUCCESS project=5b89bbb1-dd17-41b4-b1da-4f585127df45 |
| session.start | …-session-start-01 | b708c113-7f89-4283-9e19-6dd34fc85449 | SUCCESS session-1efe8c8b25424e0d81848bd8e4a98cae；server pid 61395 port 59380 loopback-only |
| session.connect | …-session-connect-01 | 8b2eab27-f47b-4edf-84a3-a43b8542021c | SUCCESS worker_instance 6c6ad99c-5ddc-4b3b-981d-40aecb300f5b epoch 2；server_instance e9dcaea249df3a137db1a451d1d82b266e85476afd11d7f7906fd738c3f0d832 |
| model.create | …-model-create-01 | a99e5407-0850-42b8-9deb-979af11977bb | SUCCESS mcp1 gen1 rev0 |
| code.compile_java | …-java-compile-01 | b0514586-3f0c-4349-8569-e62bf0279532 | SUCCESS compiled=true，source_sha=33771c5a…4670 |
| code.execute_java | …-java-execute-01 | 04078868-ee8a-4432-a9b3-68438d7a3953 | SUCCESS revision 0→1；readback study smoke_study / sol1 / dset1 / domain_count 1；worker_build_id persistent-comsol-worker/source-snapshot-v2；isolation_proof.verified=true |
| result.evaluate | …-result-evaluate-01 | 4722d099-56ac-4a22-938d-1d60e163cbc3 | **EXECUTION_STATE_UNKNOWN（新围栏）** |

UniversalSmoke.java SHA-256 = 33771c5a69e21ebad071d86904e05a4da4b9cd4be6782e50ac2f729889494670（与 T06 证据字节一致；复制到 B2/independent-projects/t07v2-smoke-20261107/src/，复制后校验一致）。

## 新 UNKNOWN 事实（单次，未重放）

- 请求 spec 与原 T07 相同形状：expressions [u]，solution{dset1,sol1}，aggregate average，entity_dim 2，selection all(smoke_comp/smoke_geom, entity_dimension 2)，complex_mode real，storage inline；execution.expected_revision=1，rpc/execution_timeout 180。
- 响应：success=false，code=EXECUTION_STATE_UNKNOWN，stage=post_dispatch，safe_retry=false；message：result.evaluate failed with NATIVE_RESULT_BINDING_MISMATCH (aggregate actual ROI is not the requested all-entity selection); the callback did not declare a pre-dispatch stage, so the engine state cannot be shown to be unchanged。details.stage=before_run，details.role=aggregate，publish_allowed=false。
- details.witness：engine_calls=194；mutation_method=getSequenceType；mutation_issued=true；methods 列表含 getSequenceType/getSizeMulti/getSolutioninfo/getOuterSolnum 等（完整 194 dispatch 记录在 logs/b08-result-evaluate.json，82,849 bytes，原始 response 未截断）。
- 本记录不推断该 mutation 分类或 ROI mismatch 的根因，也不宣称模型无副作用；不与原 T07 的 getSolnums([]) 机制混同——两者失败形状不同。

## 新围栏清单（保留，禁止查询/重放/清理/终止/复用/接管）

- job `4722d099-56ac-4a22-938d-1d60e163cbc3`（UNKNOWN）；
- project `5b89bbb1-dd17-41b4-b1da-4f585127df45`、workspace B2/independent-projects/t07v2-smoke-20261107；
- session-1efe8c8b25424e0d81848bd8e4a98cae；model mcp1 ModelRef{schema_version 1, session-1efe…, server_instance e9dcaea2…, mcp1, generation 1}；
- Worker instance 6c6ad99c-5ddc-4b3b-981d-40aecb300f5b（epoch 2）、mcp-managed COMSOL Server pid 61395 @127.0.0.1:59380（birth start_epoch_ms:1794064262864）、控制域 B（B2/mcp-home-b）。进程未发信号、未停止。
- 原 T07：job c25bce5c-87a1-4be5-b367-20ce5624f730、project 78b3d7af…、session session-5eaf…、原 Worker/model 维持原围栏，本任务除上文偏差 1 的只读目录列举外未触碰；历史 Windows/SSH/Worker 未触碰。

## 未经验证 / 未执行

- T07 acceptance 未完成：无非硬编码数值/单位/解索引读取，无真实图像渲染与 MCP/资源回读（plot.render、artifact.read、runtime.render_probe 仅 describe 过 schema）。
- T41 runtime.doctor 未在新安装包上调用（域 B 被围栏后不再调用）；T08 持久化未触及。
- 新模型上 b0cbd2d 修复路径的净收益未证明：本次 UNKNOWN 表明该请求仍产生围栏，尽管失败形状与错误码链不同。修复与复验交由下一独立 Qwen 软件任务。

## 原运行脚本证据（不可直接复用）

- 原 runner 字节保存为 t07v2-b0cbd2d-20261107/runner/*.py.txt；其中历史 env 指向现已围栏的域 B，文件已仅作文本证据保存，不得直接运行。后续验证须编写使用全新 control home、项目、模型与 request 前缀的流程。
- 请求体：t07v2-b0cbd2d-20261107/requests/*.json；响应：logs/b01..b08 + describe_*。
- 私有原件：B2（build-src/dist/venv/mcp-home/mcp-home-b/independent-projects/logs/requests/runner）。
