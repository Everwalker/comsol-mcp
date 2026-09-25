# G3.9审查：G3.10应完成什么
审查日期2026-09-25；固定源`3a7c5feb09738ddcfaedd9c30f7db53b893ec3f9`，tree`f32b390fd71c5a084338d41aa377230ad8e26e9d`。

## 判断
本轮还不具备进入W21的证据。下一轮定为 **G3.10-W20：保护逻辑回归修复＋可信观测/验收收口**，不是再加新功能清单。保留原生建模、取数、图像及本次真正接入的stdio捕获。

### 进展确实存在
新报告记载1932 passed/8 skipped、37/37目标记录。驱动的RecordingSendStream/ReceiveStream确实包裹ClientSession的实际JSON-RPC对象；与上一轮事后拼接伪transcript不同。原始C10含完整isError、模型引用、Worker方法调用记录，说明公开通信路径取得了实证。删除selection.all()回退、对部分表达式异常返回失败、caller数字不再冒充ENGINE来源、报告保留ERROR/BLOCKED/UNSUPPORTED，都是应保留的改动。
这些是仓库源码/记录支持的成果，不是本次重新执行的结果。Model/backend/module边界仍有关键缺口。

### 最强的反例：C10
调用时oracle为`transient_sine_diffusion`，产品只注册`transient_diffusion`。原始C10响应明确 `isError=true`、`success=false`、`oracle_registered=false`、`numerical_verification_status=FAIL`。但C10的checks只有前面直接Worker采样温度的解析误差，未断言validator成功，因而被37/37汇总算作PASS。
这不否定采样温度在0.1K容差内；它证明**数值夹具通过**与**验证产品成功**被合并了。新进度写max_error=0，与原checks中约0.0669K也不同，需要追加准确摘要。

## 关键发现

### F01 P0 — 瞬态验收把真实验证失败当通过
调用oracle=transient_sine_diffusion，但注册名是transient_diffusion。原始响应success=false/isError=true/oracle_registered=false；C10仅检查预先采样温度误差，仍计PASS。

建议：先修测试判据再修输入/产品；核对响应成功、被测科学状态和预期正负控。失败响应或缺关键字段不得靠默认值变为PASS。

定位：`tools/run_g3_9_acceptance.py: C10; evidence/g3_9_windows_w20/win64/mcp_transcript_C10.json`。证据等级：CONFIRMED_SOURCE_AND_COMMITTED_RESPONSE。

### F02 P0 — 隔离与模型绑定保护出现回退
没有明确model_ref时可选择ledger的第一个模型；验证和render按操作名称跳过原隔离证明；验证缺revision时自动补当前值。其他权限可能仍存在，因此不是“全系统无权限”，但破坏原合同。

建议：多模型不能任选；用显式或后端可信选中的绑定。按实际effect验证必要隔离，不按名字豁免。显式旧revision必须拒绝；便利缺省需先定合同并回读，不能掩盖调用者过期上下文。

定位：`comsol_mcp/_managed_backend.py: _invoke_g2_model/_invoke_g3_model`。证据等级：CONFIRMED_SOURCE_SECURITY_REGRESSION。

### F03 P0 — W20修复扩大为全局arguments展开，破坏其它工具
所有工具遇到arguments字典都会展开。registry_call/operation_call本来合法使用该字段包裹内层body，后端仍读取arguments.get("arguments",{}); Java任务参数也可能被拆散。setdefault对已填None默认值又不能用包装值覆盖。

建议：只对明确W20兼容schema规范化一次。保留registry_call、operation_call、code.execute_java中的合法嵌套参数。未知字段/冲突拒绝；三入口在相同实际body和execution下做正负控。

定位：`comsol_mcp/_mcp_gateway.py: add_tool.routed; _managed_backend.py: _g2_body/invoke; _g2_tools.py`。证据等级：CONFIRMED_SOURCE_FLOW。

### F04 P0 — 观测贴标变诚实，但仍未实现模型验证闭环
数据仍来自caller criteria，已修正为CALLER_SUPPLIED；引擎只读dataset目录。C19重开后把旧t2再次传给validator，未重新取存储场。

建议：保留输入数组检查但标EXTERNAL_DATA_ONLY；模型验证必须由现有W17采样/积分或受管ObservationRef提供数据，并核对ModelRef/解/时间/单位/producer。重开必须新取实际场，不复用旧数字。

定位：`comsol_mcp/_g3_w20_validation.py: validate_solution; runner C06/C07/C19`。证据等级：CONFIRMED_SOURCE。

### F05 P0 — 真实stdio传输的是固定收敛列表，不是精化实验
C12/C13继续使用固定误差/DOF/耗时列表。C11在当前瞬态模型上把铜块同一个热流同时填inflow/outflow，storage_rate=0；响应残差默认0。

建议：实际改网格/solver→求解→观测→计算误差；绑定每级模型/作业。守恒分别读真实有向边界、源项和储能，不能同数相减当独立能量证据。

定位：`tools/run_g3_9_acceptance.py: C11/C12/C13`。证据等级：CONFIRMED_SOURCE_AND_REPORT。

### F06 P1 — 回读缺失被成功常量、默认值掩盖
heat_flux回读缺失会回退到79.99999999999962；多处字段缺失默认True/PASS/0；有的case只看not isError。缺operation_id时随机造UUID。

建议：必需读数/身份缺失立刻失败。测试允许明确预期的negative response，禁止默认成功。observer和validator各自producer ID不可互换。

定位：`tools/run_g3_9_acceptance.py: copper readback, C02–C07/C11/C18`。证据等级：CONFIRMED_SOURCE。

### F07 P0 — 报告仍是直接双文件写入，非授权原子产物
已加入同名副文件预检和错误状态优先级，但仍直接Path/write_text、检查与写入存在竞态，没有ArtifactStore登记/统一授权。bool("false")=true；未知状态可落PASS。

建议：报告消费已登记ValidationRun，所有目标一起预检，使用现有ArtifactStore原子发布/覆盖策略，严格bool/枚举和非有限JSON处理；原数据/状态不因生成报告被升级。

定位：`comsol_mcp/_g3_w20_validation.py: validate_report`。证据等级：CONFIRMED_SOURCE。

### F08 P1 — 结构和数值工具仍未覆盖其宣称范围
材料、网格、Study主要检查tag存在；不能推出网格已build/物性足够/physics启用。收敛接受caller error，NaN/Inf阈值未全面拒绝，equal-errors trend可能PASS。

建议：只声明实际观察规则；为热传导profile补字段级检查、未知覆盖。可信收敛RunRef+有限输入/最终阈值/实际细化，避免用新的全能validator扩范围。

定位：`_g3_w20_validation.py: validate_structure/preflight/ConvergenceStudy`。证据等级：CONFIRMED_SOURCE_LIMITATION。

### F09 P1 — 运行身份、源码、总结与审查过程尚未闭合
根进度仍G3.8；runtime_build/worker_id运行前拼接。新文档称瞬态最大误差0，原checks约0.0669K。部分shared检查直接True。本次未取得独立Reviewer会话证据，不从文件名推断其存在。

建议：保留旧记录加更正；build/Worker取实读身份；全源/driver/oracle/证据快照绑定；新阶段由真实独立Reviewer持有验收判据并出具同快照裁决。

定位：`PROGRESS.md; docs/handoff_g3_9_windows_w20/PROGRESS.md; runner make_case_record`。证据等级：CONFIRMED_SOURCE_AND_SCOPE_LIMITATION。

## 为什么上一轮独立Review仍须加强
只有“让另一个agent说PASS”没有约束。当前需要Reviewer先冻结完成标准、核对一个已提交失败样本，再审Developer的同一源码快照，并独立执行负控；Evaluator必须同时检查传输结果、被测validator结果、模型/观测身份和数值，不依赖开发者总结、测试数量或12个旧探针已不复现。

本包 roles/ 和 TEAM_PROTOCOL.md 给出真实会话分离、只读生产源码、评审自写测试、不可篡改判据（相对于角色权限，不是抵抗机器管理员的安全沙箱）、逐轮快照和复审规则。无法实际派生独立agent时须说明INDEPENDENT_REVIEW_UNAVAILABLE；不得在同一个会话里换署名模拟独立评审。

## 证据与未验证边界
本次通过GitHub连接器读取固定版本的关键文件与原始响应；直接网络归档下载不可用，因此交付的是可验证联网恢复包，不是全源码离线镜像。没有调用你的Windows、COMSOL、Agent Host或重新跑完整pytest。独立诊断仅复制已审查的函数或片段并提供假依赖；它们检验具体控制流，不证明任何实际模型遭到损坏。

下一轮不把“更多门禁”作为产出。核心交付是：两版本用显式模型与真实解，经现有W17获取可信ObservationRef，再由W20判断并输出安全报告；包含真正精化、故意错误模型、双模型无串扰和保存重读。只做必要修补与热传导限定profile，其他已实现模块保留。

源码位置、Git blob、读取范围与官方补充资料见SOURCES.json。没有引用到的仓库目录不宣称已审。


## 本工作包附带的隔离诊断
5项有限诊断保存在review/PROBE_RESULTS.json：三项使用当前Gateway.add_tool摘录与假注册器/假dispatcher，分别展示registry嵌套载荷丢失、Java任务Map被展开、默认None压住兼容包装值；一项重放C10摘取事实以拒绝错误的正向通过；一项只复现名称豁免的布尔条件。对冲突包装拒绝做了正向控制。
这不是完整模块/真实RPC/引擎或权限系统实测，不能据此声称发生了用户数据泄露或任意权限都被绕过。C10事实文件明确不是重新生成的原始capture；原始证据必须从固定仓库读取。
