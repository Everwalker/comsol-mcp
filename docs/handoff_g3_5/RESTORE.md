# 干净目录恢复与依赖

## 1. 恢复边界

本轮唯一源基线为 `PIN.json` 的 commit/tree；不要 `git pull` 到不断变化的 main 后仍使用旧验收结论。若远端前进，记录差异；本轮修复从固定基线开始，提交前再 fetch 并处理正常合并。

恢复代码使用新目录、关闭 hooks/自动 LFS smudge、拒绝特殊文件和子模块、按 Git tree 核对所有文件。历史符号链接以不执行的文本保存，并在回执中标识。不跟随链接读取旧机器目录。

默认 `python3 tools/bootstrap.py` 创建 `repository/`，Git 路线创建 `handoff/g3_5_w19` 分支。没有 Git 可使用：

```sh
python3 tools/bootstrap.py --method archive
```

archive 路线恢复文件不恢复 Git 历史。若需要发布，随后在另一个全新 Git 工作区 fetch **同一公开 commit**，核对工作文件后迁移补丁；不能假造旧父提交、强推或依赖本机私有历史。恢复失败时保留 BOOTSTRAP_FAILURE.json；目标已存在时必须选新目录，不覆盖不清空。

恢复完成才进入源码目录。当前交接文档复制至 `docs/handoff_g3_5/`；新恢复回执写在这里，不覆盖仓库根部的历史 RESTORE_RECEIPT.json。文件哈希审计不是逐行语义审计，也不是模型验收。

## 2. 新 Python 环境

当前仓库报告使用 macOS ARM64、COMSOL 6.4、Python 3.14.7；这只是旧运行记录，不要求伪造版本。发现本机解释器，检查 `pyproject.toml` 及匹配的机器可读 constraints，重新建 `.venv`。

```sh
cd repository
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]' -c constraints-macos-arm64-py314.txt
.venv/bin/python -m pip check
```

上面 constraints 仅在实际选用兼容 macOS ARM64/Python 3.14 时使用。不同版本/架构建立对应锁和独立证据，禁止悄悄放宽依赖来称原环境复现。若固定依赖当前无法下载，记录精确失败并给出明确迁移方案；不要覆盖历史锁。不要复制旧 venv，不安装另一项目的 `comsol-mcp` 来冒充本仓库。

最终在新 venv 构建 wheel，物理源外安装。包安装 A、源码 B、科学项目 C、启动 cwd D 和私有控制目录 E 必须彼此独立；真实检查加载模块路径、公开 MCP 启动、项目根和写入位置。仅构造 MockWorker 不构成安装验收。

## 3. COMSOL / Java / Hermes

商业 COMSOL 安装、有效许可证、相容 JDK 及需要的 GUI 权限不能放进本包。保留安装或通过合法途径重新配置。用实际安装版本的文档/javap/隔离探针验证 API 和 classpath；禁止在同一 Worker 混载 6.3/6.4 JAR。不要为了本轮修复自行升级 COMSOL、Hermes 或操作系统。

配置模板为 `config/ENVIRONMENT.template.json`，只含非秘密占位符。运行时必须重新发现安装目录、CPU/JDK 架构、Server 实例、端口、进程启动时间和访问保护。旧回执不能复用。项目私有凭据位于 Git 忽略的控制根，不写进公开证据、命令文本或包内。

优先采用先前已验证且本轮授权仍成立的本机隔离路径；创建专用测试 Server 和合成模型。**本机端口或自有 PID 不等于网络隔离。**不可擅自放开监听、改防火墙、认证、VPN、Tailscale、全局 prefs 或安装文件来消除阻塞。

Hermes 未安装或无云端凭据时保持 HOST_DELIVERY_UNVERIFIED。只用合成模型做云端联调；使用已经授权的凭据机制，不索要把密钥粘贴到聊天。实际 Host 图像接收不能被 `hermes mcp test` 或硬编码工具数代替。

## 4. 无法恢复的内容

未提交/ignored 的 MPH、实验文件、Desktop 未保存状态、旧 venv、原始本地日志及私有 Git 分支不能从公开源恢复。需要旧科学数据时必须报告缺失；能用已提交 recipe 重建的测试夹具应重建为新文件、新 run_id，并重新计算 SHA。

根外 `COMSOL_MCP_G3_4_W18_DELIVERABLE.tar.gz` 是旧机器的本地路径，不是公开下载地址。不要依赖它。若公开 Git LFS 文件只恢复了指针，报告并按固定 oid 获取真实构件；未取得时不能声称已包含该模型。

## 5. 下次仍可独立继续

阶段结束应输出：公开正常提交、真实 HEAD/tree、对应源码 manifest、干净项目副本或 git bundle、依赖锁、重建 recipe、验收矩阵、未验证项、凭据/运行态排除清单及恢复说明。

只打包已审查文件，不把私有控制目录、数据库、prefs、token、Hermes home 或整个未审查 Git 历史盲目塞入归档。源码净化要保留源等价证明，不能改写保护证据。被排除的历史证据需保留公开摘要和哈希；若用户将删除本地原件，必须明确不可恢复。
