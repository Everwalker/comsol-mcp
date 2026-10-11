# 当前实现工作单元 — 2026-10-11

这是 FULL_PLAN 的执行拆分，原 26/272/60 和验收标准不变。主 Agent 做方案、公共接口判断和最终检查；明确实现由 Luna Max 连续实现、测试、修复。下列工作不以注册名字算完成。

1. 当前并行：模型依赖/包/目标重建正式路由；新独占零求解读回→W23 科学 prepare/冻结/受预算 native；W24 九文件已接入软件通过→W25 保留候选窄合并。普通 COMSOL API 仅由一个执行 Agent 使用同一任务专属 server，模型交付 Agent 当前不能另开引擎。
2. 新零求解读回通过后，继续四次 study.run 预算的 W23 小 TE 科学链，再按原范围补复场、输入/模式功率、eta_mode/eta_capture、偏移/容差、相位/丢虚部负控与保存重开。W24 使用修正后的全套源，新建 template/候选及独立新 run，稳定形状与 UV→bake→cool 历史都有实际证据；编译/proxy 不替代 native。旧 UNKNOWN/预算已耗尽活动不得换 key 重放。
3. 当前冻结 r2 registry 实际投影：273 当前目录 +1 code.describe_java 合成登记；38 个目录动作不可执行。投影仅是路由可用性，软件/原生/科学/安装验收不升级。参见 evidence/ROUTE_PROJECTION_r2_20261011.json。当前模型交付和 W25 会减少其中部分缺口；完成后用同一方式重算一次，避免重复全库分析。
4. 后续模型/代码/交付单元：七个 dotted model 基本动作应有语义准确的受管路由，复用现有 create/load/tree/save 兼容后端、ModelRef/修订/项目归属和队列；list/clone/close 不能仅返回目录名字。code.recipe_register/run 复用当前代码来源 artifact、可信代码 admission 和 durable store，不另造运行器。export.report/evidence_bundle 复用已注册数据/图/日志/来源与 model.package 安全流式发布；artifact.publish 只根据已登记的 destination/authorization 外发，不默认允许网络外发或发送别人消息。
5. 后续模型域单元：function.validate、selection.rebind、geometry.export/repair、definition.mapping_validate、material.import、physics.pde_manage、mesh.import/export/convergence_study、study.initial_solution_set、solver.solution_inspect/clear/log_read/resource_configure、result.sample_grid。每项按原目录 schema/effect/scope、现有 domain module 和 COMSOL 同版本 KB/实际 API 实现；输入 artifact 用现有 resolver，输出用现有 no-clobber ArtifactStore；DYNAMIC 必须在派发前解析真实 effect；任何 partial/UNKNOWN 不能自动 safe_retry。多网格/initial solution 复用当前 W21 原有 branch/阶段机制，不建立第二套 job 系统。具体实现前只读原 schema 和目标 API，对真的版本/许可不支持做 typed refusal，不缩成只登记的 stub。
6. W25 合并只迁移相关 Desktop 方法与 opt-in provider/tests/docs，保留当前 P0/模型交付/Worker 变化。检验同 lane READY finalization/fence 并发缺口；使用真实 Coordinator/OperationStore 软件类。旧独立审核 3 PASS +1 auditor setup FAIL 和 187 软件 PASS 仍只作历史。产品权限/窗口/迁移的 native 条件单列；当前不得调用 CUA 或启动 GUI。
7. 功能稳定后重建 W26 当前三架构候选；复用已接入 release helper。源外安装、离线、升级/回退/非空卸载和模型+artifact+数据库完整恢复分别验证。旧 dev1 不能改名宣称当前 release。只能分发公共授权源、依赖及 synthetic 域夹具，不含 COMSOL 商业 JAR/许可/秘密。
8. 全量当前候选准备好后由新独立 Reviewer 一次统一审查；缺陷修复并重测受影响范围，保留失败。原目标外部机器/权限阻塞不能说 PASS 或 N/A；有安全可做任务继续，不提前终结 Goal。源码/progress/reports/handoff 保持同一当前指针，普通非 force GitHub 同步依已有授权在可审查稳定 checkpoint 执行。

后续多次计算实现参考：现 `comsol_mcp/_w21_execution.py:461 execute_case` 已在外层受管串行 lane 内按预算直接调 Worker，明确禁止内部再 enqueue；`_g2_checkpoint_ops.py` / `ExecutionService._owned_branch_scope` 提供受管 checkpoint branch 与来源修订证明。mesh.convergence_study 应复用这些机制，先证明分支/网格设置与实际解的关联，再比较注册 ObservationRef；不把多个 h 参数、编译或代理计算自动当作网格分支科学证据。UNKNOWN 立即阻止下一算例，不重置预算，不另建队列。
