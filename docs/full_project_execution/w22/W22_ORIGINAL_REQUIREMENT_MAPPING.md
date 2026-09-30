# W22 原始要求与已批准证据映射

**本文件是索引映射，不是新科学验收。** 已找到独立 W22 最终签核及原始运行路径，足以把历史 `W22_SCOPED_APPROVED` 证据映射到限定的 Windows 6.3/6.4 范围。它不能把原始 `T016` 的 `ALL` 或 `T045` 的 `ALL_MODULES` 整体标成 PASS，也不证明当前 HEAD 与历史批准源码相同。

## 冻结要求与批准身份

原 `04_IMPLEMENTATION_PLAN.md` 将 W22 定义为“静态非轴对称热源、阵列/环功率、距离扫描、热预算”，依赖 W20/W21/W18，并映射到 T045、T016。原 `03_ACCEPTANCE` 中 T016 为 `ALL`，T045 为 `ALL_MODULES`，原始默认状态均为 `NOT_RUN`。这些默认值是冻结需求快照；后来的证据来自 `docs/handoff_w22_vcsel/`。

`docs/handoff_w22_vcsel/review/W22_FINAL_REVIEW.md` 记录实际独立 Reviewer `/root/reviewer` 作出 `W22_SCOPED_APPROVED`，D1–D6 在 Windows COMSOL 6.3 build 290 与 6.4 build 293 均为 PASS。审核绑定的 benchmark SHA-256 为 `d94322296feff1cd7678e345bf798616a704c638c8c108d3c8f78f4128f4b1fe`，批准生产源码 commit 记录为 `44b8e1931b47da564edb600d2127b28859d07b36`，最终 wheel SHA-256 为 `c1d82034e19e1f0fd897f3ec76911a3ccad229ed35f613148633bfb56f923ae6`。两版 `delivery*/package_origin.json` 都绑定该 wheel 与同一 benchmark。批准的 source archive SHA 也有记录，但该归档当前不在本地项目树中，且 44b8 commit 对象不在当前 Git 对象库中；因此不能把当前 `0b5d9ef` 当作同一已审源码。

## 原始要求映射

| 原要求 | 已批准 W22 范围 | 原始更宽范围状态 | 证据与参数身份 |
|---|---|---|---|
| T016：静态二维非轴对称 Q(x,y)、离轴热点、同半径不同角度保留、源哈希/外推/单位 | Windows 6.3/6.4：**PASS（历史独立签核）** | **PARTIAL**：原矩阵为 ALL；Mac native W22 未主张。Mac 6.3 两架构是 `USER_REQUESTED_SKIP`，不是 PASS/N/A；Mac 6.4 `UNVERIFIED` | 两版 delivery 源清单 `evidence/w22_vcsel/windows/delivery{63,64}_01/project/source/manifest.json` SHA-256 `9fd3a145c9ebb46072edc396ced3cc420fa7fe14ac6d6289a76c40adb5c6bb12`；合计 19 个阵列位置、18 个 active emitter，含角向与 mm 等价探针、零外推和源图。见 D1、D5 review。 |
| 阵列/环功率：发光单元、分组、active mask、功率和距离参数化 | Windows 6.3/6.4：**PASS（W22 D1）** | **PARTIAL**：只证明冻结的合成热学模型和两个 Windows 版本，不是所有 module/平台或真实器件 | 1 中心 + 6 个半径 6 mm + 12 个半径 12 mm 的位置，外环禁用 1 个；Gaussian 核 `sigma0=2 mm`、斜率 0.05，吸收率 0.6。输入明确标记为 synthetic，不是用户实测数据。 |
| 距离扫描和有限功率搜索 | Windows 6.3/6.4：**PASS（W22 D3–D4）** | **PARTIAL**：只能称冻结 27 候选中的 best feasible，不是全局最优，也不覆盖 ALL_MODULES 全矩阵 | L=10/20/30 mm；每版 27 个候选，发射总功率 12.45 W；中心功率 `{0.6,0.8,1.0}` W、第一环 `{0.65,0.75,0.85}` W，外环由总功率约束计算；每个 active emitter 0–1 W，ROI 平均升温至少 3 K。选择 L=30 mm、中心 0.6 W、第一环各 0.65 W、外环 active emitter 各 0.7227272727 W。 |
| 材料/散热与热预算、ROI 指标、不可行目标 | Windows 6.3/6.4：**PASS（W22 D2、D4；冻结合成模型范围）** | **PARTIAL**：`T045=ALL_MODULES` 的广义范围及物理校准均未证明 | 40×40×1 mm slab；k=20 W/(m·K)、rho=3000 kg/m³、Cp=700 J/(kg·K)；初始/环境温度 300 K，底面对流 h=500 W/(m²·K)，侧面绝热，顶部输入 `alpha × incident` 的 W/m²，alpha=0.6；圆 ROI 半径 15 mm；采样 0/1/5/20/60 s。最佳候选 60 s ROI mean≈313.510892 K、std≈2.582998 K；native slab incident≈12.407610 W、absorbed≈7.444566 W、ROI absorbed≈6.330966 W。20 W ROI absorbed 目标由 10.8 W 保守上界证明不可行。无实测器件/wafer 校准。 |
| T045 完整链与原生交付 | Windows 6.3/6.4：**PASS（历史 W22 D1–D6 scoped approval）** | **PARTIAL**：T045 原始矩阵为 ALL_MODULES；Mac 不在历史 W22 原生交付范围，物理验证 UNVERIFIED | 独立 review 记录每版 886 combined checks 与 122 fresh/reopen checks。保存 MPH：6.3 `8640ca…796d`，6.4 `d62f90…61fb`；真实 COMSOL source/temperature 图和新路径、新 Worker、零 solves reopen 见 `delivery{63,64}_01`、`reviewer{63,64}_02`、`reviewer_reopen{63,64}_01` 与 `FRESH_REOPEN{63,64}_REVIEW.json`。 |

## 失败保留与可核验范围

原始 `full63_01`、`full64_01` 都完成 27 个候选后在同源缓存复用断言处失败；`controls63_01`、`controls64_01` 又在输出准备阶段失败。它们仍保留 `FAILED`。独立最终审查明确将后续 cache controls 与 `delivery63_01`、`delivery64_01` 作为追加证据组合评审，没有覆盖或提升旧失败状态。六份 `full/controls/delivery` raw transcript 当前存在，SHA-256 与各自 `COMBINED63/64_REVIEW.json` 锁定值匹配；这只是本映射对文件身份的交叉核对，不是重算 27 个结果或重跑 886/122 条断言。

合成 benchmark 参数、D1–D6 独立签核、两版版本/build、final wheel 与 delivery 模型/图像 hash 都已定位，足以让主 Agent 为 W22 添加限定证据路径。建议当前任务状态写成“历史 W22 scoped approval 已映射；原 T016/T045 更宽矩阵保持 PARTIAL”，不要把 `03_ACCEPTANCE` 中的默认 `NOT_RUN` 直接当作后续证据，也不要把 W22 approval 当作当前 `0b5d9ef` source approval。

仍需保留的 `UNVERIFIED`：历史 source archive 与当前 Git 的重绑定；Mac native W22（Mac 6.3 两架构按用户范围跳过，Mac 6.4 未验证）；GUI/cloud Host；实测器件物理校准。完整身份与哈希清单见同目录的 `W22_ORIGINAL_REQUIREMENT_MAPPING.json`。
