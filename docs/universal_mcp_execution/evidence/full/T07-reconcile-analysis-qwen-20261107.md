# T07 UNKNOWN 只读 reconcile 分析(qwen,2026-11-07)

- 范围:仅读分析。本轮未发送任何 MCP RPC,未重新派发、重试、清理或重启;未触碰旧 UNKNOWN Worker/SSH;未重放或关闭本轮 T07 模型。
- 分析对象:本恢复仓库工作树 `workspaces/universal-completion-20261107`,运行 source_commit `cd2c9cdc1d862e335851bb7b2b254f01f7c8609c`(下称 cd2c9cd,已验证为当前 HEAD 祖先;以下四文件自 cd2c9cd 后无提交改动:`_control_daemon.py`、`_execution_service.py`、`_execution_contract.py`、`_java_worker.py`)。
- 证据来源:`docs/universal_mcp_execution/evidence/full/public_result_T07.json`;同目录 `T07-observation-c25bce5c-job-session-cache-20261107.json`;只读打开控制面 durable store `native-control/control-private/operations.sqlite3`(job_events/jobs/operations/revisions);`cd2c9cdc` 的源码快照(经 `git show` 导出到 /tmp 独立副本比对,未动工作树)。

## 1. 最后失败调用的精确定位(观测事实)

job `c25bce5c-87a1-4be5-b367-20ce5624f730`(operation `32f864f2`,request/idempotency `full-universal-20261107-result-evaluate-01`)的 job_events 共 70 条:1 条 RUNNING、34 对 worker_request(submitted/observed)、1 条 UNKNOWN 终止事件。cursor=70 的 tail probe 返回空,证明事件流完整且此后无新事件。

最后一个(也是唯一 FAILED 的)worker 请求:

- request_id:`wrk-0aa8cd17-f5bf-44e5-9010-13a28c3fd416`,type/kind=`call`
- method:`getSolnums`,args=`[[]]`(单个空 String[] 外层选择)
- receiver:handle `h-8f72089a-b678-402b-af8a-1169ccaea4a3`,java_type 由事件 123/124 的 `getSolutioninfo` 返回值明确为 `com.comsol.model.SolutionInfo`
- handle 取得链(全部有 durable 事件):`client.model("mcp1")` → `model.sol()`(h-69057aaa)→ `tags`(h-ca3a12bd)→ `get("sol1")`(→ h-37a5f379)→ `getSolutioninfo()`(→ h-8f72089a)
- Worker 终态回执:status=FAILED,code=ENGINE_CALL_FAILED,execution_state_unknown=true,message=`FlException: 未知解。 - 标记`,cause_chain=[FlException: Unknown_solution];queued=started=1794051475851,completed=1794051475912(61 ms)
- 前一个请求 `wrk-965a682d` = `SolutionInfo.getOuterSolnum()`,SUCCEEDED,返回值为 `[]`(空外层轴;reply.result 已从事件库直接读出)

Python 派发点(cd2c9cd 代码):`_g3_results._result_solution_binding` → `_fence_native_receiver(info)` 包裹 SolutionInfo → `_solution_binding.SolutionBinding._outer_labels`。因 `getOuterSolnum()` 返回空 labels,触发可选诊断 `getSolnums([])`。该调用经 `_FencedNativeMethod.__call__` → `_g2_engine._call`:失败被 fence-aware 包装器先 latch 为 first_unknown(信封 execution_state_unknown=true,`_worker_failure_unknown` 读结构化位),再原样 re-raise;`_outer_labels` 的局部 `except Exception` 随后将其记入 diagnostic_errors 并吞掉。

推断(标注):吞掉后流程短暂继续;由于 getSolnums 之后再无任何 submitted 事件,而控制面异常 cause_type=JavaWorkerError(恰为 latch 的原异常对象),结论是下一次经 `_call` 的派发(应为 numerical feature create)被 fence 的 `attempt.block()` 拦截并 re-raise first_unknown,`result_unknown_dispatch_scope` 退出时再抛出。此链条由代码路径+事件序列共同支撑,但未留运行时 trace,无法进一步精确到帧。

## 2. 失败前的引擎写入与清理

- 34 个已派发 worker 请求全部为:`model_snapshot`、`model` 获取、以及只读 getter(result/numerical/dataset/tags/get/getType/properties/getString/sol/modelNode/component/geom/getSolutioninfo/getOuterSolnum/getSolnums)。无任何 `create`/`remove`/`set` 类调用被派发。`_ephemeral_result_ticket._READS` 亦把 getSolnums/getOuterSolnum 归类为读。
- 因此:临时节点写入为零。result.evaluate 计划的临时 Interp feature 从未创建(create 被 first-UNKNOWN fence 拦截)。不存在需要清理的临时节点;`_mark_unknown_cleanup_skipped` 只标注 `created且未removed` 的记录,本次没有可标注对象。
- 控制面消息 "legacy callback raised after write dispatch" 中的 write 指控制面 ledger 写 ticket 已 dispatch(`begin_write` 后),不是引擎侧模型节点写入。

## 3. Worker FAILED + execution_state_unknown 与控制面 UNKNOWN 的区别

Worker 侧(per-call 事实,已 durable 观测):

- PersistentComsolWorker 对通用 `call` 的任何引擎抛出统一走 `catch (Throwable t) { state.fail(failure("ENGINE_CALL_FAILED", t, true)); }`(源码 439 行):请求已进入终态 FAILED,不会再有结果;generic 反射路由无法证明该调用在引擎内有无副作用,故结构化标记 execution_state_unknown=true、不可 safe_retry。
- 该 FAILED 回执被控制面作为 observed 事件持久化;`_reconcile` 的注释明确:committed completion observation 是"该调用已在旧 Worker 死亡前返回"的有效证据,替换 Worker 不能推翻它。

控制面侧(model 级认知态,fail-closed):

- result.evaluate 是写效果操作:`ExecutionLedger.begin_write` 先建 ticket(expected_revision CAS + fingerprint 快照),callback 抛任何异常 → `_execution_service` 走 `finish(ticket, outcome="unknown", changed=True)` → `_execution_contract.finish`:managed revision +1、fingerprint 置 None、dirty=true;再包成 EXECUTION_STATE_UNKNOWN(stage=post_dispatch,safe_retry=false)。
- fence 语义:dirty 或未对账的 external_event_counter 使后续写直接 REVISION_CONFLICT("external model change requires reconciliation"/"model has external changes or is dirty; reconcile before evaluating");活跃 ticket 使并发写 ENGINE_BUSY。
- durable 状态现值(revisions 表):mcp1 行 = revision 1、dirty false、active_operation_id `c330896a-25de-40d1-8451-37e480a2eebf`——该值对应 T07 ticket begin_write 时持久化的 active ticket(cd2c9cd 的异常路径 finish 不再重持久化 state),即"durable active-ticket 行仍作为 UNKNOWN fence 残留";按代码推断,daemon 内存态应为 revision 2、dirty=true、active_operation_id=None。内存态无法在不发 RPC 的情况下直接验证,标注为推断。
- 两者关系:Worker FAILED 证明"最后这次调用本身已结束且失败";execution_state_unknown=true 证明"不能排除引擎副作用";控制面 UNKNOWN 是由此推出的 model 级不可知状态——即使 per-call 全部有终态,整个写效果操作的业务结果仍不可知,job 永远保持 UNKNOWN,模型写入口被 fence。

## 4. job.reconcile 的实际证明条件(源码 cd2c9cd=HEAD)

`_control_daemon._dispatch_job_reconcile` + `_reconcile`:

1. 路由:registered_session 绑定的 UNKNOWN/RECONCILING job 必须走 durable runtime_binding 路由;`_control_read` 直路对该状态直接返回 WORKER_BINDING_UNKNOWN(safe_retry=false)。
2. 证据条件 A(纯 durable,不碰 Worker):全部 submitted request_id 都有 phase=observed 且 status∈{SUCCEEDED,FAILED} 的事件,且 submitted 非空 → 直接 `_reconcile`,零 Worker RPC。T07 当前已满足:34/34 观测齐全(33 SUCCEEDED + 1 FAILED)。该快速路径在 cd2c9cd 即存在,运行中的 daemon(pid 30511,11:03:56Z 启动)已具备。
3. 证据条件 B(兜底):若有未观测 id,须向该 job 绑定的原 Worker 逐个 `status(request_id)`;替换/新 Worker 不认识旧 id,无法证明,结果保持 EXECUTION_STATE_UNKNOWN。本轮观测显示 session runtime_live、health=HEALTHY 但 freshness=CACHED、worker_rpc_performed=false——即当前缓存不足以证明 live,若走 B 需实际 RPC。
4. 结果边界:`_reconcile` 不重放 lost callback,不改判终态——job 保持 UNKNOWN,只追加 reconciliation(逐 id 状态+durable_worker_observation 来源)、reconciled_quiescent=true、replay_performed=false、callback_completion_unknown=true。active(QUEUED/RUNNING)才转 RECONCILING;T07 无 active。
5. quiescence 边界:`_job_quiescence_proven` 对 registered_session 绑定明确"job.reconcile readback alone does not prove model revision or original Worker epoch",需要 append-only 的 session.recover/lifecycle resolution 才构成更强的静止证明(与 memory 记录的恢复边界一致)。reconciled_quiescent 标志本身不足以让 session 生命周期恢复类操作放行。

## 5. 失败根因链(观测+代码支撑)

cd2c9cd 的 `_result_evaluate_body` 在绑定阶段无条件走 `_result_solution_binding`;smoke 模型的 attached stationary 解 `getOuterSolnum()==[]`,触发 `getSolnums([])` 冗余诊断;COMSOL 6.4 对此抛 FlException Unknown_solution(与 `_solution_binding.py:188` 及工作树注释记载的运行时行为一致)。局部 `except` 本意把该拒绝降级为 diagnostic,但 first-UNKNOWN fence 先 latch,导致整个写效果操作被判 EXECUTION_STATE_UNKNOWN——诊断路径的容错语义与 fence 的 fail-closed 语义相互冲突,这是 T07 的直接触发机制。注意:局部容错后本应继续的 `numerical().create` 从未发生,这也是引擎零写入结论的关键证据。

工作区 `_g3_results.py` 存在他人未提交改动(git status M):新增 `singleton_count_witness` + `skip_empty_tuple_diagnostic`,对已验证 count-only 单例跳过该诊断——方向与上述机制一致,疑似在途 T49 修复;本轮未评审、未验证、未应用,只报告其存在。

## 6. 最小安全恢复路线(仅描述,本轮未执行)

前置:本轮观测(job.list/job.log tail/session.inspect/session.health)已确认 job 终态 UNKNOWN、事件流完整(70)、事件级 submitted⊆observed、session 记录 CONNECTED(health 为缓存)。在此证据内:

1. job 静止化:在获得明确授权后,对 `c25bce5c…` 走 durable binding 路由的 `job.reconcile`。已满足纯 durable 条件,无需 Worker RPC、无重放,预期得到 reconciled_quiescent=true;job 本身保持 UNKNOWN(这是设计终态,不应也无需"关闭")。
2. 模型写 fence:mcp1 的 dirty/revision fence 可经 `model.inspect` 带 refresh(需 project_write 权限、expected_revision 等于当前 managed revision——按推断为 2)触发 `service.reconcile`:重新快照 fingerprint/external_event_counter 并 `mark_external_observed`(dirty=false,revision 前移)。这只是再观察,不证明 UNKNOWN 调用无副作用(快照 coverage=parameters+shallow_tree_identity,CAS_NONE)。
3. 生命周期级(仅当后续确需 session 恢复语义时):走 append-only `session.recover` 严格证明;不可用 reconcile readback 或陈旧 reconciled_quiescent 标志替代。
4. 业务重试:T07 的 result.evaluate 业务意图若需完成,应在修复(如在途 skip 诊断改动)验证后,以新 idempotency_key 发起新操作;这是对 T48 实际失败证据的正常下游推进,不属于对 UNKNOWN job 的重放。
5. 全程不得:重发 lost callback、以替换 Worker 证明旧 id、把 job.reconcile 当作 revision/epoch 证明、或先"通过"T48 再修 T49。

## 7. 不确定性与证据边界

- FlException niceString 显示"未知解。 - 标记"但未带 tag 值;"COMSOL 6.4 对 attached stationary 空外层轴拒绝冗余 getSolnums([])"是对照 getOuterSolnum=[]、代码内既有行为注释与本地 KB 文档语境的最合理解释,非从异常文本独立证明。
- fence latch 的确切 Python 帧未留 trace;first-unknown 即 getSolnums 的 JavaWorkerError 由 cause_type/envelope 逐字匹配与事件序列推得,置信高但非直接运行时记录。
- daemon 内存 ledger 现态(rev2/dirty)与 running daemon 代码版本(11:03:56Z 从当时工作树启动)不可在不发 RPC/不查进程的前提下完全确证;持久 revisions 行显示的是 begin_write 时刻的 fence 快照。
- 工作区 `_g3_results.py` 未提交改动属于他人范围,本分析不作为其正确性或已生效的证据。
- 本报告不声称无需进一步证据即可继续任何派发/恢复动作;上述路线的每一步都标注了其所需授权与证据条件。
