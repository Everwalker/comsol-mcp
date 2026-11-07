# 独立 Reviewer 复核报告 · 同连接断言修复（0827fb9）

- 日期：2026-11-07（Asia/Shanghai）
- Reviewer：独立子 Reviewer（未修改受版本控制源码；未启动 Worker/COMSOL；未触碰历史 UNKNOWN；未 push/merge）
- 最新候选：commit `0827fb985300ca5d936a112d1ac2ebac64f192b9`（tree `e8c14565bff66a8bf2797d7b969fdd5045882434`），父提交为已审的 `234a7cef6b688464edd3cb721768a7e0b48ef998`；branch `handoff/java-snapshot-stage-20261107` HEAD 即该提交，工作区干净（仅既存未跟踪 workpack 目录）
- 本次审查范围：`234a7ce..0827fb9` 实际 diff（仅 `tests/test_java_worker.py`，+8/−1），以及原候选接受结论在新树上的沿用
- 结论：**ACCEPT**；先前 P3 发现已关闭

## 1. Diff 审查

唯一变化在 `test_source_binding_probe_and_code_request_share_one_authenticated_connection`：新增 `connections = []`；`create_connection` 的 monkeypatch 由不计数 lambda 改为 `connect()` 工厂，每次调用追加连接对象；新增 `assert len(connections) == 1`。这正面回应我 234a7ce 报告的 P3（该测试此前无法排除两条连接）。连接计数与连接对象记录独立于共享 ScriptedStream，任何回归为第二条 socket 的执行都会使计数断言失败；原有 `writes` 顺序断言继续保证单连接内 auth→probe→code 帧序。无其他文件变化，生产代码与我已审的 234a7ce 逐字节一致。

## 2. 独立复跑（Reviewer 本人命令，树 e8c14565）

环境同前：`/Volumes/SSD/Comsol-MCP/_state/java-snapshot-venv/bin/python`（editable → 候选工作树，HEAD=0827fb9），清除 `PYTEST_ADDOPTS`/`PYTEST_PLUGINS`，`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`，`python -m pytest -q -o addopts=`，JUnit 仅写 `/private/tmp/reviewer234a/`。

| 运行 | 节点 | 用例 | 结果 |
|---|---|---|---|
| 该节点 + 5 个关联 test_java_worker 节点（old-worker×2、mismatched-execute×6、forward-hash、missing-hash、invalid-v2-frames×4） | 6 | 15 | 15 passed / 0 failed / 0 skipped |
| 用户选定 15 节点全量重跑（绑定新 tree） | 15 | 26 | 26 passed / 0 failed / 0 skipped |
| Reviewer 新反例 `/private/tmp/reviewer234a/counter/test_reviewer_fix_0827fb9.py` | 2 | 2 | 2 passed |

未运行整个 `test_java_worker.py` 或宽泛 `-k`；未启动 Worker/引擎。

## 3. 反例识别验证

我复刻修复后测试的确切 mock 形状（计数 `connect` 工厂 + 共享 ScriptedStream），驱动我先前用于证明旧测试缺口的同一模拟两连接客户端（连接 1 认证+探测、连接 2 不认证直接发码）：payload/wire 断言仍然成立，但新断言下 `len(connections) == 2`，`assert len(connections) == 1` 必然失败——即先前的两连接反例现在能被识别。单连接流程仍通过计数与帧序断言（对照组）。生产路径本身此前已由我的计数测试证明恰好 1 次 `create_connection`（auth→probe→code 同一 stream），该性质未因本次仅测试修改而变化。

## 4. 原接受结论复核

`c6e078ab..0827fb9` 完整候选 = 我已审并 ACCEPT 的 30 文件 diff（+2302/−310，逐字节未变）+ 上述测试强化。15 节点与关联节点在新树上全部通过；先前报告中的非阻塞记录项（mmap 残余 TOCTOU 观察、`record_engine_method` 遗留 None 参数、Windows NOT_RUN、无真实 Worker/引擎/安装证据）状态不变。原 **ACCEPT** 结论继续成立，先前 P3 已修复关闭；无新发现需要 CHANGES_REQUIRED。

## 5. 边界

软件层证据；不推断生产/原生/Windows/安装验收；历史 UNKNOWN/NOT_RUN 记录保持原义；本复核独立于主 Agent 的运行与其 JUnit。

**结论：ACCEPT**（0827fb9 关闭 234a7ce 报告的 P3；候选维持接受）。
