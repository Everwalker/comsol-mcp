# 当前续跑交接 — 2026-10-22

本轮执行通用核心合同，目标仍为 **IN_PROGRESS**。唯一续跑指针是 [state/RESUME.json](state/RESUME.json)；冻结完成标准见 [RELEASE_DOD.md](RELEASE_DOD.md)。原三类案例的科学终点保留原状态，通用能力仍 required。

固定源码 `cd47281777eae6f4ea0d40c5c754dca9982eda86`、tree `94e6523bfffe21ace621045710335bea5512c3c1` 已恢复并核验 21,682 个原始跟踪文件。候选源码恢复并校验 3,978 个映射、1,105 个对象，尚未批量覆盖生产树。Git 基线已恢复到 `universal-core`，未 reset/checkout 当前工作文件。恢复工具缺失可选 AGENTS 的问题已修复；36 项恢复工具软件测试通过（含恢复副本自校验）。

M1 公开签名/effect、多类型与 null、6.3/6.4 实际版本适配及 Windows 会话验证接线的软件阶段已由 Main 集中审查：303 测试通过、112 工具源外安装 MCP smoke 通过，8 个受影响生产文件与已测试 wheel 字节一致。执行与 Main 审查见 evidence/M1_20261022_execution.json 和 evidence/M1_MAIN_STAGE_REVIEW.json。MCP smoke 使用 recording backend 和已准备依赖路径，不是完整离线安装或原生 COMSOL 验收。一次性 273 动作/427 子断言映射完成并防止重置已有状态。最终候选尚未冻结，独立 Reviewer 与两类未预设任务仍 NOT_RUN。

Main 已做小型 M3 单位补丁：通用 Interp 不再按四个光学表达式限制 native-default；空白原生单位不再误标 VERIFIED。新回归先复现 8 失败/18 通过。修复后两模块为 256 通过/1 失败；五模块受影响回归为 347 通过/1 失败/1 明确排除。两项缺失历史 fixture 的失败分别保留，未改成 SKIP 或 PASS，详见 [单位证据](evidence/unit-default/affected-result.json)。这些都是软件缝隙测试，COMSOL 原生单位及科学验收 NOT_RUN。

本机 macOS ARM COMSOL 6.4.0.293/JDK11 和 Windows 主机 6.3.0.290/6.4.0.293/JDK11 已只读确认可达，见 [环境预检](state/ENVIRONMENT_PREFLIGHT.json)。Windows 已有 standalone Desktop 保留；不接管、不终止。历史 R6I/once/UNKNOWN 原件未重放，新项目不继承旧权限或 PID 身份。

M0/M1 软件阶段已普通推送并独立回读为 `4759c097eabfdf588a366af51d9c7d1b90d1ac1b`。

下一步：同一 Luna 正在执行普通已安装 MCP 的项目 Java→绑定模型→真实求解/回读/产物闭环，再完成结果、恢复、安装及活跃目标验证。不能把本次源码恢复、软件测试或同步写成整个目标完成。

M4 输入准备已完成可得部分：Windows34/34、ARM41/41、Intel40/41。上游 cryptography49起移除Intel Mac支持，50.0.1缺官方Intel wheel；同版本私有派生构建独立进行，不能宣称上游支持或Intel硬件通过。原r13 pyproject哈希与当前相同，uv.lock确实缺desktop extra；核心M1改动仍需新的源码/构件绑定。

M4 通用发布工具阶段已由 Main 限定审查通过：`tools/universal_release.py` 复用既有 source/wheel/hash/SBOM/打包 API，不再增加固定 current175-rN profile。131 项软件测试无失败/跳过，三架构 ZIP 共 154 个清单成员核对通过（ARM53/Windows46/Intel55），175 个当前源成员与快照一致、172 个包成员与测试 wheel 一致；Mac ARM 干净环境已 `--no-index --require-hashes` 安装并启动112工具。详见 evidence/M4_RELEASE_TOOLING.json。Windows 安装、完整原生离线及最终 Reviewer 未验收；后续生产/README变化需新源码快照，不能将旧构件改名冒充。

Windows PREP 随后已由 Main 限定审查：实际35 wheels离线安装/pip check、172包源字节一致、真实MCP stdio初始化与112工具发现通过；6个项目元数据调用是生产dispatch进程内调用，不冒充MCP RPC覆盖。两版doctor各7项静态PASS但汇总UNKNOWN，原生未启动。只读T037已复现SID转换故障，同一Windows Luna正修复；主机plan尚缺精确网络/绑定材料，账户/SCM/AppContainer/ACL/WFP均未授权或应用。当前低可提交内存仅一次观察，提案10GiB不是新增完成门槛。证据：evidence/WINDOWS_PREP.json，后续源修复/计划另记录。

M2a 新项目Java经公开已安装MCP编译通过。首次执行因项目输入漏传参数名而未成功，原job `a1d8c9ed-2d03-407b-8d0c-65b7b0cc8030` 保留UNKNOWN；Worker请求已确认静止且未重放。注册会话必须由公开session.recover核验原Worker/模型/revision，job.reconcile独自不解除门禁。继续使用原target/sentinel，不接管旧科学活动；原始引用及下一动作以RESUME和evidence/M2A_NATIVE.json为准。

README已修正当前通用受管流程、源外项目根与外部JDK11说明，旧快照保留；该文档变化尚未进入47d05a8e的已测构件。M2b还需受控Java默认禁止全局模型切换、compile禁注解处理及UNKNOWN按冲突Server资源隔离的实质修补/负控；最终候选须重新捕获源码并按影响范围验证，不能重标旧wheel。
