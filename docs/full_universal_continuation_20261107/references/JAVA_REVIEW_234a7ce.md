# 独立 Reviewer 审查报告 · Java 源码快照 v2 候选

- 日期：2026-11-07（Asia/Shanghai）
- Reviewer：本轮独立子 Reviewer（与主 Agent 分离；未修改候选受版本控制源码；未启动 Worker/COMSOL/外部服务；未触碰任何历史 UNKNOWN 任务；未 push/merge）
- 候选：commit `234a7cef6b688464edd3cb721768a7e0b48ef998`（tree `f564c7bb19e65e669371039942e17ebd57cd6ee0`，branch `handoff/java-snapshot-stage-20261107`，工作区对该提交干净）
- 基线：WIP `a583f6999e3cefb02b2214772da2eaf48b793052`；WIP 的 main 父提交 `c6e078ab8dc610fc242c89c11557e5848726895f`
- 审查范围：完整 `c6e078ab..234a7cef` diff（30 个文件，+2302/−310），不是仅本轮 `a583..234a`（后者为 15 个文件：v2 见证校验、O_NONBLOCK、测试/runner 迁移）
- 结论：**ACCEPT**（附 1 项 P3 测试覆盖发现与若干记录性观察，均不改变本阶段软件验收）

## 1. 独立复跑（Reviewer 本人命令，非主 Agent 结果）

统一环境：`/Volumes/SSD/Comsol-MCP/_state/java-snapshot-venv/bin/python`（Python 3.12.13，comsol-mcp 0.1.16 editable → 候选工作树 `/Volumes/SSD/Comsol-MCP/_state/java-snapshot-partial`，与绑定提交一致）；清除 `PYTEST_ADDOPTS`/`PYTEST_PLUGINS`，设 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`；从 `/Volumes/SSD/Comsol-MCP/config/test_selection.json` 读取 15 个节点，subprocess 调用 `python -m pytest -q -o addopts=` + 全部节点；JUnit 写入 Reviewer 自己的 `/private/tmp/reviewer234a/`，未覆盖主 Agent 的 `_state/java_snapshot_stage_20261107/selected-software-junit.xml`。

| 组 | 节点 | 收集用例 | 结果 |
|---|---|---|---|
| 用户选定的 15 精确节点（reviewer_junit.xml） | 15 | 26（参数化展开） | 26 passed / 0 failed / 0 skipped（本机 POSIX，两个 skipif(nt) 节点实际执行） |
| 候选新增补充节点：byte 上限/invalid UTF-8、FIFO 非阻塞、绑定根替换+恢复保留、非法 v2 帧（4 例）、live trusted_code 权限、redacted 非身份 readback 见证、v2 事件身份篡改（6 例）共 7 节点（reviewer_extra_junit.xml） | 7 | 15 | 15 passed / 0 failed / 0 skipped |
| 可选真实 SDK 编译 harness：`test_actual_production_source_snapshot_compiler[externalJDK11 或 bundledJDK21]`（真实 COMSOL64 apiplugins JAR + Corretto 11 + COMSOL 捆绑 JDK21；harness 断言 `Worker_constructor=NOT_RUN`，未启动引擎） | 1（2 参数） | 2 | 2 passed（各约 2s，非 skip） |
| Reviewer 自有反例（counter/test_reviewer_counter.py，仅写 Reviewer 临时目录） | 9 | 9 | 9 passed |

未运行：整个 `tests/test_java_worker.py`、任何宽泛 `-k`、真实 Worker 启动、COMSOL 引擎、Windows 代码路径、旧私有状态或历史 UNKNOWN 任务。

## 2. 代码审查要点（c6e→234a 全量）

- 根句柄/目录替换/ABA：`_project_root.py` 以 O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC 逐组件开放并校验 fstat 与 lstat 的 (dev,ino) 一致，绑定后仅经 `dir_fd` 读取；根被 rename/重建/换回时读取仍落在原目录对象（候选 retain+restore 测试与 Reviewer 复核一致）；授权根 identity 变化被拒（`expected_authorized_root_identity`）。Windows 分支存在但本机 NOT_RUN。
- bytes/hash/basename/v2：`read_source_snapshot` 读原始 UTF-8 字节、SHA 对原始 bytes、CRLF 不翻译（`test_java_description_hashes_bounded…` 断言 CRLF 与规范化 hash 不同）；2 MiB 上限在 open 后 fstat 与读取循环双重检查；v2 帧要求 basename（禁路径分隔符）、canonical base64、hash 匹配，`source_artifact` 出现在 Worker 帧即拒。
- 同认证连接与旧 Worker 拒绝：`_java_worker._request` 在单个 `with socket.create_connection` 内完成认证→`source_binding_capabilities` 探测→代码帧；探测失败/协议非 2/build 不符/instance 缺失均产生 `WORKER_SOURCE_BINDING_UNAVAILABLE` 且 `source_operation_dispatched=false`，代码帧不上线；Java 侧 `sourceBindingRefusal` 同时拒绝旧控制器路径帧、错 build、错 instance、错 hash（harness 实测）。
- 真实 build/instance/entrypoint/model 回执：请求带 `expected_worker_build_id/instance_id`；回执必须回显 request/source/entrypoint/model/build/instance，否则客户端在回调与任何后续读取之前把成功声明改为 UNKNOWN（`_validate_code_execute_receipt`）；recipe 与 managed execute 在 mismatch 时不再执行 after `backend_snapshot`（调用序列断言 `[snapshot, execute]`）。
- UNKNOWN/不自动重放/不事后模型读取：`_request` 在写出代码帧后任何传输故障均记 UNKNOWN 且不重放；recipe 派发后异常直接 `EXECUTION_STATE_UNKNOWN` + `safe_retry=false`；Reviewer 反例（成功声明缺 status）→ UNKNOWN/partial_change=true。
- trusted_code/同模型/日志脱敏：live 权限撤销后 `PERMISSION_DENIED` 且 Worker 零调用（候选测试）；回执 `model_tag` 与请求 tag 强绑（`expected_model_tag`）；`_redact` 覆盖 `source_snapshot_b64/source_text` 等，持久事件与响应日志均脱敏（候选与 Reviewer 复核）。
- v2 recipe 持久事件验证（本轮新增）：`_validate_test_refs` 对含 `source_name` 的事件要求 basename 与登记源一致、`source_snapshot_b64 == "<redacted>"`、expected sha/build/instance 与 runtime_identity/readback 一致、`_source_binding_verified` 为真、observed 回执与 readback 身份字段一致；v1 路径事件走独立 legacy 分支保留原义；篡改任一字段的持久事件 → `RECIPE_TEST_UNVERIFIED`（6 参数反例 + Reviewer 复核）。
- FIFO 非阻塞（本轮新增）：`_read_posix` 以 O_NONBLOCK open + 打开后 fstat 判常规文件；候选 FIFO 测试与 Reviewer 的有/无写者两变体均在 4s 内确定性拒绝（无阻塞）。
- 保留 runner 与历史兼容：`tools/java_snapshot_runner.execute_java_fixture` 统一 v2 帧并要求 `_source_binding_verified`；run_g3_3/4/5/6/8/9 与 w24 桩全部迁移（`rg` 复核无残留旧签名 Worker 直调；W21/W23 走公开 operation，兼容）；公开 `source_artifact` 输入语义保留；PROGRESS/RESUME/CURRENT_HANDOFF 为追加式记录，旧 UNKNOWN/NOT_RUN 与冻结 0.1.16 证据未被改写。

## 3. 发现

1. [P3] `tests/test_java_worker.py::test_source_binding_probe_and_code_request_share_one_authenticated_connection`（L921-991）不断言“单一连接”。其 monkeypatch 工厂 `lambda *_a, **_k: Connection()` 不计数 `create_connection`，且每个 Connection 的 `makefile` 返回同一个共享 ScriptedStream。Reviewer 复现（`/private/tmp/reviewer234a/counter/test_reviewer_counter.py::test_reviewer_candidate_same_connection_mock_shape_accepts_two_connections`，9/9 passed）：模拟“连接1认证+探测、连接2不认证直接发码”的两连接流程仍满足该测试的全部 wire 断言；而“连接2也认证”的两连接只会在 mock 里以 IndexError 偶然暴露（reply 队列耗尽），不是断言。该测试不能排除回归为两连接——仅凭测试名不足以证明该属性。
   - 缓解事实：生产代码本身结构上单连接（`comsol_mcp/_java_worker.py` L919-963 同一 with 块），Reviewer 计数反例（`test_reviewer_probe_and_code_use_exactly_one_connection`）证明当前候选确实恰好 1 次 `create_connection`，auth→probe→code 全在同一 stream；即便未来 split 连接，代码帧仍携带 probe 获得的 build/instance 绑定，旧 Worker 降级仍被拒。故为测试断言强度缺口，不是产品缺陷，不阻塞验收。
   - 建议：在该测试或复用 Reviewer 反例节点中加 `create_connection` 计数与连接对象同一性断言。
2. [记录] 残余 TOCTOU 窗口：`_read_posix` 以 (dev,ino,size,mtime_ns,ctime_ns) before/after 校验检测读取中被改写；`write(2)` 会触发 `ARTIFACT_CHANGED`，但同项目内攻击者经 mmap 写页在回写前可保持这些字段不变，使混合字节快照自洽（bytes-hash-回执仍一致，Worker 编译收到的字节即被哈希的字节；且文件本就位于用户项目内）。本阶段边界目标（不得按路径重读、不得越出绑定根）不受影响；留作后续观察。
3. [记录/美观] `_java_worker.submit` 中 `record_engine_method(..., body.get("source_artifact"), ...)` 对 v2 帧恒为 None（约 L1174 遗留参数），无行为影响。
4. [范围] Windows 分支（`_open_windows`、`open_source_for_snapshot` 的 sharing-violation 预派发拒读）本机不可执行：NOT_RUN；两个 skipif(nt) 测试在 macOS 实际执行，不构成 Windows 句柄资格。真实 Worker/COMSOL 原生、安装入口、Windows Stage2 均维持原状态，本审查不将软件证据外推。

## 4. 复现命令（Reviewer 实际使用）

```text
/Volumes/SSD/Comsol-MCP/_state/java-snapshot-venv/bin/python /private/tmp/reviewer234a/run_selected.py   # 15 nodes -> 26 passed
/Volumes/SSD/Comsol-MCP/_state/java-snapshot-venv/bin/python /private/tmp/reviewer234a/run_extra.py      # 7 nodes -> 15 passed
cd /private/tmp/reviewer234a/counter && env -u PYTEST_ADDOPTS -u PYTEST_PLUGINS PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /Volumes/SSD/Comsol-MCP/_state/java-snapshot-venv/bin/python -m pytest -q -o addopts= test_reviewer_counter.py   # 9 passed
cd /Volumes/SSD/Comsol-MCP/_state/java-snapshot-partial && env -u PYTEST_ADDOPTS -u PYTEST_PLUGINS PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /Volumes/SSD/Comsol-MCP/_state/java-snapshot-venv/bin/python -m pytest -q -o addopts= \
  "tests/test_worker_registered_source_compiler.py::test_actual_production_source_snapshot_compiler"      # 2 passed, JDK11+JDK21
```

## 5. 边界声明

本报告仅覆盖该候选的软件层链路（含真实 SDK 编译 harness），不构成生产/原生/Windows/安装验收；`NO_RUN/UNKNOWN/PARTIAL` 的既有历史状态保持原义。本审查独立于主 Agent 的测试运行与其 JUnit。

**结论：ACCEPT**（发现 1 为 P3 测试增强建议，建议下一轮顺手补断言，不需返工本候选）。
