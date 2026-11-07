# T07 all-selection ROI 实际失败诊断与通用修复（Qwen 子任务 · 2026-11-07）

## 范围与边界

- 工作树 /Volumes/SSD/Comsol-MCP/workspaces/universal-completion-20261107（与 T13/T19 等并行共享；未触碰其文件）。
- 实际修改：comsol_mcp/_g3_results.py、tests/test_g3_results.py、tests/test_g3_w17.py（fixture 对齐 6.4 GeomSequence 已有 getter），以及本证据文件。未改 _measure_spec.py、_g3_common.py、_g3_solution/_solution_binding、任何授权文件（_project_authority.py/_project_root.py/_managed_backend.py）与 T19 文件预算 agent 文件；未扩 allowlist，未削弱绑定/权限或 UNKNOWN fence。
- 未启动 COMSOL/Worker/SSH；未 git stash/reset/checkout/clean；未提交；未改 G1.json/FULL_COMPLETION。新 T07v2 围栏作业 4722d099-56ac-4a22-938d-1d60e163cbc3 与原 T07 c25bce5c 的 project/session/model/Worker 均未查询（唯一例外见下）、未重放、未清理。

## 已入库 public_result_T07_candidate_b0_UNKNOWN.json 能/不能证明什么

- 能证明：请求 selection kind=all(component smoke_comp/geometry smoke_geom/entity_dimension 2)+aggregate average；响应 EXECUTION_STATE_UNKNOWN（details.stage=before_run, role=aggregate, cause NATIVE_RESULT_BINDING_MISMATCH "aggregate actual ROI is not the requested all-entity selection"），witness 194 dispatch、mutation_issued=true、safe_retry=false。
- 由源码可确定：kind=all 时 all_selected 恒为 True（仅由请求派生），因此该分支 raise 的唯一可能触发项是 actual_named is not None。这是推导，非引擎返回值实测。
- 不能证明：named() 的实际返回值（public 响应不含 RPC reply），也不能证明其它 getter 返回值或模型副作用范围；witness 将 getSequenceType 等只读 getter 保守标为 mutation 属既有分类，未改动。

## 只读 job.log 观察（主 Agent 限定例外）

- 方式：全新安装（B2/venv, wheel 0.1.16, b0cbd2d 源码）stdio MCP 客户端，COMSOL_SERVER_MCP_CONTROL_MODE=attach-only，专用一次性脚本（/private/tmp/t07roi_joblog_pinned.py，钉死 job.log + 该 job id + limit 500 + cursor 分页 + 无重试；preflight 失败即停，不启动/修复 daemon）。仅 job 4722d099；无 job.reconcile/wait/cancel/cleanup、无 session.recover、无 model.inspect、无 Worker RPC、无信号/清理；未触碰原 T07 job。
- 事实：单页 394 事件（第二页 0）。同一 selection handle h-76732047-1f9e-4277-a1a3-a7a6402b20c7 的成功读回：geom()='smoke_geom'、dim()=2、named()=""（wrk-fba29d20-a023-4126-b4e6-efa42f263aef, result ""), entities()=[1], isInheriting()=false。
- 原始响应两页由主 Agent 原样落盘：docs/universal_mcp_execution/evidence/full/T07-candidate-b0-joblog-page{0,1}.json 与摘要 T07-candidate-b0-joblog-observation.json；本任务脚本私有副本在 <wt>/.g3-private/full-continuation-20261107/t07v2-native-20261107/logs-joblog-readonly-20261107/。本任务未重复读取。
- 区分：这是控制面持久事件的只读观察，证明"当时被记录的那次调用返回了空字符串"；不等于模型当前状态，也不改变该 job 的 UNKNOWN 围栏（仍未解除、未再查询）。

## 源码身份

- 任务开始时工作树 comsol_mcp/_g3_results.py sha256=35987e14249e0112805dd0d9b90d44b895df326ecd1b3a4d275fe76a64f7fe84，与 G1 candidate_fix 及失败候选 b0cbd2d 记录一致（T07v2 记录亦证明 b0cbd2d..HEAD 在 comsol_mcp 为空 diff）。HEAD=0db7de7（文档 handoff）。
- 并行未提交源码（工作树状态，非 HEAD）：_g2_engine.py WT 4bc6d81efdccb9d6…（HEAD 71f1e21bb8219d4b…），_g2_registry.py WT 2819e6c701b3d995…（HEAD c8480d773315881d…）——T13 的 node.remove 路由/schema；_g2_code.py WT 56c68c655fa35891…（HEAD 92f2d1f6cbbc0dc3…）；tests/test_universal_file_budget_routes_t19.py WT a5c0fdb6432bc169…（HEAD cba2d78b79992133…，T19）。hunk 检查确认改动仅在 node.remove prepare/execute/_remove_leaf_proof 与 node.remove schema 及 T19 文件预算测试。
- 归因方法：/private/tmp/t07roi-base 基线副本与候选携带相同并行工作树状态，仅把本任务 3 个文件还原为 HEAD 版本，因此下列 FAILED 集合 diff 只归因本任务改动；本任务测试不派发 node.remove 路由，故未重复跑与 node.remove 相关的大批套件。
- 修复后 sha256：comsol_mcp/_g3_results.py=679440b63ceeab153c55a50b6045701909b115e8a1708bce6c0c3177e976caf5；tests/test_g3_results.py=b052ac3b7597aa01c708b3edc6126f1dfd986f3b119d32a402445f8f7c51d277；tests/test_g3_w17.py=8c543ff4c173a2af36579a1c3ebab0d69fe5f510b653857480adf5c8c81719ce。

## 官方 6.4 API 语义（本地 comsol_kb）

- LocalSelection.named()：String getter；空字符串表示 manual（不引用命名选区）（doc_id 7623）。Selection.getNEntities 不在 Selection；entities()/dim()/geom() getter（doc_id 7700/7494）。
- GeomSequence.getNEntities()：按实体维度计数的 int 向量，长度 space_dim+1（doc_id 4181，Programming Reference Table 3-20）。只读，不触碰 measure 工具共享 selection。

## 发现与修复（两段，均通用）

1. 空字符串误判（实际失败根因）：_native_result_binding_snapshot 现按文档语义归一化 named 读回：named_reference = None if actual_named == "" else actual_named；只有非空 tag 计为命名选区引用。roi_identity 保留 raw 读回（"" 原样入证据）。all/explicit/named 三处 MISMATCH details 增加 named_readback（并 all 加 entity_count、named 加 named_expected），修复本次"错误里没有实际值"的证据缺口。
2. 主 Agent 指出的完整性缺口（已先证伪再修）：修复前以可控 fixture（请求 all、named=''、geometry 发布 2 个 dim-2 实体而 selection 只读回 [1]）确认现码错误接受并发布（pytest DID NOT RAISE）。随后在 all 分支加入通用覆盖证明：对同一目标读 model.component(component_tag).geom(实际 geom 读回).getNEntities()，要求整数向量（space_dimension 已知时长度==space_dim+1），并断言 len(set(actual_entities)) == entity_counts[entity_dimension]；计数不可用/畸形 → 明确 NATIVE_RESULT_BINDING_UNVERIFIED（fail-closed）；计数不等 → MISMATCH（details 带两侧计数）。只用计数、不猜实体编号、不枚举标签；非 all 语义不变；命名/显式负控与 UNKNOWN fence 保持。
3. 判定依据进入公开证据：all_coverage{source, component, geometry, entity_dimension, geometry_entity_count, entity_counts_per_dimension, selected_distinct_entity_count} 写入 roi_identity["all_coverage"]，随 native_result_binding_evidence.common_roi_identity 与 readbacks[].roi 发布，并参与既有 before/after payload-read 快照比较与跨特征 common-ROI 相等性检查。
4. fixture 对齐真实 6.4：tests/test_g3_results.py 的 build_tree/_stationary_count_tree 与 tests/test_g3_w17.py 的 FWiredTree 为 geometry 节点发布 getNEntities（与各自 selection 的 all()==[1] 一致）。此前这些 fake 缺该 getter；真实 GeomSequence 文档上必有。

## 实际测试与退出码（工作树 venv .g3-private/full-continuation-20261107/venv/bin/python，PYTEST_DISABLE_PLUGIN_AUTOLOAD=1，-p no:cacheprovider，2026-11-07 UTC）

1. 反例（修复前，钉住缺口）：test_result_evaluate_all_selection_rejects_partial_membership_readback → 1 failed（DID NOT RAISE）。
2. 最终 tests/test_g3_results.py：2 failed, 258 passed；PYTEST_EXIT=1。两个红为既有环境数据红（test_actual014/015 需要未跟踪 continuation/EXTERNAL_SOURCE_MAP.json；HEAD 基线同样红，未伪造数据）。新增测试全部通过：named='' 接受并发布且公开证据含 all_coverage（geometry_entity_count=1, counts=[3,3,1]）；named='walls' 仍 MISMATCH（details named_readback='walls'，getData 未派发）；部分成员仍 MISMATCH（geometry_entity_count=2 vs entity_count=1）。
3. 相邻批 1（10 文件，含 test_g3_common/activity019/w23/typed_crud 等）：候选 46 failed/388 passed 与基线副本逐行 FAILED 集合一致（第一版改动曾在 w17/typed_crud 引入 12 红，由 fixture 修正归零后复验一致）。
4. 相邻批 2（w13/w15/solver_ops/w21_native_output/d2/ephemeral_ticket/activity022/unit_a）：31 failed/585 passed/29 errors，两侧 FAILED 集合一致。
5. trio（PYTHONPATH=tests：after_transform_order/output_unit_semantics/unit_readback_observation）：171 failed/94 passed，两侧一致。

## 残余未验证范围

- 未做原生复跑（本任务禁启 COMSOL/Worker）：all-selection result.evaluate 端到端成功（真实数值/单位/解索引/图像经 MCP 回读）仍需在全新独立资源的原生验收轮记录；T07 acceptance 未完成。
- getNEntities 在本 smoke 模型无直接 live transcript（live 同族证据仅 getSDim/lengthUnit/isAxisymmetric 在同一请求上下文成功）；其 live 成功依赖原生轮确认。
- void regions：Selection.all() 文档明言不含 void 区域，getNEntities 是否计入 void 未由文档明确；含 void 模型可能被计数比较 fail-closed 拒绝（details 含两侧计数，可诊断）。未为假设场景增加特判。
- witness 对只读 getter 的保守 mutation 标记、EXECUTION_STATE_UNKNOWN/safe_retry 语义均未改动；两枚 UNKNOWN 围栏原样保留。
- 并行 T13/T19 未提交源码未审查正确性；本轮只确认其范围（node.remove/schema、文件预算测试）与本任务改动无关。
