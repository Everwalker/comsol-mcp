# G3.5: W18 定向收口与 W19 作业控制、取消、恢复与并发进度记录

- **阶段状态**: `G3_5_MAC_W19_VERIFIED_SCOPED` (COMPLETED)
- **验收目标**: `NEXT_GOAL.md` (Gate A 定向修复 G01–G12 + W19 持久作业、取消、恢复与并发 J01–J10)
- **权威判定**: **22/22 PASS**
- **原生取消能力状态**: `UNSUPPORTED_NATIVE_CANCEL`（真实 COMSOL 6.4 API 无原生求解取消接口，如实报告取消已接受但引擎未停，严格拒绝未授权强制终止）
- **云端宿主状态**: `HOST_DELIVERY_UNVERIFIED`（本地 Stdio 经由 MCP Gateway 验证，云端 Hermes 凭据未配置保持未验证）
- **最新运行 ID**: `g3_5_acceptance_20260923T084906Z` (全量实机耗时 16.89s)
- **基线提交**: `20839628aa6f93272a463f4d88eb48704b971f87`
- **停止边界**: 严格收工于 W19，不进入 W20–W26，不扩展任何主机特权
- **交付包**: `COMSOL_MCP_G3_5_DELIVERABLE.tar.gz`（不推送至 GitHub，打包全部交付产物后停止）

---

## 1. Gate A (G01–G12) 关键修复清单

1. **G01 源码恢复与四路径隔离**:
   - 恢复自锁定公开提交 `20839628aa6f93272a463f4d88eb48704b971f87`（tree: `2e72a4fa6eae809bbce92e4620592e6906d3e87b`）。
   - 验证 Bootstrap 树哈希与安全相对路径算法。
   - 实现包安装路径 A、源码路径 B、项目数据根 C、启动 cwd D 的严格解耦。
2. **G02 统一原子发布与覆盖防护**:
   - 移除“新 staging 缺失即沿用旧图”的虚假逻辑。
   - `allow_overwrite` 强制布尔判定，未显式授权覆盖时已存在目标绝对不被篡改（`DESTINATION_EXISTS`）。
   - 失败不碰已有产物，成功原子发布新产物。
3. **G03 清理与属性恢复单调升级**:
   - `export.run` 在 `finally` 块中恢复原始属性；清理/恢复失败传播至错误信封与 `_ModelState.dirty`。
   - dirty 状态单调上升，防止在未决模型上进行后续写入。
4. **G04 产物路径严格收敛与并发防护**:
   - 验证路径逃逸拦截与原子重命名无截断保护。
5. **G05 科学绑定与解索引强校验**:
   - 区分数据集上游引用（dataset）与实际求解解（solution）。
   - 对不存在的 solution 标签 fail-closed 抛出 `SCIENTIFIC_BINDING_FAILED`。
   - 多时刻（t=0.5 与 t=1.0）瞬态渲染验证图像严格区别。
6. **G06 Typed 属性与完整三级路径**:
   - 2D 矩阵 `[[...]]` 保留原生数字嵌套数组结构，严禁字符串化为扁平文本。
   - 支持三级子节点路径 `pg/feature/subfeature` 穿透访问，对深度 > 3 的路径在写前强拒绝（`UNSUPPORTED_PATH_DEPTH`）。
7. **G07 MCP 交付契约与 PNG 全块校验**:
   - 实现完整 PNG 结构解析（验证 IHDR、IDAT、IEND 块，校验像素和大小预算）。
   - 截断数据或坏块绝对不生成 `ImageContent`。
   - 失败 envelope 严禁携带科学图像；交付失败仍完好保留原 `job_id`、`operation_id` 等执行元数据。
8. **G08 真实 storage=artifact Wire 预算**:
   - 针对实际 `result.evaluate(storage="artifact")`，先断言 `success: True` 与 `isError: False`，再验证轻量 wire payload 中剥离全量 values/field_array。
   - 异常 malformed envelope 作为独立负控通过。
9. **G09 源码清单审核与防篡改**:
   - 7,219 项文件全量 SHA-256 审计对比。
   - 单文件改动负控验证：任一文件篡改立即拒绝同源。
10. **G10 冷启动与三入口等价**:
    - `plot.render`（点分命名）、`plot_render`（下划线命名）与底层动作执行获得完全一致的渲染结果与哈希。
11. **G11 真实 Host 与共享 Server 边界**:
    - 运行时严格记录并保护外部已有 mphserver PID，禁止任何未经授权的跨进程干扰。
12. **G12 模型存盘重开与交付恢复**:
    - 通过 `RemoteClient` 完成模型存盘并由独立进程重新加载，无需重新求解即可恢复完整几何与求解场。

---

## 2. W19 (J01–J10) 作业控制、取消、恢复与并发

1. **J01 作业目录、分页与状态语义**:
   - `OperationStore` 实现 `list_jobs(offset, limit, status, project_id)`。
   - 支持 `status` 过滤与 `project_id` 租户边界隔离，返回类型化 `JobList`。
   - 包含 SQLite 索引 `idx_jobs_status` 与 `idx_jobs_created_at`。
2. **J02 排队取消与防派发机制**:
   - `cancel_queued(job_id)` 在事务中原子仲裁 `QUEUED -> CANCELLED`。
   - 确认未向引擎派发（`engine_dispatched: False`），重复取消幂等返回 `ALREADY_CANCELLED`。
   - 严格单调终态：已取消作业拒绝向 `RUNNING` 倒流。
3. **J03 运行中取消政策与所有权防护**:
   - 识别真实 COMSOL 6.4 API 无原生求解取消接口，返回明确语义：`UNSUPPORTED_NATIVE_CANCEL`（`cancel_accepted: True`, `engine_stopped: False`）。
   - 强制停止（`force_stop`）实施严格作用域鉴权：非授权拒绝 `UNAUTHORIZED_FORCE_STOP`；共享/非托管服务严格拒绝 `CANNOT_TERMINATE_SHARED_SERVER`；PID 不匹配拒绝 `PROCESS_IDENTITY_MISMATCH`。
4. **J04 宿主断连与幂等恢复**:
   - 重复请求凭相同 `idempotency_key` 命中缓存直接返回，不重复调用引擎。
   - 键相同而请求不同安全拒绝（`IDEMPOTENCY_CONFLICT`）。
5. **J05 控制进程重启协调与静止状态**:
   - 重启时未完成作业进入 `RECONCILING` / `UNKNOWN`，必须显式协调后方可接收新写入。
6. **J06 子秒级响应与无阻塞控制读取**:
   - `job_list`, `job_status`, `job_wait`, `job_cancel` 等控制读取操作跳过串行引擎队列直接返回。
   - 实测 50 次采样 p95 响应时间远低于 1.0s 目标。
7. **J07 单服务串行化控制**:
   - 单一 COMSOL 实例上的非只读引擎请求严格串行排队执行，防止多线程并行写损坏模型。
8. **J08 期限策略与超时解耦**:
   - 区分 RPC 等待超时（返回 pending，后台作业继续安全执行）与队列排队超时（超期在派发前直接标记 `EXPIRED`）。
9. **J09 存储与进程安全**:
   - SQLite 启用 WAL 模式，完备建立状态与创建时间索引。
10. **J10 完整全链路验收**:
    - 端到端完成：模型求解（Solve） → 物理场定量抽样（Evaluate） → 特定解瞬态渲染（Render） → MCP Gateway 安全图像包装（Delivery）。

---

## 3. 验收用例表 (ACCEPTANCE G01–G12, J01–J10) 全部通过

| 用例 ID | 验收范围 | 判据与结果 | 状态 |
|---|---|---|---|
| G01 | SOURCE | 固定公开 PIN 校验、四路径隔离与恢复算法 | PASS |
| G02 | STAGING | 统一新产物发布，禁止复用既有目标，覆盖显式授权 | PASS |
| G03 | CLEANUP | 属性恢复失败与清理失败追踪，模型 dirty 单调升级 | PASS |
| G04 | PATH | 原子重命名、覆盖控制与安全目录收敛 | PASS |
| G05 | BINDING | 真实解索引绑定，多时刻独立渲染，错解 fail-closed | PASS |
| G06 | PROPERTIES | 2D 矩阵保持、Typed 属性与 3 级路径穿透访问 | PASS |
| G07 | GATEWAY | PNG 完整结构与块校验，坏块拒绝，失败信封无图 | PASS |
| G08 | BUDGET | 真实 storage=artifact wire 预算截断与完整数据分离 | PASS |
| G09 | AUDIT | 7,219 项文件审计与单文件改动负控 | PASS |
| G10 | ENTRYPOINTS | plot.render / plot_render / operation_call 三入口等价 | PASS |
| G11 | BOUNDARIES | 真实 Host 与共享 Server 边界隔离与存活校验 | PASS |
| G12 | RECOVERY | 模型 MPH 存盘重开并恢复渲染，交付回执闭环 | PASS |
| J01 | JOB_DIR | 作业目录、分页、状态与项目租户过滤 | PASS |
| J02 | CANCEL_QUEUE | 排队作业原子取消，无引擎派发，单调终态 | PASS |
| J03 | CANCEL_RUN | 运行中取消 UNSUPPORTED_NATIVE_CANCEL 与共享服务防护 | PASS |
| J04 | IDEMPOTENCY | 幂等请求缓存直接恢复，冲突拒绝 | PASS |
| J05 | RECONCILE | 控制崩溃后重启自动进入 RECONCILING 协调 | PASS |
| J06 | LATENCY | 控制读取脱离引擎队列，p95 < 1.0s 子秒响应 | PASS |
| J07 | SERIAL | 单 COMSOL 实例引擎请求严格串行化 | PASS |
| J08 | TIMEOUT | RPC 等待超时与排队期限独立策略 | PASS |
| J09 | STORAGE | SQLite WAL 模式、持久化索引与进程安全 | PASS |
| J10 | TOTAL_CHAIN | 求解 -> 测温 -> 渲染 -> 网关回传全链路通过 | PASS |
