# G3.3 acceptance coverage review (read-only)

审查快照：2026-09-22，工作树为 `acceptance/independent-20260922`。本审查读取了包级 `START_HERE.md`、`NEXT_GOAL.md`、`ACCEPTANCE.md`、恢复仓库 `AGENTS.md`、W17/G3.3 设计与交接文档，并静态审查了 runner、Gate A checker、G3 production modules 和 MCP dispatch。审查过程中没有启动 COMSOL、没有连接或停止任何 worker/server，也没有修改生产源码或旧证据。

当前工作树在审查中途已有主 Agent 的未提交 `_execute_checked` 修改；因此下文区分了“基线实机证据中已经复现的缺陷”和“当前源码仍然缺少的覆盖”。基线证据不是 PASS 依据，只用来保留可复现失败事实。

## 先给结论

当前 suite 不能支持 C00–C17 或 F01–F12 的完整验收。最广泛的覆盖缺口是 runner 直接导入 `PersistentJavaWorker`、`result_*`、`ArtifactStore` 和 Probe 私有函数（`repository/tests/run_g3_3_live_acceptance.py:31-46`），fixture 构建和大多数断言因此绕过了公开 MCP operation、managed execution envelope、idempotency、model revision 和 reconcile 路径。即使某个数值断言成立，它也不能证明 C15/C16 所要求的公开 MCP 语义。

C03 的独立基线失败已经定位到 fixture 失败被隐藏：基线 `request_reply_trail.jsonl:503-504` 显示 `ClearStoredSolution` 是 `COMPILE_ERROR`（`model.save` 未声明抛出 `IOException`），runner 仍继续读取文件。基线 `SHA256SUMS.json:8-9` 显示 `chain_a_cleared.mph` 与 `chain_a_solved.mph` SHA256 相同；`result.json:82-84` 记录读回 `[[[308.1500000000069]]]`，所以 `STORED_VALUE_MISMATCH` 是读取原解的可解释结果，不是应该把期望值改成 308.15。当前工作树新增了 `_execute_checked`（runner:69-74）并给两个 Java 函数加了 `throws Exception`（runner:176、202），但这尚未经过新的实机重跑，且清除步骤仍无强制回读，故 C03 仍只能记为 UNVERIFIED。

## C03 Gate A：基线根因和剩余最小缺口

基线的可复现链条如下：

1. `CLEAR_STORED_SOLUTION_JAVA` 的 `run` 原来没有 `throws Exception`，而 `model.save(...)` 会触发编译器的 checked exception；原始源码行见基线 traceback 所指 `repository/tests/run_g3_3_live_acceptance.py:1527` 调用点以及 Java 字符串定义 `:163-187`。实际请求在 `repository/evidence/phase4_3/runs/independent_live_baseline_20260922T0052Z/request_reply_trail.jsonl:503-504` 返回 `status=FAILED`, `code=COMPILE_ERROR`。
2. 原始 runner 在 `repository/tests/run_g3_3_live_acceptance.py:1491-1496` 直接丢弃 `worker2.submit("code_execute", ...)` 回包，未检查 `ok/status`。因此没有任何清除动作发生，随后仍对复制出的原 MPH 做读回。
3. Java 中三次清除调用的异常在原始 `:176-178` 被吞掉，且无论三个布尔值为何，`:180` 都返回 `status=CLEARED`。当前源码虽已加 `throws Exception`，仍保留 `:184-186` 的吞异常和 `:188` 的无条件 `status=CLEARED`。最小修复必须把每一步错误列入返回值并在 runner 中断言 `clear_solution_data`, `clear_solution`, `remove_solution_node` 全部为 true，且清除后的文件 SHA 必须与源文件不同；还要对 dataset 的 solution property / `model.sol().tags()` 做引擎回读。
4. 原始基线读回与失败都发生在同一 loaded model/tag：加载在 `:1490`，Java 修改后仍以 `reopen_a_cleared` 在 `:1505-1510` 读。即使 Java 执行成功，也应保存后释放该 handle，再以新的 model tag 或新的 worker fresh-load `cleared_mph`，否则无法证明持久化文件而不是内存对象。
5. runner 只在一个点 `[0.0125, 0.005]` 读一次（当前 `:1513-1518`），但把同一个 `cleared_read_outcome` 在 `:1527-1532` 对 receipt 的三个 expectation 重放。于是若旧解仍存在，第一 expectation 可能碰巧是 308.15，第二个 expectation 却被同一读点错误地与 323.15 比较，正是基线 `T_x0250 expected 323.15, got 308.15`。清除负控应对所有期望点做一次真实 fresh-artifact read，或先确证解节点/solution binding 消失；不能用同一标量冒充全部点。
6. `verify_reopen` 接受 caller-supplied evaluator（`repository/comsol_mcp/_gate_a_reopen.py:118-138`），并在 `:262-288` 对回调结果做比较。C03 的 `_cleared_artifact_read` 仍是 captured result replay（runner `:1527-1532`），不是 checker 自己根据 model、dataset、expression 和 points 发出的真实读取。C03 负控因此仍不能满足“正、负控同一生产 checker 且负控改动真实 artifact”的语义。

主 Agent 已在当前工作树加入 `_execute_checked`，这修掉了“fixture 编译/运行失败仍继续”的首因；这只说明代码方向正确，不能把旧基线自动升级为 PASS。完成 C03 最小验收还需：检查清除回包和布尔步骤、fresh-load 修改后 MPH、对全部点真实读回、让 checker 使用真实读 adapter 而非 captured scalar，并把错误分类为 `SOLUTION_CLEARED_OR_EMPTY`、`NO_SOLUTION` 或明确的“清除动作未生效”失败。

## C00–C17 对照

| Case | 当前覆盖事实 | 覆盖结论与最小补强 |
|---|---|---|
| C00 | `run_c00` 只检查当前 worktree manifest/dirty 状态、wheel fresh-venv import（runner `:699-821`）。读取 PIN/历史 inventory 仅记录 `:730-736`，没有在空目录调用 `tools/bootstrap.py`，也没有把 bootstrap 的 commit/tree/blob map 与 PIN 逐项相等比较。 | **PARTIAL**。在 scratch 目录真实 bootstrap；验证 receipt 的 commit/tree/per-file blob/mode、无旧绝对路径，再从 fresh venv import。 |
| C01 | 只固定比较三份历史 SHA（`:828-868`，实际断言 `:841-850`），只检查 `Gate_A`/`T013` correction。没有扫描所有当前 PASS 的 evidence refs，也没有把缺失的 `audit/semantic_review.json` 分类为 MISSING。 | **PARTIAL**。解析全部 ledger/correction，逐 PASS 验证来源、evidence level、文件存在性；缺文件必须 MISSING，不能由字符串 hash 通过。 |
| C02 | 仅覆盖一次 `cleanup_failed -> STATE_UNKNOWN` 和一个 `worker=None` 导出拒绝（`:873-911`）。没有 false/true/null 三态、dispatch 前/后异常、cleanup UNKNOWN/partial、MCP `isError` 与 ledger/job envelope。 | **PARTIAL**。用表驱动矩阵覆盖所有组合，验证未产生目标文件、状态不被 job/MCP/ledger 覆盖。 |
| C03 | 三条链的 fixture 已由私有 Java worker 构建（builder 与 fresh read 在 `:916-1713`）；当前基线清除负控失败原因见上一节。Chain B 只比较尾部 3 个值（`:1345-1377` 附近），未明确证明至少 3 个时间、至少 2 个非初始时刻。Chain C 只读一个点并检查 numerical tag（`:1380-1422` 附近），未证明 Definitions Probe/table、solver settings 或目标改变后的差异。负控共用 checker 名称但 evaluator 可回放。 | **FAIL/UNVERIFIED**。补 pre-save/fresh-load 全数组和元数据、真实 Derived/Probe/table 回读；所有负控从 live reader 走同一 checker，不能传 captured lambda。 |
| C04 | runner 对 `f=2,V=3` 断言 integral/average/std/RMS/denominator（当前 `:1719-1797`），数值设计正确，但通过私有 `result_evaluate`，不是公开 MCP；生产实现的多表达式/多解、RMS、complex/weight/selection 缺陷仍使该常数 case 不能代表通用实现。 | **PARTIAL**。公开 MCP 端到端调用；增加多 expr、多解、complex 和 weighted cases，并用真实 measure 作分母。 |
| C05 | 2D domain selection 和 1D wire、3D block fixture 在 `:1802-2030`，当前 builder 已检查 `_execute_checked`，但仍是私有 worker/Java。没有完整的 2D boundary/surface/line 组合、不同 component/geometry；无 public MCP fixture path。 | **PARTIAL**。通过 public dataset/result operations 构建并读回 1D endpoints/line、2D boundary/surface、3D face/volume，跨 comp/geom，验证 partial/full selection。 |
| C06 | analytic rectangle `x+2y` 断言在 `:2031-2123`，公式独立且有价值，但仍直接调用私有 result function，不能覆盖公开 envelope；生产 aggregate/selection exception swallowing 会使通过常数/analytic case 产生虚假信心。 | **PARTIAL**。保留独立 analytic oracle，补 public route、非对称多点和错误 selection/shape。 |
| C07 | axisymmetric cylinder case 在 `:2124-2200` 断言体积、平均半径和 cross-section，但没有明确的侧面边界面积 `12π` 读回；同一数值/flag 可掩盖把 2D measure 当 revolved measure。 | **PARTIAL**。显式选 lateral boundary/side surface，断言一次 `2πr` 权重、volume `12π`、mean r `4/3`、side area `12π`，并记录实际 physical measure/readback。 |
| C08 | `run_c08` 在 `:2201-2308` 使用两 expr、5 点、6 个 transient step，只有一个 inner/time 轴。`dataset_solution_indices` 调用 `{"dataset":"dset1"}`（`:2220`）而生产函数要求 `arguments["path"]`（`_g3_results.py:1702-1706`），这是可复现的 `INVALID_NODE_PATH`；即使改成 path，runner 只检查 `binding_complete`（`:2228`），不检查 `axis_metadata_complete`/`read_errors`。生产返回 `binding_complete=True` 但轴元数据可不完整（`_g3_results.py:1782-1805`），且 `parameters_complete=False`（`:1815-1818`）。 | **FAIL/虚假覆盖**。构造 >=2 outer × >=3 inner × >=3 point × >=2 expr，值显式依赖四轴；测试 all/subset/first/last/0/out-of-range、wrong dataset/solution、真实 time/parameter metadata。修正参数名只是第一步，不能把 `binding_complete` 当轴绑定完成。 |
| C09 | `run_c09`（`:2312-2395`）测试 3+4i 的 preserve/real/imag/abs/phase 和一个空间点，并用 helper 测缺 imag。没有 getter 异常、imag shape mismatch、多点、非有限/零幅相位负控；仍走私有函数。 | **PARTIAL**。注入 `isComplex/getImagData` 失败与 shape mismatch，必须 FAIL/C `COMPLEX_DATA_ERROR`；多点复数数组逐轴验证，只有确认 real 才允许 imag=0。 |
| C10 | `run_c10`（`:2399-2482`）覆盖 m/mm、NaN、维度/jagged/frame 输入拒绝，但未让 engine 返回错误坐标、乱序、wrong shape、非有限坐标；也未把 `coordinate_readback.status` 非 VERIFIED 变成科学失败。生产 `result_at_points` 读取异常还可被吞。 | **PARTIAL**。stub/live adapter 注入 wrong readback、sort/shape/nonfinite，断言 `verification_status != VERIFIED` 时不得 PASS，且返回 status 明确失败。 |
| C11 | `run_c11`（`:2487-2544`）用 raw `_call` create/set 一个 `CutPoint2D`，`except Exception: pass`（`:2490-2497`）；cycle 只在 Python `MockDsetContainer` 上测（`:2505-2529`）。没有 CutPlane/CutLine/CutPoint 组合、Join、wrong comp/geom、same tag across collections、setter failure stop、fresh readback。生产 `dataset_create/update` 逐 setter 继续且无 not-executed（`_g3_results.py:1560-1603`, `:1644-1676`），inspect getter 全吞 `:1622-1631`，remove 只按 tag `:1679-1699`。 | **PARTIAL/虚假 live graph**。所有操作走 public `dataset.*`，真实构建 Solution→CutPlane/CutLine/CutPoint→Join；注入 property failure，断言后续不执行、readback/partial/unknown 正确；跨 collection/comp/geom 和真实 cycle 必须拒绝。 |
| C12 | `run_c12`（`:2549-2747`）直接测 `ArtifactStore`/`atomic_save` helper、traversal、failed payload、rollback/success。没有 public `result.field_export`、parent/symlink/existing-target policy、authorized overwrite、cleanup UNKNOWN/dispatch failure。 | **PARTIAL**。public export path + typed artifact envelope；覆盖 parent/symlink/existing target/overwrite policy，pre-side-effect refusal，eval/cleanup failure 不发布，atomic 失败保留原文件。 |
| C13 | `run_c13`（`:2748-2914`）直接调用 `_export_to_artifact` 和 `_g3_ops.dispatch("artifact.read")`，测 chunk bytes/hash/range/forged digest/tracemalloc。没有真实 artifact capability/ref/permission/corrupt block，也不验证 shape/axes/coords/units；不能证明 host-side paging 而非本地 whole-read。 | **PARTIAL**。用 public opaque handle 请求 offset/length，重建完整 bytes + schema metadata；篡改块、越权/权限、bad range 必须拒绝，并记录每块读和 peak memory。 |
| C14 | `run_c14`（`:2915-3040`）直接调用 probe/table funcs 和 `_g3_ops.dispatch`。它把 `probe.update`/`probe.history` 的 `UNSUPPORTED_OPERATION` 当成预期并记录 PASS（`:2950-3033`），直接违反 NEXT_GOAL §7；没有 user table/expr/selection/complex data preservation 或 fresh readback。 | **FAIL**。Probe update/history 必须实现并真实 readback；保留已有 user table，验证表达式/selection/complex rows，不得以 unsupported PASS。 |
| C15 | `run_c15`（`:3041-3223`）用私有 `result_evaluate` 两次，比较 worker `request_hash`（`:3053-3074`）；control plane 只是 `worker.health`（`:3076-3129`），stale ref 是私有 worker close 后调用（`:3149-3169`）。没有 MCP idempotency key、job_id/status/reconcile、unknown job、same-key no-replay、new observation new key。 | **FAIL/虚假 protocol coverage**。public `operation_call` 同 key 重试必须同 job/result、不同 key 产生新 observation；模拟 timeout/UNKNOWN 后只允许 reconcile，不得重放；solve 期间 public job_status/health；fresh worker 拒绝 stale model_ref，且 shared PID 不变。 |
| C16 | `run_c16`（`:3224-3361`）检查 pyproject 文本、三个 lock 文件、`pip wheel --no-deps`、wheel 内三个 resource suffix（`:3227-3339`）。没有 direct dependency/pin/pip check/freeze/SBOM，也没有枚举所有 operation/schema/Java resources 或比对 public runtime input/output schema。 | **PARTIAL**。fresh env 安装/`pip check`，解析 metadata/pins，枚举 catalog/schema/Java classes 与所有 required operations，比较 registry schema 和实际 result envelope。 |
| C17 | `run_c17`（`:3366-3432`）负责 teardown/status；suite 最后写 ledger。runner 的 SHA manifest 先 hash artifacts/evidence 再写 top-level ledger（`:3730-3790` 附近），因此最终 ledger 不在自身 SHA manifest；没有逐报告 source hash binding、normal sync SHA、config restore、unverified matrix。 | **PARTIAL**。所有最终报告/ledger 写完后再生成 manifest；绑定当前 source commit/tree/blob map，检查 config restore、普通同步 SHA、每平台 PASS/FAIL/BLOCKED/NOT_RUN/UNVERIFIED。 |

## F01–F12 缺陷映射

| Finding | 当前源码/runner 证据 | 结论 |
|---|---|---|
| F01 | C01 只比较三项固定 hash 与两项 correction（runner `:828-868`），没有全量 PASS/evidence ref 审计；Gate A/Fake/历史证据边界无法由该 case证明。 | **未覆盖**；至少补 evidence classifier 和 missing-ref 审计。 |
| F02 | C04–C06 有 analytic constants，但调用私有 `result_evaluate`（runner `:1719-2123`）；生产多表达式/多解降 scalar、RMS/complex/weight/selection 缺陷会被常数 case 遮蔽。 | **部分覆盖**；需要泛化数组与公开调用。 |
| F03 | C07 只有 axisymmetric flag、volume/average/cross-section（runner `:2124-2200`），没有 side-boundary selection/measure-once 证明。 | **未闭合**。 |
| F04 | C08 只有单 transient axis；`_g3_results.py:1702-1822` 的参数/outer/inner metadata 仍不完整，runner 只看 `binding_complete`。 | **失败/虚假覆盖**。 |
| F05 | C09 只有 helper 缺 imag 负控（runner `:2355-2375`），没有 getter exception/shape mismatch/真实多点。 | **部分覆盖**。 |
| F06 | C10 仅验证输入 rejection（runner `:2419-2460`），没有错误 engine coordinate readback 或 VERIFIED gate。 | **部分覆盖**。 |
| F07 | C11 raw `_call` + swallowed exception + Python mock cycle（runner `:2490-2529`）；生产 dataset setter/remove 的 typed path/failure-stop 不满足 C11。 | **虚假 live graph**。 |
| F08 | C12 helper-level ArtifactStore/atomic tests（runner `:2549-2747`），未经过 public field export；父审查还确认默认 cwd、绝对 temp 路径、overwrite/symlink/JSON/CSV 缺陷。 | **未覆盖生产路径**。 |
| F09 | C13 私有 `_export_to_artifact`/dispatch（runner `:2748-2914`），没有 host capability/ref/metadata/permission/corruption；父审查还确认 whole-read/format fallback 缺陷。 | **未覆盖生产路径**。 |
| F10 | C14 明确把 `UNSUPPORTED_OPERATION` 的 update/history 记录为 PASS（runner `:2950-3033`）；Probe 与 Derived Values 分离要求未满足。 | **失败**。 |
| F11 | C00/C16 是当前 tree/wheel 文本和三 resource 的静态检查（runner `:699-821`, `:3224-3361`），不等于 bootstrap、direct dependency、完整 resource/schema lock。 | **部分覆盖**。 |
| F12 | runner 顶部直接导入私有 worker/result/store/probe（`:31-46`），C03–C15 大量直调；公开 MCP 只在少量 `_g3_ops.dispatch` helper test 中出现。 | **系统性缺口**；需 public/private path parity test。 |

## C08 轴映射和 `SolutionBinding`/`FieldArray` 最小接口建议

当前 `repository/comsol_mcp/_solution_binding.py:17-139` 的 `SolutionBinding.slice_solution_axis` 只按 caller 已给数组切第二轴；`FieldArray`（`:141-166`）默认轴是 `("expression", "solnum", "point")`，没有 outer/inner/expr/point 的明确 schema，也会让 singleton slice 失去轴语义。`_g3_results.py:1979-1989` 还明确拒绝 `outer/time/frequency/parameters`，所以 runner 不能声称 C08 已实现四轴。

建议由独立接口实现并单测（本审查按用户要求没有改生产源码）：

```text
FieldArray.data       # 保留完整嵌套 [outer][inner][expression][point]
FieldArray.axes       # 精确为 outer, inner, expression, point；slice 不挤轴
FieldArray.shape      # 与 data/axes 一一对应
FieldArray.coords     # 每轴实际 index/time/parameter/point coordinate
FieldArray.units      # 每轴和表达式单位，未知必须显式 UNKNOWN
SolutionBinding.resolve(dataset, solution)
  -> outer/inner -> engine solnum pairs + names/values/units + provenance
SolutionBinding.select(array, outer=?, inner=?, expression=?, point=?): FieldArray
```

COMSOL 原生映射必须以 `SolutionInfo.getSolnum(outer, strict)` 逐 outer 获取实际 inner/solnum；`getOuterSolnum()` 为空可以是合法的“无 outer 元数据”，不能据此猜 `[1]`；`getMaxInner` 不能代表每个 outer 的 mapping。对于每一组 pair 用 `getPNames(int[][] pairs)`, `getPvals(pairs)`, `getUnits(pairs)` 保存真实参数 metadata。Numerical feature 的 `getData/getImagData` 要按 `[expr][solnum][vertex]` 解码；`getReal/getImag` 是 aggregate `[expr][solnum]`，不能通过长度猜轴。缺轴、错 dataset/solution、pair 不一致、shape 不一致应 fail closed。

最小 unit/contract fixture 应用值 `1000*outer + 100*inner + 10*expression + point`，覆盖 >=2 outer、>=3 inner、>=2 expr、>=3 points；验证 all/subset/first/last、0/负数/越界、wrong dataset/solution、singleton slice 仍保留四轴，并把 time/parameter 名称、值、单位和实际 solnum 逐 pair 记录。另加 `[expr][solnum]` aggregate 与 `[expr][solnum][point]` field array 的分开测试，防止按长度把 aggregate 冒充 FieldArray。

## 最小修复优先级

1. 先重跑 C03 前，保留当前 `_execute_checked`，再加入清除动作布尔回读、SHA 变化断言、fresh-load 和全点真实 reader；旧基线 `COMPILE_ERROR` 不能被任何新 ledger 覆盖。
2. 修 runner 的 public/private path：fixture create/save/read、C08 slice、C11 dataset、C14 Probe、C15 retry/status 全走公开 MCP，记录 outer execution、model_ref、revision、idempotency_key、job_id 和 reply。
3. 独立实现 `SolutionBinding`/`FieldArray` 四轴 schema 与原生 SolutionInfo pair mapping，再用 C08 的显式四轴 fixture 验证；在此之前 C08 应保持 FAIL/UNVERIFIED。
4. 修 C11 的 setter stop/readback/typed NodePath 和 C14 Probe update/history 后，重新生成全新 evidence 目录；不得修改或重写旧 evidence。

## 证据边界

本目录只包含这份审查报告。`repository/evidence/phase4_3/runs/independent_live_baseline_20260922T0052Z/` 是主 Agent 的历史实机失败证据，本报告引用它来复现 C03 failure，不把其中任何 PASS 作为本审查的接受依据。没有 COMSOL/许可证/凭据或新运行结果的部分均标为 PARTIAL、FAIL 或 UNVERIFIED。
