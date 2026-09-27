# 开发工作包与依赖

每个工作包作为独立PR/阶段交付。主线接口和模型身份由一个架构负责人冻结；领域实现可并行，但共享server的实机操作不得并行写同一实例。

| 工作包 | 阶段 | 依赖 | 验收 | 交付 |
|---|---|---|---|---|
| W01 冻结基线与遗留工具映射 | G0 | 无 | T038 | 固定SHA、列50个旧接口、建立新旧schema映射；避免已有用户配置失效。 |
| W02 六组合Runtime/Java PoC | G0 | W01 | T001, T002, T042, T059 | 实机验证JDK11 client classpath、COMSOL server版本、渲染和产品能力。 |
| W03 Desktop/取消路线PoC | G0 | W02 | T004, T023, T052 | 验证共享Desktop和可用中止路线；标记GUI-only能力，禁止虚构通用cancel。 |
| W04 无破坏求值与变量/选择修复 | G1 | W01 | T003, T005, T006, T007, T033 | 修F01-F03/F06；清理仅限本次临时节点；移除固定指标。 |
| W05 模型身份/修订/权限 | G1 | W02 | T010, T011, T035 | 建立session/model/generation、项目授权、请求hash与写前检查。 |
| W06 控制进程和Java Worker隔离 | G1 | W02, W05 | T012, T026, T028, T055 | 统一串行队列；取消伪hard timeout；真实状态与缓存分离。 |
| W07 持久存储与幂等 | G1 | W05 | T027, T030, T057 | jobs/operations/revisions/artifacts/checkpoints持久化和迁移。 |
| W08 typed值/节点解析 | G2 | W04, W06 | T008, T009, T010 | 保留数据形状；实现typed collection和Work Plane嵌套路径。 |
| W09 registry/schema/host兼容 | G2 | W01, W08 | T038, T039 | 生成文档、工具发布、operation fallback、structuredContent/isError。 |
| W10 公共API与Java执行 | G2 | W08, W05 | T031, T032, T037 | 绑定Model、编译、日志与差异；许可/主机权限与trusted_code分离。 |
| W11 版本化文档索引 | G2 | W02 | T043, T036 | 官方本地帮助索引、来源、版本过滤、无结果语义。 |
| W12 计划/试运行/checkpoint | G2 | W07, W10 | T029, T033, T050 | static preview与隔离trial不同；定义恢复范围和GUI rebind。 |
| W13 参数/变量/函数/选区 | G3 | W08, W11 | T006, T015, T016, T048 | 多维插值、单位、named/spatial选区与语义校验。 |
| W14 几何/WorkPlane/CAD/坐标Pair | G3 | W13 | T009, T034 | 完整嵌套编辑、几何测量和模型保留；产品差异能力表。 |
| W15 材料/物理/多物理 | G3 | W13 | T007, T017, T042 | 属性组和张量、选区、子特征、耦合及初值。 |
| W16 网格/Study/Solver | G3 | W14, W15 | T018, T019, T020 | 从空模型到真实求解；深层solver和物理启用。 |
| W17 Dataset/复场/指标/Probe | G4 | W16 | T013, T014, T021, T049 | 索引、权重、复数、坐标、分块数据，不静默截断。 |
| W18 Plot/Export/视觉返回 | G4 | W17, W03 | T040, T039 | 真实渲染、数据绑定、图片host回传、数据/报告导出。 |
| W19 持久jobs/取消/恢复/并发 | G4 | W07, W16, W03 | T022, T023, T024, T025, T026, T027, T028, T053, T054, T057 | 缓存状态独立响应；取消确认；限制共享Server控制权限。 |
| W20 结构/数值/物理验证 | G4 | W17 | T044, T058 | 规则有覆盖边界；三层成功状态；基准解和收敛。 |
| W21 扫描/优化/阶段状态传递 | G5 | W17, W19 | T021, T047 | case管理、缓存、参数预算、阶段参考态和变量映射。 |
| W22 VCSEL领域验收包 | G5 | W20, W21, W18 | T045, T016 | 静态非轴对称热源、阵列/环功率、距离扫描、热预算。 |
| W23 光纤耦合领域验收包 | G5 | W20, W21, W18 | T046, T014 | 复场、模式归一化、耦合与捕获效率分离、容差扫描。 |
| W24 胶形/固化领域验收包 | G5 | W20, W21, W18 | T047, T058 | 稳定胶形及全流程独立路线，质量/参考态/应力历史。 |
| W25 Desktop迁移与高级GUI | G5 | W03, W12, W19 | T051, T052, T004 | API优先；只对实测GUI路径承诺，明确迁移与重新绑定。 |
| W26 离线构件/安全/发布 | G6 | W09, W18, W19, W22, W23, W24, W25 | T034, T035, T036, T037, T041, T056, T060 | 三种OS/arch构件、六组合认证、hash/lock/SBOM、升级回退、完整交付。 |

## PR 必须包含

功能代码、输入/输出schema、权限/副作用分类、单元测试、最小真实COMSOL案例、错误案例、文档生成结果、版本/平台状态更新。未拿到实机资源时只标UNVERIFIED，不把“有mock”当验收通过。

## 防止再次陷入“工具名很多但不能用”

每个公开动作要能回答：操作哪个模型？实际触发哪个公共API/平台动作？需要什么许可证？输入输出类型是什么？怎样回读？超时后底层是否仍运行？失败造成哪些改动？能恢复到哪里？在哪里有对应实机证据？

## 兼容策略

旧工具转到新domain service，不复制第二套业务实现。原有JSON字符串参数可继续接收，但解析后统一强类型。危险的旧行为不保留；迁移说明明确改变。旧get_core_metrics仅在显式绑定metric set时工作。

## 数据库建议

projects、runtimes、sessions、models、revisions、operations、jobs、job_events、artifacts、checkpoints、capability_evidence、permissions、recipes。每条状态更新需要request_id/operation_id。job状态是观测记录，不是引擎状态的永恒真相；重启执行reconcile。数据库需schema_version和迁移测试。

## 发布报告

列出六组合具体环境、全部required test结果、blocked原因、已知缺陷、证据包hash、每个动作的SUPPORTED_VERIFIED或UNVERIFIED状态。对某模块无许可证可以发布“核心版”并明确缺失，但不能称用户完整能力包已完成。
