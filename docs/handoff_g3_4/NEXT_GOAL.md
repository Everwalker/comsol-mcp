# G3.4：W17 末端定向修补 → W18 真实绘图、导出与图像回传

审查日期：2026-09-22。基线和恢复方式见 PIN.json / RESTORE.md。本文件是活动 Goal；此前文件的“不得进入 W18”是历史边界，本次允许在下述 Gate A 通过后进入 W18，但不进入 W19。

## 0. 结论与范围

G3.3 已有真实 public MCP 数值/节点/Probe/存储解/故障验证与源码哈希桥，不能继续描述成上一轮只有 Fake Engine。保留全部已有成果。当前仍需修正：项目根与 wheel 安装位置耦合、产物读取的私有边界、CSV 轴/单位/坐标语义、artifact-only 响应里的重复全量数据，以及发布状态与历史证据等级收口。

本轮是有限 Gate A + W18，不重做整个 W17，不重新搭执行系统，不扩大到完整 W19 取消/恢复、W20 验证、W21 优化或 W25 GUI 平台。沿用原有 identity / revision / 单 Server 队列 / 持久 job / witness / 类型/结果/ArtifactStore。图像导出是新副作用，不能绕过任何这些基础能力。

## 1. 开始前

1. 验证本工作包，按 RESTORE 恢复公开 PIN。核对 origin/main 当前差异，不覆盖未知用户改动、不降级最新进度。若本地已有更后阶段，先比较代码/证据后应用本 Goal 尚未解决部分，不能按旧 SHA 重置。
2. 原始文档：AGENTS、05_DEVELOPER_TASK、04_IMPLEMENTATION_PLAN、01_ARCHITECTURE、03_ACCEPTANCE、06_CONTRACT_NOTES；当前证据：phase4_3_acceptance、RESUMPTION_REVIEW、publication_source_bridge、publication_history_scan、final_recovery_receipt，以及八个 final6 run 的原始请求/响应、run_status 和 source manifests。
3. 阅读本包 REVIEW 与 ACCEPTANCE。完整审查将修改的模块和测试，不把本包或旧报告当权威替代当前代码。必要时按 SOURCE_MAP 的当前定位复核；源码更改使某问题消失时用测试证明并记录 NO_LONGER_APPLICABLE。
4. 新建本轮分支和 evidence/phase4_4/runs/<唯一run_id>，保留旧证据。只追加本轮真实状态；不改旧 FAIL/BLOCKED 为 PASS，不把 CONTROL 映射算实机。

## 2. Gate A — 修补现有末端能力

### R01 项目根与安装根分离（优先）

已读源码 ManagedBackend.__init__ 仍由 Path(__file__).resolve().parents[1] 推导 project_root。wheel 装进 site-packages 后，这不是调用者项目目录。source-outside import/Python resources 通过不等于该使用场景已通过。

建立后端可信的配置项/构造参数，显式区分 package_resource_root、project_root、private_control_root、published_artifact_root。请求体不得选择或扩大授权根。control/Worker/jobs/文档/代码执行/导出使用同一个已验证 project_root；多项目可用独立控制实例，不要求本轮新建多租户系统。

测试：wheel 安装到 A，源码 B，科学项目 C，启动 cwd D；在 C 完成真实建模/导出/重开（可用 COMSOL 时），A/B/D 不出现项目输出；配置缺失、目录不可用、意外符号链接明确拒绝。保留源内开发入口兼容，但其默认不能把 site-packages 当数据根。

### R02 Artifact 权限不是“能读项目里任何文件”

当前 ArtifactStore.resolve_safe_path 只做项目包含/符号链接/覆盖检查，artifact_read 将 artifact_id/path 当文件路径。先用完全合成的 sentinel 经真实公开 MCP 路径复现（不读真实 secret）。核对上游是否有额外限制；不得凭 helper 推断已有泄露。

改为注册后的 artifact ID 与后端产物清单关联；legacy path alias 至少也只能解析到获准公开的已注册文件，或经过显式项目数据导出授权。读取项目内控制 prefs、token、.env、私钥、.git/运行时数据库及安装源码默认拒绝。控制私有目录放在 published root 外；项目根之内也不能突破私有子树保护。文件读取与图像回传不要求一个活跃 COMSOL 模型，可按 project-scoped action 读取已持久发布产物，但仍有授权与 hash/size/provenance 校验。

不要通过封锁所有用户数据/Java 能力来“修复”；通用数据访问与 trusted_code 是独立授权路径，不借 artifact.read 兜底读取 credential。未知访问是否真的被生产门禁挡住先测再结论。读写前权限检查和最终文件身份检查都保留。

### R03 CSV 必须保持四轴语义

当前 _row_for_leaf/_iter_csv_rows 使用 path[1] 填 inner，outer 未从 FieldArray.coords 取值；对于 [expression][outer][inner][point] 这会错位并输出零基序号，而不是实际 outer/inner 标签。

CSV 序列化从 FieldArray.axes/coords/units/metadata 获取表达式、outer、inner、point、time/frequency/parameter pair、空间坐标和单位；不能凭位置猜含义或把坐标数组写成一个字符串格。非连续标签、筛选/重排之后的标签保留。实/虚部显式分列。可用 sidecar 保留完整元数据并互相引用哈希。没有足够绑定信息就拒绝该 CSV 形态，不能输出看似完整但轴错误的表。

使用两个表达式、outer [2,5]、inner [3,7]、多点、非平凡单位、复数与乱序字段回归；CSV 再导入应与 JSON/FieldArray 一一对应。通过后无需重做全部 PDE 基准。

### R04 artifact-only 响应不得另塞全量 field_array

当前 result_evaluate 切换 storage=artifact 后，仍构造 field_array.to_dict()；该结构含 values 与 data，另有 MCP 文本镜像可能进一步复制。同一份数据应只在 artifact 中保存一次，响应提供 shape、axes、单位、source binding、有限 preview 与引用；inline 兼容也避免不必要的 values/data 双重全量序列化（需要迁移期则明确预算）。

审计 result.evaluate / at_points / field_export / job_result / MCP text mirror；最终 wire 字节而不是单次数值 payload 才是外部响应预算。不能为减小返回而截掉解轴或 silently truncate。artifact 内容必须保留完整 FieldArray metadata，避免 auto-artifact 只剩数值和少量 context。

每个 artifact.read 当前重算整文件哈希，内存有界但 N 个块可有 O(N*文件大小) I/O。记录实测读量；若本轮影响图像或大数据，则使用受控不可变发布清单、会话内稳定 fd/身份及块哈希实现可验证优化。不要删掉完整性验证来“加速”。

### R05 台账、公开来源与能力层级

phase4_3 仍 VALIDATED_AWAITING_REMOTE_SYNC / overall_acceptance=false，公开新提交却已经存在。核对远端 SHA/tree、184 项源码桥和恢复回执，追加本轮 authoritative continuation verdict。不要把过去时的 pending 直接当当前阻塞，也不能把 overall false 静默改 true。

区分总目录 272 项定义、已注册路由、可执行动作、实机验证动作。C10 PARTIAL+CONTROL_MAPPING、C11/C14 NOT_RUN 子项仍保留；本轮影响 W18 的具体部分必须重新测试，其他不相关控制注入/平台缺失无需全补后才开发。

原本地 a3f39b3 历史仅 provenance，公开 PIN 可恢复。新提交的 secret scan 范围应含本轮将发布的所有 Git 对象；不将含 token 的旧私有历史重新合并到 main，不需要改远端历史。

### Gate A 通过条件

R01–R04 的可执行缺陷有修复与负控，R05 有明确当前状态；至少一次最小 public MCP 求值→产物→分块回读→CSV 再导入测试。在无 COMSOL 时可做全部软件/协议实现，native acceptance 保留 BLOCKED；不得把新代码称实机通过，也不得跳过导致图像传输泄密/数据错位的已确认缺陷。

## 3. W18 执行范围：按原目录落地，不增第二套求解器

以恢复后实际目录/schema 为准，复用既有 operation registry。目标动作族：plot.list / group_create / feature_create / update / remove / render / geometry_render / view_manage；export.list / create / update / run / remove；artifact.register/list/inspect/read/preview/publish（已有的复用）。新 action 如确需增加，先确认目录无等价项、记录迁移理由。

export.report 属原计划 G5、export.evidence_bundle 属 G6；本轮只做 W18 交付所需最小图数关联报告/清单，不借“报告”实现完整平台。动画列出真实支持/限制，若未测试不可称完整 W18 全格式支持。

### 3.1 图形节点与视图

建立并回读 1D/2D/3D PlotGroup、常用 Surface/Slice/Line 等特征；通过当前版本公共 API 与已安装帮助查询准确 type/property。不要以显示中文名猜内部 type。选择集、组件、数据集、解、表达式、单位、复数变换、颜色范围和相机视图有明确绑定。修改/删除仅目标节点，不重建用户整个 Results 树。

至少支持几何图、3D 表面图、CutPlane 上的2D图、1D曲线；网格图按本机可验证路径实现。2D/3D/几何适配不假定都需要同一类 feature。任一图形 feature 构建失败必须显式错误，不退回旧缓存图说成功。

### 3.2 真实渲染与数据 provenance

必须由真实 COMSOL API 导出/渲染，不能用示意图、重新手画、image generation 或仅靠外部 matplotlib 图代替 native 验收。外部数值图可额外生成但标明来源。

每次 render 固定：model_ref、managed_revision、solution_epoch/实际存储解标识、dataset chain、outer/inner/时间、参数、表达式、单位、选区、view/render config、engine/build、output SHA/size/MIME、source_operation/job。仅 model revision 不足以表明当前 field 对应哪次解；参数改过但没重算时允许显示旧解须明确 STALE_SOLUTION，不暗称新参数结果。

渲染前/后回读关键设置；失败不得发布旧文件。导出到本次唯一候选文件，检查签名、非零尺寸、解码、预期尺寸与基本空白检测，再经 ArtifactStore 原子发布。相同输出路径需要显式覆盖许可。假 PNG、过期图、解码异常、错误数据集和错误参数案例都有负控。

### 3.3 W03 依赖拆分

原 W18 依赖 W03；本轮以 ADR 明确：API 原生渲染/图片输出的依赖是当前图形运行时可用，而非“Desktop 外部修改/取消全通过”。在允许的本机显示/图形能力下验证，不擅改全机设置。headless 绘图失败时报告具体所缺图形能力；不要擅自转成 GUI 点击绕过。

共享 Desktop 绑定、GUI 交替写入、跨平台窗口自动化、通用取消仍保留原边界。不存在必要 GUI 权限则只暂停相关实机操作，不阻止无关软件工作。只验证到 API 路线时报告 W18_API_VISUAL_SCOPED_PASS，不宣称完整 GUI 支持。

### 3.4 云端模型真的收到图像

当前 _mcp_gateway.mcp_result 仅构造 TextContent + structuredContent。加入正式的受控多模态返回，不把 base64 塞进 text JSON 后声称模型看到了图片。使用宿主支持的 MCP ImageContent 或可实际读取的已授权资源；保持结构化 provenance 和文本降级。

控制层只交付可信已发布 artifact ID，网关仅按已校验 ID/MIME/size/权限取图，不从模型返回字符串中的任意 file path 读文件。限制压缩大小、像素总量、base64复制预算和每次图片数量；JSON/text 不再重复全量图像。文本型 Host 保留摘要与 artifact URI/ID/按需读取，明确 vision_delivery 未验证。

必测：实际 stdio tools/call 得到 type=image；解码图像哈希与本次 COMSOL 产物一致；至少一种实际可访问的 Agent Host 接收（Hermes 优先，另一实际可用 Host 可替代且单列）。没有 Host 只能标 protocol PASS、host/model visual reception UNVERIFIED，不拿模拟 ClientSession 当所有宿主已认证。

## 4. 科学与系统验收

执行 ACCEPTANCE.md。复用已提交构建器，先恢复/重生成 bounded 测试模型。不能要求用户提供已被清理且未提交的旧临时 MPH；新模型/新解另记 SHA、source/run，不冒充旧文件。

核心端到端：
- 稳态非均匀温度：用已验证 W17 的数值采样作为对照，COMSOL Surface/1D 图与当前表达式/解一致。
- 瞬态至少两个非初始时刻或两个 outer 参数：分别渲染，说明选中的实际解；图片差异只作辅助，关键数值/绑定也要不同且正确。
- 非轴对称离轴热点：保留二维/三维方向，不偷偷圆周平均；色标、单位与坐标有证据。
- 保存包含 Plot/Export/视图设置的模型，用新 Worker 打开**同一已保存文件**，不先重算，回读图形设置并从同一存储解再渲染。PNG不要求逐字节相同，但科学绑定、尺寸/语义与数值一致。

长绘图/导出沿用已有 job。调用超时不表示引擎停止；提交响应丢失使用同 key 找回原 job，不二次建图/导出。故意失败仅在本轮自有测试模型/实例，不强停共享 Server。主机断连、清理失败的已有可靠路径必须回归到受影响范围。

## 5. 工作方式与交付

- 建一份执行计划和一份新权威台账即可；优先更新现有模块/测试，不再生成一堆重复 spec、截图副本和多套 registry。确有独立职责才拆新模块。
- 每个确认缺陷先写能失败的回归，再修复，再跑定向测试；基础链没过时不连续全量 native run。错误归因到具体层后再调整测试，禁止用放宽断言达成通过。
- 每个 pass 关联 source_sha + 文件manifest、请求/响应、实际命令、环境与level。图像、原始数值、model/solution/render身份、sha/size一起保存。保留历史 fail 和 current limitation，不复制旧数字。
- 读写根、身份、getter 类型、同 key 不重放、危险信号升级、source-outside 安装、数值轴/复数等受影响回归必须保留。mock/协议/native/数值/视觉/物理证据分开。
- 完成时提交代码、测试、生成目录/Schema、必要文档和脱敏证据，按既有授权普通非强制同步远端；发现远端并发变更先对比、协调，不 force，不泄露旧私有 Git 对象。

## 6. 停止条件

成功：R01–R05 经证据关闭；本机可执行 W18 API 原生图形/数据绑定/图像回传通过；目标 Host 有明确测试状态；科学设置保存重开通过；全套软件回归与受影响 native 回归通过；新目录恢复可继续；清理/公开源码桥/正常同步有记录。结束返回本轮真实进度、未验证范围、下一候选 W19，不自动执行它。

受阻：完成所有不依赖阻塞的本轮实现/测试；说明唯一或最小剩余前提，停止循环。已确认产品缺陷不得以环境缺失洗成 BLOCKED。未获许可/未访问平台保留状态，不能为了整体 PASS 隐去。不要要求用户把旧目录找回来。

最终不能只给“完成”；给出 model/data/images/代码源与验收可追溯的交付位置。所有后续动作仍由用户决定。
