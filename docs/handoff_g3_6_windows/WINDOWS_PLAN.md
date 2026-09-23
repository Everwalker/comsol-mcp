# Windows 双版本实现方案

## 架构选择：同源码、两个独立运行配置

先采用两个独立 stdio MCP/daemon profile，代码和依赖相同，各自持有对应 COMSOL Server 与 Java Worker。这样能复用目前单 Server 队列和持久化，而不为双版本适配重写所有业务。

| 项目 | profile win63 | profile win64 |
|---|---|---|
| 安装选择 | 实际 COMSOL 6.3 root/build | 实际 COMSOL 6.4 root/build |
| Java classpath | 6.3 官方 client manifest | 6.4 官方 client manifest |
| Worker/JVM | 独立，外部 JDK11 x64 起点 | 独立，外部 JDK11 x64 起点 |
| prefs/tmp/recovery | `.runtime/windows/win63/…` | `.runtime/windows/win64/…` |
| 控制 DB/endpoint/receipt | 独立 namespace | 独立 namespace |
| 模型产物 | `projects/win63/…` | `projects/win64/…` |
| 证据 | `evidence/windows_dual_version/<run>/win63/` | 同目录 `win64/` |

运行配置 JSON 只提供目标版本与路径提示，不赋予进程控制授权。启动后必须由引擎 `getComsolVersion()` 等真实观察确认，不仅检查文件夹名。进程/回执/handle/compiled cache 全部含 runtime identity。连接到错误版本应写前失败；不自动换成另一个“恰好能用”的版本。

## 必须复用的既有基础

- `_java_worker.py` 的 `.exe` 和 classpath 分隔符、完整 manifest 根选择；
- `_platform_process.py` 的 Windows OpenProcess/GetProcessTimes 无副作用查询；
- `_control_client.py` 的先连接现有 daemon、Windows breakaway 拒绝提示；
- `_execution_contract.py`、`_domain_outcome.py`、`_execution_service.py` 的身份与结果语义；
- W17 数据/复数/解轴/ArtifactStore、W18 渲染与网关代码；
- 原有 Windows 生命周期软件证据与 `portable_engine_regression.py`，仅作参考，不升级其证据等级。

## 平台差异表

| 关注点 | Windows 要求 | 不允许的捷径 |
|---|---|---|
| 启动器 | 从指定安装的 `bin/win64` 解析 `comsolmphserver.exe`，按该版本 `-help` 生成参数 | 复制 Mac `bin/comsol mphserver` |
| JDK/classpath | 外部 JDK 与对应产品 jar 分离，`java/javac/javap` 同一 JDK；编码 UTF-8 | 用 PATH 中第一个 Java 或混合两个版本的 jar |
| Server 隔离 | 验证本任务所有权、实例启动身份、网络边界与私有 ACL | `if os.name==nt: return True` |
| 进程持久性 | 验证 Host Job 约束，独立 broker 路线可用时明确使用 | 关闭 stdio Host 后引擎被 Job 回收却仍称持久化 |
| 停止 | OS handle/创建时间 + 所属 Job/树，实际等待退出 | `taskkill /IM java.exe /F` 或返回码为0就称停止 |
| 文件 | 私有目录 DACL、junction/reparse/ADS、共享占用、原子发布 | 把 chmod 0700 当 Windows 隔离证明 |
| 图形 | COMSOL 原生 graphics/软件或硬件渲染，记录实际模式 | 用生成图片或旧 PNG 补位 |
| 注册表 | 只读受限安装项，用 DisplayName/InstallLocation | 搜集/打印所有用户配置或许可证秘密 |
| 跨版本文件 | 各自创建保存重开；高版本兼容读单独测 | 宣称任意 6.4 文件能无损保存为6.3 |

## 别把安装成了当作验收通过

Windows x64 的 Python 版本需按项目要求和 wheel 可用性验证，可复用已有可用解释器建立新 venv。已提交 Windows cp312 requirements 与 Mac constraints 均是候选，不是本轮机读锁。建立成功环境后保存 pip freeze、pip check、构建信息和包哈希；不要为了追新版本升级全部依赖。

JDK 11 是两版本外部 client/server API 的共同认证起点。COMSOL 6.4 程序自带 Java 21 不等于它的外部 API Worker 必须或应该用 Java 21；产品与 Worker 分别验证。不要未经判断修改 COMSOL 自带 Java。

## 取消路线的结论格式

按每版本分别输出：
- `queued_cancel`: 真正未派发取消；
- `cooperative_cancel`: 实测可用入口 / UNSUPPORTED_IN_ADAPTER / UNVERIFIED；
- `owned_process_termination`: 只允许任务独占实例，记录副作用和恢复；
- `shared_server_force_stop`: DENIED；
- `cancel_intent_recorded`: 与上述停止结果分开。

官方 batch 的 `-cancel/-stop/-operation` 不自动适用于当前附着 Server 的任意 study。可作为明确独立 backend 研究，但本轮不能把改成 batch 当作偷偷完成 server API 取消。

## 进度效率

不要新建成百上千个适配层文件。优先在已有平台模块和测试框架中扩展；只有真实职责边界才拆模块。先做两个版本最小编译/连接，再适配完整链；共享修复后重跑另一个版本的影响范围。没有 Mac 不必停工，但明确 Mac 新版本实机回归待验证。
