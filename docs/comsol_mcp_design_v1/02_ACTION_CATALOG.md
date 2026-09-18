# 全能力动作目录

本目录共 **272 个逻辑动作**，是设计目标，不是声称当前仓库已有这些工具。工具名采用 `domain_action`，逻辑编号采用 `domain.action`。

## 阅读和实现规则

`?` 表示可选参数。模型操作共同要求 project_id/session_id/model_ref；模型写操作另需 expected_revision、idempotency_key。job/status 必须允许引擎断开时读取，不强制活跃模型句柄。数量不是完成度指标；每项以真实路径、回读和测试验收。

风险：READ=只读；WRITE=模型写；EVALUATE=数值计算（可能临时写节点，须串行）；COMPUTE=长计算；FILE_WRITE=产物写；STATE_WRITE=管理状态写；TRUSTED_CODE=授权代码；HOST_CONTROL=窗口/主机/外发控制；DYNAMIC=按明确子动作决定效果与权限。不能因为命名为manage/invoke就逃避权限分类。

JSON 附件为每个动作提供顶层输入 schema；类型见 common.schema.json。涉及 COMSOL feature-specific properties 的语义 schema 必须由实际版本能力生成并经测试，本文不伪造所有商业模块的内部属性列表。输出统一 ActionResult，但发布前仍要按领域补充 data 的具体 schema。

## 环境、安装与许可证（runtime）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `runtime_discover` | `roots:strings?` | 发现全部 COMSOL 安装、JDK 与架构 | READ / G0 |
| `runtime_inspect` | `runtime_id:str` | 读取指定安装完整 build、启动器、classpath | READ / G0 |
| `runtime_doctor` | `runtime_id:str?,checks:strings?` | 检查安装、目录、端口、JVM、渲染与配置 | READ / G0 |
| `runtime_capabilities` | `runtime_id:str?,refresh:bool?` | 读取能力状态、限制与实测证据 | READ / G0 |
| `runtime_license_inspect` | `runtime_id:str,products:strings?` | 检查许可允许的产品，不静默占用许可 | READ / G0 |
| `runtime_license_checkout` | `runtime_id:str,products:strings,authorization_ref:str` | 经授权尝试 checkout 并记录结果 | HOST_CONTROL / G0 |
| `runtime_render_probe` | `runtime_id:str,mode:str?` | 在隔离最小模型测试渲染输出 | COMPUTE / G0 |
| `runtime_compatibility_report` | `runtime_ids:strings,requirements:object` | 检查任务需求与目标安装兼容性 | READ / G0 |

## 工具发现与操作协议（registry）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `registry_list` | `domain:str?,cursor:str?,limit:int?` | 按域分页列出逻辑动作 | READ / G2 |
| `registry_describe` | `operation_id:str` | 取得单一动作完整输入/输出与示例 | READ / G2 |
| `registry_search` | `query:str,domain:str?` | 按任务语义或关键字查动作 | READ / G2 |
| `registry_call` | `operation_id:str,arguments:object` | host无法动态加载时调用指定动作；二次严格校验 | DYNAMIC / G2 |
| `registry_manifest` | `profile:str?` | 输出当前版本实际注册工具及兼容证据 | READ / G2 |

## 项目、任务契约与权限（project）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `project_create` | `label:str,workspace:str,policy:object` | 创建项目工作区和初始策略 | STATE_WRITE / G1 |
| `project_inspect` | `project_id:str` | 读取任务、授权、资源预算与数据策略 | READ / G1 |
| `project_contract_set` | `project_id:str,contract:object` | 登记目标、ROI、物理假设、阈值、交付版本 | STATE_WRITE / G1 |
| `project_policy_set` | `project_id:str,policy:object,authorization_ref:str` | 经授权修改项目权限及超时/资源预算 | HOST_CONTROL / G1 |
| `project_permissions` | `project_id:str` | 读取当前有效授权，不返回密钥 | READ / G1 |
| `project_state_export` | `project_id:str,detail:str?` | 为host压缩/恢复提供模型与任务状态摘要 | READ / G4 |

## Server 会话与所有权（session）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `session_list` | `filter:object?` | 列出活跃/失联/已停止的会话 | READ / G0 |
| `session_connect` | `runtime_id:str,endpoint:Endpoint,credentials_ref:str?` | 连接已有 mphserver，核对版本与所有权 | STATE_WRITE / G0 |
| `session_start` | `runtime_id:str,options:object?,resources:object?` | 启动专用受管 mphserver | HOST_CONTROL / G0 |
| `session_inspect` | `session_id:str` | 读取连接、模型身份、Server所有权与健康状态 | READ / G0 |
| `session_reconnect` | `session_id:str` | 重新附着并使旧对象句柄失效 | STATE_WRITE / G4 |
| `session_disconnect` | `session_id:str` | 断开客户端，不默认关闭Server | STATE_WRITE / G0 |
| `session_stop` | `session_id:str,authorization_ref:str` | 关闭自己管理的Server；共享Server另需授权 | HOST_CONTROL / G4 |
| `session_health` | `session_id:str` | 读取缓存/轻量生存性，不阻塞等待求解 | READ / G0 |
| `session_recover` | `session_id:str,recovery_policy:object?` | 对失联任务和模型作状态核对 | STATE_WRITE / G4 |

## 模型生命周期与可移植性（model）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `model_list` | `session_id:str` | 列出服务端模型，不删除任何模型 | READ / G0 |
| `model_create` | `session_id:str,label:str,dimension:int?` | 显式新建模型并返回唯一身份 | WRITE / G3 |
| `model_load` | `session_id:str,artifact_id:str,path_policy:object?` | 加载文件，默认不清理其他模型 | WRITE / G0 |
| `model_adopt` | `session_id:str,server_model_tag:str` | 绑定已存在的server model tag | STATE_WRITE / G0 |
| `model_inspect` | `detail:str?` | 读取身份、结构摘要、解/外部依赖 | READ / G1 |
| `model_tree` | `path:NodePath?,depth:int?,cursor:str?,limit:int?` | 按深度/路径/分页取得模型树 | READ / G2 |
| `model_save` | `destination:str,overwrite:bool,include_solution:bool?` | 保存到批准路径/Artifact，明确覆盖策略 | FILE_WRITE / G3 |
| `model_clone` | `label:str,include_solution:bool?` | 生成隔离实验模型并返回新model_ref | WRITE / G2 |
| `model_close` | `discard_changes:bool,authorization_ref:str?` | 关闭指定模型；保护未保存修改 | HOST_CONTROL / G1 |
| `model_dependencies` | `verify_hashes:bool?` | 列出函数数据、CAD、外部代码等依赖 | READ / G3 |
| `model_package` | `destination:str,include_solution:bool?` | 导出可移植模型与依赖清单 | FILE_WRITE / G6 |
| `model_rebuild_target` | `target_runtime_id:str,recipe_artifact:str` | 在指定引擎重放recipe，不伪装文件降级 | COMPUTE / G6 |
| `model_compare` | `other_model_ref:str,scope:object?` | 比较模型结构、关键属性与指标 | READ / G6 |

## 通用模型对象与嵌套节点（node）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `node_inspect` | `path:NodePath,include_values:bool?` | 读取类型、标签、属性、选择、子集合 | READ / G2 |
| `node_children` | `path:NodePath,cursor:str?,limit:int?` | 列出指定节点的子集合和对象 | READ / G2 |
| `node_find` | `query:object,root:NodePath?,limit:int?` | 按type/tag/label/path搜索模型节点 | READ / G2 |
| `node_create` | `parent:NodePath,collection:str,tag:str,type_id:str,properties:PropertySet?` | 在typed collection创建节点 | WRITE / G2 |
| `node_copy` | `source:NodePath,target_parent:NodePath,tag:str` | 在同模型或分支复制节点并核对依赖 | WRITE / G2 |
| `node_remove` | `path:NodePath,cascade:bool?` | 预检依赖后移除节点；可报告受影响对象 | WRITE / G2 |
| `node_label_set` | `path:NodePath,label:str` | 修改展示标签，不把标签作为身份 | WRITE / G2 |
| `node_active_set` | `path:NodePath,active:bool` | 启用/禁用节点 | WRITE / G2 |
| `node_move` | `path:NodePath,before:NodePath?,after:NodePath?` | 在支持排序的集合中调整位置 | WRITE / G2 |
| `node_property_schema` | `path:NodePath,name:str?` | 取得属性类型、枚举、索引信息 | READ / G2 |
| `node_property_get` | `path:NodePath,names:strings` | 按类型回读一个或多个属性 | READ / G2 |
| `node_property_set` | `path:NodePath,properties:PropertySet` | 设置带类型的标量/向量/矩阵并回读 | WRITE / G2 |
| `node_property_index_set` | `path:NodePath,name:str,indices:ints,value:TypedValue` | 按明确索引设置数组/矩阵元素 | WRITE / G2 |
| `node_property_entry_set` | `path:NodePath,name:str,key:str,value:TypedValue` | 按entry key设置值 | WRITE / G2 |
| `node_selection_get` | `path:NodePath,selection_name:str?` | 读取实体/对象/命名选择及继承状态 | READ / G2 |
| `node_selection_set` | `path:NodePath,selection:SelectionSpec,selection_name:str?` | 按明确selection种类设置 | WRITE / G2 |

## 公共 API 描述与结构化调用（api）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `api_describe` | `path:NodePath,method:str?` | 列出允许的公共方法、签名和版本文档 | READ / G2 |
| `api_invoke` | `path:NodePath,method:str,arguments:TypedValues,java_signature:strings?,declared_effect:str` | 调用未高层封装的公共COMSOL方法 | DYNAMIC / G2 |
| `api_probe` | `probe:object` | 在副本上验证公共API/特征可用性 | COMPUTE / G2 |

## MCP 内受控代码执行（code）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `code_compile_java` | `runtime_id:str,source_artifact:str,entrypoint:str` | 按目标引擎classpath编译，不执行模型动作 | COMPUTE / G2 |
| `code_execute_java` | `source_artifact:str,entrypoint:str,arguments:object,mode:str,timeout_s:number?,invariants:objects?` | 在注入的目标Model上执行已授权代码 | TRUSTED_CODE / G2 |
| `code_inspect_run` | `job_id:str` | 读取代码、编译信息、日志与副作用 | READ / G2 |
| `code_recipe_register` | `source_artifact:str,input_schema:object,test_refs:strings` | 把测试过代码登记为可复用recipe | STATE_WRITE / G5 |
| `code_recipe_run` | `recipe_id:str,arguments:object` | 按版本检查执行指定recipe | TRUSTED_CODE / G5 |

## 参数、分组与案例（parameter）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `parameter_list` | `group:str?` | 列出参数组、原表达式、描述和单位 | READ / G3 |
| `parameter_get` | `names:strings,evaluate:bool?` | 读取指定参数与可选求值 | READ / G3 |
| `parameter_set` | `parameters:objects,group:str?` | 批量设置参数表达式和描述 | WRITE / G3 |
| `parameter_remove` | `names:strings,group:str?` | 删除参数并报告引用风险 | WRITE / G3 |
| `parameter_group_manage` | `action:str,tag:str,arguments:object?` | 创建/重命名/删除参数组或移动参数 | WRITE / G3 |
| `parameter_case_manage` | `action:str,group:str,case_tag:str,values:object?` | 创建、读取或应用参数案例 | DYNAMIC / G5 |
| `parameter_import` | `artifact_id:str,format:str,group:str?` | 从项目数据文件导入并校验单位 | WRITE / G3 |
| `parameter_export` | `format:str,destination:str` | 导出参数、单位与描述 | FILE_WRITE / G3 |

## 全局/组件变量组（variable）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `variable_list` | `component:str?` | 列出所有组与变量名、表达式、描述 | READ / G1 |
| `variable_group_create` | `tag:str,component:str?,selection:SelectionSpec?` | 创建变量组及作用选区 | WRITE / G1 |
| `variable_set` | `group:NodePath,variables:objects` | 正确设置name→expression，支持同组多变量 | WRITE / G1 |
| `variable_get` | `group:NodePath,names:strings?` | 读取变量表达式/描述/有效范围 | READ / G1 |
| `variable_remove` | `group:NodePath,names:strings?,whole_group:bool?` | 删除组内变量或显式删除整个组 | WRITE / G1 |
| `variable_selection_set` | `group:NodePath,selection:SelectionSpec` | 设置变量组命名选区 | WRITE / G3 |

## 函数与插值热源（function）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `function_list` | `component:str?` | 列出全局/组件函数及依赖 | READ / G3 |
| `function_create` | `scope:NodePath?,tag:str,type_id:str,definition:object` | 创建解析、插值、分段、阶跃等函数 | WRITE / G3 |
| `function_inspect` | `path:NodePath` | 读取定义、参数单位、值单位和外推方式 | READ / G3 |
| `function_update` | `path:NodePath,definition:object` | 更新函数属性与分段/插值设置 | WRITE / G3 |
| `function_remove` | `path:NodePath` | 删除函数并检测引用 | WRITE / G3 |
| `function_data_import` | `path:NodePath,artifact_id:str,layout:object,units:object` | 绑定多维插值数据、坐标轴、单位和网格布局 | WRITE / G3 |
| `function_data_reload` | `path:NodePath` | 重新读取输入文件并记录hash | WRITE / G3 |
| `function_evaluate` | `path:NodePath,arguments:objects,derivative:object?` | 在指定测试点返回值及越界状态 | EVALUATE / G3 |
| `function_validate` | `path:NodePath,checks:object?` | 检查定义域、单位、连续性/外推规则 | EVALUATE / G3 |

## 选择集与空间实体（selection）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `selection_list` | `component:str?` | 列出命名选择、维数与实体数 | READ / G3 |
| `selection_create` | `component:str,tag:str,type_id:str,definition:object` | 创建explicit/空间/布尔/adjacent等命名选择 | WRITE / G3 |
| `selection_inspect` | `component:str,tag:str` | 读取命名选择的定义与当前展开实体 | READ / G3 |
| `selection_update` | `component:str,tag:str,definition:object` | 更新命名选择并报告实体变化 | WRITE / G3 |
| `selection_remove` | `component:str,tag:str` | 移除命名选择并报告引用对象 | WRITE / G3 |
| `selection_entities` | `selection:SelectionSpec,cursor:str?,limit:int?` | 返回实体ID与几何修订，不作跨修订稳定承诺 | READ / G3 |
| `selection_query_spatial` | `component:str,geometry:str,dimension:int,query:object,tolerance:Quantity` | 按点、框、法向、测度等条件检索实体 | EVALUATE / G3 |
| `selection_measure` | `selection:SelectionSpec,metrics:strings` | 取得面积/体积/长度、bbox、质心和方法 | EVALUATE / G3 |
| `selection_adjacency` | `selection:SelectionSpec,target_dimension:int` | 查询域/面/边/点之间邻接 | READ / G3 |
| `selection_validate` | `selection:SelectionSpec,expectations:object` | 检查非空、维数、预期测度/位置/数量 | EVALUATE / G3 |
| `selection_rebind` | `selection:SelectionSpec,rule:object,preview:bool?` | 几何变化后按语义规则重建或核验绑定 | WRITE / G4 |

## 几何、工作平面与 CAD（geometry）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `geometry_sequence_create` | `component:str,tag:str,dimension:int,axisymmetric:bool?` | 创建指定维数/轴对称属性的几何序列 | WRITE / G3 |
| `geometry_inspect` | `path:NodePath` | 读取序列特征、单位、内核和构建状态 | READ / G3 |
| `geometry_feature_create` | `parent:NodePath,tag:str,type_id:str,properties:PropertySet,inputs:object?` | 创建基本体/布尔/变换/阵列等 | WRITE / G3 |
| `geometry_feature_update` | `path:NodePath,properties:PropertySet,inputs:object?` | 改指定现有特征而不重建整个模型 | WRITE / G3 |
| `geometry_feature_remove` | `path:NodePath` | 删除特征并报告下游依赖 | WRITE / G3 |
| `geometry_workplane_create` | `geometry:NodePath,tag:str,definition:object` | 建立工作平面与局部坐标定义 | WRITE / G3 |
| `geometry_workplane_edit` | `workplane:NodePath,actions:objects` | 对嵌套2D几何应用typed操作计划 | WRITE / G3 |
| `geometry_array_create` | `geometry:NodePath,tag:str,definition:object` | 建立线性/矩形/环形/自定义阵列并保留参数 | WRITE / G5 |
| `geometry_build` | `geometry:NodePath,until_tag:str?` | 完整构建或构建到指定feature，作为作业 | COMPUTE / G3 |
| `geometry_import` | `geometry:NodePath,tag:str,artifact_id:str,options:object?` | 导入批准的CAD文件、单位和内核 | WRITE / G3 |
| `geometry_export` | `geometry:NodePath,format:str,destination:str,selection:SelectionSpec?` | 导出几何，检查格式/许可支持 | FILE_WRITE / G5 |
| `geometry_finalize` | `geometry:NodePath,mode:str,options:object?` | 设置Form Union/Assembly与保留内部边界选项 | WRITE / G3 |
| `geometry_repair` | `geometry:NodePath,plan:object` | 修复/去特征/虚拟操作，须副本或checkpoint | WRITE / G5 |
| `geometry_measure` | `geometry:NodePath,query:object` | 测量对象、实体和间距 | EVALUATE / G3 |
| `geometry_validate` | `geometry:NodePath,expectations:object?` | 检查构建问题、实体数量、体积和设计间距 | EVALUATE / G3 |

## 坐标系、Pair、耦合与组件（definition）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `definition_component_manage` | `action:str,tag:str,definition:object?` | 创建/检查/复制/删除组件 | DYNAMIC / G3 |
| `definition_coordinate_manage` | `action:str,path:NodePath?,definition:object?` | 坐标系CRUD与变换检查 | DYNAMIC / G3 |
| `definition_pair_manage` | `action:str,path:NodePath?,definition:object?` | identity/contact等Pair CRUD和方向选择 | DYNAMIC / G3 |
| `definition_coupling_manage` | `action:str,path:NodePath?,definition:object?` | 积分/平均/极值/投影/拉伸算子CRUD | DYNAMIC / G3 |
| `definition_mapping_validate` | `path:NodePath,test_points:objects?,checks:object?` | 检查源目标框架、映射误差和覆盖 | EVALUATE / G5 |

## 材料和属性组（material）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `material_list` | `component:str?` | 列出材料、类型、域覆盖和属性组 | READ / G3 |
| `material_create` | `component:str,tag:str,type_id:str,definition:object?` | 创建材料/链接/切换结构 | WRITE / G3 |
| `material_inspect` | `path:NodePath` | 读取材料属性、表达式、来源与域绑定 | READ / G3 |
| `material_set_properties` | `path:NodePath,group:str,properties:PropertySet,provenance:object?` | 按property group写标量/张量/温度函数 | WRITE / G3 |
| `material_group_manage` | `path:NodePath,action:str,group:str,definition:object?` | 管理材料属性组及输入变量 | WRITE / G3 |
| `material_selection_set` | `path:NodePath,selection:SelectionSpec` | 为材料绑定命名域/边界选择 | WRITE / G3 |
| `material_remove` | `path:NodePath` | 删除材料并给出失去材料的域 | WRITE / G3 |
| `material_import` | `artifact_id:str,selection:SelectionSpec?,provenance:object` | 从合法项目模型/文件导入材料定义 | WRITE / G3 |
| `material_validate` | `scope:NodePath?,checks:object?` | 检查已启用物理需要的已知属性规则 | EVALUATE / G3 |

## 物理接口、子特征与多物理（physics）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `physics_list` | `component:str?` | 列出接口类型、变量、域和feature树 | READ / G3 |
| `physics_create` | `component:str,tag:str,type_id:str,geometry:str,dependent_variables:strings?` | 按真实接口类型及几何绑定创建physics | WRITE / G3 |
| `physics_inspect` | `path:NodePath,depth:int?` | 读取接口及其边界/域/初值与变量设置 | READ / G3 |
| `physics_remove` | `path:NodePath` | 删除接口并报告Study/耦合依赖 | WRITE / G3 |
| `physics_feature_create` | `parent:NodePath,tag:str,type_id:str,entity_dimension:int?,properties:PropertySet?` | 创建域/面/边/点特征和嵌套子特征 | WRITE / G3 |
| `physics_feature_update` | `path:NodePath,properties:PropertySet` | 更新feature属性并回读 | WRITE / G3 |
| `physics_feature_remove` | `path:NodePath` | 删除子特征 | WRITE / G3 |
| `physics_selection_set` | `path:NodePath,selection:SelectionSpec` | 明确物理接口自身或子特征选择 | WRITE / G1 |
| `physics_multiphysics_manage` | `action:str,path:NodePath?,definition:object?` | 多物理耦合CRUD和接口关联 | DYNAMIC / G3 |
| `physics_pde_manage` | `action:str,path:NodePath?,definition:object` | 配置系数/一般/弱形式PDE与全局ODE | WRITE / G5 |
| `physics_initial_values_set` | `path:NodePath,definition:object` | 设置初值或前一阶段解来源 | WRITE / G3 |
| `physics_validate` | `scope:NodePath?,checks:object?` | 检查已知缺失/冲突/继承/维数规则 | EVALUATE / G3 |

## 网格构建与质量（mesh）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `mesh_list` | `component:str?` | 列出网格序列、状态与来源 | READ / G3 |
| `mesh_create` | `component:str,tag:str,geometry:str,mode:str?` | 创建mesh序列及物理控制/用户控制模式 | WRITE / G3 |
| `mesh_inspect` | `path:NodePath,depth:int?` | 读取mesh feature树和已生成网格统计 | READ / G3 |
| `mesh_feature_create` | `parent:NodePath,tag:str,type_id:str,properties:PropertySet,selection:SelectionSpec?` | 创建Size/FreeTri/FreeTet/Swept/边界层等 | WRITE / G3 |
| `mesh_feature_update` | `path:NodePath,properties:PropertySet` | 修改局部尺寸/层厚/分布等 | WRITE / G3 |
| `mesh_feature_remove` | `path:NodePath` | 删除网格特征 | WRITE / G3 |
| `mesh_build` | `path:NodePath,until_tag:str?` | 生成全部网格或生成到feature | COMPUTE / G3 |
| `mesh_clear` | `path:NodePath` | 清除生成网格，不静默删用户配置 | WRITE / G3 |
| `mesh_quality` | `path:NodePath,metric:str,selection:SelectionSpec?,bins:int?` | 返回质量度量定义、分布及差元素位置 | EVALUATE / G3 |
| `mesh_statistics` | `path:NodePath` | 返回单元数、维数、覆盖、DOF/资源估计 | READ / G3 |
| `mesh_import` | `path:NodePath,artifact_id:str,options:object?` | 导入网格并验证单位/拓扑 | WRITE / G5 |
| `mesh_export` | `path:NodePath,format:str,destination:str` | 导出网格文件与实体映射 | FILE_WRITE / G5 |
| `mesh_validate` | `path:NodePath,criteria:object` | 检查域覆盖、单元质量、边界层完整性 | EVALUATE / G3 |
| `mesh_convergence_study` | `definition:object,metrics:strings` | 生成多网格分支并对指定指标比较 | COMPUTE / G5 |

## 研究步骤与扫描（study）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `study_list` | `scope:NodePath?` | 列出Study、step、physics启用和solver关联 | READ / G3 |
| `study_create` | `tag:str,label:str?` | 新建Study | WRITE / G3 |
| `study_inspect` | `path:NodePath` | 读取Study步骤、扫描和依赖 | READ / G3 |
| `study_remove` | `path:NodePath,remove_solver:bool?` | 删除Study并显式处理关联solver | WRITE / G3 |
| `study_step_create` | `study:NodePath,tag:str,type_id:str,properties:PropertySet` | 创建稳态/瞬态/频域/特征值等步骤 | WRITE / G3 |
| `study_step_update` | `path:NodePath,properties:PropertySet` | 更新时间点/频率/求解变量等 | WRITE / G3 |
| `study_step_remove` | `path:NodePath` | 删除步骤 | WRITE / G3 |
| `study_physics_activation` | `step:NodePath,activation:object` | 按步骤启用/停用物理及变量求解 | WRITE / G3 |
| `study_initial_solution_set` | `step:NodePath,source:SolutionSpec,mapping:object?` | 关联先前解/时间点/参数case | WRITE / G5 |
| `study_sweep_manage` | `study:NodePath,action:str,definition:object` | 配置参数/辅助/批处理扫描 | WRITE / G5 |
| `study_solver_generate` | `study:NodePath,replace_existing:bool` | 生成自动solver；覆盖旧配置必须显式 | WRITE / G3 |
| `study_run` | `study:NodePath,resources:object?,timeout_s:number?` | 提交Study求解，立即返回持久job | COMPUTE / G3 |

## 求解器树与解管理（solver）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `solver_list` | `filter:object?` | 列出solver和solution及关联 | READ / G3 |
| `solver_inspect` | `path:NodePath,depth:int?` | 递归读取solver设置、子特征与问题节点 | READ / G3 |
| `solver_create` | `tag:str,study:NodePath` | 新建solver配置并关联Study | WRITE / G3 |
| `solver_feature_create` | `parent:NodePath,tag:str,type_id:str,properties:PropertySet?` | 创建Stationary/Time/Direct/Iterative/Segregated等节点 | WRITE / G3 |
| `solver_feature_update` | `path:NodePath,properties:PropertySet` | typed设置容差、时间步、缩放等 | WRITE / G3 |
| `solver_feature_remove` | `path:NodePath` | 删除任意受支持子feature | WRITE / G3 |
| `solver_run` | `path:NodePath,range:object?,timeout_s:number?` | 提交solver序列或指定范围求解 | COMPUTE / G3 |
| `solver_solution_inspect` | `solution:SolutionSpec` | 区分初始化/空/可用解；返回索引与参数 | READ / G4 |
| `solver_solution_clear` | `path:NodePath,scope:str` | 显式清除解数据与相关缓存 | WRITE / G4 |
| `solver_solution_transfer` | `source:SolutionSpec,target:NodePath,mapping:object` | 将明确选定的解映射到下阶段 | WRITE / G5 |
| `solver_log_read` | `job_id:str,cursor:str?,limit:int?` | 读取真实求解日志和异常链 | READ / G4 |
| `solver_resource_configure` | `resources:object,scope:str` | 设置经支持验证的核数/内存/运行参数 | WRITE / G4 |

## 持久任务与恢复（job）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `job_list` | `filter:object?,cursor:str?,limit:int?` | 列出项目内作业，可在引擎离线时调用 | READ / G4 |
| `job_status` | `job_id:str` | 读取持久状态、阶段与实际进度 | READ / G4 |
| `job_log` | `job_id:str,cursor:str?,limit:int?` | 分页读取原始日志，不需要引擎空闲 | READ / G4 |
| `job_result` | `job_id:str` | 取得最终结果与artifact，不触发重算 | READ / G4 |
| `job_cancel` | `job_id:str,mode:str,reason:str` | 请求中止并等待独立确认状态 | HOST_CONTROL / G4 |
| `job_reconcile` | `job_id:str` | 核对超时/重启后的真实执行状态 | STATE_WRITE / G4 |
| `job_resume` | `job_id:str,checkpoint_id:str?` | 只对可重入的已检查点任务恢复 | COMPUTE / G4 |
| `job_wait` | `job_id:str,max_wait_s:number` | 有界等待并返回状态；不替代持久任务 | READ / G4 |
| `job_cleanup` | `job_ids:strings,policy:object` | 清理已完成job元数据/缓存，保留正式交付物 | HOST_CONTROL / G6 |

## 数据集、截面与解索引（dataset）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `dataset_list` | `filter:object?` | 列出datasets与解、时间/参数索引关联 | READ / G4 |
| `dataset_create` | `tag:str,type_id:str,definition:object` | 创建solution/cutpoint/cutline/cutplane/join等 | WRITE / G4 |
| `dataset_inspect` | `path:NodePath` | 读取数据来源、坐标、过滤及选区 | READ / G4 |
| `dataset_update` | `path:NodePath,definition:object` | 更新数据集属性和源解 | WRITE / G4 |
| `dataset_remove` | `path:NodePath` | 删除数据集并提示依赖plot/evaluation | WRITE / G4 |
| `dataset_solution_indices` | `path:NodePath` | 列出内/外解、真实时间/频率/参数组合 | READ / G4 |

## 数值求值、复场与原始数据（result）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `result_evaluate` | `spec:EvaluationSpec` | 全局/点/积分/平均/极值；保留时间和复数 | EVALUATE / G4 |
| `result_at_points` | `spec:EvaluationSpec,points:objects,coordinate_unit:str,frame:str` | 在明确坐标/框架求值并返回有效掩膜 | EVALUATE / G4 |
| `result_sample_grid` | `spec:EvaluationSpec,grid:object` | 在规则网格或面上导出静态场 | EVALUATE / G4 |
| `result_sample_path` | `spec:EvaluationSpec,path_definition:object` | 沿线/曲线采样并保留弧长与坐标 | EVALUATE / G4 |
| `result_numerical_manage` | `action:str,path:NodePath?,definition:object?` | 维护用户可见Derived Values节点，不清整个集合 | DYNAMIC / G4 |
| `result_table_manage` | `action:str,path:NodePath?,definition:object?` | 表创建/读取/追加/清理/删除 | DYNAMIC / G4 |
| `result_field_export` | `spec:EvaluationSpec,format:str,destination:str` | 导出大复场/解向量及坐标、单位、hash | FILE_WRITE / G4 |
| `result_mode_overlap` | `definition:object` | 计算带归一化与约定的模式重叠指标 | EVALUATE / G5 |

## 探针与监控（probe）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `probe_list` | `filter:object?` | 读取global/point/boundary/domain探针 | READ / G4 |
| `probe_create` | `tag:str,type_id:str,definition:object` | 创建probe、表达式、选区与表 | WRITE / G4 |
| `probe_update` | `path:NodePath,definition:object` | 修改probe配置 | WRITE / G4 |
| `probe_remove` | `path:NodePath` | 移除probe，不删除无关结果 | WRITE / G4 |
| `probe_history` | `path:NodePath,solution:SolutionSpec?,cursor:str?` | 返回记录的probe历史、时间/参数和单位 | READ / G4 |

## 绘图、视图与图像（plot）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `plot_list` | `filter:object?` | 列出plotgroup及其绑定的数据集/解 | READ / G4 |
| `plot_group_create` | `tag:str,dimension:int,dataset:NodePath,properties:PropertySet?` | 创建1D/2D/3D绘图组 | WRITE / G4 |
| `plot_feature_create` | `group:NodePath,tag:str,type_id:str,properties:PropertySet` | 创建surface/slice/contour/arrow/line等 | WRITE / G4 |
| `plot_update` | `path:NodePath,properties:PropertySet` | 修改表达式、范围、颜色/图例/数据绑定 | WRITE / G4 |
| `plot_remove` | `path:NodePath` | 移除绘图组或feature | WRITE / G4 |
| `plot_render` | `path:NodePath,solution:SolutionSpec?,options:object?` | 按明确solution导出真实绘图图像 | COMPUTE / G4 |
| `plot_geometry_render` | `geometry:NodePath,mode:str,options:object?` | 渲染几何/网格/实体标签供检查 | COMPUTE / G4 |
| `plot_view_manage` | `action:str,path:NodePath?,definition:object?` | 创建/读取/设置视角、相机、轴、隐藏实体 | DYNAMIC / G4 |

## 导出、报告与正式交付（export）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `export_list` | `filter:object?` | 列出export节点及设置 | READ / G4 |
| `export_create` | `tag:str,type_id:str,definition:object` | 创建数据/图片/动画/表格导出 | WRITE / G4 |
| `export_update` | `path:NodePath,definition:object` | 更新导出参数和目标Artifact | WRITE / G4 |
| `export_run` | `path:NodePath` | 执行export并检查文件存在、大小、hash | COMPUTE / G4 |
| `export_remove` | `path:NodePath` | 删除export配置，不默认删目标文件 | WRITE / G4 |
| `export_report` | `definition:object,destination:str` | 生成模型/结果/验收报告与来源 | FILE_WRITE / G5 |
| `export_evidence_bundle` | `definition:object,destination:str` | 生成mph+recipe+原始数据+图+日志+来源的包 | FILE_WRITE / G6 |

## 指标、数值与物理验收（metric）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `metric_define` | `metric_id:str,definition:object` | 定义单位、ROI、加权方式、索引与阈值 | STATE_WRITE / G4 |
| `metric_list` | `filter:object?` | 列出任务级指标定义与状态 | READ / G4 |
| `metric_evaluate` | `metric_ids:strings,solution:SolutionSpec?` | 批量评估指标，返回每项证据 | EVALUATE / G4 |
| `metric_remove` | `metric_id:str` | 删除指标定义，不改用户原结果节点 | STATE_WRITE / G4 |
| `metric_compare` | `cases:objects,metric_ids:strings,tolerances:object` | 比较不同case/版本/网格的指标 | EVALUATE / G5 |

## 模型诊断与科学检查（validate）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `validate_preflight` | `scope:object?,checks:strings?` | 求解前结构、材料、选择、网格与study检查 | EVALUATE / G3 |
| `validate_structure` | `scope:NodePath?,rules:strings?` | 检测缺失节点/无效引用/禁用分支 | READ / G3 |
| `validate_expressions` | `expressions:objects,context:object` | 检查表达式/变量/已知单位规则 | EVALUATE / G3 |
| `validate_boundary_conditions` | `scope:NodePath?,rules:strings?` | 诊断已知缺失和冲突，允许合法叠加 | EVALUATE / G3 |
| `validate_solution` | `solution:SolutionSpec,criteria:object` | 检查有解、范围、NaN/Inf、收敛警告 | EVALUATE / G4 |
| `validate_conservation` | `definition:object,solution:SolutionSpec` | 执行任务定义的能量/质量/功率收支 | EVALUATE / G5 |
| `validate_convergence` | `cases:objects,metrics:strings,criteria:object` | 比较网格/时间步/域截断敏感性 | EVALUATE / G5 |
| `validate_report` | `validation_ids:strings,destination:str?` | 聚合pass/fail/unknown及未覆盖项 | FILE_WRITE / G4 |

## 扫描、优化与阶段流程（experiment）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `experiment_design` | `definition:object` | 登记设计变量、边界、约束与采样策略 | STATE_WRITE / G5 |
| `experiment_run` | `experiment_id:str,resources:object?,timeout_s:number?` | 运行DOE/外部优化/原生扫描并保存每个case | COMPUTE / G5 |
| `experiment_inspect` | `experiment_id:str` | 读取case进展、失败原因、缓存与最优可行项 | READ / G5 |
| `experiment_case_result` | `experiment_id:str,case_id:str` | 返回指定case真实模型/原始结果 | READ / G5 |
| `experiment_stage_define` | `definition:object` | 登记阶段次序、状态传递和参考态 | STATE_WRITE / G5 |
| `experiment_stage_run` | `stage_id:str,source:SolutionSpec?` | 运行一阶段并保存状态历史 | COMPUTE / G5 |
| `experiment_state_map` | `source:SolutionSpec,target:NodePath,mapping:object` | 显式映射温度、固化、应力等历史变量 | WRITE / G5 |

## 检查点、分支与恢复（checkpoint）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `checkpoint_create` | `label:str,include_solution:bool?` | 保存恢复点与依赖hash、模型身份 | FILE_WRITE / G1 |
| `checkpoint_list` | `filter:object?` | 列出项目模型恢复点 | READ / G1 |
| `checkpoint_inspect` | `checkpoint_id:str` | 读取检查点完整性和可恢复范围 | READ / G1 |
| `checkpoint_restore` | `checkpoint_id:str,authorization_ref:str?` | 恢复并报告新generation及GUI重绑定需要 | WRITE / G4 |
| `checkpoint_diff` | `left:str,right:str,scope:object?` | 比较当前模型或两个检查点 | READ / G4 |
| `checkpoint_branch` | `checkpoint_id:str,label:str` | 从检查点创建隔离试验分支 | WRITE / G2 |

## 操作计划与分组执行（transaction）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `transaction_preview` | `actions:objects,invariants:objects?` | 仅静态检查计划、权限、依赖与预算 | READ / G2 |
| `transaction_trial` | `actions:objects,invariants:objects?` | 在副本执行计划并输出差异和验证 | COMPUTE / G2 |
| `transaction_apply` | `actions:objects,invariants:objects?,checkpoint_policy:str` | 按前置修订、checkpoint和权限执行动作组 | WRITE / G2 |
| `transaction_verify` | `transaction_id:str,checks:objects?` | 按不变量和回读核验一次事务 | EVALUATE / G2 |
| `transaction_recover` | `transaction_id:str,strategy:str` | 对部分失败执行显式恢复策略 | WRITE / G4 |

## 文件、原始数据与数据外发（artifact）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `artifact_register` | `path:str,role:str,classification:str` | 将批准的文件导入项目并计算hash | FILE_WRITE / G3 |
| `artifact_list` | `filter:object?,cursor:str?,limit:int?` | 分页列出项目Artifact，不扫描整机 | READ / G3 |
| `artifact_inspect` | `artifact_id:str` | 读取大小、类型、hash、版本、生成作业 | READ / G3 |
| `artifact_read` | `artifact_id:str,offset:int?,length:int?` | 分块读取授权Artifact；保留完整文件 | READ / G4 |
| `artifact_preview` | `artifact_id:str,options:object?` | 返回图像/数据预览并声明下采样 | READ / G4 |
| `artifact_publish` | `artifact_ids:strings,destination_ref:str,authorization_ref:str` | 按明确外发授权向host交付文件/图像 | HOST_CONTROL / G4 |
| `artifact_verify` | `artifact_id:str` | 校验包内文件hash、缺失依赖和目标格式 | READ / G6 |

## 可选GUI和现有窗口协作（desktop）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `desktop_status` | `runtime_id:str?` | 读取窗口、进程、权限与adapter验证状态 | READ / G0 |
| `desktop_bind` | `window_ref:str,model_ref:str,verification:object?` | 明确绑定GUI窗口与服务端模型 | HOST_CONTROL / G0 |
| `desktop_show_model` | `window_ref:str,model_ref:str` | 让窗口显示已加载server model并验证 | HOST_CONTROL / G4 |
| `desktop_select_node` | `window_ref:str,path:NodePath` | 按API路径和GUI树映射定位节点 | HOST_CONTROL / G4 |
| `desktop_capture` | `window_ref:str,region:str` | 截取指定窗口/图形区，禁止默认全桌面 | HOST_CONTROL / G4 |
| `desktop_action` | `window_ref:str,action:object` | 执行授权的菜单/快捷键/控件动作 | HOST_CONTROL / G5 |
| `desktop_shell_execute` | `window_ref:str,source_artifact:str,expected_model_ref:str` | 经平台验证后在当前Desktop Shell执行代码 | TRUSTED_CODE / G5 |
| `desktop_migrate_standalone` | `window_ref:str,target_session_id:str,save_policy:object` | 显式保存迁移普通Desktop模型到共享server | HOST_CONTROL / G5 |

## 版本化文档、示例与错误检索（docs）

| 拟议 MCP 工具 | 主要参数（除共同字段） | 完成动作 | 风险 / 阶段 |
|---|---|---|---|
| `docs_index` | `runtime_id:str,sources:strings` | 索引用户合法安装中的帮助，不分发商业文档 | STATE_WRITE / G2 |
| `docs_search` | `query:str,version:str,product:str?,limit:int?` | 按版本/产品检索API与建模文档 | READ / G2 |
| `docs_get` | `document_ref:str,section:str?,offset:int?,length:int?` | 读取具体文档范围并带来源 | READ / G2 |
| `docs_examples` | `query:str,version:str` | 检索已验证/未验证示例并显示状态 | READ / G2 |
| `docs_error_search` | `error:str,version:str,node_type:str?` | 按原始异常与节点类型检索诊断证据 | READ / G4 |

