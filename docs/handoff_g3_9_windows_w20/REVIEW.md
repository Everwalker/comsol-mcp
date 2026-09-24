# G3.8审查 → G3.9执行建议

## 审查身份和边界
公开main固定为 `886affadc83477e940238c723edd569d00559985`，tree `b9861e5dc407f2aff934d9438fa33ef631df06a1`，审查日2026-09-24。检查最新3次提交、关键生产函数、调用包装、驱动记录生成逻辑和两个已提交原始文件。没有连接Windows/COMSOL，没有完整仓库回归，没有逐行读完全仓库。

制作环境无法直接访问GitHub二进制归档（DNS不可用）；本包是联网恢复型，不冒充全量源码镜像。GitHub连接器读到的固定提交用于恢复pin。12项隔离诊断使用已读函数片段与假引擎，其中一项是signature→body流向复现，不是生产RPC实测。

## 结论
**不进入W21。**G3.8修复了部分空数据、必需测点、未知oracle、只读字典与工具注册问题，且新增的瞬态温度记录值得保留；但公开工具调用、非破坏读取、观测来源、报告与收敛的闭环仍未完成。继续增加用例数量或把记录补齐为44条不能解决这些缺口。

### 已有成果，不推倒
- PROGRESS报告1922 passed/8 skipped，及44个目标记录的结构哈希检查通过。这是仓库报告，本次没有重跑。
- FrozenOracle有MappingProxyType、required集合和单位字段，未知oracle/缺必需观测已拒绝。
- W20八个动作的点号/下划线工具已注册。
- 新runner确实包含启动COMSOL、构建正弦瞬态模型、使用result_at_points取值的路径。已提交win64 V08中x=0.5,t=0.03的读数307.50408355223055 K、参考307.43721879410776 K，记录误差0.06686475812279014 K。这个记录不能证明整个MCP入口，但也不应被笼统称作全是模拟数据。

### 为什么44/44仍不能认证
`make_v_record`在函数调用之后创建形似tools/call的JSON，固定test_status=PASS和production_entrypoint=True；这不是从真实MCP stdio采集。`execute_shared_cases`多项检查直接True，甚至直接写全回归计数。runtime_build由字符串拼为版本.0.290，worker_id由run_id拼出。
V05的q_flow=80.0；V09使用固定80/80和-0.05/-0.05；V10/V11继续填固定误差、DOF与耗时，不进行对应网格/步长设置和重算。因此记录应按实际范围重分类。
原始V08的source_commit为ce24f40421063dd27d909651543523a63a2b327e，不等于公开pin；需逐文件源桥接或在可恢复的公开源上重跑。不同SHA不自动说明代码不同，不能未经核实作出指控。

## 必修清单

### F01 / P0 — W20公开参数多嵌套一层

**证据等级：**SOURCE_ROUTE_ANALYSIS_AND_ISOLATED_SIGNATURE_REPLICA。

工具签名arguments:dict，经Gateway dict(bound.arguments)与_g2_body后保留arguments包装，领域函数读取顶层expressions/criteria。stub的return不会被Gateway执行。

**修改建议：**统一显式schema或有边界的规范化；三入口实际调用，幂等身份与execution保留。

定位：`_tools_w20.py; _mcp_gateway.py; _managed_backend.py`。

### F02 / P0 — 边界读取回退调用selection.all()

**证据等级：**SOURCE_CONFIRMED_ISOLATED_REPRO。

entities/getter失败后尝试all，COMSOL此方法会选中所有实体；函数可吞掉异常后返回PASS。caller boundary_data还可优先覆盖真实模型检查。

**修改建议：**禁止任何写型getter回退；使用维数正确的读取；失败标UNVERIFIED/ERROR并验证模型不变。

定位：`_g3_w20_validation.py::validate_boundary_conditions`。

### F03 / P1 — 表达式错误吞掉与预检过度声明

**证据等级：**SOURCE_CONFIRMED_WITH_PARTIAL_ISOLATED_REPRO。

evaluate失败被pass吞掉；2*-3和2^-3等合法组合被正则拒绝；材料选区读取异常不进入findings，有节点不等于就绪。

**修改建议：**COMSOL负责上下文语法/单位；regex仅lint；未知检查不列为已验证；逐个独立反例测试。

定位：`_g3_w20_validation.py::validate_expressions/validate_structure/validate_preflight`。

### F04 / P0 — 请求体数值被标成引擎观测

**证据等级：**SOURCE_CONFIRMED_ISOLATED_REPRO。

values/observations仍从criteria获取；仅成功读取result/dataset目录就改origin=ENGINE_EVALUATION；空数据集列表仍可dataset_exists=True。

**修改建议：**实际调用W17或引用后端登记ObservationRef；origin不能由请求或一次目录读取升级；严格模型/解/单位/时间/来源核对。

定位：`_g3_w20_validation.py::validate_solution`。

### F05 / P1 — 守恒参数没有真实积分与power被忽略

**证据等级：**SOURCE_CONFIRMED_ISOLATED_REPRO。

函数仍只计算输入数字；power通过非空门禁但不进入平衡式；runner填入固定80/80或-0.05/-0.05。

**修改建议：**power定义清楚或拒绝；实际读取通量/源项/储能；严格有限数值、归一化单位与符号。

定位：`_g3_w20_validation.py::validate_conservation; tools/run_g3_8_acceptance.py::V09`。

### F06 / P0 — 收敛仍为硬编码三级列表

**证据等级：**SOURCE_AND_COMMITTED_RECORD_CONFIRMED。

mesh/time误差、DOF和耗时为常量；validate_convergence没有实际case refs；max_allowed使用or会忽略0。

**修改建议：**分别真正细化并求解，误差来自冻结参考和实际观测；消费验证过的case refs，不以严格单调为唯一判据。

定位：`tools/run_g3_8_acceptance.py::V10/V11; _g3_w20_validation.py::ConvergenceStudy`。

### F07 / P0 — 报告错误升级与双文件覆盖

**证据等级：**SOURCE_CONFIRMED_ISOLATED_REPRO。

ERROR/BLOCKED/UNSUPPORTED不会触发FAIL或UNVERIFIED分支，可能PASS；两份write_text无原子发布或覆盖检查；现有同名md会被覆盖。

**修改建议：**完整状态聚合与ArtifactStore事务式发布；错误保持身份、部分产物状态；副文件同样预检。

定位：`_g3_w20_validation.py::validate_report`。

### F08 / P0 — 所谓原始MCP记录由调用后重建

**证据等级：**SOURCE_AND_COMMITTED_RECORD_CONFIRMED。

领域函数直接调用后拼tools/call request/response；固定production_entrypoint=True/PASS；V01生成server_info回应，R/D多项检查直接True或固定测试数。

**修改建议：**真实stdio调用时捕获原始消息；执行器与汇总器分离，汇总器不能补PASS或数值。

定位：`tools/run_g3_8_acceptance.py::make_v_record/execute_shared_cases`。

### F09 / P1 — runtime和source身份不够真实

**证据等级：**SOURCE_CONFIRMED_SCOPE_LIMITATION。

runtime_build由版本字符串拼成.0.290；worker_id自造；原始记录source_commit=ce24f404...，需与公开源逐文件关联，不能用旧PIN掩盖。

**修改建议：**实际读取build/Worker实例与进程创建身份，记录工作树和发布源桥接；不推测源码等价。

定位：`tools/run_g3_8_acceptance.py::execute_live_engine_cases/_get_commit; evidence/.../observations_V08.json`。

## 12项隔离诊断
P01未定义变量仍PASS；P02合法2*-3被拒；P03读取回退会修改选区；P04调用者边界数据覆盖引擎且未知规则PASS；P05请求值被标引擎来源；P06空数据集列表宣称缺失dataset存在；P07 power被忽略；P08 ERROR/BLOCKED/UNSUPPORTED三类子状态被报告升级；P09生成JSON时覆盖已有同名MD；P10公开签名包装未展开使同样逻辑体直接调用和包装调用不同。
结果见review/probe_results.json。三个正控（坏括号、空solution、FAIL报告）通过，说明并非把所有输入一律判错。执行片段可用AST比较确认与恢复源一致；这里不把它称为原生/完整生产验收。

## 下一阶段的最小闭环
先跑一个真实公开工具，看到实际输入进入正确领域函数；然后证明检查不改变模型；再接入后端产生的ObservationRef；最后用真实积分与精度研究替换常量列表，并汇总原始记录。这比继续要求“更多检查表”更有价值。
保持一份代码，Windows 6.3/6.4独立跑；Mac无实机时保留历史范围及新UNVERIFIED。本轮不要求W21通用优化器或W25 Desktop全自动，不为缺云端视觉凭据阻塞当前核心。

## 源码与官方依据
具体固定路径、Git blob和审查范围见SOURCE_MAP.json。COMSOL Selection官方API将all定义为选中全部，entities才是读取；官方表达式表定义了单目正负和二元乘除。其余结论来自仓库源码、已提交记录和本次隔离推演；未将互联网信息补成仓库既有事实。

## 交付可信度
本包提供任务与工具，不是已修复的软件。所有native验收留给本机Agent执行。报告结构/哈希审计无法防止执行者重写一整套伪日志，不能替代实际执行、来源审查或远程证明。
