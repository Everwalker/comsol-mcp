# T10/U02 模型生命周期：定向软件审查记录

记录日期：2026-11-07（Asia/Shanghai）。仓库：`/Volumes/SSD/Comsol-MCP/workspaces/universal-completion-20261107`。

本文件依据本次对话中已捕获的命令和工具输出整理；整理时没有重跑测试。测试命令均将输出通过 `tail` 截取，未指定输出日志文件。对话中保留的是截取后的工具输出，不是完整原始 pytest 日志；未见另行保存完整输出的证据。命令使用管道且未设置 `pipefail`，返回的 shell 退出码属于管道末端，不能单独当作 pytest 退出码证明。以下通过和跳过数量来自已捕获的 pytest 摘要。

主 Agent 调用 `spawn_agent` 时明确请求 `omlx/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S` 和 `max`；子任务执行者首次自报使用该 Qwen 模型。后续又自述由 GPT-6 整理，与指定调用不一致。实际底层模型路由未单独验证，不能据此推断。本文不是独立 Reviewer 审查。

## 使用用户指定隔离环境的测试

这两条命令在仓库根目录执行，使用用户指定的 `.g3-private/full-continuation-20261107/venv/bin/python`，设置 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 并关闭 pytest cache provider；没有运行整个 `test_java_worker` 模块或真实 Worker fixture。该解释器的 Python 版本没有在已捕获输出中单独记录。

```sh
cd /Volumes/SSD/Comsol-MCP/workspaces/universal-completion-20261107 && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .g3-private/full-continuation-20261107/venv/bin/python -m pytest tests/test_g3_managed_models.py -q -p no:cacheprovider 2>&1 | tail -8
```

捕获的原始输出片段：

```text
.............................                                            [100%]
29 passed in 0.80s
```

```sh
cd /Volumes/SSD/Comsol-MCP/workspaces/universal-completion-20261107 && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .g3-private/full-continuation-20261107/venv/bin/python -m pytest tests/test_execution_contract.py tests/test_w24_model_connection_identity.py -q -p no:cacheprovider 2>&1 | tail -6
```

捕获的原始输出片段：

```text
............................s........                                    [100%]
36 passed, 1 skipped in 0.19s
```

原命令未使用 `-rs`，捕获片段没有显示跳过项名称或原因。上一轮答复将跳过归因于缺失历史 R6 输入；仅凭该测试输出无法独立确认这一归因。

## 此前的环境尝试

在用户提供隔离环境路径之前，还实际运行了以下命令；这些不是上述通过结果的测试环境：

```sh
cd /Volumes/SSD/Comsol-MCP/workspaces/universal-completion-20261107 && python3 -m pytest tests/test_g3_managed_models.py -x -q 2>&1 | tail -15
```

系统 `python3` 返回 `No module named pytest`，没有执行测试。随后在 `/private/tmp/g3t-venv` 创建的临时 Python 3.14.7 环境中运行：

```sh
cd /Volumes/SSD/Comsol-MCP/workspaces/universal-completion-20261107 && /private/tmp/g3t-venv/bin/python -m pytest tests/test_g3_managed_models.py -q 2>&1 | tail -12
cd /Volumes/SSD/Comsol-MCP/workspaces/universal-completion-20261107 && /private/tmp/g3t-venv/bin/python -m pytest tests/test_g3_managed_models.py -q 2>&1 | tail -8
```

两次捕获的摘要分别为 `7 failed, 22 passed in 1.13s` 和 `7 failed, 22 passed in 0.84s`。第一次截取的输出明确包含 `ModuleNotFoundError: No module named 'mcp'`；第二次发生在该临时环境的 `mcp` 安装进程尚未完成时。这些失败不能计为通过，也不替代上面用户指定环境的测试结果。

## 范围与限制

上一轮以源码和定向软件测试检查了 attach/adopt、create/load、clone、save、close 与 rebind 相关身份和生命周期路径，包括 ModelRef 的 session/server/tag/generation、revision 及项目归属，以及 foreign model 和关闭保护。`test_g3_managed_models.py` 使用 synthetic Worker，相关断言只能证明该软件桩与所覆盖路径中的行为；不能将它们扩展成完整的真实双模型隔离结论。`test_execution_contract.py` 与 `test_w24_model_connection_identity.py` 提供对应的 ledger 和身份校验软件证据。

上一轮未修改源码，未以缺少原生证据为由编造修复。其答复另提及 Windows save 回滚时 baseline 记录与 close 校验之间可能存在的不一致；该分支未在本记录中重新验证，本记录不对其影响范围作新的结论。

T10 的原生验收仍需在获授权的独立环境中证明：真实两模型目标读回及非目标保持；真实多客户端下 `modelsUsedByOtherClients` 与安全关闭；`saveCopy` 对 live filepath 的影响和 `loadCopy` 的独立性；真实连接中断、重连后的 Worker epoch、ModelRef 和 rebind 行为。领域接口、通用 API 和 Java 对同一实际绑定模型的端到端一致性也需原生证据。这些测试结果和本文件均不代表 T10 或冻结 U01–U12 已完成验收。
