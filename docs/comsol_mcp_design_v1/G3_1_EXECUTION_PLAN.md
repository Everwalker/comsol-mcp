# G3.1 执行计划（配套 NEXT_GOAL_MAC_G3_1.md）

## 基线（建立本文件时实测）
- HEAD `999174f3a62a6d5689ad4b86315adb6c89dbe56b`（= 审查基线），分支 `codex/mac-g2-accepted-delivery`，origin/main 同点。
- 工作树含 G3 + 收口修复 dirty 共 38 项；软件回归 **1381 passed / 1 skipped**（本文件建立时真实运行）。
- 运行时：任务自有 COMSOL Server pid 71225（127.0.0.1:56389），回执 `.phase1-private/g2-valve-runtime-20260920T225450573368Z`（PROBE_PASS / RUNNING）。M1 起 live 工作前按文档要求重新核对生命周期并生成当轮 receipt。
- 解释器：`comsol-mcp-server/.venv/bin/python`；JDK：amazon-corretto-11；COMSOL 6.4.0.293。
- `review_probes/` 未随仓库提供；按 §12 在本仓库自建对应生产边界回归。
- 授权：NEXT_GOAL_MAC_G3_1.md §0（含 AGENTS.md / CLAUDE.md 项目地图更新许可）；不进入 W17。

## 里程碑与状态
| 里程碑 | 内容 | 状态 |
| --- | --- | --- |
| M0 | C01/C02 集中修复 + 旧失败 transcript 回放 + Java String[] 复现 + 基准参数修正 + 方法缺口清单（离线） | 进行中 |
| M1 | 单一干净模型最小集成关卡（live；连接→空模型→runtime 探针→参数写读→revision 推进→临时求值→受控失败/UNKNOWN→原 job 查证与恢复） | 待 M0 |
| M2 | 最小物理闭环：链 A 全链 → 链 B 全链（空模型起，含材料/边界/mesh/study/solve/采样/数值/保存） | 待 M1 |
| M3 | 链 C 保留性 + R01–R06 applicable + T006/T007/T009/T015/T016/T017/T018/T019/T020/T034/T042/T048 + T005/T010/T033/T035/T038 | 待 M2 |
| M4 | 受影响 Phase2/G2 回归 + A/B/C 新 Worker 重开 + final 源码测试 + 证据一致性 + 发布检查 | 待 M3 |

## C 项 ↔ 归属（文件所有权互斥）
| 项 | 内容 | 归属 |
| --- | --- | --- |
| C01 | DomainOutcome 契约、UNKNOWN/cleanup 优先、verification_status 分离、四入口统一判定、显式 dispatch stage 证据 | 代理 A（comsol_mcp/**） |
| C05 | hasProduct `String[]` 封送 + runtime_id 绑定 + 反射/封送测试 | 代理 A |
| C06 | 材料 rank1/2 property-specific adapter、ins1/默认特征盘点、solver NodePath、网格前置读回、白名单集中合并 | 代理 A |
| C07a | `_g3_results.py` T/t 解析（去顺序依赖）、采样绑定 dataset/solution/时间、单位读回 | 代理 A |
| C02 | 驱动执行上下文（ModelRef 键 revision/dirty）、固定 schema 解包、identity 冲突预拒绝、run/case/step 唯一 key、refresh≠清错、负向不自动修复 | 代理 B（tools/phase4_run_mcp.py、tests/test_g3_phase4_*.py） |
| C03 | case 独立模型/前置、首因分类（DEPENDENCY_BLOCKED/HARNESS_FAILURE/IMPLEMENTATION_GAP/EXTERNAL_BLOCKER）、快照回放测试 | 代理 B |
| C04 | T033：驱动对 operation_describe 的 schema 读取、干净模型上验证 policy 拒绝与 ephemeral 证据 | 代理 B（+ 父级 live） |
| C07b | 基准规格唯一来源（CHAIN_A/B 常量源修正）、三链夹具纠偏（C 夹具补全、T034 真实缺依赖 mph、A/B 空建链） | 代理 B（+ 父级 live 时校验） |
| C08 | `evidence/phase4_1_acceptance.json`、`evidence/phase4_1/runs/`、finalized manifest、文档四件套与 AGENTS/CLAUDE 导航、wheel 打包核对 | 父级（实机轮与收口） |

## 证据与停止
- 新证据：`evidence/phase4_1_acceptance.json`、`evidence/phase4_1/runs/<run_id>/`（requests/results/assertions/summary/index/ledger/source manifest/脱敏）。
- 旧证据（phase4、driver..driver5c、phase4_acceptance.json）只增不改；诊断更正用 supersedes/related_case 链接。
- 停止：§11 全条件满足 → `G3_1_MAC_EXECUTABLE_SCOPE_PASS`；真实外部阻塞 → `IMPLEMENTED_WITH_BLOCKED_ACCEPTANCE`（软件完成 ≠ 实机完成）；普通非 force 同步 + 远端 SHA/tree 核对；不进入 W17。
