# 最新进展审查：W21 已有代码，原五项真实执行尚未收口
审查日2026-09-25，公开源 `c2ca5e40054fa023aafe3ae77120948063496728`，tree `ab915b33f9c843c7d00bb2e530174d4bfb136c02`。

## 范围
按上一包冻结合同检查新增W21与直接依赖，不开展全仓重新挑错。读取了新提交、W21实现和runner、工具/dispatch、当前进度、独立审查文件及W20直接相关段落；未访问用户机器、未跑COMSOL或完整仓库pytest。本地摘录探针仅说明有限Python控制流。来源位置见SOURCE_MAP。

## 确认进展（仓库报告）
从a85eccd0新增一次提交。新增ParameterCase、ParameterIndexTable、ComputationBudget、ResultCache、BoundedOptimizer、StageCheckpoint/TransferManager及公开工具登记。报告W20_APPROVED、W21_COMPLETED_AND_STOPPED；软件1965 passed / 8 skipped / 0 failed是提交/进度陈述，不是本次复跑。
保留这些数据结构、候选历史和有效控制测试，不全盘删除。支持把W24领域验收与W21通用传递分开，这是正确边界。

## 固定五项的缺口（A1原合同，不是新增要求）
### 1 参数case/扫描
`op_parameter_case_manage`的create/list/apply只返回字典，不在COMSOL登记、应用或回读。`op_study_sweep_manage`不使用worker；内部用math.sin/exp计算温度，缺参数还补默认铜参数，最终COMPLETED。因此生产操作没有实现原“真实扫描”。
`run_deliverable_1_sweep`同样明确注释Simulate，只在Python计算。两版本参数只是函数的version字符串。

### 2 缓存/预算
ResultCache和ParameterIndexTable把数值round(...,8)，不同小参数会映射同一键；这是原“输入变化不误用缓存”的直接问题。query_slice对不存在时间静默选nearest而不返回实际选择，原inner/outer/time精确对应未闭合。
_GLOBAL_CACHE尚未在生产sweep/optimization中使用；预算仅在候选前检查，当前runner甚至先记录6次再确认max_cases=5耗尽。预算测试可以保留为控制用例，但不是已证明原生调度不超额。

### 3 优化
公开优化wrapper使用固定T_mid=325以及abs(k-400)，不是用户模型目标。objective缺失时默认±inf且约束可为空，于是候选可被当best feasible；raw结果FAILED也没有在evaluate_candidate里拒绝。必须连接现有引擎求值和原预算/失败case契约，不要求新优化算法。

### 4 阶段状态传递
Manager只将调用者给的variables复制进target_initial_state，未修改目标模型/Study初值。`source_hash_verified=True`没有重新校验内容，默认selection为domain[1]，进程内全局对象无实际目标runtime绑定。保留字典映射作为计划预览，但它不能证明T047通用传递子项通过。

### 5 双版本与公开入口
runner直接调用Python函数并用两个版本字符串运行同一公式；所谓exact parity因此不说明两个COMSOL引擎结果一致。没有实际6.3/6.4启动、真实MCP调用和新Worker读取模型证据。
_tools_w21公布optimization.bounded_run/stage.state_transfer/stage.checkpoint_create，OPERATIONS却使用experiment.run/solver.solution_transfer/checkpoint.create。是否已有alias映射需核对生产路径；当前不能仅凭注册成功证明可调用。checkpoint.create已有语义不得被阶段字典checkpoint静默替换。

## 只保留必要的W20原条款复核，不重开27项全审
新W20签字在a85eccd0上执行18个反例并复用旧证据。当前`validate_solution`仍允许请求Mapping作为ObservationRef：model/dataset/hash存在才检，criteria可覆盖观测；返回physical=PASS if model_validated。这与原T06、T09、T12直接冲突，和签字声称的能力不一致。修正只对应这些原条款；既有hash错误/跨tag拒绝是进步，不等于后端来源已验证。
T11与T14的签字继续引用旧run收敛数组和重开文件；本次未重新执行或全量审阅旧raw，因此只要求核对明确来源、复用适用证据，不能机械宣布全部旧证据无效。若没有原required实测，则按原T11/T14补，不新增case。可复用W21新真实case和阶段测试产物，避免重复计算。
这不是撤销原签字原件，不判断旧Reviewer会话真实性；只是保留历史并对当前调用依赖的可证范围作更正。

## 建议
唯一主线：普通MCP调用→真实参数写读→求解→W17取数→W20验证→case登记→重复请求/缓存→最优候选→第二阶段真实初值→同版新Worker重开。先一例，再4个case；两个版本各自执行，比较独立参考而不是互相当真值。
按五个冻结交付修，不建更多验证层/算法框架。所有原要求通过且无A1/A2，Reviewer即批准；待办不必清零。停止在W21，不自动W22。

## 本次隔离诊断边界
review/probe_results.json记录数值缓存碰撞、时间选择、非有限/失败候选等摘录控制流。它不代表实际COMSOL事故或生产接口已经可达，更不证明修复。包工具和探针日志不充当项目native结果。

## 制作环境验证
本轮4项隔离摘录诊断观察到上述缓存/时间/候选机制；未使用COMSOL。恢复和合同检查工具在本地合成Git/ZIP上测试，具体日志与范围见PACKAGE_QA.json。没有执行真实远端恢复、Windows/PowerShell、完整仓库测试或原生双版本验收。
