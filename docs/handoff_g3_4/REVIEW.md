# G3.3 审查与下一步建议

## 结论

建议 **G3.4：W17 定向末端修补 → W18 真实绘图与图像回传**。已有数值语义和实机验证值得保留，不需要像上轮那样重做 W17；但不能把“新目录导入 wheel 成功”当成项目路径隔离已完成，也不能让现有 CSV/产物返回问题进入图像链路。

审查基线 `31152904205834125776524f92288e18ba93b853`，tree `87e5def90b390c4f85ba541f6825dd82fbc77f69`。实现提交 `84ec0962...`；对应 runtime source `a3f39b3...` 有专门哈希桥。证据链接及定位在 SOURCE_MAP.json。

## 本次实际做了什么

读取远端提交与发布 diff、phase4_3 台账和 source bridge，抽查原始 numeric response，以及结果测度、解轴绑定、结果输出、ArtifactStore、控制初始化和 MCP 网关。没有访问用户 COMSOL，没有执行仓库全量 pytest，没有把读文件/哈希当逐行全仓语义审计。工作包恢复器会逐文件校验整棵树，但这同样不等于人工审完每一行。

本运行环境对公开归档下载尝试失败，GitHub connector 可以读取源码文本但不支持二进制归档。因此本包采用固定公开 SHA 的联网恢复，不假称 ZIP 已含完整仓库。恢复工具在合成 Git/ZIP 上的测试与源码 helper 机制探针是本次可执行验证；它们不证明 COMSOL 或远端恢复已运行。

## 1. 已有进展与可信边界

### 1.1 这次不再是“只有假引擎测试”

提交与台账记录 8 个 final6 public MCP/native 分组，包括数值/坐标、typed nodes、Probe、存储解重开、CutPlane、导出与分块、活动作业恢复、原生求解错误传播。numeric run_status 为 PASS、exit 0、changed_source_files=[]。抽查 C04-average 的真实返回为两个常量表达式约 2、3，并带 `[expression, outer, inner, point]` 形状、参数/单位和解来源。

软件全回归在仓库报告为 **1824 passed / 1 Windows-only skipped**；不是本次重跑结果。source-outside wheel/recovery 有新回执。`audit/publication_source_bridge.json` 记录 184 个来源文件的 runtime/publication 摘要对应。新公开实现提交确实是旧公开 PIN 的子提交，而不是把私有含凭据历史整体推上来。

### 1.2 仍要保留有限范围

`phase4_3_acceptance.json` 保留 C10 PARTIAL + CONTROL_MAPPING；C11/C14 的部分行 NOT_RUN；动态/注入故障有些仅 CONTROL 证据，driver RSS 不等于引擎内存峰值。Windows、Intel Mac、6.3、GUI、共享 Server 取消等不由本轮推断。

该台账仍标 `VALIDATED_AWAITING_REMOTE_SYNC` 与 `overall_acceptance=false`。当前公开 commit 已存在，新回执也记录恢复成功；这是需要追加收口 verdict 的状态漂移，不应机械判定“所有 G3.3 实机仍失败”，也不应直接覆盖旧台账变成无条件 PASS。

## 2. 确认的实现问题与待验证风险

### R01 项目根仍取安装位置（明确源码风险）

`_managed_backend.py` 构造器使用 `Path(__file__).resolve().parents[1]`，`ControlDaemon` 初始化未传入独立项目根。非 editable wheel 的这个路径是 site-packages，而不是用户的数据目录。最终 wheel receipt 的导入/资源检查不能证明在第三个独立目录进行真实建模/导出正确。

修复为可信配置中的项目根、包资源根、私有控制根与输出根分离。新测试应安排安装 A、源码 B、项目 C、cwd D，检查实际输出只进入 C。不要简单改成 cwd；cwd 同样不是授权来源。

### R02 Artifact 私有边界不充分（helper 级确认，需公开调用负控）

ArtifactStore.resolve_safe_path 验证“在项目根里、无 symlink、覆盖规则”，但没有要求 artifact_id 属于已发布清单，也没有处理项目内部私有目录。artifact_read 的路径别名最终调用这个解析器。对于位于项目内部的 token/prefs 等，仅根包含不足。

本次不读取任何真实 secret，也不声称已经发生泄露。用合成 sentinel 在公开 MCP 路径证明实际可达性/阻断层；改为注册产物 ID、明确允许输出数据根，私有控制根完全分离并在读取路径再次保护。若某上游 gate 已阻断，就记录那条真实证据，而不是重复修。

### R03 CSV 四轴和单位丢失（源码与 helper 机制可复现）

`_row_for_leaf` 默认将 path[1] 写入 inner，outer 为空；对 `[expression][outer][inner][point]` 实际 path[1] 是 outer 数组位置。它不使用 FieldArray.coords 中的真实 outer/inner 标签，point 也用原始零基位置；表达式单位从 Sequence 读取，不能正确读取按表达式名的 map；空间坐标没有按 point 定位。

`ArtifactStore.export_field_data(fmt='csv')` 直接使用这个 iterator，因此需针对标准 FieldArray 修补，而不是继续在数据齐全的 JSON 外另造一套猜测式 CSV。测试应使用非连续 labels 和不同 outer 的时间，防止只在 `[1]` 下掩盖错位。

### R04 storage=artifact 仍有全量内联副本（明确源码路径）

`result_evaluate` 在 storage=artifact 分支发布引用，但最后仍返回 `field_array_payload.to_dict()`；该 FieldArray 含 values/data。抽查原始 C04 response 也显示 field_array 同时带 values/data（此样本是 inline，仅证明重复表示结构；artifact 分支风险来自代码）。MCP gateway 又镜像完整 JSON 为 text。

需要强制最终 wire 预算：artifact-only 时只给 shape/axes/coords摘要、绑定、引用及有限 preview，完整数据和完整 metadata 在文件内。不要删掉元数据“优化”；也不要重写已正确的解轴逻辑。

同一 artifact 的每个分块都重新哈希完整文件，内存有界，但读取 k 个块的 I/O 可达到 k 次全文件。可作为性能子项优化，并保留 fd/版本/块 hash 的一致性保护。

### R05 交付状态、历史 token 与可恢复性（文档/发布收口）

新台账应区分已发布与已验证项，替换活动入口的滞后 pending 提示但保留历史台账。恢复锁定公开 `311529...`，不去找本机 `a3f39...` 分支。后者是 source provenance，不是公开依赖。

`.gitignore` 排除了新的 native runtime artifacts、prefs、构建临时目录等。因此完整公开仓库不等于包含全部旧本地二进制模型。必须使用已提交 builder/recipe 重新生成新测试模型；仅有 hash 不能恢复文件。

## 3. 下一步是 W18，不是继续加求解工具

现有 `_mcp_gateway.py` 只用 TextContent 和 structuredContent。W18 要解决“模型看见自己刚生成的真实图”：PlotGroup/Feature CRUD → 绑定当前 stored solution → 真实 COMSOL render/export → ArtifactStore → MCP ImageContent/实际资源读取 → 宿主接收。

原 04_IMPLEMENTATION_PLAN 将 W18 设为依赖 W17 与 W03、验收 T040/T039。建议用 ADR 将 API 绘图的本机 graphics 前提与 GUI/外部编辑/取消能力分开，不以未完成完整 Desktop 自动化阻止 API 进展，也不称 headless 渲染必定可用。render() / ExportFeature.run() 接口本身存在不等于当前服务器图形上下文可用。

建议验证 1D曲线、2D CutPlane、3D Surface、几何图，以及至少两个实际时间/参数解的图。对每张图记录 source solution、dataset、selection、单位、frame/phase、view、revision和哈希；几何截图不能冒充结果图。

## 4. 防止下一轮再次产生虚假完成

- 合成 CONTROL、真实 stdio、native数值、真实宿主接收、物理验证单列。
- 图片文件存在 ≠ 解码成功 ≠ 来自正确 stored solution ≠ 宿主模型收到图像。
- 更换参数却没重算时，不把旧 solution 的截图当新结果；必须有 stale 标记或拒绝。
- 不能只比较“两个 PNG 的 hash 不同”来证明数据集正确；必须核对绑定和数值。
- 故意坏 dataset、过期 cached图、错误 MIME、无权限 artifact、体积过大等必须负控。
- 不要求像素逐字节跨平台一致；要求解与物理含义、设置、单位、数值范围在明确容差内一致。
- 没有授权的宿主或图形环境时，停止相应实测并保留 UNVERIFIED，不循环试错、不绕系统权限。

## 5. 交付范围

本包只增加恢复/审查/目标/验收与本地验证工具，未修改远端仓库或你的 COMSOL。下一 Agent 按 NEXT_GOAL 实际修改源码，按 Gate A 再 W18 执行，阶段末提交真实证据和正常同步。不得自动进入 W19。


## 6. 本次本地工具验证结果

恢复工具在本地合成 Git 仓库/ZIP 上执行了 13 项测试，全部通过；输出见 `review/recovery_tests.txt`。没有执行远端真实恢复。

`tools/review_probes.py --excerpt` 使用本包标明来源的 helper 摘录和临时合成数据：CSV 第一行的 outer 为缺失、inner/point 为 0，所需真实标签为 outer=2/inner=3/point=1，单位 K 也未带出；路径 helper 允许临时根内 `.phase1-private/review_sentinel.txt`。输出见 `review/helper_probe_result.json`。没有读取真实凭据，没有运行生产 MCP，也没有连接 COMSOL；这些是后续生产链负控的起点，不是 native 验收。
