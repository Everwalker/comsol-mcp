# G3.2 系统审查与 G3.3 决策

审查基线：`Everwalker/comsol-mcp@2cb4627924d1a3240818ea7cd00453d4bd2d2da8`。
根 tree：`dd3095e89c640c19aeb151cd0e8efe4af3c55802`。审查日期：2026-09-21。

## 范围和结论

本次读取了提交/树、Gate A 与 W17 验收台账、关键测试、W17 生产实现、分派层、
依赖/打包定义，并对照官方 NumericalFeature API。没有访问用户 COMSOL，没有重跑全仓测试；
不能声称逐行审查了几千个历史证据文件。连接器可读源码但容器不能下载仓库，因此本包提供
固定 SHA 的联网恢复工具；全文件清单和读取/解析审计将在恢复后生成。

**结论：不宜进入 W18。下一目标是 G3.3：修正证据等级、完成 W17 真实数值语义和从干净目录恢复。**
保留 G0–G3.1 的已验证成果，不推倒重写；只修与本轮结果、恢复和验收相连的缺陷。

## 有效进展

最新提交增加了 Gate A 补修、12 个 dataset/result 操作和对应测试；提交声明软件测试为
1616 passed / 1 skipped。此数字属于仓库报告，不是本次复跑。变量/数据集 accessor、
方法签名分类、危险信号单调升级都有代码和软件测试的进展，不能因此抹去已有成果。

但是 `phase4_2_acceptance.json` 的 reopen 与 W17 的数值 PASS，主要指向 Fake 模型测试。
`test_g3_gate_a2_f02_reopen.py` 内使用 `FakeReopenModel`、预置数值字典和
`b"CHAIN_A_STEADY_SOLVED_MPH_CONTENT_V1"`，没有读取真实 MPH。
其中 SHA 负控只比较两个常量字符串；这不能证明生产重开检查器能拒绝错误文件。
`tests/test_g3_w17.py` 使用 `FWiredTree`，其数值 getter 返回预置数据；轴对称测试检查标志，
平均值测试仅检查 denominator 非空。单测可以保留，但证据等级必须纠正。

## 发现与处理

| ID | 级别 | 问题与源码定位 | 处理及完成判据 |
|---|---|---|---|
| F01 | P0 | phase4_2 / w17 台账把 Fake 测试表述为已解决实机 reopen/数值验收；audit/semantic_review.json 是台账引用但根树没有 audit 目录 | 历史原件保留，追加纠正台账；每条 PASS 必须有 source SHA、真实命令、引擎/数值等级、原始产物。缺文件标 MISSING，不补造历史证据。 |
| F02 | P0 | `_g3_results.result_evaluate` 把 integral/average/std/rms 全部映射 IntVolume；average 分母硬编码1；未执行 std/rms 公式 | 按实体维数/selection选择真实算法；从积分1取分母或使用已验证平均特征；常量场 std=0，RMS=绝对值；区分变换与聚合顺序。 |
| F03 | P0 | 同函数没有按 selection 绑定求值区域；axisymmetric 标志不代表真实2πr积分路径，维数分支仍固定Volume | 明确物理测度、轴对称设置和真实选区；非单位尺寸、局部域及轴对称解析体测试。不能只改返回标志。 |
| F04 | P0 | inner 按 transformed 最外层切片；官方 getData 顺序是[expr][solnum][vertex]；outer 未落实；dataset_solution_indices 硬编码outer=[1] | 统一命名轴；使用实际SolutionInfo/版本确认接口；多表达式、多inner、多outer交叉检查；不支持的请求写前拒绝，不能忽略。 |
| F05 | P0 | `_transform_complex_data` 在虚部缺失或形状不匹配时补0；isComplex读取失败被忽略 | 只有确认全实数可合成零虚部；虚部失败/shape错误必须非成功；所有维度一致与非有限值有策略。 |
| F06 | P0 | `result_at_points` 未进行 coordinate_unit/frame 转换；getCoordinates非空就标VERIFIED | 明确坐标单位与参考系；比较请求点和真实回读点的数值/形状/单位；m/mm等价、错误点负控；无法验证为UNVERIFIED。 |
| F07 | P0 | `_dataset_tag`只取最后tag；data字段被当solution；create/update回读/失败行为不完整 | 使用类型化NodePath；dataset data链与solution链分开解析、循环检测、歧义拒绝；写后实际回读并保留部分失败，不继续无边界写。 |
| F08 | P0 | `result_field_export`直接Path(dest).resolve/write_text，未在已读G3分派路径看到路径绑定；忽略eval status/cleanup后继续写 | 按项目根、symlink、覆盖授权和原子保存校验；失败结果不得发布成功产物；借助新目录哨兵测试，不碰用户文件。集成可达性需新MCP测试，不把helper测试当安全认证。 |
| F09 | P1 | auto artifact与verify_artifact_chunks多次整体read_bytes、拼接所有块；只有chunk_info并非远端可读分页服务 | 引入真实artifact handle/chunk read契约，绑定hash/length/range；明确内存预算，流式验证；未知format不能退化为str(values)。 |
| F10 | P1 | numerical_manage只管理Derived Values，不等同Definitions Probe；表格与类型参数未完整验收 | 区分Probe与Numerical，补W17范围Probe能力或明示缺口；complex表格不丢虚部，主模型用户表不被覆盖。 |
| F11 | P1 | DEPENDENCIES是Markdown freeze，pyproject依赖未固定；包data只列data/g2；旧路径/运行回执仍可能被脚本引用 | 真正新环境重建，直接依赖/资源清单测试，生成机器锁与SBOM/下载记录；补路径发现，不复用旧环境。 |
| F12 | P1 | W17多个函数重复已修好的采样/临时节点/typed逻辑，容易绕过既有契约 | 提炼SolutionBinding、MeasureSpec、FieldArray、ArtifactStore等公共服务，旧工具走同一实现；不能通过回滚G3.1安全修复解决新失败。 |

### 数值例子（审查者推导，不是 COMSOL 实测）

对于常量 f=2、体积V=3：积分为6，平均为2，标准差为0，RMS为2。
当前 average/std/rms 只请求 IntVolume 并返回其结果，不能通过返回分母1满足这些不同定义。
对于半径R=2、高H=3的轴对称圆柱，∫1 dV=12π，而不是二维截面面积6。

对于两表达式、三时间、多个坐标，返回data[1]是第二表达式，不是所有表达式的第2时间。
`outer_indices=[1]` 也不能代表真正存在多个外层参数的解。

### 必须区分的四个结论

1. 代码里有操作、schema能解析。
2. fake引擎下函数输出符合部分控制测试。
3. 生产MCP经过真实Worker调用COMSOL，得到原始数值。
4. 原始数值通过独立解析/实验/收敛验证。

G3.2的被引用测试主要证明第1/2层，不能自动升级为第3/4层。

## 文件来源（固定提交，名称/函数为定位依据）

基址：`https://github.com/Everwalker/comsol-mcp/blob/2cb4627924d1a3240818ea7cd00453d4bd2d2da8/`

- evidence/phase4_2_acceptance.json
- evidence/w17_acceptance.json
- tests/test_g3_gate_a2_f02_reopen.py
- tests/test_g3_w17.py
- comsol_mcp/_g3_results.py（W17段，约1448行起；关键函数见表）
- comsol_mcp/_managed_backend.py（_invoke_g3_model / _dispatch_with_witness）
- DEPENDENCIES.md；pyproject.toml；仓库根Git tree。

外部核对：COMSOL 6.4 官方 NumericalFeature API：
`https://doc.comsol.com/6.4/doc/com.comsol.help.comsol/api/com/comsol/model/NumericalFeature.html`
该页明确 getData 的[expr][solnum][vertex]顺序和带outersolnum的getter。
本报告中的修复架构与新增验收是审查建议，不冒充仓库原来的设计或已取得结果。
