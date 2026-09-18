# COMSOL MCP 全能力优化设计包 v1.0

日期：2026-09-18。基线：Ching-Chiang/comsol-mcp，`ccca65aa8277d1205c5de5fb6221e460aca5997a`。

**这是开发规格，不是已经升级好的MCP。没有附带COMSOL可执行文件、许可证、模型求解结果或声称已通过实机测试的程序。**

## 内容

| 文件 | 用途 |
|---|---|
| 00_FULL_SPEC.md | 合并全文，便于一次交给开发模型 |
| 01_ARCHITECTURE.md | 目标边界、12项风险、架构、跨平台/版本、任务/安全/部署与分阶段验收 |
| 02_ACTION_CATALOG.md / .json | 272个拟议动作；名称、输入、作用、权限、阶段、路线；JSON含顶层输入schema |
| 03_ACCEPTANCE.md / .json | 60个验收与故障注入用例，六组合环境目标，当前全为NOT_RUN |
| 04_IMPLEMENTATION_PLAN.md / 04_IMPLEMENTATION_BACKLOG.json | 26个有依赖关系的开发工作包 |
| 05_DEVELOPER_TASK.md | 可直接交给开发模型的执行指令 |
| 06_CONTRACT_NOTES.md | schema语义边界、版本/取消/配置约束 |
| common.schema.json / schemas/ | 公共类型、6个核心动作、ActionResult/CapabilityEvidence与schema示例 |
| config/ | 新控制系统的拟议配置示例；不是现有Hermes配置 |
| SOURCES.md | 22项固定源码/官方文档来源 |
| DESIGN_QA.json | 文档/schema/依赖检查记录，不是COMSOL实机验收 |
| MANIFEST.sha256 | 文件校验和 |

## 阅读顺序

先读01与05，再按04开发；02提供动作面，03决定是否真的完成。不要只数工具注册数，不用mock证明平台兼容；每个新模块都必须通过目标版本的实际API与模型回读。

## 最关键的设计

云端强模型通过领域工具、通用对象API、绑定Model的受控Java完成复杂动作。Windows/macOS共用引擎核心，GUI分别适配；6.3/6.4分离worker/JVM。模型状态、实际数据、任务恢复、来源和物理验收贯穿所有路径。不能把未封装API等同于不支持，也不能把代码执行权限等同于整机管理员权限。

## 交付和认证边界

核心六组合为Windows x64 / macOS Apple Silicon / macOS Intel × COMSOL 6.3 / 6.4。每种组合仍需指定受官方支持的OS版本和COMSOL build。平台/商业模块缺失必须明确返回blocked，不通过伪造结果填补；不承诺6.4模型通用无损降级到6.3。

动作列表是逻辑能力目录。feature-specific属性与输出data语义需在实现时绑定目标版本，当前提供的JSON不可被宣传成完整可运行MCP服务。
