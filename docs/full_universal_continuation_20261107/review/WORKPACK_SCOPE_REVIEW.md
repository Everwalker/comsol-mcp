# 工作包范围与持续执行语义独立复核

- Reviewer：独立子 Agent `/root/review_generality`。
- 审查时间（UTC）：2026-11-07T09:55:36.925532+00:00。
- 工作包：`COMSOL_MCP_FULL_UNIVERSAL_WORKPACK_20261107`。
- 续作基线：`cd2c9cdc1d862e335851bb7b2b254f01f7c8609c`。
- 结论：**ACCEPT — 仅限本工作包的目标范围、执行拆解、依赖与验收分离语义。**
- 本报告不是 COMSOL MCP 产品验收，不改变 Java 原生、平台或全项目的未验收状态；恢复工具的技术验收由其他 Reviewer 负责。

## 独立性与实际工作

本 Reviewer 未编写或修改本包的计划、状态 schema、导航工具或产品代码。首次只读审查提出主入口等待全部矩阵、T47 文案/字段不一致、失败 heldout 修复支路不明确的问题；主 Agent 修订后，本 Reviewer 重新读取现有文件并独立验证。此次仅新增本报告。

实际读取：`AGENTS.md`、`MASTER_GOAL.md`、`WORK_BREAKDOWN.md`、`TASKS.json`、`state/README.md`、`state/PROGRESS_TEMPLATE.json`、`tools/next_tasks.py`，并对照冻结合同、验收与目标配置。

没有运行 COMSOL、真实 Worker、原生 fixture、网络恢复或安装流程。主 Agent 所述的 26 项工具测试不作为本 Reviewer 自己执行的结果。

## 范围判断

U01–U12 均有实质工作承接：恢复/发现、模型/节点、版本自省、项目 Java、一般建模计算、结果与图像、持久作业、授权与数据保护、验证与迁移恢复、Desktop/交付、两类 heldout、独立终审及来源覆盖。没有遗漏一个 U 组，也没有把 U 组完成等同于测试计数。

计划保持三项活跃 native 目标、既有用户跳过及三架构构件责任。正式 heldout 仍为两个有限家族；未把 W22–W24 专项科学终点、更多物理配方、额外机器或无界性能优化重新加入 required。受控项目 Java 可以与领域接口、自省/API 共同满足范围，不强制为每个底层 API 增加 wrapper。

52 项是执行步骤，不是 52 次额外审签。既有 Java snapshot v2 软件 ACCEPT 按代码身份及受影响范围继承，不冒充当前 native/安装/全项目通过。

## 前轮问题的复核

| 问题 | 修订后的实际规则 | 结论 |
|---|---|---|
| 主入口要求全部矩阵完成才开始 G9，可能让 Mac 阻塞 Windows heldout | MASTER 执行循环第 6 步、WORK_BREAKDOWN 依赖说明、state/README 均明确：生产实现就绪，且 T43/T46 两个 Windows 版本共四个检查实际完成后可进入 T47–T49；Mac、其余生命周期/Desktop 验收留在最终适用义务中 | 已关闭 |
| BLOCKED 与 implementation_done 的关系不清楚 | AGENTS 与 MASTER 明确 BLOCKED 只阻塞仍需缺失条件的验证；implementation_done=true 可放行下游软件，不能认证 native | 已关闭 |
| T47 文案要求与结构化检查不一致 | TASKS 的 validation_check_dependencies 正好是 T43/windows-x64-6.3、T43/windows-x64-6.4、T46/windows-x64-6.3、T46/windows-x64-6.4；导航要求这些行实际 DONE，IMPLEMENTED 不够 | 已关闭 |
| T48 失败可能需要先通过才能触发 T49 | T49 依赖 T47，并使用 attempt_dependencies=[T48]；execution_complete=true 必须有 evidence_refs，只放行修复。T48 的失败保持未验收，修复/重冻/等价变体后才能关闭验收项 | 已关闭 |
| T09 可能被解释为等待整个闭环成功才修阻断 | T09 明确 T05–T08 的直接阻断在原任务即时修复，T09 仅汇总可复现路径与已验证范围 | 已关闭 |

T49 的无缺陷路径明确记录 DONE + NO_FIX_NEEDED，无需人为再制造一个修复阶段。最终技术接受仍要求全部适用 required、对应候选证据及真实独立 Reviewer；WAITING_EXTERNAL 不被描述为全部完成。

## 独立执行的有限检查

对当前 JSON 和导航函数执行了只读、内存中的结构/分支检查，不写真实进度，不调用产品：

- 52 个任务 ID 唯一；实现依赖与 attempt_dependencies 合并检查无环、无未知 ID。
- 任务表与进度模板精确对应；所有 validation_check_dependencies 都在 target_checks 中存在。
- U01–U12 均有任务映射；并结合任务正文检查其实际承接内容。
- 以下七个导航情形均得到预期结果。

| 情形 | 观察结果 |
|---|---|
| T10 BLOCKED，但 implementation_done=true | T11 软件工作仍可就绪 |
| 四个 Windows 安装/核心检查 DONE，Mac 检查及 T44/T45 验证仍 BLOCKED，相关实现已完成 | T47 可就绪，不等待无关平台验证 |
| 将其中一个 Windows 安装检查从 DONE 改成 IMPLEMENTED | T47 等待该真实检查，不能以实现完成替代 |
| T48 BLOCKED 且 execution_complete=true，有真实失败证据的占位引用，T47 已完成 | T49 修复可就绪；T48 仍在 blocked；导航不宣布产品接受 |
| execution_complete=true 但没有 evidence_refs | 状态验证拒绝 |
| T48 仅 implementation_done=true，没有实际尝试记录 | 不能满足 T49 的 attempt dependency |
| T48 已 DONE 并有证据 | 可进入 T49 的 NO_FIX_NEEDED 处理路径 |

上述输入为本次导航语义检查构造的内存状态，引用字符串也是测试占位符，绝非真实 COMSOL 证据。结果仅证明导航规则的这些分支行为。导航输出固定声明 product_acceptance=NOT_ESTABLISHED_BY_THIS_TOOL；它不核验引用文件真实性，也不执行最终产品接受门禁。

## 限制与最终判断

没有发现仍需阻断本工作包交付的范围或持续执行规则问题。计划已清楚分离：

1. 实现已就绪，可继续下游软件；
2. 实际运行或负控已经尝试，可修复其已证实缺陷；
3. 本任务判据有真实证据，可记 DONE；
4. 全部适用义务与独立终审完成，才可接受产品。

T50–T52 可以提前准备材料，最终全局接受必须满足合同，不能把导航的 ready 或 IMPLEMENTED 当接受。证明证据真实性、判断适用范围、确认独立性仍属于实际产品 Reviewer 的职责。

本次 ACCEPT 不包含恢复工具正确性、源文件完整下载、安装成功、真实许可证/平台访问、COMSOL 数值或物理结果、全部产品功能完成等未执行内容。若下列受审文件的相关规则改变，应按变化范围复核；单纯记录追加不要求重跑所有产品证据。

## 受审文件 SHA-256

```json
{
  "AGENTS.md": "5db2120eee84ec2cb3a2fa58a72f1248d11bba55b9600150314c8155ed4d13bc",
  "MASTER_GOAL.md": "1a8c996564f75a9a5baa6e07f86f8d71ca0623aab409d91f23eba620198f09b4",
  "WORK_BREAKDOWN.md": "5b1890469936e76c99eae88851d8f5a7d57cdceb0d04d127c7fee8a667d112f8",
  "TASKS.json": "437aae22f8536fcc40c182e9d62ee21b1a3172db4398c0995c95cffff14d74e2",
  "state/README.md": "a4e0e46be2d26db719bcea7b9df7adafae35342db04df979c6994ed1a0fb46ac",
  "state/PROGRESS_TEMPLATE.json": "9f2778ce0f9291dcb00d1d173877d336085712eb774199f65f0dc54e15eaf750",
  "tools/next_tasks.py": "bfc7e013cc66cd41f6a51d887556444d8815161ec2b448040ac47faf507effcf",
  "ACCEPTANCE.md": "5700380cce1189f743ae2f2153d7093c4b5454e15582389d9723df24c8e1e85d",
  "RELEASE_DOD.md": "939294d0dd0623b305b9e564a8f113d86b169957b7333d70dc9856b44ca5bdef",
  "config/UNIVERSAL_DOD.json": "40258dd90f021b8f266afd1150b2f6479a27f1ba00fcc3e1efe021ea083f87ab",
  "config/TARGETS.json": "d0ce39de90834c4cddb58b1238a8b68ca2aa2f39ead1d851a68a962651d4ad53"
}
```

