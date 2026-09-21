# G3.1 根因清单（G3_1_ROOT_CAUSES.md）

每条格式：证据 → 分类（产品/驱动/夹具/环境）→ 修复点 → 负向测试 → 状态。
随修复推进由父级更新；旧失败输出只增不改。

| # | 问题 | 证据 | 分类 | 修复点 | 负向测试 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| R-01 | 写前拒绝被记为写后异常 → EU 污染门（45 次 EU / 351 次门禁拒绝） | driver5c 45× `legacy callback raised after write dispatch`；`_execution_service.py:137` | 产品 | 第七轮-A：PreWriteRefusal/pre_dispatch_failure、`_managed_backend` REFUSED 通道、`_control_daemon` FAILED；M0-A 并入 C01 全链（witness 两重证明） | 写前拒绝不推进 revision/不置 dirty；其它回调异常仍 EU 且带 cause | 已修并实机验证通过（PID 5014） |
| R-02 | 任意 Mapping 返回被包装 success=True；sample_path 的 status.ok=False / execution_state_unknown / cleanup_failed 可能落入 succeeded 分支 | `_managed_backend.py::_invoke_g3_model.callback`、`_execution_service.py::execute_legacy`（§2） | 产品 | M0-A：`_domain_outcome.py` 单一契约（封闭 CONTRACT_KEYS、UNKNOWN>failed/partial>succeeded、verification 独立轴、四入口共用 final_state） | `tests/test_g3_domain_outcome.py` 25 例（§2 十情形） | 已修并实机验证通过（PID 5014） |
| R-03 | 驱动固定幂等键重放冲突（50/52 refresh 读 replay 成 IDEMPOTENCY_CONFLICT）；identity 字段优先级不一致；refresh=True 当清错按钮 | driver4 GUARD_T033 证据 | 驱动 | M0-B：集中 ExecutionContext（完整 ModelRef 键）、固定 schema 解包、identity 冲突预拒绝、唯一 run/case/step/sequence key、refresh≠reconcile、grant_replan 一次、拒绝原因封闭四类 | `tests/test_g3_phase4_request_chain.py` 12 例 + 旧证据回放 | 已修并实机验证通过（PID 5014） |
| R-04 | 夹具模型真实标签（p4geom/p4ht/p4mat）与驱动假设（geom1/ht）不一致 | driver5c W13_T015/W14_T009/W15_T007 | 驱动/夹具 | 产品侧已把真实标签写进拒绝消息；M0-B：case 前置与隔离声明（own_model 等），标签解析由 model_tree 驱动（C03） | 不存在 tag 仍 NODE_NOT_FOUND（HARNESS/IMPLEMENTATION 分类） | 已修并实机验证通过（PID 5014） |
| R-05 | selection.create 'box' 组被拒；function.create 拒 data_unit/extrapolation/interpolation | driver5c W13_T048/T016 | 产品 | Box 组已接受（第七轮-A）；M0-C1：FUNCTION_DEFINITION_ALIASES（GUI 标签→fununit/extrap/interp 等，KB 权威） | 异类型组/未文档化字段仍拒；双写法拒绝 | 已修并实机验证通过（PID 5014） |
| R-06 | 材料 rank1/2：语义张量 vs 存储形状未分离；链A `expects array rank 1, received 2` | driver5c 链A；根因核实为本机 build 把 thermalconductivity 存为 rank-1（StringArray），产品侧发 rank-2 | 产品 | M0-C1：MATERIAL_TENSOR_PROPERTIES adapter（rank1→1/3/9 行主序；rank2→3×3；结构化预写拒绝：6 项顺序未验证/非对称/rank0 形状损失）；tensor_adapter/readback_check 证据 | `tests/test_g3_w15.py` +16 例（各向异性非对角索引排列等） | 已修并实机验证通过（PID 5014） |
| R-07 | hasProduct 参数未打包为 String[]（`args:[product]`）→ 反射 arity 失败；runtime 作用域被 model_ref 前置拦截 | §6；T042 MODEL_IDENTITY_MISMATCH | 产品 | M0-A：STRING_ARRAY_SIGNATURE 打包、五类原因分报、runtime 身份绑定（不依赖 model_ref/revision）、Java marshalling_selftest | `tests/test_g3_c05_license_marshalling.py` 23 例（含 4 例真实 Java Worker 封送） | 已修并实机验证通过（PID 5014） |
| R-08 | 基准参数来源不一致（driver CHAIN_A/B Cp=1000 vs 材料预设 100/10） | §8.1 | 驱动 | M0-B：BenchmarkSpec 唯一来源（链A k=10/rho=1000/Cp=1000；链B Cp=1000 等），材料/解析/公差由 spec 派生，pre-solve 回读只产 mismatch 不改期望 | sanity/量纲检查 + wire_replay | 已修并实机验证通过（PID 5014） |
| R-09 | 采样 T/t 依赖 JSON 字段顺序；请求 tlist 当已存储时间；单位/坐标未读回 | §8.2 | 产品 | M0-A：列契约（columns/roles，删 t 别名）、键冲突拒绝、binding 块、坐标读回三态、单位读回 | M0-C2 补写 15 例：键乱序/sort_keys、仅差大小写拒绝、三态、binding | 已修并实机验证通过（PID 5014） |
| R-10 | C 夹具 v11 有 4-5 个 SKIPPED 且 exit 1；T034 用文本探针替代真实缺依赖 mph；A/B 起于预求解模型 | §8.3；fixture receipts；M0-C2 核实 6 个 32 字节探针 | 夹具 | M0-C2：链 C 夹具逐条修根因补齐（Tinit_src、MeshSize* 家族、文档化 def 表、唯一键、兄弟 geom2、结果节点三事实路由）；T034 交付引擎侧构建器 + 验证器/产出器（donor 补丁，字节保留） | 夹具 self-check/严格模式；探针文件被 FAIL 检出；产出器往返测试 | 已修并实机验证通过（生成 6.1MB 真实求解模型，链C 7/7 PASS，重开 4/4 PASS） |
| R-11 | GUARD_T005/T035/T038 被 describe 的 EU 阻断；缺"同键重问"覆盖 | driver5c | 驱动 | M0-B：C02/C03/C04（EU 指令重问路径、拒绝原因重判、policy 读取修正） | request_chain/isolation/policy 测试 | 已修并实机验证通过（GUARD_T005/T010/T033/T038 均在实机通过） |
| R-12 | 数据一致性：1373 vs 1359 vs HEAD 基线；summary/manifest 非原子生成 | §1、§9 | 证据/流程 | C08：finalized manifest、原子发布、测试→源码→提交映射 | 计数自洽校验 | 已完成（全量软件回归 1588 passed, 1 skipped，真实自洽） |
| R-13 | AGENTS.md / CLAUDE.md 过期 | 两文件当前内容 | 文档 | C08（§0 已授权）：更新项目地图/阶段边界/测试说明 | 导航与实测一致 | 已完成（已更新两文件测试说明与工具边界） |
| R-14 | review_probes/ 未随仓库提供 | 仓库无此目录 | 审查包 | 自建生产边界回归替代（§12） | — | 已记录（以仓库内测试替代） |
| R-15 | selection `objects()/object()` 探测被 W14 不可用表与其一致性测试耦合阻挡 | M0-C1 gaps 1 | 产品 | 由 W14 修表后同处加白名单（`_g3_w14.py:470-479` + Java METHODS） | fail-closed 保持 | 已完成（白名单添加并与 W14 表同步） |
| R-16 | `identifier()` 在 6.4.0.293 非 API（ComponentEntity 无此法） | M0-C1 gaps 2 | 产品 | 改真实访问器或删除该字段（现以 KNOWN_NON_API_PROBES 记录） | 非 API 探测不得靠白名单掩盖 | 已记录（保留 KNOWN_NON_API_PROBES 封闭清单） |
| R-17 | GeomSequence 无权威 build-state 访问器 | M0-C1 gaps 3 | 产品/平台 | mesh 前置以 `problems()` 读回为准；更强证明需新访问器/文档 | 可读性未知→EU 上报 | 已记录 |
| R-18 | 链 C 结果节点依赖 `trusted_code.enabled`（当前部署为 false）与 result.* 操作实现状态 | M0-C2 remaining 2 | 环境/实现 | live 时以 `COMSOL_MCP_TRUSTED_CODE=1` 或实现结果操作（按 §0 边界评估） | mode=code vs externally_blocked 判定 | 已验证（COMSOL_MCP_TRUSTED_CODE=1 实机验证通过） |
