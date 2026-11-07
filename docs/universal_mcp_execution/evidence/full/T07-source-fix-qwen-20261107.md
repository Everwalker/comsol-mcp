# T07 源码修复复核与整改记录（Qwen 子任务 · 第 3 并发 Agent · 2026-11-07）

## 范围与边界

- 工作树：/Volumes/SSD/Comsol-MCP/workspaces/universal-completion-20261107（与 T41 等其他子 Agent 并发共享；未触碰其文件）。
- 允许修改（第一轮）：comsol_mcp/_g3_results.py、tests/test_g3_results.py（原 Qwen 子任务未提交补丁所在两文件）；第二轮由主 Agent 依据本次任务授权将范围扩展至 comsol_mcp/_solution_binding.py 及其对应测试 tests/test_activity019_private_singleton_ticket.py；本文件为指定落盘产物。
- 未执行 stash/reset/checkout/clean；未启动 COMSOL；未改 G0.json/G1.json/GENERAL_COVERAGE 状态。原 T07 作业 c25bce5c-87a1-4be5-b367-20ce5624f730 已由其他 Qwen 任务一次性 job.reconcile（quiescent=true），但 job 仍 UNKNOWN、model/session fence 未解除；本 Agent 未再查询、replay 或清理。
- 基线对照副本在 /private/tmp/t07base（HEAD 源码+测试）与 /private/tmp/t07unfixed（原提交补丁、无本次 gate），均为临时只读对照，不在工作树内。

## 原作业证据核对（补丁注释声明 vs 真实 transcript）

public_result_T07.json：请求为 spec{expressions:[u], solution{dset1,sol1}, aggregate:average, entity_dim:2, selection all(smoke_comp/smoke_geom, entity_dimension 2)}，响应 EXECUTION_STATE_UNKNOWN / stage post_dispatch，cause FlException Unknown_solution（execution_state_unknown=true）。

T07-observation-c25bce5c-job-session-cache-20261107.json（只读控制面观测，operations_not_called 含 job.reconcile/wait/cancel/cleanup、study.run、result.evaluate）：
- worker 事件流（job.log events 65–128）在 getSolutioninfo → getOuterSolnum（SUCCEEDED，result 为 []，wrk-965a682d）→ getSolnums([])（FAILED，FlException Unknown_solution，execution_state_unknown=true，wrk-0aa8cd17）处终止，其后无任何 dispatch；job.status/job.result 均 UNKNOWN。
- 结论：补丁注释的三点声明（空 outer 轴已发布、getSolnums([]) 被拒、fence 在 count-only 门之前 latch）与 transcript 一致。

## Fence 与 UNKNOWN 语义核实

result_evaluate 由 result_unknown_dispatch_scope 包裹（_g2_engine.py:470-505）：任一 _call 收到结构化 unknown（execution_state_unknown=true 或 code=EXECUTION_STATE_UNKNOWN）即 latch，此后所有 _call 被 attempt.block 拒绝，scope 出口 re-raise first_unknown。因此 _outer_labels 里对 getSolnums([]) 的 catch 不能撤销 latch——这正是原作业 UNKNOWN 的机制。

候选补丁语义变化限定为：仅当本模型 getter witness 已验证（状态/witness_kind ∈ 已评审的 attached-singleton 集合、count∈1..4096、pair mapping 确认不完整）且请求无显式 outer/inner 时，跳过该冗余诊断；其余路径保留诊断与其 latch 语义。验证后，后续新项目/会话中同类单解评估不再把这一已观察到的只读拒绝 latch 成 EXECUTION_STATE_UNKNOWN，而是走 count-only 成功或有界拒绝（SOLUTION_AXES_UNVERIFIED/budget refusal），与既有 at_points 先例（_g3_results.py:9049-9073，同一 status/kind 集合）一致；作者第二测试固定了 getData 处 latch 不被弱化。

措辞边界（本文件不主张更强结论）：原作业记录日志只能证明“被记录的派发序列”未到达 create/set/remove，不能证明整个模型绝无副作用；原作业经一次性 reconcile 后仍为 UNKNOWN，model/session fence 未解除，不可追溯改记为成功。本修复的收益只针对后续新项目的同类单解请求，使其不再因该已观察诊断触发 UNKNOWN。

## 审查发现与实际回归（已最小修正）

F1 实际回归（修正）：原提交补丁在 _result_evaluate_body 无条件先算 singleton witness，使显式 outer/inner 选择路径也派发 witness getter（getLevels/getLevelNames/getNU/getPNames/getSizeMulti/getSequenceType/isAttached/study 及复查轮）。这些 getter 经 call_probe 走 fenced _call，任一结构化 unknown 拒绝即会把原本成功的显式选择评估 latch 成 UNKNOWN——HEAD 上该路径从不派发这些 getter。违反“显式选择/非单解路径不得多出有风险的 RPC”，也偏离 at_points 先例的 outer/inner-absent 门。
- 修正（_g3_results.py）：早算 witness 仅当 solution_spec.outer 与 inner 均缺省；skip_empty_tuple_diagnostic 以 witness is not None 为首项（短路等价）。
- 证明：在 /private/tmp/t07unfixed（保留原提交无条件 witness、含本新增测试）运行新 pin 测试失败并显示 getLevels 等已派发；工作树（含 gate）同一测试通过。

F2 witness 复用安全性（确认无回归）：count-only 门（stationary_measure_count_only / count_only_raw_eval，行 5930–5936）仅在 pair mapping 不完整时可为真，而显式 outer/inner + 不完整映射在其之前的拒绝（行 5855–5861）已 raise；故到达该门时 outer/inner 必为 None，早算 witness 必存在，直接复用不新增也不丢失任何 dispatch；count-only 路径与 HEAD 相比 getter 集合相同，仅顺序提前，且 verify_stationary_count_binding 前后复查不变。

F3 无 inner/outer 的非单解路径（第一轮曾记为“先例已接受的残留范围”，第二轮证实为实际回归，见下文 F4，已修正）：早算 witness 会波及 SolutionInfo 已发布完整非空 pair mapping 的成功请求，那些路径在 HEAD 上从不派发 witness getter。

## 第二轮审查 F4：完整非空 pair mapping 回归（证实并修正）

回归机制：第一轮门控早算仍对所有无显式 outer/inner 的请求在任何映射状态查询之前派发 witness getter。对 SolutionInfo 已发布完整非空 (outer,inner) 映射的请求（HEAD 成功路径上 witness getter 派发数为零），任何 witness getter 的结构化 UNKNOWN 拒绝都会经 first-UNKNOWN fence 把本来成功的瞬态/参数解评估 latch 成 EXECUTION_STATE_UNKNOWN。
- 证实：定向 fake（完整映射：getOuterSolnum→[1]、getSolnum→[1]；witness 专属 getter getSequenceType 改为返回结构化 UNKNOWN）在 /private/tmp/t07eager（第一轮门控早算候选）FAILED——评估被 latch 出界；同一测试在 HEAD 基线 /private/tmp/t07base passed。
- 修正（惰性两阶段；改动范围由主 Agent 依据本次任务授权调整）：comsol_mcp/_solution_binding.py 的 _outer_labels/resolve_solution_info 新增 _empty_outer_singleton_witness 可调用参数，仅当 getOuterSolnum() 真实返回空标签列表时才咨询 witness；验证通过则跳过冗余 getSolnums([]) 诊断，未验证则诊断原样执行（getSolnum fallback 仍可补全映射）；非空标签完全不问 witness。_result_evaluate_body 传入闭包（仅无显式 outer/inner 时）；count-only 门保留 HEAD parity 现场现算兜底。非空映射路径 RPC 集合与 HEAD 逐条一致（getLevelNames 属 HEAD resolve 自身轴元数据读取，非 witness，已用注释固定）。

## 与 ephemeral ticket supplemental 边界的交互（第二轮）

_result_solution_binding 在 scope.supplemental_solution_info(info) 内 resolve。_ephemeral_result_ticket.py 语义：phase 内 row 标 supplemental=True，after_submit 对结构化 FAILED 仅当 supplemental 时暂不 invalidate；phase 退出时只有 info handle 上、窄 allowlist（getOuterSolnum/getLevelNames/getSolnums([])/getSolnum/getPNames/getPvals/getUnits）且被 read_errors 记录或 getSolnums fallback 覆盖的行可标 handled_failure，其余 invalidate；dangerous reply（execution_state_unknown 等）无条件 invalidate。witness 的 count/identity getter 属 required-phase，不应被标 supplemental 或改变失败判定。
- 事实：new_binding 仅对 function is _BUILTIN_RESULT_AT_POINTS 建票（consume 要求 domain.operation=='result.at_points'），result.evaluate 当前不激活该 scope；at_points 调用 _result_solution_binding 只传静态 bool，不传 witness 闭包。
- 防呆：current_scope() 非空时 _result_solution_binding 忽略 _empty_outer_singleton_witness（resolve 维持 HEAD 的 phase 内诊断 allowlist 与失败语义），witness 只可能在无 phase 时运行；由 test_result_solution_binding_ignores_lazy_witness_under_ticket_scope 行为性固定（有 scope：witness 零调用、phase 进出各一次、映射不完整；无 scope：witness 恰好一次并产生 skip 错误行）。

## 2D 域 AvSurface vs AvVolume（原作者怀疑项）——不改 measure 实现

COMSOL 6.4 官方文档（本地 comsol_kb，6.4.0.293）：
- API 页 “AvVolume, AvSurface, AvLine”（doc/help/.../comsol_api_results.52.030.html，doc_id 4433 chunk 17202）：三个 feature id 即按 volume/surface/line 几何层级命名，selection 定义计算域。
- Reference Manual “Average and Integral”（doc_id 5976 chunk 19430，PDF 同文 doc_id 3627 chunk 14095）：“Select a Geometry level — From dataset (the default), Volume, Surface, Line, or Point. The default means the highest geometry dimension for the data in the dataset; typically volumes in 3D, surfaces in 2D, and lines in 1D.” 即 2D 模型的最高维实体（域）在官方术语中是 surface 级。
- 佐证：“surfaces (that is, boundaries in 3D)”（Excluded Surfaces 页 doc_id 5900 chunk 19323）说明 3D 中 surface=边界；2D 中 surface=域。
结论：2D 域（entity_dim=space_dim=2）用 AvSurface 与 6.4 官方命名一致；AvVolume 对应 3D volume 级。未修改 _measure_spec.py（本就不在允许文件内，且无文档依据支持改动）。原 T07 作业在 feature 创建前已终止，无 AvSurface 的原生反证；作者新测试断言 AvSurface 与文档一致。

## 原作者（Qwen 子任务）已有测试

- tests/test_g3_results.py::test_result_evaluate_stationary_singleton_survives_rejected_empty_outer_diagnostic —— 单解 + 空 outer + getSolnums([]) 结构化拒绝时仍走 count-only 成功并断言未派发 getSolnums、AvSurface、cleanup removed；候选下通过。
- tests/test_g3_results.py::test_result_evaluate_first_unknown_at_native_read_still_latches_fence —— getData 处结构化拒绝仍 raise（latch 不被削弱）；候选下通过。

## 本次执行的命令与结果（工作树 venv：.g3-private/full-continuation-20261107/venv/bin/python，PYTEST_DISABLE_PLUGIN_AUTOLOAD=1，-p no:cacheprovider；2026-11-07 UTC）

1. 原提交补丁（未整改）：pytest tests/test_g3_results.py -q → 6 failed, 247 passed。失败为 test_public_result_evaluate_stationary_count_only_uses_real_eval_budget_path[1]/[3]、..._keeps_native_default_unit_unverified、..._count_drift_after_getdata_does_not_publish、test_actual014/015。
2. HEAD 基线（/private/tmp/t07base，git show HEAD 两文件 + 工作树其余副本）：同文件 7 failed, 244 passed；其中 6 个与上同（确认这 6 个为 HEAD 既有红，非补丁引入），第 7 个 test_accepted_spec_keys_match_the_published_evaluation_spec 是我副本缺 docs/ 的拷贝伪影（工作树内两轮均通过）。
3. 定位：repro（/tmp/t07_repro.py、/tmp/t07_repro2.py）显示 4 个 count-only 红源于 _native_result_binding_snapshot 读 feature.selection() 而 _raw_eval_stationary_fixture 的假 Eval 无该方法（AttributeError→API_UNSUPPORTED→post-dispatch UNKNOWN）。6.4 官方 “Eval” API 页（doc_id 4452 chunk 17232）确有 numerical(<ftag>).selection(...)，故属测试 fixture 滞后而非生产缺陷。
4. 整改（仅两允许文件）：
   - comsol_mcp/_g3_results.py：witness 早算加 outer/inner-absent 门（见 F1）。
   - tests/test_g3_results.py：a) _raw_eval_stationary_fixture 的 EvalFeature 暴露 selection()（复用 _StationaryAggregateSelection 的 geom1/dim2/单实体视图）；b) 新增 test_result_evaluate_explicit_selection_never_runs_singleton_witness：显式 outer/inner 请求仍得 SOLUTION_AXIS_METADATA_UNAVAILABLE（与 HEAD 相同拒绝语义）且 witness getter 集合不在 engine_methods。
5. 整改后：pytest tests/test_g3_results.py -q → 2 failed, 252 passed（作者两测试、新增测试均通过；4 个既有红修复）。
6. 未整改对照（/private/tmp/t07unfixed）：新增 pin 测试 FAILED（assert 'getLevels' not in {...,'getLevelNames','getLevels',...}），证明该测试能捕捉 F1 回归。
7. 相邻受影响套件（调用 result_evaluate 的 fake-tree 套件，一次性批量）：tests/test_g3_metrics.py test_g3_w17.py test_results_typed_crud.py test_w23_native_results.py test_g3_artifact_hardening.py test_stage_definition.py tests/test_w21_canonical_contracts.py：候选 27 failed, 236 passed == HEAD 基线 27 failed, 236 passed（逐条一致，无新增失败；该批为恢复树既有红）。
   tests/test_after_transform_order.py test_g3_output_unit_semantics.py test_g3_unit_readback_observation.py 需 PYTHONPATH=tests 才能互相 import（HEAD 与候选同此限制）：HEAD 173 failed/92 passed，候选 171 failed/94 passed；FAILED 集合 diff 为候选净修复 2 个、零新增失败（fixture 修复使复用 _raw_eval_stationary_fixture 的 2 测试转绿）。

第二轮（按用户指示仅定向，不再重跑上述大批既有红套件）：
8. 回归证实：/private/tmp/t07eager（第一轮门控早算代码 + 新测试）跑 A（complete_pair_mapping pin）→ FAILED（_UnknownSolutionFlError 被 latch 出界，getSequenceType 出现在派发集合）；A 与 unverified-latch 测试 B 在 HEAD 基线 /private/tmp/t07base 均 passed。
9. 惰性修正后工作树：pytest tests/test_g3_results.py → 2 failed, 255 passed（仍仅 actual014/015 环境数据红；A、B、C 与作者两测试全通过）。
10. pytest tests/test_g3_results.py tests/test_g3_3_core_numeric_contracts.py → 2 failed, 285 passed（零新增失败）。
11. tests/test_activity019_private_singleton_ticket.py：工作树 19 failed/3 passed vs HEAD 副本（含同一新测试）20 failed/2 passed；FAILED 集合 comm diff 唯一差异为新接口测试（HEAD 红、候选绿），其余 19 个恢复树既有红两边逐行一致（此前 018/019/g3_3 三文件批量两边同为 28 failed/32 passed，确认 _solution_binding 改动零漂移）。

## 缺口与未覆盖

- test_actual014/015 仍红：tests/_published_history.py 需要未跟踪数据 continuation/EXTERNAL_SOURCE_MAP.json 与 continuation/external-source/...，本恢复树与其父目录均无该数据（HEAD 基线同样红）。属环境数据缺口，非本补丁范畴；未伪造该数据。
- 未做 6.4 原生复跑（本任务禁启 COMSOL）：T07 同类请求在原生上是否不再因该已观察诊断触发 UNKNOWN、以及 AvSurface 2D 原生成功，最终仍需原生验收轮记录；本文件只固定源码/测试与证据核对层面。
- witness getter 集合在 live 6.4 附着单解上的成功 transcript 目前仅有原作业的 getSolutioninfo/getOuterSolnum 两项直接证据；其余 getter 的 live 成功依赖既有 at_points/G3 软件阶段先例。
- 恢复树多套相邻 fake 套件存在 HEAD 既有红（上批 27 红、activity018/019 的 19/18 红等），已验证与本 diff 无关（两边 FAILED 集合逐行一致）。
- 原生 6.4 上“完整非空映射瞬态/参数解 + witness getter 结构化拒绝”的组合仅由 fake 固定；live 复现依赖原生验收轮，但该路径现在与 HEAD 派发集合逐条一致，原生行为不应偏离 HEAD。

## 变更清单（本 Agent 实际写入）

- comsol_mcp/_g3_results.py：第一轮 +outer/inner-absent witness 门；第二轮改为惰性 witness 闭包（经 _result_solution_binding/_solution_binding 参数传入）+ count-only 门 HEAD parity 现算兜底（witness 未算则现场计算，不把未算缓存当失败）+ current_scope 非空时忽略 witness 闭包的 ticket 边界防呆。
- comsol_mcp/_solution_binding.py（第二轮；主 Agent 依据本次任务授权调整范围）：_outer_labels/resolve_solution_info 新增 _empty_outer_singleton_witness 关键字参数，仅在 getOuterSolnum() 真实返回空标签列表后咨询；验证通过则跳过 getSolnums([]) 诊断（错误行注明 skipped for verified singleton numeric-count read），未验证则诊断与 getSolnum fallback 原样保留；非空标签完全不问 witness。
- tests/test_g3_results.py：+fixture selection()；+test_result_evaluate_explicit_selection_never_runs_singleton_witness；+test_result_evaluate_complete_pair_mapping_never_runs_singleton_witness；+test_result_evaluate_unverified_witness_keeps_rejected_diagnostic_latch；+test_result_solution_binding_ignores_lazy_witness_under_ticket_scope；保留原作者两测试原文。
- tests/test_activity019_private_singleton_ticket.py（第二轮，作为 _solution_binding 对应测试；主 Agent 依据本次任务授权调整范围）：+test_generic_solutionbinding_lazy_witness_only_after_real_empty_labels。
- docs/universal_mcp_execution/evidence/full/T07-source-fix-qwen-20261107.md：本文件。
