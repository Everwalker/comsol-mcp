# 从干净目录恢复：边界与操作

## 1. 不依赖旧目录

所有目录以新包根、新 repository 或配置里的绝对路径计算，不使用以下历史示例作为有效输入：
`/Users/everwalker/...`、旧项目的 `.venv`、历史 PID/56389 端口、旧 isolation receipt、外置盘、
`.g3-private`、原 KB 数据库、旧临时模型。历史证据中出现这些字符串不授权访问它们。

bootstrap 只下载/校验源文件，不安装依赖、不运行仓库代码、不启动 MCP/COMSOL。
Git 路线会校验 pinned commit/tree 和每个 checkout blob；ZIP 路线重算所有文件的 Git blob/tree。
不会自动 git pull；main 后来变化不影响这个包。首次恢复可离线自测：

```sh
python3 -m unittest discover -s tests -v
```

这些是**本包恢复工具测试**，不是仓库/COMSOL 通过证据。

## 2. 数据保全表

| 内容 | 能否仅凭本包+网络恢复 |
|---|---|
| 固定提交中的源码、测试、文档、已提交模型/证据 | 可以，GitHub 该提交仍可访问时 |
| Python venv、Java 编译缓存、普通临时文件 | 可以重新生成，但不等于原环境逐位复现 |
| 文档索引 | 有合法 COMSOL 本地帮助/授权来源时重新生成；旧数据库不必存在 |
| 已提交构建脚本生成的测试模型 | 可以重新运行生成，属于新模型/新证据 |
| 未提交/ignored 文件、未保存的 Desktop 模型 | **不可以从 GitHub恢复**；需要用户自行备份 |
| COMSOL 安装、商业模块和许可证 | 不随包提供；保留合法安装或从授权来源重新安装 |
| 密码、Token、私钥、API Key、登录状态、SSH配置 | 不随包提供；本机安全登记/用户恢复 |
| 旧 Server/Worker 的内存状态、PID、旧回执 | 不能重用；建立新会话并验证 |

如旧项目尚在，可让 agent 使用 `git status --short`、`git ls-files --others --exclude-standard`
和 ignored 文件元数据做只读清理清单。不要输出秘密内容，不自动执行 clean/reset/rm/kill。
若用户已经删除旧资料，报告不可恢复部分，继续可以独立完成的新开发，不虚构旧证据。

## 3. Python 依赖重建

仓库 `DEPENDENCIES.md` 是环境记录，不是经机器验证的跨平台锁文件。
`pyproject.toml` 仍使用未固定依赖；不要把全局环境和旧 venv 当成依赖来源。

建议过程：

```sh
# 在恢复的 repository 目录
python3 -m venv .venv
# Mac:
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pip check
# Windows 对应 .venv\Scripts\python.exe；无需激活。
```

以上是**新的依赖解析**，不是历史环境保证；安装前先审查 pyproject 和依赖文档。
更好的目标是从 DEPENDENCIES 提取约束，经真实可用性验证并补直接依赖，再安装。
不得安装 PyPI 上同名 `comsol-mcp==0.1.9` 来代替本地源码。精确版本不可获得时明确记录，
不要静默换版本。记录解释器、OS/CPU、pip freeze、pip check、源码 SHA、测试命令/退出码。
本轮交付最终需真正的锁/约束文件、下载来源与构建元数据，不只一段 Markdown。

所有脚本使用 `sys.executable` 或该新 venv 的明确路径。pytest 默认排除 real COMSOL 测试：
必须把软件测试与显式开启的实机测试分开统计，不能把排除的测试计 PASS。

## 4. Java / COMSOL / 文档

发现安装并回读版本。外部 Worker 先以项目已使用的 JDK11 路线验证，不把 `JDK11+`
解释成任意新 JDK 都已兼容。逐引擎绑定 classpath；不混合 6.3/6.4 JAR，不复制商业 JAR到包。
使用 `config/runtime.template.json` 建立新的私有配置；所有 null 都需真实发现或用户配置。

文档根从已安装 COMSOL 帮助或授权来源发现，新建本地索引。旧的专用 KB 不应成为硬依赖。
公开归档只放文档来源/章节/哈希，不复制完整商业语料。

清洁恢复后先运行不启动引擎的 doctor，再完成 initialize/tools-list/schema 的 stdio 测试。
运行实机前检查：合法模块、Server/Worker 真实身份、授权/隔离模式、是否有用户模型、输出根可写。
优先新建获授权的测试会话；不占用/终止/修改未知共享会话。
已批准过的历史安装配置修改和旧 receipt，不自动成为新机器/新运行的授权与证据。
不得为扩大能力关闭身份、修订、幂等或权限门禁。

## 5. 干净房间验收与发布

第一次只在新路径 `repository/` 建环境；不能访问旧路径来补文件后声称包可自恢复。
新路径包含空格/中文的场景也需验证。无 Git 的 ZIP 恢复路线须在公开发布前拉取原 pinned
commit 的历史，在真实后代提交上应用修改；不得把新建无父提交仓库强推覆盖远端。

将新源码/测试/文档和脱敏证据提交；不提交 venv、索引库、运行凭据、商业安装文件。
本包不自动 push。agent 按用户已存在的阶段同步授权普通 non-force 发布，并校验远端 SHA；
权限不可用时保留本地提交和阻塞说明，不通过重写远端历史解决。
