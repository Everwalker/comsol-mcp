# COMSOL MCP

中文 | [English](#english)

`comsol-mcp` 是连接 AI 助手与 COMSOL Multiphysics 的通用 MCP 服务。它提供受管理的项目、会话、模型、任务、结果和文件工作流，也保留连接已有 COMSOL Server 并由 Desktop 查看同一服务端模型的入口。它面向具体任务输入和项目级代码；不针对固定科学案例，也不保证 COMSOL 未支持的功能、许可证或物理结论。

## 当前状态与适用范围

通用核心仍在开发，尚未完成统一验收。按当前目标，待验证的平台包括 Windows x64 + COMSOL 6.3/6.4，以及 macOS ARM64 + COMSOL 6.4；当前公开证据不构成这些组合的完整产品验收。[当前源码 macOS ARM64 离线包证据](docs/universal_mcp_execution/evidence/MAC_CURRENT_SOURCE_OFFLINE_PACKAGE.json)绑定源码 `3d92b3c7`，记录离线构建、安装及安装后的 MCP stdio 初始化与 112 项工具 schema 读取，范围仅限该软件阶段。它不代表全平台或原生/T044 验收；当前源码 T044 仍未通过。查看[当前进度](PROGRESS.md)、[交接记录](docs/universal_mcp_execution/CURRENT_HANDOFF.md)、[目标平台范围](docs/universal_mcp_execution/config/TARGETS.json)和[执行目标](docs/universal_mcp_execution/MASTER_GOAL.md)。

Java 运行条件取决于 COMSOL 安装及目标版本。JDK 11 有特定环境的证据；不能据此承诺任意更高 Java 版本。请在使用前查看对应 COMSOL 版本的系统要求与本机配置。

## 安装并连接 MCP Host

使用为目标操作系统和架构生成的离线发布包。发布包包含应用 wheel、锁定依赖清单和 wheelhouse；支持离线安装，不需要运行时联网。将包解压到本机专用目录，在其中创建 Python 虚拟环境，并按包内 `locks/requirements.lock` 和 `wheelhouse/` 安装依赖。实际安装使用 pip 的 `--no-index`、`--only-binary=:all:` 和 `--require-hashes`；不要从其他环境复制虚拟环境。上述当前软件包证据只覆盖 macOS ARM64，不代表其他平台包已经验证。

以下安装命令模式对应上述 macOS ARM64 离线包；尖括号内容须替换为绝对路径，不是可原样执行的命令。解压该目标平台的离线包后，在 Host 将使用的虚拟环境中运行：

```sh
<absolute-path-to-venv-python> -I -B -m pip install --no-index --only-binary=:all: --disable-pip-version-check --no-input --find-links <absolute-path-to-unpacked-bundle/wheelhouse> --require-hashes --requirement <absolute-path-to-unpacked-bundle/locks/requirements.lock>
```

这个锁文件包含应用本身 `comsol-mcp` 和依赖；当前 Mac 包的源码、应用 wheel 与 bundle 的 SHA-256 见上述软件包证据。安装后准备 MCP 进程专用的运行状态目录与授权项目根目录，配置实际的 COMSOL 安装路径。Java 编译所用的 `COMSOL_JAVA_HOME` 应指向完整 JDK 根目录，其中应有 `bin/java` 和 `bin/javac`（Windows 对应 `bin\java.exe` 和 `bin\javac.exe`）；不要把含义不明的 COMSOL 聚合目录或仅含运行时的 JRE 当作 JDK。JDK 版本须符合目标 COMSOL 安装要求；本项目的 JDK 11 证据只覆盖特定环境。项目根目录应存在且只包含允许该 MCP 项目访问的文件。实际安装的控制台入口名为 `comsol-mcp`：Unix 类环境通常位于虚拟环境的 `bin/comsol-mcp`，Windows 位于 `Scripts\comsol-mcp.exe`。在 Host 的 MCP 配置中，将 `command` 设为当前操作系统上该入口的绝对路径，`args` 设为空数组，并在 `env` 中传入上述变量。Host 启动进程后，通过其 MCP 连接初始化并读取当前工具/操作 schema；不要从旧 README 或旧运行快照复制固定工具数量或过期签名。

MCP Host 配置形状示例（将路径换成实际路径）：

```json
{
  "mcpServers": {
    "comsol-mcp": {
      "command": "/absolute/path/to/venv/bin/comsol-mcp",
      "args": [],
      "env": {
        "COMSOL_ROOT": "/path/to/COMSOL/Multiphysics",
        "COMSOL_JAVA_HOME": "/absolute/path/to/full-jdk-11-home",
        "COMSOL_PROJECT_ROOT": "/path/to/authorized/projects",
        "COMSOL_SERVER_MCP_HOME": "/path/to/private/mcp-runtime"
      }
    }
  }
}
```

这是 macOS/Unix 风格路径的 MCP Host 配置片段，不是可直接执行的 shell 命令；Windows 请使用 `Scripts\comsol-mcp.exe` 和 Windows 格式的 JDK/项目路径。不同 Host 的配置文件位置各异；请使用其 MCP server 配置方式。不要把令牌、许可证凭据或个人目录写入共享配置或项目文件。

## 常规项目工作流

通用操作使用 `operation_describe` 读取当前操作的输入 schema，再以 `operation_call` 提交请求。先确认描述与当前安装版本匹配；新建项目和启动会话可能需要明确授权。典型流程如下：

1. 用 `runtime.discover` / `runtime.inspect` 检查可用 COMSOL 安装和运行条件；用 `project.create` 创建授权项目，之后通过 `project.inspect` 核对项目。
2. 通过 `session.start` 启动该项目的受管会话，或用 `session.connect` 连接已运行的 Server。保留服务返回的项目、会话与模型身份信息。
3. 创建新模型、按下述登记流程加载项目中的 `.mph`，或在受支持且已授权时采用现有模型。后续请求绑定返回的 `ModelRef` 和最新 `expected_revision`；不要猜测或复用其他会话的身份。
4. 先查领域操作和对象/API 描述。可用领域操作时优先使用；对任务所需的其他公开 COMSOL API，先查 `api.describe`，再通过受管 API 入口调用。实际可用性受 COMSOL 版本、许可、接口与授权约束。
5. 项目 Java 源文件放在授权项目目录内。用 `code.describe_java` 查看入口要求，以 `code.compile_java` 编译，再用 `code.execute_java` 针对指定会话和模型执行。项目代码应操作传入的绑定模型；不要用模型创建/切换代码绕过模型身份边界。Java 代码权限需要 Host/项目显式授权，且不等同于操作系统沙箱。
6. 用结果操作回读明确指定模型与解的数据；需要图像时使用绘图/渲染操作，并把输出保存在项目授权的产物目录。保存模型时明确目标相对路径与覆盖策略，再回读结果及产物状态。
7. 长作业通过任务状态接口观察原任务。等待超时或连接中断时，先查询并协调原任务；只有确认原请求未提交时才可另作提交。对执行结果未知的请求，不要重放求解或 Java 操作；按接口要求使用同一请求身份恢复，并在会话恢复后重新核对模型身份、版本和状态。

加载已有 `.mph` 前，先用 `artifact.register` 登记授权项目内的文件，设置 `role="model"`、`classification="project_local"`。把服务已返回的 `project_id`、`session_id` 放在 outer `execution`，让登记使用目标会话已授予的 `project_write` 权限；不要猜默认会话。按当前 schema，body 的 `project_id`、`idempotency_key` 也须提供，并与 outer 对应值完全相同。

以下是传给 `operation_call` 的参数形状；尖括号内容是占位符，须替换为已返回的身份、新请求标识及授权项目内实际 `.mph` 相对路径：

```json
{
  "operation_id": "artifact.register",
  "arguments": {
    "project_id": "<returned-project-id>",
    "idempotency_key": "<new-idempotency-key>",
    "path": "<project-relative-model-path>.mph",
    "role": "model",
    "classification": "project_local"
  },
  "execution": {
    "project_id": "<returned-project-id>",
    "session_id": "<returned-session-id>",
    "request_id": "<new-request-id>",
    "idempotency_key": "<new-idempotency-key>"
  }
}
```

登记成功后保留 `data.artifact_id`、`data.sha256`、`data.size`，随后以返回的 artifact id 和同一 project/session 发起新的 `model.load` 请求。`model.save` 成功结果仍使用 `size_bytes`。同一 digest 的已有登记不能通过重注册修改 classification 或身份；保留旧记录，出错先核对当前 schema 和原 job，不手改 state。

此流程描述的是公开接口的正常使用方式，不代表所有功能在每个平台均已完成原生验证。已有受控 Java 实际运行、结果回读、图像生成和模型保存记录；另有恢复结果保持 `UNKNOWN` 或 `PARTIAL_UNRESOLVED` 的记录。不要把一次成功响应推断为整个恢复生命周期通过。执行、数值与物理验证应分别判断；软件返回数值本身不证明物理假设正确。

## 连接已有 Server 与 Desktop

对于已有的 COMSOL Multiphysics Server，可使用当前运行时描述的连接、模型加载和 visible-main 操作。连接前确认目标主机、端口、模型文件和操作授权。Desktop 可以作为同一 Server 模型的可视客户端；MCP 不会自动识别 Desktop 当前窗口，也不会替用户决定应采用哪个模型。旧的 attach-first 工作流和脚本仍作为兼容入口保留，见[历史使用说明](continuation/README.md)及[设计文档](docs/comsol_mcp_design_v1/README.md)。

## 保留的历史记录

仓库曾包含多个按日期记录的阶段快照、案例验收和暂停说明。它们用于追溯当时的源码、环境和验收边界，不是当前项目状态，也不能作为当前平台、发布包或通用核心的验收声明。本 README 的[原始已发布版本（历史文档）](https://github.com/Everwalker/comsol-mcp/blob/e4daac8f35cadd6fd6776466ef2e964aef43a36f/README.md)保留原有日期快照；当前状态请以本页顶部链接为准。

## English

`comsol-mcp` is a general MCP service connecting an AI host to COMSOL Multiphysics. It exposes managed project, session, model, job, result, and artifact workflows, while retaining a path for connecting to an existing COMSOL Server that Desktop can view. Work is driven by task inputs and project-level code, not a fixed set of scientific examples. Availability depends on COMSOL, its license, supported APIs, platform, and explicit authorization; returned values do not certify physical correctness.

The universal core is still in development and has not passed unified acceptance. The target combinations currently include Windows x64 with COMSOL 6.3/6.4 and macOS ARM64 with COMSOL 6.4. The [current-source macOS ARM64 package evidence](docs/universal_mcp_execution/evidence/MAC_CURRENT_SOURCE_OFFLINE_PACKAGE.json) binds source `3d92b3c7` and records offline build/install plus installed MCP stdio initialization and 112 tool schemas in that software stage. This does not establish cross-platform or native/T044 acceptance; current-source T044 has not passed. JDK 11 has evidence in a particular environment, which does not certify arbitrary newer Java versions. See the linked progress, handoff, target matrix, and execution goal above.

Install the offline release built for the target OS and architecture. The macOS ARM64 command pattern is shown above; its lock file installs the application wheel together with dependencies from the bundle wheelhouse using `--no-index`, `--only-binary=:all:`, and `--require-hashes`. Source, application-wheel and bundle hashes are in the current package evidence linked above. Configure the MCP host to run the absolute path to the environment's entrypoint (`bin/comsol-mcp` on Unix-like systems or `Scripts\comsol-mcp.exe` on Windows) with an empty argument list. Set `COMSOL_ROOT`, a full JDK home in `COMSOL_JAVA_HOME` containing both `bin/java` and `bin/javac` (Windows `.exe` names), an existing authorized `COMSOL_PROJECT_ROOT`, and a private `COMSOL_SERVER_MCP_HOME` in the host process environment. JDK 11 is evidenced only in a specific environment; verify the target COMSOL version requirements. The host-configuration JSON above uses macOS/Unix paths and is not a shell command. The original dated README remains available at the immutable historical link above. After connecting, inspect the live operation schemas instead of relying on a stale tool list.

For a normal task, describe an operation before calling it; inspect runtime availability; create and inspect an authorized project; start or connect a managed session; then create/load/adopt a model and carry forward the returned model identity and revision. Use domain operations where they fit, and inspect public API metadata before other API calls. Put new Java under the authorized project, inspect its entrypoint contract, compile it, and execute it against the explicitly bound model. Read back results for the intended solution, render and store requested outputs under the project, and save the model with an explicit destination and overwrite policy. On timeout or interruption, reconcile the original job and session before considering any new request. An unknown result must not be replayed as a new solve. Native recovery remains subject to the documented state and evidence; some recorded outcomes are still `UNKNOWN` or `PARTIAL_UNRESOLVED`.

For an existing `.mph`, first call `artifact.register` with `role="model"` and `classification="project_local"`. Put the returned `project_id` and `session_id` in outer `execution` so registration uses the intended session's granted `project_write` permission; do not guess a default session. The current schema also requires body `project_id` and `idempotency_key`, exactly matching their outer values. Use the `operation_call` example above with every placeholder replaced. Keep successful registration's `data.artifact_id`, `data.sha256` and `data.size`, then make a new `model.load` request with that artifact id and the same project/session. Successful `model.save` still uses `size_bytes`. An existing digest registration cannot be reclassified or rebound by re-registering it: preserve the old record, check the actual schema and original job on error, and do not edit state by hand.
