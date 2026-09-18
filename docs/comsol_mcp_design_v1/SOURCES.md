# 来源与证据索引

核验日期：2026-09-18。代码结论来自固定提交的静态检查；官方文档说明底层接口，不等于本方案已经实施。源码发现的风险需要在目标环境回归。设计章节中的“建议/必须”是本方案的工程要求，不是声称现有项目已有功能。

## S01 — 审查基线：Ching-Chiang/comsol-mcp 提交

来源：https://github.com/Ching-Chiang/comsol-mcp/commit/ccca65aa8277d1205c5de5fb6221e460aca5997a

核验用途：基线提交时间 2026-05-14；当前检索 main 指向该提交。

## S02 — 源码：表达式求值与几何属性

来源：https://github.com/Ching-Chiang/comsol-mcp/blob/ccca65aa8277d1205c5de5fb6221e460aca5997a/comsol_mcp/_model_ops.py

核验用途：核验 numerical 集合清空、维度/geom1 默认、类型转字符串等行为。

## S03 — 源码：物理场、选择集与变量

来源：https://github.com/Ching-Chiang/comsol-mcp/blob/ccca65aa8277d1205c5de5fb6221e460aca5997a/comsol_mcp/_physics_ops.py

核验用途：核验 physics-level 选择集缺少分支、变量 set(name/expr) 语义、类型发现。

## S04 — 源码：连接超时

来源：https://github.com/Ching-Chiang/comsol-mcp/blob/ccca65aa8277d1205c5de5fb6221e460aca5997a/comsol_mcp/_connection.py

核验用途：future.result 超时和 executor.shutdown(wait=False) 并不实现底层 Java 操作终止。

## S05 — 源码：状态与锁

来源：https://github.com/Ching-Chiang/comsol-mcp/blob/ccca65aa8277d1205c5de5fb6221e460aca5997a/comsol_mcp/_state.py

核验用途：只读工具路径不获取 runtime lock，锁等待有硬编码 120 秒。

## S06 — 源码：visible-main 工作流

来源：https://github.com/Ching-Chiang/comsol-mcp/blob/ccca65aa8277d1205c5de5fb6221e460aca5997a/comsol_mcp/_tools_workflow.py

核验用途：加载主模型时会调用其他模型清理；加载存在 300 秒限制；验证不是 Desktop 当前标签页的证明。

## S07 — 源码：工具注册

来源：https://github.com/Ching-Chiang/comsol-mcp/blob/ccca65aa8277d1205c5de5fb6221e460aca5997a/tests/test_registration.py

核验用途：公开工具表为 50 项；注册测试不等于真实 COMSOL 集成验证。

## S08 — 源码：模型指标

来源：https://github.com/Ching-Chiang/comsol-mcp/blob/ccca65aa8277d1205c5de5fb6221e460aca5997a/comsol_mcp/_tools_params.py

核验用途：get_core_metrics 硬编码特定模型变量和域/边界。

## S09 — COMSOL 6.3 系统要求

来源：https://www.comsol.com/system-requirements/63/general

核验用途：Windows x64、Intel Mac、Apple Silicon；Java API JDK 8/11；产品自带 Java 11；Application Builder 为 Windows 功能。

## S10 — COMSOL 6.4 系统要求

来源：https://www.comsol.com/system-requirements

核验用途：检索时页面为 6.4 Update 3；产品自带 Java 21，但外部 client/server Java API 列 JRE 8/11；应用构建等有平台差异。

## S11 — COMSOL 6.3 PropFeature

来源：https://doc.comsol.com/6.3/doc/com.comsol.help.comsol/api/com/comsol/model/PropFeature.html

核验用途：properties、getType、getValueType、typed getter/setter、setIndex/setEntry 和选择集接口。

## S12 — COMSOL 6.4 PropFeature

来源：https://doc.comsol.com/6.4/doc/com.comsol.help.comsol/api/com/comsol/model/PropFeature.html

核验用途：同类通用属性接口；具体特征可用性须运行时验证。

## S13 — COMSOL 6.3 ModelUtil

来源：https://doc.comsol.com/6.3/doc/com.comsol.help.comsol/api/com/comsol/model/util/ModelUtil.html

核验用途：服务器连接、模型身份、版本、许可证、progress 等公共入口。

## S14 — COMSOL 6.4 ModelUtil

来源：https://doc.comsol.com/6.4/doc/com.comsol.help.comsol/api/com/comsol/model/util/ModelUtil.html

核验用途：含 ServerBusyHandler、ModelChangedHandler、blockOtherClients；后者阻塞其他客户端且无内建超时。

## S15 — COMSOL 变量 API

来源：https://doc.comsol.com/6.3/doc/com.comsol.help.comsol/comsol_api_general.47.63.html

核验用途：变量定义是 variable(tag).set(variable_name, expression)，不是写 name/expr 两个伪属性。

## S16 — COMSOL Windows 命令

来源：https://doc.comsol.com/6.3/doc/com.comsol.help.comsol/comsol_ref_running.38.31.html

核验用途：Windows 的 Multiphysics server/client 命令；不是 COMSOL Server 应用发布产品。

## S17 — COMSOL macOS 命令

来源：https://doc.comsol.com/6.4/doc/com.comsol.help.comsol/comsol_ref_running.38.33.html

核验用途：macOS 命令行启动模式；平台适配器须验证安装位置、执行权限及参数。

## S18 — COMSOL 6.4 SolverSequence

来源：https://doc.comsol.com/6.4/doc/com.comsol.help.comsol/api/com/comsol/model/SolverSequence.html

核验用途：求解状态/警告/解向量等接口；本方案不凭空假定通用 cancel() 方法。

## S19 — COMSOL 6.4 GeomSequence

来源：https://doc.comsol.com/6.4/doc/com.comsol.help.comsol/api/com/comsol/model/GeomSequence.html

核验用途：几何序列与构建接口；workplane 是嵌套几何，不能一律当普通 feature 链。

## S20 — MCP Tools 协议

来源：https://modelcontextprotocol.io/specification/2025-11-25/server/tools

核验用途：工具 JSON Schema、structuredContent、结果内容和工具发现。

## S21 — MCP Transports

来源：https://modelcontextprotocol.io/specification/2025-11-25/basic/transports

核验用途：stdio 与 Streamable HTTP；网络入口需要身份认证和安全配置。

## S22 — MCP Tasks

来源：https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks

核验用途：所查版本中 Tasks 为实验性功能；需能力协商并保留传统 job 轮询兼容。

