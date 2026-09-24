# Windows 双版本执行计划

## 保留而非重写
保留已有版本发现、官方 classpath、typed API、ModelRef、Worker/Server 串行控制、DomainOutcome、FieldArray/SolutionBinding、ArtifactStore、checkpoint 与绘图。W20 调用这些基础层；禁止新增绕过权限或状态机的“便捷验证后门”。

## 两版本独立
同源码、同冻结基准，在 win63 / win64 分别从空模型或已核验 fixture 构建并验证；不同 runtime/session/worker/model generation。6.3 输入由 6.3 创建，不让 6.4 MPH 成为必需前提。每次实际读取返回 build、schema 与限制。

## 真正公开入口
至少一个正向和每类关键负向用例：干净 venv wheel → 普通 MCP stdio initialize/tools-list/tools-call → 生产控制器/队列 → 实际 Worker → COMSOL → 结构化验证结果/产物。
不能用 collect_registry 代替 initialize，不能注入 FakeService 冒充生产冷启动，不能直调领域函数后把 production_entrypoint 设为 true。

## 建模和验证分离
测试构建器可通过受控 Java 创建工况，之后验证必须调用产品 validate.* 操作，不能在验收脚本另写一套“真正的验证器”而让产品接口继续为空。数值观察由 W17 受控求值得到，留原始响应，参考值由独立冻结的数学基准提供。

## 针对真实通路补测试
先修 W20 的空输入/无解/缺失必需观测/未知规则，然后复用已跑通的小块热模型验证。先打通一个版本的小链，随即跑另一个；不要在 6.4 写完整套后才发现 6.3 API 差异。

## Mac
公共修补不写死 win32 / PowerShell，权限适配只在 platform 层。能访问 Mac 则跑受影响真实回归，不能访问则保留历史 evidence 和新代码兼容性测试、将新实机矩阵标为 NOT_RUN；不要声称“Mac 完全保持”而无证据。
